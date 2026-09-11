from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from export.mt5_csv import export_mt5_csv
from storage.tick_format import write_hour_file


class Mt5CsvExportTests(unittest.TestCase):
    def test_exports_native_mt5_rows_and_preserves_milliseconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "hour.bin"
            write_hour_file(
                source,
                [
                    int(datetime(2024, 6, 12, 10, 0, 0, 125_000, tzinfo=timezone.utc).timestamp() * 1000),
                    int(datetime(2024, 6, 12, 10, 0, 1, 7_000, tzinfo=timezone.utc).timestamp() * 1000),
                ],
                [1.08123, 1.08124],
                [1.08135, 1.08136],
            )
            output = root / "ticks.csv"

            result = export_mt5_csv([source], output, decimals=5)

            self.assertEqual(result.ticks, 2)
            self.assertEqual(result.hour_files, 1)
            self.assertEqual(
                output.read_text(encoding="ascii"),
                "2024.06.12\t10:00:00.125\t1.08123\t1.08135\t0\t0\n"
                "2024.06.12\t10:00:01.007\t1.08124\t1.08136\t0\t0\n",
            )

    def test_does_not_overwrite_an_existing_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "hour.bin"
            write_hour_file(source, [1_700_000_000_000], [1.0], [1.1])
            output = root / "ticks.csv"
            output.write_text("keep", encoding="ascii")

            with self.assertRaises(FileExistsError):
                export_mt5_csv([source], output, decimals=5)
            self.assertEqual(output.read_text(encoding="ascii"), "keep")
