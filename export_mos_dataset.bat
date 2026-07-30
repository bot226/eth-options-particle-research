@echo off
setlocal
cd /d "%~dp0"
python backend\scripts\mos_dataset_exporter.py %*
if errorlevel 1 (
  echo.
  echo MOS Dataset Exporter failed.
  exit /b 1
)
echo.
echo MOS Dataset Exporter completed successfully.
endlocal
