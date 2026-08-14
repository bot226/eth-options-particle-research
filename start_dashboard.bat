@echo off
title ETH Options Trading Workstation
set "MOS_BACKEND_HOST=127.0.0.1"
if not defined MOS_BACKEND_PORT set "MOS_BACKEND_PORT=8101"
if not defined MOS_FRONTEND_PORT set "MOS_FRONTEND_PORT=5174"
echo =================================================================
echo     ETH OPTIONS TRADING WORKSTATION - AUTOLAUNCHER (WINDOWS)
echo =================================================================
echo.

:: Переходим в директорию самого скрипта, чтобы относительные пути работали всегда корректно
cd /d "%~dp0"

:: Проверяем наличие Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python не найден в переменной окружения PATH.
    echo Пожалуйста, установите Python и добавьте его в PATH при установке.
    echo.
    pause
    exit /b
)

:: Проверяем и устанавливаем зависимости Python, если они отсутствуют
python -c "import fastapi, uvicorn, httpx, websockets, numpy, scipy" >nul 2>&1
if %errorlevel% neq 0 (
    echo [SYSTEM] Обнаружены отсутствующие Python-библиотеки.
    echo [SYSTEM] Запускаем автоматическую установку зависимостей из requirements.txt...
    echo.
    python -m pip install -r backend\requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Ошибка при установке Python-зависимостей.
        pause
        exit /b
    )
    echo [SYSTEM] Python-зависимости успешно установлены.
    echo.
)

:: Проверяем наличие Node.js / npm
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Node.js или npm не обнаружены в системе.
    echo Пожалуйста, убедитесь, что Node.js установлен для корректной работы фронтенда.
    echo.
) else (
    :: Если папка node_modules не создана, устанавливаем npm-пакеты
    if not exist "frontend\node_modules\" (
        echo [SYSTEM] Папка node_modules не найдена.
        echo [SYSTEM] Запускаем автоматическую установку Node.js зависимостей...
        echo.
        cd frontend
        call npm install
        cd ..
        if %errorlevel% neq 0 (
            echo [ERROR] Ошибка при выполнении npm install.
            pause
            exit /b
        )
        echo [SYSTEM] Node.js зависимости успешно установлены.
        echo.
    )
)

echo [SYSTEM] Запуск FastAPI бэкенда и Vite фронтенда...
echo.

:: Запускаем основной скрипт-оркестратор серверов
python run.py

echo.
echo [SYSTEM] Работа серверов завершена.
pause
