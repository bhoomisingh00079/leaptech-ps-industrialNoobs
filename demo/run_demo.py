"""
One-command live demo for the pitch.

Starts three things on this PC:
  1. a pick-and-place machine simulator that drops a CSV log every few seconds
  2. a mock customer MES server (the "end user's site") at http://localhost:8080
  3. the LineBridge control panel at http://localhost:8600, already watching

    python demo/run_demo.py            (Ctrl+C to stop)
    python demo/run_demo.py --reset    (start from a clean workspace)
"""
import argparse
import json
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

import machine_simulator  # noqa: E402
import mock_mes_server  # noqa: E402

WS = ROOT / "demo" / "workspace"


def demo_config():
    return {
        "station_name": "SMT Line 1 · Machine 8",
        "input_folder": str(WS / "machine_out"),
        "backup_folder": str(WS / "backup"),
        "output_folder": str(WS / "output"),
        "output_format": "json",
        "selections": ["HEADER", "L1xM8[Timestamp,Ref,Part_No,Result]", "ERRORS", "NOZZLE_STATS", "", "", "", "", "", ""],
        "manual_inputs": [{"label": "Operator", "value": "OP-17"}, {"label": "Work order", "value": "WO-1187"}],
        "shared_enabled": True,
        "shared_folder": str(WS / "mes_shared_folder"),
        "api_enabled": True,
        "api_url": "http://localhost:8080/api/upload",
        "api_token": "demo-token",
        "api_body": "raw",
        "poll_seconds": 2,
        "stable_seconds": 2,
        "ledger_path": str(WS / "linebridge_ledger.db"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--every", type=float, default=7, help="seconds between boards")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--ui-port", type=int, default=8600)
    ap.add_argument("--mes-port", type=int, default=8080)
    a = ap.parse_args()

    if a.reset and WS.exists():
        shutil.rmtree(WS)
    WS.mkdir(parents=True, exist_ok=True)
    cfg_path = WS / "demo_config.json"
    cfg = demo_config()
    cfg["api_url"] = f"http://localhost:{a.mes_port}/api/upload"
    cfg_path.write_text(json.dumps(cfg, indent=2))

    mock_mes_server.serve(a.mes_port, "demo-token", str(WS / "mes_received"), block=False)
    threading.Thread(target=machine_simulator.run, daemon=True,
                     kwargs=dict(out_dir=WS / "machine_out", every=a.every, duplicate_every=6)).start()

    print("\n  LineBridge demo running")
    print(f"  Control panel  : http://localhost:{a.ui_port}")
    print(f"  Customer MES   : http://localhost:{a.mes_port}")
    print(f"  Workspace      : {WS}")
    print("  Try: click 'Simulate outage' on the MES page, wait, then restore it - queued files are re-sent.\n")
    if not a.no_browser:
        threading.Timer(1.5, lambda: (webbrowser.open(f"http://localhost:{a.mes_port}"),
                                      webbrowser.open(f"http://localhost:{a.ui_port}"))).start()

    from linebridge.webui import serve
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    serve(str(cfg_path), "127.0.0.1", a.ui_port, autostart=True)


if __name__ == "__main__":
    main()
