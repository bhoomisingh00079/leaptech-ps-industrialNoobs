"""Live HTTP tests for the optional YSUP-Link receiver."""
import base64
import http.client
import json
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cncbridge.ysup import materials, records, replies
from cncbridge.ysup.server import YsupServer, _authenticated


def sample(func="CmpStatusReport"):
    return {"FuncName": func, "TraceDate": "20260102030405006",
            "MchInfo": {"MchName": "M1", "MchSerial": "S123", "MchModel": "YRM20",
                        "LaneNo": 1, "LineName": "L1"}}


class YsupReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.reports = []
        self.cfg = {"ysup_enabled": True, "ysup_listen_address": "127.0.0.1", "ysup_port": 0,
                    "ysup_basic_username": "", "ysup_basic_password": "", "ysup_api_token": "",
                    "ysup_database": "ysup_materials.csv"}
        self.receiver = YsupServer(self.cfg, self.data, lambda *args: self.reports.append(args))
        self.assertTrue(self.receiver.start())
        self.assertTrue((self.data / "ysup.db").is_file())
        self.addCleanup(self.receiver.stop)
        self.port = self.receiver.server.server_address[1]

    def post(self, body, path="/", headers=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        self.addCleanup(connection.close)
        values = {"client-name": "Relay1", "Content-Type": content_type}
        values.update(headers or {})
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        connection.request("POST", path, data, values)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None

    def db_count(self):
        with records.connect(self.data) as db:
            return db.execute("SELECT COUNT(*) FROM ysup_messages").fetchone()[0]

    def test_simple_reply_both_supported_paths_and_record(self):
        for path in ("/", "/ysup/api"):
            status, body = self.post(sample(), path)
            self.assertEqual((status, body), (200, {"FuncName": "CmpStatusReport"}))
        self.assertEqual(self.db_count(), 2)
        with records.connect(self.data) as db:
            row = db.execute("SELECT client_name, caller_address, func_name, machine_serial, http_status, "
                             "elapsed_ms, request_json, response_json FROM ysup_messages ORDER BY id LIMIT 1").fetchone()
        self.assertEqual(row[:5], ("Relay1", "127.0.0.1", "CmpStatusReport", "S123", 200))
        self.assertGreaterEqual(row[5], 0)
        self.assertEqual(json.loads(row[6]), sample())
        self.assertEqual(json.loads(row[7]), {"FuncName": "CmpStatusReport"})

    def test_request_checks_exact_status_and_error_contract(self):
        cases = [
            (sample(), {"client-name": " "}, "application/json", 400, "Missing required client-name header"),
            (sample(), {}, "text/plain", 400, "Content-Type must be application/json"),
            (sample(), {"Authorization": "Bearer wrong"}, "application/json", 200, None),
        ]
        # With no credentials configured, loopback access is deliberately accepted.
        self.receiver.cfg.update(ysup_basic_username="operator", ysup_basic_password="secret")
        for body, headers, content_type, expected, message in cases[:2]:
            status, response = self.post(body, headers=headers, content_type=content_type)
            self.assertEqual(status, expected)
            self.assertEqual(response["message"], message)
            self.assertIn("data", response)
        status, response = self.post(sample(), headers={"Authorization": "Bearer wrong"})
        self.assertEqual((status, response), (401, {"message": "Unauthenticated", "data": None}))

    def test_missing_auth_header_loopback_and_remote_rule(self):
        self.assertTrue(_authenticated({}, self.cfg, "127.0.0.1")[0])
        self.assertTrue(_authenticated({}, self.cfg, "::1")[0])
        self.assertFalse(_authenticated({}, self.cfg, "192.0.2.9")[0])
        status, body = self.post(sample(), headers={"client-name": ""})
        self.assertEqual((status, body["message"]), (400, "Missing required client-name header"))

    def test_basic_bearer_and_header_token_authentication(self):
        cfg = dict(self.cfg, ysup_basic_username="user", ysup_basic_password="pass", ysup_api_token="token")
        basic = "Basic " + base64.b64encode(b"user:pass").decode()
        self.assertTrue(_authenticated({"Authorization": basic}, cfg, "192.0.2.5")[0])
        self.assertTrue(_authenticated({"Authorization": "Bearer token"}, cfg, "192.0.2.5")[0])
        self.assertTrue(_authenticated({"X-YSUP-Token": "token"}, cfg, "192.0.2.5")[0])
        self.assertFalse(_authenticated({"Authorization": "Basic bad"}, cfg, "192.0.2.5")[0])

    def test_live_server_accepts_configured_credentials(self):
        self.receiver.cfg.update(ysup_basic_username="user", ysup_basic_password="pass", ysup_api_token="token")
        basic = "Basic " + base64.b64encode(b"user:pass").decode()
        for header in ({"Authorization": basic}, {"Authorization": "Bearer token"},
                       {"X-YSUP-Token": "token"}):
            status, body = self.post(sample(), headers=header)
            self.assertEqual((status, body), (200, {"FuncName": "CmpStatusReport"}))
        self.assertEqual(self.post(sample(), headers={"Authorization": "Bearer incorrect"})[0], 401)

    def test_invalid_json_empty_json_and_unknown_function(self):
        for payload in (b"", b"{"):
            status, response = self.post(payload)
            self.assertEqual(status, 400)
            self.assertEqual(response["message"], "Incorrect Request Body Data")
        unknown = sample("NoSuchFunction")
        status, response = self.post(unknown)
        self.assertEqual((status, response["message"]), (404, "FuncName not available in Request Body"))
        missing = {"FuncName": "CmpStatusReport"}
        status, response = self.post(missing)
        self.assertEqual((status, response["message"]), (400, "Incorrect Request Body Data"))

    def test_invalid_path(self):
        status, body = self.post(sample(), path="/unknown")
        self.assertEqual((status, body["message"]), (404, "Not found"))

    def test_known_reply_shapes(self):
        for name in replies.SIMPLE:
            self.assertEqual(replies.build(sample(name)), {"FuncName": name})
        entry = sample("PcbEntryEvent")
        entry["PcbInfo"] = {"PcbID": "PCB7", "PgmName": "JOB"}
        entry["Blk"] = [{"BlkNo": 4, "Mount": 2}, {"BlkNo": 5}]
        self.assertEqual(replies.build(entry), {"FuncName": "PcbEntryEvent", "TraceDate": entry["TraceDate"],
                         "MchInfo": entry["MchInfo"], "Err": 0, "ErrMsg": "", "PcbInfo": entry["PcbInfo"],
                         "Blk": [{"BlkNo": 4, "Mount": 2}, {"BlkNo": 5, "Mount": 1}]})
        exit_request = dict(entry, FuncName="PcbExitEvent")
        self.assertNotIn("Blk", replies.build(exit_request))
        self.assertEqual(replies.build(exit_request)["PcbInfo"], entry["PcbInfo"])

    def test_placeholder_responses_and_database_gate(self):
        for func in ("SetupRequest", "MaterialRequest"):
            status, body = self.post(sample(func))
            self.assertEqual(status, 503)
            self.assertEqual(body["message"], "Material database has not been imported")
        setup = sample("SetupRequest")
        setup["Carrier"] = [{"CarrierID": "C1", "Result": 0}]
        materials.replace_database(b"Part Number,Reel ID\nP1,R1\n", self.cfg, self.data, "initial.csv")
        status, response = self.post(setup)
        self.assertEqual(status, 200)
        self.assertEqual(response["Carrier"][0]["Result"], 1)
        self.assertEqual(response["Carrier"][0]["Message"], replies.PLACEHOLDER)
        status, response = self.post(sample("MaterialRequest"))
        self.assertEqual((status, response), (200, {"FuncName": "MaterialRequest", "Err": 1,
                                                    "ErrMsg": replies.PLACEHOLDER}))

    def test_required_machine_fields(self):
        bad = sample()
        del bad["MchInfo"]["MchSerial"]
        status, response = self.post(bad)
        self.assertEqual((status, response["message"]), (400, "Incorrect Request Body Data"))

    def test_port_in_use_is_reported_without_crashing_caller(self):
        blocker = socket.socket()
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        cfg = dict(self.cfg, ysup_port=blocker.getsockname()[1])
        issues = []
        server = YsupServer(cfg, self.data, lambda *args: issues.append(args))
        self.assertFalse(server.start())
        self.assertTrue(any("already in use" in str(item[1]) for item in issues))

    def test_clean_shutdown_and_history_retention(self):
        receiver = self.receiver
        receiver.stop()
        self.assertIsNone(receiver.server)
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
        recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for timestamp in (old, recent):
            records.record(self.data, received_at=timestamp, client_name="R", caller_address="127.0.0.1",
                           func_name="Test", machine_serial="", http_status=200, result_summary="ok",
                           elapsed_ms=1, request_json="{}", response_json="{}")
        self.assertEqual(records.clean_up(self.data, 2), 1)
        self.assertEqual(self.db_count(), 1)


if __name__ == "__main__":
    unittest.main()
