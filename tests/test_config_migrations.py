"""The settings file carries a version, so a later layout change can be migrated instead of guessed."""
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import config  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)


class SettingsVersion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.json"

    def write(self, **values):
        self.path.write_text(json.dumps(values), encoding="utf-8")

    def test_a_file_without_a_version_is_an_old_one_and_loads(self):
        self.write(station_name="Line 2", source_folder="C:/in")  # as version 1.2.0 wrote it
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["station_name"], cfg["source_folder"]), ("Line 2", "C:/in"))
        self.assertEqual(cfg["config_version"], config.SETTINGS_VERSION)

    def test_a_version_3_file_loads_with_nothing_to_process_first(self):
        self.write(config_version=3, station_name="Line 3", file_pattern="*.csv")
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["priority_patterns"], cfg["file_pattern"], cfg["config_version"]),
                         ("", "*.csv", config.SETTINGS_VERSION))

    def test_v4_migration_preserves_the_existing_admin_password_hash(self):
        stored = config.hash_password("old setup password")
        self.write(config_version=4, station_name="Line 4", setup_password=stored)
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["setup_password"], stored)
        self.assertEqual(cfg["admin_idle_minutes"], 0)
        self.assertEqual(cfg["config_version"], config.SETTINGS_VERSION)

    def test_v4_migration_adds_an_empty_password_when_none_was_set(self):
        self.write(config_version=4, station_name="Line 4")
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["setup_password"], cfg["admin_idle_minutes"], cfg["config_version"]),
                         ("", 0, config.SETTINGS_VERSION))

    def test_file_patterns_are_tidied(self):
        cfg = config.normalize_config({"file_pattern": " *.csv ;*.json;", "priority_patterns": "urgent_*\nrework_*"})
        self.assertEqual((cfg["file_pattern"], cfg["priority_patterns"]), ("*.csv; *.json", "urgent_*; rework_*"))
        self.assertEqual(config.normalize_config({"file_pattern": " ; "})["file_pattern"], "*.csv")

    def test_saving_stamps_the_current_version(self):
        config.save_config(config.normalize_config({"station_name": "A"}), self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["config_version"], config.SETTINGS_VERSION)

    def test_each_migration_runs_once_in_order(self):
        calls = []
        saved = dict(config.MIGRATIONS), config.SETTINGS_VERSION
        self.addCleanup(lambda: (config.MIGRATIONS.clear(), config.MIGRATIONS.update(saved[0]),
                                 setattr(config, "SETTINGS_VERSION", saved[1])))
        config.SETTINGS_VERSION = 4
        config.MIGRATIONS.update({2: lambda raw: calls.append(2) or dict(raw, a=1),
                                  3: lambda raw: calls.append(3) or dict(raw, b=raw["a"] + 1)})
        out = config.migrate({"config_version": 2})
        self.assertEqual((calls, out["a"], out["b"], out["config_version"]), ([2, 3], 1, 2, 4))
        calls.clear()
        config.migrate({"config_version": 4})
        self.assertEqual(calls, [])

    def test_a_newer_file_is_refused_when_saving_but_still_read(self):
        newer = config.SETTINGS_VERSION + 1
        self.write(config_version=newer, station_name="From the future", brand_new_key=True)
        self.assertEqual(config.load_config(self.path)["station_name"], "From the future")
        with self.assertRaises(config.SettingsTooNew):
            config.save_config(config.normalize_config({"station_name": "Mine"}), self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["brand_new_key"], True)  # untouched

    def test_an_old_export_can_be_imported(self):
        export = Path(self._tmp.name) / "export.json"
        export.write_text(json.dumps({"exported_by": "CNCBridge 1.2.0", "station_name": "Old"}), encoding="utf-8")
        cfg = config.import_settings(export, config.normalize_config({}))
        self.assertEqual((cfg["station_name"], cfg["config_version"]), ("Old", config.SETTINGS_VERSION))

    def test_an_export_from_a_newer_program_is_not_imported(self):
        export = Path(self._tmp.name) / "export.json"
        export.write_text(json.dumps({"config_version": config.SETTINGS_VERSION + 1, "station_name": "X"}),
                          encoding="utf-8")
        with self.assertRaises(config.SettingsTooNew):
            config.import_settings(export, config.normalize_config({}))

    def test_exports_carry_the_version(self):
        out = Path(self._tmp.name) / "export.json"
        config.export_settings(config.normalize_config({}), out)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["config_version"], config.SETTINGS_VERSION)

    def test_a_broken_version_counts_as_an_old_file(self):
        for junk in ("two", None, -3, [1]):
            self.assertEqual(config.file_version({"config_version": junk}), 1)

    def test_password_policy_and_legacy_hash_verification(self):
        self.assertEqual(config.MIN_PASSWORD, 12)
        modern = "machine-admin-password"
        legacy = "old1"
        self.assertTrue(config.check_password(modern, config.hash_password(modern)))
        self.assertTrue(config.check_password(legacy, config.hash_password(legacy)))


if __name__ == "__main__":
    unittest.main()
