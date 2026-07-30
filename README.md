# BTC Options Dashboard (Variant C)

Real-time дашборд для анализа BTC-опционов на Bybit.

**Архитектура:** Python FastAPI backend + Vite JS frontend

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

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000
- **API Docs:** http://localhost:8000/docs

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
7. BTC Price + Gamma Levels
8. Summary
