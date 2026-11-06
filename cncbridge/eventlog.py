"""
The Windows Event Log (Application log), so that IT tools that already watch it can see CNCBridge.

A logging handler sends every WARNING and ERROR line there, and the few named events below, which the program
marks with extra={"event": <id>} in its log calls. The same text is written at most once every ten minutes, so
a fault that repeats every cycle does not flood the log.

The source "CNCBridge" is registered by the set-up program (it needs administrator rights). Without that the
events are still written, but Windows adds a note that it cannot find the description text.
"""
import ctypes
import logging
import os
import time
from ctypes import wintypes

SOURCE = "CNCBridge"
REPEAT_SECONDS = 600
MAX_TEXT = 30000                     # the Event Log refuses a string longer than about 31,800 characters

STARTED, STOPPED = 1000, 1001
SETTINGS_CHANGED, SETTINGS_OUTSIDE = 1100, 1101
ADMIN_LOGIN, ADMIN_LOGIN_FAILED, ADMIN_LOGOUT, ADMIN_PASSWORD_CHANGED = 1200, 1201, 1202, 1203
PROBLEM, RESOLVED = 2000, 2001
HEARTBEAT_FAILING = 2100
WARNING_EVENT, ERROR_EVENT = 3000, 3001   # any other WARNING / ERROR line

ERROR_TYPE, WARNING_TYPE, INFORMATION_TYPE = 1, 2, 4


class EventLog:
    """A connection to the Application log. write() never raises: a missing log must not stop the program."""

    def __init__(self, source=SOURCE):
        self.source = source
        self.handle = None
        self.failed = os.name != "nt"

    def _open(self):
        if self.handle or self.failed:
            return self.handle
        try:
            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            advapi.RegisterEventSourceW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            advapi.RegisterEventSourceW.restype = wintypes.HANDLE
            advapi.ReportEventW.argtypes = [wintypes.HANDLE, wintypes.WORD, wintypes.WORD, wintypes.DWORD,
                                            wintypes.LPVOID, wintypes.WORD, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.LPCWSTR), wintypes.LPVOID]
            advapi.ReportEventW.restype = wintypes.BOOL
            advapi.DeregisterEventSource.argtypes = [wintypes.HANDLE]
            advapi.DeregisterEventSource.restype = wintypes.BOOL
            self._advapi = advapi
            self.handle = advapi.RegisterEventSourceW(None, self.source)
        except (OSError, AttributeError):
            self.handle = None
        self.failed = not self.handle
        return self.handle

    def write(self, event_id, text, kind=INFORMATION_TYPE):
        if not self._open():
            return False
        strings = (wintypes.LPCWSTR * 1)(text[:MAX_TEXT])
        return bool(self._advapi.ReportEventW(self.handle, kind, 0, int(event_id), None, 1, 0, strings, None))

    def close(self):
        if self.handle:
            self._advapi.DeregisterEventSource(self.handle)
            self.handle = None


class EventLogHandler(logging.Handler):
    """WARNING and ERROR lines, and lines marked with an event number, go to the Event Log."""

    def __init__(self, writer=None, context=""):
        """writer(event_id, text, kind): replaced in tests. context: put before each text (the station's name)."""
        super().__init__(level=logging.INFO)
        self.log = None if writer else EventLog()
        self.writer = writer or self.log.write
        self.context = f"[{context}] " if context else ""
        self._last = {}

    def emit(self, record):
        try:
            event_id = getattr(record, "event", None)
            if event_id is None:
                if record.levelno < logging.WARNING:
                    return
                event_id = ERROR_EVENT if record.levelno >= logging.ERROR else WARNING_EVENT
            text = self.context + record.getMessage()
            now = time.time()
            key = (event_id, text)
            if now - self._last.get(key, -REPEAT_SECONDS) < REPEAT_SECONDS:
                return
            self._last[key] = now
            if len(self._last) > 500:
                self._last = {k: t for k, t in self._last.items() if now - t < REPEAT_SECONDS}
            kind = ERROR_TYPE if record.levelno >= logging.ERROR else WARNING_TYPE if record.levelno >= logging.WARNING \
                else INFORMATION_TYPE
            self.writer(event_id, text, kind)
        except Exception:  # logging must never raise into the program
            pass

    def close(self):
        if self.log:
            self.log.close()
        super().close()


def attach(logger, context=""):
    """Add the handler once to this logger; returns it (or None when the Event Log is switched off)."""
    if os.environ.get("CNCBRIDGE_NO_EVENTLOG") or os.name != "nt":
        return None
    for existing in logger.handlers:
        if isinstance(existing, EventLogHandler):
            return existing
    handler = EventLogHandler(context=context)
    logger.addHandler(handler)
    return handler
