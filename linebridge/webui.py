"""
LineBridge control panel - a local web UI served with the Python standard library.

    python -m linebridge ui --config config.json      -> http://127.0.0.1:8600
"""
import json
import logging
import os
import string
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import core

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("linebridge")


class LogBuffer(logging.Handler):
    def __init__(self, size=600):
        super().__init__()
        self.lines, self.seq, self._buf_lock = deque(maxlen=size), 0, threading.Lock()

    def emit(self, record):
        with self._buf_lock:
            self.seq += 1
            self.lines.append({"id": self.seq, "t": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                               "level": record.levelname, "msg": record.getMessage()})

    def since(self, n):
        with self._buf_lock:
            return [l for l in self.lines if l["id"] > n]


class Controller:
    def __init__(self, config_path):
        self.config_path = config_path
        self.cfg = core.load_config(config_path)
        self.engine = None
        self.thread = None
        self.busy = threading.Lock()
        self.logs = LogBuffer()
        log.addHandler(self.logs)
        log.setLevel(logging.INFO)
        self._ledgers = {}

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and self.engine and not self.engine._stop.is_set())

    def ledger(self):
        p = self.cfg["ledger_path"]
        if p not in self._ledgers:
            self._ledgers[p] = core.Ledger(p)
        return self._ledgers[p]

    def save(self, cfg):
        self.cfg = core.normalize_config(cfg)
        core.save_config(self.cfg, self.config_path)
        log.info("Settings saved")
        if self.running:
            self.stop()
            self.start()

    def start(self):
        if self.running:
            return
        self.engine = core.Engine(self.cfg)
        self.thread = threading.Thread(target=self.engine.run_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.engine:
            self.engine.stop()
        if self.thread:
            self.thread.join(timeout=5)

    def run_once(self):
        if self.running or not self.busy.acquire(blocking=False):
            return False

        def job():
            try:
                n = core.Engine(self.cfg).scan_once()
                log.info("Run once finished: %d file(s) processed", n)
            finally:
                self.busy.release()
        threading.Thread(target=job, daemon=True).start()
        return True

    def status(self, since=0):
        eng = self.engine or core.Engine(self.cfg)
        try:
            pending = len(eng.pending_files()) if Path(self.cfg["input_folder"]).exists() else 0
        except Exception:
            pending = 0
        return {"running": self.running, "station": self.cfg.get("station_name", ""),
                "stats": self.ledger().stats(), "queued": eng.queued(), "pending": pending,
                "history": self.ledger().recent(60), "logs": self.logs.since(since), "version": core.__version__}


def preview(body):
    cfg = core.normalize_config(body.get("config") or {})
    rows = core.read_rows(body.get("csv", ""))
    text, chosen, warnings, tables = core.build_output(body.get("name") or "preview.csv", "(preview)", rows, cfg)
    summary = [{"name": n, "range": t["range"], "columns": t["columns"], "row_count": len(t["rows"]),
                "sample": t["rows"][:4]} for n, t in tables.items()]
    return {"tables": summary, "chosen": list(chosen), "warnings": warnings,
            "output": text[:60000], "truncated": len(text) > 60000, "format": cfg["output_format"]}


def test_api(body):
    cfg = core.normalize_config(body.get("config") or {})
    if not cfg["api_url"]:
        return {"ok": False, "message": "Enter an API link first"}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "linebridge_connection_test.json"
        p.write_text(json.dumps({"test": True, "station": cfg["station_name"],
                                 "sent_at": datetime.now().isoformat(timespec="seconds")}))
        try:
            code = core.send_api(p, cfg)
            return {"ok": True, "message": f"Connected - endpoint answered HTTP {code}"}
        except Exception as e:
            return {"ok": False, "message": f"Failed: {e}"}


def test_shared(body):
    cfg = core.normalize_config(body.get("config") or {})
    folder = Path(cfg["shared_folder"] or ".")
    if not cfg["shared_folder"]:
        return {"ok": False, "message": "Enter a shared folder first"}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".linebridge_probe_{os.getpid()}"
        probe.write_text("ok")
        probe.unlink()
        return {"ok": True, "message": f"Writable: {folder.resolve()}"}
    except Exception as e:
        return {"ok": False, "message": f"Not writable: {e}"}


def integration_check(body):
    """Run a safe, operator-facing preflight for the complete integration path."""
    cfg = core.normalize_config(body.get("config") or {})
    checks = []

    def add(key, label, ok, detail, kind="check"):
        checks.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "kind": kind})

    input_folder = Path(cfg["input_folder"]).expanduser()
    output_folder = Path(cfg["output_folder"]).expanduser()
    backup_folder = Path(cfg["backup_folder"]).expanduser()

    add("input", "Machine CSV folder", input_folder.exists() and input_folder.is_dir(),
        str(input_folder) if input_folder.exists() else "Folder does not exist yet")

    sample = None
    if input_folder.exists():
        candidates = sorted(input_folder.glob(cfg.get("file_pattern", "*.csv")),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            sample = candidates[0]
    if sample is None:
        bundled = Path(__file__).resolve().parent.parent / "samples" / "pnp_L1M8_board_00001.csv"
        if bundled.exists():
            sample = bundled
    try:
        rows = core.read_rows(sample) if sample else []
        tables = core.split_tables(rows)
        _, chosen, warnings, _ = core.build_output(sample.name if sample else "sample.csv", "(check)", rows, cfg)
        add("parser", "CSV parser + selections", bool(tables) and (not cfg["selections"] or bool(chosen)),
            f"{len(tables)} table(s) found from {sample.name if sample else 'no sample'}"
            + (f"; {len(warnings)} warning(s)" if warnings else ""),
            "warning" if warnings else "check")
    except Exception as e:
        add("parser", "CSV parser + selections", False, str(e), "error")

    for key, label, folder in (("output", "Output folder", output_folder),
                               ("backup", "Back-up folder", backup_folder)):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".linebridge_check_{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(key, label, True, str(folder.resolve()))
        except Exception as e:
            add(key, label, False, str(e), "error")

    if cfg.get("shared_enabled"):
        shared = test_shared({"config": cfg})
        add("shared", "Shared-folder delivery", shared["ok"], shared["message"],
            "check" if shared["ok"] else "error")
    else:
        add("shared", "Shared-folder delivery", True, "Disabled in this profile", "skipped")

    if cfg.get("api_enabled"):
        if not cfg.get("api_url"):
            add("api", "Customer API delivery", False, "API is enabled but no URL is configured", "error")
        elif body.get("check_api"):
            result = test_api({"config": cfg})
            add("api", "Customer API delivery", result["ok"], result["message"],
                "check" if result["ok"] else "error")
        else:
            add("api", "Customer API delivery", True, "Configured; click Check again to send a test payload", "skipped")
    else:
        add("api", "Customer API delivery", True, "Disabled in this profile", "skipped")

    add("archive", "Archive path ready", backup_folder.exists(), str(backup_folder.resolve()))
    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks,
            "message": "Integration path is ready" if not failed else f"{len(failed)} check(s) need attention"}


def list_dirs(path):
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": "", "dirs": drives}
        path = str(Path.cwd())
    p = Path(path).expanduser()
    try:
        dirs = sorted([d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")], key=str.lower)
    except Exception as e:
        return {"path": str(p), "parent": str(p.parent), "dirs": [], "error": str(e)}
    return {"path": str(p.resolve()), "parent": str(p.resolve().parent), "dirs": dirs[:500]}


def make_handler(ctl):
    class H(BaseHTTPRequestHandler):
        server_version = f"LineBridge/{core.__version__}"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/config":
                return self._send(200, ctl.cfg)
            if u.path == "/api/status":
                return self._send(200, ctl.status(int(q.get("since", ["0"])[0])))
            if u.path == "/api/dirs":
                return self._send(200, list_dirs(q.get("path", [""])[0]))
            if u.path == "/api/sample":
                sample = Path(__file__).resolve().parent.parent / "samples" / "pnp_L1M8_board_00001.csv"
                if not sample.exists():
                    return self._send(404, {"error": "Bundled sample is missing"})
                return self._send(200, {"name": sample.name, "csv": sample.read_text(encoding="utf-8")})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                b = self._body()
                p = urlparse(self.path).path
                if p == "/api/config":
                    ctl.save(b)
                    return self._send(200, {"ok": True, "config": ctl.cfg})
                if p == "/api/start":
                    ctl.start()
                    return self._send(200, {"ok": True})
                if p == "/api/stop":
                    ctl.stop()
                    return self._send(200, {"ok": True})
                if p == "/api/run-once":
                    return self._send(200, {"ok": ctl.run_once()})
                if p == "/api/preview":
                    return self._send(200, preview(b))
                if p == "/api/test-api":
                    return self._send(200, test_api(b))
                if p == "/api/test-shared":
                    return self._send(200, test_shared(b))
                if p == "/api/integration-check":
                    return self._send(200, integration_check(b))
                self._send(404, {"error": "not found"})
            except Exception as e:
                log.error("UI request failed: %s", e)
                self._send(400, {"error": str(e)})
    return H


def serve(config_path, host="127.0.0.1", port=8600, autostart=False):
    ctl = Controller(config_path)
    srv = ThreadingHTTPServer((host, port), make_handler(ctl))
    if autostart:
        ctl.start()
    log.info("Control panel: http://%s:%d", "localhost" if host in ("127.0.0.1", "0.0.0.0") else host, port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()
        srv.server_close()
