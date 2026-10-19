"""Read-only saved-output access for the panel."""
import tempfile
import unittest
from pathlib import Path

from linebridge import core
from linebridge.webui import list_outputs, read_output


class OutputViewer(unittest.TestCase):
    def test_lists_and_reads_all_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cfg = {"output_folder": str(folder)}
            rows = core.read_rows(Path(__file__).resolve().parents[1] / "samples" / "pnp_L1M8_board_00001.csv")
            for fmt in core.FORMATS:
                config = core.normalize_config({"output_format": fmt})
                content, _, _, _ = core.build_output("sample.csv", "abc", rows, config)
                (folder / f"result.{fmt}").write_text(content, encoding="utf-8")
            (folder / "_outbox").mkdir()
            (folder / ".partial.json").write_text("incomplete", encoding="utf-8")
            self.assertEqual({f["format"] for f in list_outputs(cfg)}, set(core.FORMATS))
            for fmt in core.FORMATS:
                result = read_output(cfg, f"result.{fmt}")
                self.assertEqual(result["format"], fmt)
                self.assertIn("sample.csv", result["content"])

    def test_rejects_paths_outside_output_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"output_folder": tmp}
            for name in ("../secret.json", "..\\secret.json", "missing.json", "other.db"):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    read_output(cfg, name)


if __name__ == "__main__":
    unittest.main()
