@echo off
setlocal
cd /d "%~dp0"
".\bin\dosbox-x.exe" -console -python -moddir mw2mods -log-fileio -conf ".\dosbox-mw2.conf"
