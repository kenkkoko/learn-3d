@echo off
rem Learn 3D bridge starter. ASCII only on purpose: cmd misreads UTF-8 batch
rem files on some Windows setups. Works from the downloaded zip folder, the
rem installed folder and the repo's windows\ folder.
rem   Start-Learn-Bridge.bat                      start (and open the website)
rem   learn3d-bridge://start  (from the website)  start, don't open the website again
rem   learn3d-bridge://update (from the website)  download the latest bridge, replace, restart
rem   learn3d-bridge://login  (from the website)  open the Claude Code login (claude auth login)
setlocal
title Learn 3D Bridge
if "%LEARN3D_PORT%"=="" (set "PORT=8766") else (set "PORT=%LEARN3D_PORT%")
set "SITE=https://learn.hellokai07.com"
set "ARG=%~1"
set "MODE=start"
if defined ARG (
  set "LEARN3D_NO_BROWSER=1"
  if not "%ARG:update=%"=="%ARG%" set "MODE=update"
  if not "%ARG:login=%"=="%ARG%" set "MODE=login"
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
rem When the website starts this file, it inherits the browser's PATH, which may be
rem older than the uv install (uv adds itself to PATH only for new programs).
rem So also look in the folders the uv installers use.
where uv >nul 2>nul
if errorlevel 1 (
  for %%D in ("%USERPROFILE%\.local\bin" "%USERPROFILE%\.cargo\bin" "%LOCALAPPDATA%\Microsoft\WinGet\Links" "%LOCALAPPDATA%\uv" "%ProgramData%\chocolatey\bin") do (
    if exist "%%~D\uv.exe" set "PATH=%%~D;%PATH%"
  )
)
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
if "%MODE%"=="login" goto login

rem -- update: download the latest bridge from the website and replace this copy
if "%MODE%"=="update" (
  if exist ".git" (
    echo This is a git checkout: update it with git pull instead.
    ping -n 4 127.0.0.1 >nul
    exit /b 0
  )
  echo Updating Learn 3D bridge from %SITE% ...
  rem This file is running, so it is not overwritten here (cmd reads a running batch
  rem file line by line; replacing it mid-run executes garbage). The new copy is saved
  rem as Start-Learn-Bridge.bat.new and swapped in at :done, right before exiting.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'learn3d-bridge-update.zip'; $d=Join-Path $env:TEMP 'learn3d-bridge-update'; Invoke-WebRequest -UseBasicParsing '%SITE%/downloads/learn3d-bridge.zip' -OutFile $z; if (Test-Path $d) { Remove-Item -Recurse -Force $d }; Expand-Archive $z $d; Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe','uv.exe','learn3d-bridge.exe') -and $_.CommandLine -match 'learn3d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 1; $s=Join-Path $d 'learn3d-bridge'; robocopy $s '%DIR%.' /E /IS /IT /XD .venv /XF Start-Learn-Bridge.bat /NFL /NDL /NJH /NJS /NP | Out-Null; if ($LASTEXITCODE -ge 8) { exit 1 }; Copy-Item (Join-Path $s 'Start-Learn-Bridge.bat') '%DIR%Start-Learn-Bridge.bat.new' -Force; exit 0"
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
  goto done
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

:done
rem Swap in the updated starter (see the update step). The move and the exit are in
rem one parenthesised block, which cmd reads completely before running it.
if exist "%DIR%Start-Learn-Bridge.bat.new" (
  move /y "%DIR%Start-Learn-Bridge.bat.new" "%DIR%Start-Learn-Bridge.bat" >nul
  endlocal
  exit /b 0
)
endlocal
exit /b 0

:login
rem Claude Code login: the bridge ships its own Claude Code (inside the Agent SDK),
rem so this works even when Claude Code was never installed separately.
set "CLAUDE_EXE=%DIR%.venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe"
if not exist "%CLAUDE_EXE%" set "CLAUDE_EXE="
if not defined CLAUDE_EXE for /f "delims=" %%P in ('where claude 2^>nul') do if not defined CLAUDE_EXE set "CLAUDE_EXE=%%P"
if not defined CLAUDE_EXE (
  echo [!] Claude Code was not found. Install it from https://claude.com/claude-code
  start "" https://claude.com/claude-code
  pause
  exit /b 1
)
echo Opening the Claude Code login. Your browser will ask you to sign in with your Claude account.
start "Learn 3D - Claude Code login" cmd /c ""%CLAUDE_EXE%" auth login & echo. & echo When it says Login successful, close this window and go back to the website. & pause"
endlocal
exit /b 0
