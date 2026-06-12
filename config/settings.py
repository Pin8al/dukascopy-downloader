"""Central configuration for the downloader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # Paths
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    export_dir: Path = field(default_factory=lambda: BASE_DIR / "exports")
    db_path: Path = field(default_factory=lambda: BASE_DIR / "data" / "metadata.db")
    instruments_file: Path = field(default_factory=lambda: BASE_DIR / "config" / "instruments.json")

    # Dukascopy datafeed
    base_url: str = "https://datafeed.dukascopy.com/datafeed"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    # Throughput
    max_workers: int = 16
    request_timeout: float = 30.0

    # Retry policy
    max_attempts: int = 5
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    # Extra full passes over hours that still failed after per-request retries.
    retry_rounds: int = 2

    # Planning
    # The most recent hours are not yet published by Dukascopy.
    min_data_lag_hours: int = 2

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
