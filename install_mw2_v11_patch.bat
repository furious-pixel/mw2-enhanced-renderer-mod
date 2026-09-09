@echo off
setlocal
cd /d "%~dp0"

set "PATCH_DIR=.\game\c_mech2"
set "GAME_DIR=%PATCH_DIR%\MECH2"
set "PATCH_URL=https://www.moddb.com/games/mechwarrior-2-31st-century-combat/downloads/mechwarrior-2-dos-v11-patch"
set "PATCH_SHA256=9AAF14725489742DBA164B77A9F1FD6374FA25E7981C81D272C3A881E67CFD8B"

if not exist ".\bin\dosbox-x.exe" goto missing_dosbox
if not exist "%GAME_DIR%\MW2.EXE" goto missing_game
if not exist "%GAME_DIR%\MW2.PRJ" goto missing_game

call :patch_files_ready
if not errorlevel 1 goto launch_patch

set "PATCH_ZIP="
if exist ".\mech2v11.zip" set "PATCH_ZIP=.\mech2v11.zip"
if not defined PATCH_ZIP if exist "%PATCH_DIR%\mech2v11.zip" set "PATCH_ZIP=%PATCH_DIR%\mech2v11.zip"
if not defined PATCH_ZIP goto missing_patch

echo Verifying and extracting %PATCH_ZIP%...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$zip = (Get-Item -LiteralPath '%PATCH_ZIP%').FullName; if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne '%PATCH_SHA256%') { Write-Error 'The patch ZIP checksum does not match the expected official archive.'; exit 2 }; Expand-Archive -LiteralPath $zip -DestinationPath '%PATCH_DIR%' -Force"
if errorlevel 1 goto extract_failed

call :patch_files_ready
if errorlevel 1 goto extract_failed

:launch_patch
echo Starting the official MechWarrior 2 DOS v1.1 patch.
echo Choose option 2 to patch.
echo When patching is finished, type EXIT in the DOSBox-X window.
echo.
".\bin\dosbox-x.exe" -conf ".\dosbox-mw2.conf" -noautoexec ^
  -c "MOUNT C .\game\c_mech2" ^
  -c "C:" ^
  -c "PATCH C:\MECH2"
set "DOSBOX_EXIT_CODE=%errorlevel%"
echo.
echo If you saw "Version 1.1 patching process complete" in DOSBox-X,
echo the patch was installed successfully.
echo Run configure.bat and open Game Installation to verify the game files.
echo Press any key to continue.
pause >nul
exit /b %DOSBOX_EXIT_CODE%

:missing_patch
echo.
echo The complete MechWarrior 2 DOS v1.1 patch was not found in:
echo   game\c_mech2
echo.
echo Download the patch from:
echo   %PATCH_URL%
echo.
echo Save mech2v11.zip beside install_mw2_v11_patch.bat, then run this installer again.
echo The archive will be verified and extracted automatically.
echo.
pause
exit /b 1

:extract_failed
echo.
echo The patch archive could not be verified or extracted.
echo Download a fresh copy from:
echo   %PATCH_URL%
echo.
echo Save it as mech2v11.zip beside install_mw2_v11_patch.bat, then try again.
echo.
pause
exit /b 1

:missing_game
echo.
echo The installed DOS game was not found in:
echo   game\c_mech2\MECH2
echo.
echo Copy your complete MechWarrior 2 DOS installation there, then try again.
echo.
pause
exit /b 1

:patch_files_ready
if not exist "%PATCH_DIR%\PATCH.BAT" exit /b 1
if not exist "%PATCH_DIR%\PATCH11.EXE" exit /b 1
if not exist "%PATCH_DIR%\README2.TXT" exit /b 1
if not exist "%PATCH_DIR%\WHATSNEW.TXT" exit /b 1
exit /b 0

:missing_dosbox
echo.
echo The bundled DOSBox-X executable was not found at:
echo   bin\dosbox-x.exe
echo.
echo Re-extract the complete Enhanced Renderer release, then try again.
echo.
pause
exit /b 1
