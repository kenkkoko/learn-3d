@echo off
rem Learn 3D bridge installer. Run once. ASCII only on purpose (see Start-Learn-Bridge.bat).
rem Copies the bridge to %LOCALAPPDATA%\Learn3D\bridge, installs its packages and
rem registers learn3d-bridge:// so the website's "Start bridge" button can open it.
setlocal
title Learn 3D Bridge - Install
echo.
echo  Learn 3D bridge - install
echo  ------------------------------------------
echo  Lets learn.hellokai07.com use Claude Code / Codex / Ollama on this PC.
echo.

if not exist "%~dp0pyproject.toml" (
  echo  [!] pyproject.toml not found. Unzip learn3d-bridge.zip first
  echo      ^(right click - Extract All^), then run this file from that folder.
  pause
  exit /b 1
)

rem -- uv (Python package manager) -----------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
  for %%D in ("%USERPROFILE%\.local\bin" "%USERPROFILE%\.cargo\bin" "%LOCALAPPDATA%\Microsoft\WinGet\Links") do (
    if exist "%%~D\uv.exe" set "PATH=%%~D;%PATH%"
  )
)
where uv >nul 2>nul
if errorlevel 1 (
  echo  uv is not installed. It is needed to run the bridge.
  choice /c YN /m "  Install uv now from astral.sh"
  if errorlevel 2 (
    echo  Install it later with:
    echo    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
  )
  powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  where uv >nul 2>nul
  if errorlevel 1 (
    echo  [!] uv install did not finish. Close this window and run the installer again.
    pause
    exit /b 1
  )
)

set "TARGET=%LOCALAPPDATA%\Learn3D\bridge"
rem A bridge that is still running would keep serving the old version after the copy
echo  Stopping a running Learn 3D bridge, if any ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe','uv.exe','learn3d-bridge.exe') -and $_.CommandLine -match 'learn3d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
echo  1/3 Copying files to %TARGET%
if not exist "%TARGET%" mkdir "%TARGET%"
robocopy "%~dp0." "%TARGET%" /E /NFL /NDL /NJH /NJS /NP /XD .venv __pycache__ /XF bridge.log >nul
if errorlevel 8 (
  echo  [!] Copy failed.
  pause
  exit /b 1
)

echo  2/3 Installing Python and the Claude Agent SDK, about 400 MB, only once
pushd "%TARGET%"
call uv sync --frozen --no-dev
if errorlevel 1 (
  popd
  echo  [!] Package install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
popd

echo  3/3 Registering learn3d-bridge:// so the website can start the bridge
reg add "HKCU\Software\Classes\learn3d-bridge" /ve /d "URL:Learn 3D Bridge" /f >nul
reg add "HKCU\Software\Classes\learn3d-bridge" /v "URL Protocol" /d "" /f >nul
rem Chrome will not launch a link whose handler is a .bat file ("no registered handler"),
rem so the handler is cmd.exe, which then runs the .bat.
reg add "HKCU\Software\Classes\learn3d-bridge\shell\open\command" /ve /d "\"%SystemRoot%\System32\cmd.exe\" /c \"\"%TARGET%\Start-Learn-Bridge.bat\" \"%%1\"\"" /f >nul

echo.
echo  Done. From now on press "Start bridge" on learn.hellokai07.com.
echo  The browser asks once whether to open the link: allow it.
echo.
echo  Starting the bridge now ...
call "%TARGET%\Start-Learn-Bridge.bat"
endlocal
