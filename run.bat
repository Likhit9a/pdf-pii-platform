@echo off
setlocal
cd /d "%~dp0"
title PDF PII Redaction Platform

rem ---- find Python 3.10-3.12 (spaCy wheels are not available for every newer version) ----
set "PY="
for %%V in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python 3.10-3.12 was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"

if not exist ".venv\deps_installed.txt" (
  echo Installing dependencies. First run only; this can take a few minutes...
  "%VPY%" -m pip install --upgrade pip >nul
  "%VPY%" -m pip install -r requirements.txt || goto :fail
  echo ok> ".venv\deps_installed.txt"
)

if /i "%~1"=="test" (
  "%VPY%" -m pytest -q
  pause
  exit /b %errorlevel%
)

"%VPY%" -c "import sys; sys.path.insert(0,'src'); from pii_platform.config import Settings; from pii_platform.extraction import ocr_available; print('OCR for scanned PDFs: available' if ocr_available(Settings.from_env()) else 'NOTE: Tesseract OCR not found - scanned PDFs will be rejected (see README).')"

echo.
echo Starting the app. Your browser should open at http://localhost:8501
echo Close this window (or press Ctrl+C) to stop it.
echo.| "%VPY%" -m streamlit run app.py
goto :eof

:fail
echo.
echo Setup failed. Copy the messages above and send them to your coding agent.
pause
exit /b 1
