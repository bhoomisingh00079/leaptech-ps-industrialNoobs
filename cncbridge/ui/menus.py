"""The menu bar, its enabled/disabled states and the keyboard shortcuts."""
import tkinter as tk
from tkinter import ttk

from .dropdown import DropDown
from ..i18n import tr


class MenusMixin:
    def _build_menubar(self):
        """The menu bar: everything that is not the daily work. Each entry calls the method the old button did."""
        strip = ttk.Frame(self, style="MenuBar.TFrame")
        strip.pack(fill="x")
        self.menu_items, self.menubar_buttons, self.menus = {}, {}, {}
        self.menu_titles = {}  # menu title button -> its English name, which the code keys everything by
        self._dropdown, self._dropdown_button = None, None
        menu_font = (self._font_body, self.L.menu_size)

        def menu(label, parent=None):
            if parent is not None:
                sub = tk.Menu(parent, tearoff=False, font=menu_font)
                parent.add_cascade(label=label, menu=sub)
                return sub
            # a menu bar drawn by the program: Windows draws its own at a fixed size, whatever the font
            button = ttk.Button(strip, text=tr(label), style="MenuBar.Toolbutton", underline=0, takefocus=False,
                                command=lambda: self._click_title(button))
            button.pack(side="left")
            sub = tk.Menu(button, tearoff=False, font=menu_font)
            self.menubar_buttons[label] = button
            self.menus[label] = sub
            self.menu_titles[button] = label
            self.bind(f"<Alt-{tr(label)[0].lower()}>", lambda event, b=button: self._open_menu(b))
            return sub

        def item(parent, key, label, command, accelerator=None):
            parent.add_command(label=label, command=command, accelerator=accelerator)
            self.menu_items[key] = (parent, parent.index("end"))

        file_menu = menu("File")
        item(file_menu, "open_csv", tr("Open CSV file..."), self.open_csv, "Ctrl+O")
        item(file_menu, "open_newest", tr("Open newest machine file"), self.open_newest_file)
        file_menu.add_separator()
        item(file_menu, "save", tr("Save settings"), self.save, "Ctrl+S")
        item(file_menu, "export", tr("Export settings..."), self.export_settings)
        item(file_menu, "import", tr("Import settings..."), self.import_settings)
        item(file_menu, "restore", tr("Restore an earlier version..."),
             lambda: self.import_settings(self.data_dir / "backups"))
        file_menu.add_separator()
        folders = menu(tr("Open folder"), file_menu)
        for label, key in ((tr("Machine folder"), "source_folder"), (tr("Back-up folder"), "backup_folder"),
                           (tr("Shared folder"), "shared_folder")):
            folders.add_command(label=label, command=lambda key=key: self._open_folder(self.v[key].get()))
        folders.add_separator()
        folders.add_command(label=tr("Data folder (send queue, rejected files, log file)"),
                            command=lambda: self._open_folder(self.data_dir))
        file_menu.add_separator()
        item(file_menu, "logout", tr("Log out"), self.toggle_lock)
        file_menu.add_separator()
        item(file_menu, "restart_admin", tr("Restart as administrator"), self.restart_as_administrator)
        item(file_menu, "exit", tr("Exit"), self.on_close)

        monitoring = menu("Monitoring")
        item(monitoring, "start", tr("Start monitoring"), self.start, "F5")
        item(monitoring, "stop", tr("Stop"), self.stop, "Shift+F5")
        item(monitoring, "process_now", tr("Process now"), self.process_now, "F6")

        settings = menu("Settings")
        item(settings, "options", tr("Options..."), self.open_options, "Ctrl+,")
        settings.add_separator()
        for key, page, label in (("opt_processing", "processing", tr("Processing...")),
                                 ("opt_output", "output", tr("Output...")),
                                 ("opt_alerts", "alerts", tr("Alerts...")),
                                 ("opt_heartbeat", "heartbeat", tr("Heartbeat...")),
                                 ("opt_access", "access", tr("Operators & access...")),
                                 ("opt_appearance", "appearance", tr("Appearance..."))):
            item(settings, key, label, lambda page=page: self.open_options(page))
        settings.add_separator()
        item(settings, "lock", tr("Lock setup"), self.toggle_lock)

        tools = menu("Tools")
        item(tools, "test_shared", tr("Test shared folder"), self.test_shared)
        item(tools, "test_api", tr("Test API link"), self.test_api)
        item(tools, "test_ftp", tr("Test FTP server"), self.test_ftp)
        item(tools, "test_alert", tr("Send test alert"), self.test_alert)
        item(tools, "test_heartbeat", tr("Send test heartbeat"), self.test_heartbeat)
        tools.add_separator()
        item(tools, "simulation", tr("Simulation..."), self.open_simulation)

        view = menu("View")
        item(view, "log", tr("Activity log"), self.open_log, "Ctrl+L")
        item(view, "stations", tr("Stations..."), self.open_stations)
        item(view, "settings_history", tr("Settings history..."), self.open_settings_history)
        item(view, "setup_checklist", tr("Setup checklist"), self.show_checklist)
        view.add_separator()
        if self.L.monitor_page:
            item(view, "view_monitor", tr("Monitor"), lambda: self._show_tab(self.monitor_tab), "Ctrl+0")
        for number, (key, attribute, label) in enumerate((("view_setup", "setup_tab", tr("Setup")),
                                                          ("view_delivery", "delivery_tab", tr("Delivery")),
                                                          ("view_pick", "pick_tab", tr("Pick ranges & preview")),
                                                          ("view_history", "history_tab", tr("History"))), 1):
            item(view, key, label, lambda attribute=attribute: self._show_tab(getattr(self, attribute)),
                 f"Ctrl+{number}")

        help_menu = menu("Help")
        item(help_menu, "help", tr("Getting started"), lambda: self.open_help("start"), "F1")
        for key, title, _ in self.help_topic_data[1:]:
            item(help_menu, f"help_{key}", tr(title), lambda key=key: self.open_help(key))
        help_menu.add_separator()
        item(help_menu, "diagnostics", tr("Export diagnostics..."), self.export_diagnostics)
        item(help_menu, "about", tr("About CNCBridge"), self.about)
        self.bind("<F10>", lambda event: self._open_menu(next(iter(self.menubar_buttons.values()))))
        ttk.Separator(self).pack(fill="x")

    def _open_menu(self, button, keyboard=True):
        """Drop a menu down from its title, as Alt+letter, F10 and a click do."""
        self._close_menus(refocus=False)
        label = self.menu_titles[button]
        self._dropdown_button = button
        self._dropdown = DropDown(self, self.menus[label], button.winfo_rootx(),
                                  button.winfo_rooty() + button.winfo_height(), keyboard=keyboard)
        return "break"

    def _click_title(self, button):
        """A click on a menu title opens its menu, or closes it when it is already open."""
        if self._dropdown is not None and self._dropdown_button is button:
            self._close_menus()
        else:
            self._open_menu(button, keyboard=False)

    def _close_menus(self, refocus=True):
        if self._dropdown is not None:
            dropdown, self._dropdown, self._dropdown_button = self._dropdown, None, None
            dropdown.close()
            if refocus:
                self.focus_force()

    def _step_menu(self, step):
        """Left / Right in an open menu: the next or previous menu in the bar."""
        buttons = list(self.menubar_buttons.values())
        if self._dropdown_button in buttons:
            self._open_menu(buttons[(buttons.index(self._dropdown_button) + step) % len(buttons)])

    def _hover_menu(self, widget):
        """While a menu is open, the pointer on another title switches to that menu."""
        if self._dropdown is not None and widget in self.menubar_buttons.values()                 and widget is not self._dropdown_button:
            self._open_menu(widget, keyboard=False)

    def _click_outside_menus(self, widget):
        """A click outside an open menu closes it; on another title it opens that menu instead."""
        buttons = list(self.menubar_buttons.values())
        self._close_menus()
        if widget in buttons:
            self._open_menu(widget, keyboard=False)

    # entries that need the setup rights: they are disabled while the setup is locked
    LOCKED_ENTRIES = ("open_csv", "open_newest", "save", "export", "import", "restore", "options", "opt_processing",
                      "opt_output", "opt_alerts", "opt_heartbeat", "opt_access", "opt_appearance", "test_shared", "test_api",
                      "test_ftp", "test_alert", "test_heartbeat", "simulation", "setup_checklist", "view_setup",
                      "view_delivery", "view_pick")

    def _set_menu(self, key, enabled):
        menu, index = self.menu_items[key]
        menu.entryconfigure(index, state="normal" if enabled else "disabled")

    def _menu_enabled(self, key):
        menu, index = self.menu_items[key]
        return str(menu.entrycget(index, "state")) != "disabled"

    def _bind_shortcuts(self):
        """Keyboard shortcuts; each does what its menu entry does, and nothing while that entry is disabled."""
        def bind(sequence, key, handler):
            def run(_event=None):
                if self._menu_enabled(key):
                    handler()
                return "break"
            self.bind(sequence, run)

        bind("<Control-o>", "open_csv", self.open_csv)
        bind("<Control-s>", "save", self.save)
        bind("<F5>", "start", self.start)
        bind("<Shift-F5>", "stop", self.stop)
        bind("<F6>", "process_now", self.process_now)
        bind("<Control-comma>", "options", self.open_options)
        bind("<Control-l>", "log", self.open_log)
        bind("<F1>", "help", lambda: self.open_help("start"))
        if self.L.monitor_page:
            bind("<Control-Key-0>", "view_monitor", lambda: self._show_tab(self.monitor_tab))
        for number, (key, attribute) in enumerate((("view_setup", "setup_tab"), ("view_delivery", "delivery_tab"),
                                                   ("view_pick", "pick_tab"), ("view_history", "history_tab")), 1):
            bind(f"<Control-Key-{number}>", key, lambda attribute=attribute: self._show_tab(getattr(self, attribute)))

    def _show_tab(self, tab):
        if str(self.tabs.tab(tab, "state")) == "normal":
            self.tabs.select(tab)

    def open_newest_file(self):
        self.tabs.select(self.pick_tab)
        self.open_newest()
