"""Window behavior for the optional Materials extension (requires the real CNCBridge test window)."""
import os
import unittest
from unittest import mock

if os.environ.get("CNCBRIDGE_WINDOW_TESTS") != "1":
    raise unittest.SkipTest("window tests open real windows; set CNCBRIDGE_WINDOW_TESTS=1 to run them")

from tkinter import filedialog  # noqa: E402
from cncbridge.ysup import materials  # noqa: E402
from tests.test_window import WindowTest  # noqa: E402


class MaterialsWindow(WindowTest):
    def extension(self):
        return next(ext for ext in self.window.extensions if hasattr(ext, "open_materials"))

    def test_materials_window_searches_the_imported_rows(self):
        app = self.window
        extension = self.extension()

        def steps():
            materials.replace_database(
                b"Part Number,Description,Reel ID\nP-100,Stepper motor,R-1\nP-200,Control board,R-2\n",
                app.cfg, app.data_dir, "customer.csv")
            extension.open_materials()
            app.update()
            self.assertIn("2 of 2", extension.count_label.cget("text"))
            extension.search_var.set("motor")
            app.update()
            self.assertIn("1 of 1", extension.count_label.cget("text"))
            values = extension.tree.item(extension.tree.get_children()[0], "values")
            self.assertEqual(values[0], "P-100")
            yield 100
        self.run_steps(steps)

    def test_import_dialog_shows_report_then_replaces_the_database(self):
        app = self.window
        extension = self.extension()
        source = self.tmp / "customer.csv"
        source.write_bytes(b"CmpID,Description,UniqueID,Quantity\nP-300,Capacitor,R-300,500\n")
        shown = []
        self._patch(filedialog, "askopenfilename", lambda **_kwargs: str(source))
        self._patch(extension, "_show_import_report", lambda report: shown.append(report) or True)

        def steps():
            extension.import_database()
            self.assertEqual(shown[0]["rows_kept"], 1)
            self.assertEqual(shown[0]["mapping"]["part_number"], "CmpID")
            loaded = materials.load(app.cfg, app.data_dir)
            self.assertEqual(loaded["records"][0]["part_number"], "P-300")
            self.assertEqual(loaded["source"], source.name)
            yield 100
        self.run_steps(steps)

    def test_operator_can_view_materials_but_cannot_import(self):
        app = self.window
        extension = self.extension()
        source = self.tmp / "operator.csv"
        source.write_bytes(b"Part Number\nP-400\n")
        self._patch(filedialog, "askopenfilename", lambda **_kwargs: str(source))

        def steps():
            app.toggle_lock()
            extension.open_materials()
            self.assertEqual(str(extension.import_button["state"]), "disabled")
            extension.import_database()
            self.assertEqual(self.dialogs[-1][0], "showerror")
            self.assertIsNone(materials.load(app.cfg, app.data_dir))
            yield 100
        self.run_steps(steps)


if __name__ == "__main__":
    unittest.main()
