"""Bybit V5 REST API клиент (async — httpx)."""

import logging
import httpx
from config import BYBIT_REST_URL, BASE_COIN, LINEAR_SYMBOL, KLINE_INTERVAL, KLINE_LIMIT

log = logging.getLogger(__name__)


class BybitRestClient:
    """Async клиент для публичного REST API Bybit V5."""

    def __init__(self, base_url: str = BYBIT_REST_URL):
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Accept": "application/json"},
            timeout=15,
        )

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    #  Приватный хелпер
    # ------------------------------------------------------------------ #
    async def _get(self, path: str, params: dict) -> dict | None:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                log.error("API error %s: %s", data.get("retCode"), data.get("retMsg"))
                return None
            return data.get("result", {})
        except Exception as e:
            log.error("REST request failed: %s", e)
            return None

    # ------------------------------------------------------------------ #
    #  Публичные методы
    # ------------------------------------------------------------------ #
    async def get_instruments(self, base_coin: str = BASE_COIN) -> list:
        """Получить список всех активных ETH-опционов."""
        result = await self._get("/v5/market/instruments-info", {
            "category": "option",
            "baseCoin": base_coin,
            "limit": 1000,
        })
        if result is None:
            return []
        items = result.get("list", [])
        cursor = result.get("nextPageCursor")
        while cursor:
            r = await self._get("/v5/market/instruments-info", {
                "category": "option",
                "baseCoin": base_coin,
                "limit": 1000,
                "cursor": cursor,
            })
            if r is None:
                break
            items.extend(r.get("list", []))
            cursor = r.get("nextPageCursor")
        return items

    async def get_option_tickers(self, base_coin: str = BASE_COIN) -> list:
        """Получить тикеры всех ETH-опционов (OI, IV, Greeks, Volume)."""
        result = await self._get("/v5/market/tickers", {
            "category": "option",
            "baseCoin": base_coin,
        })
        return result.get("list", []) if result else []

    async def get_spot_ticker(self, symbol: str = LINEAR_SYMBOL) -> dict | None:
        """Получить текущий тикер ETHUSDT (linear)."""
        result = await self._get("/v5/market/tickers", {
            "category": "linear",
            "symbol": symbol,
        })
        if result and result.get("list"):
            return result["list"][0]
        return None

    async def get_kline(self, symbol: str = LINEAR_SYMBOL,
                        interval: str = KLINE_INTERVAL,
                        limit: int = KLINE_LIMIT) -> list:
        """Получить свечные данные ETHUSDT."""
        result = await self._get("/v5/market/kline", {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        return result.get("list", []) if result else []

    async def get_historical_volatility(self, base_coin: str = BASE_COIN,
                                        period: int = 30) -> list:
        """Получить историческую волатильность."""
        result = await self._get("/v5/market/historical-volatility", {
            "category": "option",
            "baseCoin": base_coin,
            "period": period,
        })
        return result if isinstance(result, list) else []
