@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW=.\.venv\Scripts\pythonw.exe"
if exist "%PYTHONW%" (
    start "" "%PYTHONW%" ".\tools\configure.py"
    exit /b 0
)

set "PYTHON=python"
"%PYTHON%" ".\tools\configure.py"
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo The configuration application could not be started.
    pause
)
exit /b %EXIT_CODE%
