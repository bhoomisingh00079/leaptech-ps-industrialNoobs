"""
LineBridge core engine.

Pipeline for every machine CSV that appears in the watched folder:

    detect (stable file) -> fingerprint (SHA-256, duplicate guard)
    -> parse into tables -> apply up to 10 selectors -> add 2 manual inputs
    -> render JSON / CSV / TXT (atomic write) -> deliver (shared folder + API, with outbox retry)
    -> archive source in dated back-up folder -> record everything in the audit ledger

Only the Python standard library is used, so it runs on locked-down factory PCs with no internet.
"""
import contextlib
import csv
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

__version__ = "1.0.0"

MAX_SELECTIONS = 10
MAX_MANUAL = 2
FORMATS = ("json", "csv", "txt")
CONTENT_TYPES = {"json": "application/json", "csv": "text/csv", "txt": "text/plain"}

DEFAULT_CONFIG = {
    "station_name": "SMT Line 1",
    "input_folder": "./input",            # where the machine drops its CSV logs
    "file_pattern": "*.csv",
    "backup_folder": "./backup",          # used source files are archived here (dated sub-folders)
    "output_folder": "./output",          # generated files are written here first
    "output_format": "json",              # json | csv | txt
    "csv_layout": "sections",             # sections | flat   (only for csv output)
    "selections": [""] * MAX_SELECTIONS,  # e.g. "L1xM8", "L1xM8[Ref,Result]", "L1:M8", "A1:B*", "L2x*"
    "manual_inputs": [{"label": "", "value": ""} for _ in range(MAX_MANUAL)],
    "shared_enabled": False,
    "shared_folder": "",                  # local path or UNC path, e.g. \\\\MES-PC\\inbox
    "api_enabled": False,
    "api_url": "",
    "api_token": "",                      # optional, sent as "Authorization: Bearer <token>"
    "api_body": "raw",                    # raw | multipart
    "api_timeout": 20,
    "skip_duplicates": True,              # same file content never sent twice
    "poll_seconds": 3,
    "stable_seconds": 2,                  # file must be untouched this long (machine finished writing)
    "ledger_path": "./linebridge_ledger.db",
}

log = logging.getLogger("linebridge")


# =============================================================== config
def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = Path(path)
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return normalize_config(cfg)


def normalize_config(cfg):
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base.update(cfg or {})
    base["selections"] = [str(s or "").strip() for s in (list(base["selections"]) + [""] * MAX_SELECTIONS)][:MAX_SELECTIONS]
    mi = [m for m in base["manual_inputs"] if isinstance(m, dict)] + [{"label": "", "value": ""}] * MAX_MANUAL
    base["manual_inputs"] = [{"label": str(m.get("label", "")), "value": str(m.get("value", ""))} for m in mi[:MAX_MANUAL]]
    if base["output_format"] not in FORMATS:
        raise ValueError(f"output_format must be one of {FORMATS}")
    return base


def save_config(cfg, path):
    Path(path).write_text(json.dumps(normalize_config(cfg), indent=2), encoding="utf-8")


# =============================================================== parsing
_PREFIX = re.compile(r"^(table|section)\s*[:=]\s*", re.I)
RANGE_RE = re.compile(r"^([A-Z]{1,3})(\d+)(?:x|:|×|to|-)([A-Z]{1,3})(\d+|\*)$", re.I)
COLS_RE = re.compile(r"^(.*?)\s*\[([^\]]*)\]\s*$")


def norm(name):
    """'[L1 x M8]' / 'l1×m8' / 'Table: L1xM8' -> 'l1xm8'"""
    n = _PREFIX.sub("", str(name).strip()).strip("[] ").lower().replace("×", "x")
    return re.sub(r"\s+", "", n)


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_rows(source):
    """Read a CSV from a path or from text. Handles BOM, odd bytes, and ; or tab delimiters."""
    is_path = isinstance(source, Path) or (
        isinstance(source, str) and "\n" not in source and len(source) < 1024 and os.path.isfile(source))
    if is_path:
        text = Path(source).read_bytes().decode("utf-8-sig", errors="replace")
    else:
        text = str(source).lstrip("﻿")
    sample = text[:4096]
    delim = ","
    if sample.count(";") > sample.count(",") and sample.count(";") > sample.count("\t"):
        delim = ";"
    elif sample.count("\t") > sample.count(","):
        delim = "\t"
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _header(cells):
    out, seen = [], {}
    for i, h in enumerate(cells):
        h = h or f"col{i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 1
        out.append(h)
    return out


def _table(header, rows, cell_range=""):
    cols = _header(header)
    recs = [dict(zip(cols, (r + [""] * len(cols))[: len(cols)])) for r in rows]
    return {"columns": cols, "rows": recs, "range": cell_range}


def split_tables(rows):
    """
    Split one machine CSV into named tables.

    A table = optional title row (one filled cell, e.g. `[L1xM8]` or `Table: L1xM8`)
              + header row + data rows, ended by a blank row or the next title.
    Rows before any title form a table called "ALL". Each table remembers its
    spreadsheet cell range (e.g. A5:I12) so operators can see where it sits.
    """
    tables = {}
    st = {"name": None, "header": None, "rows": [], "start": None, "end": None}

    def flush():
        if st["name"] and st["header"]:
            block = [st["header"]] + st["rows"]
            # machines often pad every row to a fixed width - drop trailing empty columns
            width = max(max((i + 1 for i, c in enumerate(r) if c), default=0) for r in block) or 1
            block = [r[:width] for r in block]
            rng = f"A{st['start'] + 1}:{col_letter(width - 1)}{st['end'] + 1}"
            key, n = st["name"], 2
            while key in tables:
                key, n = f"{st['name']}#{n}", n + 1
            tables[key] = _table(block[0], block[1:], rng)
        st.update(name=None, header=None, rows=[], start=None, end=None)

    for i, cells in enumerate(rows):
        filled = [c for c in cells if c]
        if not filled:
            flush()
            continue
        title_pending = st["name"] is not None and st["header"] is None
        is_title = (len(filled) == 1 and cells and cells[0] and not title_pending
                    and (st["header"] is None or len(st["header"]) > 1))
        if is_title:
            flush()
            st["name"] = _PREFIX.sub("", cells[0]).strip("[] ")
            continue
        if st["name"] is None:
            st["name"] = "ALL"
        if st["header"] is None:
            st["header"], st["start"] = cells, i
        else:
            st["rows"].append(cells)
        st["end"] = i
    flush()
    return tables


def range_table(rows, selector):
    """Excel-style block: 'L1:M8', 'L1 x M8', 'A5 to F*' (* = until last row). First row = header."""
    m = RANGE_RE.match(re.sub(r"\s+", "", selector))
    if not m:
        return None
    c1, r1, c2, r2 = m.groups()
    r1 = int(r1) - 1
    r2 = len(rows) - 1 if r2 == "*" else int(r2) - 1
    a, b = sorted((col_index(c1), col_index(c2)))
    r1, r2 = sorted((r1, r2))
    block = [(row + [""] * (b + 1))[a:b + 1] for row in rows[r1:r2 + 1]]
    block = [r for r in block if any(r)]
    if not block:
        return None
    label = f"{c1.upper()}{r1 + 1}:{c2.upper()}{'*' if m.group(4) == '*' else r2 + 1}"
    return label, _table(block[0], block[1:], label)


def parse_selector(sel):
    """'L1xM8[Ref, Result]' -> ('L1xM8', ['Ref', 'Result'])"""
    s, cols = sel.strip(), None
    m = COLS_RE.match(s)
    if m and m.group(1).strip():
        s = m.group(1).strip()
        cols = [c.strip() for c in m.group(2).split(",") if c.strip()]
    return s, cols


def _pick_columns(table, cols, sel, warnings):
    if not cols:
        return table
    lookup = {c.lower(): c for c in table["columns"]}
    keep = []
    for c in cols:
        real = lookup.get(c.lower())
        if real:
            keep.append(real)
        else:
            warnings.append(f"{sel}: column '{c}' not found")
    if not keep:
        return {**table, "rows": []}
    return {"columns": keep, "rows": [{k: r[k] for k in keep} for r in table["rows"]], "range": table["range"]}


def select(rows, tables, selections):
    """
    Resolve up to 10 selectors. Each may be:
      * a table name        L1xM8   (case/space-insensitive, `×` ok, wildcards: L1x*)
      * a cell range        L1:M8 / L1 x M8 / A5:F*   (used when no table has that name)
      * either + columns    L1xM8[Ref,Result]
    Returns (chosen: {label: table}, warnings: [str]).
    """
    chosen, warnings = {}, []
    by_norm = {norm(k): k for k in tables}
    for sel in selections:
        if not sel or not sel.strip():
            continue
        name, cols = parse_selector(sel)
        pat = norm(name)
        hits = [orig for nk, orig in by_norm.items() if fnmatch.fnmatch(nk, pat)]
        if hits:
            for h in hits:
                chosen[h] = _pick_columns(tables[h], cols, sel, warnings)
            continue
        rt = range_table(rows, name)
        if rt:
            label, t = rt
            chosen[label] = _pick_columns(t, cols, sel, warnings)
            continue
        warnings.append(f"'{sel}' not found (available: {', '.join(tables) or 'none'})")
    return chosen, warnings


# =============================================================== rendering
def manual_dict(cfg):
    return {m["label"].strip(): m["value"] for m in cfg["manual_inputs"] if m.get("label", "").strip()}


def render(fmt, meta, tables, csv_layout="sections"):
    """Return the output document as text."""
    manual = meta.get("manual_inputs", {})
    if fmt == "json":
        doc = dict(meta)
        doc["tables"] = {n: {"range": t["range"], "columns": t["columns"], "row_count": len(t["rows"]),
                             "rows": t["rows"]} for n, t in tables.items()}
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if csv_layout == "flat":
            # one row per record - easiest for MES/database import
            cols = []
            for t in tables.values():
                cols += [c for c in t["columns"] if c not in cols]
            fixed = ["source_file", "processed_at", "table"] + list(manual)
            w.writerow(fixed + cols)
            for name, t in tables.items():
                for r in t["rows"]:
                    w.writerow([meta["source_file"], meta["processed_at"], name] + list(manual.values())
                               + [r.get(c, "") for c in cols])
        else:
            for k in ("source_file", "processed_at", "station", "sha256"):
                w.writerow([f"#{k}", meta.get(k, "")])
            for k, v in manual.items():
                w.writerow([f"#manual:{k}", v])
            for name, t in tables.items():
                w.writerow([])
                w.writerow([f"[{name}]"])
                w.writerow(t["columns"])
                for r in t["rows"]:
                    w.writerow([r[c] for c in t["columns"]])
        return buf.getvalue()

    if fmt == "txt":
        lines = [f"{'SOURCE FILE':<14}: {meta['source_file']}",
                 f"{'PROCESSED AT':<14}: {meta['processed_at']}",
                 f"{'STATION':<14}: {meta.get('station', '')}"]
        for k, v in manual.items():
            lines.append(f"{k.upper()[:14]:<14}: {v}")
        for name, t in tables.items():
            cols, recs = t["columns"], t["rows"]
            lines += ["", f"=== {name}  [{t['range']}]  ({len(recs)} rows) ==="]
            widths = [max([len(c)] + [len(str(r[c])) for r in recs]) for c in cols]
            lines.append(" | ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip())
            lines.append("-+-".join("-" * w for w in widths))
            for r in recs:
                lines.append(" | ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)).rstrip())
        return "\n".join(lines) + "\n"

    raise ValueError(f"Unsupported format: {fmt}")


def atomic_write(path, data):
    """Write to a temp name then rename, so MES pollers never read a half-written file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.part")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


def build_output(src_name, sha, rows, cfg, now=None):
    """Parse + select + render. Returns (text, chosen_tables, warnings). Used by engine and preview."""
    now = now or datetime.now()
    tables = split_tables(rows)
    if any(s.strip() for s in cfg["selections"]):
        chosen, warnings = select(rows, tables, cfg["selections"])
    else:
        chosen, warnings = tables, []
    meta = {"source_file": src_name, "processed_at": now.isoformat(timespec="seconds"),
            "station": cfg.get("station_name", ""), "sha256": sha,
            "generator": f"LineBridge {__version__}", "manual_inputs": manual_dict(cfg)}
    text = render(cfg["output_format"], meta, chosen, cfg.get("csv_layout", "sections")) if chosen else ""
    return text, chosen, warnings, tables


# =============================================================== audit ledger
class Ledger:
    """SQLite record of every file: fingerprint, outcome, delivery status. Gives traceability + duplicate guard."""

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY, processed_at TEXT, source TEXT, sha256 TEXT, size INTEGER,
                status TEXT, tables TEXT, output TEXT, shared TEXT, api TEXT, message TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS ix_sha ON files(sha256)")

    @contextlib.contextmanager
    def _db(self):
        with self._lock:
            con = sqlite3.connect(self.path, timeout=10)
            try:
                yield con
                con.commit()
            finally:
                con.close()

    def seen(self, sha):
        with self._db() as c:
            return c.execute("SELECT 1 FROM files WHERE sha256=? AND status='ok' LIMIT 1", (sha,)).fetchone() is not None

    def add(self, **row):
        row.setdefault("processed_at", datetime.now().isoformat(timespec="seconds"))
        keys = ",".join(row)
        with self._db() as c:
            c.execute(f"INSERT INTO files({keys}) VALUES({','.join('?' * len(row))})", list(row.values()))

    def set_delivery(self, output, kind, status):
        with self._db() as c:
            c.execute(f"UPDATE files SET {kind}=? WHERE output=?", (status, output))

    def stats(self):
        with self._db() as c:
            counts = dict(c.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())
            last = c.execute("SELECT processed_at, source FROM files ORDER BY id DESC LIMIT 1").fetchone()
        return {"ok": counts.get("ok", 0), "failed": counts.get("failed", 0),
                "duplicate": counts.get("duplicate", 0), "last": last}

    def recent(self, n=50):
        with self._db() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (n,))]


# =============================================================== delivery
def _multipart(file_path, extra):
    boundary = uuid.uuid4().hex
    p = Path(file_path)
    parts = []
    for k, v in extra.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    ctype = CONTENT_TYPES.get(p.suffix.lstrip("."), "application/octet-stream")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{p.name}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_api(file_path, cfg):
    p = Path(file_path)
    if cfg.get("api_body") == "multipart":
        body, ctype = _multipart(p, {"station": cfg.get("station_name", "")})
    else:
        body, ctype = p.read_bytes(), CONTENT_TYPES.get(p.suffix.lstrip("."), "application/octet-stream")
    req = urllib.request.Request(cfg["api_url"], data=body, method="POST", headers={
        "Content-Type": ctype, "X-Filename": p.name, "X-Station": cfg.get("station_name", ""),
        "X-Content-SHA256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "User-Agent": f"LineBridge/{__version__}"})
    if cfg.get("api_token"):
        req.add_header("Authorization", f"Bearer {cfg['api_token']}")
    with urllib.request.urlopen(req, timeout=cfg.get("api_timeout", 20)) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.status


def copy_shared(file_path, cfg):
    dest = Path(cfg["shared_folder"])
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write(dest / Path(file_path).name, Path(file_path).read_bytes())


DESTINATIONS = {
    "shared": (copy_shared, lambda c: c["shared_enabled"] and c["shared_folder"]),
    "api": (send_api, lambda c: c["api_enabled"] and c["api_url"]),
}


# =============================================================== engine
def _unique(dest_dir, name):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    p, n = dest_dir / name, 1
    while p.exists():
        p = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class Engine:
    def __init__(self, cfg):
        self.cfg = normalize_config(cfg)
        self.ledger = Ledger(self.cfg["ledger_path"])
        self._stop = threading.Event()

    # ---- helpers
    def outbox(self, kind):
        d = Path(self.cfg["output_folder"]) / "_outbox" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def queued(self):
        return {k: len([f for f in self.outbox(k).glob("*") if not f.name.startswith(".")]) for k in DESTINATIONS}

    def stop(self):
        self._stop.set()

    # ---- delivery
    def deliver(self, out):
        result = {}
        for kind, (fn, enabled) in DESTINATIONS.items():
            if not enabled(self.cfg):
                result[kind] = "off"
                continue
            try:
                fn(out, self.cfg)
                result[kind] = "ok"
                log.info("  -> %s delivered: %s", kind, out.name)
            except Exception as e:
                shutil.copy2(out, self.outbox(kind) / out.name)
                result[kind] = "queued"
                log.warning("  -> %s unavailable (%s) - queued for retry", kind, e)
        return result

    def retry_outbox(self):
        for kind, (fn, enabled) in DESTINATIONS.items():
            if not enabled(self.cfg):
                continue
            for f in sorted(self.outbox(kind).glob("*")):
                if f.name.startswith("."):
                    continue
                try:
                    fn(f, self.cfg)
                    f.unlink()
                    self.ledger.set_delivery(f.name, kind, "ok (retried)")
                    log.info("Retry OK -> %s: %s", kind, f.name)
                except Exception as e:
                    log.debug("Retry still failing (%s) %s: %s", kind, f.name, e)
                    break  # destination still down; keep order, try next cycle

    # ---- pipeline
    def process_file(self, src):
        cfg, src = self.cfg, Path(src)
        now = datetime.now()
        sha = sha256_file(src)
        size = src.stat().st_size
        day = Path(cfg["backup_folder"]) / f"{now:%Y-%m-%d}"

        if cfg["skip_duplicates"] and self.ledger.seen(sha):
            shutil.move(str(src), str(_unique(Path(cfg["backup_folder"]) / "_duplicates", src.name)))
            self.ledger.add(source=src.name, sha256=sha, size=size, status="duplicate",
                            message="identical content already processed")
            log.warning("Duplicate skipped: %s", src.name)
            return "duplicate"

        text, chosen, warnings, tables = build_output(src.name, sha, read_rows(src), cfg, now)
        for w in warnings:
            log.warning("%s: %s", src.name, w)
        if not chosen:
            raise ValueError(f"no selected table found (file has: {', '.join(tables) or 'nothing'})")

        fmt = cfg["output_format"]
        out = _unique(Path(cfg["output_folder"]), f"{src.stem}_{now:%Y%m%d_%H%M%S}.{fmt}")
        atomic_write(out, text)
        log.info("Created %s  (%s)", out.name, ", ".join(chosen))
        status = self.deliver(out)

        shutil.move(str(src), str(_unique(day, src.name)))
        self.ledger.add(processed_at=now.isoformat(timespec="seconds"), source=src.name, sha256=sha, size=size,
                        status="ok", tables=", ".join(chosen), output=out.name,
                        shared=status["shared"], api=status["api"], message="; ".join(warnings))
        log.info("Archived %s -> %s", src.name, day)
        return "ok"

    def pending_files(self):
        inp = Path(self.cfg["input_folder"])
        if not inp.exists():
            log.error("Input folder not found: %s", inp.resolve())
            return []
        now = time.time()
        files = [f for f in inp.glob(self.cfg["file_pattern"]) if f.is_file() and not f.name.startswith(".")]
        files = [f for f in files if now - f.stat().st_mtime >= self.cfg["stable_seconds"]]
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))

    def scan_once(self):
        self.retry_outbox()
        done = 0
        for f in self.pending_files():
            if self._stop.is_set():
                break
            try:
                if self.process_file(f) == "ok":
                    done += 1
            except Exception as e:
                log.error("Failed %s: %s", f.name, e)
                try:
                    sha = sha256_file(f)
                    shutil.move(str(f), str(_unique(Path(self.cfg["backup_folder"]) / "_failed", f.name)))
                    self.ledger.add(source=f.name, sha256=sha, size=0, status="failed", message=str(e))
                except Exception as e2:
                    log.error("Could not quarantine %s: %s", f.name, e2)
        return done

    def run_forever(self):
        self._stop.clear()
        log.info("Watching %s (every %ss)", Path(self.cfg["input_folder"]).resolve(), self.cfg["poll_seconds"])
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:  # never let the watcher die on the shop floor
                log.exception("Scan error: %s", e)
            self._stop.wait(self.cfg["poll_seconds"])
        log.info("Watcher stopped")
