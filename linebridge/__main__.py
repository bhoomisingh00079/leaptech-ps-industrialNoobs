"""
LineBridge command line.

  python -m linebridge ui      [--config config.json] [--port 8600] [--host 127.0.0.1] [--start]
  python -m linebridge run     [--config config.json] [--once]
  python -m linebridge preview  FILE.csv [--config config.json]
  python -m linebridge history [--config config.json]
"""
import argparse
import logging
import sys
from pathlib import Path

from . import core


def main(argv=None):
    ap = argparse.ArgumentParser(prog="linebridge", description="Machine CSV -> MES-ready JSON/CSV/TXT bridge")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("ui", "run", "preview", "history"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default="config.json")
        if name == "ui":
            sp.add_argument("--port", type=int, default=8600)
            sp.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow access from other PCs")
            sp.add_argument("--start", action="store_true", help="start watching immediately")
        if name == "run":
            sp.add_argument("--once", action="store_true", help="process what is there now and exit")
        if name == "preview":
            sp.add_argument("file")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    if a.cmd in (None, "ui"):
        from .webui import serve
        a = a if a.cmd else ap.parse_args(["ui"] + (argv or sys.argv[1:]))
        serve(a.config, a.host, a.port, a.start)
    elif a.cmd == "run":
        eng = core.Engine(core.load_config(a.config))
        if a.once:
            print(f"Processed {eng.scan_once()} file(s)")
        else:
            try:
                eng.run_forever()
            except KeyboardInterrupt:
                eng.stop()
    elif a.cmd == "preview":
        cfg = core.load_config(a.config)
        rows = core.read_rows(a.file)
        text, chosen, warnings, tables = core.build_output(Path(a.file).name, "(preview)", rows, cfg)
        print("Tables found in file:")
        for n, t in tables.items():
            print(f"  {n:<16} {t['range']:<10} {len(t['rows']):>4} rows  {', '.join(t['columns'])}")
        for w in warnings:
            print("WARNING:", w)
        print(f"\nSelected: {', '.join(chosen) or 'nothing'}\n" + "-" * 60)
        print(text)
    elif a.cmd == "history":
        for r in core.Ledger(core.load_config(a.config)["ledger_path"]).recent(30):
            print(f"{r['processed_at']}  {r['status']:<9} {r['source']:<28} -> {r['output'] or '-':<36} "
                  f"shared={r['shared'] or '-'} api={r['api'] or '-'}  {r['message'] or ''}")


if __name__ == "__main__":
    main()
