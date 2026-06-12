"""Export stored Parquet ticks to an MT5-compatible tick CSV.

The heavy work (reading Parquet + formatting millions of tick lines) is
fanned out to a process pool so the export saturates all CPU cores. The
parent process consumes results in hour order and streams them to disk,
keeping memory bounded via a sliding submission window.
"""
from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from config.settings import Settings
from core.exceptions import JobCancelled
from core.models.instrument import Instrument
from core.services.planner import Planner
from storage.parquet_storage import ParquetStorage

HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\n"
FLAGS_BID_ASK = 6
_WRITE_BUFFER = 4 * 1024 * 1024
_PROGRESS_EVERY = 32
# Process startup has a fixed cost on Windows; small exports run serially.
_MIN_HOURS_FOR_POOL = 64

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000


def _format_hour_file(path_str: str, digits: int) -> tuple[str, int]:
    """Read one hourly Parquet file and render its CSV lines.

    Top-level function so it is picklable for worker processes. All ticks in
    an hourly file share the same UTC date, so the date prefix is computed
    once and the time-of-day comes from integer math (much faster than
    datetime formatting per tick).
    """
    table = pq.read_table(path_str, columns=["timestamp_ms", "bid", "ask"])
    count = table.num_rows
    if count == 0:
        return "", 0

    timestamps = table.column("timestamp_ms").to_numpy().tolist()
    bids = table.column("bid").to_numpy().tolist()
    asks = table.column("ask").to_numpy().tolist()

    day = datetime.fromtimestamp(timestamps[0] / 1000.0, tz=timezone.utc)
    date_prefix = f"{day.year:04d}.{day.month:02d}.{day.day:02d}\t"
    price_fmt = f"{{:.{digits}f}}"
    tail = f"\t0\t0\t{FLAGS_BID_ASK}\n"

    lines = []
    append = lines.append
    for ts_ms, bid, ask in zip(timestamps, bids, asks):
        ms_of_day = ts_ms % _MS_PER_DAY
        hour, rem = divmod(ms_of_day, _MS_PER_HOUR)
        minute, rem = divmod(rem, 60_000)
        second, ms = divmod(rem, 1000)
        append(
            f"{date_prefix}{hour:02d}:{minute:02d}:{second:02d}.{ms:03d}\t"
            f"{price_fmt.format(bid)}\t{price_fmt.format(ask)}{tail}"
        )
    return "".join(lines), count


@dataclass
class ExportResult:
    path: Path
    rows: int
    hours_with_data: int


class MT5CsvExporter:
    def __init__(self, settings: Settings, storage: ParquetStorage, planner: Planner):
        self.settings = settings
        self.storage = storage
        self.planner = planner

    def export(
        self,
        instrument: Instrument,
        start_date: date,
        end_date: date,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExportResult:
        start_hour = datetime(
            start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc,
        )
        end_hour = datetime(
            end_date.year, end_date.month, end_date.day, 23, tzinfo=timezone.utc,
        )
        hours = self.storage.list_stored_hours(instrument, start_hour, end_hour)
        out_path = self._output_path(instrument, start_date, end_date)
        return self._export_hours(
            instrument, hours, out_path,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def export_all(
        self,
        instrument: Instrument,
        start_hour: datetime,
        end_hour: datetime,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExportResult:
        hours = self.storage.list_stored_hours(instrument, start_hour, end_hour)
        out_path = self._output_path(
            instrument, start_hour.date(), end_hour.date(), suffix="_all",
        )
        return self._export_hours(
            instrument, hours, out_path,
            on_progress=on_progress, should_cancel=should_cancel,
        )

    def _output_path(
        self,
        instrument: Instrument,
        start_date: date,
        end_date: date,
        suffix: str = "",
    ) -> Path:
        out_dir = self.settings.export_dir / instrument.symbol
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / (
            f"{instrument.symbol}_{start_date.isoformat()}_"
            f"{end_date.isoformat()}{suffix}.csv"
        )

    def _export_hours(
        self,
        instrument: Instrument,
        hours: list[datetime],
        out_path: Path,
        on_progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExportResult:
        digits = instrument.price_decimals
        rows = 0
        hours_with_data = 0
        total = len(hours)
        last_emit = 0.0

        def check_cancel() -> None:
            if should_cancel and should_cancel():
                raise JobCancelled()

        def emit(done: int, hour: datetime | None = None, force: bool = False) -> None:
            nonlocal last_emit
            if not on_progress:
                return
            now = time.monotonic()
            if not force and done < total and now - last_emit < 0.25 and done % _PROGRESS_EVERY:
                return
            last_emit = now
            on_progress({
                "total": total,
                "done": done,
                "rows": rows,
                "hours_with_data": hours_with_data,
                "percent": round(100 * done / total, 1) if total else 100.0,
                "message": (
                    f"Exporting {hour:%Y-%m-%d %H:00} UTC"
                    if hour is not None
                    else "Finalizing export"
                ),
            })

        tmp_path = out_path.with_name(out_path.name + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="", buffering=_WRITE_BUFFER) as out:
                out.write(HEADER)

                def consume(text: str, count: int, done: int, hour: datetime) -> None:
                    nonlocal rows, hours_with_data
                    if count:
                        hours_with_data += 1
                        rows += count
                        out.write(text)
                    emit(done, hour)

                if total >= _MIN_HOURS_FOR_POOL:
                    self._run_pool(instrument, hours, digits, consume, check_cancel)
                else:
                    for index, hour in enumerate(hours):
                        check_cancel()
                        path = self.storage.hour_path(instrument, hour)
                        text, count = _format_hour_file(str(path), digits)
                        consume(text, count, index + 1, hour)
            tmp_path.replace(out_path)
        except JobCancelled:
            tmp_path.unlink(missing_ok=True)
            raise

        emit(total, force=True)
        return ExportResult(path=out_path, rows=rows, hours_with_data=hours_with_data)

    def _run_pool(
        self,
        instrument: Instrument,
        hours: list[datetime],
        digits: int,
        consume: Callable[[str, int, int, datetime], None],
        check_cancel: Callable[[], None],
    ) -> None:
        """Format hour files on all CPU cores; write results in order.

        A sliding submission window bounds memory: at most window-many
        formatted hour blocks exist at any time.
        """
        workers = os.cpu_count() or 4
        window = workers * 4
        total = len(hours)
        pool = ProcessPoolExecutor(max_workers=workers)
        pending: deque = deque()
        next_index = 0
        done = 0
        try:
            while done < total:
                while next_index < total and len(pending) < window:
                    hour = hours[next_index]
                    path = str(self.storage.hour_path(instrument, hour))
                    pending.append(
                        (hour, pool.submit(_format_hour_file, path, digits)),
                    )
                    next_index += 1
                hour, future = pending.popleft()
                text, count = future.result()
                check_cancel()
                done += 1
                consume(text, count, done, hour)
        except JobCancelled:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
