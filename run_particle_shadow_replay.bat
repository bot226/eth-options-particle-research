@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Drag a MOS dataset ZIP onto this file, or run:
  echo   run_particle_shadow_replay.bat C:\path\to\mos_baseline.zip
  exit /b 2
)

python backend\scripts\run_particle_shadow_replay.py "%~1"
if errorlevel 1 exit /b %errorlevel%

echo.
echo Particle shadow replay completed.
endlocal
