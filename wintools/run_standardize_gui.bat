@echo off
setlocal

set "APP_DIR=%~dp0"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if exist "%CODEX_PY%" (
    "%CODEX_PY%" "%APP_DIR%standardize_gui.py"
    pause
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py -3 "%APP_DIR%standardize_gui.py"
    pause
    exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python "%APP_DIR%standardize_gui.py"
    pause
    exit /b %ERRORLEVEL%
)

echo Python was not found. Install Python 3.10 or newer, then run:
echo python -m pip install -r requirements.txt
echo python standardize_gui.py
pause
