@echo off
setlocal
title ETH Options Dashboard - Shutdown
cd /d "%~dp0"

if not exist ".runtime\eth-stack.json" (
    echo [ETH] No active ETH stack PID file was found.
    echo [ETH] BTC and all unrelated Python/Node processes were left untouched.
    pause
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$meta = Get-Content -Raw '.runtime\eth-stack.json' | ConvertFrom-Json;" ^
  "$process = Get-Process -Id $meta.orchestrator_pid -ErrorAction SilentlyContinue;" ^
  "if (-not $process) { Remove-Item -LiteralPath '.runtime\eth-stack.json' -Force; exit 0 };" ^
  "$started = [DateTimeOffset]$process.StartTime;" ^
  "$delta = [Math]::Abs($started.ToUnixTimeSeconds() - [int64]$meta.started_at);" ^
  "if ($delta -gt 10) { Write-Error 'Refusing to stop a reused PID.'; exit 2 };" ^
  "taskkill /PID $meta.orchestrator_pid /T /F | Out-Null;" ^
  "Remove-Item -LiteralPath '.runtime\eth-stack.json' -Force -ErrorAction SilentlyContinue"

if errorlevel 1 (
    echo [ETH] Could not safely stop the ETH stack.
    pause
    exit /b 1
)

echo [ETH] ETH backend, frontend and workers stopped.
echo [ETH] BTC and unrelated processes were not touched.
pause
