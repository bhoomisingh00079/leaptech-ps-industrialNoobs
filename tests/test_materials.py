"""The YSUP-compatible material CSV, import report, cache and automatic folder feed."""
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import audit, config  # noqa: E402
from cncbridge.ysup import materials  # noqa: E402


def csv_content(headers, rows, encoding="utf-8"):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode(encoding)


class ParseMaterials(unittest.TestCase):
    def test_every_supported_header_alias_maps_to_its_canonical_field(self):
        for field, aliases in materials.FIELD_ALIASES.items():
            for alias in aliases:
                with self.subTest(field=field, alias=alias):
                    headers = [alias] if field == "part_number" else ["Part Number", alias]
                    row = ["P-1"] if field == "part_number" else ["P-1", "R-1"]
                    records, mapping = materials.parse_database_csv(csv_content(headers, [row]))
                    self.assertEqual(len(records), 1)
                    self.assertEqual(mapping[field], alias)

    def test_slide_sample_keeps_the_whole_row_and_customer_columns(self):
        content = ("Part Number,Description,Reel ID,LOT ID,Quantity,Customer Note\n"
                   "LTC9078563412,RES-10K-50%-R0603,UID1101,GRN109E,4000,Line A\n").encode()
        records, mapping = materials.parse_database_csv(content)
        self.assertEqual(records[0], {
            "part_number": "LTC9078563412", "reel_id": "UID1101", "lot_id": "GRN109E", "quantity": "4000",
            "extra": {"Part Number": "LTC9078563412", "Description": "RES-10K-50%-R0603", "Reel ID": "UID1101",
                      "LOT ID": "GRN109E", "Quantity": "4000", "Customer Note": "Line A"}})
        self.assertEqual(mapping["extra_fields"], ["Description", "Customer Note"])

    def test_the_first_header_matching_an_alias_wins(self):
        records, mapping = materials.parse_database_csv(
            b"Part Number,CmpID,Description\nP-1,P-2,Part\n")
        self.assertEqual((records[0]["part_number"], mapping["part_number"]), ("P-1", "Part Number"))

    def test_import_report_refuses_missing_parts_and_every_duplicate_reel_row(self):
        report = materials.import_report(
            b"Part Number,Reel ID,Quantity\nP-1,DUP,4\nP-2,dup,bad\n,OTHER,2\nP-3,UNIQUE,not-a-number\n",
            "reels.csv")
        self.assertEqual((report["rows_read"], report["rows_kept"], report["rows_refused"]), (4, 1, 3))
        self.assertEqual(report["refusal_counts"], {"no_part_number": 1, "duplicate_reel_id": 2})
        self.assertEqual([row["reason"] for row in report["refused"]],
                         ["duplicate_reel_id", "duplicate_reel_id", "no_part_number"])
        self.assertEqual(report["warnings"][0]["value"], "bad")
        self.assertIn("does not exclude", report["warnings"][0]["message"])
        self.assertEqual(report["records"][0]["part_number"], "P-3")

    def test_invalid_quantity_warns_and_row_is_kept(self):
        report = materials.import_report(b"Part Number,Quantity\nP-1,unknown\n")
        self.assertEqual(report["rows_kept"], 1)
        self.assertEqual(report["rows_refused"], 0)
        self.assertEqual(report["warnings"][0]["field"], "Quantity")

    def test_missing_part_column_and_empty_material_data_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Database CSV needs a Part Number column"):
            materials.parse_database_csv(b"Description,Reel ID\nPart,R-1\n")
        for content in (b"", b"Part Number,Description\n\n"):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "Database CSV has no material records"):
                materials.parse_database_csv(content)

    def test_utf8_bom_cp1252_and_semicolon_separator(self):
        bom = b"\xef\xbb\xbfPart Number;Description;Reel ID\nP-1;Resistor;R-1\n"
        records, mapping = materials.parse_database_csv(bom)
        self.assertEqual((records[0]["part_number"], mapping["reel_id"]), ("P-1", "Reel ID"))
        cp1252 = b"Part Number;Description\nP-2;R\xe9sistance\n"
        records, _ = materials.parse_database_csv(cp1252)
        self.assertEqual(records[0]["extra"]["Description"], "Résistance")


class MaterialSettings(unittest.TestCase):
    def test_v5_migration_adds_material_defaults_and_preserves_admin_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            stored = config.hash_password("existing-admin-password")
            path.write_text(json.dumps({"config_version": 5, "setup_password": stored}), encoding="utf-8")
            cfg = config.load_config(path)
        self.assertEqual(cfg["setup_password"], stored)
        self.assertEqual(cfg["ysup_database"], "ysup_materials.csv")
        self.assertEqual(cfg["ysup_fields"], {"part_number": True, "reel_id": True, "lot_id": False})
        self.assertEqual(cfg["ysup_extra_fields"], [])
        self.assertEqual(cfg["ysup_import_folder"], "")
        self.assertEqual(cfg["config_version"], 6)

    def test_partial_field_settings_merge_defaults_and_drop_blank_extra_names(self):
        cfg = config.normalize_config({"ysup_fields": {"lot_id": True},
                                       "ysup_extra_fields": ["Description", "", " Customer ", "Description"]})
        self.assertEqual(cfg["ysup_fields"], {"part_number": True, "reel_id": True, "lot_id": True})
        self.assertEqual(cfg["ysup_extra_fields"], ["Description", "Customer"])

    def test_audit_note_records_import_without_claiming_a_settings_change(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = audit.note(folder, "materials", "Imported customer.csv")
            self.assertEqual((entry["how"], entry["detail"], entry["changes"]),
                             ("materials", "Imported customer.csv", []))


class StoredMaterials(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.normalize_config({"stable_seconds": 0})
        materials.clear_cache()

    def tearDown(self):
        materials.clear_cache()

    def test_manual_import_stores_bom_csv_and_hash_checked_refused_rows(self):
        content = b"Part Number,Reel ID,Description\nP-1,DUP,first\nP-2,DUP,second\nP-3,R-3,ok\n,NO-PART,skip\n"
        result = materials.replace_database(content, self.cfg, self.root, "customer.csv")
        path = materials.database_path(self.cfg, self.root)
        sidecar = materials.refused_path(path)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(result["rows_refused"], 3)
        loaded = materials.load(self.cfg, self.root)
        self.assertEqual([record["part_number"] for record in loaded["records"]], ["P-3"])
        self.assertEqual(loaded["source"], "customer.csv")
        self.assertEqual(len(loaded["refused"]), 3)
        sidecar_doc = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(sidecar_doc["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_external_replacement_does_not_reuse_stale_refusal_metadata(self):
        materials.replace_database(b"Part Number\nP-1\n,\n", self.cfg, self.root, "source.csv")
        path = materials.database_path(self.cfg, self.root)
        path.write_bytes(b"Part Number\nP-2\n")
        loaded = materials.load(self.cfg, self.root)
        self.assertEqual(loaded["records"][0]["part_number"], "P-2")
        self.assertEqual((loaded["source"], loaded["refused"]), (path.name, []))

    def test_load_returns_none_for_a_missing_database_and_caches_by_mtime(self):
        self.assertIsNone(materials.load(self.cfg, self.root))
        materials.replace_database(b"Part Number\nP-1\n", self.cfg, self.root, "one.csv")
        first = materials.load(self.cfg, self.root)
        self.assertIs(materials.load(self.cfg, self.root), first)
        path = materials.database_path(self.cfg, self.root)
        path.write_bytes(b"Part Number\nP-2\n")
        future = path.stat().st_mtime + 2
        os.utime(path, (future, future))
        second = materials.load(self.cfg, self.root)
        self.assertIsNot(second, first)
        self.assertEqual(second["records"][0]["part_number"], "P-2")

    def test_a_database_write_failure_keeps_the_previous_csv_and_sidecar(self):
        materials.replace_database(b"Part Number\nP-OLD\n", self.cfg, self.root, "old.csv")
        path = materials.database_path(self.cfg, self.root)
        sidecar = materials.refused_path(path)
        before_csv, before_sidecar = path.read_bytes(), sidecar.read_bytes()
        original = materials.delivery.atomic_write
        calls = []

        def fail_database_once(target, data):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("simulated disk failure")
            return original(target, data)

        with mock.patch.object(materials.delivery, "atomic_write", side_effect=fail_database_once):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                materials.replace_database(b"Part Number\nP-NEW\n", self.cfg, self.root, "new.csv")
        self.assertEqual(path.read_bytes(), before_csv)
        self.assertEqual(sidecar.read_bytes(), before_sidecar)

    def test_automatic_import_moves_good_and_bad_files_and_keeps_old_database_on_failure(self):
        folder = self.root / "feed"
        folder.mkdir()
        cfg = config.normalize_config(dict(self.cfg, ysup_import_folder=str(folder)))
        materials.replace_database(b"Part Number\nP-OLD\n", cfg, self.root, "old.csv")
        good = folder / "new.csv"
        good.write_bytes(b"Part Number,Description\nP-NEW,Good\n")
        result = materials.import_pending(cfg, self.root)
        self.assertEqual(result["rows_kept"], 1)
        self.assertFalse(good.exists())
        self.assertTrue((folder / "imported" / "new.csv").exists())
        self.assertEqual(materials.load(cfg, self.root)["records"][0]["part_number"], "P-NEW")

        old_database = materials.database_path(cfg, self.root).read_bytes()
        bad = folder / "bad.csv"
        bad.write_bytes(b"Description,Reel ID\nBad,R-1\n")

        class AlertSpy:
            def __init__(self):
                self.events = []

            def event(self, text, alert_cfg, title=None):
                self.events.append((text, alert_cfg, title))

        spy = AlertSpy()
        failed = materials.import_pending(cfg, self.root, spy)
        self.assertIn("Part Number column", failed["error"])
        self.assertEqual(materials.database_path(cfg, self.root).read_bytes(), old_database)
        self.assertTrue((folder / "rejected" / "bad.csv").exists())
        self.assertEqual(spy.events[0][2], "Material database import failed")

    def test_no_material_rows_cannot_replace_a_valid_database(self):
        materials.replace_database(b"Part Number\nP-OLD\n", self.cfg, self.root)
        path = materials.database_path(self.cfg, self.root)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Database CSV has no material records"):
            materials.replace_database(b"Part Number\n,\n", self.cfg, self.root)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
