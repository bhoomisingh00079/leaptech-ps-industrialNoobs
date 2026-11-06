"""CNCBridge settings: defaults, load/save, the data folder, and API token protection (Windows DPAPI)."""
import base64
import ctypes
import hashlib
import hmac
import json
import logging
import os
import re  # M2-materials
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .csvread import split_patterns

APP_NAME = "CNCBridge"
VERSION = "1.2.0"
SETTINGS_VERSION = 6  # the layout of config.json; MIGRATIONS bring an older file up to it
OPERATOR_FILE = "operator.json"   # the typed values: what an operator may change without the setup rights
SECRETS_FILE = "secrets.json"     # the encrypted passwords and tokens, apart so that they can be locked away

MAX_SELECTIONS = 10
MAX_MANUAL = 2
FORMATS = ("json", "csv", "txt", "pdf")
API_BODIES = ("raw", "multipart")
COLOR_MODES = ("light", "dark", "system")
FONT_SIZES = (90, 100, 115, 130)
UI_SCALES = (90, 100, 110, 125, 150)
BACKUPS_KEPT = 20  # copies of the settings file kept in <data folder>\backups

DEFAULT_CONFIG = {
    "config_version": SETTINGS_VERSION,   # which layout of this file the program wrote; see MIGRATIONS
    "station_name": "",
    "source_folder": "",                  # where the machine drops its CSV files
    "file_pattern": "*.csv",              # one or more, separated by ;  e.g. *.csv; *.json
    "priority_patterns": "",              # files that fit these (separated by ;) are processed first, in this order
    "backup_folder": "",                  # used source files are moved here (dated sub-folders)
    "output_format": "json",              # json | csv | txt | pdf
    "selections": [{"name": "", "range": "", "header": True} for _ in range(MAX_SELECTIONS)],
    "manual_inputs": [{"name": "", "value": ""} for _ in range(MAX_MANUAL)],
    "shared_enabled": False,
    "shared_folder": "",                  # local or UNC path, e.g. \\MES-PC\inbox
    "api_enabled": False,
    "api_url": "",
    "api_token": "",                      # sent as "Authorization: Bearer <token>"; encrypted on disk
    "api_headers": "",                    # extra headers, one "Name: value" per line
    "api_body": "raw",                    # raw | multipart
    "api_timeout": 20,
    "ftp_enabled": False,                 # third destination: upload each output file to an FTP / FTPS server
    "ftp_host": "",
    "ftp_port": 21,
    "ftp_user": "",                       # empty = anonymous
    "ftp_password": "",                   # encrypted on disk, like the API token; never exported
    "ftp_folder": "",                     # folder on the server (created if missing); empty = the login folder
    "ftp_tls": False,                     # FTPS: explicit TLS, the login and the data are encrypted
    "ftp_passive": True,
    "skip_duplicates": True,              # identical file content is never sent twice
    "encoding": "auto",                   # auto | utf-8 | cp1252 | utf-16 | shift_jis | ...
    "delimiter": "auto",                  # auto | , | ; | tab | |
    "poll_seconds": 3,
    "stable_seconds": 2,                  # file must be untouched this long before it is picked up
    "complete_marker": "",                # text the machine writes last (e.g. End_Time); empty = idle time only
    "autostart": False,                   # start monitoring when the program opens
    "theme": "modern",                    # the look of the window: modern | legacy (more to come); applies on restart
    "color_mode": "light",                # light | dark | system (follow Windows)
    "language": "en",                     # the window's language: a code from cncbridge/locales; applies on restart
    "font_family": "",                    # "" = the theme's own font, else one of theme.FONT_CHOICES
    "font_size": 100,                     # text size in percent: one of FONT_SIZES
    "ui_scale": 100,                      # size of the whole window in percent: one of UI_SCALES
    # output switches: all off, so the files stay exactly as in version 1.0
    "json_numbers": False,                # JSON: write 12 and 40.53 as numbers, not as text
    "csv_flat": False,                    # CSV: one plain table, one row per record
    "time_zone": False,                   # processed_at carries the PC's time zone, e.g. +05:30
    "rename_columns": "",                 # one "Old=New" per line, applied to the column names of every block
    "setup_password": "",                 # salted Admin password hash; retained as a key for compatibility
    "admin_idle_minutes": 0,                # log Admin out after this many idle minutes; 0 = off
    "ysup_database": "ysup_materials.csv",  # M2-materials: imported CSV, relative to the data folder by default
    "ysup_fields": {"part_number": True, "reel_id": True, "lot_id": False},  # M2-materials
    "ysup_extra_fields": [],                # M2-materials: customer fields also checked by later YSUP lookups
    "ysup_import_folder": "",              # M2-materials: optional folder fed by a customer database export
    "shift_times": "",                    # e.g. "06:00, 14:00, 22:00": ask the operator to confirm the typed values
    "keep_backup_days": 0,                # delete back-up files older than this; 0 = keep them all
    "keep_history_days": 0,               # delete history rows older than this; 0 = keep them all
    # alerts: a problem that lasts alert_after_minutes, or a file set aside, is announced
    "alert_after_minutes": 10,
    "alert_notify": True,                 # Windows notification on this PC
    "alert_webhook_enabled": False,
    "alert_webhook_url": "",
    "alert_email_enabled": False,
    "alert_email_server": "",
    "alert_email_port": 587,
    "alert_email_starttls": True,
    "alert_email_user": "",
    "alert_email_password": "",           # encrypted on disk, like the API token
    "alert_email_from": "",
    "alert_email_to": "",                 # one or more addresses, separated by commas
    # heartbeat: a regular sign of life for a monitoring tool or the MES (see heartbeat.py)
    "heartbeat_enabled": False,
    "heartbeat_url": "",                  # a JSON POST to this address ...
    "heartbeat_folder": "",               # ... and/or <station>.status.json written into this folder
    "heartbeat_minutes": 5,
    # protection: with it on, an administrator locks config.json and the passwords away (see permissions.py)
    "protect_settings": False,
    "password_accounts": "",                # Windows accounts that may read the saved passwords, one per line
}
SECRET_KEYS = ("api_token", "alert_email_password", "ftp_password")  # encrypted on disk, never exported

log = logging.getLogger("cncbridge")


def _number(value, default, low):
    try:
        return max(low, float(value))
    except (TypeError, ValueError):
        return default


def normalize_config(cfg):
    """Fill in defaults and force every setting into its expected shape."""
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base.update({k: v for k, v in (cfg or {}).items() if k in base})

    sels = [s for s in base["selections"] if isinstance(s, dict)]
    sels += [{} for _ in range(MAX_SELECTIONS)]
    base["selections"] = [{"name": str(s.get("name", "")).strip(), "range": str(s.get("range", "")).strip(),
                           "header": bool(s.get("header", True))} for s in sels[:MAX_SELECTIONS]]
    mans = [m for m in base["manual_inputs"] if isinstance(m, dict)]
    mans += [{} for _ in range(MAX_MANUAL)]
    base["manual_inputs"] = [{"name": str(m.get("name", "")).strip(), "value": str(m.get("value", ""))}
                             for m in mans[:MAX_MANUAL]]

    for key in ("station_name", "source_folder", "file_pattern", "backup_folder", "shared_folder",
                "api_url", "api_token", "encoding", "delimiter", "complete_marker", "setup_password", "shift_times",
                "alert_webhook_url", "ftp_host", "ftp_user", "ftp_folder", "heartbeat_url", "heartbeat_folder",
                "alert_email_server",
                "alert_email_user", "alert_email_from", "alert_email_to"):
        base[key] = str(base[key] or "").strip()
    base["api_headers"] = str(base["api_headers"] or "")
    base["password_accounts"] = str(base["password_accounts"] or "")
    base["theme"] = str(base["theme"] or "").strip().lower() or "modern"  # an unknown name falls back when used
    base["color_mode"] = str(base["color_mode"] or "").strip().lower()
    if base["color_mode"] not in COLOR_MODES:
        base["color_mode"] = "light"
    base["font_family"] = str(base["font_family"] or "").strip()
    base["language"] = str(base["language"] or "").strip().lower() or "en"  # an unknown one shows English
    for key, allowed in (("font_size", FONT_SIZES), ("ui_scale", UI_SCALES)):
        base[key] = int(_number(base[key], 100, 1))
        if base[key] not in allowed:
            base[key] = 100
    base["rename_columns"] = str(base["rename_columns"] or "")
    base["priority_patterns"] = "; ".join(split_patterns(base["priority_patterns"]))
    base["file_pattern"] = "; ".join(split_patterns(base["file_pattern"]))
    base["alert_email_password"] = str(base["alert_email_password"] or "")  # spaces may be part of it
    base["ftp_password"] = str(base["ftp_password"] or "")
    for key in ("shared_enabled", "api_enabled", "skip_duplicates", "autostart", "json_numbers", "csv_flat",
                "time_zone", "alert_notify",
                "alert_webhook_enabled", "alert_email_enabled", "alert_email_starttls", "ftp_enabled", "ftp_tls",
                "ftp_passive", "heartbeat_enabled", "protect_settings"):
        base[key] = bool(base[key])
    base["file_pattern"] = base["file_pattern"] or "*.csv"
    base["encoding"] = base["encoding"] or "auto"
    base["delimiter"] = base["delimiter"] or "auto"
    if base["output_format"] not in FORMATS:
        base["output_format"] = "json"
    if base["api_body"] not in API_BODIES:
        base["api_body"] = "raw"
    base["poll_seconds"] = _number(base["poll_seconds"], 3, 0.5)
    base["stable_seconds"] = _number(base["stable_seconds"], 2, 0)
    base["api_timeout"] = _number(base["api_timeout"], 20, 1)
    base["keep_backup_days"] = int(_number(base["keep_backup_days"], 0, 0))
    base["keep_history_days"] = int(_number(base["keep_history_days"], 0, 0))
    base["alert_after_minutes"] = _number(base["alert_after_minutes"], 10, 0)
    base["heartbeat_minutes"] = _number(base["heartbeat_minutes"], 5, 1)
    base["admin_idle_minutes"] = int(_number(base["admin_idle_minutes"], 0, 0))
    base["ysup_database"] = str(base["ysup_database"] or "ysup_materials.csv").strip() or "ysup_materials.csv"  # M2-materials
    base["ysup_import_folder"] = str(base["ysup_import_folder"] or "").strip()  # M2-materials
    ysup_fields = base.get("ysup_fields") if isinstance(base.get("ysup_fields"), dict) else {}  # M2-materials
    base["ysup_fields"] = {key: bool(ysup_fields.get(key, value)) for key, value in {  # M2-materials
        "part_number": True, "reel_id": True, "lot_id": False}.items()}
    ysup_extra = base.get("ysup_extra_fields")  # M2-materials
    if isinstance(ysup_extra, str):  # M2-materials
        ysup_extra = re.split(r"[,\n]", ysup_extra)  # M2-materials
    if not isinstance(ysup_extra, (list, tuple)):  # M2-materials
        ysup_extra = []  # M2-materials
    base["ysup_extra_fields"] = list(dict.fromkeys(str(name).strip() for name in ysup_extra  # M2-materials
                                                   if name is not None and str(name).strip()))  # M2-materials
    base["alert_email_port"] = int(_number(base["alert_email_port"], 587, 1))
    base["ftp_port"] = int(_number(base["ftp_port"], 21, 1))
    if base["ftp_port"] > 65535:
        base["ftp_port"] = 21
    base["config_version"] = SETTINGS_VERSION
    return base


# ---------------------------------------------------------------- settings versions
class SettingsTooNew(ValueError):
    """The settings file was written by a newer CNCBridge than this one."""


def _v1_to_v2(raw):
    return raw  # 1.2.0 files carried no version; the layout itself did not change


# MIGRATIONS[n] turns a version-n file into version n+1. A file without a version is version 1.
def _v2_to_v3(raw):
    # 3 keeps the typed values in operator.json and the passwords in secrets.json. Nothing is moved here: a
    # file without those two is read as before, and the next save writes the new layout.
    return raw


def _v3_to_v4(raw):
    # 4 allows several file patterns ("*.csv; *.json"), adds priority_patterns and the PDF output. Nothing to
    # convert, but an older program would read "*.csv; *.json" as one pattern that matches nothing, and would
    # quietly send JSON instead of PDF, so it must not take this file.
    return raw


def _v4_to_v5(raw):
    # Keep the old key and hash: existing setup passwords become the Admin password without a reset.
    raw.setdefault("setup_password", "")
    raw.setdefault("admin_idle_minutes", 0)
    return raw


def _v5_to_v6(raw):  # M2-materials
    raw.setdefault("ysup_database", "ysup_materials.csv")  # M2-materials
    raw.setdefault("ysup_fields", {"part_number": True, "reel_id": True, "lot_id": False})  # M2-materials
    raw.setdefault("ysup_extra_fields", [])  # M2-materials
    raw.setdefault("ysup_import_folder", "")  # M2-materials
    return raw  # M2-materials


MIGRATIONS = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5}
MIGRATIONS[5] = _v5_to_v6  # M2-materials


def file_version(raw):
    try:
        return max(1, int(raw.get("config_version", 1)))
    except (AttributeError, TypeError, ValueError):
        return 1


def migrate(raw):
    """A settings dict from any older layout, brought up to the current one. Newer layouts raise SettingsTooNew."""
    version = file_version(raw)
    if version > SETTINGS_VERSION:
        raise SettingsTooNew(f"these settings were saved by a newer {APP_NAME} (settings version {version}; "
                             f"this program understands up to {SETTINGS_VERSION})")
    raw = dict(raw)
    while version < SETTINGS_VERSION:
        raw = MIGRATIONS[version](raw)
        version += 1
    raw["config_version"] = SETTINGS_VERSION
    return raw


# ---------------------------------------------------------------- token protection
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    src, out = _Blob(len(data), ctypes.create_string_buffer(data, len(data))), _Blob()
    if protect:
        # 0x1 = never show UI, 0x4 = machine scope, so a scheduled task under another Windows account can read it
        ok = crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0x1 | 0x4, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    """Encrypt a secret for storage in config.json. Only this PC can decrypt it."""
    if not text or os.name != "nt":
        return text
    return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(stored):
    if not stored.startswith("dpapi:"):
        return stored  # typed into the file by hand
    return _dpapi(base64.b64decode(stored[6:]), False).decode("utf-8")


# ---------------------------------------------------------------- load / save
def _read_json(path):
    """A small JSON file as a dict; {} when there is none or it is not readable; None when access is denied."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except PermissionError:
        return None
    except (OSError, ValueError) as e:
        log.warning("%s could not be read: %s", path, e)
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_json(path, doc):
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)  # e.g. the file is protected: nothing may be left behind
        raise


def load_config(path, quiet=False):
    """
    The settings: config.json, the passwords from secrets.json and the typed values from operator.json.
    A file from before those two existed still carries them itself, and is read as it is.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    try:
        raw = migrate(raw)
    except SettingsTooNew as e:  # use what can be understood, but save_config will not overwrite the file
        log.warning("%s: %s", p, e)
    cfg = normalize_config(raw)
    stored = _read_json(p.parent / SECRETS_FILE)
    if stored is None:
        if not quiet:
            log.warning("This Windows account may not read the saved passwords (%s). Run CNCBridge as administrator, "
                        "or ask an administrator to add this account under Operators & access.", p.parent / SECRETS_FILE)
        stored = {}
    for key in SECRET_KEYS:
        try:
            cfg[key] = unprotect(stored[key] if key in stored else cfg[key])
        except (OSError, ValueError):
            log.warning("The saved %s cannot be read on this PC - enter it again", key.replace("_", " "))
            cfg[key] = ""
    typed = _read_json(p.parent / OPERATOR_FILE) or {}
    values = typed.get("manual_inputs")
    if isinstance(values, list):
        for slot, value in zip(cfg["manual_inputs"], values):
            slot["value"] = str(value)
    return cfg


def check_not_newer(path):
    """Raise SettingsTooNew when the file on disk was written by a newer program, so it is not overwritten."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if file_version(raw) > SETTINGS_VERSION:
        migrate(raw)  # raises with the message


def save_config(cfg, path, how="save", detail=""):
    """
    Write the settings: config.json (without passwords or typed values), secrets.json (the passwords, encrypted)
    and operator.json (the typed values). how says what led to it (save, import, restore, password, operator),
    for the settings change log (audit.py), which also notices a file that was edited outside CNCBridge.
    With protect_settings on, the files are locked afterwards (permissions.py).
    """
    p = Path(path)
    check_not_newer(p)
    out = normalize_config(cfg)
    now = normalize_config(cfg)  # with the secrets readable, for the change log
    secrets = {key: protect(out[key]) for key in SECRET_KEYS}
    for key in SECRET_KEYS:
        out[key] = ""
    values = [m["value"] for m in out["manual_inputs"]]
    for slot in out["manual_inputs"]:
        slot["value"] = ""
    previous = _audit_before(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p.parent / SECRETS_FILE, secrets)
    _write_json(p.parent / OPERATOR_FILE, {"manual_inputs": values})
    _write_json(p, out)
    _keep_backup(p)
    _protect(p.parent, now, previous)
    _audit_after(p, previous, now, how, detail)


def save_operator_values(data_dir, values, how="operator"):
    """
    Write only the typed values (operator.json). This is all an operator without the setup rights may change.
    Returns the settings as they are now.
    """
    folder = Path(data_dir)
    path = folder / "config.json"
    previous = load_config(path)
    _write_json(folder / OPERATOR_FILE, {"manual_inputs": [str(v) for v in list(values)[:MAX_MANUAL]]})
    now = load_config(path)
    _audit_after(path, previous, now, how, "")
    return now


def _protect(folder, now, previous):
    """Lock the settings files when protection is on; give them back their permissions when it was just switched off."""
    try:
        from . import permissions
        if now["protect_settings"]:
            problems = permissions.lock_down(folder, permissions.account_list(now["password_accounts"]))
            for problem in problems:
                log.warning("Protecting the settings: %s", problem)
        elif previous and previous.get("protect_settings"):
            for problem in permissions.unlock(folder):
                log.warning("Removing the protection of the settings: %s", problem)
    except Exception as e:  # saving must work even when the permissions cannot be changed
        log.warning("Could not change the permissions of the settings files: %s", e)


def _audit_before(p):
    """Log an edit made outside CNCBridge before it is overwritten; the settings as they were, or None."""
    try:
        from . import audit
        audit.check_outside_edit(p.parent)
        return load_config(p) if p.exists() else None
    except Exception as e:  # the change log must never stop a save
        log.warning("Could not read the settings for the change log: %s", e)
        return None


def _audit_after(p, previous, now, how, detail):
    try:
        from . import audit
        audit.record(p.parent, previous, now, how, detail, audit.file_sha(p))
    except Exception as e:
        log.warning("Could not write the settings change log: %s", e)


def _keep_backup(path):
    """A copy of every saved settings file in backups\\, so a lost file or a bad change can be undone."""
    folder = path.parent / "backups"
    try:
        folder.mkdir(exist_ok=True)
        shutil.copy2(path, folder / f"config-{datetime.now():%Y%m%d-%H%M%S}.json")
        for old in sorted(folder.glob("config-*.json"))[:-BACKUPS_KEPT]:
            old.unlink()
    except OSError as e:
        log.warning("Could not keep a backup copy of the settings: %s", e)


def export_settings(cfg, path):
    """
    Write the settings to a file, for another PC or for safekeeping. The API token and the email password
    are left out: they are encrypted for this PC and would not work anywhere else.
    """
    out = normalize_config(cfg)
    for key in SECRET_KEYS:
        out[key] = ""
    out["setup_password"] = ""  # a settings file never carries or changes a password
    doc = {"exported_by": f"{APP_NAME} {VERSION}", "exported_at": datetime.now().isoformat(timespec="seconds")}
    doc.update(out)
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def import_settings(path, current):
    """
    Settings from an exported file or a backup copy. The current API token and email password are kept
    when the file has none, or has one that cannot be read on this PC.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not set(raw) & set(DEFAULT_CONFIG):
        raise ValueError("this is not a CNCBridge settings file")
    cfg = normalize_config(migrate(raw))  # a file from a newer program is refused, not half-read
    for key in SECRET_KEYS:
        try:
            secret = unprotect(cfg[key])
        except (OSError, ValueError):
            secret = ""
        cfg[key] = secret or current.get(key, "")
    cfg["setup_password"] = current.get("setup_password", "")  # importing or restoring never changes the password
    for slot, now in zip(cfg["manual_inputs"], current.get("manual_inputs") or []):
        slot["value"] = slot["value"] or now.get("value", "")  # a backup copy has none: the current ones stay
    return cfg


# ---------------------------------------------------------------- setup password
PASSWORD_ROUNDS = 200_000
MIN_PASSWORD = 12


def hash_password(password, salt=None, rounds=PASSWORD_ROUNDS):
    """'pbkdf2$<rounds>$<salt>$<hash>': the password itself is never stored."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    """True when the password matches a stored hash (and is not empty)."""
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2" or not password:
            return False
        again = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(again.hex(), digest)


def program_dir():
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def legacy_data_dir():
    """Where version 1.0 kept settings and history: 'data' beside the program."""
    return program_dir() / "data"


def _writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def default_data_dir():
    """
    Settings, history, send queue and logs. They are kept outside the program folder, so rebuilding,
    replacing or tidying up the program cannot delete them: %ProgramData%\\CNCBridge (shared by the
    window and a background task), else %LOCALAPPDATA%\\CNCBridge.
    """
    for root in (os.environ.get("PROGRAMDATA"), os.environ.get("LOCALAPPDATA")):
        if root and _writable(Path(root) / APP_NAME):
            return Path(root) / APP_NAME
    folder = Path.home() / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def has_data(folder):
    """True when a data folder holds settings or history worth keeping."""
    return any((Path(folder) / name).exists() for name in ("config.json", "ledger.db"))


def move_data(old, new, today=None):
    """
    Bring an old data folder over to the new location, once: copy everything except the lock file,
    then rename the old folder so that nothing is processed twice. Returns a sentence for the user,
    or None when there is nothing to move.
    """
    old, new = Path(old), Path(new)
    if not has_data(old) or has_data(new):
        return None
    if old.resolve() == new.resolve():
        return None
    shutil.copytree(old, new, dirs_exist_ok=True, ignore=shutil.ignore_patterns("instance.lock"))
    stamp = f"{today or datetime.now():%Y%m%d}"
    moved, n = old.with_name(f"{old.name}.moved-{stamp}"), 2
    while moved.exists():
        moved, n = old.with_name(f"{old.name}.moved-{stamp}-{n}"), n + 1
    try:
        old.rename(moved)
    except OSError as e:
        log.warning("Copied %s to %s but could not rename the old folder: %s", old, new, e)
        return (f"Your settings and history were copied from {old} to {new}. The old folder could not be "
                f"renamed ({e.strerror}); it is no longer used and can be deleted.")
    return (f"Your settings and history were moved from {old} to {new}, where a rebuild of the program "
            f"cannot delete them. The old folder is kept as {moved}.")
