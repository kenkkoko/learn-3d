@echo off
chcp 65001 >nul
setlocal
title Learn 3D Bridge
cd /d "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo [錯誤] 找不到 uv。請先安裝 uv 再雙擊這個檔案：
  echo   在 PowerShell 執行：
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  echo   安裝完成後請關掉這個視窗、重新雙擊。
  echo.
  pause
  exit /b 1
)

if "%LEARN3D_PORT%"=="" (set "PORT=8766") else (set "PORT=%LEARN3D_PORT%")

echo ============================================
echo  Learn 3D 學習橋接
echo  橋接網址：http://127.0.0.1:%PORT%/
echo  線上版：  https://learn.hellokai07.com
echo  關掉這個視窗就會停止橋接。
echo ============================================
echo.

uv run learn3d-bridge
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [錯誤] 橋接已停止（代碼 %CODE%）。若顯示埠被占用，可能已經開了另一個橋接視窗。
)
echo.
pause
endlocal
