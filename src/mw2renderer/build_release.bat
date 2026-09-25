@echo off
setlocal
set "ROOT=%~dp0"
call "%ROOT%cmake_env.bat"
if errorlevel 1 exit /b 1
set "CLEAN_ENV=%ROOT%..\..\tools\run_clean_env.ps1"
rem Portable x64 Release. Builds only the renderer DLL by default.
pushd "%ROOT%"
where pwsh.exe >nul 2>nul
set "PS=pwsh.exe"
if errorlevel 1 set "PS=powershell.exe"
%PS% -NoProfile -ExecutionPolicy Bypass -File "%CLEAN_ENV%" -FilePath "%CMAKE%" -CommandLine "--preset windows-msvc -S ."
if errorlevel 1 goto build_failed
%PS% -NoProfile -ExecutionPolicy Bypass -File "%CLEAN_ENV%" -FilePath "%CMAKE%" -CommandLine "--build build --config Release --target mw2renderer"
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%

:build_failed
popd
exit /b 1
