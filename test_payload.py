import time
import os
from datetime import datetime

# Set cwd to backend so sqlite connects correctly
os.chdir("c:\\Users\\User\\Desktop\\Project\\btc-gpt-codex-v11-targeted-fix\\backend")

from routes.market import _build_manual_trading_payload

# Create a mock market_state that will trigger ENTRY_CANDIDATE
market_state = {
    "timestamp": 1781075025000, # corresponding to 2026-06-10T07:03:45Z
    "symbol": "BTCUSD",
    "spot": 61500,
    "current_state": "TRANSITION",
    "execution_timing_state": "EXECUTION_WINDOW_OPEN",
    "event_type": "BREAKOUT_ALERT",
    "level_result": "RESISTANCE_REJECTION",
    "level_side": "RESISTANCE",
    "short_term_flow_direction": "BEARISH",
    "signal_cluster_score": 70,
    "expansion_probability": 60,
    "nearest_level": 61600,
    "synthetic_flow_pressure": -20,
    "gamma": {"metrics": {"call_wall": 62000, "put_wall": 60000}},
    "advanced_intelligence": {
        "signal_cluster_score": 70,
        "phase_1": {
            "gamma_surface": {"metrics": {"gamma_slope_state": "weakening"}},
            "regime_transition": {"metrics": {"expansion_probability": 60}},
            "short_term_flow_context": {"features": {"short_term_flow_direction": "BEARISH"}}
        },
        "phase_2": {
            "execution_timing": {"features": {"execution_state": "EXECUTION_WINDOW_OPEN"}}
        }
    }
}

payload = _build_manual_trading_payload(market_state)
print("Manual Status:", payload["manual_setup"].get("manual_status"))
print("Bias:", payload["manual_setup"].get("manual_bias"))
print("Main context active:", payload["manual_setup"].get("main_context_conflict_active"))
print("Main context count:", payload["manual_setup"].get("checked_main_context_count"))
print("Latest context direction:", payload["manual_setup"].get("latest_main_context_direction"))
print("Decision blocker:", payload["manual_setup"].get("decision_blocker"))
