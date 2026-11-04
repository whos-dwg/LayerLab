@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [LayerLab] Creating project-local virtual environment...
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv ".venv"
  ) else (
    python -c "import sys" >nul 2>nul
    if errorlevel 1 goto no_python
    python -m venv ".venv"
  )
  if errorlevel 1 goto fail
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, multipart, ezdxf" >nul 2>nul
if errorlevel 1 (
  echo [LayerLab] Installing local Python dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)

start "LayerLab local server" /min "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\run.py" serve --port 8765 --open-browser
echo [LayerLab] Open http://127.0.0.1:8765 to use LayerLab.
exit /b 0

:no_python
echo [LayerLab] Python 3.11 or newer was not found on PATH.
pause
exit /b 1

:fail
echo [LayerLab] Startup failed. Check Python 3.11+ and your network connection.
pause
exit /b 1
