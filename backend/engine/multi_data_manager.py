"""MultiExchangeDataManager — Multi-adapter orchestrator for global aggregation.

Replaces single-exchange DataManager when MULTI_EXCHANGE_ENABLED = True.
Maintains identical public interface (chain, tickers, spot_price, expiries, strikes)
for backward compatibility with all existing engine modules.
"""

import time
import asyncio
import logging
import math
from collections import defaultdict
from typing import Optional

from api.base_adapter import BaseExchangeAdapter
from engine.instrument_normalizer import InstrumentNormalizer
from engine.exchange_weight_engine import ExchangeWeightEngine
from engine.global_aggregator import GlobalAggregator
from config import REST_POLL_INTERVAL

from engine.history_db import HistoryDB
from engine.research_logger import ResearchLogger

log = logging.getLogger(__name__)


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _has_complete_surface_metrics(ticker: dict) -> bool:
    """Validate strict IV/Greek fields without changing live aggregation."""
    try:
        mark_iv = float(ticker.get("markIv"))
        greeks = [
            float(ticker.get(metric))
            for metric in ("delta", "gamma", "vega", "theta")
        ]
    except (TypeError, ValueError):
        return False
    return mark_iv > 0 and math.isfinite(mark_iv) and all(
        math.isfinite(value) for value in greeks
    )


def _has_valid_mark_iv(ticker: dict) -> bool:
    """Accept the normalized ``markIv`` key and the raw compatibility alias."""
    try:
        value = ticker.get("markIv", ticker.get("mark_iv"))
        mark_iv = float(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return mark_iv > 0 and math.isfinite(mark_iv)


class MultiExchangeDataManager:
    """Multi-exchange data orchestrator with unified output interface.

    Provides the same public fields as the legacy DataManager:
        - spot_price, spot_prev_price, spot_24h_change
        - tickers: dict
        - chain: dict
        - expiries: list[str]
        - strikes: list[int]
        - klines: list
        - last_update: float

    Additionally provides:
        - per_exchange_data: dict[str, list[dict]]  (raw normalized tickers)
        - exchange_weights: dict
        - exchange_health: dict
    """

    def __init__(self, adapters: list[BaseExchangeAdapter]):
        self.adapters = {a.exchange_id: a for a in adapters}

        # ── Unified output (backward-compatible) ─────────────────────
        self.spot_price: float = 0.0
        self.spot_prev_price: float = 0.0
        self.spot_24h_change: float = 0.0
        self.tickers: dict = {}
        self.chain: dict = defaultdict(lambda: defaultdict(dict))
        self.expiries: list[str] = []
        self.strikes: list[int] = []
        self.klines: list = []
        self.last_update: float = 0

        # ── Multi-exchange state ─────────────────────────────────────
        self.per_exchange_tickers: dict[str, list[dict]] = {}
        self.exchange_weights: dict = {}
        self.exchange_health: dict = {}
        self.global_metrics: dict = {}
        self.per_exchange_summary: dict = {}
        self.aggregation_warnings: list = []

        # OI cache (same interface as legacy DataManager)
        self.cached_oi: dict = {}
        self.last_oi_sync: float = 0

        # History tracking (replaces history.json)
        self.history_db = HistoryDB()
        self.oi_history: list = []
        self._load_history()

        # Research Layer
        self.research_logger = ResearchLogger()
        self.last_research_log: float = 0

        # WebSocket frontend clients
        self._ws_clients: set = set()

        # Background task
        self._poll_task: Optional[asyncio.Task] = None

    # ── WebSocket subscriber management ──────────────────────────────

    def add_ws_client(self, ws):
        self._ws_clients.add(ws)

    def remove_ws_client(self, ws):
        self._ws_clients.discard(ws)

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self):
        """Start all adapters and polling loop."""
        # Start all exchange adapters
        for ex_id, adapter in self.adapters.items():
            try:
                await adapter.start()
                log.info("Adapter %s started", ex_id)
            except Exception as e:
                log.error("Failed to start adapter %s: %s", ex_id, e)

        # Start aggregation polling
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("MultiExchangeDataManager started with %d adapters",
                 len(self.adapters))

    async def stop(self):
        """Stop all adapters and polling."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        for ex_id, adapter in self.adapters.items():
            try:
                await adapter.stop()
                log.info("Adapter %s stopped", ex_id)
            except Exception as e:
                log.error("Error stopping adapter %s: %s", ex_id, e)

        try:
            self.research_logger.close()
        except Exception as e:
            log.error("Error closing research logger: %s", e)

        log.info("MultiExchangeDataManager stopped")

    # ── Polling loop ─────────────────────────────────────────────────

    async def _poll_loop(self):
        """Main data polling and aggregation loop."""
        from engine.state_engine import StateEngine
        
        while True:
            try:
                await self._fetch_and_aggregate()
                try:
                    self._update_history()
                except Exception as db_err:
                    log.error("HistoryDB update failed: %s", db_err)
                
                # Research Logging (every 15-30s)
                now = time.time()
                if now - self.last_research_log >= 15:
                    try:
                        market_state = StateEngine.build_market_state(self)
                        self.research_logger.log_snapshot(market_state)
                        self.last_research_log = now
                    except Exception as res_err:
                        log.error("ResearchLogger update failed: %s", res_err)
                
                await self._broadcast_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("MultiExchange poll error: %s", e)

            await asyncio.sleep(REST_POLL_INTERVAL)

    async def _fetch_and_aggregate(self):
        """Fetch data from all adapters and run aggregation."""
        # ── Step 1: Fetch tickers from all adapters concurrently ─────
        fetch_tasks = {}
        for ex_id, adapter in self.adapters.items():
            fetch_tasks[ex_id] = asyncio.create_task(
                self._safe_fetch_tickers(ex_id, adapter)
            )

        per_exchange_raw = {}
        for ex_id, task in fetch_tasks.items():
            try:
                per_exchange_raw[ex_id] = await task
            except Exception as e:
                log.error("Fetch failed for %s: %s", ex_id, e)
                per_exchange_raw[ex_id] = []

        # ── Step 2: Normalize tickers ────────────────────────────────
        per_exchange_normalized = {}
        for ex_id, raw_tickers in per_exchange_raw.items():
            normalized = []
            for raw in raw_tickers:
                norm = InstrumentNormalizer.normalize_ticker(ex_id, raw)
                if norm is not None:
                    normalized.append(norm)
            per_exchange_normalized[ex_id] = normalized

        self.per_exchange_tickers = per_exchange_normalized

        # ── Step 3: Fetch spot price (primary: first available) ──────
        await self._update_spot_price()

        # ── Step 4: Collect health data ──────────────────────────────
        exchange_health = {}
        exchange_meta = {}
        for ex_id, adapter in self.adapters.items():
            health = adapter.get_health()
            exchange_health[ex_id] = health
            exchange_meta[ex_id] = {
                "total_oi": adapter.get_total_oi(),
                "total_volume": adapter.get_total_volume(),
                "quality_factor": adapter.quality_factor,
                "latency_ms": health.get("latency_ms", 0),
                "stale_seconds": health.get("stale_seconds", 0),
                "status": health.get("status", "OFFLINE"),
            }
            if ex_id == "deribit":
                tickers = per_exchange_normalized.get(ex_id, [])
                raw_tickers = per_exchange_raw.get(ex_id, [])
                raw_count = len(raw_tickers)
                parsed_count = len(tickers)

                calls_count = sum(1 for t in tickers if t.get("type") == "call")
                puts_count = sum(1 for t in tickers if t.get("type") == "put")
                expiries_count = len(set(t.get("expiry") for t in tickers if t.get("expiry")))
                strikes_count = len(set(t.get("strike") for t in tickers if t.get("strike")))
                valid_greeks = sum(1 for t in tickers if t.get("delta") is not None)
                valid_iv = sum(_has_valid_mark_iv(t) for t in tickers)
                valid_gamma = sum(1 for t in tickers if t.get("gamma") is not None)
                
                # Dynamic deribit_status and reason
                adapter_health_status = health.get("status", "OFFLINE")
                deribit_error_str = str(getattr(adapter, "last_error", health.get("error_message", ""))).lower()
                stale_seconds = health.get("stale_seconds", 0)
                
                deribit_disabled_reason = "deribit_offline"
                if not getattr(adapter, "initialized", True):
                    deribit_disabled_reason = "deribit_adapter_missing"
                elif not getattr(adapter, "enabled_config", True):
                    deribit_disabled_reason = "deribit_config_disabled"
                elif "timeout" in deribit_error_str:
                    deribit_disabled_reason = "deribit_timeout"
                elif stale_seconds > 180:
                    deribit_disabled_reason = "deribit_stale_data"
                elif adapter_health_status == "ONLINE":
                    if parsed_count > 0:
                        deribit_disabled_reason = "ONLINE"
                    elif raw_count > 0:
                        deribit_disabled_reason = "deribit_parse_error"
                    else:
                        deribit_disabled_reason = "deribit_empty_response"

                if adapter_health_status == "ONLINE":
                    if parsed_count > 0:
                        deribit_status = "ONLINE"
                    elif raw_count > 0:
                        deribit_status = "PARSE_ERROR"
                    else:
                        deribit_status = "EMPTY"
                else:
                    if parsed_count > 0 and stale_seconds > 0:
                        deribit_status = "STALE"
                    else:
                        deribit_status = adapter_health_status
                
                exchange_meta[ex_id].update({
                    "status": deribit_status,
                    "deribit_disabled_reason": deribit_disabled_reason,
                    "raw_instruments_count": raw_count,
                    "parsed_options_count": parsed_count,
                    "deribit_age_sec": health.get("stale_seconds", 0),
                    "deribit_records_used": parsed_count,
                    "deribit_error": getattr(adapter, "last_error", health.get("error_message", "")),
                    "option_tickers_count": parsed_count,
                    "valid_greeks_count": valid_greeks,
                    "valid_iv_count": valid_iv,
                    "valid_gamma_count": valid_gamma,
                    "calls_count": calls_count,
                    "puts_count": puts_count,
                    "expiries_count": expiries_count,
                    "strikes_count": strikes_count,
                })
                
                if hasattr(adapter, "get_diagnostics"):
                    exchange_meta[ex_id].update(adapter.get_diagnostics())

        self.exchange_health = exchange_health
        self.exchange_meta = exchange_meta

        # ── Step 5: Calculate weights ────────────────────────────────
        weight_result = ExchangeWeightEngine.calculate(exchange_meta)
        self.exchange_weights = weight_result

        # ── Step 6: Aggregate ────────────────────────────────────────
        agg = GlobalAggregator.aggregate(
            per_exchange_tickers=per_exchange_normalized,
            weights=weight_result["weights"],
            summation_mask=weight_result["summation_mask"],
            exchange_health=exchange_health,
        )

        # ── Step 7: Update unified fields ────────────────────────────
        self.chain = agg["chain"]
        self.tickers = agg["tickers"]
        self.expiries = agg["expiries"]
        self.strikes = agg["strikes"]
        self.global_metrics = agg["global_metrics"]
        self.per_exchange_summary = agg["per_exchange_summary"]
        self.aggregation_warnings = agg["warnings"]

        # Update OI cache
        now = time.time()
        if not self.cached_oi or (now - self.last_oi_sync >= 30):
            for sym, data in self.tickers.items():
                self.cached_oi[sym] = data.get("oi", 0.0)
            self.last_oi_sync = now

        self.last_update = time.time()

        active = weight_result["active_count"]
        total = weight_result["total_count"]
        log.info("Multi-exchange poll: %d tickers from %d/%d exchanges, spot=%.0f",
                 len(self.tickers), active, total, self.spot_price)

    async def _safe_fetch_tickers(self, ex_id: str,
                                  adapter: BaseExchangeAdapter) -> list[dict]:
        """Safely fetch tickers from an adapter with timeout."""
        _FETCH_TIMEOUT = 8.0  # seconds per adapter
        try:
            return await asyncio.wait_for(
                adapter.fetch_option_tickers(),
                timeout=_FETCH_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning("Ticker fetch TIMEOUT for %s (>%.0fs)", ex_id, _FETCH_TIMEOUT)
            adapter.health.mark_error(f"timeout>{_FETCH_TIMEOUT}s")
            if hasattr(adapter, "get_cached_tickers"):
                cached = adapter.get_cached_tickers()
                if cached:
                    log.info("Using cached tickers for %s due to timeout (count: %d)", ex_id, len(cached))
                    return cached
            return []
        except Exception as e:
            log.error("Ticker fetch error for %s: %s", ex_id, e)
            adapter.health.mark_error(str(e))
            if hasattr(adapter, "get_cached_tickers"):
                cached = adapter.get_cached_tickers()
                if cached:
                    log.info("Using cached tickers for %s due to error (count: %d)", ex_id, len(cached))
                    return cached
            return []

    async def _update_spot_price(self):
        """Update spot price from primary source (Bybit, then Deribit)."""
        # Priority: Bybit (has ETHUSDT linear), then Deribit (ETH index)
        for ex_id in ["bybit", "deribit"]:
            adapter = self.adapters.get(ex_id)
            if adapter is None:
                continue
            try:
                price = await asyncio.wait_for(
                    adapter.fetch_spot_price(),
                    timeout=5.0
                )
                if price and price > 0:
                    self.spot_price = price
                    if hasattr(adapter, 'spot_24h_change'):
                        self.spot_24h_change = adapter.spot_24h_change
                    if hasattr(adapter, 'spot_prev_price'):
                        self.spot_prev_price = adapter.spot_prev_price
                    return
            except asyncio.TimeoutError:
                log.debug("Spot fetch from %s timed out", ex_id)
            except Exception as e:
                log.debug("Spot fetch from %s failed: %s", ex_id, e)

    # ── Legacy DataManager compatibility helpers ─────────────────────

    def get_expiry_nearest(self) -> Optional[str]:
        """Get nearest expiry (same interface as old DataManager)."""
        return self.expiries[0] if self.expiries else None

    def get_chain_for_expiry(self, expiry: str) -> dict:
        """Get chain for specific expiry (same interface as old DataManager)."""
        return self.chain.get(expiry, {})

    def get_oi_delta_pct(self, symbol: str, hours_ago: int = 24) -> float:
        """Возвращает изменение OI в процентах за последние hours_ago часов."""
        if not self.oi_history:
            return 0.0
        
        target_ts = time.time() - (hours_ago * 3600)
        
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
            return 100.0
        return 0.0

    # ── History Tracking ─────────────────────────────────────────────

    def _load_history(self):
        cutoff = time.time() - (25 * 3600)
        self.oi_history = self.history_db.get_all_snapshots_since(cutoff)

    def _stable_surface_history_payload(self):
        """Build the additive Deribit research panel from normalized tickers."""
        adapter = self.adapters.get("deribit")
        if adapter is None or not hasattr(adapter, "get_stable_surface_snapshot"):
            return None
        surface = dict(adapter.get_stable_surface_snapshot())
        target_count = int(surface.get("target_contracts") or 0)
        target_symbols = list(surface.get("target_contract_names") or [])
        normalized_by_symbol = {
            str(ticker.get("symbol") or ""): ticker
            for ticker in self.per_exchange_tickers.get("deribit", [])
            if (
                isinstance(ticker, dict)
                and ticker.get("symbol")
                and _has_complete_surface_metrics(ticker)
            )
        }
        contract_data = [
            normalized_by_symbol[symbol]
            for symbol in target_symbols
            if symbol in normalized_by_symbol
        ]
        normalized_count = len(contract_data)
        normalized_coverage = (
            normalized_count / target_count
            if target_count > 0
            else 0.0
        )
        source_fresh_count = int(surface.get("fresh_contracts") or 0)
        source_coverage = float(surface.get("coverage_ratio") or 0.0)
        surface["fresh_contracts"] = min(
            source_fresh_count,
            normalized_count,
        )
        surface["coverage_ratio"] = min(
            source_coverage,
            normalized_coverage,
        )
        minimum_coverage = float(surface.get("minimum_coverage_ratio") or 0.95)
        if (
            not surface.get("snapshot_valid")
            or surface["coverage_ratio"] < minimum_coverage
        ):
            surface["snapshot_valid"] = False
            if not surface.get("invalid_reason"):
                surface["invalid_reason"] = (
                    "normalized_coverage_below_95pct"
                    if target_count > 0
                    else "universe_not_initialized"
                )
            contract_data = []
        surface["contract_data"] = contract_data
        return surface

    def _update_history(self):
        now = time.time()
        # Сохраняем слепок каждые 5 минут (300 сек)
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

            # Persist source-level observations.  The existing aggregate chain remains
            # untouched; replay can reconstruct exactly which exchange supplied each
            # contract's IV, volume, and Greeks.
            contract_observations = []
            for exchange, tickers in self.per_exchange_tickers.items():
                for ticker in tickers:
                    observation = dict(ticker)
                    observation.setdefault("exchange", exchange)
                    contract_observations.append(observation)
            stable_surface_data = self._stable_surface_history_payload()
            
            # Save to SQLite
            self.history_db.save_snapshot(
                ts=now,
                oi_data=snapshot_oi,
                pdf_data=snapshot_pdf,
                gex_data=snapshot_gex,
                term_data=snapshot_ts,
                contract_data=contract_observations,
                stable_surface_data=stable_surface_data,
            )
            
            self.oi_history.append({
                "ts": now,
                "oi": snapshot_oi,
                "pdf": snapshot_pdf,
                "gex": snapshot_gex,
                "term_structure": snapshot_ts,
            })
            
            cutoff = now - 25 * 3600
            self.oi_history = [h for h in self.oi_history if h['ts'] >= cutoff]
            
            if len(self.oi_history) % 10 == 0:
                self.history_db.cleanup_old()

    # ── Broadcast ────────────────────────────────────────────────────

    async def _broadcast_update(self):
        """Notify all connected frontend WS clients."""
        import json as _json
        if not self._ws_clients:
            return
        msg = _json.dumps({"type": "update", "ts": self.last_update})
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
