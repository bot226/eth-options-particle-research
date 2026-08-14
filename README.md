# ETH Options Dashboard (Variant C)

Real-time дашборд для анализа ETH-опционов на Bybit.

**Архитектура:** Python FastAPI backend + Vite JS frontend

## Изоляция от BTC

- Основа: BTC v70, commit `424c5692be09bd8693b41acbc65127d20f69cbb1`.
- ETH использует отдельные процессы, порты, зависимости и локальные базы.
- BTC-данные в ETH-проект не перенесены.
- Остановка ETH затрагивает только дерево процессов, запущенное `run.py` этого проекта.
- Формулы v70 сохранены, но ещё не валидированы на ETH и не считаются доказанной торговой логикой для ETH.

Порты можно переопределить переменными `MOS_BACKEND_PORT` и
`MOS_FRONTEND_PORT`; безопасные значения по умолчанию — `8101` и `5174`.

Короткая проверка с автоматической штатной остановкой:

```powershell
$env:MOS_OPEN_BROWSER="0"
$env:MOS_SMOKE_CHECK="1"
python run.py
```

## 📖 Документация и Справка

Для глубокого понимания работы аналитического терминала изучите подробное руководство по каждому из 9 модулей:
*   [Справочник по аналитическим модулям дашборда (MOS Guide)](docs/dashboard_modules_guide.md) — детальное описание логики работы State Machine, Gamma/GEX, Volatility, Skew, Liquidity, сценариев и качества исполнения.

## Запуск

```bash
# Одна команда:
python run.py
```

Или раздельно:
```bash
# Terminal 1 — Backend
cd backend
pip install -r requirements.txt
python main.py

# Terminal 2 — Frontend
cd frontend
npm install
npm run dev
```

- **Frontend:** http://localhost:5174
- **Backend API:** http://localhost:8101
- **API Docs:** http://localhost:8101/docs

## Стек

- **Backend:** FastAPI, uvicorn, httpx, websockets, numpy, scipy
- **Frontend:** Vite, Vanilla JS, Chart.js, Lightweight Charts
- **API:** Bybit V5 (публичный, без ключей)

## Панели

1. Options Heatmap
2. Probability Curve
3. 25D Risk Reversal Skew
4. IV Term Structure
5. Top OI by Expiry
6. Signals
7. ETH Price + Gamma Levels
8. Summary
