"""The settings change log: who changed what, secrets kept out, edits made outside CNCBridge noticed."""
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cncbridge import alerts, audit, config, engine  # noqa: E402

logging.getLogger("cncbridge").setLevel(logging.CRITICAL)
SECRET = "s3cr3t-token-value"


def settings(**values):
    return config.normalize_config(values)


class ChangeLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.json"

    def save(self, how="save", detail="", **values):
        config.save_config(settings(**values), self.path, how, detail)

    def entries(self):
        return audit.read(self.dir)

    def test_a_save_is_logged_with_who_and_what(self):
        self.save(station_name="Line 1", source_folder="C:/in")
        entry = self.entries()[0]
        self.assertEqual(entry["how"], "save")
        self.assertTrue(entry["user"])
        self.assertIn("computer", entry)
        self.assertIn("elevated", entry)
        changed = {c["setting"]: (c["old"], c["new"]) for c in entry["changes"]}
        self.assertEqual(changed["Station name"], ("(empty)", "Line 1"))
        self.assertEqual(changed["Machine folder"], ("(empty)", "C:/in"))
        self.assertEqual(entry["sha256"], audit.file_sha(self.path))

    def test_only_what_changed_is_listed(self):
        self.save(station_name="Line 1", source_folder="C:/in")
        self.save(station_name="Line 1", source_folder="D:/in")
        self.assertEqual([(c["setting"], c["old"], c["new"]) for c in self.entries()[0]["changes"]],
                         [("Machine folder", "C:/in", "D:/in")])

    def test_saving_the_same_settings_again_adds_nothing(self):
        self.save(station_name="Line 1")
        count = len(self.entries())
        self.save(station_name="Line 1")
        self.assertEqual(len(self.entries()), count)

    def test_secrets_are_never_written_only_that_they_changed(self):
        self.save(api_token=SECRET, ftp_password=SECRET, alert_email_password=SECRET,
                  api_headers="X-Api-Key: " + SECRET, setup_password="pbkdf2$1$aa$" + SECRET)
        text = audit.log_path(self.dir).read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        changed = {c["setting"]: (c["old"], c["new"]) for c in self.entries()[0]["changes"]}
        self.assertEqual(changed["API token"], ("not set", "set"))
        self.assertEqual(changed["Setup password"], ("not set", "set"))
        self.save(api_token="another", ftp_password="", alert_email_password=SECRET,
                  api_headers="X-Api-Key: " + SECRET, setup_password="pbkdf2$1$aa$" + SECRET)
        changed = {c["setting"]: (c["old"], c["new"]) for c in self.entries()[0]["changes"]}
        self.assertEqual(changed, {"API token": ("set", "changed"), "FTP password": ("set", "not set")})

    def test_the_query_part_of_an_address_is_not_written(self):
        self.save(api_url="https://mes.example/api/files?key=" + SECRET)
        self.assertNotIn(SECRET, audit.log_path(self.dir).read_text(encoding="utf-8"))
        self.assertIn("https://mes.example/api/files?...", audit.log_path(self.dir).read_text(encoding="utf-8"))

    def test_ranges_and_typed_values_are_listed_by_slot(self):
        self.save(selections=[{"name": "Temp", "range": "A1:B4", "header": True}])
        self.save(selections=[{"name": "Temp", "range": "A1:B9", "header": True}],
                  manual_inputs=[{"name": "Operator", "value": "Sam"}])
        changed = {c["setting"]: (c["old"], c["new"]) for c in self.entries()[0]["changes"]}
        self.assertEqual(changed["Range 1"], ("Temp: A1:B4", "Temp: A1:B9"))
        self.assertEqual(changed["Typed value 1"], ("(empty)", "Operator = Sam"))

    def test_how_and_detail_say_what_led_to_the_save(self):
        self.save(how="import", detail="line2 settings.json", station_name="Line 2")
        entry = self.entries()[0]
        self.assertEqual((entry["how"], entry["detail"]), ("import", "line2 settings.json"))

    def test_admin_access_activity_records_action_without_credentials(self):
        config.save_config(settings(station_name="Line 9", setup_password=config.hash_password(SECRET)), self.path)
        audit.activity(self.dir, "Admin login")
        text = audit.log_path(self.dir).read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        entry = self.entries()[0]
        self.assertEqual((entry["how"], entry["detail"], entry["changes"]),
                         ("access", "Admin login", []))

    def test_the_log_is_tidied_to_the_newest_entries(self):
        saved = audit.KEEP, audit.TIDY_ABOVE_BYTES
        self.addCleanup(lambda: (setattr(audit, "KEEP", saved[0]), setattr(audit, "TIDY_ABOVE_BYTES", saved[1])))
        audit.KEEP, audit.TIDY_ABOVE_BYTES = 3, 0
        for n in range(6):
            self.save(station_name=f"Line {n}")
        stations = [c["new"] for e in self.entries() for c in e["changes"] if c["setting"] == "Station name"]
        self.assertEqual(stations, ["Line 5", "Line 4", "Line 3"])

    def test_a_failing_log_never_stops_a_save(self):
        original = audit.record
        self.addCleanup(setattr, audit, "record", original)

        def broken(*args, **kwargs):
            raise OSError("disk full")
        audit.record = broken
        self.save(station_name="Still saved")
        self.assertEqual(config.load_config(self.path)["station_name"], "Still saved")


class EditedOutside(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.json"

    def edit_by_hand(self, **values):
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.update(values)
        self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def test_a_file_that_matches_the_last_save_is_fine(self):
        config.save_config(settings(station_name="A"), self.path)
        self.assertIsNone(audit.check_outside_edit(self.dir))

    def test_a_hand_edit_is_noticed_logged_once_and_itemised(self):
        config.save_config(settings(station_name="A", setup_password="pbkdf2$1$aa$bb"), self.path)
        self.edit_by_hand(setup_password="", source_folder="C:/elsewhere")
        entry = audit.check_outside_edit(self.dir)
        self.assertEqual((entry["how"], entry["user"]), ("outside", audit.OUTSIDE))
        changed = {c["setting"]: (c["old"], c["new"]) for c in entry["changes"]}
        self.assertEqual(changed["Setup password"], ("set", "not set"))
        self.assertEqual(changed["Machine folder"], ("(empty)", "C:/elsewhere"))
        self.assertEqual(audit.read(self.dir, 1)[0]["how"], "outside")
        self.assertIsNone(audit.check_outside_edit(self.dir))  # announced once

    def test_a_save_after_a_hand_edit_still_records_the_edit(self):
        config.save_config(settings(station_name="A"), self.path)
        self.edit_by_hand(station_name="Edited")
        config.save_config(settings(station_name="B"), self.path)
        self.assertEqual([e["how"] for e in audit.read(self.dir)[:3]], ["save", "outside", "save"])

    def test_a_file_from_before_the_log_gets_a_starting_point_not_an_alarm(self):
        self.path.write_text(json.dumps({"station_name": "Old"}), encoding="utf-8")
        self.assertIsNone(audit.check_outside_edit(self.dir))
        self.assertEqual([e["how"] for e in audit.read(self.dir)], ["baseline"])
        self.assertIsNone(audit.check_outside_edit(self.dir))
        self.edit_by_hand(station_name="Changed")
        self.assertEqual(audit.check_outside_edit(self.dir)["how"], "outside")

    def test_nothing_to_check_without_a_settings_file(self):
        self.assertIsNone(audit.check_outside_edit(self.dir))

    def test_the_engine_raises_an_alert_for_a_hand_edit(self):
        sent = []
        self.addCleanup(alerts.SENDERS.__setitem__, "notify", alerts.SENDERS["notify"])
        alerts.SENDERS["notify"] = lambda title, text, kind, *a: sent.append((title, text))
        config.save_config(settings(station_name="Line 7"), self.path)
        self.edit_by_hand(shared_folder="C:/hand")
        eng = engine.Engine(settings(station_name="Line 7"), self.dir, config_path=self.path)
        eng.alerts.flush()
        self.assertEqual(len(sent), 1)
        self.assertIn("outside", sent[0][0])
        self.assertIn("Shared folder", sent[0][1])
        engine.Engine(settings(station_name="Line 7"), self.dir, config_path=self.path).alerts.flush()
        self.assertEqual(len(sent), 1)  # not again


if __name__ == "__main__":
    unittest.main()
