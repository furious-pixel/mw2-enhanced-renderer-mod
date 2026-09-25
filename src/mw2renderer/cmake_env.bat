@echo off
rem Respect an explicit CMAKE executable, then PATH, then Visual Studio.
if defined CMAKE if exist "%CMAKE%" exit /b 0
for /f "delims=" %%I in ('where cmake.exe 2^>nul') do (
  set "CMAKE=%%I"
  exit /b 0
)
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -find Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`) do (
  set "CMAKE=%%I"
  exit /b 0
)
echo CMake not found. Install CMake or set CMAKE to its executable path.
exit /b 1
