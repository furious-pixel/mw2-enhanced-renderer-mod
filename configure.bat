@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=.\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -u -X faulthandler ".\tools\configure.py"
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo The configuration application failed with exit code %EXIT_CODE%.
    echo Please copy the error output above when reporting this issue.
    pause
)
exit /b %EXIT_CODE%
