"""Replay Simulation Script

Runs a synthetic historical replay using real market data structures.
We simulate three distinct market phases to verify the Advanced Intelligence engines:
1. COMPRESSION: Strong dealer gamma support, high OI, stable spot.
2. DETERIORATION: Spot moves up, dealer support weakens, liquidity voids emerge.
3. EXPANSION: Acceleration into the void, IV spikes.
"""

import asyncio
import json
import logging
from collections import defaultdict

from api.bybit_adapter import BybitAdapter
from engine.instrument_normalizer import InstrumentNormalizer
from engine.gamma_surface_engine import GammaSurfaceEngine
from engine.liquidity_void_engine import LiquidityVoidEngine
from engine.dealer_hedging_engine import DealerHedgingEngine
from engine.regime_transition_engine import RegimeTransitionEngine

from engine.term_structure_engine import TermStructureEngine
from engine.synthetic_orderflow_engine import SyntheticOrderflowEngine
from engine.breakout_timing_engine import BreakoutTimingEngine
from engine.execution_timing_engine import ExecutionTimingEngine

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def build_chain(tickers, spot_override=None):
    """Build a standard chain from normalized tickers, with optional spot shifting."""
    chain = defaultdict(lambda: defaultdict(dict))
    for t in tickers:
        expiry = t["expiry"]
        strike = t["strike"]
        opt_type = t["type"]
        chain[expiry][strike][opt_type] = t
    return dict(chain)

def modify_chain_for_phase(tickers, phase, spot):
    """Synthetically alter OI and Gamma to simulate structural phases."""
    import copy
    new_tickers = copy.deepcopy(tickers)
    
    for t in new_tickers:
        strike = t["strike"]
        opt_type = t["type"]
        
        if phase == "COMPRESSION":
            # Massive OI around spot (±2%)
            if abs(strike - spot)/spot < 0.02:
                t["oi"] = t.get("oi", 0) * 5 + 100
                t["volume"] = t.get("volume", 0) * 3 + 50
                t["gamma"] = t.get("gamma", 0) * 2
                
        elif phase == "DETERIORATION":
            # Void emerging above spot (+2% to +5%)
            dist = (strike - spot)/spot
            if 0.01 < dist < 0.06:
                t["oi"] = t.get("oi", 0) * 0.1
                t["volume"] = t.get("volume", 0) * 0.1
                t["gamma"] = t.get("gamma", 0) * 0.1
            # Normal support below spot
            elif dist < 0:
                t["oi"] = t.get("oi", 0) * 2 + 50

        elif phase == "EXPANSION":
            # IV spikes, gamma drops everywhere, wide spreads
            t["gamma"] = t.get("gamma", 0) * 0.3
            if opt_type == "C":
                t["delta"] = min(1.0, t.get("delta", 0) * 1.5)
                
    return new_tickers

async def run_simulation():
    log.info("Fetching real market data to build simulation baseline...")
    adapter = BybitAdapter()
    
    raw_tickers = await adapter.fetch_option_tickers()
    spot = await adapter.fetch_spot_price()
    
    if not spot or not raw_tickers:
        log.error("Failed to fetch market data.")
        return
        
    normalized = []
    for raw in raw_tickers:
        norm = InstrumentNormalizer.normalize_ticker("bybit", raw)
        if norm:
            normalized.append(norm)
            
    # Mock states for Regime Transition Engine
    base_gamma_state = {"signals": {"gamma_regime": "POSITIVE_GAMMA"}}
    base_vol_state = {"signals": {"iv_regime": "COMPRESSION"}, "metrics": {"iv_velocity": 0}}
    base_flow_state = {"signals": {"flow_bias": "NEUTRAL"}, "metrics": {"flow_pressure": 50}}
            
    phases = [
        {"name": "1. COMPRESSION", "spot": spot, "vol_regime": "COMPRESSION", "gamma_regime": "POSITIVE_GAMMA", "iv_vel": 0},
        {"name": "2. DETERIORATION", "spot": spot * 1.02, "vol_regime": "COMPRESSION", "gamma_regime": "NEGATIVE_GAMMA", "iv_vel": 5},
        {"name": "3. EXPANSION", "spot": spot * 1.06, "vol_regime": "EXPANSION", "gamma_regime": "NEGATIVE_GAMMA", "iv_vel": 15},
    ]
    
    print("\n" + "="*60)
    print("🚀 INSTITUTIONAL MOS: REPLAY VALIDATION FRAMEWORK")
    print("="*60)
    
    for p in phases:
        print(f"\n[{p['name']}] Spot: ${p['spot']:,.0f}")
        print("-" * 40)
        
        # Alter chain
        phase_tickers = modify_chain_for_phase(normalized, p["name"].split(". ")[1], p["spot"])
        chain = build_chain(phase_tickers)
        
        # 1. Gamma Surface
        gs = GammaSurfaceEngine.calculate(chain, p["spot"])
        voids = gs["features"].get("gamma_voids", [])
        
        # 2. Liquidity Voids
        lv = LiquidityVoidEngine.calculate(chain, p["spot"], gs)
        true_voids = lv["features"].get("true_voids", [])
        
        # 3. Dealer Hedging
        dh = DealerHedgingEngine.calculate(chain, p["spot"])
        hedge_risk = dh["metrics"]["hedge_acceleration_risk"]
        
        # 4. Regime Transition
        vol_state = {"signals": {"iv_regime": p["vol_regime"]}, "metrics": {"iv_velocity": p["iv_vel"]}}
        gamma_state = {"signals": {"gamma_regime": p["gamma_regime"]}}
        rt = RegimeTransitionEngine.calculate(gamma_state, vol_state, base_flow_state, lv, dh)
        
        # 5. Phase 2 Engines
        ts = TermStructureEngine.calculate(chain, p["spot"])
        so = SyntheticOrderflowEngine.calculate(vol_state, base_flow_state, None, p["spot"])
        bt = BreakoutTimingEngine.calculate(vol_state, rt, so)
        
        ex = ExecutionTimingEngine.calculate(
            {"regime_transition": rt, "dealer_hedging": dh},
            ts, so, bt
        )
        
        # Output intelligence
        ts_state = ts["features"]["term_structure_state"]
        exec_state = ex["features"]["execution_state"]
        breakout_win = bt["features"]["estimated_breakout_window"]
        
        print(f"🔹 Gamma Voids: {len(voids)} zones detected")
        if true_voids:
            v_str = ", ".join([f"${v['start']} - ${v['end']}" for v in true_voids[:2]])
            print(f"🔹 True Liquidity Voids: {v_str}")
        else:
            print(f"🔹 True Liquidity Voids: None")
            
        print(f"🔹 Term Structure State: {ts_state}")
        print(f"🔹 Breakout Window: {breakout_win}")
        print(f"🔹 Execution State: [{exec_state}]")
        print(f"   {ex['features']['execution_context']}")
        
        exp_prob = rt["metrics"]["expansion_probability"]
        fail_risk = rt["metrics"]["compression_failure_risk"]
        
        if exp_prob > 50:
            print("⚠️ ALERT: Structural Deterioration Warning Issued!")
        if fail_risk > 50:
            print("⚠️ ALERT: Compression is highly unstable.")

if __name__ == "__main__":
    asyncio.run(run_simulation())
