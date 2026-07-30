"""ExchangeLeadershipEngine — Detects which exchange is leading the market.

Temporal delta analysis to identify the "first mover" (leader) for:
    - IV expansion/contraction
    - Directional flow
    - OI shifts
"""

import time
import logging
from collections import deque
from typing import Dict, Any

log = logging.getLogger(__name__)


class ExchangeLeadershipEngine:
    """Tracks temporal leadership across exchanges."""

    # History: exchange_id -> deque of (timestamp, value)
    _iv_history: Dict[str, deque] = {}
    _flow_history: Dict[str, deque] = {}
    
    # Store previous state for delta computation
    _prev_iv: Dict[str, float] = {}
    _prev_flow: Dict[str, float] = {}

    @classmethod
    def calculate(cls, per_exchange_tickers: Dict[str, list], spot: float) -> Dict[str, Any]:
        """Determine market leaders.

        Args:
            per_exchange_tickers: {exchange_id: [tickers]}
            spot: current spot price

        Returns:
            {
                "iv_leader": "deribit" | "bybit" | "none",
                "flow_leader": str,
                "momentum_leader": str,
                "leadership_confidence": int,
                "leadership_context": str,
            }
        """
        now = time.time()

        # Update IV and Flow histories
        current_ivs = {}
        current_flows = {}

        for ex_id, tickers in per_exchange_tickers.items():
            if ex_id not in cls._iv_history:
                cls._iv_history[ex_id] = deque(maxlen=60) # ~3 min at 3s interval
                cls._flow_history[ex_id] = deque(maxlen=60)

            iv = cls._get_atm_iv(tickers, spot)
            if iv > 0:
                current_ivs[ex_id] = iv
                if ex_id in cls._prev_iv:
                    delta = iv - cls._prev_iv[ex_id]
                    if abs(delta) > 0.001:  # significant move
                        cls._iv_history[ex_id].append({"ts": now, "delta": delta, "val": iv})
                cls._prev_iv[ex_id] = iv

            # Placeholder for flow: using sum of volume as momentum proxy
            flow = sum(t.get("volume", 0) for t in tickers)
            if flow > 0:
                current_flows[ex_id] = flow
                if ex_id in cls._prev_flow:
                    delta = flow - cls._prev_flow[ex_id]
                    if abs(delta) > 0:
                        cls._flow_history[ex_id].append({"ts": now, "delta": delta, "val": flow})
                cls._prev_flow[ex_id] = flow

        # Analyze leaders
        iv_leader = cls._find_leader(cls._iv_history, threshold=0.01) # 1% IV shift
        flow_leader = cls._find_leader(cls._flow_history, threshold=10) 

        context = []
        if iv_leader and iv_leader != "none":
            context.append(f"{iv_leader.capitalize()} leads IV dynamics")
        if flow_leader and flow_leader != "none":
            context.append(f"{flow_leader.capitalize()} leads volume flow")

        leadership_confidence = 100
        if not iv_leader or iv_leader == "none":
            leadership_confidence -= 20
        if not flow_leader or flow_leader == "none":
            leadership_confidence -= 20

        return {
            "iv_leader": iv_leader or "none",
            "flow_leader": flow_leader or "none",
            "momentum_leader": flow_leader or "none",
            "leadership_confidence": max(0, leadership_confidence),
            "leadership_context": ", ".join(context) if context else "No clear market leader",
        }

    @staticmethod
    def _find_leader(history_dict: Dict[str, deque], threshold: float) -> str:
        """Find the exchange that consistently moved first in the recent window."""
        # Find the earliest timestamp of a significant delta across all exchanges
        earliest_move = float('inf')
        leader = "none"

        for ex_id, history in history_dict.items():
            for item in history:
                if abs(item["delta"]) >= threshold:
                    if item["ts"] < earliest_move:
                        earliest_move = item["ts"]
                        leader = ex_id
                    break # check only the first significant move in the window

        return leader

    @staticmethod
    def _get_atm_iv(tickers: list, spot: float) -> float:
        if not tickers or spot <= 0:
            return 0.0
        best_iv = 0.0
        best_dist = float("inf")
        for t in tickers:
            strike = t.get("strike", 0)
            iv = t.get("markIv", 0)
            if strike <= 0 or iv <= 0:
                continue
            dist = abs(strike - spot)
            if dist < best_dist:
                best_dist = dist
                best_iv = iv
        return best_iv
