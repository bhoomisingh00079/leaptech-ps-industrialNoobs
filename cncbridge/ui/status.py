"""Live status: the statistics thread and the periodic refresh of the window."""
import queue
import re
import time

from .. import alerts, background, engine, theme
from .common import log
from ..i18n import tr


class StatusMixin:
    # ================================================================ live status
    def _stats_loop(self):
        """Runs on its own thread so a slow network folder can never freeze the window."""
        while not self.closing.is_set():
            try:
                cfg = self.cfg
                stats = self.ledger.stats()
                queued = engine.count_queued(self.data_dir)
                self.snapshot = {
                    "ok": stats.get("ok", 0), "failed": stats.get("failed", 0),
                    "waiting": engine.count_waiting(cfg) if cfg["source_folder"] else 0,
                    "queued": sum(queued.values()), "queued_kinds": queued,
                    "rejected": engine.count_rejected(self.data_dir),
                    "history": self.ledger.recent(200),
                    "alerts": alerts.read_state(self.data_dir),
                    "health": engine.health(cfg, self.data_dir, self.ledger)}
                if self.connected:
                    self.bg_running = background.is_running(self.data_dir)
                    self._read_background_log()
            except Exception as e:
                log.debug("status refresh failed: %s", e)
            self.closing.wait(1.5)

    _LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+)\s+(.*)$")

    def _read_background_log(self):
        """Show the background process's new log lines in the Log tab."""
        path = self.data_dir / "logs" / "cncbridge.log"
        try:
            size = path.stat().st_size
        except OSError:
            return
        if self._log_pos is None or size < self._log_pos:  # first look, or the log was rotated
            self._log_pos = size if self._log_pos is None else 0
            if self._log_pos:
                return
        with open(path, "rb") as f:
            f.seek(self._log_pos)
            chunk = f.read()
        self._log_pos += len(chunk)
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            m = self._LOG_LINE.match(line)
            if m:
                self.log_q.put((m.group(2), f"{m.group(1)}  {m.group(2):<7}  [background] {m.group(3)}"))

    def _tick(self):
        self.check_admin_session()
        while True:
            try:
                self.ui_q.get_nowait()()
            except queue.Empty:
                break
        lines = []
        while len(lines) < 500:
            try:
                lines.append(self.log_q.get_nowait())
            except queue.Empty:
                break
        if lines:
            at_end = self.log_text.yview()[1] > 0.999
            self.log_text.configure(state="normal")
            for level, line in lines:
                self.log_text.insert("end", line + "\n", level)
            if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
                self.log_text.delete("1.0", "1000.0")
            self.log_text.configure(state="disabled")
            self.log_empty.place_forget()
            if at_end:
                self.log_text.see("end")

        running = self.processing
        if self.connected:
            text, colour = (tr("In background"), theme.SUCCESS) if running else (tr("Background off"), theme.IDLE)
            if self.take_over_when_stopped and not running:
                self._become_normal()
        else:
            text, colour = (tr("Monitoring"), theme.SUCCESS) if running else ("Working...", theme.WARNING) if self.busy else (
                tr("Stopped"), theme.IDLE)
        self.state_label.configure(text=text, width=max(11, len(text)))
        self.state_label.configure(foreground=colour)
        self.state_dot.configure(fg=colour)
        if self.L.page_headers:
            problem = running and bool((self.snapshot or {}).get("alerts"))
            self._update_pill(text + ("  ·  " + tr("needs attention") if problem else ""),
                              "warn" if problem else "ok" if running else "idle")
            self._swap_start_stop(running)
        if self.sidebar is not None:
            self._update_sidebar_state(text, colour)
        idle = not running and not self.busy
        self.start_btn.configure(state="normal" if idle else "disabled")
        self.once_btn.configure(state="normal" if idle and not self.connected else "disabled")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._set_menu("start", idle)
        self._set_menu("process_now", idle and not self.connected)
        self._set_menu("stop", running)
        if not running and self.status_msg.get() in self._stopping_notes():
            self.status_msg.set(tr("Monitoring stopped."))

        if self.folder_notes and time.monotonic() - getattr(self, "_folders_checked", 0) > 5:
            self._folders_checked = time.monotonic()  # machine files come and go: keep 'n waiting' current
            self._check_folders()
        if time.monotonic() - self._shift_checked > 15:  # cheap, but once every few seconds is plenty
            self._shift_checked = time.monotonic()
            self._check_shift()

        snap = self.snapshot
        if snap:
            self._show_alerts(snap.get("alerts") or {})
            self._show_health(snap.get("health") or {})
            for key, label in self.counts.items():
                colour = (theme.ERROR if snap[key] else theme.TEXT) if key in ("failed", "rejected") else (
                    theme.WARNING if snap[key] else theme.TEXT) if key == "queued" else (
                    theme.SUCCESS if snap[key] else theme.TEXT) if key == "ok" else theme.ACCENT if snap[key] else theme.TEXT
                label.configure(text=str(snap[key]), foreground=colour)
            if snap["history"] != self.shown_history:
                self.shown_history = snap["history"]
                selected = set(self.history.selection())
                self.history.delete(*self.history.get_children())
                for row in self.shown_history:
                    pending = "queued" in (row["shared"], row["api"], row["ftp"]) or any(
                        "rejected" in (row[k] or "") for k in ("api", "ftp"))
                    tag = row["status"] if row["status"] != "ok" else "pending" if pending else ""
                    self.history.insert("", "end", iid=str(row["id"]), tags=(tag,), values=[
                        (row["processed_at"] or "").replace("T", "  "), row["source"], self._result_text(row["status"]),
                        row["output"] or "", self._delivery_text(row["shared"]), self._delivery_text(row["api"]),
                        self._delivery_text(row["ftp"]), row["message"] or ""])
                keep = [iid for iid in selected if self.history.exists(iid)]
                if keep:
                    self.history.selection_set(keep)
                self._update_history_buttons()
                if self.shown_history:
                    self.history_empty.place_forget()
                else:
                    self.history_empty.place(relx=0.5, rely=0.5, anchor="center")
            if self.L.monitor_page:
                self._show_monitor(snap)
        self._tick_job = self.after(300, self._tick)

    @staticmethod
    def _ago(seconds):
        """'just now', '5 min ago', '3 h ago', '2 days ago'."""
        if seconds < 60:
            return tr("just now")
        if seconds < 3600:
            return tr("{n} min ago", n=int(seconds // 60))
        if seconds < 86400:
            return tr("{n} h ago", n=int(seconds // 3600))
        return tr("{n} days ago", n=int(seconds // 86400))

    @staticmethod
    def _span(seconds):
        """'5 min', '3 h', '2 days'."""
        if seconds < 60:
            return tr("under a minute")
        if seconds < 3600:
            return tr("{n} min", n=int(seconds // 60))
        if seconds < 86400:
            return f"{int(seconds // 3600)} h"
        return tr("{n} days", n=int(seconds // 86400))

    def _show_health(self, h):
        """The facts line under the counters; an item turns amber or red when it needs attention."""
        limit = self.cfg["alert_after_minutes"] * 60
        parts = {}
        last, age = h.get("last_file"), h.get("last_file_age")
        parts["last"] = (tr("Last file {time} ({ago})", time=last[11:16], ago=self._ago(age)) if last and age is not None else tr("No files yet"),
                         theme.MUTED)
        queued = h.get("oldest_queued_age")
        if queued is None:
            parts["queue"] = (tr("Queue empty"), theme.MUTED)
        else:
            parts["queue"] = (tr("Oldest in queue: {span}", span=self._span(queued)),
                              theme.ERROR if limit and queued > 3 * limit else theme.WARNING if limit and queued > limit
                              else theme.MUTED)
        free, level = h.get("backup_free"), h.get("disk_level")
        if free is not None:
            parts["disk"] = (tr("Back-up drive {free:.1f} GB free", free=free / 1024 ** 3),
                             theme.ERROR if level == "critical" else theme.WARNING if level == "low" else theme.MUTED)
        if h.get("source_reachable") is False:
            parts["source"] = (tr("Machine folder not reachable"), theme.ERROR)
        shown = False
        for key, label in self.health_labels.items():
            label.pack_forget()  # packed again in order, so the items never swap places
            if key in parts:
                text, colour = parts[key]
                label.configure(text=("   ·   " if shown else "") + text, foreground=colour)
                label.pack(side="left")
                shown = True

    def _show_alerts(self, open_alerts):
        """A red banner under the counters while a problem is open; gone when it is resolved."""
        if self.L.page_headers:
            return self._show_problem_banner(open_alerts, (self.snapshot or {}).get("queued_kinds", {}))
        if open_alerts:
            lines = ["⚠  " + tr("{problem}  (since {time})", problem=item['text'], time=item['since'][11:16]) for item in open_alerts.values()]
            self.alert_banner.configure(text="\n".join(lines), wraplength=max(self.winfo_width() - 40, 400))
            if not self.alert_banner_shown:
                self.alert_banner.pack(fill="x", after=self.header_sep)
                self.alert_banner_shown = True
        elif self.alert_banner_shown:
            self.alert_banner.pack_forget()
            self.alert_banner_shown = False
