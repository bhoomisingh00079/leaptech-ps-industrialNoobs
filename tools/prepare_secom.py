"""Validate and stage the supplied SECOM records as LineBridge CSV batches."""
import argparse
import csv
import math
import re
from datetime import datetime
from pathlib import Path


LABEL = re.compile(r'^(-1|1)\s+"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})"$')


def prepare(data_path, labels_path, incoming, batch_size=100):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    data_lines = Path(data_path).read_text(encoding="utf-8").splitlines()
    label_lines = Path(labels_path).read_text(encoding="utf-8").splitlines()
    if not data_lines or len(data_lines) != len(label_lines):
        raise ValueError("measurement and label files must have the same nonzero row count")

    width = len(data_lines[0].split())
    if not width:
        raise ValueError("measurement rows have no sensor values")
    header = ["record_id", "test_timestamp", "yield_label", "yield_result"] + [
        f"sensor_{i:03d}" for i in range(1, width + 1)
    ]
    records = []
    missing = 0
    for line_number, (line, label_line) in enumerate(zip(data_lines, label_lines), 1):
        values = line.split()
        if len(values) != width:
            raise ValueError(f"measurement row {line_number} has {len(values)} values; expected {width}")
        match = LABEL.fullmatch(label_line.strip())
        if not match:
            raise ValueError(f"invalid label or timestamp at row {line_number}")
        timestamp = datetime.strptime(match.group(2), "%d/%m/%Y %H:%M:%S").isoformat(sep=" ")
        label = int(match.group(1))
        for value in values:
            if value == "NaN":
                missing += 1
            elif not math.isfinite(float(value)):
                raise ValueError(f"non-finite sensor value at row {line_number}")
        records.append([line_number, timestamp, label, "pass" if label == -1 else "fail"] +
                       ["" if value == "NaN" else value for value in values])

    incoming = Path(incoming)
    incoming.mkdir(parents=True, exist_ok=True)
    if list(incoming.glob("*.csv")):
        raise FileExistsError(f"CSV files already exist in {incoming}; refusing to overwrite staged logs")
    for start in range(0, len(records), batch_size):
        end = min(start + batch_size, len(records))
        target = incoming / f"secom_{start + 1:04d}_{end:04d}.csv"
        with target.open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            writer.writerow(header)
            writer.writerows(records[start:end])
    return len(records), width, missing, (len(records) + batch_size - 1) // batch_size


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("incoming", type=Path)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    rows, sensors, missing, files = prepare(args.data, args.labels, args.incoming, args.batch_size)
    print(f"Staged {rows} records with {sensors} sensors in {files} CSV files ({missing} missing values).")
