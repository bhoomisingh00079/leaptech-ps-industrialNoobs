# Recording the app walkthrough

`record_video.py` creates an isolated LineBridge workspace, stages the supplied CNC and SECOM source files, records the app and mock MES, and writes `video_output/LineBridge_app_functionalities_demo.mp4`. It does not change the normal app profile or its processed outputs.

On Windows, install the recording-only dependencies, then run:

```powershell
python -m pip install --target .video_deps playwright imageio-ffmpeg
$env:PYTHONPATH = (Resolve-Path .video_deps).Path
$env:PLAYWRIGHT_BROWSERS_PATH = (Join-Path $env:PYTHONPATH 'browsers')
python -m playwright install ffmpeg
python demo/record_video.py
```

The recorder uses Chrome at `C:\Program Files\Google\Chrome\Application\chrome.exe` and local ports 18600 and 18080. The video is silent and shows the supplied historical records in the app's real processing pipeline; the receiving MES is a local mock.
