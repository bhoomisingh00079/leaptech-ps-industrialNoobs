"""Read, validate, cache and import the material CSV shared with YSUP-Link."""
import csv
import hashlib
import io
import json
import logging
import shutil
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .. import audit, csvread, delivery

log = logging.getLogger("cncbridge")

FIELD_ALIASES = {
    "part_number": ("part_number", "partnumber", "part", "cmpid", "component", "material_part_number"),
    "reel_id": ("reel_id", "reelid", "reel", "uniqueid", "material_id"),
    "lot_id": ("lot_id", "lotid", "lot", "batch", "batch_id"),
    "quantity": ("quantity", "qty", "remainqty", "remainingquantity", "stock"),
}
DEFAULT_FIELDS = {"part_number": True, "reel_id": True, "lot_id": False}
TEMPLATE_HEADERS = ("Part Number", "Description", "Reel ID", "LOT ID", "Quantity")
REFUSED_FILE = "ysup_materials.refused.json"

_CACHE = {}
_CACHE_LOCK = threading.RLock()
_IMPORT_LOCK = threading.Lock()


def _header_key(value):
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _content_bytes(content):
    return content.encode("utf-8-sig") if isinstance(content, str) else bytes(content)


def database_path(cfg, data_dir):
    """Resolve the shared database setting; a relative path lives in CNCBridge's data folder."""
    path = Path(str(cfg.get("ysup_database") or "ysup_materials.csv"))
    return path if path.is_absolute() else Path(data_dir) / path


def refused_path(path):
    return Path(path).parent / REFUSED_FILE


def _analyze(content, filename=""):
    rows = csvread.read_rows(_content_bytes(content))
    if not rows:
        raise ValueError("Database CSV has no material records")
    headers = [str(h).strip() for h in rows[0]]
    aliases = {field: {_header_key(alias) for alias in names} for field, names in FIELD_ALIASES.items()}
    indexes = {field: None for field in FIELD_ALIASES}
    for index, header in enumerate(headers):
        key = _header_key(header)
        for field, names in aliases.items():
            if indexes[field] is None and key in names:
                indexes[field] = index
    if indexes["part_number"] is None:
        raise ValueError("Database CSV needs a Part Number column (Part Number, PartNumber, Part, or CmpID)")

    # Retain the original row widths and every customer column in the extra dictionary.
    data_rows = []
    for row_number, raw in enumerate(rows[1:], 2):
        cells = list(raw[:len(headers)]) + [""] * max(0, len(headers) - len(raw))
        if not any(cell.strip() for cell in cells):
            continue
        data_rows.append((row_number, cells))
    if not data_rows:
        raise ValueError("Database CSV has no material records")

    mapping = {field: headers[index] if index is not None else None for field, index in indexes.items()}
    used_indexes = {index for index in indexes.values() if index is not None}
    mapping["extra_fields"] = [header for index, header in enumerate(headers) if index not in used_indexes]
    reel_index = indexes["reel_id"]
    reel_counts = {}
    for _row_number, cells in data_rows:
        if reel_index is not None and cells[reel_index].strip():
            key = cells[reel_index].strip().casefold()
            reel_counts[key] = reel_counts.get(key, 0) + 1
    duplicate_reels = {key for key, count in reel_counts.items() if count > 1}

    kept_rows, records, refused, warnings = [], [], [], []
    for row_number, cells in data_rows:
        extra = {header: cells[index] for index, header in enumerate(headers)}
        part = cells[indexes["part_number"]].strip()
        reel = cells[indexes["reel_id"]].strip() if indexes["reel_id"] is not None else ""
        quantity = cells[indexes["quantity"]].strip() if indexes["quantity"] is not None else ""
        if quantity:
            try:
                parsed_quantity = Decimal(quantity)
                if not parsed_quantity.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                warnings.append({"row": row_number, "field": mapping["quantity"], "value": quantity,
                                 "message": "Quantity is not a number; this alone does not exclude the row."})
        reason = "no_part_number" if not part else "duplicate_reel_id" if reel and reel.casefold() in duplicate_reels else ""
        if reason:
            refused.append({"row": row_number, "reason": reason, "data": extra})
            continue
        records.append({
            "part_number": part,
            "reel_id": reel,
            "lot_id": cells[indexes["lot_id"]].strip() if indexes["lot_id"] is not None else "",
            "quantity": quantity,
            "extra": extra,
        })
        kept_rows.append(cells)

    return {
        "filename": str(filename or ""),
        "headers": headers,
        "mapping": mapping,
        "rows_read": len(data_rows),
        "rows_kept": len(records),
        "rows_refused": len(refused),
        "refusal_counts": {reason: sum(row["reason"] == reason for row in refused)
                           for reason in ("no_part_number", "duplicate_reel_id")},
        "refused": refused,
        "warnings": warnings,
        "records": records,
        "kept_rows": kept_rows,
    }


def parse_database_csv(content):
    """Return valid records and the discovered field/header mapping for a CSV byte string."""
    report = _analyze(content)
    return report["records"], report["mapping"]


def import_report(content, filename=""):
    """Build the preview shown before a manual or automatic database replacement."""
    return _analyze(content, filename)


def _stored_csv(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def template_csv():
    """The standard customer import template, with a header row ready to fill."""
    return _stored_csv(TEMPLATE_HEADERS, [])


def _sidecar_bytes(report, content):
    stored_sha = hashlib.sha256(content).hexdigest()
    doc = {
        "sha256": stored_sha,
        "source": report["filename"],
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rows_read": report["rows_read"],
        "rows_kept": report["rows_kept"],
        "rows_refused": report["rows_refused"],
        "refusal_counts": report["refusal_counts"],
        "refused": report["refused"],
        "warnings": report["warnings"],
        "mapping": report["mapping"],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def _write_database(path, report, audit_dir):
    if not report["rows_kept"]:
        raise ValueError("Database CSV has no material records")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _stored_csv(report["headers"], report["kept_rows"])
    sidecar = refused_path(path)
    sidecar_data = _sidecar_bytes(report, content)
    old_sidecar = sidecar.read_bytes() if sidecar.exists() else None
    # Write the sidecar first. If replacing the database fails, restore its prior metadata; the old database
    # itself is untouched because atomic_write only replaces it after the complete new file has been written.
    try:
        delivery.atomic_write(sidecar, sidecar_data)
        delivery.atomic_write(path, content)
    except Exception:
        try:
            if old_sidecar is None:
                sidecar.unlink(missing_ok=True)
            else:
                delivery.atomic_write(sidecar, old_sidecar)
        except OSError:
            log.exception("Could not restore material import metadata after a failed database write")
        raise
    clear_cache(path)
    audit.note(audit_dir, "materials", f"Imported {report['filename'] or path.name}: "
               f"{report['rows_kept']} rows kept, {report['rows_refused']} refused")
    return {key: value for key, value in report.items() if key not in ("records", "kept_rows")}


def replace_database(content, cfg, data_dir, filename=""):
    """Validate and atomically replace the configured database, preserving a previous usable database on error."""
    with _IMPORT_LOCK:
        report = import_report(content, filename)
        return _write_database(database_path(cfg, data_dir), report, data_dir)


def clear_cache(path=None):
    with _CACHE_LOCK:
        if path is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(Path(path).resolve()), None)


def load(cfg, data_dir):
    """Load parsed materials from the configured CSV, caching the result by database/sidecar mtime."""
    path = database_path(cfg, data_dir)
    try:
        database_stat = path.stat()
    except OSError:
        return None
    sidecar = refused_path(path)
    try:
        sidecar_stat = sidecar.stat()
        sidecar_stamp = (sidecar_stat.st_mtime_ns, sidecar_stat.st_size)
    except OSError:
        sidecar_stamp = None
    key = str(path.resolve())
    stamp = (database_stat.st_mtime_ns, database_stat.st_size, sidecar_stamp)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    content = path.read_bytes()
    report = import_report(content, path.name)
    metadata = {}
    if sidecar_stamp:
        try:
            sidecar_doc = json.loads(sidecar.read_text(encoding="utf-8"))
            if sidecar_doc.get("sha256") == hashlib.sha256(content).hexdigest():
                metadata = sidecar_doc
        except (OSError, ValueError, AttributeError):
            pass
    result = {
        "path": path,
        "mtime_ns": database_stat.st_mtime_ns,
        "headers": report["headers"],
        "mapping": report["mapping"],
        "records": report["records"],
        "source": metadata.get("source") or path.name,
        "imported_at": metadata.get("imported_at") or datetime.fromtimestamp(
            database_stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "rows_read": metadata.get("rows_read", report["rows_read"]),
        "rows_kept": metadata.get("rows_kept", len(report["records"])),
        "rows_refused": metadata.get("rows_refused", 0),
        "refusal_counts": metadata.get("refusal_counts", {}),
        "refused": metadata.get("refused", []),
        "warnings": metadata.get("warnings", []),
    }
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, result)
    return result


def _unique_target(folder, name):
    target = Path(folder) / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target.with_name(f"{stem}-{stamp}{suffix}")
    number = 2
    while target.exists():
        target = target.with_name(f"{stem}-{stamp}-{number}{suffix}")
        number += 1
    return target


def import_pending(cfg, data_dir, alerts=None):
    """Import the first stable CSV in the optional feed folder; bad imports alert and keep the last database."""
    folder = Path(str(cfg.get("ysup_import_folder") or ""))
    if not folder.is_dir():
        return None
    with _IMPORT_LOCK:
        database = database_path(cfg, data_dir).resolve()
        try:
            candidates = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() == ".csv"
                                 and p.resolve() != database),
                                key=lambda p: (p.stat().st_mtime_ns, p.name.casefold()))
        except OSError as error:
            log.warning("Could not list the material import folder %s: %s", folder, error)
            return None
        if not candidates:
            return None
        source = candidates[0]
        try:
            before = source.stat()
            if cfg.get("stable_seconds", 0) and datetime.now().timestamp() - before.st_mtime < float(
                    cfg["stable_seconds"]):
                return None
            content = source.read_bytes()
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                return None
            report = import_report(content, source.name)
            if not report["rows_kept"]:
                raise ValueError("Database CSV has no material records")
            imported = folder / "imported"
            imported.mkdir(parents=True, exist_ok=True)
            target = _unique_target(imported, source.name)
            shutil.move(str(source), str(target))
            try:
                _write_database(database_path(cfg, data_dir), report, data_dir)
            except Exception:
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
                raise
            return {key: value for key, value in report.items() if key not in ("records", "kept_rows")}
        except Exception as error:
            try:
                if source.exists():
                    rejected = folder / "rejected"
                    rejected.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(_unique_target(rejected, source.name)))
            except OSError:
                log.exception("Could not move failed material CSV %s out of its import folder", source)
            text = f"Material database import failed for {source.name}; the existing database was kept: {error}"
            log.error(text)
            if alerts is not None:
                alerts.event(text, cfg, title="Material database import failed")
            return {"filename": source.name, "error": str(error)}
