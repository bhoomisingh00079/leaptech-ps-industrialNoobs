"""Checks for the supplied SECOM adapter and the panel's Start path."""
import csv
import json
import tempfile
import time
import unittest
from pathlib import Path

from linebridge import core
from linebridge.webui import Controller, integration_check
from tools.prepare_secom import prepare


ROOT = Path(__file__).resolve().parents[1]


class SecomStaging(unittest.TestCase):
    def test_alignment_and_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").write_text("1 NaN 3\n4 5 6\n", encoding="utf-8")
            (root / "labels").write_text(
                '-1 "19/07/2008 11:55:00"\n1 "19/07/2008 12:32:00"\n', encoding="utf-8")
            self.assertEqual(prepare(root / "data", root / "labels", root / "incoming", 1), (2, 3, 1, 2))
            with (root / "incoming" / "secom_0001_0001.csv").open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["test_timestamp"], "2008-07-19 11:55:00")
            self.assertEqual(rows[0]["yield_result"], "pass")
            self.assertEqual(rows[0]["sensor_002"], "")
            with self.assertRaises(FileExistsError):
                prepare(root / "data", root / "labels", root / "incoming", 1)

    def test_misaligned_rows_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").write_text("1 2\n3\n", encoding="utf-8")
            (root / "labels").write_text(
                '-1 "19/07/2008 11:55:00"\n1 "19/07/2008 12:32:00"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "row 2"):
                prepare(root / "data", root / "labels", root / "incoming")

    def test_panel_start_processes_staged_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incoming = root / "incoming"
            incoming.mkdir()
            source = ROOT / "real_machine_logs" / "source"
            (root / "data").write_text("\n".join((source / "secom.data").read_text(encoding="utf-8").splitlines()[:2]), encoding="utf-8")
            (root / "labels").write_text("\n".join((source / "secom_labels.data").read_text(encoding="utf-8").splitlines()[:2]), encoding="utf-8")
            prepare(root / "data", root / "labels", incoming)
            batch = incoming / "secom_0001_0002.csv"
            cfg = json.loads((ROOT / "config.secom.json").read_text(encoding="utf-8"))
            cfg.update(input_folder=str(incoming), output_folder=str(root / "output"),
                       backup_folder=str(root / "backup"), ledger_path=str(root / "ledger.db"),
                       stable_seconds=0, poll_seconds=0.1)
            check = integration_check({"config": cfg})
            self.assertTrue(check["ok"], check)
            controller = Controller(root / "config.json")
            controller.save(cfg)
            controller.start()
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and controller.status()["stats"]["ok"] < 1:
                    time.sleep(0.1)
                self.assertEqual(controller.status()["stats"]["ok"], 1)
            finally:
                controller.stop()
            outputs = list((root / "output").glob("*.json"))
            self.assertEqual(len(outputs), 1)
            result = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(result["tables"]["ALL"]["row_count"], 2)
            self.assertEqual(len(result["tables"]["ALL"]["columns"]), 594)
            self.assertEqual(result["tables"]["ALL"]["rows"][0]["yield_result"], "pass")
            self.assertTrue(list((root / "backup").rglob(batch.name)))


if __name__ == "__main__":
    unittest.main()
