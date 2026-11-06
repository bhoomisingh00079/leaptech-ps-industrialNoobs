"""The Settings history window (View menu): who changed which setting, and when. See cncbridge/audit.py."""
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from .. import alerts, audit, theme
from ..config import APP_NAME
from ..i18n import N_, tr


class SettingsHistoryMixin:
    # N_ marks the texts for translation; they are translated where they are shown (this is read at import)
    HISTORY_COLUMNS = (("when", N_("When"), 150), ("who", N_("Who"), 230), ("how", N_("How"), 120),
                       ("what", N_("What changed"), 620))
    HOW_TEXT = {"save": N_("Saved"), "import": N_("Imported"), "restore": N_("Restored"), "password": N_("Password"),
                "operator": N_("Typed values"), "access": N_("Admin access"),
                "materials": N_("Materials import"),  # M2-materials
                "outside": N_("Outside CNCBridge"), "baseline": N_("Starting point")}
    HISTORY_LIMIT = 2000

    def _build_settings_history(self):
        win = self.history_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        win.title(tr("{APP_NAME} settings history", APP_NAME=APP_NAME))
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(self.px(1180 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(560 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(700), width), min(self.px(360), height))
        frame = ttk.Frame(win, padding=(14, 12, 14, 0))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=tr("Every change to the settings: when, by which Windows user, and what changed. "
                              "Passwords and keys are never shown, only that they changed."),
                  style="PageHint.TLabel").pack(anchor="w", pady=(0, 8))
        search = ttk.Frame(frame)
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text=tr("Filter:")).pack(side="left")
        self.history_filter = tk.StringVar(self)
        self.history_filter_entry = ttk.Entry(search, textvariable=self.history_filter, width=40)
        self.history_filter_entry.pack(side="left", padx=8)
        self.history_filter.trace_add("write", lambda *_: self._fill_settings_history())
        area = ttk.Frame(frame, style="Card.TFrame", padding=1)
        area.pack(fill="both", expand=True)
        self.history_tree = ttk.Treeview(area, columns=[c[0] for c in self.HISTORY_COLUMNS], show="headings",
                                         selectmode="browse")
        for key, title, column_width in self.HISTORY_COLUMNS:
            self.history_tree.heading(key, text=tr(title), anchor="w")
            self.history_tree.column(key, width=self.px(column_width), anchor="w", stretch=key == "what")
        self.history_tree.tag_configure("outside", foreground=theme.ERROR)
        self.history_tree.tag_configure("baseline", foreground=theme.MUTED)
        ys = ttk.Scrollbar(area, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", lambda event: self._show_history_detail())
        self.history_detail = tk.Text(frame, height=7, wrap="word", state="disabled", relief="flat", borderwidth=0,
                                      font=(self._font_mono, self.L.text_mono), background=theme.SURFACE,
                                      foreground=theme.TEXT, highlightthickness=0, padx=9, pady=7)
        self.history_detail.pack(fill="x", pady=(8, 0))
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text=tr("Close"), command=win.withdraw).pack(side="right")
        ttk.Button(bar, text=tr("Refresh"), command=self.refresh_settings_history).pack(side="right", padx=8)
        self.history_entries = {}

    def open_settings_history(self):
        if not hasattr(self, "history_win"):
            self._build_settings_history()
        self.history_win.deiconify()
        self.history_win.lift()
        if self.theme.dark:
            theme.dark_title_bar(self.history_win)
        self.refresh_settings_history()

    def refresh_settings_history(self):
        self._history_all = audit.read(self.data_dir, self.HISTORY_LIMIT)
        self._fill_settings_history()

    def _fill_settings_history(self):
        tree = self.history_tree
        words = self.history_filter.get().lower().split()
        tree.delete(*tree.get_children())
        self.history_entries = {}
        for number, entry in enumerate(self._history_all):
            when = entry.get("time", "")
            try:
                when = f"{datetime.fromisoformat(when):%Y-%m-%d %H:%M:%S}"
            except ValueError:
                pass
            how = tr(self.HOW_TEXT.get(entry.get("how"), entry.get("how", "")))
            row = [when, entry.get("user", ""), how, audit.summary(entry)]
            if words and not all(w in " ".join(row).lower() for w in words):
                continue
            iid = str(number)
            self.history_entries[iid] = entry
            tree.insert("", "end", iid=iid, values=row,
                        tags=[entry["how"]] if entry.get("how") in ("outside", "baseline") else [])
        self._show_history_detail()

    def _show_history_detail(self):
        selection = self.history_tree.selection()
        entry = self.history_entries.get(selection[0]) if selection else None
        lines = []
        if entry:
            lines.append(tr("{user} on {computer}", user=entry.get("user", ""), computer=entry.get("computer", ""))
                         + (tr(" (administrator)") if entry.get("elevated") else ""))
            if entry.get("detail"):
                lines.append(entry["detail"])
            lines += [f"{c['setting']}: {c['old']} → {c['new']}" for c in entry.get("changes", [])]
        text = self.history_detail
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", chr(10).join(lines))
        text.configure(state="disabled")

    def _check_settings_edit(self):
        """When the window opens: was the settings file edited outside CNCBridge since the last save?"""
        try:
            entry = audit.check_outside_edit(self.data_dir)
        except Exception:  # the change log must never stop the window from opening
            return
        if entry:
            self.status_msg.set(tr("The settings file was changed outside CNCBridge. See View > Settings history."))
            alerts.announce(self.cfg, f"{APP_NAME}: settings changed outside the program", audit.outside_text(entry))
