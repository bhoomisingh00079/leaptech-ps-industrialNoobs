"""Run:  python -m unittest discover -s tests -v"""
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

from linebridge import core  # noqa: E402
from linebridge.webui import integration_check  # noqa: E402
import machine_simulator  # noqa: E402
import mock_mes_server  # noqa: E402

logging.getLogger("linebridge").setLevel(logging.CRITICAL)
SAMPLE = ROOT / "samples" / "pnp_L1M8_board_00001.csv"
OVEN = ROOT / "samples" / "reflow_oven_log.csv"


class Parsing(unittest.TestCase):
    def setUp(self):
        self.rows = core.read_rows(SAMPLE)
        self.tables = core.split_tables(self.rows)

    def test_tables_and_ranges(self):
        self.assertEqual(list(self.tables), ["HEADER", "L1xM8", "FEEDER_STATUS", "NOZZLE_STATS", "ERRORS"])
        self.assertEqual(self.tables["L1xM8"]["range"], "A12:I24")
        self.assertEqual(len(self.tables["L1xM8"]["rows"]), 12)

    def test_trailing_padding_removed(self):
        self.assertEqual(self.tables["HEADER"]["columns"], ["Key", "Value"])

    def test_selector_name_variants(self):
        for s in ("L1xM8", "l1 x m8", "L1×M8", "[L1xM8]", "Table: L1xM8", "L1x*"):
            chosen, warn = core.select(self.rows, self.tables, [s])
            self.assertEqual(list(chosen), ["L1xM8"], s)
            self.assertEqual(warn, [])

    def test_column_pick(self):
        chosen, warn = core.select(self.rows, self.tables, ["L1xM8[ref, RESULT, nope]"])
        self.assertEqual(chosen["L1xM8"]["columns"], ["Ref", "Result"])
        self.assertTrue(any("nope" in w for w in warn))

    def test_cell_range(self):
        chosen, _ = core.select(self.rows, self.tables, ["A2:B9"])
        self.assertEqual(chosen["A2:B9"]["columns"], ["Key", "Value"])
        self.assertEqual(chosen["A2:B9"]["rows"][0], {"Key": "Machine", "Value": "PNP-M8"})

    def test_cell_range_x_syntax_and_star(self):
        rows = core.read_rows(OVEN)  # semicolon file with no table titles
        chosen, _ = core.select(rows, core.split_tables(rows), ["B1 x D*"])
        t = chosen["B1:D*"]
        self.assertEqual(t["columns"], ["Zone", "Setpoint_C", "Actual_C"])
        self.assertEqual(len(t["rows"]), 6)

    def test_untitled_file_is_ALL(self):
        rows = core.read_rows(OVEN)
        self.assertEqual(list(core.split_tables(rows)), ["ALL"])

    def test_missing_selector_warns(self):
        chosen, warn = core.select(self.rows, self.tables, ["L9xM9"])
        self.assertEqual(chosen, {})
        self.assertIn("not found", warn[0])


class Rendering(unittest.TestCase):
    def setUp(self):
        self.cfg = core.normalize_config({"selections": ["HEADER", "ERRORS"],
                                          "manual_inputs": [{"label": "Operator", "value": "OP-17"},
                                                            {"label": "Shift", "value": "B"}]})
        self.rows = core.read_rows(SAMPLE)

    def out(self, fmt, layout="sections"):
        self.cfg.update(output_format=fmt, csv_layout=layout)
        return core.build_output("x.csv", "abc", self.rows, self.cfg)[0]

    def test_json(self):
        d = json.loads(self.out("json"))
        self.assertEqual(d["manual_inputs"], {"Operator": "OP-17", "Shift": "B"})
        self.assertEqual(list(d["tables"]), ["HEADER", "ERRORS"])
        self.assertEqual(d["tables"]["HEADER"]["row_count"], 7)

    def test_csv_sections(self):
        t = self.out("csv")
        self.assertIn("#manual:Operator,OP-17", t)
        self.assertIn("[HEADER]", t)

    def test_csv_flat(self):
        lines = self.out("csv", "flat").splitlines()
        self.assertTrue(lines[0].startswith("source_file,processed_at,table,Operator,Shift"))
        self.assertEqual(len(lines), 1 + 7 + 1)

    def test_txt(self):
        t = self.out("txt")
        self.assertIn("OPERATOR", t)
        self.assertIn("=== HEADER  [A2:B9]  (7 rows) ===", t)

    def test_pdf_metadata_tables_and_repeated_headers_across_pages(self):
        self.cfg.update(output_format="pdf", station_name="Station-PDF")
        self.cfg["selections"] = ["HEADER"]
        self.rows = [["[HEADER]"], ["ID", "Value"], *[[str(i), "payload"] for i in range(100)]]
        data = self.out("pdf")
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        texts = [bytes.fromhex(x.decode("ascii")).decode("cp1252")
                 for x in re.findall(rb"<([0-9A-F]+)> Tj", data)]
        self.assertIn("Source file: x.csv", texts)
        self.assertIn("Station: Station-PDF", texts)
        self.assertIn("Operator: OP-17", texts)
        self.assertTrue(any(value.startswith("HEADER") for value in texts))
        self.assertIn("ID", texts)
        self.assertIn("Value", texts)
        self.assertIn("payload", texts)
        self.assertGreaterEqual(data.count(b"/Type /Page /Parent"), 2)
        self.assertGreaterEqual(texts.count("ID"), 2)


class EngineEndToEnd(unittest.TestCase):
    PORT = 18765

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.srv, cls.mes = mock_mes_server.serve(cls.PORT, "tkn", str(cls.tmp / "mes"), block=False)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(dir=self.tmp))
        (self.ws / "in").mkdir()
        self.cfg = core.normalize_config({
            "input_folder": str(self.ws / "in"), "backup_folder": str(self.ws / "bak"),
            "output_folder": str(self.ws / "out"), "ledger_path": str(self.ws / "ledger.db"),
            "selections": ["L1xM8[Ref,Result]", "ERRORS"], "stable_seconds": 0,
            "manual_inputs": [{"label": "Operator", "value": "OP-17"}],
            "shared_enabled": True, "shared_folder": str(self.ws / "share"),
            "api_enabled": True, "api_url": f"http://127.0.0.1:{self.PORT}/api/upload", "api_token": "tkn"})
        self.mes.online = True

    def drop(self, name, text=None):
        p = self.ws / "in" / name
        p.write_text(text or SAMPLE.read_text())
        old = time.time() - 10
        os.utime(p, (old, old))
        return p

    def test_full_pipeline(self):
        before = len(self.mes.items)
        self.drop("b1.csv")
        eng = core.Engine(self.cfg)
        self.assertEqual(eng.scan_once(), 1)
        out = list((self.ws / "out").glob("*.json"))
        self.assertEqual(len(out), 1)
        self.assertTrue((self.ws / "share" / out[0].name).exists())
        self.assertEqual(len(self.mes.items), before + 1)
        self.assertEqual(list((self.ws / "in").iterdir()), [])                    # input emptied
        self.assertEqual(len(list((self.ws / "bak").rglob("b1.csv"))), 1)         # source archived
        self.assertFalse(list(self.ws.rglob("*.part")))                            # atomic writes clean
        h = eng.ledger.recent()[0]
        self.assertEqual((h["status"], h["shared"], h["api"]), ("ok", "ok", "ok"))

    def test_outage_queue_and_retry(self):
        self.mes.online = False
        self.drop("b2.csv")
        eng = core.Engine(self.cfg)
        eng.scan_once()
        self.assertEqual(eng.queued()["api"], 1)
        self.assertEqual(eng.ledger.recent()[0]["api"], "queued")
        self.mes.online = True
        eng.scan_once()
        self.assertEqual(eng.queued()["api"], 0)
        self.assertEqual(eng.ledger.recent()[0]["api"], "ok (retried)")

    def test_pdf_automatic_pipeline_shared_folder_and_api_delivery(self):
        before = len(self.mes.items)
        self.drop("pdf_batch.csv")
        eng = core.Engine(dict(self.cfg, output_format="pdf"))
        self.assertEqual(eng.scan_once(), 1)
        out = next((self.ws / "out").glob("*.pdf"))
        self.assertTrue(out.read_bytes().startswith(b"%PDF-1.4"))
        self.assertEqual((self.ws / "share" / out.name).read_bytes(), out.read_bytes())
        received = self.mes.items[before]
        self.assertEqual(received["type"], "application/pdf")
        self.assertEqual(Path(received["path"]).read_bytes(), out.read_bytes())
        record = eng.ledger.recent()[0]
        self.assertEqual((record["status"], record["shared"], record["api"]), ("ok", "ok", "ok"))

    def test_duplicate_guard(self):
        eng = core.Engine(self.cfg)
        self.drop("first.csv")
        eng.scan_once()
        self.drop("resent.csv")
        eng.scan_once()
        self.assertEqual(eng.ledger.stats()["duplicate"], 1)
        self.assertTrue((self.ws / "bak" / "_duplicates" / "resent.csv").exists())

    def test_bad_file_quarantined(self):
        self.drop("weird.csv", "[OTHER]\na,b\n1,2\n")
        eng = core.Engine(self.cfg)
        eng.scan_once()
        self.assertTrue((self.ws / "bak" / "_failed" / "weird.csv").exists())
        self.assertEqual(eng.ledger.stats()["failed"], 1)

    def test_file_still_being_written_is_skipped(self):
        cfg = dict(self.cfg, stable_seconds=30)
        (self.ws / "in" / "fresh.csv").write_text(SAMPLE.read_text())
        self.assertEqual(core.Engine(cfg).scan_once(), 0)
        self.assertTrue((self.ws / "in" / "fresh.csv").exists())

    def test_multipart_upload(self):
        self.drop("b3.csv")
        core.Engine(dict(self.cfg, api_body="multipart", output_format="txt")).scan_once()
        item = self.mes.items[-1]
        self.assertTrue(item["name"].endswith(".txt"))
        self.assertIn("OPERATOR", Path(item["path"]).read_text())


class Simulator(unittest.TestCase):
    def test_generated_log_parses(self):
        rows = core.read_rows(machine_simulator.make_log(5, "L2", "M3", seed=1))
        self.assertIn("L2xM3", core.split_tables(rows))


class IntegrationPreflight(unittest.TestCase):
    def test_check_uses_bundled_sample_when_machine_folder_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            cfg = core.normalize_config({
                "input_folder": str(ws / "input"),
                "output_folder": str(ws / "output"),
                "backup_folder": str(ws / "backup"),
                "selections": ["L1xM8[Ref,Result]"],
            })
            (ws / "input").mkdir()
            result = integration_check({"config": cfg})
            self.assertTrue(result["ok"])
            parser = next(c for c in result["checks"] if c["key"] == "parser")
            self.assertIn("5 table(s)", parser["detail"])


if __name__ == "__main__":
    unittest.main()
