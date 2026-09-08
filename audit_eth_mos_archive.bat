@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag an ETH MOS ZIP onto this file or pass its full path.
  pause
  exit /b 1
)
python -m backend.scripts.eth_mos_archive_audit "%~1" --update-governance
if errorlevel 1 (
  echo.
  echo ETH MOS audit reported a fail-closed error or failed quality gate.
  pause
  exit /b 1
)
echo.
echo ETH MOS audit completed. JSON and Markdown are next to the source archive.
pause
endlocal
