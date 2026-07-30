"""Replay Testing Framework — Validates MOS predictive engines against historical data.

This tool allows loading historical snapshots from HistoryDB (or a mock dataset)
and replaying them chronologically through the StateEngine.

Goal:
Verify whether the advanced intelligence engines (Regime Transition, Liquidity Void)
detected structural deterioration BEFORE market expansion occurred.
"""

import time
import logging
from typing import List, Dict

from engine.history_db import HistoryDB
from engine.state_engine import StateEngine

log = logging.getLogger(__name__)

class MockDataManager:
    """Mocks DataManager interface for historical replay."""
    def __init__(self, snapshot: dict):
        self.spot_price = snapshot.get("spot", 0)
        self.chain = snapshot.get("chain", {})
        self.per_exchange_tickers = snapshot.get("per_exchange_tickers", {})
        self.exchange_health = snapshot.get("exchange_health", {})
        self.exchange_weights = snapshot.get("exchange_weights", {})
        self.per_exchange_summary = snapshot.get("per_exchange_summary", {})
        self.global_metrics = snapshot.get("global_metrics", {})
        self.oi_history = snapshot.get("oi_history", [])

    def get_oi_delta_pct(self, symbol: str, hours_ago: int = 24) -> float:
        return 0.0

    def get_expiry_nearest(self) -> str:
        return list(self.chain.keys())[0] if self.chain else ""

    def get_chain_for_expiry(self, expiry: str) -> dict:
        return self.chain.get(expiry, {})


class ReplayFramework:
    """Orchestrates historical replay validation."""
    
    @staticmethod
    def run_replay(snapshots: List[Dict]):
        """Run replay on a list of historical snapshots.
        
        Args:
            snapshots: List of dicts containing 'ts', 'spot', 'chain', etc.
        """
        results = []
        log.info(f"Starting Replay Testing: {len(snapshots)} snapshots")
        
        for idx, snap in enumerate(snapshots):
            ts = snap.get("ts", 0)
            spot = snap.get("spot", 0)
            log.info(f"Replaying T={ts} (Spot: {spot})")
            
            dm = MockDataManager(snap)
            market_state = StateEngine.build_market_state(dm)
            
            adv_intel = market_state.get("advanced_intelligence", {})
            rt = adv_intel.get("regime_transition", {}).get("metrics", {})
            
            expansion_prob = rt.get("expansion_probability", 0)
            void_count = adv_intel.get("liquidity_voids", {}).get("metrics", {}).get("total_void_count", 0)
            hedge_risk = adv_intel.get("dealer_hedging", {}).get("metrics", {}).get("hedge_acceleration_risk", "LOW")
            
            results.append({
                "ts": ts,
                "spot": spot,
                "expansion_prob": expansion_prob,
                "void_count": void_count,
                "hedge_risk": hedge_risk,
                "market_state": market_state
            })
            
            if expansion_prob > 50:
                log.warning(f"⚠️ Structural Deterioration Detected at T={ts}! Expansion Prob: {expansion_prob}%")
                
        return results

    @staticmethod
    def analyze_replay_results(results: List[Dict]):
        """Analyze if deterioration preceded price expansion."""
        if not results:
            return
            
        print("\n--- Replay Analysis Report ---")
        for i in range(1, len(results)):
            prev = results[i-1]
            curr = results[i]
            
            spot_change = (curr["spot"] - prev["spot"]) / prev["spot"] * 100 if prev["spot"] else 0
            
            if abs(spot_change) > 2.0:
                print(f"\n[!] Significant Spot Move Detected: {spot_change:.2f}% at T={curr['ts']}")
                print(f"    Prior State (T={prev['ts']}):")
                print(f"    - Expansion Prob: {prev['expansion_prob']}%")
                print(f"    - Voids Count: {prev['void_count']}")
                print(f"    - Hedge Risk: {prev['hedge_risk']}")
                
                if prev['expansion_prob'] > 50:
                    print("    ✅ SUCCESS: Structural deterioration correctly forecast expansion risk.")
                else:
                    print("    ❌ MISS: Move occurred without prior deterioration warning.")

if __name__ == "__main__":
    # Example usage
    import json
    import os
    logging.basicConfig(level=logging.INFO)
    
    print("Replay Testing Framework initialized.")
    print("To run, provide a list of historical snapshots to ReplayFramework.run_replay()")
