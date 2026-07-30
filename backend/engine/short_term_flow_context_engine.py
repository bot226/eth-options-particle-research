"""Experimental short-term flow context for MOS Research diagnostics.

This engine is read-only. It does not feed live synthetic_flow_pressure,
StateEngine, ExecutionTimingEngine, SignalCluster, events, or trading logic.
It exists only to compare short-term OHLCV movement with the slower legacy
synthetic flow layer.
"""

from typing import Any, Dict, Iterable, List, Optional


class ShortTermFlowContextEngine:
    """Calculate diagnostic 1m/5m/15m OHLCV flow pressure."""

    FLOW_SCALE = "-100_to_100_neutral_0"

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _empty(cls, reason: str) -> Dict[str, Any]:
        return {
            "status": "experimental",
            "available": False,
            "flow_scale": cls.FLOW_SCALE,
            "short_term_flow_pressure": 0.0,
            "short_term_flow_intensity": 0.0,
            "direction": "NEUTRAL",
            "components": {
                "return_1m_component": 0.0,
                "return_5m_component": 0.0,
                "return_15m_component": 0.0,
                "range_expansion_component": 0.0,
                "volume_acceleration_component": 0.0,
                "breakout_component": 0.0,
                "reversal_component": 0.0,
            },
            "inputs": {
                "return_1m": 0.0,
                "return_5m": 0.0,
                "return_15m": 0.0,
                "range_5m": 0.0,
                "range_15m": 0.0,
                "volume_change_5m": 0.0,
                "volume_change_15m": 0.0,
                "latest_close": 0.0,
                "latest_volume": 0.0,
            },
            "reason": reason,
            "note": "Experimental debug-only context; not used by live MOS scoring or event generation.",
        }

    @classmethod
    def _normalize_candles(cls, candles: Iterable[Dict[str, Any]], timestamp_utc: Optional[float]) -> List[Dict[str, float]]:
        normalized: List[Dict[str, float]] = []
        cutoff = cls._safe_float(timestamp_utc, 0.0) if timestamp_utc is not None else 0.0
        for row in candles or []:
            item = {
                "timestamp_utc": cls._safe_float(row.get("timestamp_utc")),
                "open": cls._safe_float(row.get("open")),
                "high": cls._safe_float(row.get("high")),
                "low": cls._safe_float(row.get("low")),
                "close": cls._safe_float(row.get("close")),
                "volume": cls._safe_float(row.get("volume")),
            }
            if item["timestamp_utc"] <= 0:
                continue
            if cutoff > 0 and item["timestamp_utc"] > cutoff:
                continue
            if item["open"] <= 0 or item["high"] <= 0 or item["low"] <= 0 or item["close"] <= 0:
                continue
            normalized.append(item)
        normalized.sort(key=lambda x: x["timestamp_utc"])
        return normalized

    @staticmethod
    def _pct_return(rows: List[Dict[str, float]]) -> float:
        if len(rows) < 2:
            return 0.0
        start = rows[0]["open"]
        end = rows[-1]["close"]
        return ((end - start) / start * 100.0) if start > 0 else 0.0

    @staticmethod
    def _range_pct(rows: List[Dict[str, float]]) -> float:
        if len(rows) < 2:
            return 0.0
        start = rows[0]["open"]
        high = max(row["high"] for row in rows)
        low = min(row["low"] for row in rows)
        return ((high - low) / start * 100.0) if start > 0 else 0.0

    @staticmethod
    def _volume_change(rows: List[Dict[str, float]], recent_count: int) -> float:
        if len(rows) < recent_count * 2:
            return 0.0
        previous = sum(row["volume"] for row in rows[-recent_count * 2:-recent_count])
        current = sum(row["volume"] for row in rows[-recent_count:])
        return ((current - previous) / previous * 100.0) if previous > 0 else 0.0

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @classmethod
    def calculate(
        cls,
        timestamp_utc: Optional[float],
        spot_price: float,
        candles: Iterable[Dict[str, Any]],
        volume: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return diagnostic short-term pressure from OHLCV candles."""
        rows = cls._normalize_candles(candles, timestamp_utc)
        if len(rows) < 2:
            return cls._empty("insufficient_ohlcv_history")

        last_5m = rows[-5:] if len(rows) >= 5 else rows
        last_15m = rows[-15:] if len(rows) >= 15 else rows
        previous_15m = rows[-30:-15] if len(rows) >= 30 else []

        latest = rows[-1]
        return_1m = (
            (latest["close"] - latest["open"]) / latest["open"] * 100.0
            if latest["open"] > 0 else 0.0
        )
        return_5m = cls._pct_return(last_5m)
        return_15m = cls._pct_return(last_15m)
        range_5m = cls._range_pct(last_5m)
        range_15m = cls._range_pct(last_15m)
        volume_change_5m = cls._volume_change(rows, 5)
        volume_change_15m = 0.0
        if previous_15m:
            previous_volume = sum(row["volume"] for row in previous_15m)
            current_volume = sum(row["volume"] for row in last_15m)
            volume_change_15m = ((current_volume - previous_volume) / previous_volume * 100.0) if previous_volume > 0 else 0.0

        directional_bias = 1.0 if return_15m > 0 else -1.0 if return_15m < 0 else 0.0
        if directional_bias == 0 and spot_price:
            latest_close = rows[-1]["close"]
            directional_bias = 1.0 if spot_price > latest_close else -1.0 if spot_price < latest_close else 0.0

        return_1m_component = 0.0
        if return_1m > 0.15:
            return_1m_component = 8.0
        elif return_1m > 0.05:
            return_1m_component = 3.0
        elif return_1m < -0.15:
            return_1m_component = -8.0
        elif return_1m < -0.05:
            return_1m_component = -3.0

        return_5m_component = 0.0
        if return_5m > 0.30:
            return_5m_component = 15.0
        elif return_5m > 0.15:
            return_5m_component = 8.0
        elif return_5m < -0.30:
            return_5m_component = -15.0
        elif return_5m < -0.15:
            return_5m_component = -8.0

        return_15m_component = 0.0
        if return_15m > 0.60:
            return_15m_component = 25.0
        elif return_15m > 0.30:
            return_15m_component = 12.0
        elif return_15m < -0.60:
            return_15m_component = -25.0
        elif return_15m < -0.30:
            return_15m_component = -12.0

        range_expansion_component = 0.0
        if range_15m > 0.60 and directional_bias:
            range_expansion_component = 8.0 * directional_bias

        volume_acceleration_component = 0.0
        if volume_change_15m > 40 and directional_bias:
            volume_acceleration_component = 7.0 * directional_bias

        breakout_component = 0.0
        reversal_component = 0.0

        components = {
            "return_1m_component": round(return_1m_component, 2),
            "return_5m_component": round(return_5m_component, 2),
            "return_15m_component": round(return_15m_component, 2),
            "range_expansion_component": round(range_expansion_component, 2),
            "volume_acceleration_component": round(volume_acceleration_component, 2),
            "breakout_component": round(breakout_component, 2),
            "reversal_component": round(reversal_component, 2),
        }
        pressure = cls._clamp(sum(components.values()), -100.0, 100.0)
        intensity = abs(pressure)
        if pressure >= 50:
            direction = "STRONG_BUY"
        elif pressure >= 25:
            direction = "MODERATE_BUY"
        elif pressure >= 8:
            direction = "WEAK_BUY"
        elif pressure <= -50:
            direction = "STRONG_SELL"
        elif pressure <= -25:
            direction = "MODERATE_SELL"
        elif pressure <= -8:
            direction = "WEAK_SELL"
        else:
            direction = "NEUTRAL"

        if direction == "NEUTRAL":
            reason = "neutral_short_term_flow"
        elif direction.endswith("BUY"):
            reason = "buy_short_term_momentum"
        elif direction.endswith("SELL"):
            reason = "sell_short_term_momentum"
        else:
            reason = "mixed_short_term_flow"

        return {
            "status": "experimental",
            "available": True,
            "flow_scale": cls.FLOW_SCALE,
            "short_term_flow_pressure": round(pressure, 2),
            "short_term_flow_intensity": round(intensity, 2),
            "direction": direction,
            "components": components,
            "inputs": {
                "return_1m": round(return_1m, 3),
                "return_5m": round(return_5m, 3),
                "return_15m": round(return_15m, 3),
                "range_5m": round(range_5m, 3),
                "range_15m": round(range_15m, 3),
                "volume_change_5m": round(volume_change_5m, 2),
                "volume_change_15m": round(volume_change_15m, 2),
                "latest_close": round(rows[-1]["close"], 2),
                "latest_volume": round(volume if volume is not None else rows[-1]["volume"], 4),
            },
            "ohlcv_window_candles": len(rows),
            "ohlcv_from": rows[0]["timestamp_utc"],
            "ohlcv_to": rows[-1]["timestamp_utc"],
            "reason": reason,
            "note": "Experimental debug-only context; not used by live MOS scoring or event generation.",
        }
