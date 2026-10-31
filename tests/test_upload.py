"""Tests for manual file upload, processing pipeline integration, format choices, and output download."""
import io
import json
import base64
import hashlib
import http.cookiejar
import os
import tempfile
import unittest
import urllib.request
import urllib.error
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

from linebridge import core, webui, ysup

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "samples" / "pnp_L1M8_board_00001.csv"
EXPERIMENT_CSV = ROOT / "real_machine_logs" / "source" / "experiment_13.csv"


class UploadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.out_dir = self.ws / "output"
        self.bak_dir = self.ws / "backup"
        self.in_dir = self.ws / "input"
        self.out_dir.mkdir()
        self.bak_dir.mkdir()
        self.in_dir.mkdir()
        self.ledger_path = str(self.ws / "ledger.db")
        self.cfg = core.normalize_config({
            "station_name": "TestStation-01",
            "input_folder": str(self.in_dir),
            "output_folder": str(self.out_dir),
            "backup_folder": str(self.bak_dir),
            "ledger_path": self.ledger_path,
            "output_format": "json",
            "selections": [""],
            "manual_inputs": [{"label": "Operator", "value": "OP-99"}],
        })
        self.ctl = webui.Controller(str(self.ws / "config.json"))
        salt = b"linebridge-test-salt"
        self.ctl.cfg = self.cfg
        self.ctl.cfg["admin_password_salt"] = base64.b64encode(salt).decode("ascii")
        self.ctl.cfg["admin_password_hash"] = hashlib.pbkdf2_hmac("sha256", b"test-admin-password", salt, 310000).hex()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(self.ctl))
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        import threading
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login = urllib.request.Request(f"{self.url}/api/login", data=json.dumps({
            "username": "Admin", "password": "test-admin-password"
        }).encode(), headers={"Content-Type": "application/json"})
        self.opener.open(login).read()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post_json(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.url}{path}", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with self.opener.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_multipart(self, path, filename, content_bytes, overrides=None):
        boundary = "----WebKitFormBoundaryUploadTest7MA4YWxkTrZu0gW"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
        q = f"?format={overrides['format']}" if overrides and "format" in overrides else ""
        req = urllib.request.Request(
            f"{self.url}{path}{q}",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
        )
        with self.opener.open(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        req = urllib.request.Request(f"{self.url}{path}")
        with self.opener.open(req) as resp:
            return resp.status, resp.headers, resp.read()

    def test_upload_csv_to_json_and_download(self):
        csv_content = SAMPLE_CSV.read_text(encoding="utf-8")
        res = self._post_json("/api/upload", {
            "name": "board_test.csv",
            "csv": csv_content,
            "format": "json"
        })
        self.assertTrue(res["ok"])
        out_name = res["output"]
        self.assertTrue(out_name.endswith(".json"))
        self.assertIn("L1xM8", res["tables"])

        # Output file must exist in output_folder
        out_path = self.out_dir / out_name
        self.assertTrue(out_path.exists())
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(doc["station"], "TestStation-01")
        self.assertEqual(doc["manual_inputs"]["Operator"], "OP-99")
        self.assertIn("L1xM8", doc["tables"])

        # Audit ledger must record it
        records = core.Ledger(self.ledger_path).recent(10)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "board_test.csv")
        self.assertEqual(records[0]["status"], "ok")
        self.assertEqual(records[0]["output"], out_name)

        # Download file via GET /api/download
        status, headers, content = self._get(res["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertIn(f'filename="{out_name}"', headers.get("Content-Disposition"))
        self.assertEqual(json.loads(content.decode("utf-8")), doc)

    def test_upload_csv_to_csv_and_txt_formats(self):
        csv_content = SAMPLE_CSV.read_text(encoding="utf-8")
        # Test CSV output
        res_csv = self._post_json("/api/upload", {
            "name": "sample.csv",
            "csv": csv_content,
            "format": "csv"
        })
        self.assertTrue(res_csv["output"].endswith(".csv"))
        status, headers, body_csv = self._get(res_csv["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/csv")
        self.assertIn("#station,TestStation-01", body_csv.decode("utf-8"))

        # Test TXT output
        res_txt = self._post_json("/api/upload", {
            "name": "sample.csv",
            "csv": csv_content,
            "format": "txt"
        })
        self.assertTrue(res_txt["output"].endswith(".txt"))
        status, headers, body_txt = self._get(res_txt["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/plain")
        self.assertIn("STATION       : TestStation-01", body_txt.decode("utf-8"))

    def test_upload_pdf_download_and_shared_delivery(self):
        self.cfg.update(shared_enabled=True, shared_folder=str(self.ws / "shared"))
        content = "Ref,Result\nR1,OK\nR2,FAIL\n"
        res = self._post_json("/api/upload", {"name": "board.pdf.csv", "csv": content, "format": "pdf"})
        self.assertTrue(res["output"].endswith(".pdf"))
        saved = self.out_dir / res["output"]
        self.assertTrue(saved.read_bytes().startswith(b"%PDF-1.4"))
        self.assertEqual((self.ws / "shared" / saved.name).read_bytes(), saved.read_bytes())
        status, headers, body = self._get(res["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/pdf")
        self.assertEqual(body, saved.read_bytes())
        self.assertEqual(res["format"], "pdf")

    def test_pdf_upload_empty_input_and_render_failure(self):
        with self.assertRaises(urllib.error.HTTPError) as empty:
            self._post_json("/api/upload", {"name": "empty.csv", "csv": "", "format": "pdf"})
        self.assertEqual(empty.exception.code, 400)
        with mock.patch.object(core, "render_pdf", side_effect=RuntimeError("renderer failed")):
            with self.assertRaises(urllib.error.HTTPError) as failed:
                self._post_json("/api/upload", {
                    "name": "board.csv", "csv": "Ref,Result\nR1,OK\n", "format": "pdf"
                })
        self.assertEqual(failed.exception.code, 400)
        self.assertIn("renderer failed", failed.exception.read().decode("utf-8"))

    def test_multipart_upload(self):
        content = "ColA,ColB\n10,20\n30,40\n".encode("utf-8")
        res = self._post_multipart("/api/upload", "part_log.csv", content, overrides={"format": "csv"})
        self.assertTrue(res["ok"])
        self.assertTrue((self.out_dir / res["output"]).exists())
        self.assertEqual(res["format"], "csv")

    def test_upload_real_experiment_csv(self):
        exp_text = EXPERIMENT_CSV.read_text(encoding="utf-8")
        res = self._post_json("/api/upload", {
            "name": "experiment_13.csv",
            "csv": exp_text,
            "format": "json"
        })
        self.assertTrue(res["ok"])
        out_doc = json.loads((self.out_dir / res["output"]).read_text(encoding="utf-8"))
        self.assertEqual(out_doc["tables"]["ALL"]["row_count"], 2233)
        self.assertIn("Machining_Process", out_doc["tables"]["ALL"]["columns"])

    def test_upload_real_staged_secom_csv(self):
        from tools.prepare_secom import prepare
        secom_dir = self.ws / "secom_tmp"
        secom_dir.mkdir()
        src = ROOT / "real_machine_logs" / "source"
        (secom_dir / "data").write_text("\n".join((src / "secom.data").read_text().splitlines()[:5]))
        (secom_dir / "labels").write_text("\n".join((src / "secom_labels.data").read_text().splitlines()[:5]))
        prepare(secom_dir / "data", secom_dir / "labels", secom_dir / "incoming", 5)
        staged_file = list((secom_dir / "incoming").glob("*.csv"))[0]
        res = self._post_json("/api/upload", {
            "name": staged_file.name,
            "csv": staged_file.read_text(encoding="utf-8"),
            "format": "json"
        })
        self.assertTrue(res["ok"])
        out_doc = json.loads((self.out_dir / res["output"]).read_text(encoding="utf-8"))
        self.assertEqual(out_doc["tables"]["ALL"]["row_count"], 5)
        self.assertEqual(len(out_doc["tables"]["ALL"]["columns"]), 594)
        self.assertEqual(out_doc["tables"]["ALL"]["rows"][0]["yield_result"], "pass")

    def test_upload_structured_json(self):
        # List of records JSON
        record_data = json.dumps([
            {"Ref": "R100", "Part": "RES-10K", "Status": "PASS"},
            {"Ref": "C100", "Part": "CAP-100NF", "Status": "PASS"}
        ])
        res = self._post_json("/api/upload", {
            "name": "feeders.json",
            "csv": record_data,
            "format": "json"
        })
        self.assertTrue(res["ok"])
        out_doc = json.loads((self.out_dir / res["output"]).read_text(encoding="utf-8"))
        self.assertEqual(out_doc["tables"]["ALL"]["row_count"], 2)
        self.assertEqual(out_doc["tables"]["ALL"]["columns"], ["Ref", "Part", "Status"])

    def test_upload_unsupported_json_rejected(self):
        # Non-tabular nested JSON
        nested = json.dumps({"machine": {"nested_id": 123}, "settings": True})
        req = urllib.request.Request(
            f"{self.url}/api/upload",
            data=json.dumps({"name": "bad.json", "csv": nested}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 400)
        err_body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("Unsupported JSON structure", err_body["error"])

    def test_upload_unsupported_extension_rejected(self):
        req = urllib.request.Request(
            f"{self.url}/api/upload",
            data=json.dumps({"name": "firmware.bin", "csv": "some binary data"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.opener.open(req)
        self.assertEqual(ctx.exception.code, 400)
        err_body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertIn("Unsupported file format", err_body["error"])

    def test_download_security_checks(self):
        # Path traversal rejected
        for bad_name in ("../secret.txt", "/etc/passwd", "..\\boot.ini", ".hidden"):
            req = urllib.request.Request(f"{self.url}/api/download?name={bad_name}")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.opener.open(req)
            self.assertEqual(ctx.exception.code, 400)

        # Non-existent file in output folder returns 404
        req404 = urllib.request.Request(f"{self.url}/api/download?name=nonexistent.json")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.opener.open(req404)
        self.assertEqual(ctx.exception.code, 404)


    def test_ysup_setup_verification_and_admin_auth(self):
        self.cfg.update(ysup_api_token="", ysup_basic_username="ysup-test",
                        ysup_basic_password="ysup-password", ysup_database=str(self.ws / "materials.csv"),
                        ysup_fields={"part_number": True, "reel_id": True, "lot_id": False})
        self.ctl.cfg = self.cfg
        csv_text = "Part Number,Reel ID,Lot ID\nPN-100,REEL-9,LOT-7\n"
        imported = self._post_json("/api/ysup/database", {"csv": csv_text})
        self.assertEqual(imported["records"], 1)
        payload = {
            "FuncName": "SetupRequest", "TraceDate": "20260601123000123",
            "MchInfo": {"MchName": "MOUNTER", "MchSerial": "Y30001", "MchModel": "YSM", "LaneNo": 1, "LineName": "L1"},
            "SetInfos": [{"Operation": 5, "LocationID": "FEEDER-1", "CarrierInfos": [
                {"Name": "8mmTape", "SetNo": 1, "Type": 0, "UniqueID": "CARRIER-1",
                 "CmpInfos": [{"CmpID": "PN-100", "Qty": 5000, "UniqueID": "REEL-9"}]}
            ]}]
        }
        req = urllib.request.Request(f"{self.url}/ysup/api", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "client-name": "Relay1", "Authorization": "Basic " +
                                              base64.b64encode(b"ysup-test:ysup-password").decode()})
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
        self.assertEqual(result["FuncName"], "SetupRequest")
        self.assertEqual(result["SetInfos"][0]["CarrierInfos"][0]["Result"], 0)
        bad = json.loads(json.dumps(payload))
        bad["SetInfos"][0]["CarrierInfos"][0]["CmpInfos"][0]["CmpID"] = "WRONG"
        req = urllib.request.Request(f"{self.url}/ysup/api", data=json.dumps(bad).encode(),
                                     headers={"Content-Type": "application/json", "client-name": "Relay1", "Authorization": "Basic " +
                                              base64.b64encode(b"ysup-test:ysup-password").decode()})
        with urllib.request.urlopen(req) as response:
            rejected = json.loads(response.read())
        self.assertEqual(rejected["SetInfos"][0]["CarrierInfos"][0]["Result"], 1)
        material = {
            "FuncName": "MaterialRequest", "TraceDate": "20260601123000124",
            "MchInfo": payload["MchInfo"],
            "Component": {"ReelID": "REEL-9", "OrderID": "0", "Assy": "", "LotID": "LOT-7",
                          "SurfaceInfo": "", "BoardName": ""}
        }
        req = urllib.request.Request(f"{self.url}/ysup/api", data=json.dumps(material).encode(),
                                     headers={"Content-Type": "application/json", "client-name": "Relay1",
                                              "Authorization": "Basic " +
                                              base64.b64encode(b"ysup-test:ysup-password").decode()})
        with urllib.request.urlopen(req) as response:
            material_result = json.loads(response.read())
        self.assertEqual(material_result["Err"], 0)
        self.assertEqual(material_result["Component"]["CmpID"], "PN-100")
        materials, _ = ysup.parse_database_csv(csv_text)
        callback = ysup.remote_scan_component_payload(
            "Y30001", True, 1, {"CmpID": "PN-100", "UniqueID": "REEL-9", "Qty": 5000}, materials
        )
        self.assertEqual(callback["CommandInfo"]["TargetOperation"], "REMOTE_SCAN")
        params = callback["CommandInfo"]["Params"]["Params"]
        self.assertEqual(params[:7], ["0", "PN-100$REEL-9$5000", "1", "PN-100", "REEL-9", "LOT-7", "5000"])
        self.assertEqual(len(params), 12)
        unauth = urllib.request.Request(f"{self.url}/api/config")
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(unauth)
        self.assertEqual(denied.exception.code, 401)

    def test_ysup_remote_scan_uses_vendor_api_contract(self):
        self.cfg.update(ysup_database=str(self.ws / "materials.csv"), ysup_basic_username="in",
                        ysup_basic_password="in-pass", ysup_outbound_enabled=True,
                        ysup_outbound_url="http://ysup.test/api", ysup_client_name="LEAPTECH-MES",
                        ysup_outbound_username="out", ysup_outbound_password="out-pass")
        self.ctl.cfg = self.cfg
        self._post_json("/api/ysup/database", {
            "csv": "Part Number,Reel ID,Lot ID,Quantity\nPN-1,R-1,L-1,2000\n"
        })
        payload = {
            "FuncName": "SetupRequest", "TraceDate": "20260601123000125",
            "MchInfo": {"MchName": "MOUNTER", "MchSerial": "Y30001", "MchModel": "YSM", "LaneNo": 1, "LineName": "L1"},
            "SetInfos": [{"Operation": 5, "LocationID": "FEEDER-1", "CarrierInfos": [
                {"Name": "8mmTape", "SetNo": 3, "Type": 0, "UniqueID": "CARRIER-1",
                 "CmpInfos": [{"CmpID": "PN-1", "Qty": 2000, "UniqueID": "R-1"}]}
            ]}]
        }
        class UpstreamResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"Result":1,"ResultMessage":"1001: Request Validation Success"}'
        captured = []
        def accept(request, timeout=None):
            captured.append(request)
            return UpstreamResponse()
        req = urllib.request.Request(
            f"{self.url}/ysup/api", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "client-name": "Relay1",
                     "Authorization": "Basic " + base64.b64encode(b"in:in-pass").decode()}
        )
        with mock.patch("urllib.request.urlopen", side_effect=accept):
            with self.opener.open(req) as response:
                returned = json.loads(response.read())
        self.assertEqual(returned["SetInfos"][0]["CarrierInfos"][0]["Result"], 0)
        self.assertEqual(len(captured), 1)
        request = captured[0]
        sent_headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(sent_headers["client-name"], "LEAPTECH-MES")
        self.assertEqual(request.get_header("Authorization"), "Basic " + base64.b64encode(b"out:out-pass").decode())
        outbound = json.loads(request.data)
        self.assertEqual(outbound["CommandInfo"]["TargetOperation"], "REMOTE_SCAN")
        self.assertEqual(outbound["CommandInfo"]["TargetMachine"], "Y30001")
        self.assertEqual(outbound["CommandInfo"]["Params"]["Params"][:7],
                         ["0", "PN-1$R-1$2000", "3", "PN-1", "R-1", "L-1", "2000"])


if __name__ == "__main__":
    unittest.main()
