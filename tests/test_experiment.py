"""Compatibility and ordering for the supplied machining experiment."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from linebridge import core


ROOT = Path(__file__).resolve().parents[1]


class ExperimentLogs(unittest.TestCase):
    def test_experiment_csv_parses_as_one_table(self):
        source = ROOT / "real_machine_logs" / "source" / "experiment_13.csv"
        rows = core.read_rows(source)
        self.assertEqual(len(rows), 2234)
        self.assertTrue(all(len(row) == 48 for row in rows))
        tables = core.split_tables(rows)
        self.assertEqual(list(tables), ["ALL"])
        self.assertEqual(len(tables["ALL"]["rows"]), 2233)
        self.assertEqual(tables["ALL"]["columns"][-1], "Machining_Process")

    def test_experiment_has_priority_over_older_secom_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incoming = root / "incoming"
            incoming.mkdir()
            experiment = incoming / "experiment_13.csv"
            secom = incoming / "secom_0001_0100.csv"
            experiment.write_text("a\n1\n", encoding="utf-8")
            secom.write_text("a\n2\n", encoding="utf-8")
            old = time.time() - 100
            os.utime(secom, (old, old))
            cfg = core.normalize_config({"input_folder": str(incoming), "file_pattern": "*.csv",
                                         "priority_patterns": ["experiment_13.csv", "secom_*.csv"],
                                         "ledger_path": str(root / "ledger.db"), "stable_seconds": 0})
            self.assertEqual([p.name for p in core.Engine(cfg).pending_files()],
                             ["experiment_13.csv", "secom_0001_0100.csv"])


if __name__ == "__main__":
    unittest.main()
