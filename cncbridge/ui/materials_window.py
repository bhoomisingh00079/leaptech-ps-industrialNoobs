"""Optional Materials window, import report and settings page."""
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import alerts, theme
from ..i18n import tr
from ..ysup import materials
from .scrollframe import ScrollFrame


HELP_TOPIC = ("materials", "Materials database", """\
The Materials window shows the imported customer CSV as a read-only searchable list. Use Import database to \
review a CSV report before replacing the current database. The standard columns are Part Number, Description, \
Reel ID, LOT ID and Quantity; other customer columns are retained. Optional automatic import watches the folder \
set under Settings > Options > Materials. A good CSV replaces the database and moves into its imported folder; \
a failed import keeps the previous database and raises an alert.

Part Number aliases include Part, PartNumber and CmpID. Reel, lot and quantity columns are detected by name. \
The options page controls which values a later YSUP lookup compares and lists any extra customer fields.
""")


class MaterialsExtension:
    """A removable App extension that owns the Materials UI and hands the shared CSV to YSUP lookups."""
    def __init__(self, app):
        self.app = app
        self.window = None
        self.import_button = None
        self.database_var = None
        self.import_folder_var = None
        self.field_vars = {}
        self.extra_fields = None
        self.materials = None
        self._import_alerts = None
        self._poll_job = None

    def help_topics(self):
        return (HELP_TOPIC,)

    def add_menus(self):
        menu = self.app.menus["Tools"]
        menu.add_command(label=tr("Materials..."), command=self.open_materials)
        self.app.menu_items["materials"] = (menu, menu.index("end"))

    def option_pages(self):
        page = ScrollFrame(self.app.options_tabs, theme.BACKGROUND, padding=self.app.L.tab_pad)
        self.app.options_tabs.add(page, text=f"  {tr('Materials')}  ")
        self.app.option_pages["materials"] = page
        self._build_material_options(page.body)

    def _build_material_options(self, parent):
        app = self.app
        self.database_var = tk.StringVar(app)
        self.import_folder_var = tk.StringVar(app)
        self.field_vars = {name: tk.BooleanVar(app) for name in ("part_number", "reel_id", "lot_id")}
        self.extra_fields = tk.Text(parent, height=4, wrap="word", relief="flat", borderwidth=0,
                                    background=theme.SURFACE, foreground=theme.TEXT,
                                    insertbackground=theme.TEXT, highlightthickness=1,
                                    highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT,
                                    padx=8, pady=6, font=(app._font_mono, app.L.text_mono))

        database_box = ttk.LabelFrame(parent, text=tr(" Shared material database "), padding=app.L.box_pad)
        database_box.pack(fill="x")
        database_box.columnconfigure(1, weight=1)
        ttk.Label(database_box, text=tr("CSV file"), style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(database_box, textvariable=self.database_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(database_box, text=tr("Browse..."), command=self._browse_database).grid(row=0, column=2)
        ttk.Label(database_box, text=tr("Automatic import folder"), style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(database_box, textvariable=self.import_folder_var).grid(row=1, column=1, sticky="ew", padx=8,
                                                                          pady=(8, 0))
        ttk.Button(database_box, text=tr("Browse..."), command=self._browse_import_folder).grid(
            row=1, column=2, pady=(8, 0))
        ttk.Label(database_box, style="CardSmall.TLabel", wraplength=app.px(560), justify="left",
                  text=tr("A relative database path is stored under the data folder. Automatic imports are checked "
                          "while monitoring, and while the window is open when monitoring is stopped." )).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        fields_box = ttk.LabelFrame(parent, text=tr(" Fields used by YSUP lookups "), padding=app.L.box_pad)
        fields_box.pack(fill="x", pady=(app.L.box_gap, 0))
        for row, (key, label) in enumerate((("part_number", tr("Part Number")), ("reel_id", tr("Reel ID")),
                                             ("lot_id", tr("Lot ID")))):
            ttk.Checkbutton(fields_box, text=label, variable=self.field_vars[key]).grid(
                row=row, column=0, sticky="w", pady=3)
        ttk.Label(fields_box, text=tr("Spare customer fields (one per line)"), style="Card.TLabel").grid(
            row=3, column=0, sticky="w", pady=(8, 4))
        self.extra_fields.grid(row=4, column=0, sticky="ew")
        fields_box.columnconfigure(0, weight=1)

    def _browse_database(self):
        path = filedialog.asksaveasfilename(parent=self.app, title=tr("Material database file"),
                                            initialfile=Path(self.database_var.get() or "ysup_materials.csv").name,
                                            defaultextension=".csv", filetypes=((tr("CSV files"), "*.csv"),))
        if path:
            self.database_var.set(path)

    def _browse_import_folder(self):
        path = filedialog.askdirectory(parent=self.app, title=tr("Automatic import folder"), mustexist=False)
        if path:
            self.import_folder_var.set(path)

    def fill(self, cfg):
        if self.database_var is None:
            return
        self.database_var.set(cfg["ysup_database"])
        self.import_folder_var.set(cfg["ysup_import_folder"])
        for field, variable in self.field_vars.items():
            variable.set(cfg["ysup_fields"].get(field, materials.DEFAULT_FIELDS[field]))
        self.extra_fields.delete("1.0", "end")
        self.extra_fields.insert("1.0", "\n".join(cfg["ysup_extra_fields"]))

    def collect(self, cfg):
        if self.database_var is None:
            return
        cfg["ysup_database"] = self.database_var.get()
        cfg["ysup_import_folder"] = self.import_folder_var.get()
        cfg["ysup_fields"] = {key: variable.get() for key, variable in self.field_vars.items()}
        cfg["ysup_extra_fields"] = [name.strip() for name in re.split(r"[,\n]", self.extra_fields.get("1.0", "end"))
                                    if name.strip()]

    def lock_changed(self, locked):
        if self.import_button is not None:
            self.import_button.configure(state="disabled" if locked else "normal")

    def start(self):
        self._import_alerts = alerts.Alerts(self.app.data_dir, clear=False)
        self._schedule_import_check()

    def _schedule_import_check(self):
        if not self.app.closing.is_set():
            self._poll_job = self.app.after(2500, self._check_import_folder)

    def _check_import_folder(self):
        if self.app.cfg.get("ysup_import_folder") and not self.app.processing and not self.app.busy:
            report = materials.import_pending(self.app.cfg, self.app.data_dir, self._import_alerts)
            if report and self.window is not None and self.window.state() != "withdrawn":
                self.refresh_materials()
        self._schedule_import_check()

    def open_materials(self):
        if self.window is None:
            self._build_window()
        self.refresh_materials()
        self.window.deiconify()
        self.window.lift()
        if self.app.theme.dark:
            theme.dark_title_bar(self.window)

    def _build_window(self):
        app = self.app
        win = self.window = tk.Toplevel(app, background=theme.BACKGROUND)
        win.withdraw()
        win.title(tr("Materials database"))
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        width = min(app.px(1050 * app.text_scale), app.winfo_screenwidth() - app.px(30))
        height = min(app.px(650 * app.text_scale), app.winfo_screenheight() - app.px(120))
        win.geometry(f"{width}x{height}")
        win.minsize(min(app.px(650), width), min(app.px(400), height))
        top = ttk.Frame(win, padding=(14, 12, 14, 8))
        top.pack(fill="x")
        ttk.Label(top, text=tr("Materials"), font=app.strong(14)).pack(anchor="w")
        self.last_import_label = ttk.Label(top, text="", style="PageHint.TLabel")
        self.last_import_label.pack(anchor="w", pady=(3, 8))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text=tr("Search")).pack(side="left")
        self.search_var = tk.StringVar(app)
        self.search_var.trace_add("write", lambda *_: self._fill_material_rows())
        ttk.Entry(row, textvariable=self.search_var, width=36).pack(side="left", padx=(7, 14))
        self.count_label = ttk.Label(row, text="", style="PageHint.TLabel")
        self.count_label.pack(side="left")
        body = ttk.Frame(win, style="Card.TFrame", padding=1)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.tree = ttk.Treeview(body, show="headings", selectmode="browse")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)
        bottom = ttk.Frame(win, padding=(14, 4, 14, 12))
        bottom.pack(fill="x")
        ttk.Button(bottom, text=tr("Close"), command=win.withdraw).pack(side="right")
        ttk.Button(bottom, text=tr("Export CSV..."), command=self.export_database).pack(side="right", padx=8)
        self.import_button = ttk.Button(bottom, text=tr("Import database..."), style="Go.TButton",
                                        command=self.import_database)
        self.import_button.pack(side="right")
        self.lock_changed(not app.unlocked)

    def refresh_materials(self):
        try:
            self.materials = materials.load(self.app.cfg, self.app.data_dir)
        except (OSError, ValueError) as error:
            self.materials = None
            messagebox.showerror(tr("Materials database"), tr("Cannot read the material database: {error}",
                                                               error=error), parent=self.app)
        if not hasattr(self, "tree"):
            return
        if self.materials is None:
            self.last_import_label.configure(text=tr("No material database has been imported."))
            self.tree.configure(columns=())
            self.count_label.configure(text=tr("0 materials"))
            return
        imported = self.materials["imported_at"]
        self.last_import_label.configure(text=tr("Last import: {time} · {file} · {rows} rows",
                                                 time=imported, file=self.materials["source"],
                                                 rows=self.materials["rows_kept"]))
        self._fill_material_rows()

    def _fill_material_rows(self):
        if not hasattr(self, "tree"):
            return
        tree = self.tree
        tree.delete(*tree.get_children())
        data = self.materials
        if data is None:
            self.count_label.configure(text=tr("0 materials"))
            return
        headers = data["headers"]
        columns = [f"c{index}" for index in range(len(headers))]
        tree.configure(columns=columns)
        for index, (column, heading) in enumerate(zip(columns, headers)):
            tree.heading(column, text=heading or tr("Column {number}", number=index + 1), anchor="w")
            tree.column(column, width=self.app.px(150), minwidth=self.app.px(70), anchor="w", stretch=True)
        query = self.search_var.get().strip().casefold()
        matches = [record for record in data["records"]
                   if not query or query in " ".join(record["extra"].values()).casefold()]
        for record in matches[:1000]:
            tree.insert("", "end", values=[record["extra"].get(header, "") for header in headers])
        shown = min(len(matches), 1000)
        self.count_label.configure(text=tr("{shown} of {total} materials shown", shown=shown, total=len(matches)))

    def export_database(self):
        data = self.materials or materials.load(self.app.cfg, self.app.data_dir)
        if data is None:
            return messagebox.showinfo(tr("Materials database"), tr("There is no database to export."),
                                       parent=self.app)
        path = filedialog.asksaveasfilename(parent=self.app, title=tr("Export material database"),
                                            defaultextension=".csv", initialfile=Path(data["path"]).name,
                                            filetypes=((tr("CSV files"), "*.csv"),))
        if path:
            try:
                Path(path).write_bytes(Path(data["path"]).read_bytes())
            except OSError as error:
                messagebox.showerror(tr("Export failed"), str(error), parent=self.app)

    def import_database(self):
        if not self.app.unlocked:
            return messagebox.showerror(tr("Admin sign-in required"),
                                        tr("Sign in as Admin to import a material database."), parent=self.app)
        path = filedialog.askopenfilename(parent=self.app, title=tr("Choose material CSV"),
                                          filetypes=((tr("CSV files"), "*.csv"), (tr("All files"), "*.*")))
        if not path:
            return
        try:
            content = Path(path).read_bytes()
            report = materials.import_report(content, Path(path).name)
        except (OSError, ValueError) as error:
            return messagebox.showerror(tr("Material import failed"), str(error), parent=self.app)
        if not self._show_import_report(report):
            return
        try:
            materials.replace_database(content, self.app.collect(), self.app.data_dir, Path(path).name)
        except (OSError, ValueError) as error:
            messagebox.showerror(tr("Material import failed"),
                                 tr("The existing database was kept. {error}", error=error), parent=self.app)
            return
        self.refresh_materials()
        self.app.status_msg.set(tr("Material database imported: {rows} rows kept.", rows=report["rows_kept"]))

    @staticmethod
    def _report_text(report):
        lines = [f"File: {report['filename']}", f"Rows read: {report['rows_read']}",
                 f"Rows kept: {report['rows_kept']}", f"Rows refused: {report['rows_refused']}", "", "Column mapping:"]
        for field in ("part_number", "reel_id", "lot_id", "quantity"):
            lines.append(f"  {field.replace('_', ' ').title()}: {report['mapping'].get(field) or '(not found)'}")
        lines.append("  Extra fields: " + (", ".join(report["mapping"]["extra_fields"]) or "(none)"))
        if report["rows_refused"]:
            lines.extend(("", "Rows refused by reason:"))
            reason_labels = {"no_part_number": tr("No Part Number"),
                             "duplicate_reel_id": tr("Duplicate Reel ID")}
            lines.extend(f"  Row {item['row']}: {reason_labels.get(item['reason'], item['reason'])}"
                         for item in report["refused"])
        if report["warnings"]:
            lines.extend(("", "Warnings (rows are kept):"))
            lines.extend(f"  Row {item['row']}: "
                         f"{tr('Quantity is not a number; this alone does not exclude the row.')}"
                         for item in report["warnings"])
        return "\n".join(lines)

    def _show_import_report(self, report):
        win = tk.Toplevel(self.app)
        win.title(tr("Review material import"))
        win.transient(self.app)
        win.geometry(f"{self.app.px(700)}x{self.app.px(510)}")
        result = tk.BooleanVar(win, False)
        ttk.Label(win, text=tr("Review the report before replacing the current database."),
                  style="PageHint.TLabel").pack(anchor="w", padx=14, pady=(14, 6))
        body = ttk.Frame(win, style="Card.TFrame", padding=1)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        text = tk.Text(body, wrap="none", state="normal", relief="flat", borderwidth=0,
                       background=theme.SURFACE, foreground=theme.TEXT,
                       font=(self.app._font_mono, self.app.L.text_mono), padx=9, pady=8)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", self._report_text(report))
        text.configure(state="disabled")
        buttons = ttk.Frame(win, padding=(14, 0, 14, 14))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=tr("Cancel"), command=win.destroy).pack(side="right")
        replace = ttk.Button(buttons, text=tr("Replace database"), style="Go.TButton",
                             command=lambda: (result.set(True), win.destroy()))
        replace.pack(side="right", padx=8)
        replace.configure(state="normal" if report["rows_kept"] else "disabled")
        ttk.Button(buttons, text=tr("Save template CSV..."), command=self.save_template).pack(side="left")
        win.bind("<Escape>", lambda _event: win.destroy())
        win.grab_set()
        self.app.wait_window(win)
        return result.get()

    def save_template(self):
        path = filedialog.asksaveasfilename(parent=self.app, title=tr("Save material CSV template"),
                                            defaultextension=".csv", initialfile="ysup_materials_template.csv",
                                            filetypes=((tr("CSV files"), "*.csv"),))
        if path:
            try:
                Path(path).write_bytes(materials.template_csv())
            except OSError as error:
                messagebox.showerror(tr("Template save failed"), str(error), parent=self.app)
