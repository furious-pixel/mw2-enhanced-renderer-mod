@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\tools\launch_mw2.ps1" %*
exit /b %errorlevel%
