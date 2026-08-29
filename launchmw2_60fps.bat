@echo off
setlocal
cd /d "%~dp0"
".\bin\dosbox-x.exe" -console -python -moddir mw2mods -log-fileio -conf ".\dosbox-mw2.conf" ^
  -set "sdl fullscreen=true" ^
  -set "sdl fullresolution=desktop" ^
  -set "sdl showmenu=false" ^
  -set "render mod renderer start view=mod-only" ^
  -set "render mod renderer target fps=60" ^
  -set "render mod renderer host vsync=true" ^
  -set "vsync vsyncmode=off" ^
  -set "cpu cycles=auto"
