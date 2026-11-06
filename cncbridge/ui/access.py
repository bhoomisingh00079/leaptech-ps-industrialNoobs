"""The operator view, the Admin session and the shift-change confirmation."""
import tkinter as tk
from datetime import timedelta
from tkinter import messagebox, ttk

from .. import config, firstrun, permissions, shifts
from ..config import APP_NAME, SettingsTooNew, normalize_config, save_config
from .common import log
from .dialogs import ShiftDialog
from ..i18n import tr


class AdminDialog:
    """Small modal dialog for Admin login, first-run creation and password changes."""
    def __init__(self, parent, mode):
        self.result = None
        window = self.window = tk.Toplevel(parent)
        window.transient(parent)
        window.title(tr({"create": "Create the Admin password", "login": "Admin login",
                         "change": "Change Admin password"}[mode]))
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)
        fields = []
        if mode == "login":
            fields.append(("username", tr("User name"), "Admin", False))
            fields.append(("password", tr("Password"), "", True))
        elif mode == "create":
            fields.extend((("new_password", tr("New password (12 or more characters)"), "", True),
                           ("repeat", tr("Type the password again"), "", True)))
        else:
            fields.extend((("current_password", tr("Current password"), "", True),
                           ("new_password", tr("New password (12 or more characters)"), "", True),
                           ("repeat", tr("Type the new password again"), "", True)))
        self.values = {key: tk.StringVar(window, value=value) for key, _label, value, _secret in fields}
        for row, (key, label, _value, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            entry = ttk.Entry(frame, textvariable=self.values[key], width=34, show="●" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            if row == 0 or (mode == "login" and key == "password"):
                self.first_entry = entry
        self.message = tk.StringVar(window)
        ttk.Label(frame, textvariable=self.message, wraplength=390).grid(
            row=len(fields), column=0, columnspan=2, sticky="w", pady=(6, 2))
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text=tr("Cancel"), command=window.destroy).pack(side="right", padx=(8, 0))
        submit = tr("Sign in") if mode == "login" else tr("Create password") if mode == "create" else tr("Change password")
        ttk.Button(buttons, text=submit, style="Go.TButton", command=lambda: self._submit(mode)).pack(side="right")
        window.bind("<Return>", lambda _event: self._submit(mode))
        window.bind("<Escape>", lambda _event: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.update_idletasks()
        x = parent.winfo_rootx() + max((parent.winfo_width() - window.winfo_reqwidth()) // 2, 0)
        y = parent.winfo_rooty() + max((parent.winfo_height() - window.winfo_reqheight()) // 2, 0)
        window.geometry(f"+{x}+{y}")
        window.grab_set()
        self.first_entry.focus_set()
        parent.wait_window(window)

    def _submit(self, mode):
        values = {key: value.get() for key, value in self.values.items()}
        if mode == "login":
            if not values["username"].strip():
                self.message.set(tr("Enter the Admin username."))
                return
            if not values["password"]:
                self.message.set(tr("Enter the Admin password."))
                return
        else:
            password = values["new_password"]
            if len(password) < config.MIN_PASSWORD:
                self.message.set(tr("Use at least {count} characters.", count=config.MIN_PASSWORD))
                return
            if password != values["repeat"]:
                self.message.set(tr("The passwords do not match."))
                return
        self.result = values
        self.window.destroy()


class AccessMixin:
    def _build_operator(self):
        """What an operator sees while the setup is locked: the typed values and nothing else to change."""
        tab = self.operator_tab = ttk.Frame(self.tabs, padding=self.L.tab_pad)
        self.tabs.add(tab, text=tr("  Operator  "))
        self._page_intro(tab, tr("Typed values"),
                         tr("These values are written into every file. Change them when the operator or the "
                         "work order changes; they apply from the next file, without stopping."))
        box = ttk.LabelFrame(tab, text=tr(" Typed values "), padding=self.L.box_pad)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.operator_labels = []
        for i, (_, value) in enumerate(self.man_vars):
            label = ttk.Label(box, text=tr("Typed value {number}", number=i + 1), style="Card.TLabel", width=22)
            label.grid(row=i, column=0, sticky="w", pady=6)
            ttk.Entry(box, textvariable=value).grid(row=i, column=1, sticky="ew", padx=8, pady=6)
            self.operator_labels.append(label)
        apply = ttk.Button(box, text=tr("Apply"), style="Go.TButton", command=self.apply_operator_values)
        apply.grid(row=len(self.man_vars), column=1, sticky="w", padx=8, pady=(8, 2))
        self.field_widgets["operator_apply"] = apply
        self.operator_unlock_btn = ttk.Button(box, text=tr("Unlock setup..."), command=self.toggle_lock)
        self.operator_unlock_btn.grid(row=len(self.man_vars) + 1, column=1, sticky="w", padx=8, pady=(8, 2))

    # ================================================================ setup password
    @property
    def protected(self):
        return bool(self.cfg["setup_password"])

    def _apply_lock(self):
        """Show the setup tabs, or only the operator's, depending on the password and whether it was entered."""
        locked = not self.unlocked
        for tab in (self.setup_tab, self.delivery_tab, self.pick_tab):
            self.tabs.tab(tab, state="hidden" if locked else "normal")
        self.tabs.tab(self.operator_tab, state="normal" if locked else "hidden")
        for button in self.save_buttons:
            button.configure(state="disabled" if locked else "normal")
        for key in self.LOCKED_ENTRIES:
            self._set_menu(key, not locked)
        lock_menu, lock_index = self.menu_items["lock"]
        lock_menu.entryconfigure(lock_index, label=tr("Unlock setup...") if locked else tr("Lock setup"),
                                 state="normal")
        self._set_menu("logout", self.unlocked)
        if locked:
            self.options_win.withdraw()
        for label, (name, _) in zip(self.operator_labels, self.man_vars):
            label.configure(text=name.get().strip() or label.cget("text"))
        self.password_state.set(tr("Admin password is set") if self.protected else tr("Admin password must be created"))
        self.password_change_btn.configure(state="normal" if self.unlocked else "disabled")
        self.tabs.select(self._home_tab(locked))
        self._sync_sidebar()
        self._schedule_checklist()
        self._each_extension("lock_changed", locked)

    def _home_tab(self, locked):
        """The page shown when the window opens, and after the lock changes."""
        if locked:
            return self.operator_tab
        if self.L.monitor_page and firstrun.is_done(self.data_dir):
            return self.monitor_tab  # set up and started before: the first thing wanted is to see how it is going
        return self.setup_tab

    def _show_admin_dialog(self, mode):
        """The one patch point used by window tests instead of opening a modal dialog."""
        return AdminDialog(self, mode).result

    def _record_admin_event(self, action, event_id):
        from .. import audit, eventlog
        audit.activity(self.data_dir, action)
        log.info("%s", action, extra={"event": event_id})

    def _begin_admin_session(self):
        from ..eventlog import ADMIN_LOGIN
        self.unlocked = True
        self._admin_login_at = self._admin_last_input_at = self._now()
        self._admin_failures = 0
        self._admin_retry_until = None
        self._record_admin_event("Admin login", ADMIN_LOGIN)
        self._apply_lock()

    def _wait_for_admin_retry(self):
        if not self._admin_retry_until:
            return
        seconds = (self._admin_retry_until - self._now()).total_seconds()
        if seconds <= 0:
            return
        ready = tk.BooleanVar(self, False)
        self.after(int(seconds * 1000), lambda: ready.set(True))
        self.wait_variable(ready)

    def _failed_admin_login(self):
        from ..eventlog import ADMIN_LOGIN_FAILED
        self._admin_failures += 1
        delay = min(30, 2 ** (self._admin_failures - 1))
        self._admin_retry_until = self._now() + timedelta(seconds=delay)
        self._record_admin_event("Admin login failed", ADMIN_LOGIN_FAILED)
        messagebox.showerror(APP_NAME, tr("That Admin sign-in was not accepted. Try again in {seconds} seconds.",
                                          seconds=delay), parent=self)

    def create_admin_password(self):
        """First start: keep the application in the operator view until an Admin password is created."""
        if self.protected:
            return
        values = self._show_admin_dialog("create")
        if values is None:
            self.status_msg.set(tr("Create an Admin password to unlock setup."))
            return
        password = values.get("new_password", "")
        if len(password) < config.MIN_PASSWORD or password != values.get("repeat"):
            return messagebox.showerror(APP_NAME, tr("Use a matching password with at least {count} characters.",
                                                     count=config.MIN_PASSWORD), parent=self)
        if self._store_admin_password(password):
            from ..eventlog import ADMIN_PASSWORD_CHANGED
            self._record_admin_event("Admin password created", ADMIN_PASSWORD_CHANGED)
            self._begin_admin_session()

    def _login_admin(self):
        if not self.protected:
            return self.create_admin_password()
        self._wait_for_admin_retry()
        values = self._show_admin_dialog("login")
        if values is None:
            return
        password = values.get("password", "")
        username = values.get("username", "").strip()
        if username.casefold() != "admin" or not config.check_password(password, self.cfg["setup_password"]):
            return self._failed_admin_login()
        self._admin_failures = 0
        self._admin_retry_until = None
        if len(password) < config.MIN_PASSWORD:
            messagebox.showwarning(APP_NAME, tr("This older Admin password is too short. Change it before using setup."),
                                   parent=self)
            if self.change_admin_password(force=True):
                messagebox.showinfo(APP_NAME, tr("Password changed. Sign in again with the new password."), parent=self)
            return
        self._begin_admin_session()

    def _store_admin_password(self, password):
        cfg = dict(self.cfg, setup_password=config.hash_password(password))
        try:
            save_config(cfg, self.cfg_path, how="password")
        except PermissionError:
            self._need_administrator()
            return False
        except SettingsTooNew as e:
            messagebox.showerror(APP_NAME, tr("The password was not changed:\n{e}", e=e), parent=self)
            return False
        self.cfg = normalize_config(cfg)
        return True

    def change_admin_password(self, force=False):
        if not self.unlocked and not force:
            return False
        values = self._show_admin_dialog("change")
        if values is None:
            return False
        if not config.check_password(values.get("current_password", ""), self.cfg["setup_password"]):
            from ..eventlog import ADMIN_LOGIN_FAILED
            self._record_admin_event("Admin password change failed", ADMIN_LOGIN_FAILED)
            messagebox.showerror(APP_NAME, tr("The current Admin password is not right."), parent=self)
            return False
        password = values.get("new_password", "")
        if len(password) < config.MIN_PASSWORD or password != values.get("repeat"):
            messagebox.showerror(APP_NAME, tr("Use a matching password with at least {count} characters.",
                                              count=config.MIN_PASSWORD), parent=self)
            return False
        if not self._store_admin_password(password):
            return False
        from ..eventlog import ADMIN_PASSWORD_CHANGED
        self._record_admin_event("Admin password changed", ADMIN_PASSWORD_CHANGED)
        if self.unlocked:
            self._logout()
        return True

    def _logout(self, expired=False):
        if not self.unlocked:
            return
        from ..eventlog import ADMIN_LOGOUT
        self.unlocked = False
        self._admin_login_at = self._admin_last_input_at = None
        self._record_admin_event("Admin session expired" if expired else "Admin logout", ADMIN_LOGOUT)
        self._apply_lock()

    def _mark_admin_activity(self, event=None):
        if not self.unlocked or event is None:
            return
        try:
            widget = event.widget
            while widget is not None and widget is not self:
                widget = widget.master
            if widget is self:
                self._admin_last_input_at = self._now()
        except (AttributeError, tk.TclError):
            pass

    def check_admin_session(self):
        if not self.unlocked or not self._admin_login_at:
            return
        now = self._now()
        if (now - self._admin_login_at).total_seconds() >= 8 * 60 * 60:
            return self._logout(expired=True)
        idle = int(self.cfg.get("admin_idle_minutes", 0) or 0)
        if idle and self._admin_last_input_at and (now - self._admin_last_input_at).total_seconds() >= idle * 60:
            self._logout(expired=True)

    def toggle_lock(self):
        if not self.unlocked:
            return self._login_admin()
        else:
            self._logout()

    # ================================================================ shift changes
    @staticmethod
    def _shift_problem(cfg):
        try:
            shifts.parse_times(cfg["shift_times"], strict=True)
        except ValueError as e:
            return tr("Shift times: {e}", e=e)
        return None

    def _check_shift(self):
        """Ask for a confirmation when a shift has started that nobody has confirmed yet."""
        due = shifts.pending(shifts.parse_times(self.cfg["shift_times"]), self.shift_confirmed, self._now())
        self._show_shift(due)
        if due and due != self._shift_prompted:
            self._shift_prompted = due  # ask once per shift change; the banner stays for a later click
            self.confirm_shift(due)

    def _show_shift(self, due):
        if due:
            self.shift_banner.configure(text="⏱  " + tr("Shift change at {due:%H:%M}: please confirm the typed values (click here)", due=due))
            if not self.shift_banner_shown:
                self.shift_banner.pack(fill="x", after=self.header_sep)
                self.shift_banner_shown = True
        elif self.shift_banner_shown:
            self.shift_banner.pack_forget()
            self.shift_banner_shown = False

    def confirm_shift(self, due=None):
        due = due or shifts.latest_boundary(shifts.parse_times(self.cfg["shift_times"]), self._now())
        if due is None:
            return
        if self.shift_dialog is not None and self.shift_dialog.winfo_exists():
            self.shift_dialog.deiconify()
            self.shift_dialog.lift()
            return
        self.shift_dialog = ShiftDialog(self, due)

    def finish_shift(self, values=None):
        """The operator confirmed. New values, if they changed, are saved first. Returns False if that failed."""
        if values is not None and values != [var.get() for _, var in self.man_vars]:
            for (_, var), text in zip(self.man_vars, values):
                var.set(text)
            if not self._save_typed_values():
                return False
        self.shift_confirmed = self._now()
        shifts.write_confirmed(self.data_dir, self.shift_confirmed)
        self._show_shift(None)
        self.status_msg.set(tr("Shift change confirmed."))
        return True

    def _save_typed_values(self):
        """
        Save only the typed values (operator.json). Operators may do this even when the settings are protected;
        nothing else in the form is saved. Returns False if it failed.
        """
        values = [var.get() for _, var in self.man_vars]
        try:
            cfg = config.save_operator_values(self.data_dir, values)
        except OSError as e:
            messagebox.showerror(APP_NAME, tr("The typed values could not be saved:\n{e}", e=e), parent=self)
            return False
        self.cfg = dict(self.cfg, manual_inputs=cfg["manual_inputs"])
        if self.running:
            self.engine.cfg = dict(self.engine.cfg, manual_inputs=cfg["manual_inputs"])
        log.info("Typed values saved")
        return True

    def apply_operator_values(self):
        """The Operator tab: the typed values are the only thing that can have changed."""
        if self._save_typed_values():
            self.status_msg.set(tr("Typed values applied. They are used from the next file."))
