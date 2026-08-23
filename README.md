# Dukascopy Tick Downloader

Downloads historical tick data from the Dukascopy JETTA API into local
**MT5-ready binary files** and imports ticks into **MetaTrader 5**
custom symbols via a bundled MQL5 script.

## Install

```powershell
pip install -r requirements.txt
```

Requires Python 3.10+.

If you are upgrading from an older Parquet-based install, run the one-time
migration after installing:

```powershell
python main.py migrate
```

(`pyarrow` is only needed for that migration step.)

## Web UI

```powershell
python main.py web
# open http://127.0.0.1:8080
```

Minimal white-themed interface for search, **bulk download** (multiple symbols in one
job), MT5 import, gap scan/repair, stored-data library, and live job progress.

## Usage (CLI)

```powershell
# Find an instrument (1600+ in the bundled catalog)
python main.py search gold
python main.py search eurusd

# Download ticks into binary hour files (resumable, concurrent, auto-retrying)
python main.py download EURUSD 2025-01-01 2025-06-30

# One-time: convert legacy .parquet data to .bin
python main.py migrate

# Check for and repair holes in the dataset
python main.py gaps EURUSD 2025-01-01 2025-06-30 --repair

# Scan the entire recorded range (no dates needed)
python main.py gaps EURUSD --all
python main.py gaps EURUSD --all --repair
python main.py gaps EURUSD --all --repair --refetch-empty   # also re-request empty hours

# What is stored locally?
python main.py status EURUSD

# Export a complete stored range to one MT5 tick CSV (tab-separated, no header)
python main.py export-csv EURUSD 2017-01-01 2026-08-21 --output EURUSD_ticks.csv
```

`download` options: `--workers N` (default 15, max 64), `--force`, `--profile`

## How it works

```
plan hours -> fetch JETTA JSON (parallel) -> decode -> verify -> .bin (atomic)
                                                                  |
                 SQLite ledger: completed / empty / failed  <-----+
                                                                  |
                              MT5 import  <-- link hour .bin files --+
```

- **One `.bin` file per instrument-hour** (`data/EURUSD/2025/01/02/14.bin`),
  stored in the same bin_v1 layout MT5 imports. Written atomically via temp file
  + rename and never overwritten.
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

## MT5 import

Downloads are already MT5-ready. Import hard-links each hour `.bin` into the
MT5 job folder (no concat copy) and launches MetaTrader 5 with
`DukascopyTickImport.mq5`, which reads `hours.txt` and imports each file
via `CustomTicksReplace`. Large histories are split into bounded batches; the
first batch replaces the custom symbol history and following batches append
their non-overlapping dates. The controller waits for MT5's explicit completion
state rather than treating a quiet progress update as success.

### CSV fallback / manual import

If you prefer MT5's native tick importer, export any complete downloaded range
to one tab-separated, headerless CSV:

```powershell
python main.py export-csv EURUSD 2017-01-01 2026-08-21 --output EURUSD_ticks.csv
```

The command verifies that the selected range has no missing or failed hours,
writes atomically, and preserves millisecond timestamps. In MT5, open the
custom symbol's **Ticks** tab and choose **Import Ticks**. Use the default
six-column mapping: date, time, bid, ask, last, volume.

## Layout

```
core/
  models/        instrument, tick, tick_batch, hour-task
  services/      instrument_search, planner, download_engine,
                 retry_manager, decoder, verification, gap_scanner
storage/         tick_storage, tick_format, metadata_db, parquet_migration
export/          mt5_tick_publisher, mt5_importer, mt5_csv
mt5/             DukascopyTickImport.mq5
config/          settings, instruments.json
cli/             commands
scripts/         migrate_parquet_to_bin.py
data/            binary tick store + SQLite ledger   (created at runtime)
```
