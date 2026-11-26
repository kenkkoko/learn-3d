@echo off
rem Learn 3D bridge starter. ASCII only on purpose: cmd misreads UTF-8 batch
rem files on some Windows setups. Works from the downloaded zip folder and from
rem the repo's windows\ folder.
setlocal
title Learn 3D Bridge
if "%LEARN3D_PORT%"=="" (set "PORT=8766") else (set "PORT=%LEARN3D_PORT%")
set "SITE=https://learn.hellokai07.com"
rem Opened from the website's learn3d-bridge:// link: the site is already open, don't open it again.
if not "%~1"=="" set "LEARN3D_NO_BROWSER=1"

rem -- already running? ------------------------------------------------------
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%PORT%/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo Learn 3D bridge is already running on port %PORT%.
  if not defined LEARN3D_NO_BROWSER start "" "%SITE%"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

rem -- find pyproject.toml: zip folder (next to this file) or the repo -------
set "DIR="
if exist "%~dp0pyproject.toml" set "DIR=%~dp0"
if not defined DIR if exist "%~dp0..\pyproject.toml" set "DIR=%~dp0..\"
if not defined DIR if exist "%LOCALAPPDATA%\Learn3D\bridge\pyproject.toml" set "DIR=%LOCALAPPDATA%\Learn3D\bridge\"
if not defined DIR (
  echo [!] pyproject.toml not found next to this file.
  echo     Unzip learn3d-bridge.zip first, then double-click Start-Learn-Bridge.bat inside it.
  pause
  exit /b 1
)

rem -- uv (Python package manager) -----------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
  echo [!] uv is not installed. Open PowerShell and run:
  echo.
  echo     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo.
  echo     Then close this window and double-click Start-Learn-Bridge.bat again.
  start "" https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)

cd /d "%DIR%"
if not exist ".venv" (
  echo First run: downloading Python and the Claude Agent SDK, about 400 MB.
  echo This takes a few minutes, only once.
)

rem A minimised window keeps the bridge running. Its output goes to bridge.log,
rem never to the window: clicking inside a console window puts it in
rem "select" mode, which freezes any program that prints to it.
if exist bridge.log for %%A in (bridge.log) do if %%~zA GTR 1000000 del bridge.log
start "Learn 3D Bridge - close this window to stop" /min cmd /c "uv run learn3d-bridge >> bridge.log 2>&1"

rem -- wait until it answers (first run installs packages) ------------------
echo Starting Learn 3D bridge on http://127.0.0.1:%PORT%/ ...
powershell -NoProfile -Command "$u='http://127.0.0.1:%PORT%/health'; for ($i=0; $i -lt 300; $i++) { try { Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 2 } }; exit 1" >nul 2>nul
if errorlevel 1 (
  echo [!] The bridge did not start. See the log: %CD%\bridge.log
  pause
  exit /b 1
)
echo Learn 3D bridge is running. Log: %CD%\bridge.log
echo To stop it, close the minimised "Learn 3D Bridge" window.
if not defined LEARN3D_NO_BROWSER start "" "%SITE%"
ping -n 4 127.0.0.1 >nul
endlocal
