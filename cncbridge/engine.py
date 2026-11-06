"""
CNCBridge engine.

For every machine file (CSV) that appears in the machine folder:

    wait until the machine has finished writing -> fingerprint (duplicate guard)
    -> read -> cut out up to 10 cell ranges -> add the 2 typed values
    -> render JSON / CSV / TXT -> queue it for each destination (shared folder, API link, FTP server)
    -> move the source into the dated back-up folder -> record it in the ledger
    -> deliver everything queued; what cannot be delivered now is retried every cycle

This file holds the Engine that runs that loop. The pieces it uses are in their own modules:

    csvread       read a machine file, cell ranges
    output        render JSON / CSV / TXT, check the settings (validate)
    ledger        the history database
    delivery      shared folder, API link, FTP server, and the tests for them
    housekeeping  queues, back-up drive, second chances, clean-up

Every name from those modules can still be reached as engine.<name>.

Only the Python standard library is used.
"""
import codecs  # noqa: F401
import contextlib  # noqa: F401
import csv  # noqa: F401
import ftplib  # noqa: F401
import hashlib  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import logging  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import sqlite3  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import urllib.error  # noqa: F401
import urllib.parse  # noqa: F401
import urllib.request  # noqa: F401
import uuid  # noqa: F401
from datetime import datetime, timedelta  # noqa: F401
from pathlib import Path  # noqa: F401

from . import audit, eventlog, heartbeat
from .alerts import Alerts
from .background import stop_requested
from .config import APP_NAME, OPERATOR_FILE, VERSION, load_config, normalize_config
from .ysup import materials as ysup_materials  # M2-materials
from .ysup import records as ysup_records  # M3-ysup-receiver
from .ysup.server import YsupServer  # M3-ysup-receiver
# Everything that used to live in this file is still reachable as engine.<name>.
from .csvread import (  # noqa: F401
    check_range, col_index, col_letter, decode, DELIMITERS, extract, extract_all, FileError, find_heading,
    find_tables, find_titles, is_wildcard, split_columns,
    format_range, is_heading, json_rows, machine_files, normalize_range, parse_range, parse_renames, priority,
    RANGE_RE, read_rows, split_patterns,
    resolve_range, sniff_delimiter, _header, _resolve_heading,
)
from .output import (  # noqa: F401
    as_number, build_output, manual_dict, NUMBER, parse_headers, read_output, render, validate, _aligned,
    _flat_csv, _same_folder,
)
from .ledger import (  # noqa: F401
    Ledger,
)
from .delivery import (  # noqa: F401
    DESTINATION_NAMES, destination_name,
    atomic_write, CONTENT_TYPES, copy_shared, DESTINATIONS, PERMANENT_FTP, PERMANENT_HTTP,
    Rejected, send_api, send_ftp, test_api, test_ftp, test_shared, _ftp_connect, _ftp_enter,
    _ftp_free_name, _ftp_quit, _header_safe, _multipart, _NoRedirect, _opener,
)
from .housekeeping import (  # noqa: F401
    ALLOW_ONCE, allow_once, clean_up, count_queued, count_rejected, count_waiting,
    CRITICAL_DISK_BYTES, DAY_FOLDER, disk_level, disk_space, has_marker, health, is_ready,
    find_output, LOW_DISK_BYTES, LOW_DISK_FRACTION, oldest_queued_age, OUTPUTS, reprocess, resend_rejected,
    sha256_file, take_allowance, _allowed, _kept_copy, _unique, _visible,
)

log = logging.getLogger("cncbridge")


class Engine:
    CLEAN_UP_EVERY = 3600  # seconds
    DISK_CHECK_EVERY = 60  # seconds

    def __init__(self, cfg, data_dir, config_path=None):
        self.cfg = normalize_config(cfg)  # may be replaced while running; each file uses the current one
        self.data = Path(data_dir)
        # In the background there is no window to hand over new settings, so the settings file is
        # read again whenever it changes.
        self.config_path = Path(config_path) if config_path else None
        self._config_mtime = self._settings_time()
        self.ledger = Ledger(self.data / "ledger.db")
        self.alerts = Alerts(self.data)
        self._check_settings_file()
        self._stop = threading.Event()
        self._last_clean_up = 0.0
        self._last_disk_check = 0.0
        self._waiting_marker = set()  # files seen without the complete-marker, by name
        self._reported = {}  # last problem logged per subject, so a standing fault is logged once, not every cycle
        self._since = {}     # when each standing fault was first seen, for alert_after_minutes
        self._last_beat = 0.0
        self._beat_thread = None
        self._beat_error = None   # why the last heartbeat could not be sent; set by its thread
        self._ysup_server = None  # M3-ysup-receiver
        self._ysup_settings = None  # M3-ysup-receiver

    def stop(self):
        self._stop.set()

    def _settings_time(self):
        """When the settings last changed: the settings file or the typed values, whichever is newer."""
        try:
            if not self.config_path:
                return None
            times = [self.config_path.stat().st_mtime]
            typed = self.config_path.with_name(OPERATOR_FILE)
            if typed.exists():
                times.append(typed.stat().st_mtime)
            return max(times)
        except OSError:
            return None

    def reload_settings(self):
        """Pick up settings saved by a window while this runs in the background."""
        mtime = self._settings_time()
        if mtime is None or mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        try:
            self.cfg = load_config(self.config_path)
        except (OSError, ValueError) as e:
            log.error("The changed settings could not be read, the previous ones stay in use: %s", e)
            return False
        log.info("Settings reloaded")
        self._check_settings_file()
        return True

    def _check_settings_file(self):
        """The settings file was edited outside CNCBridge (see audit.py): log it and tell people."""
        try:
            entry = audit.check_outside_edit(self.data)
        except Exception as e:  # the change log must never stop the processing
            log.debug("Could not check the settings against the change log: %s", e)
            return
        if entry:
            self.alerts.event(audit.outside_text(entry), self.cfg, title=f"{APP_NAME}: settings changed outside the program")

    def _check_disk(self):
        """A back-up drive that fills up stops everything: say so early, through the usual alerts."""
        if time.time() - self._last_disk_check < self.DISK_CHECK_EVERY:
            return
        self._last_disk_check = time.time()
        space = disk_space(self.cfg["backup_folder"])
        if space is None:
            return
        level = disk_level(*space)
        if level == "ok":
            self._report("disk", None)
        else:
            self._report("disk", f"The back-up drive is nearly full: {space[0] / 1024 ** 3:.1f} GB free of "
                                 f"{space[1] / 1024 ** 3:.0f} GB.",
                         logging.ERROR if level == "critical" else logging.WARNING)

    def outbox(self, kind):
        d = self.data / "outbox" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _report(self, subject, problem, level=logging.WARNING, event=None):
        """
        Log a fault when it appears or changes; pass problem=None when the subject is healthy again.
        A fault that lasts alert_after_minutes raises an alert, and its end is announced too.
        event: the Event Log number to log it under (see eventlog.py).
        """
        if problem is None:
            self._since.pop(subject, None)
            self.alerts.resolved(subject, self.cfg)
            return self._reported.pop(subject, None) is not None
        if self._reported.get(subject) != problem:
            self._reported[subject] = problem
            log.log(level, "%s", problem, extra={"event": event} if event else None)
        since = self._since.setdefault(subject, time.time())
        if time.time() - since >= self.cfg["alert_after_minutes"] * 60:
            self.alerts.problem(subject, problem, self.cfg)
        return False

    def _heartbeat(self, state=None):
        """
        Send the heartbeat (see heartbeat.py) when it is due. state=None: by the clock, and "ok" or "problem" by
        what is open; "stopped": now, once, as monitoring ends. Sending is done on a thread, so that a slow
        receiver never holds up the files; a failure is reported on the next cycle.
        """
        try:
            cfg = self.cfg
            if not cfg["heartbeat_enabled"]:
                self._beat_error = None
                self._report("heartbeat", None)
                return
            self._report("heartbeat", f"The heartbeat could not be sent: {self._beat_error}" if self._beat_error
                         else None, event=eventlog.HEARTBEAT_FAILING)
            if state is None and (time.time() - self._last_beat < cfg["heartbeat_minutes"] * 60
                                  or (self._beat_thread and self._beat_thread.is_alive())):
                return
            self._last_beat = time.time()
            problems = [dict(subject=k, **v) for k, v in dict(self.alerts.open).items()]
            payload = heartbeat.snapshot(cfg, self.data, self.ledger, state or ("problem" if problems else "ok"),
                                         problems)

            def work():
                try:
                    heartbeat.send(cfg, payload)
                    self._beat_error = None
                except Exception as e:
                    self._beat_error = str(e)

            thread = threading.Thread(target=work, name="heartbeat", daemon=True)
            thread.start()
            if state == "stopped":
                thread.join(10)
            else:
                self._beat_thread = thread
        except Exception as e:  # the heartbeat must never stop the processing
            log.debug("Heartbeat: %s", e)

    # ---- delivery
    def drain_outbox(self):
        cfg = self.cfg
        for kind, (fn, enabled) in DESTINATIONS.items():
            if not enabled(cfg):
                continue
            files = sorted(_visible(self.outbox(kind)), key=lambda p: (p.stat().st_mtime, p.name))
            for f in files:
                try:
                    fn(f, cfg)
                except Rejected as e:
                    shutil.move(str(f), str(_unique(self.data / "rejected" / kind, f.name)))
                    self.ledger.set_delivery(f.name, kind, f"rejected ({e})")
                    log.error("The %s refused %s: %s - moved to data/rejected", destination_name(kind), f.name, e)
                    self.alerts.event(f"The {destination_name(kind)} refused {f.name} ({e}). It was set aside in "
                                      f"'rejected' in the data folder; the next files continue.", cfg)
                    continue
                except Exception as e:
                    # destination is down: keep the order and try again next cycle
                    self._report(kind, f"Delivery to the {destination_name(kind)} is not possible, "
                                       f"{len(files)} file(s) waiting: {e}")
                    break
                f.unlink()
                self.ledger.set_delivery(f.name, kind, "ok")
                if self._report(kind, None):
                    log.info("Delivery to the %s is working again", destination_name(kind))
                log.info("Delivered to the %s: %s", destination_name(kind), f.name)

    # ---- pipeline
    def process_file(self, src):
        cfg, src = self.cfg, Path(src)
        now = datetime.now()
        sha, size = sha256_file(src), src.stat().st_size
        backup = Path(cfg["backup_folder"])

        if cfg["skip_duplicates"] and self.ledger.seen(sha) and not take_allowance(self.data, sha):
            shutil.move(str(src), str(_unique(backup / "_duplicates", src.name)))
            self.ledger.add(source=src.name, sha256=sha, size=size, status="duplicate",
                            message="identical content was already processed")
            log.warning("Duplicate skipped: %s", src.name)
            return "duplicate"

        try:
            rows = read_rows(src, cfg["encoding"], cfg["delimiter"])
        except csv.Error as e:
            raise FileError(f"not a readable CSV ({e})") from None
        text, blocks, warnings = build_output(src.name, rows, cfg, now)
        for w in warnings:
            log.warning("%s: %s", src.name, w)

        kinds = [k for k, (_, enabled) in DESTINATIONS.items() if enabled(cfg)]
        day = backup / f"{now:%Y-%m-%d}"
        day.mkdir(parents=True, exist_ok=True)  # if the back-up drive is away, fail before anything is queued
        stem, name, n = f"{src.stem}_{now:%Y%m%d_%H%M%S}", "", 0
        while not name or any((self.outbox(k) / name).exists() for k in kinds):
            name = f"{stem}{f'_{n}' if n else ''}.{cfg['output_format']}"
            n += 1
        queued = []
        try:
            for k in kinds:
                queued.append(self.outbox(k) / name)
                atomic_write(queued[-1], text)
            shutil.move(str(src), str(_unique(day, src.name)))
        except OSError:
            for q in queued:  # the source stays where it is and is processed again, so nothing may go out twice
                q.unlink(missing_ok=True)
            raise
        try:  # a copy to look at later (History > View output); it goes with its day folder at clean-up
            (day / OUTPUTS).mkdir(exist_ok=True)
            atomic_write(day / OUTPUTS / name, text)
        except OSError as e:
            log.warning("Could not keep a copy of %s in the back-up folder: %s", name, e)
        self.ledger.add(processed_at=now.isoformat(timespec="seconds"), source=src.name, sha256=sha, size=size,
                        status="ok", output=name, message="; ".join(warnings),
                        **{k: "queued" if k in kinds else "off" for k in DESTINATIONS})
        log.info("Processed %s -> %s (%s); source moved to %s", src.name, name,
                 ", ".join(b["name"] for b in blocks), day)
        return "ok"

    def _quarantine(self, src, reason):
        log.error("Failed %s: %s", src.name, reason)
        try:
            sha, size = sha256_file(src), src.stat().st_size
            shutil.move(str(src), str(_unique(Path(self.cfg["backup_folder"]) / "_failed", src.name)))
            self.ledger.add(source=src.name, sha256=sha, size=size, status="failed", message=str(reason))
            self.alerts.event(f"Machine file {src.name} could not be used ({reason}). It was moved to "
                              f"back-up\\_failed.", self.cfg)
        except OSError as e:
            self._report(src.name, f"Could not move {src.name} to back-up/_failed: {e}", logging.ERROR)

    def pending_files(self):
        cfg = self.cfg
        files = [f for f in machine_files(cfg["source_folder"], cfg["file_pattern"])
                 if not f.name.startswith(".") and is_ready(f, cfg["stable_seconds"])]
        if cfg["complete_marker"]:
            files = self._with_marker(files, cfg["complete_marker"])
        elif self._waiting_marker:
            self._with_marker([], "")  # the setting was switched off: nothing waits for a marker any more
        # the process-first patterns go ahead, in their order; then the rest, oldest first
        return sorted(files, key=lambda p: (priority(p.name, cfg["priority_patterns"]), p.stat().st_mtime, p.name))

    def _with_marker(self, files, marker):
        """
        Only files that contain the marker are taken. A file without it is left alone and reported (and
        alerted, when it lasts) so it is not forgotten: the machine may still be writing it, or the
        marker setting may be wrong.
        """
        ready, waiting = [], set()
        for f in files:
            if has_marker(f, marker, self.cfg["encoding"]):
                ready.append(f)
                self._report(f"marker:{f.name}", None)
            else:
                waiting.add(f.name)
                self._report(f"marker:{f.name}", f"{f.name} does not contain '{marker}' yet, so it is not "
                                                  f"taken. Is the machine still writing it, or is the marker "
                                                  f"setting wrong?")
        for name in self._waiting_marker - waiting:  # taken in the meantime, or no longer in the folder
            self._report(f"marker:{name}", None)
        self._waiting_marker = waiting
        return ready

    def scan_once(self):
        """One cycle: process every finished file in the machine folder, then deliver what is queued."""
        ysup_materials.import_pending(self.cfg, self.data, self.alerts)  # M2-materials
        problems = validate(self.cfg)
        if problems:
            self._report("settings", "Settings are not complete: " + " ".join(problems), logging.ERROR)
            return 0
        self._report("settings", None)
        done = 0
        for f in self.pending_files():
            if self._stop.is_set():
                break
            try:
                if self.process_file(f) == "ok":
                    done += 1
                self._report(f.name, None)
            except FileError as e:
                self._quarantine(f, e)
            except Exception as e:  # folder or disk trouble, not the file's fault: leave it and retry
                self._report(f.name, f"Could not process {f.name} yet, will retry: {e}", logging.ERROR)
        self.drain_outbox()
        self._check_disk()
        if time.time() - self._last_clean_up >= self.CLEAN_UP_EVERY:
            self._last_clean_up = time.time()
            clean_up(self.cfg, self.ledger)
            ysup_records.clean_up(self.data, self.cfg["keep_history_days"])  # M3-ysup-receiver
        return done

    def _sync_ysup_listener(self):  # M3-ysup-receiver
        wanted = (bool(self.cfg.get("ysup_enabled")), self.cfg.get("ysup_listen_address", "127.0.0.1"),
                  int(self.cfg.get("ysup_port", 3001)), self.cfg.get("ysup_basic_username", ""),
                  self.cfg.get("ysup_basic_password", ""), self.cfg.get("ysup_api_token", ""))
        listener_alive = bool(self._ysup_server and self._ysup_server.thread and self._ysup_server.thread.is_alive())
        if wanted == self._ysup_settings and (not wanted[0] or listener_alive):
            return
        if self._ysup_server is not None:
            self._ysup_server.stop()
            self._ysup_server = None
        self._ysup_settings = wanted
        if wanted[0]:
            self._ysup_server = YsupServer(self.cfg, self.data, self._report)
            if not self._ysup_server.start():
                self._ysup_server = None

    def run_forever(self):
        self._stop.clear()
        stop_requested(self.data)  # an old request must not stop this new run
        log.info("Monitoring %s", self.cfg["source_folder"], extra={"event": eventlog.STARTED})
        try:
            self._sync_ysup_listener()  # M3-ysup-receiver
            while not self._stop.is_set():
                if stop_requested(self.data):
                    log.info("Stop requested from the window")
                    break
                try:
                    self.reload_settings()
                    self._sync_ysup_listener()  # M3-ysup-receiver
                    self.scan_once()
                    self._heartbeat()
                except Exception as e:  # never let the watcher die on the shop floor
                    self._report("scan", f"Scan error: {e}", logging.ERROR)
                self._stop.wait(self.cfg["poll_seconds"])
        finally:
            if self._ysup_server is not None:  # M3-ysup-receiver
                self._ysup_server.stop()  # M3-ysup-receiver
                self._ysup_server = None  # M3-ysup-receiver
        log.info("Monitoring stopped", extra={"event": eventlog.STOPPED})
        self._heartbeat("stopped")
        self.alerts.reset()
        log.info("Monitoring stopped")
