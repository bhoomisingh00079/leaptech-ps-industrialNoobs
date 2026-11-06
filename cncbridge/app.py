"""CNCBridge main window (Tkinter). The engine runs on a worker thread; the window only reads snapshots.

The window is put together from the pieces in cncbridge/ui/: each is a mixin holding one part of it (the menus,
the Options window, the Pick tab, ...). This module keeps the constructor, which builds them in order, and run().
The names the pieces use are imported here too, so that cncbridge.app still offers all of them.
"""
import ctypes
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import alerts, background, config, engine, i18n, permissions, shifts, theme
from . import help as help_text
from .config import (APP_NAME, FORMATS, MAX_MANUAL, MAX_SELECTIONS, VERSION, export_settings, import_settings,
                     load_config, normalize_config, save_config)
from .font_registration import register_bundled_fonts
from .grid import Grid
from .tooltip import NotebookTabTooltip, Tooltip
from .ui.common import (APPEARANCE_KEYS, BODY_LABELS, DEFAULT_FONT_LABEL, DELIMITER_CHOICES, ENCODINGS,  # noqa: F401
                        FONT_SIZE_LABELS, PREVIEW_LIMIT, UI_SCALE_LABELS, QueueHandler, log)
from .ui.dialogs import ShiftDialog  # noqa: F401
from .ui.layout import LayoutMixin
from .ui.form import FormMixin
from .ui.menus import MenusMixin
from .ui.setup_tab import SetupTabMixin
from .ui.options import OptionsMixin
from .ui.access import AccessMixin
from .ui.pick_tab import PickTabMixin
from .ui.history_tab import HistoryTabMixin
from .ui.windows import WindowsMixin
from .ui.tooltips import TooltipsMixin
from .ui.control import ControlMixin
from .ui.stations_window import StationsMixin
from .ui.audit_window import SettingsHistoryMixin
from .ui.cards import restyle_cards
from .ui.checklist import ChecklistMixin
from .ui.monitor import MonitorMixin
from .ui.output_window import OutputWindowMixin
from .ui.pageheader import PageHeaderMixin
from .ui.sidebar import SidebarMixin
from .ui.status import StatusMixin


class App(LayoutMixin,
          FormMixin,
          MenusMixin,
          SetupTabMixin,
          OptionsMixin,
          AccessMixin,
          PickTabMixin,
          HistoryTabMixin,
          WindowsMixin,
          TooltipsMixin,
          ControlMixin,
          StatusMixin,
          StationsMixin,
          SettingsHistoryMixin,
          ChecklistMixin,
          SidebarMixin,
          MonitorMixin,
          PageHeaderMixin,
          OutputWindowMixin,
          tk.Tk):
    def __init__(self, data_dir, notice=None, lock=None, connected=False, resume=False):
        """
        resume: start monitoring at once (the window was restarted while monitoring).
        lock: the data folder's single-copy lock, held while this window processes files itself.
        connected: background processing is set up or running; this window shows and controls it.
        """
        self.data_dir = Path(data_dir)
        self.cfg_path = self.data_dir / "config.json"
        self.cfg = load_config(self.cfg_path)
        # the look and the language are chosen once, before any widget exists
        self.theme = theme.use(self.cfg["theme"], self.cfg["color_mode"])
        self.language = i18n.use(self.cfg["language"])
        self.appearance_in_use = dict({k: self.cfg[k] for k in APPEARANCE_KEYS}, theme=self.theme.key,
                                      language=self.language)
        self.bundled_fonts = register_bundled_fonts() if self.theme.bundled_fonts else {}
        super().__init__()
        # Window scale enlarges everything; Text size only the text. Fonts are in points, so Tk's scaling carries both.
        dpi_scale = self.winfo_fpixels("1i") / 96
        window_scale, self.text_scale = self.cfg["ui_scale"] / 100, self.cfg["font_size"] / 100
        if window_scale != 1 or self.text_scale != 1:
            self.tk.call("tk", "scaling", float(self.tk.call("tk", "scaling")) * window_scale * self.text_scale)
        self.lock, self.connected = lock, connected
        self.bg_running = False          # connected: the background process is working (stats thread)
        self.take_over_when_stopped = False
        self._log_pos = None             # connected: how much of the background's log has been shown
        self.ledger = engine.Ledger(self.data_dir / "ledger.db")
        self.engine = self.thread = None
        self.simulation_process = None
        self.busy = False            # a one-off "Process now" run is in progress
        self.closing = threading.Event()
        self.snapshot = None         # latest counters + history, produced by the stats thread
        self.shown_history = None
        self.sample_rows, self.sample_name = [], ""
        self.ui_q, self.log_q = queue.Queue(), queue.Queue()

        handler = QueueHandler(self.log_q)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

        self.title(f"{APP_NAME} {VERSION}" + (" (administrator)" if permissions.is_elevated() else ""))
        self.scale = dpi_scale * window_scale  # fonts grow with the Windows display scale; sizes must follow
        # never taller or wider than the screen (minus taskbar and title bar), or the Save button ends up off-screen
        self.L = self.theme.layout
        wide, tall, min_wide, min_tall = self.L.window
        width = min(self.px(wide * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(tall), self.winfo_screenheight() - self.px(100))
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+{self.px(8)}")
        self.minsize(min(self.px(min_wide * self.text_scale), width), min(self.px(min_tall), height))
        self.font_families = theme.resolve_font_families(self, self.theme, self.cfg["font_family"])
        body, heading, mono = (self.font_families[k] for k in ("body", "heading", "mono"))
        theme.apply_tk_palette(self, self.font_families, self.theme)
        self.configure(background=theme.BACKGROUND)
        if self.theme.dark:
            theme.dark_title_bar(self)
        style = ttk.Style(self)
        if self.theme.ttk_base and self.theme.ttk_base in style.theme_names():
            style.theme_use(self.theme.ttk_base)
        self.theme.configure_styles(style, self.px, self.font_families)
        self._font_body, self._font_heading, self._font_mono = body, heading, mono
        log.info("Theme: %s%s, font %s, text %d%%, scale %d%%, language %s", self.theme.key,
                 " (dark)" if self.theme.dark else "", self.font_families["body"], self.cfg["font_size"],
                 self.cfg["ui_scale"], self.language)

        self._make_vars()
        self._build_menubar()
        self._build_body()
        self._build_toolbar()
        self._build_header()
        self._build_tabs_scroller()
        self._build_setup()
        self._build_delivery()
        self._build_pick()
        self._build_history()
        self._build_operator()
        if self.L.monitor_page:
            self._build_monitor()
        self._build_footer()
        self._build_options()
        self._build_log()
        self._build_help()
        self._fill_sidebar()
        self.fill(self.cfg)
        self._add_tooltips()
        if self.L.cards:
            restyle_cards(self)  # the plain widgets inside a box take the card's colour
        self._bind_shortcuts()
        self._apply_lock()
        for sequence in ("<KeyPress>", "<ButtonPress>", "<MouseWheel>"):
            self.bind_all(sequence, self._mark_admin_activity, add="+")
        if not self.protected:
            self.after_idle(self.create_admin_password)
        self._update_protection_state()
        self._watch_setup_changes()
        self._refresh_checklist()
        self.tabs.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        threading.Thread(target=self._stats_loop, daemon=True).start()
        self._tick_job = self.after(200, self._tick)
        self._edit_check_job = self.after(700, self._check_settings_edit)
        if notice:  # e.g. the one-time move of the data folder
            self.after(600, lambda: messagebox.showinfo(APP_NAME, notice, parent=self))
        if self.cfg["autostart"] or resume:
            self.after(400, lambda: self.start(quiet=True))


def run(data_dir, notice=None, lock=None, resume=False):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except (AttributeError, OSError):
        pass
    App(data_dir, notice, lock, connected=lock is None, resume=resume).mainloop()
