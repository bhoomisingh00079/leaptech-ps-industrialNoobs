"""
End-to-end tests that drive the real CNCBridge window. They open windows on the screen, so they only run
when asked:

    set CNCBRIDGE_WINDOW_TESTS=1
    python -m unittest tests.test_window -v
"""
import gc
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import types
import unittest
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if os.environ.get("CNCBRIDGE_WINDOW_TESTS") != "1":
    raise unittest.SkipTest("window tests open real windows; set CNCBRIDGE_WINDOW_TESTS=1 to run them")

os.environ["CNCBRIDGE_NO_EVENTLOG"] = "1"  # the background process some tests start must not write to this PC's real Event Log
from cncbridge import alerts, app, audit, config, engine, firstrun, i18n, permissions, shifts, stations  # noqa: E402
from cncbridge.ui import access as access_ui  # noqa: E402
from cncbridge.ui import cards, icons, widgets  # noqa: E402
from cncbridge import theme  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

# Tk may only free a window on the thread that made it. Python's cyclic collector can run on any thread, so
# a closed window could be freed on a background thread and crash the process (Tcl_AsyncDelete). The tests
# therefore switch it off and collect on the main thread, after each test.
gc.disable()
from test_engine import Receiver  # noqa: E402
from test_stations import make_copy  # noqa: E402
from test_ftp import FakeFtpServer  # noqa: E402
from test_installer import delete_registry_tree, fake_exe, hold_open, release  # noqa: E402
from installer import core as setup_core  # noqa: E402
from installer.wizard import Wizard  # noqa: E402

SAMPLE = ROOT / "samples" / "pnp_L1M8_board_00001.csv"


def drive(window, steps):
    """
    Run a generator of steps inside the window's event loop. A step yields a number (milliseconds to wait)
    or a function (wait until it returns true, at most 30 s). Returns the errors, empty when all passed.
    """
    errors, gen = [], steps()

    def stop(message=None):
        if message:
            errors.append(message)
        window.closing.set()
        if window.engine:
            window.engine.stop()
        window.destroy()

    def advance():
        try:
            item = next(gen)
        except StopIteration:
            return stop()
        except Exception:
            return stop(traceback.format_exc())
        if not callable(item):
            return window.after(item, advance)
        deadline = time.time() + 30

        def poll():
            try:
                done = item()
            except Exception:
                return stop(traceback.format_exc())
            if done:
                window.after(10, advance)
            elif time.time() > deadline:
                code = getattr(item, "__code__", None)
                where = f"line {code.co_firstlineno} of {Path(code.co_filename).name}" if code else repr(item)
                stop(f"timed out waiting for the condition at {where}")
            else:
                window.after(200, poll)
        poll()

    window.after(600, advance)
    window.mainloop()
    return errors


def focused(window):
    """
    True once the keyboard focus is in this window itself, not in another window of the program such as a Help
    window that was just closed (asked for again on each try, since another program may hold the focus).
    """
    try:
        window.focus_force()
        window.update()
        widget = window.focus_get()
        return widget is not None and widget.winfo_toplevel() is window
    except Exception:
        return False


def press(window, sequence, done, tries=10):
    """
    Send a key press and wait for its effect. Tk drops a key press when the program does not have the keyboard
    focus at that moment, and another program (or a window left over from a test) can take it away, so a press
    that had no effect is sent again. Only for presses that are harmless to repeat.
    """
    for _ in range(tries):
        focused(window)
        window.event_generate(sequence)
        window.update()
        if done():
            return True
        time.sleep(0.1)
    return False


def drag(window, c1, r1, c2, r2):
    """Drag over cells in the range picker, as a user would with the mouse."""
    g = window.grid_view
    g.see(c1, r1)
    window.update()
    canvas = g.canvas

    def at(col, row):
        return int(g.rw + g.colx[col] + 6 - canvas.canvasx(0)), int(g.rh * (row + 1) + 6 - canvas.canvasy(0))
    (x1, y1), (x2, y2) = at(c1, r1), at(c2, r2)
    canvas.event_generate("<Button-1>", x=x1, y=y1)
    canvas.event_generate("<B1-Motion>", x=(x1 + x2) // 2, y=(y1 + y2) // 2)
    canvas.event_generate("<B1-Motion>", x=x2, y=y2)
    canvas.event_generate("<ButtonRelease-1>", x=x2, y=y2)
    window.update()


class WindowTest(unittest.TestCase):
    def setUp(self):
        logging.getLogger("cncbridge").setLevel(logging.INFO)
        self.tmp = Path(tempfile.mkdtemp())
        self.machine, self.backup, self.shared = (self.tmp / n for n in ("machine", "backup", "shared"))
        self.machine.mkdir()
        self.dialogs = []
        for name in ("showinfo", "showerror", "showwarning"):
            self._patch(messagebox, name, lambda *a, _n=name, **k: self.dialogs.append((_n, a)))
        self._patch(messagebox, "askokcancel", lambda *a, **k: True)
        self.notified = []  # Windows notifications are recorded, not shown
        self._patch(alerts, "SENDERS", dict(alerts.SENDERS, notify=lambda *a, **k: self.notified.append(a)))
        self.window = self.make_window()

    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        # Ordinary window tests begin with a known Admin account and an authenticated session.
        # First-run setup and sign-in are covered by the focused tests below.
        raw = config.normalize_config({"setup_password": config.hash_password("window-test-password")})
        (data / "config.json").write_text(json.dumps(raw), encoding="utf-8")
        window = app.App(data)
        window._begin_admin_session()
        return window

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.window = None
        gc.collect()

    def _patch(self, owner, name, value):
        original = getattr(owner, name)
        setattr(owner, name, value)
        self.addCleanup(setattr, owner, name, original)

    def run_steps(self, steps):
        errors = drive(self.window, steps)
        self.assertEqual(errors, [], "\n".join(errors))

    def menu_state(self, key):
        """'normal' or 'disabled': the state of a menu entry, found by the key the window knows it by."""
        menu, index = self.window.menu_items[key]
        return str(menu.entrycget(index, "state"))

    def menu_label(self, key):
        menu, index = self.window.menu_items[key]
        return menu.entrycget(index, "label")

    def drop(self, name, serial):
        (self.machine / name).write_bytes(SAMPLE.read_bytes().replace(b"B240001", serial.encode()))

    def files(self, folder, pattern="*"):
        return sorted(p for p in Path(folder).glob(pattern) if p.is_file())


class RangePicker(WindowTest):
    def test_drag_assign_and_preview(self):
        w = self.window

        def steps():
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            w._update_slots()
            yield 300
            self.assertEqual((w.sample_name, len(w.sample_rows)), (SAMPLE.name, 50))
            w.slot_box.current(0)
            w._slot_chosen()
            drag(w, 0, 11, 8, 15)  # inside the visible rows: a drag past the edge scrolls, which is another test
            self.assertEqual(w.pick_range.get(), "A12:I16")
            w.pick_open.set(True)
            w._update_pick_range()
            self.assertEqual(w.pick_range.get(), "A12:I*")
            w.pick_name.set("Placements")
            w.assign()
            w.slot_box.current(1)
            w._slot_chosen()
            w.pick_open.set(False)
            drag(w, 1, 5, 1, 5)
            self.assertEqual(w.pick_range.get(), "B6")
            w.pick_name.set("Board serial")
            w.assign()
            yield 300
            preview = w.preview.get("1.0", "end")
            self.assertIn('"Board serial"', preview)
            self.assertIn('"value": "B240001"', preview)
            self.assertEqual([r.get() for _, r, _ in w.sel_vars[:2]], ["A12:I*", "B6"])
        self.run_steps(steps)

    def test_the_pdf_preview_says_what_the_report_holds_and_can_be_saved(self):
        w = self.window
        saved = self.tmp / "preview.pdf"

        def steps():
            self.assertIn("output_format_pdf", w.field_widgets)  # the fourth format on the Setup tab
            self.assertTrue(w.open_sample(SAMPLE))
            w.sel_vars[0][1].set("A12:I*")
            w.v["output_format"].set("pdf")
            w.refresh_preview()
            yield 100
            self.assertTrue(w.preview.get("1.0", "end").startswith("PDF report: 1 page(s), 1 selection(s)"))
            self.assertTrue(w.preview_pdf_btn.winfo_manager())
            with mock.patch.object(filedialog, "asksaveasfilename", return_value=str(saved)), \
                    mock.patch("os.startfile", create=True) as opened:
                w.save_preview_pdf()
            self.assertTrue(saved.read_bytes().startswith(b"%PDF"))
            opened.assert_called_once_with(str(saved))
            w.v["output_format"].set("json")
            w.refresh_preview()
            self.assertFalse(w.preview_pdf_btn.winfo_manager())
            self.assertIsNone(w._preview_pdf)
        self.run_steps(steps)

    def test_a_table_from_the_list_is_marked_and_assigned_by_its_title(self):
        w = self.window

        def steps():
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            w._update_slots()
            yield 200
            titles = [w.table_list.set(i, "title") for i in w.table_list.get_children()]
            self.assertEqual(titles, ["[HEADER]", "[L1xM8]", "[FEEDER_STATUS]", "[NOZZLE_STATS]", "[ERRORS]"])
            self.assertEqual(w.table_empty.winfo_manager(), "")
            w.slot_box.current(2)
            w._slot_chosen()
            w.table_list.selection_set("4")
            yield 100
            self.assertEqual(w.grid_view.sel, (0, 48, 3, 49))  # marked in the grid
            self.assertEqual((w.pick_range.get(), w.pick_name.get()), ("@[ERRORS]", "ERRORS"))
            w.assign()
            yield 200
            self.assertEqual((w.sel_vars[2][0].get(), w.sel_vars[2][1].get()), ("ERRORS", "@[ERRORS]"))
            self.assertIn('"Pick-up failure"', w.preview.get("1.0", "end"))
            w._show_tables(engine.read_rows(b"a,b\n1,2\n"))
            self.assertEqual(w.table_list.get_children(), ())
            self.assertTrue(w.table_empty.winfo_manager())
        self.run_steps(steps)


class SetupLock(WindowTest):
    """A setup password hides the setup tabs; operators keep Start/Stop, History, Log and the typed values."""

    def state(self, tab):
        return str(self.window.tabs.tab(tab, "state"))

    def visible(self):
        w = self.window
        return [self.state(t) for t in (w.setup_tab, w.delivery_tab, w.pick_tab, w.operator_tab)]

    def answers(self, *typed):
        queue = list(typed)
        self._patch(self.window, "_show_admin_dialog", lambda _mode: queue.pop(0))
        self._patch(self.window, "_wait_for_admin_retry", lambda: None)

    def test_lock_unlock_wrong_login_and_cancel(self):
        w = self.window

        def steps():
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])
            self.assertTrue(w.protected and w.unlocked)
            self.assertEqual(self.menu_state("lock"), "normal")
            self.assertGreaterEqual(config.MIN_PASSWORD, 12)

            w.toggle_lock()  # lock
            self.assertEqual(self.visible(), ["hidden", "hidden", "hidden", "normal"])
            self.assertEqual(w.tabs.select(), str(w.operator_tab))
            self.assertEqual(str(w.save_btn["state"]), "disabled")
            self.assertEqual([self.menu_state(k) for k in ("save", "open_csv", "options", "import", "opt_alerts",
                                                           "test_api", "simulation", "view_setup")], ["disabled"] * 8)
            self.assertEqual([self.menu_state(k) for k in ("lock", "log", "view_history", "help", "exit")],
                             ["normal"] * 5)
            self.assertEqual(self.menu_label("lock"), "Unlock setup...")
            self.assertEqual(w.options_win.state(), "withdrawn")
            w.open_options()  # and the Options window stays shut
            self.assertEqual(w.options_win.state(), "withdrawn")
            w.update()  # the run controls stay usable: monitoring is stopped, so Start and Process now are on
            self.assertEqual([str(b["state"]) for b in (w.start_btn, w.once_btn, w.stop_btn)],
                             ["normal", "normal", "disabled"])
            self.assertEqual(w.password_state.get(), "Admin password is set")

            self.dialogs.clear()
            self.answers({"username": "admin", "password": "wrong"})
            w.toggle_lock()
            self.assertEqual((self.dialogs[-1][0], self.visible()),
                             ("showerror", ["hidden", "hidden", "hidden", "normal"]))
            self.answers(None)
            w.toggle_lock()
            self.assertEqual(self.visible(), ["hidden", "hidden", "hidden", "normal"])
            self.answers({"username": "ADMIN", "password": "window-test-password"})
            w.toggle_lock()
            self.assertEqual(self.visible(), ["normal", "normal", "normal", "hidden"])
            self.assertEqual(self.menu_label("lock"), "Lock setup")
            self.assertEqual(str(w.save_btn["state"]), "normal")
            self.assertEqual([self.menu_state(k) for k in ("save", "options", "import", "simulation")], ["normal"] * 4)

            yield 100
        self.run_steps(steps)

    def test_password_change_requires_current_password_and_ends_session(self):
        w = self.window

        def steps():
            self.answers({"current_password": "window-test-password", "new_password": "replacement-password",
                          "repeat": "replacement-password"})
            self.assertTrue(w.change_admin_password())
            self.assertFalse(w.unlocked)
            self.assertTrue(w.protected)
            self.assertEqual(self.visible(), ["hidden", "hidden", "hidden", "normal"])
            self.assertTrue(config.check_password("replacement-password", w.cfg["setup_password"]))
            yield 100
        self.run_steps(steps)

    def test_first_run_requires_admin_password_before_setup_unlocks(self):
        first = app.App(self.tmp / "first-run")
        self.addCleanup(first.destroy)
        self.assertFalse(first.protected)
        self.assertFalse(first.unlocked)
        self.assertEqual(str(first.tabs.tab(first.setup_tab, "state")), "hidden")
        self._patch(first, "_show_admin_dialog", lambda mode: {
            "new_password": "first-run-admin-password", "repeat": "first-run-admin-password"}
            if mode == "create" else None)
        first.create_admin_password()
        self.assertTrue(first.protected and first.unlocked)
        self.assertTrue(config.check_password("first-run-admin-password", first.cfg["setup_password"]))
        self.assertEqual(str(first.tabs.tab(first.setup_tab, "state")), "normal")

    def test_short_legacy_password_is_accepted_once_then_forced_to_change(self):
        w = self.window

        def steps():
            w._store_admin_password("old1")
            w.unlocked = False
            w._apply_lock()
            queue = [
                {"username": "aDmIn", "password": "old1"},
                {"current_password": "old1", "new_password": "legacy-updated-password",
                 "repeat": "legacy-updated-password"},
            ]
            self._patch(w, "_show_admin_dialog", lambda _mode: queue.pop(0))
            self._patch(w, "_wait_for_admin_retry", lambda: None)
            w._login_admin()
            self.assertFalse(w.unlocked)  # sign in again after the forced change
            self.assertTrue(config.check_password("legacy-updated-password", w.cfg["setup_password"]))
            self.assertTrue(any(name == "showwarning" for name, _ in self.dialogs))
            yield 100
        self.run_steps(steps)

    def test_login_backoff_and_eight_hour_session_expiry(self):
        w = self.window

        def steps():
            clock = [datetime(2026, 1, 1, 12, 0)]
            self._patch(w, "_now", lambda: clock[0])
            self._patch(w, "_wait_for_admin_retry", lambda: None)
            queue = [{"username": "operator", "password": "window-test-password"},
                     {"username": "admin", "password": "wrong"},
                     {"username": "ADMIN", "password": "window-test-password"}]
            self._patch(w, "_show_admin_dialog", lambda _mode: queue.pop(0))
            w.unlocked = False
            w._apply_lock()
            w._login_admin()
            self.assertEqual((w._admin_failures, (w._admin_retry_until - clock[0]).total_seconds()), (1, 1))
            w._login_admin()
            self.assertEqual((w._admin_failures, (w._admin_retry_until - clock[0]).total_seconds()), (2, 2))
            w._login_admin()
            self.assertTrue(w.unlocked)
            w.cfg["admin_idle_minutes"] = 5
            clock[0] += timedelta(minutes=5)
            w.check_admin_session()
            self.assertFalse(w.unlocked)
            w._begin_admin_session()
            clock[0] += timedelta(hours=8)
            w.check_admin_session()
            self.assertFalse(w.unlocked)
            yield 100
        self.run_steps(steps)

    def test_a_restart_starts_locked(self):
        w = self.window

        def steps():
            w.v["station_name"].set("Line 7")
            self.assertTrue(w.save())
            yield 100
        self.run_steps(steps)
        second = app.App(self.tmp / "data")
        try:
            self.assertTrue(second.protected and not second.unlocked)
            self.assertEqual([str(second.tabs.tab(t, "state")) for t in (second.setup_tab, second.operator_tab)],
                             ["hidden", "normal"])
            self.assertEqual(second.tabs.select(), str(second.operator_tab))
            self.assertEqual(second.cfg["station_name"], "Line 7")  # the password did not cost the other settings
        finally:
            second.destroy()

    def test_operator_changes_the_typed_values_while_locked_and_running(self):
        w = self.window

        def operator_of(pattern):
            files = self.files(self.shared, pattern)
            return json.loads(files[0].read_text(encoding="utf-8"))["manual_inputs"] if files else None

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("B6")
            w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
            w.man_vars[1][0].set("Work order"), w.man_vars[1][1].set("WO-1")
            w.cfg["stable_seconds"] = 0.3
            self.assertTrue(w.save())
            w.toggle_lock()
            self.assertEqual([lbl["text"] for lbl in w.operator_labels], ["Operator", "Work order"])
            w.start()
            yield lambda: w.running
            self.drop("b1.csv", "S1")
            yield lambda: operator_of("b1_*.json") == {"Operator": "OP-17", "Work order": "WO-1"}

            w.man_vars[0][1].set("OP-99")  # typed on the Operator tab
            w.man_vars[1][1].set("WO-2")
            w.apply_operator_values()
            self.assertIn("Typed values applied", w.status_msg.get())
            self.assertEqual(str(w.tabs.tab(w.setup_tab, "state")), "hidden")  # still locked after applying
            self.drop("b2.csv", "S2")
            yield lambda: operator_of("b2_*.json") == {"Operator": "OP-99", "Work order": "WO-2"}
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual([m["value"] for m in saved["manual_inputs"]], ["OP-99", "WO-2"])
            self.assertTrue(saved["setup_password"].startswith("pbkdf2$"))  # saving kept the password
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class ShiftChange(WindowTest):
    """At each shift start the operator is asked to confirm the typed values; processing never waits."""

    def saved_values(self):
        saved = app.load_config(self.tmp / "data" / "config.json")
        return [m["value"] for m in saved["manual_inputs"]]

    def fill_form(self, w):
        w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
        w.man_vars[1][0].set("Work order"), w.man_vars[1][1].set("WO-1")
        w.v["shift_times"].set("06:00, 14:00, 22:00")

    def test_ask_once_later_click_confirm_and_next_shift(self):
        w = self.window
        clock = [datetime(2026, 10, 19, 13, 0)]
        w._now = lambda: clock[0]
        data = self.tmp / "data"

        def steps():
            self.fill_form(w)
            self.assertTrue(w.save())
            w._check_shift()
            self.assertEqual((w.shift_banner_shown, w.shift_dialog), (False, None))  # nothing is overdue yet

            clock[0] = datetime(2026, 10, 19, 14, 5)
            w._check_shift()
            self.assertTrue(w.shift_banner_shown)
            self.assertIn("14:00", w.shift_banner["text"])
            self.assertEqual([v.get() for v in w.shift_dialog.entries], ["OP-17", "WO-1"])

            w.shift_dialog.later()  # not now: the banner stays, and the operator is not asked again at once
            yield 100
            first_dialog = w.shift_dialog
            w._check_shift()
            self.assertTrue(w.shift_banner_shown)
            self.assertIs(w.shift_dialog, first_dialog)
            self.assertFalse(first_dialog.winfo_exists())

            w.shift_banner.event_generate("<Button-1>")  # a click on the banner opens it again
            w.update()
            self.assertTrue(w.shift_dialog.winfo_exists())
            w.shift_dialog.entries[0].set("OP-99")
            w.shift_dialog.confirm()
            yield 100
            self.assertFalse(w.shift_banner_shown)
            self.assertEqual(self.saved_values(), ["OP-99", "WO-1"])
            self.assertEqual(shifts.read_confirmed(data), datetime(2026, 10, 19, 14, 5))
            w._check_shift()
            self.assertFalse(w.shift_banner_shown)

            clock[0] = datetime(2026, 10, 19, 22, 0, 30)  # the next shift
            w._check_shift()
            self.assertTrue(w.shift_banner_shown and "22:00" in w.shift_banner["text"])
            w.shift_dialog.confirm()  # unchanged values: nothing to save
            yield 100
            self.assertFalse(w.shift_banner_shown)
            self.assertEqual(self.saved_values(), ["OP-99", "WO-1"])
            self.assertEqual(shifts.read_confirmed(data), datetime(2026, 10, 19, 22, 0, 30))
        self.run_steps(steps)

    def test_an_unanswered_shift_is_remembered_across_a_restart(self):
        w = self.window

        def steps():
            self.fill_form(w)
            self.assertTrue(w.save())
            yield 100
        self.run_steps(steps)
        shifts.write_confirmed(self.tmp / "data", datetime(2026, 10, 19, 13, 0))
        second = app.App(self.tmp / "data")
        try:
            second._now = lambda: datetime(2026, 10, 19, 14, 30)
            second._check_shift()
            self.assertTrue(second.shift_banner_shown)
            self.assertIn("14:00", second.shift_banner["text"])
            second.shift_dialog.later()
        finally:
            second.destroy()

    def test_bad_times_are_refused_and_changing_them_does_not_ask_for_the_past(self):
        w = self.window
        w._now = lambda: datetime(2026, 10, 19, 18, 0)

        def steps():
            self.fill_form(w)
            w.v["shift_times"].set("06:00, 25:00")
            self.assertFalse(w.save())
            self.assertIn("'25:00' is not a time", self.dialogs[-1][1][1])
            self.assertFalse((self.tmp / "data" / "config.json").exists())
            w.v["source_folder"].set(str(self.machine))
            self.assertIsNone(w._ready(quiet=True))  # starting is refused for the same reason
            w.v["shift_times"].set("06:00, 14:00")
            self.assertTrue(w.save())  # the 14:00 shift began before the times were set: nobody is asked
            self.assertFalse(w.shift_banner_shown)
            self.assertIsNone(w.shift_dialog)
            w.v["shift_times"].set("")  # switched off: never asks
            self.assertTrue(w.save())
            w._now = lambda: datetime(2026, 10, 20, 7, 0)
            w._check_shift()
            self.assertFalse(w.shift_banner_shown)
            yield 100
        self.run_steps(steps)


class MarkerInWindow(WindowTest):
    def test_file_waits_for_the_marker_and_the_banner_explains(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A1:B2")
            w.v["complete_marker"].set("End_Time")
            w.v["alert_after_minutes"].set("0")
            w.cfg["stable_seconds"] = 0.3
            self.assertTrue(w.save())
            w.start()
            yield lambda: w.running
            board = self.machine / "board.csv"
            board.write_text("Key,Value\nMachine,PNP-M8\n")
            yield lambda: w.alert_banner_shown and "does not contain 'End_Time'" in w.alert_banner["text"]
            self.assertTrue(board.exists())
            self.assertEqual(self.files(self.shared), [])
            with open(board, "a") as f:
                f.write("End_Time,2026-10-19 10:00:11" + chr(10))
            yield lambda: self.files(self.shared, "board_*.json") and not board.exists()
            yield lambda: not w.alert_banner_shown
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class OutputOptions(WindowTest):
    def test_switches_are_saved_and_shown_in_the_preview(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            yield 300
            self.assertIn('"X_mm": "40.53"', w.preview.get("1.0", "end"))  # text, as before

            w.b["json_numbers"].set(True)
            w.rename_text.insert("1.0", "Ref=Reference")
            w.refresh_preview()
            preview = w.preview.get("1.0", "end")
            self.assertIn('"X_mm": 40.53', preview)
            self.assertIn('"Reference"', preview)

            w.b["time_zone"].set(True)
            w.v["output_format"].set("csv")
            w.b["csv_flat"].set(True)
            w.refresh_preview()
            lines = w.preview.get("1.0", "end").splitlines()
            self.assertEqual(lines[0].split(",")[:3], ["source_file", "processed_at", "station"])
            self.assertRegex(lines[1], r"[+-]\d\d:\d\d,")

            self.assertTrue(w.save())
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual([saved[k] for k in ("json_numbers", "csv_flat", "time_zone", "rename_columns")],
                             [True, True, True, "Ref=Reference"])
            w.rename_text.insert("end", chr(10) + "no equals sign here")
            self.assertEqual(engine.validate(w.collect())[-1].split(":")[0], "Rename columns")
            yield 100
        self.run_steps(steps)


class HeadingPicker(WindowTest):
    def test_find_by_heading_in_the_picker(self):
        w = self.window

        def steps():
            self.assertTrue(w.open_sample(SAMPLE))
            w.tabs.select(w.pick_tab)
            w._update_slots()
            yield 300
            w.slot_box.current(0)
            w._slot_chosen()
            w.pick_heading.set(True)
            drag(w, 0, 47, 0, 47)  # the [ERRORS] title cell
            self.assertEqual(w.pick_range.get(), "@[ERRORS]")
            self.assertIn("found again by this text", w.pick_hint.get())
            w.pick_name.set("Errors")
            w.assign()
            self.assertEqual(w.sel_vars[0][1].get(), "@[ERRORS]")
            self.assertEqual(w.pick_hint.get(), "")
            yield 300
            self.assertIn('"cells": "A49:D50"', w.preview.get("1.0", "end"))
            self.assertIn('"Code"', w.preview.get("1.0", "end"))

            w.slot_box.current(1)
            w._slot_chosen()
            drag(w, 0, 11, 0, 11)  # Timestamp occurs in two header rows
            self.assertEqual(w.pick_range.get(), "@Timestamp")
            self.assertIn("appears 2 times", w.pick_hint.get())
            drag(w, 5, 30, 5, 30)  # an empty cell has no heading to find
            self.assertIn("is empty", w.pick_hint.get())
            w.pick_heading.set(False)
            w._update_pick_range()
            self.assertEqual(w.pick_range.get(), "F31")
            self.assertEqual(w.pick_hint.get(), "")

            cfg = w.collect()  # a heading range survives saving the settings
            self.assertEqual(cfg["selections"][0]["range"], "@[ERRORS]")
            self.assertTrue(w.save())
        self.run_steps(steps)


class Monitoring(WindowTest):
    def test_files_flow_and_faults_are_handled(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)
        data = self.tmp / "data"

        def queued_api():
            return engine.count_queued(data)["api"]

        def steps():
            w.v["station_name"].set("SMT Line 1")
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            for i, (name, rng) in enumerate((("Placements", "A12:I*"), ("Board serial", "B6"))):
                w.sel_vars[i][0].set(name), w.sel_vars[i][1].set(rng)
            w.man_vars[0][0].set("Operator"), w.man_vars[0][1].set("OP-17")
            w.cfg["stable_seconds"] = 0.3  # no field in the window; keeps the test fast
            w.start()
            yield lambda: w.running

            self.drop("b1.csv", "S1")
            self.drop("b2.csv", "S2")
            yield lambda: len(self.files(self.shared, "*.json")) == 2 and len(api.got) == 2
            self.assertEqual(self.files(self.machine), [])
            self.assertEqual(len(self.files(self.backup / f"{datetime.now():%Y-%m-%d}")), 2)
            doc = json.loads(self.files(self.shared, "b1_*.json")[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["manual_inputs"], {"Operator": "OP-17"})
            self.assertEqual(doc["selections"][1]["value"], "S1")

            api.status = 503  # the customer's system goes down
            self.drop("b3.csv", "S3")
            yield lambda: len(self.files(self.shared, "*.json")) == 3 and queued_api() == 1
            api.status = 200
            yield lambda: queued_api() == 0 and len(api.got) == 3

            self.drop("b1_again.csv", "S1")  # same content as b1
            yield lambda: (self.backup / "_duplicates" / "b1_again.csv").exists()
            (self.machine / "junk.csv").write_text("only,one,line\n")
            yield lambda: (self.backup / "_failed" / "junk.csv").exists()

            w.man_vars[0][1].set("OP-99")  # changed while running
            self.assertTrue(w.save())
            self.drop("b4.csv", "S4")
            yield lambda: self.files(self.shared, "b4_*.json")
            doc = json.loads(self.files(self.shared, "b4_*.json")[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["manual_inputs"], {"Operator": "OP-99"})

            yield lambda: w.snapshot and w.snapshot["ok"] == 4 and w.snapshot["failed"] == 1
            yield lambda: len(w.history.get_children()) == 6
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class AlertsInWindow(WindowTest):
    def test_banner_follows_the_problem_and_test_alert_reports(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            w.sel_vars[0][1].set("A12:I*")
            w.v["alert_after_minutes"].set("0")
            w.cfg["stable_seconds"] = 0.3
            api.status = 503
            w.start()
            self.drop("b1.csv", "S1")
            yield lambda: w.alert_banner_shown and "Delivery to the API link is not possible" in w.alert_banner["text"]
            yield lambda: any("needs attention" in title for title, *_ in self.notified)
            api.status = 200
            yield lambda: not w.alert_banner_shown
            yield lambda: any("working again" in title for title, *_ in self.notified)
            w.stop()
            yield lambda: not w.running

            self.dialogs.clear()
            w.test_alert()
            yield lambda: self.dialogs
            self.assertEqual(self.dialogs[0][1][1], "Windows notification: sent")
        self.run_steps(steps)


class ConnectedToBackground(WindowTest):
    """A real background process (CNCBridge --headless) and a window connected to it."""

    def make_window(self):
        import subprocess
        from cncbridge import background, config
        data = self.tmp / "data"
        config.save_config({
            "source_folder": str(self.machine), "backup_folder": str(self.backup), "shared_enabled": True,
            "shared_folder": str(self.shared), "selections": [{"name": "Board serial", "range": "B6"}],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}], "poll_seconds": 1, "stable_seconds": 0.3,
            "alert_notify": False}, data / "config.json")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "CNCBridge.pyw"), "--headless", "--data", str(data)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        self.addCleanup(lambda: self.process.poll() is None and self.process.kill())
        deadline = time.time() + 20
        while background.holder(data) != "background" and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(background.holder(data), "background")
        return app.App(data, connected=True)

    def operator_of(self, pattern):
        files = self.files(self.shared, pattern)
        return json.loads(files[0].read_text(encoding="utf-8"))["manual_inputs"]["Operator"] if files else None

    def test_window_shows_and_controls_the_background(self):
        w = self.window

        def steps():
            yield lambda: w.bg_running
            yield lambda: w.state_label["text"] == "In background"
            self.assertEqual(str(w.once_btn["state"]), "disabled")
            self.drop("b1.csv", "S1")
            yield lambda: self.operator_of("b1_*.json") == "OP-17"
            yield lambda: len(w.history.get_children()) == 1
            yield lambda: "[background]" in w.log_text.get("1.0", "end")

            w.man_vars[0][1].set("OP-99")  # saved by the window, picked up by the background
            self.assertTrue(w.save())
            yield 1500
            self.drop("b2.csv", "S2")
            yield lambda: self.operator_of("b2_*.json") == "OP-99"

            w.stop()
            yield lambda: self.process.poll() is not None
            yield lambda: not w.bg_running and w.state_label["text"] == "Background off"

            w.start()  # no background task is set up, so this window takes over
            yield lambda: w.running and not w.connected
            self.drop("b3.csv", "S3")
            yield lambda: self.operator_of("b3_*.json") == "OP-99"
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class HistoryActions(WindowTest):
    def test_send_again_and_process_again(self):
        w, api = self.window, Receiver()
        self.addCleanup(api.close)

        def row_id(source, status):
            return next(str(r["id"]) for r in w.shown_history if r["source"] == source and r["status"] == status)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.v["poll_seconds"].set("1")
            w.b["api_enabled"].set(True)
            w.v["api_url"].set(api.url)
            w.sel_vars[0][1].set("B6")
            w.v["alert_after_minutes"].set("60")
            w.cfg["stable_seconds"] = 0.3
            api.status = 422
            w.start()
            self.drop("b1.csv", "S1")
            (self.machine / "junk.csv").write_text("only,one,line\n")
            yield lambda: w.snapshot and w.snapshot["rejected"] == 1 and w.snapshot["failed"] == 1
            yield lambda: len(w.history.get_children()) == 2

            w.history.selection_set([row_id("b1.csv", "ok")])
            w.update()
            self.assertEqual((str(w.resend_btn["state"]), str(w.reprocess_btn["state"])), ("normal", "disabled"))
            api.status = 200
            w.resend_selected()
            yield lambda: len(api.got) == 1 and w.snapshot["rejected"] == 0

            w.history.selection_set([row_id("junk.csv", "failed")])
            w.update()
            self.assertEqual((str(w.resend_btn["state"]), str(w.reprocess_btn["state"])), ("disabled", "normal"))
            w.reprocess_selected()
            self.assertIn("put back in the machine folder", w.status_msg.get())
            yield lambda: w.snapshot["failed"] == 2  # still not a board log, so it is set aside again
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)


class OutputViewer(WindowTest):
    """History > View output: the copy kept in the back-up folder, shown as tables, as text, or opened."""

    def make_window(self):
        data = self.tmp / "data"
        config.save_config(config.normalize_config({
            "source_folder": str(self.machine), "backup_folder": str(self.backup), "shared_enabled": True,
            "shared_folder": str(self.shared), "manual_inputs": [{"name": "Operator", "value": "OP-17"}],
            "selections": [{"name": "Serial", "range": "B6", "header": True},
                           {"name": "Placements", "range": "A12:I*", "header": True}]}), data / "config.json")
        firstrun.mark_done(data)
        return app.App(data)

    def keep(self, fmt, name, day="2026-10-21"):
        """An output as the engine keeps it, and its history row."""
        w = self.window
        rows = engine.read_rows(SAMPLE)
        text, _, _ = engine.build_output("b.csv", rows, config.normalize_config(dict(w.cfg, output_format=fmt)))
        folder = self.backup / day / engine.OUTPUTS
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        w.ledger.add(processed_at=f"{day}T10:00:00", source="b.csv", output=name, status="ok", shared="ok",
                     api="off", ftp="off", message="")

    def row(self, name):
        return next(r for r in self.window.shown_history if r["output"] == name)

    def test_a_json_output_is_shown_as_tables_with_a_filter(self):
        w = self.window
        self.keep("json", "b_1.json")

        def steps():
            yield lambda: w.shown_history
            self.assertTrue(w.view_output(self.row("b_1.json")))
            yield lambda: w.output_win.state() == "normal"
            self.assertEqual(w.output_title.cget("text"), "b_1.json")
            self.assertIn("Operator: OP-17", w.output_info.cget("text"))
            self.assertIn("Machine file: b.csv", w.output_info.cget("text"))
            self.assertEqual(w.output_block.cget("values")[1], "2:  Placements  (12)")
            self.assertEqual(w.output_tree.item(w.output_tree.get_children()[0], "values")[0], "B240001")
            w.output_block.current(1)
            w.output_block.event_generate("<<ComboboxSelected>>")
            yield 50
            self.assertEqual(len(w.output_tree.get_children()), 12)
            self.assertEqual(w.output_tree.heading("c1", "text"), "Ref")
            w.output_filter.set("pickup")
            yield 50
            self.assertEqual([w.output_tree.set(i, "c1") for i in w.output_tree.get_children()], ["R102"])
            self.assertEqual(w.output_count.cget("text"), "1 of 12 rows")
        self.run_steps(steps)

    def test_csv_and_txt_outputs_and_a_pdf_that_opens_in_the_reader(self):
        w = self.window
        self.keep("csv", "b_2.csv")
        self.keep("txt", "b_3.txt")
        self.keep("pdf", "b_4.pdf")

        def steps():
            yield lambda: len(w.shown_history) == 3
            w.view_output(self.row("b_2.csv"))
            yield 50
            self.assertEqual(w.output_block.cget("values")[1], "2:  Placements  (12)")
            self.assertTrue(w.output_tables.winfo_manager())
            w.view_output(self.row("b_3.txt"))
            yield 50
            self.assertIn("=== Placements  [A12:I*] ===", w.output_text.get("1.0", "end"))
            self.assertFalse(w.output_tables.winfo_manager())
            w.view_output(self.row("b_4.pdf"))
            yield 50
            self.assertIn("A PDF report of 1 page(s)", w.output_pdf_note.cget("text"))
            with mock.patch("os.startfile", create=True) as opened:
                w.open_output_file()
            opened.assert_called_once_with(self.backup / "2026-10-21" / "outputs" / "b_4.pdf")
            target = self.tmp / "copy.pdf"
            with mock.patch.object(filedialog, "asksaveasfilename", return_value=str(target)):
                w.save_output_copy()
            self.assertEqual(target.read_bytes()[:4], b"%PDF")
        self.run_steps(steps)

    def test_view_output_follows_the_selection_in_history(self):
        w = self.window
        self.keep("json", "b_5.json")
        w.ledger.add(processed_at="2026-10-21T11:00:00", source="junk.csv", status="failed", message="no data")

        def steps():
            yield lambda: len(w.history.get_children()) == 2
            failed = next(str(r["id"]) for r in w.shown_history if r["status"] == "failed")
            w.history.selection_set([failed])
            w.update()
            self.assertEqual(str(w.view_output_btn["state"]), "disabled")  # it made no output
            ok = str(self.row("b_5.json")["id"])
            w.history.selection_set([ok])
            w.update()
            self.assertEqual(str(w.view_output_btn["state"]), "normal")
            w.view_output_btn.invoke()
            yield lambda: w.output_win.state() == "normal"
        self.run_steps(steps)

    def test_an_output_that_is_not_kept_says_so(self):
        w = self.window
        w.ledger.add(processed_at="2026-01-02T10:00:00", source="old.csv", output="old_1.json", status="ok",
                     shared="ok", api="off", ftp="off", message="")

        def steps():
            yield lambda: w.shown_history
            self.assertFalse(w.view_output(self.row("old_1.json")))
            self.assertEqual(self.dialogs[-1][0], "showinfo")
            self.assertIn("is not kept any more", self.dialogs[-1][1][1])
        self.run_steps(steps)


class SettingsFiles(WindowTest):
    def test_export_import_and_restore(self):
        w = self.window
        exported = self.tmp / "exported.json"

        def steps():
            w.v["station_name"].set("Line 7")
            w.v["api_token"].set("secret-token")
            w.sel_vars[0][1].set("L1:M8")
            w.save()
            self.assertEqual(len(self.files(self.tmp / "data" / "backups", "config-*.json")), 1)
            self._patch(filedialog, "asksaveasfilename", lambda **k: str(exported))
            w.export_settings()
            self.assertNotIn("secret-token", exported.read_text(encoding="utf-8"))
            w.v["station_name"].set("CHANGED")
            w.sel_vars[0][1].set("")
            self._patch(filedialog, "askopenfilename", lambda **k: str(exported))
            w.import_settings()
            self.assertEqual((w.v["station_name"].get(), w.sel_vars[0][1].get(), w.v["api_token"].get()),
                             ("Line 7", "L1:M8", "secret-token"))
            self.assertIn("press Save settings", w.status_msg.get())
            yield 100
        self.run_steps(steps)


class Appearance(WindowTest):
    def catch_restarts(self, launched, fail=None):
        """Record the command that restarts the program instead of starting it; other programs run as usual."""
        real = app.subprocess.Popen

        def popen(command, *a, **k):
            if "--restarted" not in command:
                return real(command, *a, **k)
            if fail:
                raise fail
            launched.append((command, k))
        self._patch(app.subprocess, "Popen", popen)

    def test_choose_a_theme_save_and_restart(self):
        w = self.window
        launched = []
        self.catch_restarts(launched)

        def steps():
            self.assertEqual(w.theme.key, "modern")
            for key in app.theme.THEMES:  # one radio button per theme, found by the registry
                self.assertIn(f"theme_{key}", w.field_widgets)
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")
            self.assertIn("in use", w.theme_note.get())

            w.v["theme"].set("legacy")
            w._update_theme_note()
            self.assertEqual(str(w.restart_btn.cget("state")), "normal")
            self.assertIn("Save settings", w.theme_note.get())

            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.restart()  # saves first, then starts the program again and closes this window
            yield 100
        self.run_steps(steps)
        saved = app.load_config(self.tmp / "data" / "config.json")
        self.assertEqual(saved["theme"], "legacy")
        self.assertEqual(len(launched), 1)
        command = launched[0][0]
        self.assertIn("--restarted", command)
        self.assertNotIn("--resume", command)  # it was not monitoring
        self.assertEqual(command[command.index("--data") + 1], str(self.tmp / "data"))
        self.assertEqual(launched[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_a_restart_while_monitoring_resumes_monitoring(self):
        w = self.window
        launched = []
        self.catch_restarts(launched)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.start()
            yield lambda: w.running
            w.restart()
            self.assertFalse(w.running)  # the engine stopped before the new window takes over
            yield 100
        self.run_steps(steps)
        self.assertIn("--resume", launched[0][0])

    def test_a_failed_restart_keeps_the_window_open(self):
        w = self.window

        self.catch_restarts([], fail=OSError("no such program"))

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            w.restart()
            self.assertTrue(w.winfo_exists())
            self.assertEqual(self.dialogs[-1][0], "showerror")
            yield 100
        self.run_steps(steps)


class LegacyWindow(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "legacy"}), encoding="utf-8")
        return app.App(data)

    def test_the_window_opens_with_the_legacy_look(self):
        w = self.window

        def steps():
            self.assertEqual(w.theme.key, "legacy")
            self.assertEqual(w.v["theme"].get(), "legacy")
            self.assertEqual(app.theme.BACKGROUND, app.theme.LEGACY.colours["BACKGROUND"])
            self.assertNotEqual(app.ttk.Style(w).theme_use(), "clam")
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")  # already in use
            for tab in (w.tabs.tabs()):  # every tab builds and shows in this theme
                w.tabs.select(tab)
                w.update()
            yield 100
        self.run_steps(steps)


def page_titles(widget):
    """The page-title labels (one at the top of each tab in themes that have them), found anywhere in the window."""
    found = [widget] if str(widget.winfo_class()) == "TLabel" and widget.cget("style") == "PageTitle.TLabel" else []
    for child in widget.winfo_children():
        found += page_titles(child)
    return found


class LegacyLayout(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "legacy"}), encoding="utf-8")
        return app.App(data)

    def test_version_1_layout(self):
        w = self.window

        def steps():
            self.assertEqual(page_titles(w), [])  # version 1.0 had no page titles
            self.assertEqual(w.counts["ok"].cget("style"), "Count.TLabel")  # a bare number, not a card
            self.assertEqual(str(w.field_widgets["selection_header_0"].cget("text")), "")
            self.assertEqual(w.code_font, {})  # short codes in the normal font
            self.assertEqual(w.grid_view.font.cget("size"), 10)  # Consolas 10, as in version 1.0
            self.assertEqual(w.L.window, (1180, 740, 980, 620))
            yield 100
        self.run_steps(steps)


class ModernLayout(WindowTest):
    def test_titles_cards_and_scrollbar(self):
        w = self.window

        def steps():
            self.assertEqual(len(page_titles(w)), 5)  # Setup, Delivery, Typed values, Pick, History
            self.assertEqual(w.counts["ok"].cget("style"), "MetricValue.TLabel")
            self.assertEqual(str(w.field_widgets["selection_header_0"].cget("text")), "Yes")
            self.assertIn("font", w.code_font)
            # the counters really are larger than the captions: a style font is not overridden by the defaults
            self.assertGreater(w.counts["ok"].winfo_reqheight(), 30)
            # the scrollbar shows only while the tabs are taller than the window
            real = w.tabs_frame.winfo_reqheight
            w.tabs_frame.winfo_reqheight = lambda: 50
            w._tabs_content_changed()
            w.update()
            self.assertFalse(w.tabs_scrollbar.winfo_ismapped())
            w.tabs_frame.winfo_reqheight = real
            w._tabs_content_changed()
            w.update()
            self.assertTrue(w.tabs_scrollbar.winfo_ismapped())
            # and the tabs are never wider than the window, however wide a tab wants to be
            self.assertEqual(w.tabs_frame.winfo_width(), w.tabs_canvas.winfo_width())
            yield 100
        self.run_steps(steps)


class UnknownTheme(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        data.mkdir()
        (data / "config.json").write_text(json.dumps({"theme": "from-the-future"}), encoding="utf-8")
        return app.App(data)

    def test_falls_back_to_the_default_theme(self):
        def steps():
            self.assertEqual(self.window.theme.key, "modern")
            yield 100
        self.run_steps(steps)


class MenuBar(WindowTest):
    def fill_valid(self):
        w = self.window
        w.v["source_folder"].set(str(self.machine))
        w.v["backup_folder"].set(str(self.backup))
        w.b["shared_enabled"].set(True)
        w.v["shared_folder"].set(str(self.shared))
        w.sel_vars[0][1].set("A12:I14")

    def test_menus_and_the_working_area(self):
        w = self.window

        def steps():
            self.assertEqual(list(w.menubar_buttons), ["File", "Monitoring", "Settings", "Tools", "View", "Help"])
            self.assertEqual(w.cget("menu"), "")  # the bar is drawn by the program, so it can be larger
            drop_down = w.menus["File"]
            self.assertGreaterEqual(tkfont.Font(font=drop_down.cget("font")).actual("size"), 11)  # native bar: 9
            self.assertGreater(w.menubar_buttons["File"].winfo_reqheight(), w.px(28))
            shown = [w.tabs.tab(t, "text").strip() for t in w.tabs.tabs() if str(w.tabs.tab(t, "state")) != "hidden"]
            self.assertEqual(shown, ["Setup", "Delivery", "Pick ranges & preview", "History"])
            for gone in ("settings_btn", "open_csv_btn", "open_folders_btn", "simulation_btn", "lock_btn"):
                self.assertFalse(hasattr(w, gone), gone)  # the toolbar holds only the daily buttons
            self.assertEqual(str(w.start_btn["state"]), "normal")
            self.assertEqual(self.menu_state("start"), "normal")
            self.assertEqual(self.menu_state("stop"), "disabled")
            yield 100
        self.run_steps(steps)

    def test_every_setting_is_in_the_options_window(self):
        w = self.window

        def steps():
            pages = {key: str(page) for key, page in w.option_pages.items()}
            self.assertEqual(list(pages), ["processing", "output", "alerts", "heartbeat", "access", "appearance",
                                           "materials"])  # M2-materials
            for key in ("autostart", "skip_duplicates", "poll_seconds", "encoding", "delimiter", "complete_marker",
                        "run_in_background", "keep_backup_days", "json_numbers", "csv_flat", "time_zone",
                        "rename_columns", "alert_notify", "alert_email_to", "test_alert", "heartbeat_url", "test_heartbeat",
                        "protect_settings", "password_accounts", "restart_admin", "shift_times",
                        "change_admin_password", "theme_modern", "theme_legacy", "restart_now"):
                widget = w.field_widgets[key]
                self.assertTrue(str(widget).startswith(str(w.options_win)), key)  # in Options, not in a tab
            for key in ("station_name", "source_folder", "output_format_json", "selection_range_0", "shared_enabled",
                        "api_url", "test_api"):
                self.assertTrue(str(w.field_widgets[key]).startswith(str(w.tabs)), key)  # in the working area
            yield 100
        self.run_steps(steps)

    def test_menu_opens_the_page_and_save_writes_the_change(self):
        w = self.window

        def steps():
            self.fill_valid()
            self.assertEqual(w.options_win.state(), "withdrawn")
            menu, index = w.menu_items["opt_alerts"]
            menu.invoke(index)
            w.update()
            self.assertEqual(w.options_win.state(), "normal")
            self.assertEqual(w.options_tabs.select(), str(w.option_pages["alerts"]))
            w.v["alert_after_minutes"].set("25")
            w.options_save_btn.invoke()
            self.assertEqual(app.load_config(self.tmp / "data" / "config.json")["alert_after_minutes"], 25)
            menu, index = w.menu_items["opt_appearance"]
            menu.invoke(index)
            self.assertEqual(w.options_tabs.select(), str(w.option_pages["appearance"]))
            w.close_options()
            self.assertEqual(w.options_win.state(), "withdrawn")
            yield 100
        self.run_steps(steps)

    def test_shortcuts_follow_the_menu_entries(self):
        w = self.window

        def steps():
            self.fill_valid()
            yield lambda: focused(w)
            w.update()
            w.event_generate("<Control-comma>")
            w.update()
            self.assertEqual(w.options_win.state(), "normal")
            w.close_options()
            w.event_generate("<F5>")
            yield lambda: w.running
            yield lambda: self.menu_state("start") == "disabled"  # the menu follows the run state at the next refresh
            self.assertEqual((self.menu_state("start"), self.menu_state("stop")), ("disabled", "normal"))
            yield lambda: focused(w)
            w.event_generate("<Shift-F5>")
            yield lambda: not w.running
            w.event_generate("<Control-Key-2>")
            w.update()
            self.assertEqual(w.tabs.select(), str(w.delivery_tab))
            w.event_generate("<Control-Key-4>")
            w.update()
            self.assertEqual(w.tabs.select(), str(w.history_tab))
            w.event_generate("<Control-s>")
            w.update()
            self.assertIn("Settings saved", w.status_msg.get())
        self.run_steps(steps)

    def test_a_locked_window_ignores_the_setup_shortcuts(self):
        w = self.window

        def steps():
            w._store_admin_password("window-test-password")
            w.unlocked = False  # as after a restart: the password is set and not yet entered
            w._apply_lock()
            w.update()
            self.assertEqual(self.menu_state("options"), "disabled")
            yield lambda: focused(w)
            w.event_generate("<Control-comma>")
            w.event_generate("<Control-s>")
            w.update()
            self.assertEqual(w.options_win.state(), "withdrawn")
            self.assertNotIn("Settings saved", w.status_msg.get())
            yield 100
        self.run_steps(steps)


class LogAndHelp(WindowTest):
    def test_the_log_window_keeps_filling_while_hidden(self):
        w = self.window

        def steps():
            self.assertEqual(w.log_win.state(), "withdrawn")
            app.log.info("a message written while the log is hidden")
            yield lambda: "written while the log is hidden" in w.log_text.get("1.0", "end")
            menu, index = w.menu_items["log"]
            menu.invoke(index)
            w.update()
            self.assertEqual(w.log_win.state(), "normal")
            w.log_win.withdraw()
        self.run_steps(steps)

    def test_every_help_topic_shows_and_f1_opens_getting_started(self):
        w = self.window

        def steps():
            self.assertEqual(w.help_win.state(), "withdrawn")
            for key, title, _ in w.help_topic_data:  # M2-materials
                w.open_help(key)
                w.update()
                shown = w.help_body.get("1.0", "end")
                self.assertTrue(shown.startswith(title), key)
                self.assertGreater(len(shown), 150, key)
            self.assertIn("F5", shown)  # the last topic is the shortcut list
            self.assertIn("Ctrl+S", shown)
            w.help_win.withdraw()
            self.assertTrue(press(w, "<F1>", lambda: w.help_win.state() == "normal"))
            self.assertEqual(w.help_win.state(), "normal")
            self.assertTrue(w.help_body.get("1.0", "end").startswith("Getting started"))
            menu, index = w.menu_items["help_ranges"]
            menu.invoke(index)
            self.assertTrue(w.help_body.get("1.0", "end").startswith("Choosing cell ranges"))
            self._patch(app.messagebox, "showinfo", lambda title, text, **k: self.dialogs.append((title, text)))
            w.about()
            self.assertIn(app.VERSION, self.dialogs[-1][1])
            yield 100
        self.run_steps(steps)


class WindowWithSettings(WindowTest):
    """A window started with the settings in `settings`, written before it opens (a second window per test is avoided)."""
    settings = {}

    def make_window(self):
        data = self.tmp / "data"
        data.mkdir(exist_ok=True)
        (data / "config.json").write_text(json.dumps(self.settings), encoding="utf-8")
        return app.App(data)


class ModernDarkWindow(WindowWithSettings):
    settings = {"theme": "modern", "color_mode": "dark"}

    def test_modern_dark(self):
        w = self.window

        def steps():
            self.assertTrue(w.theme.dark)
            self.assertEqual(app.theme.BACKGROUND, app.theme.MODERN.dark_colours["BACKGROUND"])
            self.assertEqual(w.cget("background"), app.theme.MODERN.dark_colours["BACKGROUND"])
            self.assertEqual(app.ttk.Style(w).lookup("TLabel", "foreground"), app.theme.MODERN.dark_colours["TEXT"])
            self.assertEqual(w.appearance_in_use["color_mode"], "dark")
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")  # the look in use
            w.tabs.select(w.pick_tab)
            self.assertTrue(w.open_sample(SAMPLE))
            w.update()
            canvas = w.grid_view.canvas
            fills = {canvas.itemcget(i, "fill") for i in canvas.find_all() if canvas.type(i) == "text"}
            self.assertIn(app.theme.MODERN.dark_colours["TEXT"], fills)  # cell text is light on the dark grid
            self.assertNotIn("black", fills)
            yield 100
        self.run_steps(steps)


class LegacyDarkWindow(WindowWithSettings):
    settings = {"theme": "legacy", "color_mode": "dark"}

    def test_legacy_dark_inverts_the_legacy_colours(self):
        w = self.window

        def steps():
            self.assertTrue(w.theme.dark)
            self.assertEqual(app.ttk.Style(w).theme_use(), "alt")
            self.assertEqual(app.theme.BACKGROUND, app.theme.invert_colour(app.theme.LEGACY.colours["BACKGROUND"]))
            self.assertEqual(w.cget("background"), app.theme.invert_colour("#f0f0f0"))
            self.assertEqual(w.L.window, (1180, 740, 980, 620))  # the 1.0 layout, as in light
            for tab in w.tabs.tabs():  # every tab builds in this mode
                w.tabs.select(tab)
                w.update()
            yield 100
        self.run_steps(steps)


class ScaledWindow(WindowWithSettings):
    settings = {"theme": "modern", "font_family": "Arial", "font_size": 130, "ui_scale": 125}

    def test_text_size_scale_and_font(self):
        w = self.window

        def steps():
            self.assertEqual((w.text_scale, w.font_families["body"], w.font_families["heading"]), (1.3, "Arial", "Arial"))
            dpi = w.winfo_fpixels("1i") / 96
            self.assertAlmostEqual(dpi / w.scale, 1.3, places=2)  # the text carries both factors, spacing only the scale
            rows = int(app.ttk.Style(w).lookup("History.Treeview", "rowheight"))
            self.assertGreaterEqual(rows, tkfont.Font(font=("Roboto Mono", 11)).metrics("linespace") + w.px(8))
            self.assertEqual(w._chosen_appearance(), {"theme": "modern", "color_mode": "light", "font_family": "Arial",
                                                      "font_size": 130, "ui_scale": 125, "language": "en"})
            yield 100
        self.run_steps(steps)


class BadAppearanceWindow(WindowWithSettings):
    settings = {"theme": "modern", "color_mode": "purple", "font_family": "No Such Font", "font_size": 7,
                "ui_scale": 3}

    def test_bad_appearance_values_fall_back(self):
        w = self.window

        def steps():
            self.assertFalse(w.theme.dark)
            self.assertEqual((w.text_scale, w.scale / (w.winfo_fpixels("1i") / 96)), (1.0, 1.0))
            self.assertEqual(w.font_families["body"], "Roboto")  # an unknown family is ignored: the theme's own
            yield 100
        self.run_steps(steps)


class AppearanceVariants(WindowTest):
    def test_appearance_page_note_follows_every_choice(self):
        w = self.window

        def steps():
            self.assertEqual(str(w.restart_btn.cget("state")), "disabled")
            for key, value in (("color_mode", "dark"), ("font_family", "Arial"),
                               ("font_size", app.FONT_SIZE_LABELS[115]), ("ui_scale", app.UI_SCALE_LABELS[150])):
                before = w.v[key].get()
                w.v[key].set(value)
                w._update_theme_note()
                self.assertEqual(str(w.restart_btn.cget("state")), "normal", key)
                self.assertIn("Save settings", w.theme_note.get(), key)
                w.v[key].set(before)
                w._update_theme_note()
                self.assertEqual(str(w.restart_btn.cget("state")), "disabled", key)
            w.v["theme"].set("legacy")
            w.v["color_mode"].set("dark")
            w._update_theme_note()
            self.assertIn("inverts", w.mode_note.get())
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            self.assertTrue(w.save())
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual((saved["theme"], saved["color_mode"]), ("legacy", "dark"))
            self.assertIn("Saved", w.theme_note.get())  # and it now waits for a restart
            self.assertEqual(str(w.restart_btn.cget("state")), "normal")
            yield 100
        self.run_steps(steps)

    def test_font_choices_are_installed_ui_fonts(self):
        w = self.window

        def steps():
            choices = app.theme.installed_font_choices(w)
            self.assertTrue(set(choices) <= set(app.theme.FONT_CHOICES))
            self.assertIn("Segoe UI", choices)
            self.assertEqual(list(w.field_widgets["font_family"]["values"]), [app.DEFAULT_FONT_LABEL] + choices)
            yield 100
        self.run_steps(steps)

    def test_alt_keys_and_f10_open_the_menus(self):
        w = self.window
        opened = []
        self._patch(app.App, "_open_menu", lambda self, button: opened.append(button["text"]))

        def steps():
            yield lambda: focused(w)
            w.update()
            for letter, title in (("f", "File"), ("m", "Monitoring"), ("s", "Settings"), ("t", "Tools"),
                                  ("v", "View"), ("h", "Help")):
                w.event_generate(f"<Alt-{letter}>")
                w.update()
                self.assertEqual(opened[-1], title)
            w.event_generate("<F10>")
            w.update()
            self.assertEqual(opened[-1], "File")
            yield 100
        self.run_steps(steps)


class MenuNavigation(WindowTest):
    """The drop-downs are drawn by the program: Left/Right between menus, hover switching, submenus, no blocking."""

    def label(self, dropdown):
        return dropdown.menu.entrycget(dropdown.rows[dropdown.selected]["index"], "label")

    def test_keyboard_opens_with_the_first_entry_and_left_right_change_menu(self):
        w = self.window

        def steps():
            w._open_menu(w.menubar_buttons["File"])
            w.update()
            self.assertTrue(w._dropdown.exists())
            self.assertEqual(self.label(w._dropdown), "Open CSV file...")
            for title in ("Monitoring", "Settings", "Tools", "View", "Help", "File"):  # Right, wrapping round
                w._dropdown.press("Right")
                w.update()
                self.assertEqual(w._dropdown_button["text"], title)
            w._dropdown.press("Left")
            w.update()
            self.assertEqual(w._dropdown_button["text"], "Help")
            w._dropdown.press("Escape")
            self.assertIsNone(w._dropdown)
            yield 100
        self.run_steps(steps)

    def test_up_down_skip_disabled_entries_and_enter_runs_the_entry(self):
        w = self.window

        def steps():
            w._set_menu("open_newest", False)
            w._open_menu(w.menubar_buttons["File"])
            w.update()
            dd = w._dropdown
            dd.press("Down")
            self.assertEqual(self.label(dd), "Save settings")  # "Open newest machine file" is disabled
            dd.press("Up")
            self.assertEqual(self.label(dd), "Open CSV file...")
            dd.press("Up")  # wraps to the last entry
            self.assertEqual(self.label(dd), "Exit")
            w._set_menu("open_newest", True)
            w._close_menus()
            w._open_menu(w.menubar_buttons["View"])
            w.update()
            self.assertEqual(self.label(w._dropdown), "Activity log")
            w._dropdown.press("Return")
            w.update()
            self.assertIsNone(w._dropdown)
            self.assertEqual(w.log_win.state(), "normal")
            w.log_win.withdraw()
            yield 100
        self.run_steps(steps)

    def test_a_disabled_entry_does_nothing_when_clicked(self):
        w = self.window

        def steps():
            w._set_menu("export", False)
            w._open_menu(w.menubar_buttons["File"])
            w.update()
            dd = w._dropdown
            position = next(i for i, r in enumerate(dd.rows) if dd.menu.entrycget(r["index"], "label") ==
                            "Export settings...")
            dd.activate(position)
            self.assertIs(w._dropdown, dd)  # still open, nothing ran
            w._close_menus()
            yield 100
        self.run_steps(steps)

    def test_pointer_on_another_title_switches_and_clicks_open_and_close(self):
        w = self.window

        def steps():
            w._click_title(w.menubar_buttons["File"])
            w.update()
            self.assertEqual(w._dropdown_button["text"], "File")
            w._hover_menu(w.menubar_buttons["Help"])
            w.update()
            self.assertEqual(w._dropdown_button["text"], "Help")
            self.assertEqual(w._dropdown.menu, w.menus["Help"])
            w._hover_menu(w.menubar_buttons["Help"])  # the same title: nothing changes
            self.assertEqual(w._dropdown_button["text"], "Help")
            w._click_title(w.menubar_buttons["Help"])  # a second click closes
            self.assertIsNone(w._dropdown)
            w._hover_menu(w.menubar_buttons["File"])  # with no menu open, hovering opens nothing
            self.assertIsNone(w._dropdown)
            w._click_title(w.menubar_buttons["File"])
            w._click_outside_menus(w.tabs)  # a click elsewhere closes it
            self.assertIsNone(w._dropdown)
            w._click_title(w.menubar_buttons["File"])
            w._click_outside_menus(w.menubar_buttons["Tools"])  # on another title: that menu opens
            self.assertEqual(w._dropdown_button["text"], "Tools")
            w._close_menus()
            yield 100
        self.run_steps(steps)

    def test_submenu_opens_with_right_closes_with_left_and_runs_its_entry(self):
        w = self.window
        opened = []
        self._patch(app.os, "startfile", lambda folder: opened.append(str(folder)))

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w._open_menu(w.menubar_buttons["File"])
            w.update()
            dd = w._dropdown
            position = next(i for i, r in enumerate(dd.rows) if r["kind"] == "cascade")
            dd.selected = position
            dd.press("Right")
            w.update()
            self.assertIsNotNone(dd.child)
            self.assertEqual(self.label(dd.child), "Machine folder")
            dd.child.press("Left")  # back to the parent menu, which stays open
            self.assertIsNone(dd.child)
            self.assertIs(w._dropdown, dd)
            dd.press("Right")
            dd.child.press("Escape")  # Esc closes one level at a time
            self.assertIsNone(dd.child)
            self.assertIs(w._dropdown, dd)
            dd.press("Right")
            dd.child.press("Return")
            self.assertEqual(opened, [str(self.machine)])
            self.assertIsNone(w._dropdown)
            yield 100
        self.run_steps(steps)

    def test_an_open_menu_never_blocks_the_window(self):
        w = self.window
        ticks = []

        def steps():
            w._open_menu(w.menubar_buttons["Settings"])
            w.after(50, lambda: ticks.append(1))
            yield lambda: ticks  # the event loop keeps running while the menu is open
            w._close_menus()
        self.run_steps(steps)


class HealthLine(WindowTest):
    def test_the_line_under_the_counters_follows_the_facts(self):
        w = self.window

        def steps():
            day = 86400
            w._show_health({"last_file": "2026-10-19T10:42:05", "last_file_age": 180, "oldest_queued_age": None,
                            "backup_free": 120 * 1024 ** 3, "disk_level": "ok", "source_reachable": True})
            texts = [w.health_labels[k].cget("text") for k in ("last", "queue", "disk", "source")]
            self.assertEqual(texts[0], "Last file 10:42 (3 min ago)")
            self.assertIn("Queue empty", texts[1])
            self.assertIn("Back-up drive 120.0 GB free", texts[2])
            self.assertEqual(w.health_labels["source"].winfo_manager(), "")  # only shown when there is a problem
            self.assertEqual(str(w.health_labels["queue"].cget("foreground")), app.theme.MUTED)

            w.cfg = dict(w.cfg, alert_after_minutes=10)
            w._show_health({"last_file": None, "last_file_age": None, "oldest_queued_age": 700, "backup_free": 1.5 * 1024 ** 3,
                            "disk_level": "low", "source_reachable": False})
            self.assertEqual(w.health_labels["last"].cget("text"), "No files yet")
            self.assertEqual(str(w.health_labels["queue"].cget("foreground")), app.theme.WARNING)  # past 10 minutes
            self.assertEqual(str(w.health_labels["disk"].cget("foreground")), app.theme.WARNING)
            self.assertEqual(str(w.health_labels["source"].cget("foreground")), app.theme.ERROR)
            self.assertIn("not reachable", w.health_labels["source"].cget("text"))
            w._show_health({"oldest_queued_age": 2 * day, "backup_free": 0.3 * 1024 ** 3, "disk_level": "critical"})
            self.assertEqual(str(w.health_labels["queue"].cget("foreground")), app.theme.ERROR)
            self.assertIn("2 days", w.health_labels["queue"].cget("text"))
            self.assertEqual(str(w.health_labels["disk"].cget("foreground")), app.theme.ERROR)
            order = [str(c) for c in w.health_bar.pack_slaves()]
            self.assertEqual(order, [str(w.health_labels[k]) for k in ("last", "queue", "disk")])  # in order, no source
            yield 100
        self.run_steps(steps)

    def test_the_stats_thread_delivers_it(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            yield lambda: (w.snapshot or {}).get("health") is not None
            self.assertTrue(w.snapshot["health"]["source_reachable"] in (True, None))
            yield lambda: "Last file" in w.health_labels["last"].cget("text") or "No files" in w.health_labels["last"].cget("text")
        self.run_steps(steps)


class StationsWindow(WindowTest):
    def test_lists_every_copy_and_opens_or_removes_one(self):
        w = self.window
        registry = self.tmp / "stations.json"
        self._patch(stations, "registry_path", lambda: registry)  # never the real list of this PC
        launched = []
        real = app.subprocess.Popen
        self._patch(app.subprocess, "Popen", lambda command, *a, **k: launched.append(command)
                    if "--data" in command else real(command, *a, **k))
        a = make_copy(self.tmp, "LineA", station="Line A", processed=4, failed=1, problem="API cannot be reached")
        b = make_copy(self.tmp, "LineB", station="Line B", mode="background")
        gone = self.tmp / "LineGone"
        for folder in (a, b, gone):
            stations.register(folder, registry)

        def steps():
            self.assertEqual((self.menu_label("stations"), self.menu_state("stations")), ("Stations...", "normal"))
            w.open_stations()
            w.update()
            self.assertEqual(w.stations_win.state(), "normal")
            yield lambda: len(w.stations_tree.get_children()) == 4
            tree = w.stations_tree
            values = {Path(iid).name: tree.item(iid, "values") for iid in tree.get_children()}
            self.assertIn("this window", values["data"][0])
            self.assertEqual((values["LineA"][0], values["LineA"][2], values["LineA"][3], values["LineA"][4]),
                             ("Line A", "Stopped", "4", "1"))
            self.assertEqual(values["LineA"][-1], "API cannot be reached")
            self.assertEqual(values["LineB"][2], "In background")
            self.assertEqual(values["LineGone"][2], "Missing")
            self.assertTrue(values["LineA"][-2].endswith("GB"))  # back-up drive free space

            tree.selection_set(str(a))
            w.update()
            self.assertEqual(str(w.station_buttons["open"]["state"]), "normal")
            w.open_station()
            self.assertEqual(launched[-1][-2:], ["--data", str(a)])
            tree.selection_set(str(w.data_dir))  # this window cannot be opened again
            w.update()
            self.assertEqual(str(w.station_buttons["open"]["state"]), "disabled")
            tree.selection_set(str(gone))
            w.update()
            self.assertEqual((str(w.station_buttons["open"]["state"]), str(w.station_buttons["data"]["state"]),
                              str(w.station_buttons["remove"]["state"])), ("disabled", "disabled", "normal"))
            w.remove_station()
            yield lambda: len(w.stations_tree.get_children()) == 3
            self.assertEqual(len(stations.load(registry)), 2)
            w.close_stations()
            self.assertEqual(w.stations_win.state(), "withdrawn")
        self.run_steps(steps)

    def test_adding_a_folder_without_data_is_refused(self):
        w = self.window
        registry = self.tmp / "stations.json"
        self._patch(stations, "registry_path", lambda: registry)
        empty = self.tmp / "empty"
        empty.mkdir()
        answers = [str(empty), str(make_copy(self.tmp, "LineC", station="C"))]
        self._patch(app.filedialog if hasattr(app, "filedialog") else filedialog, "askdirectory",
                    lambda **k: answers.pop(0))

        def steps():
            w.open_stations()
            w.add_station()
            self.assertEqual(self.dialogs[-1][0], "showinfo")
            self.assertEqual(stations.load(registry), [])
            w.add_station()
            self.assertEqual([Path(e["folder"]).name for e in stations.load(registry)], ["LineC"])
            w.close_stations()
            yield 100
        self.run_steps(steps)


class SettingsHistoryWindow(WindowTest):
    def test_a_save_appears_in_the_history_and_the_filter_narrows_it(self):
        w = self.window

        def steps():
            self.assertEqual((self.menu_label("settings_history"), self.menu_state("settings_history")),
                             ("Settings history...", "normal"))
            w.v["station_name"].set("Line 9")
            w.v["source_folder"].set(str(self.machine))
            self.assertTrue(w.save())
            w.open_settings_history()
            w.update()
            self.assertEqual(w.history_win.state(), "normal")
            tree = w.history_tree
            self.assertEqual(len(tree.get_children()), 1)
            when, who, how, what = tree.item(tree.get_children()[0], "values")
            self.assertEqual(how, "Saved")
            self.assertTrue(who)
            self.assertIn("Station name: (empty) → Line 9", what)
            tree.selection_set(tree.get_children()[0])
            w.update()
            detail = w.history_detail.get("1.0", "end")
            self.assertIn("Machine folder: (empty) → " + str(self.machine), detail)
            w.history_filter.set("nothing like this")
            w.update()
            self.assertEqual(len(tree.get_children()), 0)
            w.history_filter.set("line 9")
            w.update()
            self.assertEqual(len(tree.get_children()), 1)
            w.history_win.withdraw()
            yield 100
        self.run_steps(steps)

    def test_a_password_change_is_listed_without_the_password(self):
        w = self.window

        def steps():
            w._store_admin_password("window-test-password")
            w.open_settings_history()
            w.update()
            tree = w.history_tree
            values = tree.item(tree.get_children()[0], "values")
            self.assertEqual(values[2], "Password")
            self.assertIn("Setup password: not set → set", values[3])
            self.assertNotIn("opensesame", audit.log_path(w.data_dir).read_text(encoding="utf-8"))
            yield 100
        self.run_steps(steps)

    def test_importing_settings_is_logged_as_an_import_once_saved(self):
        w = self.window
        export = self.tmp / "line2 settings.json"
        config.export_settings(config.normalize_config({"station_name": "Line 2"}), export)
        self._patch(filedialog, "askopenfilename", lambda **k: str(export))

        def steps():
            w.import_settings()
            self.assertEqual(w.v["station_name"].get(), "Line 2")
            self.assertTrue(w.save())
            entry = audit.read(w.data_dir, 1)[0]
            self.assertEqual((entry["how"], entry["detail"]), ("import", "line2 settings.json"))
            w.v["station_name"].set("Line 3")
            self.assertTrue(w.save())
            self.assertEqual(audit.read(w.data_dir, 1)[0]["how"], "save")  # only the first save after an import
            yield 100
        self.run_steps(steps)


class EditedOutsideInWindow(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        config.save_config(config.normalize_config({"station_name": "Line 5"}), data / "config.json")
        raw = json.loads((data / "config.json").read_text(encoding="utf-8"))
        raw["shared_folder"] = "C:/by-hand"
        (data / "config.json").write_text(json.dumps(raw), encoding="utf-8")
        return app.App(data)

    def test_the_window_says_so_and_sends_an_alert(self):
        w = self.window

        def steps():
            yield lambda: "outside CNCBridge" in w.status_msg.get()
            yield lambda: len(self.notified) == 1
            self.assertIn("outside", self.notified[0][0])
            w.open_settings_history()
            w.update()
            tree = w.history_tree
            self.assertEqual(tree.item(tree.get_children()[0], "values")[2], "Outside CNCBridge")
            self.assertIn("Shared folder: (empty) → C:/by-hand", tree.item(tree.get_children()[0], "values")[3])
            yield 100
        self.run_steps(steps)


class ProtectionInWindow(WindowTest):
    """Settings that only an administrator may change; the typed values stay open to operators."""

    def make_window(self):
        data = self.tmp / "data"
        config.save_config(config.normalize_config({
            "station_name": "Line 5", "source_folder": str(self.machine), "backup_folder": str(self.backup),
            "shared_enabled": True, "shared_folder": str(self.shared),
            "selections": [{"name": "Summary", "range": "L1:M8", "header": True}],
            "manual_inputs": [{"name": "Operator", "value": "OP-1"}, {"name": "Work order", "value": "WO-7"}]}),
            data / "config.json")
        return app.App(data)

    def settings_bytes(self):
        return (self.tmp / "data" / "config.json").read_bytes()

    def read_typed(self):
        return json.loads((self.tmp / "data" / "operator.json").read_text(encoding="utf-8"))["manual_inputs"]

    def without_rights(self, elevated=False):
        self._patch(permissions, "is_elevated", lambda: elevated)
        self._patch(permissions, "can_write", lambda path: False)
        self.restarts = []
        self.window.restart_as_administrator = lambda: self.restarts.append(1)

    def test_typed_values_are_saved_without_touching_the_settings(self):
        w = self.window
        before = self.settings_bytes()

        def steps():
            w.man_vars[0][1].set("OP-5")
            w.apply_operator_values()
            self.assertEqual(self.settings_bytes(), before)
            self.assertEqual(self.read_typed(), ["OP-5", "WO-7"])
            self.assertEqual(w.cfg["manual_inputs"][0]["value"], "OP-5")
            self.assertIn("Typed values applied", w.status_msg.get())
            self.assertTrue(w.finish_shift(["OP-6", "WO-8"]))  # the shift-change confirmation saves them the same way
            self.assertEqual(self.read_typed(), ["OP-6", "WO-8"])
            self.assertEqual(self.settings_bytes(), before)
            self.assertEqual(audit.read(w.data_dir, 1)[0]["how"], "operator")
            yield 100
        self.run_steps(steps)

    def test_saving_without_the_rights_offers_to_restart_as_administrator(self):
        w = self.window
        self.without_rights()
        asked = []
        self._patch(messagebox, "askokcancel", lambda *a, **k: asked.append(a) or True)
        before = self.settings_bytes()

        def steps():
            w.v["station_name"].set("Changed")
            self.assertFalse(w.save())
            self.assertEqual(len(asked), 1)
            self.assertIn("needs administrator rights", asked[0][1])
            self.assertEqual(self.restarts, [1])
            self.assertEqual(self.settings_bytes(), before)
            self._patch(messagebox, "askokcancel", lambda *a, **k: False)  # the person says no
            self.assertFalse(w.save())
            self.assertEqual(self.restarts, [1])
            yield 100
        self.run_steps(steps)

    def test_a_refusal_even_with_administrator_rights_is_explained(self):
        w = self.window
        self.without_rights(elevated=True)

        def steps():
            self.assertFalse(w.save())
            self.assertEqual(self.dialogs[-1][0], "showerror")
            self.assertIn("cannot be changed, even with administrator rights", self.dialogs[-1][1][1])
            self.assertEqual(self.restarts, [])
            yield 100
        self.run_steps(steps)

    def test_setting_the_password_without_the_rights_offers_the_restart(self):
        w = self.window
        self.without_rights()

        def refused(*args, **kwargs):
            raise PermissionError("denied")
        self._patch(access_ui, "save_config", refused)

        def steps():
            w._store_admin_password("window-test-password")
            self.assertEqual(self.restarts, [1])
            self.assertEqual(w.cfg["setup_password"], "")  # nothing changed
            yield 100
        self.run_steps(steps)

    def test_monitoring_starts_with_the_saved_settings_when_they_are_protected(self):
        w = self.window
        self.without_rights()
        before = self.settings_bytes()

        def steps():
            w.v["station_name"].set("Edited, not saved")
            cfg = w._ready(quiet=True)
            self.assertEqual(cfg["station_name"], "Line 5")
            self.assertEqual(self.settings_bytes(), before)
            w.cfg = dict(w.cfg, source_folder="")  # the saved settings are not usable
            w.v["source_folder"].set(str(self.machine))
            self.assertIsNone(w._ready(quiet=True))
            self.assertEqual(self.dialogs[-1][0], "showerror")
            yield 100
        self.run_steps(steps)

    def test_the_protection_box_follows_the_session_and_the_files(self):
        w = self.window
        protect, accounts, button = (w.field_widgets[k] for k in ("protect_settings", "password_accounts", "restart_admin"))

        def steps():
            self.assertEqual((self.menu_label("restart_admin"), self.menu_state("restart_admin")),
                             ("Restart as administrator", "normal"))
            self._patch(permissions, "is_elevated", lambda: False)
            self._patch(permissions, "settings_protected", lambda folder: False)
            w._update_protection_state()
            self.assertTrue(w.protection_state.get().startswith("Not protected"))
            self.assertIn("needs administrator rights", w.protection_state.get())
            self.assertEqual((str(protect.cget("state")), str(accounts.cget("state")), str(button.cget("state"))),
                             ("disabled", "disabled", "normal"))
            self.assertEqual(self.menu_state("restart_admin"), "normal")
            self._patch(permissions, "settings_protected", lambda folder: True)
            w._update_protection_state()
            self.assertTrue(w.protection_state.get().startswith("Protected: only administrators"))
            self._patch(permissions, "settings_protected", lambda folder: False)
            w.cfg = dict(w.cfg, protect_settings=True)
            w._update_protection_state()
            self.assertTrue(w.protection_state.get().startswith("Switched on, but not applied"))
            self._patch(permissions, "is_elevated", lambda: True)
            w._update_protection_state()
            self.assertEqual((str(protect.cget("state")), str(accounts.cget("state")), str(button.cget("state"))),
                             ("normal", "normal", "disabled"))
            self.assertEqual(self.menu_state("restart_admin"), "disabled")
            self.assertNotIn("needs administrator rights", w.protection_state.get())
            yield 100
        self.run_steps(steps)

    def test_restart_as_administrator_asks_windows_and_closes_this_window(self):
        w = self.window
        self._patch(permissions, "is_elevated", lambda: False)
        started, destroyed = [], []
        answers = iter([True, False])
        self._patch(permissions, "run_as_administrator", lambda command, cwd=None: started.append(command) or next(answers))
        w.destroy = lambda: destroyed.append(1)

        def steps():
            w.restart_as_administrator()
            self.assertIn("--restarted", started[0])
            self.assertEqual(started[0][started[0].index("--data") + 1], str(w.data_dir))
            self.assertEqual(destroyed, [1])
            self.assertTrue(w.closing.is_set())
            w.closing.clear()
            w.restart_as_administrator()  # Windows' question was answered with no
            self.assertEqual(destroyed, [1])
            self.assertEqual(self.dialogs[-1][0], "showinfo")
            del w.destroy
            yield 100
        self.run_steps(steps)


class AdministratorWindow(WindowTest):
    def make_window(self):
        self._patch(permissions, "is_elevated", lambda: True)
        return app.App(self.tmp / "data")

    def test_the_window_says_so_and_the_protection_can_be_switched(self):
        w = self.window
        calls = []
        self._patch(permissions, "lock_down", lambda folder, readers: calls.append(("lock", folder, readers)) or [])
        self._patch(permissions, "unlock", lambda folder: calls.append(("unlock", folder)) or [])

        def steps():
            self.assertTrue(w.title().endswith("(administrator)"))
            self.assertEqual(self.menu_state("restart_admin"), "disabled")
            self.assertEqual(str(w.field_widgets["protect_settings"].cget("state")), "normal")
            w.b["protect_settings"].set(True)
            w.accounts_text.insert("1.0", "DOM" + chr(92) + "op\nline-pc")
            self.assertTrue(w.save())
            self.assertEqual(calls, [("lock", w.data_dir, ["DOM" + chr(92) + "op", "line-pc"])])
            saved = config.load_config(w.data_dir / "config.json")
            self.assertEqual((saved["protect_settings"], saved["password_accounts"]),
                             (True, "DOM" + chr(92) + "op\nline-pc"))
            w.b["protect_settings"].set(False)
            self.assertTrue(w.save())
            self.assertEqual(calls[-1], ("unlock", w.data_dir))
            yield 100
        self.run_steps(steps)


class OptionsScrolling(WindowTest):
    def test_a_page_taller_than_the_window_scrolls_and_otherwise_does_not(self):
        w = self.window
        page = w.option_pages["access"]
        last = w.field_widgets["restart_admin"]  # the bottom of the page

        def steps():
            w.options_win.minsize(300, 300)  # the real minimum is larger; a small screen is simulated here
            w.options_win.geometry("780x900")
            w.open_options("access")
            w.update()
            yield lambda: page.canvas.winfo_height() > 700
            w.update()
            self.assertFalse(page.scrolls)
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
            w.options_win.geometry("780x420")
            yield lambda: page.canvas.winfo_height() < 400
            w.update()
            self.assertTrue(page.scrolls)
            self.assertGreater(page.canvas.yview()[1] - page.canvas.yview()[0], 0)
            self.assertLess(page.canvas.yview()[1], 1.0)
            bottom = page.canvas.winfo_rooty() + page.canvas.winfo_height()
            self.assertGreater(last.winfo_rooty() + last.winfo_height(), bottom)  # cut off until it is scrolled to
            page.canvas.yview_moveto(1.0)
            w.update()
            self.assertLessEqual(last.winfo_rooty() + last.winfo_height(), bottom + 1)
            page.canvas.yview_moveto(0)
            w.update()
            page.scroll(3)
            w.update()
            self.assertGreater(page.canvas.yview()[0], 0)
            page.scroll(-30)
            w.update()
            self.assertEqual(page.canvas.yview()[0], 0)
            # the wheel scrolls the page the pointer is over: here the pointer is said to be over its last widget ...
            wheel = types.SimpleNamespace(delta=-120, x_root=0, y_root=0)
            page.winfo_containing = lambda x, y: last
            page._wheel(wheel)
            w.update()
            self.assertGreater(page.canvas.yview()[0], 0)
            page.canvas.yview_moveto(0)
            page.winfo_containing = lambda x, y: w.field_widgets["station_name"]  # ... and not when it is over the form
            page._wheel(wheel)
            w.update()
            self.assertEqual(page.canvas.yview()[0], 0)
            del page.winfo_containing
            w.options_win.geometry("780x900")
            yield lambda: page.canvas.winfo_height() > 700
            w.update()
            self.assertFalse(page.scrolls)  # room again: the scrollbar goes
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.run_steps(steps)


class FirstStartChecklist(WindowTest):
    def ticks(self):
        """The guide's badges, e.g. ['1', '✓', '3', '4']: a number while the step is open, a tick when it is done."""
        return [self.window.checklist_rows[k]["badge"].cget("text").strip()
                for k in ("folders", "ranges", "delivery", "start")]

    def settled(self, expected):
        return lambda: self.ticks() == expected

    def fill_folders(self):
        self.backup.mkdir(exist_ok=True)
        self.window.v["source_folder"].set(str(self.machine))
        self.window.v["backup_folder"].set(str(self.backup))

    def test_a_new_setup_starts_with_the_guide_and_each_step_ticks_when_done(self):
        w = self.window

        def steps():
            self.assertTrue(w.checklist_shown)
            self.assertEqual(w.checklist_box.winfo_manager(), "pack")
            self.assertEqual(self.ticks(), ["1", "2", "3", "4"])
            rows = w.checklist_rows
            self.assertEqual(str(rows["folders"]["go"].cget("style")), "Go.TButton")  # the next thing to do stands out
            self.assertEqual(str(rows["ranges"]["go"].cget("style")), "TButton")
            self.assertEqual(str(rows["start"]["go"].cget("state")), "disabled")  # not before the other three
            self.fill_folders()
            yield self.settled(["✓", "2", "3", "4"])
            self.assertEqual(str(rows["ranges"]["go"].cget("style")), "Go.TButton")
            w.sel_vars[0][1].set("L1:M8")
            yield self.settled(["✓", "✓", "3", "4"])
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            yield self.settled(["✓", "✓", "✓", "4"])
            self.assertEqual(str(rows["start"]["go"].cget("state")), "normal")
            self.assertEqual(str(rows["start"]["go"].cget("style")), "Go.TButton")
            w.sel_vars[0][1].set("")  # a step that stops being true loses its tick
            yield self.settled(["✓", "2", "✓", "4"])
        self.run_steps(steps)

    def test_a_working_destination_test_is_noticed(self):
        w = self.window

        def steps():
            self.fill_folders()
            w.sel_vars[0][1].set("L1:M8")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            yield self.settled(["✓", "✓", "✓", "4"])
            self.assertEqual(w.checklist_rows["delivery"]["hint"].cget("text"),
                             "Set. Use its Test button to check that it works.")
            w.test_shared()
            yield lambda: w.checklist_rows["delivery"]["hint"].cget("text") == "Set, and the test worked."
        self.run_steps(steps)

    def test_the_buttons_go_to_the_right_place(self):
        w = self.window
        asked, started = [], []
        self._patch(w, "open_csv", lambda: asked.append("dialog"))
        self._patch(w, "start", lambda quiet=False: started.append(1))

        def steps():
            w.checklist_rows["delivery"]["go"].invoke()
            self.assertEqual(w.tabs.select(), str(w.delivery_tab))
            w.checklist_rows["folders"]["go"].invoke()
            self.assertEqual(w.tabs.select(), str(w.setup_tab))
            self.fill_folders()
            w.checklist_rows["ranges"]["go"].invoke()  # no machine file anywhere yet: it asks for one
            self.assertEqual(w.tabs.select(), str(w.pick_tab))
            self.assertEqual(asked, ["dialog"])
            self.drop("a.csv", "B240001")
            w.checklist_rows["ranges"]["go"].invoke()  # with a file in the machine folder it opens that
            self.assertEqual(w.sample_name, "a.csv")
            self.assertEqual(asked, ["dialog"])
            w.sel_vars[0][1].set("L1:M8")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            yield self.settled(["✓", "✓", "✓", "4"])
            w.checklist_rows["start"]["go"].invoke()
            self.assertEqual(started, [1])
        self.run_steps(steps)

    def test_the_first_start_ends_the_guide(self):
        w = self.window

        def steps():
            self.fill_folders()
            w.sel_vars[0][1].set("L1:M8")
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            yield self.settled(["✓", "✓", "✓", "4"])
            self.assertFalse(firstrun.is_done(w.data_dir))
            w.checklist_rows["start"]["go"].invoke()
            self.assertTrue(w.running)
            self.assertTrue(firstrun.is_done(w.data_dir))
            yield lambda: not w.checklist_shown
            self.assertEqual(w.checklist_box.winfo_manager(), "")
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)

    def test_the_view_menu_brings_it_back_and_close_ends_it(self):
        w = self.window

        def steps():
            self.assertEqual((self.menu_label("setup_checklist"), self.menu_state("setup_checklist")),
                             ("Setup checklist", "normal"))
            w.field_widgets["checklist_hide"].invoke()
            self.assertFalse(w.checklist_shown)
            self.assertTrue(firstrun.is_done(w.data_dir))
            w.tabs.select(w.history_tab)
            w.menu_items["setup_checklist"][0].invoke(w.menu_items["setup_checklist"][1])
            self.assertTrue(w.checklist_shown)
            self.assertEqual(w.tabs.select(), str(w.setup_tab))
            w.hide_checklist()
            self.assertFalse(w.checklist_shown)
            yield 100
        self.run_steps(steps)

    def test_operators_do_not_see_the_guide(self):
        w = self.window

        def steps():
            self.assertTrue(w.checklist_shown)
            w._store_admin_password("window-test-password")  # whoever sets it stays in the setup
            self.assertTrue(w.checklist_shown)
            w.toggle_lock()  # lock
            yield lambda: not w.checklist_shown
            self.assertEqual(self.menu_state("setup_checklist"), "disabled")
        self.run_steps(steps)


class SetUpAlreadyInWindow(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        self.backup.mkdir()
        config.save_config(config.normalize_config({
            "station_name": "Line 5", "source_folder": str(self.machine), "backup_folder": str(self.backup),
            "shared_enabled": True, "shared_folder": str(self.shared),
            "selections": [{"name": "Summary", "range": "L1:M8", "header": True}]}), data / "config.json")
        return app.App(data)

    def test_a_copy_that_is_already_set_up_gets_no_guide(self):
        w = self.window

        def steps():
            self.assertFalse(w.checklist_shown)
            self.assertTrue(firstrun.is_done(w.data_dir))
            w.show_checklist()  # but it can be asked for: everything ticked
            self.assertTrue(w.checklist_shown)
            self.assertEqual([w.checklist_rows[k]["badge"].cget("text").strip() for k in ("folders", "ranges", "delivery", "start")],
                             ["✓", "✓", "✓", "✓"])
            yield 100
        self.run_steps(steps)


class WordingInWindow(WindowTest):
    def test_the_counters_the_menus_and_the_boxes_use_the_agreed_words(self):
        w = self.window

        def steps():
            keys = ("ok", "waiting", "queued", "failed", "rejected")
            self.assertEqual([w.count_captions[k].cget("text") for k in keys],
                             ["Done", "Waiting to be read", "Waiting to be sent", "Could not be read", "Refused"])
            for key in keys:  # each counter explains itself when the pointer rests on it
                for widget in (w.count_boxes[key], w.counts[key], w.count_captions[key]):
                    self.assertTrue(widget.bind("<Enter>"), key)
            self.assertEqual(self.menu_label("test_shared"), "Test shared folder")
            self.assertEqual(self.menu_label("test_api"), "Test API link")
            self.assertEqual(self.menu_label("test_ftp"), "Test FTP server")
            boxes = []

            def walk(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == "TLabelframe":
                        boxes.append(str(child.cget("text")).strip())
                    walk(child)
            walk(w.tabs)
            for expected in ("1  Machine folders", "4  Typed values", "6  API link", "5  Shared folder",
                             "7  FTP server", "Typed values"):
                self.assertIn(expected, boxes)
            labels = []

            def labels_of(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == "TLabel":
                        labels.append(str(child.cget("text")))
                    labels_of(child)
            labels_of(w.tabs)
            for expected in ("Machine folder", "Typed value 1", "Typed value 2"):
                self.assertIn(expected, labels)
            self.assertEqual([w.history.heading(k, "text") for k in w.history["columns"]],
                             ["Time", "Machine file", "Result", "Output file", "Shared folder", "API link", "FTP server", "Note"])
            yield 100
        self.run_steps(steps)

    def test_the_history_says_it_in_the_words_of_the_counters(self):
        w = self.window

        def steps():
            w.ledger.add(source="a.csv", output="a.json", status="ok", shared="ok", api="queued", ftp="off")
            w.ledger.add(source="b.csv", output="b.json", status="ok", shared="ok", api="rejected (HTTP 422)", ftp="off")
            w.ledger.add(source="c.csv", status="failed", message="not a readable CSV")
            w.ledger.add(source="d.csv", status="duplicate")
            yield lambda: len(w.history.get_children()) == 4
            rows = {w.history.item(i, "values")[1]: w.history.item(i, "values") for i in w.history.get_children()}
            self.assertEqual(rows["a.csv"][2:], ("Done", "a.json", "Delivered", "Waiting to be sent", "Off", ""))
            self.assertEqual(rows["b.csv"][5], "Refused (HTTP 422)")
            self.assertEqual(rows["c.csv"][2], "Could not be read")
            self.assertEqual(rows["d.csv"][2], "Duplicate")
            self.assertEqual(rows["c.csv"][7], "not a readable CSV")
            # what is stored does not change, so Send again still finds a refused file
            refused = next(r for r in w.shown_history if r["source"] == "b.csv")
            self.assertEqual(refused["api"], "rejected (HTTP 422)")
            self.assertTrue(w._refused(refused))
            self.assertTrue(w._refused({"shared": "ok", "api": "rejected (x)", "ftp": "off"}))
        self.run_steps(steps)

    def test_the_stations_window_uses_the_same_counter_names(self):
        w = self.window

        def steps():
            w.open_stations()
            w.update()
            headings = [w.stations_tree.heading(c, "text") for c in w.stations_tree["columns"]]
            for expected in ("Done", "Could not be read", "Waiting to be read", "Waiting to be sent", "Refused"):
                self.assertIn(expected, headings)
            w.close_stations()
            yield 100
        self.run_steps(steps)


class DiagnosticsInWindow(WindowTest):
    def make_window(self):
        data = self.tmp / "data"
        config.save_config(config.normalize_config({
            "station_name": "Line 5", "api_token": "tok-SECRET-1", "api_url": "https://mes.example/in?key=KEY-SECRET-5",
            "api_headers": "X-Api-Key: hdr-SECRET-4"}), data / "config.json")
        (data / "logs").mkdir()
        (data / "logs" / "cncbridge.log").write_text("ERROR POST https://mes.example/in?key=KEY-SECRET-5 -> 503\n"
                                                       "DEBUG Authorization: Bearer tok-SECRET-1\n", encoding="utf-8")
        return app.App(data)

    def test_the_help_menu_saves_a_zip_without_passwords_and_shows_it(self):
        w = self.window
        target = self.tmp / "send" / "diag.zip"
        asked, launched = [], []
        self._patch(filedialog, "asksaveasfilename", lambda **k: asked.append(k) or str(target))
        real = app.subprocess.Popen
        self._patch(app.subprocess, "Popen", lambda command, *a, **k: launched.append(command)
                    if command[0] == "explorer" else real(command, *a, **k))

        def steps():
            self.assertEqual((self.menu_label("diagnostics"), self.menu_state("diagnostics")),
                             ("Export diagnostics...", "normal"))
            w.export_diagnostics()
            self.assertTrue(asked[0]["initialfile"].startswith("CNCBridge diagnostics Line 5 "))
            self.assertTrue(asked[0]["initialfile"].endswith(".zip"))
            yield lambda: "Diagnostics saved in" in w.status_msg.get()
            self.assertIn("holds no passwords", w.status_msg.get())
            self.assertEqual(launched, [["explorer", f"/select,{target}"]])  # the folder opens with the file selected
            import zipfile
            with zipfile.ZipFile(target) as z:
                names = z.namelist()
                self.assertEqual(names[0], "summary.txt")
                self.assertIn("logs/cncbridge.log", names)
                text = "".join(z.read(n).decode("utf-8", errors="replace") for n in names)
            for secret in ("tok-SECRET-1", "KEY-SECRET-5", "hdr-SECRET-4"):
                self.assertNotIn(secret, text)
            self.assertIn("Bearer ***", text)
            self.assertIn("Station:      Line 5", text)
        self.run_steps(steps)

    def test_it_works_while_the_setup_is_locked_and_a_cancelled_dialog_does_nothing(self):
        w = self.window
        answers = iter(["", str(self.tmp / "locked.zip")])
        self._patch(filedialog, "asksaveasfilename", lambda **k: next(answers))
        self._patch(app.subprocess, "Popen", lambda command, *a, **k: None)

        def steps():
            w._store_admin_password("window-test-password")
            w.toggle_lock()
            self.assertEqual(self.menu_state("save"), "disabled")  # locked
            self.assertEqual(self.menu_state("diagnostics"), "normal")  # but support can still be asked
            w.export_diagnostics()  # cancelled
            self.assertEqual(w.status_msg.get().count("Diagnostics"), 0)
            self.assertFalse((self.tmp / "locked.zip").exists())
            w.export_diagnostics()
            yield lambda: (self.tmp / "locked.zip").exists() and "Diagnostics saved" in w.status_msg.get()
        self.run_steps(steps)

    def test_a_place_that_cannot_be_written_is_reported(self):
        w = self.window
        blocker = self.tmp / "a file"
        blocker.write_text("x")
        self._patch(filedialog, "asksaveasfilename", lambda **k: str(blocker / "inside.zip"))

        def steps():
            w.export_diagnostics()
            yield lambda: self.dialogs and self.dialogs[-1][0] == "showerror"
            self.assertIn("could not be saved", self.dialogs[-1][1][1])
            self.assertEqual(w.status_msg.get(), "The diagnostics were not saved.")
        self.run_steps(steps)


class DashboardWindow(WindowTest):
    """The Dashboard theme: a sidebar instead of tabs, a Monitor page, and white cards."""

    def make_window(self):
        self.backup.mkdir(exist_ok=True)
        data = self.tmp / "data"
        config.save_config(config.normalize_config({
            "theme": "dashboard", "station_name": "Line 7", "source_folder": str(self.machine),
            "backup_folder": str(self.backup), "shared_enabled": True, "shared_folder": str(self.shared),
            "api_enabled": True, "api_url": "https://mes.example/api",
            "selections": [{"name": "Placements", "range": "A12:I*", "header": True}],
            "manual_inputs": [{"name": "Operator", "value": "OP-17"}, {"name": "Work order", "value": "WO-1187"}]}),
            data / "config.json")
        firstrun.mark_done(data)  # a copy that has run before opens on the Monitor
        return app.App(data)

    def visible(self):
        return [k for k in ("monitor", "setup", "pick", "delivery", "history", "operator")
                if self.window.nav_items[k]["row"].winfo_manager()]

    def test_the_window_has_a_sidebar_a_monitor_page_and_no_tab_strip(self):
        w = self.window

        def steps():
            self.assertIsNotNone(w.sidebar)
            self.assertIsNot(w.content, w)
            self.assertEqual(w.theme.key, "dashboard")
            self.assertEqual([e[0] for e in ttk.Style(w).layout("Pages.TNotebook.Tab")], ["null"])  # the sidebar does the tabs' job
            self.assertNotEqual([e[0] for e in ttk.Style(w).layout("TNotebook.Tab")], ["null"])  # other notebooks keep theirs
            self.assertIn(str(w.options_tabs.cget("style")), ("", "TNotebook"))  # such as the Options window's pages
            self.assertEqual(str(w.tabs.cget("style")), "Pages.TNotebook")
            self.assertEqual(self.visible(), ["monitor", "setup", "pick", "delivery", "history"])
            for extra in ("settings", "help", "log"):
                self.assertTrue(w.nav_items[extra]["row"].winfo_manager(), extra)
            self.assertEqual(w.tabs.select(), str(w.monitor_tab))  # it has run before: the first thing is the state
            self.assertTrue(w.nav_items["monitor"]["active"])
            self.assertEqual((self.menu_label("view_monitor"), self.menu_state("view_monitor")), ("Monitor", "normal"))
            self.assertEqual(w.sidebar_station.get(), "Line 7")
            yield 100
        self.run_steps(steps)

    def test_clicking_the_sidebar_opens_the_page_and_marks_it(self):
        w = self.window

        def steps():
            w.nav_items["delivery"]["text"].event_generate("<Button-1>")
            yield lambda: w.tabs.select() == str(w.delivery_tab)
            yield lambda: w.nav_items["delivery"]["active"] and not w.nav_items["monitor"]["active"]
            w.nav_items["history"]["icon"].event_generate("<Button-1>")  # the icon is part of the entry
            yield lambda: w.tabs.select() == str(w.history_tab)
            w._show_tab(w.pick_tab)  # and a page opened any other way is marked too
            yield lambda: w.nav_items["pick"]["active"] and not w.nav_items["history"]["active"]
            self.assertTrue(all(str(i["row"].cget("takefocus")) in ("1", "True", "true") for i in w.nav_items.values()))
            self.assertTrue(w.nav_items["setup"]["row"].bind("<Return>"))
        self.run_steps(steps)

    def test_settings_help_and_the_log_open_from_the_sidebar(self):
        w = self.window

        def steps():
            w.nav_items["settings"]["text"].event_generate("<Button-1>")
            yield lambda: w.options_win.state() == "normal"
            w.close_options()
            w.nav_items["help"]["text"].event_generate("<Button-1>")
            yield lambda: w.help_win.state() == "normal"
            w.help_win.withdraw()
            w.nav_items["log"]["text"].event_generate("<Button-1>")
            yield lambda: w.log_win.state() == "normal"
        self.run_steps(steps)

    def test_the_monitor_shows_the_counters_the_latest_files_and_each_destination(self):
        w = self.window
        (w.data_dir / "outbox" / "api").mkdir(parents=True)
        for n in range(3):
            (w.data_dir / "outbox" / "api" / f"b{n}.json").write_text("{}")
        rows = [("10:04:11", "b6.csv", "ok", "ok", "ok"), ("10:05:20", "b7.csv", "duplicate", "off", "off"),
                ("10:06:44", "b8.csv", "failed", "off", "off"), ("10:12:31", "b10.csv", "ok", "ok", "queued")]
        for t, name, status, shared, api in rows:
            w.ledger.add(processed_at=f"2026-10-21T{t}", source=name, output=name + ".json", status=status,
                         shared=shared, api=api, ftp="off", message="")

        def steps():
            yield lambda: w.counts["ok"].cget("text") == "2" and w.recent_shown
            self.assertEqual([w.counts[k].cget("text") for k in ("ok", "waiting", "queued", "failed", "rejected")],
                             ["2", "0", "3", "1", "0"])
            values = [texts for _, texts in w.recent_shown]
            self.assertEqual([v[1] for v in values], ["b10.csv", "b8.csv", "b7.csv", "b6.csv"])  # newest first
            self.assertEqual(values[0], ("10:12:31", "b10.csv", "Done", "Shared folder ✓ · API link waiting"))
            self.assertEqual(values[1][2], "Could not be read")
            self.assertEqual(values[2][2], "Duplicate")
            self.assertEqual(values[3][3], "Shared folder ✓ · API link ✓")
            self.assertEqual(w.recent_empty.winfo_manager(), "")  # there is something to show
            result, places = w.recent_lines[0][2], w.recent_lines[0][4]
            self.assertEqual((result.text, result.kind), ("Done", "ok"))  # as chips
            self.assertEqual([(c.text, c.kind) for c in places if c.winfo_manager()],
                             [("Shared folder ✓", "ok"), ("API link waiting", "warn")])
            self.assertEqual(w.recent_lines[1][2].kind, "error")  # Could not be read
            self.assertFalse(w.recent_lines[5][0].winfo_manager())  # only four files: the other lines are hidden
            self.assertEqual(w.destination_rows["shared"][1].cget("text"), "Delivered · last at 10:12")
            self.assertEqual(w.destination_rows["api"][1].cget("text"), "3 waiting to be sent")
            self.assertEqual(w.destination_rows["ftp"][1].cget("text"), "Off")
            self.assertEqual(str(w.destination_rows["ftp"][0].cget("fg")), theme.IDLE)
            self.assertEqual(str(w.destination_rows["api"][0].cget("fg")), theme.WARNING)
            self.assertEqual([(a.get(), b.get()) for a, b in w.monitor_values],
                             [("Operator", "OP-17"), ("Work order", "WO-1187")])
            self.assertIn("Last file 10:12", w.health_labels["last"].cget("text"))
        self.run_steps(steps)

    def test_switches_segments_and_more_work_like_the_controls_they_replace(self):
        w = self.window

        def steps():
            switch = w.field_widgets["api_enabled"]
            self.assertIsInstance(switch, widgets.Switch)
            self.assertTrue(w.b["api_enabled"].get())
            switch.invoke()
            self.assertFalse(w.b["api_enabled"].get())
            switch.event_generate("<space>")  # the keyboard too
            switch.focus_force()
            switch.event_generate("<space>")
            w.update()
            switch.configure(state="disabled")
            self.assertEqual(switch.cget("state"), "disabled")
            before = w.b["api_enabled"].get()
            switch.invoke()  # a switched-off switch does nothing
            self.assertEqual(w.b["api_enabled"].get(), before)
            switch.configure(state="normal")

            self.assertIsInstance(w.field_widgets["output_format_pdf"], widgets.Segment)
            w.field_widgets["output_format_pdf"].invoke()
            self.assertEqual(w.v["output_format"].get(), "pdf")
            segmented = w.field_widgets["output_format_pdf"].owner
            segmented._step(1)  # Right from the last goes round to the first
            self.assertEqual(w.v["output_format"].get(), "json")

            self.assertFalse(w.api_more.opened)  # nothing set in it: closed
            self.assertEqual(w.field_widgets["api_body"].winfo_ismapped(), 0)
            w.api_more.toggle()
            self.assertTrue(w.api_more.opened)
            w.api_more.toggle()
            w.fill(dict(w.cfg, api_headers="X-Key: 1"))  # something in it: opened, never hidden
            self.assertTrue(w.api_more.opened)
            yield 50
        self.run_steps(steps)

    def test_folder_fields_say_whether_the_folder_is_there(self):
        w = self.window
        for name in ("a.csv", "b.csv"):
            self.drop(name, name)

        def steps():
            w._check_folders()
            self.assertEqual(w.folder_notes["source_folder"].cget("text"), "Found. 2 file(s) waiting.")
            self.assertEqual(w.folder_notes["backup_folder"].cget("text"), "Found.")
            w.v["source_folder"].set(str(self.tmp / "nowhere"))
            w.v["shared_folder"].set(r"\\MES-SERVER\inbox")
            w._check_folders()
            self.assertEqual(w.folder_notes["source_folder"].cget("text"), "Folder not found.")
            self.assertEqual(str(w.folder_notes["source_folder"].cget("foreground")), theme.ERROR)
            self.assertIn("network folder", w.folder_notes["shared_folder"].cget("text"))  # not looked at here
            w.v["backup_folder"].set(str(self.tmp / "nowhere"))
            w._check_folders()
            self.assertIn("different folder", w.folder_notes["backup_folder"].cget("text"))
            yield 50
        self.run_steps(steps)

    def test_a_recent_file_on_the_monitor_opens_its_output(self):
        w = self.window
        folder = self.backup / "2026-10-21" / engine.OUTPUTS
        folder.mkdir(parents=True)
        (folder / "b6.json").write_text(json.dumps({"source_file": "b6.csv", "selections": [
            {"name": "Serial", "range": "B6", "value": "B6-SERIAL"}]}), encoding="utf-8")
        w.ledger.add(processed_at="2026-10-21T10:04:11", source="b6.csv", output="b6.json", status="ok",
                     shared="ok", api="off", ftp="off", message="")

        def steps():
            yield lambda: w.recent_shown
            self.assertTrue(w.recent_lines[0][1].bind("<Double-1>"))  # a double-click on the line opens it
            w._view_recent(0)
            yield lambda: getattr(w, "output_win", None) is not None and w.output_win.state() == "normal"
            self.assertEqual(w.output_tree.item(w.output_tree.get_children()[0], "values")[0], "B6-SERIAL")
        self.run_steps(steps)

    def test_a_destination_that_is_down_is_said_to_be_not_answering(self):
        w = self.window
        (w.data_dir / "alerts.json").write_text(json.dumps({"open": {"api": {
            "text": "Delivery to the API link is not possible", "since": "2026-10-21T10:08:00"}}}), encoding="utf-8")

        def steps():
            yield lambda: "Not answering since 10:08" in w.destination_rows["api"][1].cget("text")
            self.assertEqual(str(w.destination_rows["api"][0].cget("fg")), theme.WARNING)
        self.run_steps(steps)

    def test_typed_values_on_the_monitor_follow_the_form_and_lead_to_where_they_are_changed(self):
        w = self.window

        def steps():
            w.man_vars[0][1].set("OP-99")
            self.assertEqual(w.monitor_values[0][1].get(), "OP-99")
            w.man_vars[1][0].set("")
            self.assertEqual(w.monitor_values[1][0].get(), "Typed value 2")
            w.field_widgets["monitor_values"].invoke()
            yield lambda: w.tabs.select() == str(w.setup_tab)
            w._show_tab(w.monitor_tab)
            w._store_admin_password("window-test-password")
            w.toggle_lock()
            yield lambda: w.tabs.select() == str(w.operator_tab)
            w._show_tab(w.monitor_tab)
            w.field_widgets["monitor_values"].invoke()  # locked: the operator's own page
            yield lambda: w.tabs.select() == str(w.operator_tab)
        self.run_steps(steps)

    def test_locking_the_setup_leaves_the_operator_the_monitor_history_and_their_own_page(self):
        w = self.window

        def steps():
            w._store_admin_password("window-test-password")
            w.toggle_lock()
            yield lambda: self.visible() == ["monitor", "history", "operator"]
            self.assertEqual(w.tabs.select(), str(w.operator_tab))
            self.assertTrue(w.nav_items["operator"]["active"])
            self.assertTrue(w.protected)
        self.run_steps(steps)

    def test_the_status_pill_and_the_station_card_say_the_same(self):
        w = self.window

        def steps():
            self.assertEqual(w.state_label.cget("text").strip(), "Stopped")
            self.assertEqual(w.sidebar_state.cget("text"), "Stopped")
            w._update_sidebar_state("Monitoring", theme.SUCCESS)
            self.assertEqual((w.sidebar_state.cget("text"), str(w.sidebar_dot.cget("fg"))), ("Monitoring", "#5cc98a"))
            w._update_sidebar_state("Stopped", theme.IDLE)
            self.assertEqual(str(w.sidebar_dot.cget("fg")), theme.SIDEBAR_MUTED)
            w.v["station_name"].set("Line 9")
            self.assertEqual(w.sidebar_station.get(), "Line 9")
            w.v["station_name"].set("")
            self.assertEqual(w.sidebar_station.get(), "(no name yet)")
            w.start()  # the pill and the card follow a real start too
            yield lambda: w.running and w.sidebar_state.cget("text") == "Monitoring"
            self.assertEqual(w.state_label.cget("text").strip(), "Monitoring")
            w.stop()
            yield lambda: not w.running and w.sidebar_state.cget("text") == "Stopped"
        self.run_steps(steps)

    def test_plain_widgets_inside_a_box_are_white_like_the_box(self):
        w = self.window

        def steps():
            styles = set()

            def walk(widget, inside=False):
                for child in widget.winfo_children():
                    within = inside or child.winfo_class() == "TLabelframe"
                    if within and child.winfo_class() in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
                        styles.add(str(child.cget("style")))
                    walk(child, within)
            walk(w.setup_tab)
            walk(w.delivery_tab)
            self.assertTrue(styles & {"Inner.TFrame", "Card.TLabel", "Inner.TCheckbutton", "Inner.Small.TLabel"}, styles)
            self.assertFalse(styles & {"", "TFrame", "Small.TLabel"}, styles)  # none left in the page colour
            style = ttk.Style(w)
            for name in ("Inner.TFrame", "Card.TLabel", "Inner.TCheckbutton", "TLabelframe"):
                self.assertEqual(str(style.lookup(name, "background")), theme.SURFACE, name)
            self.assertEqual(str(style.lookup("TFrame", "background")), theme.BACKGROUND)  # the page is not
            yield 100
        self.run_steps(steps)

    def test_the_card_pass_only_touches_what_is_in_a_box(self):
        def steps():
            frame = ttk.Frame(self.window)
            outside = ttk.Frame(frame)
            box = ttk.LabelFrame(frame, text="box")
            inner, small, check = ttk.Frame(box), ttk.Label(box, style="Small.TLabel"), ttk.Checkbutton(box)
            own = ttk.Label(box, style="Card.TLabel")
            deep = ttk.Label(inner)
            window = tk.Toplevel(box)
            in_window = ttk.Frame(window)
            try:
                self.assertEqual(cards.restyle_cards(frame), 4)
                self.assertEqual(str(outside.cget("style")), "")
                self.assertEqual((str(inner.cget("style")), str(small.cget("style")), str(check.cget("style"))),
                                 ("Inner.TFrame", "Inner.Small.TLabel", "Inner.TCheckbutton"))
                self.assertEqual(str(own.cget("style")), "Card.TLabel")  # already a card style
                self.assertEqual(str(deep.cget("style")), "Inner.TLabel")
                self.assertEqual(str(in_window.cget("style")), "")  # a window of its own starts on the page again
                self.assertEqual(cards.restyle_cards(frame), 0)  # and a second pass changes nothing
            finally:
                window.destroy()
                frame.destroy()
            yield 50
        self.run_steps(steps)

    def test_a_box_becomes_a_card_with_its_title_inside_and_no_number(self):
        def steps():
            frame = ttk.Frame(self.window)
            box = ttk.LabelFrame(frame, text=" 5  Shared folder ")
            ttk.Label(box, text="x").pack()
            box.pack(padx=10, pady=10)
            try:
                cards.restyle_cards(frame)
                self.assertEqual(str(box.cget("text")), "Shared folder")
                self.assertEqual(str(box.cget("style")), "CardBox.TLabelframe")
                self.assertEqual(len(box.card_edges), 4)
                self.assertTrue(all(str(e.cget("bg")) == theme.BORDER for e in box.card_edges))
                style = ttk.Style(self.window)
                self.assertEqual(str(style.lookup("CardBox.TLabelframe", "bordercolor")), theme.SURFACE)  # no own border
                cards.restyle_cards(frame)  # a second pass makes no second edge
                self.assertEqual(len([c for c in frame.winfo_children() if c.winfo_class() == "Frame"]), 4)
            finally:
                frame.destroy()
            yield 50
        self.run_steps(steps)

    def test_the_header_shows_the_open_pages_title_and_its_own_buttons(self):
        w = self.window

        def steps():
            self.assertFalse(hasattr(w, "toolbar"))
            self.assertEqual(w.page_title.cget("text"), "Monitor")
            shown = lambda: [k for k, f in w.page_actions.items() if f.winfo_manager()]  # noqa: E731
            self.assertEqual(shown(), ["monitor"])
            w._show_tab(w.setup_tab)
            yield lambda: w.page_title.cget("text") == "Set up your machine"
            self.assertEqual(shown(), ["setup"])
            self.assertIn("machine files arrive", w.page_hint.cget("text"))
            w._show_tab(w.delivery_tab)
            yield lambda: shown() == ["delivery"]
            self.assertEqual(w.page_title.cget("text"), "Choose where files are delivered")
            w._show_tab(w.history_tab)
            yield lambda: shown() == []
            self.assertEqual(w.page_title.cget("text"), "File history")
            self.assertEqual(len(w.save_buttons), 2)
            self.assertIs(w.save_btn, w.save_buttons[0])
        self.run_steps(steps)

    def test_the_pill_is_tinted_and_start_and_stop_take_turns(self):
        w = self.window

        def steps():
            yield lambda: str(w.state_pill.cget("style")) == "PillIdle.TFrame"
            self.assertTrue(w.start_btn.winfo_manager())
            self.assertFalse(w.stop_btn.winfo_manager())
            w.sidebar_go.invoke()  # the sidebar's button starts it from any page
            yield lambda: w.running and str(w.state_pill.cget("style")) == "PillOk.TFrame"
            self.assertEqual(str(w.state_label.cget("style")), "PillOk.TLabel")
            self.assertEqual(str(w.state_dot.cget("bg")), theme.SUCCESS_TINT)
            self.assertTrue(w.stop_btn.winfo_manager())
            self.assertFalse(w.start_btn.winfo_manager())
            self.assertIn("Stop", w.sidebar_go.cget("text"))
            w.stop_btn.invoke()
            yield lambda: not w.running and w.start_btn.winfo_manager()
            yield lambda: "Start monitoring" in w.sidebar_go.cget("text")
        self.run_steps(steps)

    def test_a_problem_is_said_in_plain_words_with_a_way_to_it(self):
        w = self.window
        (w.data_dir / "outbox" / "api").mkdir(parents=True)
        for n in range(3):
            (w.data_dir / "outbox" / "api" / f"b{n}.json").write_text("{}")
        (w.data_dir / "alerts.json").write_text(json.dumps({"open": {"api": {
            "text": "Delivery to the API link is not possible: HTTP 503", "since": "2026-10-21T10:08:00"}}}),
            encoding="utf-8")

        def steps():
            # the count follows the queue: wait until the snapshot has seen all three files
            yield lambda: w.alert_banner_shown and "3 file(s) are safe in the send queue" in w.banner_advice.cget("text")
            self.assertEqual(w.banner_headline.cget("text"), "The API link is not answering.")
            self.assertIn("HTTP 503", w.banner_detail.cget("text"))  # the engine's own words stay, small
            self.assertIn("10:08", w.banner_detail.cget("text"))
            self.assertEqual(w.banner_button.cget("text"), "See what is waiting")
            w.banner_button.invoke()
            yield lambda: w.tabs.select() == str(w.history_tab)
            (w.data_dir / "alerts.json").write_text(json.dumps({"open": {}}), encoding="utf-8")
            yield lambda: not w.alert_banner_shown and not w.alert_banner.winfo_manager()
        self.run_steps(steps)

    def test_unsaved_changes_are_shown_until_they_are_saved(self):
        w = self.window

        def steps():
            w._show_tab(w.setup_tab)
            yield 100
            self.assertEqual([l.cget("text") for l in w.unsaved_labels], ["", ""])
            w.v["station_name"].set("Line 8")
            yield lambda: all(l.cget("text") == "Unsaved changes" for l in w.unsaved_labels)
            w.save_buttons[1].invoke()  # Delivery's Save does the same
            yield lambda: all(l.cget("text") == "" for l in w.unsaved_labels)
            self.assertEqual(w.cfg["station_name"], "Line 8")
        self.run_steps(steps)

    def test_locking_disables_every_save_button(self):
        w = self.window

        def steps():
            w._store_admin_password("window-test-password")
            w.toggle_lock()
            yield lambda: all(str(b.cget("state")) == "disabled" for b in w.save_buttons)
        self.run_steps(steps)

    def test_the_dashboard_uses_the_bundled_plex_fonts(self):
        def steps():
            families = set(tkfont.families(self.window))
            self.assertIn("IBM Plex Sans", families)
            self.assertIn("IBM Plex Mono", families)
            self.assertEqual(self.window._font_body, "IBM Plex Sans")
            yield 50
        self.run_steps(steps)

    def test_every_icon_draws_and_the_sidebar_uses_only_icons_that_exist(self):
        def steps():
            canvas = tk.Canvas(self.window, width=24, height=24)
            try:
                for name in icons.ICONS:
                    icons.draw(canvas, name, "#ffffff", 24)
                    self.assertTrue(canvas.find_all(), name)
                icons.draw(canvas, "monitor", "#ffffff", 48)  # and at another size, from scratch
                self.assertTrue(canvas.find_all())
            finally:
                canvas.destroy()
            names = [key for key, _label, _tab in self.window.NAV] + ["settings", "help", "log", "logo"]
            self.assertEqual([n for n in names if n not in icons.ICONS], [])
            yield 50
        self.run_steps(steps)


class DashboardFirstRun(WindowTest):
    def make_window(self):
        data = self.tmp / "fresh"
        config.save_config(config.normalize_config({"theme": "dashboard"}), data / "config.json")
        return app.App(data)

    def test_a_copy_that_has_never_run_opens_on_setup_with_the_guide(self):
        w = self.window

        def steps():
            self.assertEqual(w.tabs.select(), str(w.setup_tab))
            self.assertTrue(w.checklist_shown)
            self.assertTrue(w.nav_items["setup"]["active"])
            yield 100
        self.run_steps(steps)


class OtherThemesHaveNoSidebar(WindowTest):
    def test_the_default_theme_has_neither_a_sidebar_nor_a_monitor_page(self):
        w = self.window

        def steps():
            self.assertEqual(w.theme.key, "modern")
            self.assertIsNone(w.sidebar)
            self.assertIs(w.content, w)
            self.assertFalse(hasattr(w, "monitor_tab"))
            self.assertNotIn("view_monitor", w.menu_items)
            self.assertNotEqual([e[0] for e in ttk.Style(w).layout("TNotebook.Tab")], ["null"])  # its tab strip is there
            self.assertFalse(hasattr(w, "page_header"))  # the toolbar, as before
            self.assertEqual(w.save_buttons, [w.save_btn])
            self.assertIsInstance(w.field_widgets["api_enabled"], ttk.Checkbutton)  # no switches, no chips
            self.assertIsInstance(w.field_widgets["output_format_pdf"], ttk.Radiobutton)
            self.assertEqual(w.folder_notes, {})
            self.assertTrue(w.start_btn.winfo_manager() and w.stop_btn.winfo_manager())
            yield 100
        self.run_steps(steps)


class HeartbeatInWindow(WindowTest):
    def test_settings_page_test_button_and_menu(self):
        w = self.window
        status = self.tmp / "status"

        def steps():
            self.assertEqual((self.menu_label("opt_heartbeat"), self.menu_state("opt_heartbeat")),
                             ("Heartbeat...", "normal"))
            self.assertEqual((self.menu_label("test_heartbeat"), self.menu_state("test_heartbeat")),
                             ("Send test heartbeat", "normal"))
            w.open_options("heartbeat")
            w.update()
            self.assertEqual(w.options_tabs.select(), str(w.option_pages["heartbeat"]))
            w.test_heartbeat()  # nowhere to send it yet
            self.assertEqual(self.dialogs[-1][0], "showerror")
            w.b["heartbeat_enabled"].set(True)
            w.v["heartbeat_folder"].set(str(status))
            w.v["heartbeat_minutes"].set("2")
            w.test_heartbeat()
            yield lambda: self.dialogs and self.dialogs[-1][0] == "showinfo"
            self.assertIn("A test heartbeat was sent to:", self.dialogs[-1][1][1])
            sent = json.loads(next(status.glob("*.status.json")).read_text(encoding="utf-8"))
            self.assertEqual((sent["state"], sent["kind"]), ("test", "heartbeat"))
            self.assertTrue(w.save())
            saved = config.load_config(self.tmp / "data" / "config.json")
            self.assertEqual((saved["heartbeat_enabled"], saved["heartbeat_folder"], saved["heartbeat_minutes"]),
                             (True, str(status), 2))
        self.run_steps(steps)

    def test_a_heartbeat_with_nowhere_to_go_blocks_starting_with_a_clear_reason(self):
        w = self.window

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A1:B2")
            w.b["heartbeat_enabled"].set(True)
            w.start()
            self.assertEqual(self.dialogs[-1][0], "showerror")
            self.assertIn("The heartbeat is on", self.dialogs[-1][1][1])
            yield 100
        self.run_steps(steps)


class FtpInWindow(WindowTest):
    def test_settings_test_button_and_delivery_through_the_window(self):
        w = self.window
        root = self.tmp / "ftproot"
        root.mkdir()
        server = FakeFtpServer(root)
        self.addCleanup(server.close)

        def steps():
            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.sel_vars[0][1].set("A12:I14")
            w.b["ftp_enabled"].set(True)
            for key, value in (("ftp_host", "127.0.0.1"), ("ftp_port", str(server.port)), ("ftp_user", "op"),
                               ("ftp_password", "secret"), ("ftp_folder", "/incoming/line7")):
                w.v[key].set(value)
            self.assertTrue(w.save())  # FTP alone is enough: no shared folder or API needed
            saved = app.load_config(self.tmp / "data" / "config.json")
            self.assertEqual((saved["ftp_enabled"], saved["ftp_port"], saved["ftp_password"]), (True, server.port, "secret"))
            self.assertNotIn("secret", (self.tmp / "data" / "config.json").read_text(encoding="utf-8"))

            menu, index = w.menu_items["test_ftp"]  # Tools > Test FTP server
            menu.invoke(index)
            yield lambda: any(name == "showinfo" for name, _ in self.dialogs)
            self.assertIn("/incoming/line7", self.dialogs[-1][1][1])
            self.dialogs.clear()

            self.drop("a.csv", "S1")
            w.start()
            yield lambda: any(p.is_file() for p in root.rglob("*") if not p.name.startswith("."))
            yield lambda: w.history.get_children() and w.history.item(w.history.get_children()[0], "values")[6] == "Delivered"
            self.assertEqual(w.history.heading("ftp")["text"], "FTP server")
            w.stop()
            yield lambda: not w.running
        self.run_steps(steps)

    def test_incomplete_ftp_settings_are_reported(self):
        w = self.window

        def steps():
            w.b["ftp_enabled"].set(True)
            self.assertIn("Enter the FTP server address.", engine.validate(w.collect()))
            w.v["ftp_host"].set("")
            menu, index = w.menu_items["test_ftp"]
            menu.invoke(index)
            self.assertEqual(self.dialogs[-1][0], "showerror")
            yield 100
        self.run_steps(steps)


class TranslatedWindow(WindowWithSettings):
    """A window started in another language: a stand-in language file with a handful of translations."""
    settings = {"theme": "modern", "language": "xx"}

    def setUp(self):
        self.locales = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.locales, True)
        (self.locales / "xx.json").write_text(json.dumps({"language": "Xish", "strings": {
            "File": "Datei", "Open CSV file...": "CSV-Datei oeffnen...", "Save settings": "Einstellungen speichern",
            "Setup": "Einrichtung", "1  Machine folders": "1  Maschinenordner", "Settings saved.": "Gespeichert.",
            "No files yet": "Noch keine Dateien", "Getting started": "Erste Schritte",
            "Last file {time} ({ago})": "Letzte Datei {time} ({ago})", "Stopped": "Angehalten"}}), encoding="utf-8")
        self._patch(i18n, "LOCALE_DIR", self.locales)
        self.addCleanup(i18n.use, "en")
        super().setUp()

    def test_the_window_is_shown_in_the_language(self):
        w = self.window

        def steps():
            self.assertEqual((w.language, i18n.current()), ("xx", "xx"))
            self.assertEqual(w.menubar_buttons["File"]["text"], "Datei")  # the code still keys it by "File"
            self.assertEqual(w.tabs.tab(w.setup_tab, "text").strip(), "Einrichtung")
            self.assertEqual(w.save_btn["text"].strip()[-len("Einstellungen speichern"):], "Einstellungen speichern")
            self.assertEqual(w.state_label["text"], "Angehalten")
            self.assertTrue(w.bind("<Alt-d>"))  # Alt+D opens the menu now called Datei
            self.assertFalse(w.bind("<Alt-f>"))
            self.assertEqual(w.field_widgets["language"]["values"], ("English", "Xish"))
            self.assertEqual(w.v["language"].get(), "Xish")
            self.assertEqual(w._chosen_appearance()["language"], "xx")
            self.assertEqual(str(w.restart_btn["state"]), "disabled")  # the language in use

            w._open_menu(w.menubar_buttons["File"])  # translated titles still open their menus
            w.update()
            dd = w._dropdown
            self.assertEqual(dd.menu.entrycget(dd.rows[0]["index"], "label"), "CSV-Datei oeffnen...")
            w._close_menus()

            w._show_health({})
            self.assertEqual(w.health_labels["last"]["text"], "Noch keine Dateien")
            w._show_health({"last_file": "2026-10-19T10:42:00", "last_file_age": 5})
            self.assertEqual(w.health_labels["last"]["text"], "Letzte Datei 10:42 (just now)")  # untranslated part: English

            w.open_help("start")
            self.assertTrue(w.help_body.get("1.0", "end").startswith("Erste Schritte"))

            w.v["source_folder"].set(str(self.machine))
            w.v["backup_folder"].set(str(self.backup))
            w.b["shared_enabled"].set(True)
            w.v["shared_folder"].set(str(self.shared))
            w.sel_vars[0][1].set("A12:I14")
            self.assertTrue(w.save())
            self.assertEqual(w.status_msg.get(), "Gespeichert.")
            self.assertEqual(app.load_config(self.tmp / "data" / "config.json")["language"], "xx")
            yield 100
        self.run_steps(steps)

    def test_changing_the_language_needs_a_save_and_a_restart(self):
        w = self.window

        def steps():
            w.v["language"].set("English")
            w._update_theme_note()
            self.assertEqual(str(w.restart_btn["state"]), "normal")
            self.assertIn("Save settings", w.theme_note.get())
            self.assertEqual(w._chosen_appearance()["language"], "en")
            yield 100
        self.run_steps(steps)


class UnknownLanguageWindow(WindowWithSettings):
    settings = {"theme": "modern", "language": "zz"}

    def test_an_unknown_language_shows_english(self):
        w = self.window

        def steps():
            self.assertEqual(w.language, "en")
            self.assertEqual(w.tabs.tab(w.setup_tab, "text").strip(), "Setup")
            self.assertEqual(w.menubar_buttons["File"]["text"], "File")
            self.assertEqual(w.v["language"].get(), "English")
            self.assertEqual(w._chosen_appearance()["language"], "en")
            yield 100
        self.run_steps(steps)


class SetupWizard(unittest.TestCase):
    """The set-up window, working on temporary folders and a test registry key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        delete_registry_tree()
        self.addCleanup(lambda: (delete_registry_tree(), shutil.rmtree(self.tmp, ignore_errors=True)))
        where = setup_core.locations(self.tmp / "machine")
        payload = self.tmp / "payload"
        payload.mkdir()
        (payload / "CNCBridge.exe").write_bytes(fake_exe("app"))
        (payload / "README.md").write_text("readme", encoding="utf-8")
        self.parts = {"payload": payload, "program": where["program"], "data": where["data"], "menu": where["menu"],
                      "desktop": where["desktop"], "registry": where["registry"], "setup_exe": None,
                      "running_exe": None, "remove_tasks": lambda folder: None}
        self.where = where

    def log(self, wizard):
        return wizard.log.get("1.0", "end")

    def test_install_then_uninstall_through_the_window(self):
        wizard = Wizard(self.parts, sync=True)
        try:
            self.assertEqual(wizard.go_button["text"], "Install")
            wizard.start_menu.set(False)
            wizard.desktop.set(False)
            wizard.go()
            wizard.update()
            self.assertEqual(wizard.outcome[0], "ok")
            self.assertIn("is installed in", self.log(wizard))
            self.assertEqual(wizard.close_button["text"], "Finish")
            self.assertTrue((self.where["program"] / "CNCBridge.exe").exists())
            self.assertEqual(setup_core.installed_location(self.where["registry"])[0], self.where["program"])
        finally:
            wizard.destroy()
        again = Wizard(self.parts, sync=True)  # started again: it knows CNCBridge is installed
        try:
            texts = [w.cget("text") for w in again.winfo_children()[0].winfo_children() if w.winfo_class() == "TLabel"]
            self.assertTrue(any("is installed in" in t and "updates it" in t for t in texts))
        finally:
            again.destroy()
        (self.where["data"] / "config.json").write_text("{}")
        remove = Wizard(self.parts, uninstall=True, sync=True)
        try:
            self.assertEqual(remove.go_button["text"], "Uninstall")
            self.assertTrue(remove.keep_data.get())  # the default keeps the person's settings
            remove.go()
            remove.update()
            self.assertEqual(remove.outcome[0], "ok")
            self.assertIn("is removed", self.log(remove))
        finally:
            remove.destroy()
        self.assertFalse(self.where["program"].exists())
        self.assertEqual((self.where["data"] / "config.json").read_text(), "{}")

    def test_a_program_that_is_running_is_reported_and_the_person_can_try_again(self):
        wizard = Wizard(self.parts, sync=True)
        self.addCleanup(lambda: wizard.winfo_exists() and wizard.destroy())
        wizard.start_menu.set(False)
        wizard.desktop.set(False)
        wizard.go()
        wizard.update()
        handle = hold_open(self.where["program"] / "CNCBridge.exe")
        original = messagebox.askyesno
        messagebox.askyesno = lambda *a, **k: False  # the person does not want it closed
        try:
            wizard.go()
            wizard.update()
        finally:
            messagebox.askyesno = original
            release(handle)
        self.assertEqual(wizard.outcome[0], "error")
        self.assertIn("in use", self.log(wizard))
        self.assertEqual(wizard.go_button["text"], "Try again")
        self.assertEqual(str(wizard.go_button["state"]), "normal")
        wizard.go()  # closed now: it works
        wizard.update()
        self.assertEqual(wizard.outcome[0], "ok")


class Notice(unittest.TestCase):
    def test_one_time_message_is_shown(self):
        shown = []
        original = messagebox.showinfo
        messagebox.showinfo = lambda title, text, **k: shown.append(text)
        tmp = Path(tempfile.mkdtemp())
        try:
            window = app.App(tmp / "data", notice="Your settings were moved.")
            errors = drive(window, lambda: iter([1000]))
            window = None
        finally:
            messagebox.showinfo = original
            shutil.rmtree(tmp, ignore_errors=True)
            gc.collect()
        self.assertEqual(errors, [])
        self.assertEqual(shown, ["Your settings were moved."])


if __name__ == "__main__":
    unittest.main()
