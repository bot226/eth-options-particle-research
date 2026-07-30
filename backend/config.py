"""Конфигурация BTC Options Dashboard (Variant C)."""

# Bybit API
BYBIT_REST_URL = "https://api.bybit.com"
BYBIT_WS_OPTION_URL = "wss://stream.bybit.com/v5/public/option"
BYBIT_WS_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"

# Параметры данных
BASE_COIN = "BTC"
LINEAR_SYMBOL = "BTCUSDT"

# Интервалы обновления (секунды)
REST_POLL_INTERVAL = 3        # опционные тикеры — каждые 3 сек
WS_RECONNECT_DELAY = 5        # переподключение WS
WS_PING_INTERVAL = 20         # ping

# Свечи
KLINE_INTERVAL = "D"
KLINE_LIMIT = 60

# Таймзона
TIMEZONE = "Europe/Moscow"

# FastAPI
API_HOST = "0.0.0.0"
API_PORT = 8000
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

# Frontend dev server
FRONTEND_DEV_PORT = 5173

# ── Multi-Exchange Architecture ──────────────────────────────────────

# Feature flag: False = legacy Bybit-only, True = multi-exchange aggregation
MULTI_EXCHANGE_ENABLED = True

# Deribit API (primary institutional options venue)
DERIBIT_REST_URL = "https://www.deribit.com/api/v2"
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"

# Exchange institutional quality factors
EXCHANGE_QUALITY_FACTORS = {
    "deribit": 1.50,
    "binance": 1.00,
    "bybit": 0.80,
    "okx": 0.70,
}

# Weight formula coefficients
WEIGHT_OI_COEFF = 0.7       # Open Interest contribution
WEIGHT_VOLUME_COEFF = 0.2   # Volume contribution
WEIGHT_QUALITY_COEFF = 0.1  # Quality factor contribution

# Deribit minimum weight floor (ONLY for WEIGHTED metrics: IV, Skew, etc.)
DERIBIT_MIN_WEIGHT_FLOOR = 0.40

# Latency / staleness thresholds
DEGRADED_LATENCY_MS = 2000   # > 2s = DEGRADED
STALE_THRESHOLD_SECONDS = 5  # > 5s stale = DELAYED
OFFLINE_THRESHOLD_SECONDS = 10  # > 10s stale = OFFLINE / excluded

# ── Price Source Configuration (MOS Manual) ──────────────────────────────────
# execution_price: the actual tradable instrument for manual entry / SL / TP / MFE / MAE
EXECUTION_VENUE  = "bybit_linear"
EXECUTION_SYMBOL = "BTCUSDT"

# reference_price: BTC index / spot reference for options structure, gamma/GEX, IV/skew
# Sourced from: Bybit options tickers → underlyingPrice field
REFERENCE_VENUE  = "bybit_options_index"
REFERENCE_SYMBOL = "BTCUSD_INDEX"

# OHLCV source transparency — v1 uses Binance spot (close to execution but different venue)
# v1.1 goal: switch to Bybit linear BTCUSDT 1m for exact execution alignment
OHLCV_SOURCE_LABEL = "binance_spot_btcusdt"   # must be updated when ohlcv_collector changes

