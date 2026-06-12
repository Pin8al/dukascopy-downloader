"""Export stored Parquet ticks to an MT5-compatible tick CSV.

Format accepted by MetaTrader 5 custom symbol tick import (tab separated):

    <DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>
    2025.01.02\t00:00:00.351\t1.03512\t1.03524\t0\t0\t6

Timestamps are UTC. Prices use the instrument's native decimal precision.
FLAGS=6 means both bid and ask changed. The CSV is a disposable export;
Parquet remains the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from config.settings import Settings
from core.models.instrument import Instrument
from core.services.planner import Planner
from storage.parquet_storage import ParquetStorage

HEADER = "<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\n"
FLAGS_BID_ASK = 6  # TICK_FLAG_BID | TICK_FLAG_ASK


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

    def export(self, instrument: Instrument, start_date: date, end_date: date) -> ExportResult:
        out_dir = self.settings.export_dir / instrument.symbol
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (
            f"{instrument.symbol}_{start_date.isoformat()}_{end_date.isoformat()}.csv"
        )

        hours = self.planner.hours_in_range(instrument, start_date, end_date)
        digits = instrument.price_decimals
        rows = 0
        hours_with_data = 0

        tmp_path = out_path.with_name(out_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="") as out:
            out.write(HEADER)
            for _, table in self.storage.iter_range(instrument, hours):
                if table.num_rows == 0:
                    continue
                hours_with_data += 1
                timestamps = table.column("timestamp_ms").to_pylist()
                bids = table.column("bid").to_pylist()
                asks = table.column("ask").to_pylist()
                lines = []
                for ts_ms, bid, ask in zip(timestamps, bids, asks):
                    moment = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                    lines.append(
                        f"{moment:%Y.%m.%d}\t{moment:%H:%M:%S}.{ts_ms % 1000:03d}\t"
                        f"{bid:.{digits}f}\t{ask:.{digits}f}\t0\t0\t{FLAGS_BID_ASK}\n"
                    )
                out.writelines(lines)
                rows += len(lines)
        tmp_path.replace(out_path)
        return ExportResult(path=out_path, rows=rows, hours_with_data=hours_with_data)
