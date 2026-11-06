"""
Settings change log: who changed which setting, and when.

Every save of the settings adds a line to settings_changes.jsonl in the data folder: when, the Windows user,
the PC, whether the program ran as administrator, how it was changed (save, import, restore, password,
operator) and the list of changes with the old and the new value. Passwords, tokens and header values are
never written, only the fact that they changed.

Each line also holds the fingerprint (SHA-256) of the settings file as it was saved. A settings file that
no longer matches the last line was edited outside CNCBridge. check_outside_edit() notices that, writes a
line of its own for it, and returns it so that the caller can raise an alert.
"""
import ctypes
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from .config import SECRET_KEYS, normalize_config
from .eventlog import SETTINGS_CHANGED, SETTINGS_OUTSIDE

log = logging.getLogger("cncbridge")

LOG_FILE = "settings_changes.jsonl"
KEEP = 5000                          # entries kept; older ones are dropped when the file is tidied
TIDY_ABOVE_BYTES = 2_000_000
NL = chr(10)

HIDDEN = set(SECRET_KEYS) | {"api_headers", "setup_password"}  # only "set" / "changed" is ever written
URL_KEYS = ("api_url", "alert_webhook_url", "heartbeat_url")
SKIPPED = {"config_version", "selections", "manual_inputs"}     # the last two are compared slot by slot
OUTSIDE = "(edited outside CNCBridge)"

LABELS = {
    "station_name": "Station name", "source_folder": "Machine folder", "file_pattern": "File pattern",
    "backup_folder": "Back-up folder", "output_format": "Output format", "shared_enabled": "Shared folder: on",
    "shared_folder": "Shared folder", "api_enabled": "API link: on", "api_url": "API link", "api_token": "API token",
    "api_headers": "API headers", "api_body": "API body", "api_timeout": "API timeout (seconds)",
    "ftp_enabled": "FTP server: on", "ftp_host": "FTP server", "ftp_port": "FTP port", "ftp_user": "FTP user",
    "ftp_password": "FTP password", "ftp_folder": "FTP folder", "ftp_tls": "FTPS (encrypted)",
    "ftp_passive": "FTP passive mode", "skip_duplicates": "Skip duplicate files", "encoding": "Encoding",
    "delimiter": "Delimiter", "poll_seconds": "Check every (seconds)", "stable_seconds": "File untouched for (seconds)",
    "complete_marker": "Complete marker", "priority_patterns": "Process first", "autostart": "Start monitoring with the program", "theme": "Theme",
    "color_mode": "Colours", "language": "Language", "font_family": "Font", "font_size": "Text size",
    "ui_scale": "Window scale", "json_numbers": "JSON numbers", "csv_flat": "CSV as one table",
    "time_zone": "Time zone in processed_at", "rename_columns": "Rename columns", "setup_password": "Setup password",
    "admin_idle_minutes": "Admin idle lock (minutes)",
    "shift_times": "Shift times", "keep_backup_days": "Keep back-up files (days)",
    "keep_history_days": "Keep history (days)", "alert_after_minutes": "Alert after (minutes)",
    "alert_notify": "Alerts: Windows notification", "alert_webhook_enabled": "Alerts: webhook on",
    "alert_webhook_url": "Alerts: webhook address", "alert_email_enabled": "Alerts: email on",
    "alert_email_server": "Alerts: mail server", "alert_email_port": "Alerts: mail port",
    "alert_email_starttls": "Alerts: encrypted mail", "alert_email_user": "Alerts: mail user",
    "alert_email_password": "Alerts: mail password", "alert_email_from": "Alerts: from address",
    "alert_email_to": "Alerts: to address(es)", "heartbeat_enabled": "Heartbeat: on",
    "heartbeat_url": "Heartbeat address", "heartbeat_folder": "Heartbeat folder",
    "heartbeat_minutes": "Heartbeat every (minutes)", "protect_settings": "Protect the settings",
    "password_accounts": "Accounts that may read the passwords",
}


# ---------------------------------------------------------------- who and what
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def who():
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    return {"user": domain + chr(92) + user if domain and user else user or "unknown",
            "computer": os.environ.get("COMPUTERNAME", ""), "elevated": is_admin()}


def file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def redact_url(url):
    """The address without its query string, which may carry a key."""
    return re.sub(r"[?#].*$", "?...", url) if re.search(r"[?#]", url) else url


def _show(key, value):
    if isinstance(value, bool):
        return "on" if value else "off"
    if value in ("", None):
        return "(empty)"
    text = redact_url(str(value)) if key in URL_KEYS else str(value)
    return text if len(text) <= 160 else text[:157] + "..."


def _slot(kind, item):
    item = item or {}
    if kind == "range":
        if not (item.get("name") or item.get("range")):
            return "(empty)"
        return (f"{item.get('name') or '(no name)'}: {item.get('range') or '(no range)'}"
                f"{'' if item.get('header', True) else ', no heading row'}")
    if not (item.get("name") or item.get("value")):
        return "(empty)"
    return f"{item.get('name') or '(no name)'} = {item.get('value', '')}"


def changes(old, new):
    """[{"setting", "old", "new"}] for everything that differs. Hidden values are never included."""
    old = old if old else normalize_config({})
    out = []
    for key, value in new.items():
        if key in SKIPPED or old.get(key) == value:
            continue
        label = LABELS.get(key) or key.replace("_", " ").capitalize()
        if key in HIDDEN:
            before = old.get(key)
            out.append({"setting": label, "old": "set" if before else "not set",
                        "new": "not set" if not value else "changed" if before else "set"})
        else:
            out.append({"setting": label, "old": _show(key, old.get(key)), "new": _show(key, value)})
    for key, kind, name in (("selections", "range", "Range"), ("manual_inputs", "typed", "Typed value")):
        before, after = old.get(key) or [], new.get(key) or []
        for i in range(max(len(before), len(after))):
            a = before[i] if i < len(before) else {}
            b = after[i] if i < len(after) else {}
            if a != b:
                out.append({"setting": f"{name} {i + 1}", "old": _slot(kind, a), "new": _slot(kind, b)})
    return out


def summary(entry, limit=3):
    """One line for a list: 'Range 3: A12:I* → A12:J*; Shared folder: x → y  (+2 more)'."""
    parts = [f"{c['setting']}: {c['old']} → {c['new']}" for c in entry.get("changes", [])]
    if not parts:
        return entry.get("detail") or "(nothing changed)"
    text = "; ".join(parts[:limit])
    return text + (f"  (+{len(parts) - limit} more)" if len(parts) > limit else "")


# ---------------------------------------------------------------- the file
def log_path(data_dir):
    return Path(data_dir) / LOG_FILE


def read(data_dir, limit=None):
    """The entries, newest first. Lines that cannot be read are skipped."""
    try:
        lines = log_path(data_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if limit and len(out) >= limit:
            break
    return out


def _append(data_dir, entry):
    path = log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + NL)
    try:
        if path.stat().st_size > TIDY_ABOVE_BYTES:  # tidying needs more rights than appending; skip it if denied
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > KEEP:
                tmp = path.with_name(path.name + ".part")
                tmp.write_text(NL.join(lines[-KEEP:]) + NL, encoding="utf-8")
                os.replace(tmp, path)
    except OSError:
        pass


def _entry(how, detail, diff, sha256):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), **who(), "how": how}
    if detail:
        entry["detail"] = detail
    entry.update({"changes": diff, "sha256": sha256})
    return entry


def record(data_dir, old, new, how="save", detail="", sha256=None):
    """
    Log a save. old is the settings before (None for a first save), new the settings now. Returns the entry,
    or None when nothing changed and the file is the same as at the last entry.
    """
    diff = changes(old, new)
    last = read(data_dir, 1)
    if not diff and last and last[0].get("sha256") == sha256:
        return None
    entry = _entry(how, detail, diff, sha256)
    _append(data_dir, entry)
    log.info("Settings changed (%s) by %s: %s", how, entry["user"], summary(entry), extra={"event": SETTINGS_CHANGED})
    return entry


def activity(data_dir, action, detail=""):
    """Record an Admin access event without putting credentials in the audit trail."""
    entry = _entry("access", detail or action, [], file_sha(Path(data_dir) / "config.json"))
    _append(data_dir, entry)
    return entry


def _last_saved(data_dir):
    """The settings as the last save left them: the newest copy in backups."""
    from .config import load_config
    copies = sorted((Path(data_dir) / "backups").glob("config-*.json"))
    try:
        return load_config(copies[-1]) if copies else None
    except (OSError, ValueError):
        return None


def check_outside_edit(data_dir):
    """
    Compare the settings file with the last entry. Returns None when it matches (or when there is no log yet:
    the first look writes a baseline). When it does not match, the edit is logged as an entry and returned.
    """
    from .config import load_config
    path = Path(data_dir) / "config.json"
    if not path.exists():
        return None
    sha = file_sha(path)
    last = read(data_dir, 1)
    if not last:
        _append(data_dir, _entry("baseline", "settings as found", [], sha))
        return None
    if last[0].get("sha256") == sha:
        return None
    diff = []
    before = _last_saved(data_dir)
    if before is not None:
        try:
            diff = changes(before, load_config(path))
        except (OSError, ValueError):
            pass
    stamp = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    entry = _entry("outside", f"the settings file was changed at {stamp} by something other than CNCBridge", diff, sha)
    entry["user"] = OUTSIDE
    _append(data_dir, entry)
    log.warning("The settings file was changed outside CNCBridge (%s)%s", stamp,
                f": {summary(entry)}" if diff else "", extra={"event": SETTINGS_OUTSIDE})
    return entry


def outside_text(entry):
    """What an alert says about an edit made outside CNCBridge."""
    what = f" Changes: {summary(entry, 5)}." if entry.get("changes") else ""
    return ("The settings file was changed outside CNCBridge (not through its Save button)." + what +
            " See View > Settings history.")
