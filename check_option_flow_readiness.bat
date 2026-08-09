@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Checking MOS option-flow research collection...
echo.
python -m backend.scripts.option_flow_research backend\data --human
set "MOS_CHECK_EXIT=%errorlevel%"

echo.
if "%MOS_CHECK_EXIT%"=="0" (
  echo Collection is ready for the frozen research run.
) else (
  echo Collection is not ready yet. Continue running MOS without changing thresholds.
)
echo.
pause
exit /b %MOS_CHECK_EXIT%
