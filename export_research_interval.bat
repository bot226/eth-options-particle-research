@echo off
setlocal
cd /d "%~dp0"
echo Enter the UTC beginning of the NEW ETH analysis interval.
echo Example: 2026-09-01T00:00:00Z
set /p MOS_ETH_INTERVAL_FROM=From UTC:
if "%MOS_ETH_INTERVAL_FROM%"=="" (
  echo Start time is required.
  pause
  exit /b 1
)
echo.
echo The archive will end at the latest fully closed ETHUSDT minute.
echo Seven days of support context and a separate 720-minute maturity boundary will be recorded.
python -m backend.scripts.research_interval_dataset_exporter --from "%MOS_ETH_INTERVAL_FROM%" %*
if errorlevel 1 (
  echo.
  echo ETH MOS Interval Exporter failed. Live databases were not changed.
  pause
  exit /b 1
)
echo.
echo ETH MOS Interval Exporter completed successfully.
pause
endlocal
