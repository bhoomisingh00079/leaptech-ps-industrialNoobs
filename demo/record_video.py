"""Record a concise app walkthrough using the supplied real CSV data."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".video_deps"))
sys.path.insert(0, str(ROOT / "demo"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".video_deps" / "browsers"))

from playwright.sync_api import sync_playwright
import imageio_ffmpeg
import mock_mes_server
from linebridge import core
from linebridge.webui import Controller, make_handler
from tools.prepare_secom import prepare

APP = "http://127.0.0.1:18600"
MES = "http://127.0.0.1:18080"
SOURCE = ROOT / "real_machine_logs" / "source"


def navigate(page, url):
    """Fade between the LineBridge panel and the mock MES."""
    if page.url != "about:blank":
        page.evaluate("document.body.style.transition='opacity 400ms ease'; document.body.style.opacity='0'")
        page.wait_for_timeout(420)
    page.goto(url)
    page.add_style_tag(content="""
      body { transition: opacity 400ms ease; }
      .tab.active { animation: tabIn 550ms ease-out both; }
      @keyframes tabIn {
        from { opacity: 0; transform: translateY(9px); }
        to { opacity: 1; transform: translateY(0); }
      }
    """)
    page.evaluate("document.body.style.opacity='0'; requestAnimationFrame(() => document.body.style.opacity='1')")
    page.wait_for_timeout(600)


def tab(page, name):
    page.evaluate("window.scrollTo({top:0,behavior:'smooth'})")
    page.wait_for_timeout(550)
    button = page.locator(f'nav.tabs button[data-tab="{name}"]')
    box = button.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
    page.wait_for_timeout(250)
    button.click()
    page.mouse.move(1240, 185, steps=8)
    page.wait_for_timeout(800)


def stage(source, incoming):
    target = incoming / source.name
    shutil.copy2(source, target)
    return target


def wait_count(controller, expected, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if controller.ledger().stats()["ok"] >= expected:
            return
        time.sleep(0.2)
    raise RuntimeError(f"Expected {expected} processed files; got {controller.ledger().stats()['ok']}")


def run():
    run_dir = ROOT / "demo" / "video_workspace" / ("run_" + uuid.uuid4().hex[:8])
    incoming = run_dir / "machine_out"
    incoming.mkdir(parents=True)
    secom_batches = run_dir / "secom_batches"
    prepare(SOURCE / "secom.data", SOURCE / "secom_labels.data", secom_batches)
    qa = run_dir / "qa"
    qa.mkdir()
    output_dir = ROOT / "demo" / "video_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = core.normalize_config({
        "station_name": "Real manufacturing data demo",
        "input_folder": str(incoming), "file_pattern": "*.csv",
        "priority_patterns": ["experiment_13.csv", "secom_*.csv"],
        "backup_folder": str(run_dir / "backup"), "output_folder": str(run_dir / "output"),
        "ledger_path": str(run_dir / "ledger.db"), "output_format": "json",
        "selections": [""], "stable_seconds": 0, "poll_seconds": 1,
        "shared_enabled": True, "shared_folder": str(run_dir / "mes_shared_folder"),
        "api_enabled": True, "api_url": MES + "/api/upload", "api_token": "demo-token",
    })
    cfg_path = run_dir / "config.json"
    core.save_config(config, cfg_path)
    stage(SOURCE / "experiment_13.csv", incoming)
    stage(secom_batches / "secom_0001_0100.csv", incoming)

    mes_server, mes_state = mock_mes_server.serve(18080, "demo-token", str(run_dir / "mes_received"), block=False)
    controller = Controller(cfg_path)
    app_server = ThreadingHTTPServer(("127.0.0.1", 18600), make_handler(controller))
    app_thread = threading.Thread(target=app_server.serve_forever, daemon=True)
    app_thread.start()
    video_path = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                                                 headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1280, "height": 800},
                                          record_video_dir=str(run_dir),
                                          record_video_size={"width": 1280, "height": 800},
                                          color_scheme="light")
            page = context.new_page()
            page.set_default_timeout(30000)
            video = page.video

            navigate(page, APP)
            page.wait_for_function("document.querySelector('#kPending').textContent.trim() === '2'")
            page.wait_for_timeout(2200)
            page.screenshot(path=str(qa / "02_dashboard_before.png"))
            print("Recorded monitoring dashboard", flush=True)

            page.locator("#btnGuide").click()
            page.wait_for_timeout(1000)
            page.locator("#guideDone").click()
            page.locator("#btnAppearance").click()
            page.locator("#themeSelect").select_option("dark")
            page.locator("#appearanceDone").click()
            page.wait_for_timeout(900)
            page.locator("#btnAppearance").click()
            page.locator("#themeSelect").select_option("light")
            page.locator("#appearanceDone").click()
            for selector in ("#input_folder", "#fmtSeg", "#selGrid", "#dShared"):
                page.locator(selector).scroll_into_view_if_needed()
                page.wait_for_timeout(900)
            page.locator("#testShared").click()
            page.wait_for_timeout(1200)
            page.locator("#testApi").click()
            page.wait_for_function("document.querySelector('#rApi').textContent.includes('Connected')")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(qa / "02_setup.png"))
            print("Recorded setup, guide, appearance, and delivery tests", flush=True)


            page.locator("#fmtSeg").scroll_into_view_if_needed()
            for output_format in ("csv", "txt", "json"):
                page.locator(f'#fmtSeg button[data-v="{output_format}"]').click()
                page.wait_for_timeout(700)

            page.evaluate("window.scrollTo({top:0,behavior:'smooth'})")
            page.wait_for_timeout(500)
            page.locator("#btnIntegration").click()
            page.wait_for_function("document.querySelector('#integrationMsg').textContent.includes('Integration path is ready')")
            page.wait_for_timeout(1800)
            page.screenshot(path=str(qa / "04_check.png"))
            print("Recorded integration check", flush=True)

            tab(page, "preview")
            page.locator("#file").set_input_files(str(SOURCE / "experiment_13.csv"))
            page.locator("#tlist .titem").first.wait_for()
            page.wait_for_timeout(2200)
            page.screenshot(path=str(qa / "03_preview.png"))
            print("Recorded real CSV parser preview", flush=True)

            page.locator("#btnStart").click()
            wait_count(controller, 2)
            page.wait_for_function("document.querySelector('#kOk').textContent.trim() === '2'")
            page.wait_for_timeout(1600)
            tab(page, "outputs")
            page.locator("#outputList .output-file").filter(has_text="experiment_13").first.click()
            page.locator("#outputColumns").fill("Machining_Process")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(qa / "05_experiment_output.png"))
            page.locator("#outputList .output-file").filter(has_text="secom_0001").first.click()
            page.locator("#outputColumns").fill("yield_")
            page.wait_for_timeout(3000)
            page.screenshot(path=str(qa / "06_secom_output.png"))
            print("Recorded real-data processing and outputs", flush=True)

            tab(page, "history")
            page.wait_for_timeout(1300)
            tab(page, "log")
            page.wait_for_timeout(1300)
            page.locator("#btnStop").click()
            page.wait_for_function("document.querySelector('#btnStart').disabled === false")
            tab(page, "setup")
            page.locator('#fmtSeg button[data-v="csv"]').click()
            page.locator("#btnSave").click()
            page.wait_for_function("document.querySelector('#saveMsg').textContent.includes('Saved')")
            stage(secom_batches / "secom_0101_0200.csv", incoming)
            page.locator("#btnOnce").click()
            wait_count(controller, 3)
            tab(page, "outputs")
            page.locator("#outputList .output-file").filter(has_text="secom_0101").first.click()
            page.wait_for_timeout(2000)
            page.screenshot(path=str(qa / "07_csv_output.png"))

            tab(page, "setup")
            page.locator('#fmtSeg button[data-v="txt"]').click()
            page.locator("#btnSave").click()
            page.wait_for_function("document.querySelector('#saveMsg').textContent.includes('Saved')")
            stage(secom_batches / "secom_0201_0300.csv", incoming)
            page.locator("#btnOnce").click()
            wait_count(controller, 4)
            tab(page, "outputs")
            page.locator("#outputList .output-file").filter(has_text="secom_0201").first.click()
            page.wait_for_timeout(2000)
            page.screenshot(path=str(qa / "08_txt_output.png"))
            tab(page, "setup")
            page.locator('#fmtSeg button[data-v="json"]').click()
            page.locator("#btnSave").click()
            page.wait_for_function("document.querySelector('#saveMsg').textContent.includes('Saved')")
            page.locator("#btnStart").click()
            print("Recorded Run once and saved CSV/TXT viewers", flush=True)


            navigate(page, MES)
            page.locator("#list .it").filter(has_text="experiment_13").first.wait_for()
            page.locator("#list .it").filter(has_text="experiment_13").first.click()
            page.wait_for_function("document.querySelector('#body').textContent.includes('experiment_13.csv')")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(qa / "07_mes.png"))
            print("Recorded MES delivery", flush=True)

            page.locator("#tg").click()
            page.wait_for_function("document.querySelector('#st').textContent.includes('OUTAGE (503)')")
            stage(secom_batches / "secom_0301_0400.csv", incoming)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and controller.engine.queued()["api"] < 1:
                time.sleep(0.2)
            if controller.engine.queued()["api"] < 1:
                raise RuntimeError("Outage did not queue an API delivery")
            navigate(page, APP)
            tab(page, "log")
            page.wait_for_timeout(3500)
            page.screenshot(path=str(qa / "08_outage_queue.png"))
            navigate(page, MES)
            page.locator("#tg").click()
            page.wait_for_function("document.querySelector('#st').textContent.trim() === 'Online'")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and controller.engine.queued()["api"]:
                time.sleep(0.2)
            if controller.engine.queued()["api"]:
                raise RuntimeError("Queued API delivery did not retry")
            navigate(page, APP)
            tab(page, "history")
            page.wait_for_timeout(2000)
            page.wait_for_timeout(1600)
            page.screenshot(path=str(qa / "09_retried.png"))
            print("Recorded outage and successful retry", flush=True)

            for batch in sorted(secom_batches.glob("secom_*.csv")):
                if batch.name not in ("secom_0001_0100.csv", "secom_0101_0200.csv", "secom_0201_0300.csv", "secom_0301_0400.csv"):
                    stage(batch, incoming)
            wait_count(controller, 17, timeout=60)
            page.wait_for_function("document.querySelector('#kOk').textContent.trim() === '17'")
            tab(page, "history")
            page.wait_for_timeout(1800)
            page.screenshot(path=str(qa / "10_all_data.png"))
            print("Recorded all 17 processed source files", flush=True)
            tab(page, "log")
            page.wait_for_timeout(1800)

            page.close()
            context.close()
            browser.close()
            raw = Path(video.path())
            video_path = output_dir / "LineBridge_app_functionalities_demo.mp4"
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run([ffmpeg, "-y", "-i", str(raw), "-c:v", "libx264", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(json.dumps({"video": str(video_path), "bytes": video_path.stat().st_size,
                          "processed": controller.ledger().stats()["ok"],
                          "mes_received": len(mes_state.items), "mes_rejected": mes_state.rejected,
                          "qa": str(qa)}, indent=2))
    finally:
        controller.stop()
        app_server.shutdown()
        app_server.server_close()
        mes_server.shutdown()
        mes_server.server_close()


if __name__ == "__main__":
    run()
