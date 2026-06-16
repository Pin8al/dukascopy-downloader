"""Shared application dependencies for the web UI."""
from __future__ import annotations

from config.settings import Settings
from core.services.instrument_search import InstrumentCatalog
from storage.metadata_db import MetadataDB
from storage.tick_storage import TickStorage

_settings: Settings | None = None
_catalog: InstrumentCatalog | None = None
_db: MetadataDB | None = None
_storage: TickStorage | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_directories()
    return _settings


def catalog() -> InstrumentCatalog:
    global _catalog
    if _catalog is None:
        _catalog = InstrumentCatalog(settings().instruments_file)
    return _catalog


def db() -> MetadataDB:
    global _db
    if _db is None:
        _db = MetadataDB(settings().db_path)
    return _db


def storage() -> TickStorage:
    global _storage
    if _storage is None:
        s = settings()
        _storage = TickStorage(s.data_dir)
    return _storage
