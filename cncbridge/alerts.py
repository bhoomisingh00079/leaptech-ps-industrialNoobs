"""
Alerts: tell a person when CNCBridge needs attention, instead of only writing it to the log.

    problem   a fault that has lasted alert_after_minutes (a destination down, a stuck file,
              incomplete settings). Announced once; a "working again" message follows when it clears.
    event     something that needs a look but does not last (a file set aside). Announced each time.

Channels, each switched on in the settings: a Windows notification on this PC (on by default), a
webhook (a JSON POST, which Teams, Slack and most systems accept), and email. Sending happens on a
background thread, so a slow mail server never holds up the processing of files.

The open problems are also written to alerts.json in the data folder, so the window can show them in a
banner, whether the processing runs in the window or in the background.
"""
import ctypes
import json
import logging
import os
import queue
import smtplib
import ssl
import threading
import time
import urllib.request
from ctypes import wintypes
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from . import eventlog
from .config import APP_NAME, VERSION

log = logging.getLogger("cncbridge")
STATE_FILE = "alerts.json"


# =============================================================== channels
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wintypes.DWORD), ("guidItem", _GUID),
                ("hBalloonIcon", wintypes.HICON)]


def _show_notification(title, text, kind, seconds):
    """Runs on its own thread: Windows ties the notification icon to a window of the thread that shows it."""
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
    hwnd = user32.CreateWindowExW(0, "STATIC", f"{APP_NAME} alert", 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        log.warning("Could not show a Windows notification (no window)")
        return
    nid = _NOTIFYICONDATAW()
    nid.cbSize, nid.hWnd, nid.uID = ctypes.sizeof(nid), hwnd, 1
    nid.uFlags = 0x2 | 0x4 | 0x10                                        # icon, tooltip, message
    nid.hIcon = user32.LoadIconW(None, ctypes.c_void_p(32515 if kind != "info" else 32516))
    nid.szTip, nid.szInfo, nid.szInfoTitle = APP_NAME, text[:255], title[:63]
    nid.dwInfoFlags = 0x1 if kind == "info" else 0x2                     # information or warning
    try:
        if not shell32.Shell_NotifyIconW(0, ctypes.byref(nid)):
            log.warning("Windows did not accept the notification")
            return
        time.sleep(seconds)  # the icon has to stay while the message is on screen
    finally:
        shell32.Shell_NotifyIconW(2, ctypes.byref(nid))
        user32.DestroyWindow(hwnd)


def notify_windows(title, text, kind="warning", seconds=15):
    """Show a notification on this PC. Returns at once; the notification goes away by itself."""
    if os.name != "nt":
        raise OSError("Windows notifications are only available on Windows")
    threading.Thread(target=_show_notification, args=(title, text, kind, seconds), name="notification").start()


def post_webhook(url, payload, timeout=15):
    from .delivery import _opener  # the opener that refuses redirects, so a POST is never silently dropped
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{VERSION}"})
    with _opener.open(req, timeout=timeout) as resp:
        return resp.status


def send_email(cfg, subject, body, timeout=20):
    recipients = [a.strip() for a in cfg["alert_email_to"].split(",") if a.strip()]
    if not cfg["alert_email_server"] or not recipients:
        raise ValueError("enter the mail server and at least one address to send to")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, ", ".join(recipients)
    msg["From"] = cfg["alert_email_from"] or cfg["alert_email_user"] or f"{APP_NAME.lower()}@localhost"
    msg.set_content(body)
    port = cfg["alert_email_port"]
    smtp = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP  # 465 means an encrypted connection from the start
    with smtp(cfg["alert_email_server"], port, timeout=timeout) as server:
        if port != 465 and cfg["alert_email_starttls"]:
            server.starttls(context=ssl.create_default_context())
        if cfg["alert_email_user"]:
            server.login(cfg["alert_email_user"], cfg["alert_email_password"])
        server.send_message(msg)


# Looked up when a message is sent, so tests can replace a channel.
SENDERS = {"notify": notify_windows, "webhook": post_webhook, "email": send_email}


def enabled_channels(cfg):
    return [name for name, on in (("notify", cfg["alert_notify"]),
                                  ("webhook", cfg["alert_webhook_enabled"] and cfg["alert_webhook_url"]),
                                  ("email", cfg["alert_email_enabled"])) if on]


def deliver(cfg, state, title, text):
    """Send one message through every channel switched on. Returns [(channel, error or None)]."""
    station = cfg["station_name"] or APP_NAME
    when = datetime.now().isoformat(timespec="seconds")
    results = []
    for channel in enabled_channels(cfg):
        try:
            if channel == "notify":
                SENDERS["notify"](f"{title} - {station}", text, "info" if state in ("resolved", "test") else "warning")
            elif channel == "webhook":
                SENDERS["webhook"](cfg["alert_webhook_url"], {
                    "text": f"{title} ({station}): {text}", "app": APP_NAME, "station": station,
                    "state": state, "title": title, "message": text, "time": when})
            else:
                SENDERS["email"](cfg, f"{title} - {station}",
                                 f"{text}\n\nStation: {station}\nTime: {when}\nSent by {APP_NAME} {VERSION}.")
            results.append((channel, None))
        except Exception as e:
            log.warning("Could not send the alert by %s: %s", channel, e)
            results.append((channel, str(e)))
    return results


def read_state(data_dir):
    """The open problems written by the processing: {subject: {"text": ..., "since": ...}}."""
    try:
        return json.loads((Path(data_dir) / STATE_FILE).read_text(encoding="utf-8")).get("open", {})
    except (OSError, ValueError):
        return {}


class Alerts:
    def __init__(self, data_dir, clear=True):
        self.state_file = Path(data_dir) / STATE_FILE
        self.open = read_state(data_dir) if not clear else {}  # subject -> {"text", "since"}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = None
        if clear:
            self._save()         # a previous run may have left problems that are no longer watched

    def problem(self, subject, text, cfg):
        with self._lock:
            if subject in self.open:
                return
            self.open[subject] = {"text": text, "since": datetime.now().isoformat(timespec="seconds")}
            self._save()
        log.warning("Alert raised: %s", text, extra={"event": eventlog.PROBLEM})
        self._send(cfg, "problem", f"{APP_NAME} needs attention", text)

    def resolved(self, subject, cfg):
        with self._lock:
            item = self.open.pop(subject, None)
            if item is None:
                return
            self._save()
        log.info("Alert resolved: %s", item["text"], extra={"event": eventlog.RESOLVED})
        self._send(cfg, "resolved", f"{APP_NAME} is working again", f"Resolved: {item['text']}")

    def event(self, text, cfg, title=None):
        self._send(cfg, "event", title or f"{APP_NAME}: a file was set aside", text)

    def reset(self):
        """Monitoring stopped: nothing is watched any more, so nothing is open."""
        with self._lock:
            self.open.clear()
            self._save()

    def flush(self, timeout=60):
        """Wait until the queued messages have been sent (used before a one-off run exits)."""
        deadline = time.time() + timeout
        while self._queue.unfinished_tasks and time.time() < deadline:
            time.sleep(0.1)

    def _send(self, cfg, state, title, text):
        if not enabled_channels(cfg):
            return
        self._queue.put((dict(cfg), state, title, text))
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="alerts", daemon=True)
            self._worker.start()

    def _work(self):
        while True:
            try:
                item = self._queue.get(timeout=30)
            except queue.Empty:
                return
            try:
                deliver(*item)
            finally:
                self._queue.task_done()

    def _save(self):
        try:
            tmp = self.state_file.with_name(STATE_FILE + ".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"open": self.open}, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError as e:
            log.debug("Could not write %s: %s", self.state_file, e)


def announce(cfg, title, text):
    """Send one message now, on its own thread, for a window that has no alert queue of its own."""
    if enabled_channels(cfg):
        threading.Thread(target=deliver, args=(dict(cfg), "event", title, text), name="alert", daemon=True).start()


def send_test(cfg):
    """Send a test message through every channel switched on, now. Returns a sentence per channel."""
    channels = enabled_channels(cfg)
    if not channels:
        return "No alert channel is switched on."
    names = {"notify": "Windows notification", "webhook": "Webhook", "email": "Email"}
    results = deliver(cfg, "test", f"{APP_NAME} test alert", "This is a test. Alerts reach you this way.")
    return "\n".join(f"{names[c]}: {'sent' if e is None else 'failed - ' + e}" for c, e in results)
