"""The activity log window and the Help window."""
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import diagnostics, theme
from .. import help as help_text
from ..config import APP_NAME, VERSION
from ..i18n import tr


class WindowsMixin:
    def _build_log(self):
        """The activity log, in a window of its own (View > Activity log). It keeps filling while hidden."""
        win = self.log_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        if self.theme.dark:
            theme.dark_title_bar(win)
        win.title(tr("{APP_NAME} activity log", APP_NAME=APP_NAME))
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(self.px(980 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(600 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(560), width), min(self.px(320), height))
        frame = ttk.Frame(win, padding=(14, 12, 14, 0))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=tr("Technical processing and delivery messages. Most day-to-day status is shown in "
                              "History."), style="PageHint.TLabel").pack(anchor="w", pady=(0, 8))
        log_area = ttk.Frame(frame, style="Card.TFrame", padding=1)
        log_area.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_area, wrap="none", font=(self._font_mono, self.L.text_mono), state="disabled",
                                relief="flat", borderwidth=0, background=theme.SURFACE, foreground=theme.TEXT,
                                insertbackground=theme.TEXT, highlightthickness=0, padx=9, pady=9, takefocus=True)
        self.log_text.tag_configure("WARNING", foreground=theme.WARNING)
        self.log_text.tag_configure("ERROR", foreground=theme.ERROR)
        ys = ttk.Scrollbar(log_area, orient="vertical", command=self.log_text.yview)
        xs = ttk.Scrollbar(log_area, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.log_text.pack(fill="both", expand=True)
        self.log_empty = ttk.Label(log_area, style="EmptyState.TLabel",
                                   text=tr("No activity messages yet\n\nMessages will appear as CNCBridge works."))
        self.log_empty.place(relx=0.5, rely=0.5, anchor="center")
        bar = ttk.Frame(win, padding=(14, 10))
        bar.pack(fill="x")
        ttk.Button(bar, text=tr("Close"), command=win.withdraw).pack(side="right")
        ttk.Button(bar, text=tr("Open data folder"), command=lambda: self._open_folder(self.data_dir)).pack(side="right",
                                                                                                         padx=8)

    def open_log(self):
        self.log_win.deiconify()
        self.log_win.lift()
        if self.theme.dark:
            theme.dark_title_bar(self.log_win)
        self.log_text.see("end")

    # ================================================================ help
    def _build_help(self):
        win = self.help_win = tk.Toplevel(self, background=theme.BACKGROUND)
        win.withdraw()
        if self.theme.dark:
            theme.dark_title_bar(win)
        win.title(tr("{APP_NAME} help", APP_NAME=APP_NAME))
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(self.px(980 * self.text_scale), self.winfo_screenwidth() - self.px(30))
        height = min(self.px(660 * self.text_scale), self.winfo_screenheight() - self.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(self.px(640), width), min(self.px(400), height))
        panes = ttk.PanedWindow(win, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=14, pady=14)
        topics = self.help_topics = ttk.Treeview(panes, show="tree", selectmode="browse")
        topics.column("#0", width=self.px(300))
        for key, title, _ in self.help_topic_data:
            topics.insert("", "end", iid=key, text=tr(title))
        topics.bind("<<TreeviewSelect>>", self._help_selected)
        panes.add(topics, weight=1)
        body = ttk.Frame(panes, style="Card.TFrame", padding=1)
        text = self.help_body = tk.Text(body, wrap="word", state="disabled", relief="flat", borderwidth=0,
                                        background=theme.SURFACE, foreground=theme.TEXT, highlightthickness=0,
                                        padx=18, pady=14, font=(self._font_body, 11), cursor="arrow",
                                        spacing3=3)
        ys = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.tag_configure("h1", font=self.strong(15), spacing3=6)
        text.tag_configure("h2", font=self.strong(12), spacing1=10, spacing3=4)
        text.tag_configure("bullet", lmargin1=self.px(12), lmargin2=self.px(28))
        text.tag_configure("gap", font=(self._font_body, 5))
        text.tag_configure("code", font=(self._font_mono, 10), background=theme.SURFACE_ALT, lmargin1=self.px(12),
                           lmargin2=self.px(12), spacing3=0)
        panes.add(body, weight=4)

    def _help_selected(self, _event=None):
        selection = self.help_topics.selection()
        if selection:
            self._render_help(selection[0])

    def _render_help(self, key):
        title, body = next((t, b) for k, t, b in self.help_topic_data if k == key)
        title, body = tr(title), tr(body)
        if key == "keys":
            body = (tr("The shortcut is shown beside each menu entry. A shortcut does nothing while its entry is "
                    "disabled (for example while the setup is locked).\n\n")
                    + "\n".join(f"    {keys:<20}{tr(what)}" for keys, what in help_text.SHORTCUTS))
        text = self.help_body
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", title + "\n", "h1")
        for line in body.split("\n"):
            if line.startswith("## "):
                text.insert("end", line[3:] + "\n", "h2")
            elif line.startswith("- "):
                text.insert("end", "•  " + line[2:] + "\n", "bullet")
            elif line.startswith("    "):
                text.insert("end", line + "\n", "code")
            elif not line.strip():
                text.insert("end", "\n", "gap")
            else:
                text.insert("end", line + "\n")
        text.configure(state="disabled")
        text.yview_moveto(0)

    def open_help(self, key="start"):
        self.help_topics.selection_set(key)
        self.help_topics.see(key)
        self._render_help(key)
        self.help_win.deiconify()
        self.help_win.lift()
        if self.theme.dark:
            theme.dark_title_bar(self.help_win)

    def export_diagnostics(self):
        """Help > Export diagnostics: one zip for support (log, settings without passwords, history, versions)."""
        path = filedialog.asksaveasfilename(
            parent=self, title=tr("Export diagnostics"), defaultextension=".zip",
            initialfile=diagnostics.default_name(self.cfg),
            filetypes=[(tr("Zip files"), "*.zip"), (tr("All files"), "*.*")])
        if not path:
            return
        self.status_msg.set(tr("Collecting the diagnostics..."))
        cfg = dict(self.cfg)

        def job():
            try:
                diagnostics.build(self.data_dir, path, cfg)
            except OSError as e:
                error = str(e)  # e is gone once this block ends
                self.ui_q.put(lambda: (self.status_msg.set(tr("The diagnostics were not saved.")), messagebox.showerror(
                    APP_NAME, tr("The diagnostics could not be saved:\n{e}", e=error), parent=self)))
                return
            self.ui_q.put(lambda: self._diagnostics_saved(path))
        threading.Thread(target=job, daemon=True).start()

    def _diagnostics_saved(self, path):
        self.status_msg.set(tr("Diagnostics saved in {file}. Send this one file to support; it holds no passwords.",
                               file=path))
        try:
            subprocess.Popen(["explorer", f"/select,{path}"])  # the folder opens with the file selected
        except OSError:
            pass

    def about(self):
        messagebox.showinfo(
            tr("About {APP_NAME}", APP_NAME=APP_NAME),
            tr("{APP_NAME} {VERSION}\n\nTakes the CSV file an assembly machine writes for every board, cuts out the cell ranges you choose, and delivers the result as JSON, CSV, TXT or PDF to a shared folder, an API link and/or an FTP server.\n\nData folder:\n{v2}\n\nThe bundled fonts Roboto, Roboto Mono, IBM Plex Sans and IBM Plex Mono are used under the SIL Open Font License.", APP_NAME=APP_NAME, VERSION=VERSION, v2=self.data_dir), parent=self)
