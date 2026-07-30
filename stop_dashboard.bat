@echo off
title BTC Options Dashboard - Shutdown
echo =================================================================
echo     ОСТАНОВКА ФОНОВЫХ ПРОЦЕССОВ ДАШБОРДА
echo =================================================================
echo.

echo [SYSTEM] Завершение процессов Python (бэкенд)...
taskkill /F /IM python.exe /T >nul 2>&1

echo [SYSTEM] Завершение процессов Node.js (фронтенд)...
taskkill /F /IM node.exe /T >nul 2>&1

echo.
echo [OK] Все процессы успешно остановлены.
pause
