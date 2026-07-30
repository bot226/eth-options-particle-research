import os
import re

file_path = r"C:\Users\User\Desktop\Project\btc-gpt-codex-v11-targeted-fix\backend\routes\market.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Insert lookup BEFORE enriched = _enrich_market_state(market_state)
lookup_block = """
        snapshot_ts = market_state.get("timestamp")
        current_ts = _parse_ts_safe(snapshot_ts)
        if current_ts <= 0:
            current_ts = time.time()
        elif current_ts > 1e11:
            current_ts /= 1000.0

        try:
            from engine.main_context_lookup import lookup_latest_main_context
            _mc = lookup_latest_main_context(
                research_db_path=_RESEARCH_DB_PATH,
                current_ts=current_ts,
                lookback_sec=MAIN_CONTEXT_TTL_SEC,
                manual_bias=None,
            )
            market_state["latest_main_context_direction"]      = _mc.get("latest_main_context_direction", "NONE")
            market_state["latest_main_context_reaction_label"] = _mc.get("latest_main_context_reaction_label")
            market_state["latest_main_context_level_side"]     = _mc.get("latest_main_context_level_side")
            market_state["latest_main_context_level_price"]    = _mc.get("latest_main_context_level_price")
            market_state["latest_main_context_ts"]             = _mc.get("latest_main_context_ts")
            market_state["latest_main_context_age_sec"]        = _mc.get("latest_main_context_age_sec")
            market_state["latest_main_context_source"]         = _mc.get("latest_main_context_source")
            market_state["latest_main_context_confidence"]     = _mc.get("latest_main_context_confidence")
            market_state["latest_main_context_deribit_status"] = _mc.get("latest_main_context_deribit_status")
            market_state["checked_main_context_count"]         = _mc.get("checked_main_context_count", 0)
        except Exception as e:
            log.error(f"Error early MAIN_CONTEXT_LOOKUP: {e}")

"""

inject_pattern = r'(\s+)(enriched = _enrich_market_state\(market_state\))'
content = re.sub(inject_pattern, r'\1' + lookup_block.strip() + r'\n\n\1\2', content, count=1)

# Ensure manual_setup gets the fields immediately
inject_pattern2 = r'(\s+)(sf = ManualSetupClassifier\.source_fields\(enriched\))'
inject2_block = """
        # Inject main context fields into manual_setup for candidate key and later conflict logic
        for k in ["latest_main_context_direction", "latest_main_context_reaction_label", 
                  "latest_main_context_level_side", "latest_main_context_level_price", 
                  "latest_main_context_ts", "latest_main_context_age_sec", 
                  "latest_main_context_source", "latest_main_context_confidence", 
                  "latest_main_context_deribit_status", "checked_main_context_count"]:
            if k in market_state:
                manual_setup[k] = market_state[k]
"""
content = re.sub(inject_pattern2, r'\1\2\n' + inject2_block, content, count=1)

# Remove old current_ts calculation since we moved it up
content = re.sub(r'(\s+)snapshot_ts = market_state\.get\("timestamp"\)\n\s+current_ts = _parse_ts_safe\(snapshot_ts\)\n\s+if current_ts <= 0:\n\s+current_ts = time\.time\(\)\n\s+elif current_ts > 1e11:\n\s+current_ts /= 1000\.0', r'\1# TS calculation moved up', content, count=1)

# 2. Fix candidate_key logic
old_candidate_key_logic = r'''\s+mc_source = manual_setup\.get\("latest_main_context_source"\) or ""\n\s+deribit_status = manual_setup\.get\("latest_main_context_deribit_status"\)\n\s+if mc_source == "ohlcv_only" or \("ohlcv_only" in mc_source and deribit_status != "ACTIVE"\):\n\s+key_src = "ohlcv_only"\n\s+else:\n\s+key_src = "gamma_walls\+ohlcv_1h" if src and "gamma_walls" in src else \(src or ""\)'''

new_candidate_key_logic = """
                mc_source = manual_setup.get("latest_main_context_source") or ""
                deribit_status = manual_setup.get("latest_main_context_deribit_status")
                
                if mc_source == "ohlcv_only":
                    key_src = "ohlcv_only"
                elif mc_source == "gamma_walls+ohlcv_1h":
                    if deribit_status == "ACTIVE":
                        key_src = "gamma_walls+ohlcv_1h"
                    else:
                        key_src = "ohlcv_only"
                else:
                    key_src = src or ""
                    if "gamma_walls" in key_src:
                        key_src = "ohlcv_only"
"""
content = re.sub(old_candidate_key_logic, new_candidate_key_logic, content, count=1)

# 3. Replace old main_context_lookup with just taking values from manual_setup
old_main_context_lookup = r'''\s+# ── 11\. MAIN CONTEXT GUARD \(v29\) ──.*?checked_main_context_count = manual_setup\.get\("checked_main_context_count", 0\)'''

new_main_context_lookup = """
        # ── 11. MAIN CONTEXT GUARD (v29) ──
        latest_main_context_direction = manual_setup.get("latest_main_context_direction", "NONE")
        latest_main_context_reaction_label = manual_setup.get("latest_main_context_reaction_label")
        latest_main_context_level_side = manual_setup.get("latest_main_context_level_side")
        latest_main_context_level_price = manual_setup.get("latest_main_context_level_price")
        latest_main_context_ts = manual_setup.get("latest_main_context_ts")
        latest_main_context_age_sec = manual_setup.get("latest_main_context_age_sec")
        latest_main_context_source = manual_setup.get("latest_main_context_source")
        latest_main_context_confidence = manual_setup.get("latest_main_context_confidence")
        latest_main_context_deribit_status = manual_setup.get("latest_main_context_deribit_status")
        checked_main_context_count = manual_setup.get("checked_main_context_count", 0)
"""
content = re.sub(old_main_context_lookup, new_main_context_lookup, content, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied to market.py")
