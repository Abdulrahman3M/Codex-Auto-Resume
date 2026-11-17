@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
  if not errorlevel 1 (
    py -3 -m codex_resume %*
    exit /b
  )
)

where python >nul 2>nul
if errorlevel 1 goto :missing
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 goto :badpython
python -m codex_resume %*
exit /b

:missing
echo Python 3 was not found. Install Python 3.9 or later. 1>&2
exit /b 127

:badpython
echo Python version is too old. Python 3.9 or later is required. 1>&2
exit /b 2
