@echo off
rem Learn 3D bridge starter. ASCII only on purpose: cmd misreads UTF-8 batch
rem files on some Windows setups. Works from the downloaded zip folder, the
rem installed folder and the repo's windows\ folder.
rem   Start-Learn-Bridge.bat                      start (and open the website)
rem   learn3d-bridge://start  (from the website)  start, don't open the website again
rem   learn3d-bridge://update (from the website)  download the latest bridge, replace, restart
setlocal
title Learn 3D Bridge
if "%LEARN3D_PORT%"=="" (set "PORT=8766") else (set "PORT=%LEARN3D_PORT%")
set "SITE=https://learn.hellokai07.com"
set "ARG=%~1"
set "MODE=start"
if defined ARG (
  set "LEARN3D_NO_BROWSER=1"
  if not "%ARG:update=%"=="%ARG%" set "MODE=update"
)

rem -- find pyproject.toml: zip folder (next to this file), installed folder or the repo
set "DIR="
if exist "%~dp0pyproject.toml" set "DIR=%~dp0"
if not defined DIR if exist "%~dp0..\pyproject.toml" set "DIR=%~dp0..\"
if not defined DIR if exist "%LOCALAPPDATA%\Learn3D\bridge\pyproject.toml" set "DIR=%LOCALAPPDATA%\Learn3D\bridge\"
if not defined DIR (
  echo [!] pyproject.toml not found next to this file.
  echo     Unzip learn3d-bridge.zip first, then double-click Install-Learn-Bridge.bat inside it.
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

rem -- update: download the latest bridge from the website and replace this copy
if "%MODE%"=="update" (
  if exist ".git" (
    echo This is a git checkout: update it with git pull instead.
    ping -n 4 127.0.0.1 >nul
    exit /b 0
  )
  echo Updating Learn 3D bridge from %SITE% ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'learn3d-bridge-update.zip'; $d=Join-Path $env:TEMP 'learn3d-bridge-update'; Invoke-WebRequest -UseBasicParsing '%SITE%/downloads/learn3d-bridge.zip' -OutFile $z; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; Expand-Archive $z $d; Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe','uv.exe','learn3d-bridge.exe') -and $_.CommandLine -match 'learn3d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 1; robocopy (Join-Path $d 'learn3d-bridge') '%DIR%.' /E /XD .venv /NFL /NDL /NJH /NJS /NP | Out-Null; if ($LASTEXITCODE -ge 8) { exit 1 }; exit 0"
  if errorlevel 1 (
    echo [!] Update failed. Check your internet connection, or download the bridge from the website again.
    pause
    exit /b 1
  )
  call uv sync --frozen --no-dev
  echo Update done. Starting the new bridge ...
)

rem -- already running? ------------------------------------------------------
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%PORT%/health -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo Learn 3D bridge is already running on port %PORT%.
  if not defined LEARN3D_NO_BROWSER start "" "%SITE%"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

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
