@echo off
setlocal

title MOS Manual Trading Dashboard

cd /d "%~dp0"

echo ================================================================
echo   MOS Manual Trading Dashboard
echo ================================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    pause
    exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [WARN] npm.cmd was not found in PATH. Frontend may not start.
    echo.
) else (
    if not exist "frontend\node_modules\" (
        echo [SETUP] Installing frontend dependencies...
        pushd frontend
        call npm.cmd ci
        if errorlevel 1 (
            popd
            echo [ERROR] npm ci failed.
            pause
            exit /b 1
        )
        popd
        echo.
    )
)

python -c "import fastapi, uvicorn, httpx, websockets, numpy, scipy" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Installing backend dependencies...
    python -m pip install -r backend\requirements.txt
    if errorlevel 1 (
        echo [ERROR] Backend dependency install failed.
        pause
        exit /b 1
    )
    echo.
)

echo [START] Launching backend and frontend via run.py...
echo.
python run.py

echo.
echo [STOPPED] Dashboard process finished.
pause
