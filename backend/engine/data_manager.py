"""Центральное хранилище данных. Async-версия для FastAPI."""

import time
import asyncio
import logging
import json
import os
from collections import defaultdict
from api.rest_client import BybitRestClient
from config import REST_POLL_INTERVAL

log = logging.getLogger(__name__)


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class DataManager:
    """Центральное async хранилище данных опционов."""

    def __init__(self):
        # Текущее состояние
        self.spot_price: float = 0.0
        self.spot_prev_price: float = 0.0
        self.spot_24h_change: float = 0.0

        # {symbol_str: {все поля тикера как float/str}}
        self.tickers: dict = {}

        # Структурированные данные: {expiry_str: {strike_int: {"C": {...}, "P": {...}}}}
        self.chain: dict = defaultdict(lambda: defaultdict(dict))

        # Уникальные expiry и strikes
        self.expiries: list[str] = []
        self.strikes: list[int] = []

        # Свечи BTCUSDT: [{ts, o, h, l, c, v}, ...]
        self.klines: list = []

        # Предыдущий snapshot для дельт
        self._prev_tickers: dict = {}

        # Время последнего обновления
        self.last_update: float = 0
        
        # Кэш OI (обновляется каждые 30 сек)
        self.cached_oi: dict = {}
        self.last_oi_sync: float = 0

        # История OI для расчета дельты (24h)
        from engine.history_db import HistoryDB
        self.history_db = HistoryDB()
        self.oi_history: list = []  # In-memory cache for fast access
        self._load_history()

        # REST client
        self.rest = BybitRestClient()

        # Подключённые WebSocket-клиенты фронтенда
        self._ws_clients: set = set()

        # Background task
        self._poll_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    #  Управление WebSocket подписчиками
    # ------------------------------------------------------------------ #
    def add_ws_client(self, ws):
        self._ws_clients.add(ws)

    def remove_ws_client(self, ws):
        self._ws_clients.discard(ws)

    # ------------------------------------------------------------------ #
    #  Старт / стоп
    # ------------------------------------------------------------------ #
    async def start(self):
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("DataManager REST polling started")

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self.rest.close()
        log.info("DataManager stopped")

    # ------------------------------------------------------------------ #
    #  REST polling loop
    # ------------------------------------------------------------------ #
    async def _poll_loop(self):
        while True:
            try:
                tickers = await self.rest.get_option_tickers()
                spot = await self.rest.get_spot_ticker()
                klines = await self.rest.get_kline()

                # Обновить спот
                if spot:
                    self.spot_price = _safe_float(spot.get("lastPrice"))
                    self.spot_prev_price = _safe_float(spot.get("prevPrice24h"))
                    if self.spot_prev_price > 0:
                        self.spot_24h_change = (
                            (self.spot_price - self.spot_prev_price) / self.spot_prev_price
                        )

                # Обновить свечи
                if klines:
                    self.klines = []
                    for k in klines:
                        self.klines.append({
                            "ts": int(k[0]),
                            "o": float(k[1]),
                            "h": float(k[2]),
                            "l": float(k[3]),
                            "c": float(k[4]),
                            "v": float(k[5]),
                        })
                    self.klines.reverse()  # от старых к новым

                # Обновить опционы
                if tickers:
                    self._prev_tickers = dict(self.tickers)
                    self._parse_tickers(tickers)
                    
                    # Обновляем кэш OI каждые 30 секунд
                    now = time.time()
                    if not self.cached_oi or (now - self.last_oi_sync >= 30):
                        for sym, data in self.tickers.items():
                            self.cached_oi[sym] = data.get("oi", 0.0)
                        self.last_oi_sync = now
                        
                    self._update_history()

                self.last_update = time.time()
                log.info("REST poll: %d tickers, spot=%.0f", len(tickers), self.spot_price)

                # Уведомляем WebSocket-клиентов фронтенда
                await self._broadcast_update()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("REST poll error: %s", e)

            await asyncio.sleep(REST_POLL_INTERVAL)

    def _parse_tickers(self, tickers: list):
        """Парсит список тикеров в self.tickers и self.chain."""
        chain = defaultdict(lambda: defaultdict(dict))
        all_expiries = set()
        all_strikes = set()

        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol:
                continue
            # Парсим символ: BTC-29NOV24-85000-C или BTC-29NOV24-85000-C-USDT
            parts = symbol.split("-")
            if len(parts) < 4:
                continue
            expiry_str = parts[1]
            try:
                strike = int(parts[2])
            except ValueError:
                continue
            opt_type = parts[3]  # "C" или "P"

            parsed = {
                "symbol": symbol,
                "expiry": expiry_str,
                "strike": strike,
                "type": opt_type,
                "oi": self.cached_oi.get(symbol, _safe_float(t.get("openInterest"))),
                "volume": _safe_float(t.get("volume24h")),
                "markIv": _safe_float(t.get("markIv")),
                "bidIv": _safe_float(t.get("bid1Iv")),
                "askIv": _safe_float(t.get("ask1Iv")),
                "delta": _safe_float(t.get("delta")),
                "gamma": _safe_float(t.get("gamma")),
                "vega": _safe_float(t.get("vega")),
                "theta": _safe_float(t.get("theta")),
                "markPrice": _safe_float(t.get("markPrice")),
                "lastPrice": _safe_float(t.get("lastPrice")),
                "bidPrice": _safe_float(t.get("bid1Price")),
                "askPrice": _safe_float(t.get("ask1Price")),
                "underlyingPrice": _safe_float(t.get("underlyingPrice")),
            }

            self.tickers[symbol] = parsed
            chain[expiry_str][strike][opt_type] = parsed
            all_expiries.add(expiry_str)
            all_strikes.add(strike)

        self.chain = dict(chain)
        self.expiries = sorted(all_expiries)
        self.strikes = sorted(all_strikes)

    # ------------------------------------------------------------------ #
    #  WebSocket обновления (от Bybit WS)
    # ------------------------------------------------------------------ #
    def on_ws_ticker(self, data: dict):
        """Обработка одиночного тикера из Bybit WebSocket."""
        symbol = data.get("symbol", "")
        if not symbol or not symbol.startswith("BTC-"):
            return
        parts = symbol.split("-")
        if len(parts) < 4:
            return
        expiry_str = parts[1]
        try:
            strike = int(parts[2])
        except ValueError:
            return
        opt_type = parts[3]

        existing = self.tickers.get(symbol, {})
        parsed = {
            "symbol": symbol,
            "expiry": expiry_str,
            "strike": strike,
            "type": opt_type,
            "oi": self.cached_oi.get(symbol, _safe_float(data.get("openInterest", existing.get("oi")))),
            "volume": _safe_float(data.get("volume24h", existing.get("volume"))),
            "markIv": _safe_float(data.get("markIv", existing.get("markIv"))),
            "bidIv": _safe_float(data.get("bid1Iv", existing.get("bidIv"))),
            "askIv": _safe_float(data.get("ask1Iv", existing.get("askIv"))),
            "delta": _safe_float(data.get("delta", existing.get("delta"))),
            "gamma": _safe_float(data.get("gamma", existing.get("gamma"))),
            "vega": _safe_float(data.get("vega", existing.get("vega"))),
            "theta": _safe_float(data.get("theta", existing.get("theta"))),
            "markPrice": _safe_float(data.get("markPrice", existing.get("markPrice"))),
            "lastPrice": _safe_float(data.get("lastPrice", existing.get("lastPrice"))),
            "bidPrice": _safe_float(data.get("bid1Price", existing.get("bidPrice"))),
            "askPrice": _safe_float(data.get("ask1Price", existing.get("askPrice"))),
            "underlyingPrice": _safe_float(data.get("underlyingPrice", existing.get("underlyingPrice"))),
        }
        self.tickers[symbol] = parsed
        if expiry_str not in self.chain:
            self.chain[expiry_str] = {}
        if strike not in self.chain[expiry_str]:
            self.chain[expiry_str][strike] = {}
        self.chain[expiry_str][strike][opt_type] = parsed

    def on_ws_spot(self, data: dict):
        """Обработка спот-тикера из Bybit WebSocket."""
        price = _safe_float(data.get("lastPrice"))
        if price > 0:
            self.spot_price = price
            prev = _safe_float(data.get("prevPrice24h"))
            if prev > 0:
                self.spot_prev_price = prev
                self.spot_24h_change = (price - prev) / prev

    # ------------------------------------------------------------------ #
    #  Хелперы
    # ------------------------------------------------------------------ #
    def get_expiry_nearest(self) -> str | None:
        return self.expiries[0] if self.expiries else None

    def get_chain_for_expiry(self, expiry: str) -> dict:
        return self.chain.get(expiry, {})

    # ------------------------------------------------------------------ #
    #  Broadcast обновлений фронтенд-клиентам
    # ------------------------------------------------------------------ #
    async def _broadcast_update(self):
        """Отправляем уведомление всем подключённым WS-клиентам."""
        import json
        if not self._ws_clients:
            return
        msg = json.dumps({"type": "update", "ts": self.last_update})
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    # ------------------------------------------------------------------ #
    #  History Tracking (OI)
    # ------------------------------------------------------------------ #
    def _load_history(self):
        cutoff = time.time() - (25 * 3600)
        self.oi_history = self.history_db.get_all_snapshots_since(cutoff)

    def _save_history(self):
        pass  # Handled by HistoryDB during _update_history

    def _update_history(self):
        now = time.time()
        # Сохраняем слепок каждые 5 минут (300 сек) для дельты
        if not self.oi_history or (now - self.oi_history[-1]['ts'] >= 300):
            snapshot_oi = {}
            for sym, data in self.tickers.items():
                if data.get("oi", 0) > 0:
                    snapshot_oi[sym] = data["oi"]
            
            snapshot_pdf = {}
            snapshot_gex = {}
            snapshot_ts = {}
            
            from engine.calculator import Calculator, _dte_from_expiry_str
            nearest = self.get_expiry_nearest()
            if nearest:
                dte = _dte_from_expiry_str(nearest) or 7
                snapshot_pdf = Calculator.probability_distribution(
                    self.get_chain_for_expiry(nearest), self.spot_price, dte
                )
                snapshot_gex = Calculator.get_gamma_exposure_full(
                    dict(self.chain), self.spot_price
                )
            snapshot_ts = Calculator.get_iv_term_structure(self.chain, self.spot_price)
            
            # Save to SQLite
            self.history_db.save_snapshot(
                ts=now,
                oi_data=snapshot_oi,
                pdf_data=snapshot_pdf,
                gex_data=snapshot_gex,
                term_data=snapshot_ts,
            )
            
            self.oi_history.append({
                "ts": now,
                "oi": snapshot_oi,
                "pdf": snapshot_pdf,
                "gex": snapshot_gex,
                "term_structure": snapshot_ts,
            })
            # Храним только последние 24 часа (около 96 записей) + запас
            cutoff = now - 25 * 3600
            self.oi_history = [h for h in self.oi_history if h['ts'] >= cutoff]
            
            # Optionally trigger cleanup
            if len(self.oi_history) % 10 == 0:
                self.history_db.cleanup_old()

    def get_oi_delta_pct(self, symbol: str, hours_ago: int = 24) -> float:
        """Возвращает изменение OI в процентах за последние hours_ago часов."""
        if not self.oi_history:
            return 0.0
        
        target_ts = time.time() - (hours_ago * 3600)
        
        # Находим ближайший слепок к target_ts
        best_diff = float('inf')
        best_snap = None
        for snap in self.oi_history:
            diff = abs(snap['ts'] - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_snap = snap
                
        if not best_snap:
            return 0.0
            
        old_oi = best_snap['oi'].get(symbol, 0.0)
        current_oi = self.tickers.get(symbol, {}).get('oi', 0.0)
        
        if old_oi > 0:
            return (current_oi - old_oi) / old_oi * 100.0
        elif current_oi > 0:
            return 100.0 # Рост с нуля
        return 0.0
