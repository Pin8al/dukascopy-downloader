"""SQLite ledger tracking the state of every instrument-hour.

This is what makes the downloader resumable: completed and empty hours are
never requested again, and a status can never be downgraded (a failure can
never overwrite a completed hour).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.models.task import TaskStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hour_status (
    instrument  TEXT NOT NULL,
    hour_utc    TEXT NOT NULL,
    status      TEXT NOT NULL,
    tick_count  INTEGER NOT NULL DEFAULT 0,
    file_path   TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (instrument, hour_utc)
);
CREATE INDEX IF NOT EXISTS idx_hour_status_lookup
    ON hour_status (instrument, status);
"""

# A status may only move "upwards"; never away from completed/empty.
_FINAL_STATUSES = {TaskStatus.COMPLETED.value, TaskStatus.EMPTY.value}


def _hour_key(hour: datetime) -> str:
    return hour.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00")


class MetadataDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes ------------------------------------------------------------

    def mark(
        self,
        instrument_id: str,
        hour: datetime,
        status: TaskStatus,
        tick_count: int = 0,
        file_path: str | None = None,
        error: str | None = None,
    ) -> None:
        key = _hour_key(hour)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT status, attempts FROM hour_status WHERE instrument=? AND hour_utc=?",
                (instrument_id, key),
            ).fetchone()
            if row is not None and row[0] in _FINAL_STATUSES:
                return  # never downgrade valid data
            attempts = (row[1] if row else 0) + 1
            self._conn.execute(
                """
                INSERT INTO hour_status
                    (instrument, hour_utc, status, tick_count, file_path,
                     attempts, last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (instrument, hour_utc) DO UPDATE SET
                    status=excluded.status,
                    tick_count=excluded.tick_count,
                    file_path=excluded.file_path,
                    attempts=excluded.attempts,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (instrument_id, key, status.value, tick_count, file_path,
                 attempts, error, now),
            )
            self._conn.commit()

    # -- reads -------------------------------------------------------------

    def status_map(
        self, instrument_id: str, start_hour: datetime, end_hour: datetime
    ) -> dict[str, str]:
        """{hour_key: status} for all recorded hours in [start_hour, end_hour]."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT hour_utc, status FROM hour_status
                WHERE instrument=? AND hour_utc BETWEEN ? AND ?
                """,
                (instrument_id, _hour_key(start_hour), _hour_key(end_hour)),
            ).fetchall()
        return dict(rows)

    def summary(self, instrument_id: str) -> dict:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT status, COUNT(*), COALESCE(SUM(tick_count), 0)
                FROM hour_status WHERE instrument=? GROUP BY status
                """,
                (instrument_id,),
            ).fetchall()
            span = self._conn.execute(
                "SELECT MIN(hour_utc), MAX(hour_utc) FROM hour_status WHERE instrument=?",
                (instrument_id,),
            ).fetchone()
        by_status = {status: {"hours": count, "ticks": ticks} for status, count, ticks in rows}
        return {"by_status": by_status, "first_hour": span[0], "last_hour": span[1]}
