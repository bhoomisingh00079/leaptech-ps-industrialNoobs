@echo off
cd /d "%~dp0"
if not exist config.json (
  copy config.secom.json config.json >nul
  python tools\stage_real_logs.py
  if errorlevel 1 exit /b 1
)
python -m linebridge ui --config config.json
pause
