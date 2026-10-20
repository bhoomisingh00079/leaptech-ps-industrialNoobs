"""
LineBridge control panel - a local web UI served with the Python standard library.

    python -m linebridge ui --config config.json      -> http://127.0.0.1:8600
"""
import hashlib
import json
import logging
import os
import re
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


def json_to_rows(text):
    """Convert structured JSON into table rows suitable for LineBridge split_tables."""
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError(f"Invalid JSON: {e}")
    if isinstance(data, list):
        if not data:
            raise ValueError("JSON array is empty")
        if not all(isinstance(r, dict) for r in data):
            raise ValueError("JSON array must contain objects with key-value pairs")
        cols = []
        for r in data:
            for k in r.keys():
                if k not in cols:
                    cols.append(k)
        rows = [cols]
        for r in data:
            rows.append([str(r.get(c, "")) for c in cols])
        return rows
    elif isinstance(data, dict):
        if "tables" in data and isinstance(data["tables"], dict):
            # LineBridge output JSON format
            rows = []
            for tname, tval in data["tables"].items():
                rows.append([f"[{tname}]"])
                cols = tval.get("columns", [])
                if cols:
                    rows.append(cols)
                    for r in tval.get("rows", []):
                        if isinstance(r, dict):
                            rows.append([str(r.get(c, "")) for c in cols])
                        elif isinstance(r, (list, tuple)):
                            rows.append([str(x) for x in r])
                rows.append([])
            return rows
        # Dict of record lists, e.g. {"Placements": [{"Ref": "R1"}]}
        table_keys = [k for k, v in data.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if table_keys:
            rows = []
            for tname in table_keys:
                items = data[tname]
                cols = []
                for r in items:
                    for k in r.keys():
                        if k not in cols:
                            cols.append(k)
                rows.append([f"[{tname}]"])
                rows.append(cols)
                for r in items:
                    rows.append([str(r.get(c, "")) for c in cols])
                rows.append([])
            return rows
    raise ValueError(
        "Unsupported JSON structure: LineBridge requires a list of records or a dictionary of table arrays. "
        "Arbitrary nested JSON is not supported."
    )


def parse_input_to_rows(filename, content):
    """Parse raw uploaded content (CSV, plain-text log, or structured JSON) into rows."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    ext = Path(filename).suffix.lower() if filename else ""
    trimmed = content.strip() if isinstance(content, str) else ""
    if ext == ".json" or (trimmed.startswith(("{", "[")) and ext in ("", ".json", ".txt", ".log")):
        return json_to_rows(content)
    if ext and ext not in (".csv", ".txt", ".log", ".tsv", ".data"):
        raise ValueError(f"Unsupported file format '{ext}'. Accepted: .csv, .txt, .log, .json")
    return core.read_rows(content)


def preview(body):
    cfg = core.normalize_config(body.get("config") or {})
    name = body.get("name") or "preview.csv"
    rows = parse_input_to_rows(name, body.get("csv", ""))
    text, chosen, warnings, tables = core.build_output(name, "(preview)", rows, cfg)
    summary = [{"name": n, "range": t["range"], "columns": t["columns"], "row_count": len(t["rows"]),
                "sample": t["rows"][:4]} for n, t in tables.items()]
    is_pdf = cfg["output_format"] == "pdf"
    return {"tables": summary, "chosen": list(chosen), "warnings": warnings,
            "output": "PDF document ready to generate." if is_pdf and chosen else ("" if is_pdf else text[:60000]),
            "truncated": False if is_pdf else len(text) > 60000, "format": cfg["output_format"]}


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


def list_outputs(cfg):
    """List only finished output documents in the configured output folder."""
    folder = Path(cfg["output_folder"])
    if not folder.is_dir():
        return []
    root = folder.resolve()
    files = (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".json", ".csv", ".txt", ".pdf")
             and not p.name.startswith(".") and p.resolve().parent == root)
    return [{"name": p.name, "format": p.suffix[1:].lower(), "size": p.stat().st_size,
             "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


def read_output(cfg, name):
    """Read one finished document; never accept a path outside the output folder."""
    if not name or name.startswith(".") or "/" in name or "\\" in name or Path(name).suffix.lower() not in (".json", ".csv", ".txt", ".pdf"):
        raise ValueError("Invalid output filename")
    folder = Path(cfg["output_folder"])
    target = folder / name
    if not target.is_file() or target.resolve().parent != folder.resolve():
        raise FileNotFoundError("Output file not found")
    if target.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Output file is too large to preview (10 MB limit)")
    fmt = target.suffix[1:].lower()
    return {"name": name, "format": fmt, "size": target.stat().st_size,
            "content": "" if fmt == "pdf" else target.read_text(encoding="utf-8")}


def _parse_multipart(raw_bytes, content_type_header):
    """Minimal stdlib-only multipart/form-data parser.
    Returns dict of {field_name: (value_bytes, filename_or_None)}.
    """
    m = re.search(r'boundary=([^\s;]+)', content_type_header or "")
    if not m:
        raise ValueError("No boundary in Content-Type")
    boundary = m.group(1).strip('"').encode()
    parts = {}
    sep = b"--" + boundary
    for seg in raw_bytes.split(sep)[1:]:
        if seg.strip() in (b"", b"--", b"--\r\n"):
            continue
        if seg.startswith(b"--"):
            break
        if b"\r\n\r\n" in seg:
            header_part, body = seg.split(b"\r\n\r\n", 1)
        elif b"\n\n" in seg:
            header_part, body = seg.split(b"\n\n", 1)
        else:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        cd = next((l for l in header_part.decode("utf-8", errors="replace").splitlines()
                   if l.lower().startswith("content-disposition:")), "")
        name_m = re.search(r'name="([^"]*)"', cd)
        file_m = re.search(r'filename="([^"]*)"', cd)
        if name_m:
            parts[name_m.group(1)] = (body, file_m.group(1) if file_m else None)
    return parts


def handle_upload(name, content, cfg, overrides=None):
    """Process an uploaded raw file through the core LineBridge pipeline."""
    effective_cfg = dict(cfg)
    if overrides:
        if overrides.get("output_format") in core.FORMATS:
            effective_cfg["output_format"] = overrides["output_format"]
        if overrides.get("csv_layout") in ("sections", "flat"):
            effective_cfg["csv_layout"] = overrides["csv_layout"]

    rows = parse_input_to_rows(name, content)
    now = datetime.now()
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    sha = hashlib.sha256(raw_bytes).hexdigest()
    text, chosen, warnings, tables = core.build_output(name, sha, rows, effective_cfg, now)
    if not chosen:
        avail = ", ".join(tables) or "none"
        raise ValueError(
            f"No selected table found in '{name}'. Tables detected: {avail}. "
            "Leave all Data selections empty to include every table, or add a matching selector."
        )

    fmt = effective_cfg["output_format"]
    stem = Path(name).stem or "upload"
    out_dir = Path(effective_cfg["output_folder"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = core._unique(out_dir, f"{stem}_{now:%Y%m%d_%H%M%S}.{fmt}")
    core.atomic_write(out_path, text)
    log.info("Upload processed: %s -> %s (%s)", name, out_path.name, ", ".join(chosen))

    delivery = {}
    try:
        delivery = core.Engine(effective_cfg).deliver(out_path)
    except Exception as de:
        log.warning("Upload delivery error (output still saved): %s", de)
        delivery = {"shared": "error", "api": "error"}

    core.Ledger(effective_cfg["ledger_path"]).add(
        processed_at=now.isoformat(timespec="seconds"),
        source=name,
        sha256=sha,
        size=len(raw_bytes),
        status="ok",
        tables=", ".join(chosen),
        output=out_path.name,
        shared=delivery.get("shared", "off"),
        api=delivery.get("api", "off"),
        message="manual upload" + ("; " + "; ".join(warnings) if warnings else ""),
    )

    return {
        "ok": True,
        "output": out_path.name,
        "format": fmt,
        "tables": list(chosen),
        "warnings": warnings,
        "download_url": f"/api/download?name={out_path.name}",
    }


def download_output(cfg, name):
    """Return (bytes, content_type, filename) for an output file; never escape the output folder."""
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise ValueError("Invalid output filename")
    folder = Path(cfg["output_folder"])
    target = folder / name
    if not target.is_file() or target.resolve().parent != folder.resolve():
        raise FileNotFoundError("Output file not found")
    ext = target.suffix.lower().lstrip(".")
    ctype = core.CONTENT_TYPES.get(ext, "application/octet-stream")
    return target.read_bytes(), ctype, target.name


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
            if u.path == "/api/outputs":
                return self._send(200, {"files": list_outputs(ctl.cfg)})
            if u.path == "/api/output":
                try:
                    return self._send(200, read_output(ctl.cfg, q.get("name", [""])[0]))
                except FileNotFoundError as e:
                    return self._send(404, {"error": str(e)})
                except (ValueError, UnicodeError) as e:
                    return self._send(400, {"error": str(e)})
            if u.path == "/api/download":
                try:
                    data, ctype, fname = download_output(ctl.cfg, q.get("name", [""])[0])
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except FileNotFoundError as e:
                    return self._send(404, {"error": str(e)})
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                p = urlparse(self.path).path
                if p == "/api/upload":
                    ctype_hdr = self.headers.get("Content-Type", "")
                    if "multipart/form-data" in ctype_hdr:
                        n = int(self.headers.get("Content-Length") or 0)
                        raw = self.rfile.read(n)
                        parts = _parse_multipart(raw, ctype_hdr)
                        if "file" not in parts:
                            return self._send(400, {"error": "Missing 'file' field in upload"})
                        file_bytes, fname = parts["file"]
                        fname = fname or "upload.csv"
                        text = file_bytes.decode("utf-8-sig", errors="replace")
                        q = parse_qs(urlparse(self.path).query)
                        overrides = {
                            "output_format": q.get("format", [""])[0] or None,
                            "csv_layout": q.get("layout", [""])[0] or None,
                        }
                        result = handle_upload(fname, text, ctl.cfg, overrides)
                        return self._send(200, result)
                    else:
                        b = self._body()
                        name = b.get("name") or "upload.csv"
                        content = b.get("csv") if b.get("csv") is not None else b.get("content", "")
                        overrides = {
                            "output_format": b.get("format") or None,
                            "csv_layout": b.get("layout") or None,
                        }
                        custom_cfg = b.get("config")
                        cfg = core.normalize_config(custom_cfg) if custom_cfg else ctl.cfg
                        result = handle_upload(name, content, cfg, overrides)
                        return self._send(200, result)
                b = self._body()
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
