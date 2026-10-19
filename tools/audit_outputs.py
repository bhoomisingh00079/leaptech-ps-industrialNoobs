"""Verify saved JSON outputs against archived CSV inputs and the audit ledger."""
import hashlib
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from linebridge import core


def audit(root):
    root = Path(root)
    cfg = core.load_config(root / "config.json")
    output_dir = root / cfg["output_folder"]
    backup_dir = root / cfg["backup_folder"]
    ledger_path = root / cfg["ledger_path"]
    issues = []
    totals = Counter()
    outputs = sorted(p for p in output_dir.iterdir() if p.is_file() and p.suffix in (".json", ".csv", ".txt"))
    with sqlite3.connect(ledger_path) as db:
        db.row_factory = sqlite3.Row
        ledger = [dict(row) for row in db.execute("SELECT * FROM files")]
    ok_rows = [row for row in ledger if row["status"] == "ok"]
    by_output = {row["output"]: row for row in ok_rows}
    if len(by_output) != len(ok_rows):
        issues.append("Ledger has duplicate output filenames")
    if {p.name for p in outputs} != set(by_output):
        issues.append("Output files and successful ledger entries do not match")
    for path in outputs:
        try:
            if path.suffix != ".json":
                issues.append(f"{path.name}: content validation for {path.suffix} is not implemented")
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            row = by_output.get(path.name)
            if not row:
                continue
            source = list(backup_dir.rglob(row["source"]))
            if len(source) != 1:
                issues.append(f"{path.name}: expected one archived source, found {len(source)}")
                continue
            source = source[0]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if doc.get("source_file") != source.name or doc.get("sha256") != digest or row["sha256"] != digest:
                issues.append(f"{path.name}: source name or SHA-256 mismatch")
            if not doc.get("station") or not doc.get("generator") or not doc.get("processed_at"):
                issues.append(f"{path.name}: required metadata missing")
            else:
                datetime.fromisoformat(doc["processed_at"])
            expected = core.split_tables(core.read_rows(source))
            if set(doc.get("tables", {})) != set(expected):
                issues.append(f"{path.name}: table names differ from archived CSV")
            for name, table in expected.items():
                actual = doc.get("tables", {}).get(name, {})
                if any(actual.get(key) != table[key] for key in ("range", "columns", "rows")):
                    issues.append(f"{path.name}: values, headers, or range differ in {name}")
                if actual.get("row_count") != len(table["rows"]):
                    issues.append(f"{path.name}: row count differs in {name}")
                totals["rows"] += len(table["rows"])
                totals["columns_max"] = max(totals["columns_max"], len(table["columns"]))
            totals["json_files"] += 1
            totals["bytes"] += path.stat().st_size
        except Exception as exc:
            issues.append(f"{path.name}: {exc}")
    totals["ledger_ok"] = len(ok_rows)
    totals["ledger_failed"] = sum(row["status"] == "failed" for row in ledger)
    totals["ledger_duplicate"] = sum(row["status"] == "duplicate" for row in ledger)
    totals["partial_files"] = len(list(output_dir.rglob("*.part")))
    if totals["ledger_failed"] or totals["partial_files"]:
        issues.append("Failed ledger entries or partial output files are present")

    source_dir = backup_dir.parent / "source"
    raw_data = (source_dir / "secom.data").read_text(encoding="utf-8").splitlines()
    raw_labels = (source_dir / "secom_labels.data").read_text(encoding="utf-8").splitlines()
    seen_records = set()
    for archived in backup_dir.rglob("secom_*.csv"):
        with archived.open(encoding="utf-8", newline="") as stream:
            for record in csv.DictReader(stream):
                number = int(record["record_id"])
                if number in seen_records or not 1 <= number <= len(raw_data):
                    issues.append(f"{archived.name}: duplicate or invalid record ID {number}")
                    continue
                seen_records.add(number)
                source_values = raw_data[number - 1].split()
                if any(record[f"sensor_{i:03d}"] != ("" if value == "NaN" else value)
                       for i, value in enumerate(source_values, 1)):
                    issues.append(f"{archived.name}: sensor mismatch at record {number}")
                label, timestamp = raw_labels[number - 1].split(' "', 1)
                expected_time = datetime.strptime(timestamp.rstrip('"'), "%d/%m/%Y %H:%M:%S").isoformat(sep=" ")
                if record["yield_label"] != label or record["test_timestamp"] != expected_time or record["yield_result"] != ("pass" if label == "-1" else "fail"):
                    issues.append(f"{archived.name}: label or timestamp mismatch at record {number}")
                totals["secom_pass" if label == "-1" else "secom_fail"] += 1
    if len(seen_records) != len(raw_data):
        issues.append(f"SECOM coverage: {len(seen_records)} of {len(raw_data)} source records")
    experiment_archives = list(backup_dir.rglob("experiment_13.csv"))
    if len(experiment_archives) != 1 or hashlib.sha256(experiment_archives[0].read_bytes()).digest() != hashlib.sha256((source_dir / "experiment_13.csv").read_bytes()).digest():
        issues.append("Archived experiment file does not match the supplied source copy")
    return totals, issues


if __name__ == "__main__":
    import sys
    summary, problems = audit(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd())
    print(json.dumps({"summary": summary, "issues": problems}, indent=2))
    raise SystemExit(1 if problems else 0)
