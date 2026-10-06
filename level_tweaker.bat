@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\tools\level_tweak\launch.ps1" -PanelOnly -Channel "%~1"
if errorlevel 1 pause
