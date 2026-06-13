"""Central configuration for the downloader."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # Paths
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")
    export_dir: Path = field(default_factory=lambda: BASE_DIR / "exports")
    db_path: Path = field(default_factory=lambda: BASE_DIR / "data" / "metadata.db")
    instruments_file: Path = field(default_factory=lambda: BASE_DIR / "config" / "instruments.json")

    # Dukascopy JETTA API (JSON ticks, not rate-limited like the legacy datafeed)
    base_url: str = "https://jetta.dukascopy.com/v1"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    # Throughput — each worker holds one keep-alive connection to JETTA.
    # Raise via --workers or the UI; run multiple jobs to multiply total in-flight.
    max_workers: int = 15
    max_workers_ceiling: int = 64
    process_workers: int = 32  # unused; kept for settings compat
    request_timeout: float = 30.0
    parquet_compression: str = "snappy"

    # Retry policy (fetch uses fast=True in the download engine)
    max_attempts: int = 3
    backoff_base_seconds: float = 0.15
    backoff_max_seconds: float = 1.5
    # Extra full passes over hours that still failed after per-request retries.
    retry_rounds: int = 2

    # Planning
    # The most recent hours may not yet be published.
    min_data_lag_hours: int = 2

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def for_job(self, workers: int | None = None) -> Settings:
        """Download/gap-repair settings with an optional worker ceiling override."""
        ceiling = workers if workers is not None else self.max_workers
        ceiling = max(1, min(ceiling, self.max_workers_ceiling))
        return replace(self, max_workers=ceiling)
