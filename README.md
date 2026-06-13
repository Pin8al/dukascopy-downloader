# Dukascopy Tick Downloader

Downloads historical tick data from the Dukascopy JETTA API into local
**Parquet storage** (the source of truth) and exports **MT5-compatible tick
CSV** files on demand (CSV is only a disposable export format).

## Install

```powershell
pip install -r requirements.txt
```

Requires Python 3.10+.

## Web UI

```powershell
python main.py web
# open http://127.0.0.1:8080
```

Minimal white-themed interface for search, **bulk download** (multiple symbols in one
job), export, gap scan/repair, stored-data library, and live job progress.

## Usage (CLI)

```powershell
# Find an instrument (1600+ in the bundled catalog)
python main.py search gold
python main.py search eurusd

# Download ticks into Parquet (resumable, concurrent, auto-retrying)
python main.py download EURUSD 2025-01-01 2025-06-30

# Check for and repair holes in the dataset
python main.py gaps EURUSD 2025-01-01 2025-06-30 --repair

# Scan the entire recorded range (no dates needed)
python main.py gaps EURUSD --all
python main.py gaps EURUSD --all --repair
python main.py gaps EURUSD --all --repair --refetch-empty   # also re-request empty hours

# Export an MT5 tick CSV from stored Parquet
python main.py export EURUSD 2025-01-01 2025-06-30
#   -> exports/EURUSD/EURUSD_2025-01-01_2025-06-30.csv

# Export the entire recorded range (no dates needed)
python main.py export EURUSD --all
#   -> exports/EURUSD/EURUSD_2025-01-01_2025-12-31_all.csv

# What is stored locally?
python main.py status EURUSD
```

`download` options: `--workers N` (default 15, max 64), `--force`, `--profile`

## How it works

```
plan hours -> fetch JETTA JSON (parallel) -> decode -> verify -> Parquet (atomic)
                                                                  |
                 SQLite ledger: completed / empty / failed  <-----+
                                                                  |
                              MT5 CSV export  <-- reads Parquet --+
```

- **One Parquet file per instrument-hour** (`data/EURUSD/2025/01/02/14.parquet`),
  written atomically via temp file + rename and never overwritten.
- **SQLite ledger** (`data/metadata.db`) records every hour's state. Completed
  and empty hours are never re-downloaded, so interrupted runs resume for free
  and progress is never lost.
- **Retry manager**: exponential backoff with jitter for network errors and
  5xx responses; corrupt payloads (JSON/structure/verification failures)
  trigger a fresh fetch. Hours that still fail get extra retry rounds, then
  remain flagged for `gaps --repair`. Temporary failures never abort a run.
- **Planner** clamps ranges to each instrument's earliest available data and
  skips the not-yet-published most recent hours. Hours with no tick data are
  recorded as empty after an empty JETTA response.
- **Verification** checks record structure, timestamp monotonicity and bounds,
  positive prices, and plausible spreads before anything is persisted.
- **Instrument catalog** (`config/instruments.json`) carries display names used
  to resolve JETTA instrument codes, plus data start dates.

## MT5 CSV format

Tab-separated, UTC timestamps, ready for MT5 custom symbol tick import:

```
<DATE>	<TIME>	<BID>	<ASK>	<LAST>	<VOLUME>	<FLAGS>
2025.01.02	00:00:00.351	1.03512	1.03524	0	0	6
```

FLAGS=6 marks both bid and ask as changed. LAST/VOLUME are 0 because the
Dukascopy FX feed is quote-based.

## Layout

```
core/
  models/        instrument, tick, hour-task
  services/      instrument_search, planner, download_engine,
                 retry_manager, decoder, verification, gap_scanner
storage/         parquet_storage (source of truth), metadata_db (ledger)
export/          mt5_csv_exporter
config/          settings, instruments.json
cli/             commands
data/            Parquet store + SQLite ledger   (created at runtime)
exports/         generated CSVs                  (created at runtime)
```

The Parquet-first design keeps the door open for future additions (candle
aggregation, other export formats, multiple feeds) without re-downloading
anything.
