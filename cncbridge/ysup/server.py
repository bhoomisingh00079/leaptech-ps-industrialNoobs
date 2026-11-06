"""Small HTTP Basic/token authenticated YSUP-Link receiver."""
import base64
import binascii
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import materials, records, replies

log = logging.getLogger("cncbridge")
MAX_BODY = 8 * 1024 * 1024


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _loopback(address):
    return address in ("127.0.0.1", "::1")


def _same_secret(left, right):
    return hmac.compare_digest(str(left).encode("utf-8"), str(right).encode("utf-8"))


def _valid_basic(header, username, password):
    if not header or not header.lower().startswith("basic ") or not username or not password:
        return False
    try:
        raw = base64.b64decode(header.split(None, 1)[1], validate=True).decode("utf-8")
        user, sep, secret = raw.partition(":")
    except (ValueError, UnicodeError, binascii.Error, IndexError):
        return False
    return bool(sep and _same_secret(user, username) and _same_secret(secret, password))


def _authenticated(headers, cfg, address):
    username, password, token = (str(cfg.get(key) or "") for key in
                                 ("ysup_basic_username", "ysup_basic_password", "ysup_api_token"))
    if not username and not password and not token:
        return _loopback(address), ""
    authorization = headers.get("Authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    supplied = headers.get("X-YSUP-Token", "")
    token_ok = bool(token and ((bearer and _same_secret(bearer, token)) or
                               (supplied and _same_secret(supplied, token))))
    return _valid_basic(authorization, username, password) or token_ok, ""


def _required(request):
    if not isinstance(request, dict):
        return "request body must be a JSON object"
    if not isinstance(request.get("FuncName"), str) or not request["FuncName"].strip():
        return "FuncName is required"
    # The receiver uses the common envelope documented for every YSUP request. Exact per-message
    # schemas are kept intentionally small here because the confidential OpenAPI file is not shipped.
    for key in ("TraceDate", "MchInfo"):
        if key not in request:
            return f"{key} is required"
    if not isinstance(request.get("MchInfo"), dict):
        return "MchInfo must be an object"
    for key in ("MchName", "MchSerial", "MchModel", "LaneNo", "LineName"):
        if key not in request["MchInfo"]:
            return f"MchInfo.{key} is required"
    if request["FuncName"] in ("PcbEntryEvent", "PcbExitEvent"):
        if not isinstance(request.get("PcbInfo"), dict):
            return "PcbInfo is required"
        for key in ("PcbID", "PgmName"):
            if key not in request["PcbInfo"]:
                return f"PcbInfo.{key} is required"
    return ""


class YsupHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class YsupServer:
    """Lifecycle wrapper. Call start from Engine.run_forever and stop when that owner stops."""
    def __init__(self, cfg, data_dir, report=None):
        self.cfg = dict(cfg)
        self.data_dir = data_dir
        self.report = report or (lambda *_args, **_kwargs: None)
        self.server = None
        self.thread = None
        self._auth_lock = threading.Lock()
        self._auth_failures = []

    def start(self):
        if not self.cfg.get("ysup_enabled"):
            return False
        if bool(self.cfg.get("ysup_basic_username")) != bool(self.cfg.get("ysup_basic_password")):
            self.report("ysup_listener", "YSUP Basic Auth needs both a user name and a password.", logging.ERROR)
            return False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                log.info("YSUP receiver: " + fmt, *args)

            def _send(self, status, body):
                data = _json_bytes(body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
                return data

            def _exchange(self):
                started = time.monotonic()
                received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                client = str(self.headers.get("client-name", "")).strip()
                caller = self.client_address[0]
                request_doc, request_raw = {}, ""
                status, response = 200, {}
                func_name = machine_serial = ""
                try:
                    if self.path not in ("/", "/ysup/api"):
                        status, response = 404, {"message": "Not found", "data": None}
                    elif not client:
                        status, response = 400, {"message": "Missing required client-name header", "data": None}
                    elif "application/json" not in str(self.headers.get("Content-Type", "")).lower():
                        status, response = 400, {"message": "Content-Type must be application/json", "data": None}
                    else:
                        ok, _ = _authenticated(self.headers, outer.cfg, caller)
                        if not ok:
                            status = 401
                            creds = any(outer.cfg.get(k) for k in ("ysup_basic_username", "ysup_basic_password",
                                                                   "ysup_api_token"))
                            message = "Unauthenticated" if creds else \
                                "Configure YSUP Basic Auth credentials for remote access"
                            response = {"message": message, "data": None}
                            outer._auth_failure()
                        else:
                            outer._auth_success()
                            length = int(self.headers.get("Content-Length", "0"))
                            if length < 0 or length > MAX_BODY:
                                raise ValueError("request body is too large")
                            request_raw = self.rfile.read(length).decode("utf-8")
                            try:
                                request_doc = json.loads(request_raw)
                            except (json.JSONDecodeError, UnicodeError) as error:
                                status, response = 400, {"message": "Incorrect Request Body Data", "data": str(error)}
                            else:
                                if isinstance(request_doc, dict):
                                    func_name = str(request_doc.get("FuncName") or "")
                                    mch = request_doc.get("MchInfo")
                                    if isinstance(mch, dict):
                                        machine_serial = str(mch.get("MchSerial") or "")
                                if func_name in ("SetupRequest", "MaterialRequest"):
                                    try:
                                        loaded = materials.load(outer.cfg, outer.data_dir)
                                    except (OSError, ValueError) as error:
                                        status, response = 500, {"message": "Material database is unusable",
                                                                 "data": str(error)}
                                    else:
                                        if loaded is None:
                                            status, response = 503, {"message": "Material database has not been imported",
                                                                     "data": None}
                                if status == 200:
                                    missing = _required(request_doc)
                                    if missing:
                                        status, response = 400, {"message": "Incorrect Request Body Data",
                                                                 "data": missing}
                                    elif func_name not in replies.KNOWN:
                                        status, response = 404, {"message": "FuncName not available in Request Body",
                                                                 "data": None}
                                    else:
                                        response = replies.build(request_doc)
                except (ValueError, OSError) as error:
                    status, response = 400, {"message": "Incorrect Request Body Data", "data": str(error)}
                except Exception:
                    log.exception("Unexpected YSUP-Link request failure")
                    status, response = 500, {"message": "YSUP receiver error", "data": None}
                elapsed = int((time.monotonic() - started) * 1000)
                response_raw = _json_bytes(response).decode("utf-8")
                try:
                    records.record(outer.data_dir, received_at=received_at, client_name=client,
                                   caller_address=caller, func_name=func_name, machine_serial=machine_serial,
                                   http_status=status, result_summary=str(response.get("message", "OK")),
                                   elapsed_ms=elapsed, request_json=request_raw or json.dumps(request_doc),
                                   response_json=response_raw)
                except Exception:
                    log.exception("Could not record a YSUP-Link exchange")
                self._send(status, response)
                if elapsed > 1000:
                    outer.report("ysup_slow", f"A YSUP-Link request took {elapsed} ms (over 1 second).",
                                 logging.WARNING)
                else:
                    outer.report("ysup_slow", None)

            def do_POST(self):
                self._exchange()

            def do_GET(self):
                self._send(405, {"message": "Method not allowed", "data": None})

        try:
            self.server = YsupHTTPServer((str(self.cfg.get("ysup_listen_address") or "127.0.0.1"),
                                          int(self.cfg.get("ysup_port", 3001))), Handler)
        except OSError as error:
            if getattr(error, "errno", None) in (48, 98, 10048):
                self.report("ysup_listener", f"YSUP receiver port {self.cfg.get('ysup_port', 3001)} is already in use.",
                            logging.ERROR)
            else:
                self.report("ysup_listener", f"Could not start the YSUP receiver: {error}", logging.ERROR)
            self.server = None
            return False
        try:
            records.initialize(self.data_dir)
        except Exception as error:
            self.server.server_close()
            self.server = None
            self.report("ysup_listener", f"Could not initialize the YSUP exchange log: {error}", logging.ERROR)
            return False
        self._stopping = False
        self.thread = threading.Thread(target=self._serve, name="ysup-receiver", daemon=True)
        self.thread.start()
        self.report("ysup_listener", None)
        log.info("YSUP receiver listening on %s:%s", *self.server.server_address[:2])
        return True

    def _serve(self):
        try:
            self.server.serve_forever(poll_interval=0.2)
            if not getattr(self, "_stopping", False):
                self.report("ysup_listener", "YSUP receiver stopped unexpectedly.", logging.ERROR)
        except Exception as error:
            log.exception("YSUP receiver stopped unexpectedly")
            self.report("ysup_listener", f"YSUP receiver stopped unexpectedly: {error}", logging.ERROR)

    def _auth_failure(self):
        now = time.monotonic()
        with self._auth_lock:
            self._auth_failures = [item for item in self._auth_failures if now - item < 60]
            self._auth_failures.append(now)
            repeated = len(self._auth_failures) >= 5
        if repeated:
            self.report("ysup_auth", "Repeated YSUP-Link authentication failures (5 or more within 60 seconds).",
                        logging.WARNING)

    def _auth_success(self):
        with self._auth_lock:
            had_repeated_failures = len(self._auth_failures) >= 5
            self._auth_failures.clear()
        if had_repeated_failures:
            self.report("ysup_auth", None)

    def stop(self):
        if self.server is None:
            return
        server, thread = self.server, self.thread
        self.server = self.thread = None
        self._stopping = True
        server.shutdown()
        server.server_close()
        if thread:
            thread.join(timeout=3)
        self.report("ysup_listener", None)
        self.report("ysup_auth", None)
        self.report("ysup_slow", None)
        log.info("YSUP receiver stopped")
