"""Persistent log of YSUP-Link exchanges."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def database_path(data_dir):
    return Path(data_dir) / "ysup.db"


def initialize(data_dir):
    """Create the exchange database and table before accepting requests."""
    with connect(data_dir):
        pass


@contextmanager
def connect(data_dir):
    path = database_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("""CREATE TABLE IF NOT EXISTS ysup_messages (
        id INTEGER PRIMARY KEY, received_at TEXT NOT NULL, client_name TEXT NOT NULL,
        caller_address TEXT NOT NULL, func_name TEXT NOT NULL, machine_serial TEXT NOT NULL,
        http_status INTEGER NOT NULL, result_summary TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
        request_json TEXT NOT NULL, response_json TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ysup_messages_received ON ysup_messages(received_at)")
    try:
        with db:
            yield db
    finally:
        db.close()


def record(data_dir, *, received_at, client_name, caller_address, func_name, machine_serial,
           http_status, result_summary, elapsed_ms, request_json, response_json):
    with connect(data_dir) as db:
        db.execute("""INSERT INTO ysup_messages
            (received_at, client_name, caller_address, func_name, machine_serial, http_status,
             result_summary, elapsed_ms, request_json, response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (received_at, client_name, caller_address, func_name, machine_serial, int(http_status),
                    result_summary, int(elapsed_ms), request_json, response_json))


def clean_up(data_dir, keep_days, now=None):
    """Remove exchanges older than the same retention window used for the history ledger."""
    if not keep_days:
        return 0
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoff = (now - timedelta(days=int(keep_days))).isoformat(timespec="seconds")
    path = database_path(data_dir)
    if not path.exists():
        return 0
    with connect(data_dir) as db:
        cursor = db.execute("DELETE FROM ysup_messages WHERE received_at < ?", (cutoff,))
        return cursor.rowcount
