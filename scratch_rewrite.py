import re
import os

with open("c:\\Users\\User\\Desktop\\Project\\btc-gpt-codex-v11-targeted-fix\\backend\\routes\\market.py", "r", encoding="utf-8") as f:
    content = f.read()

start_marker = "        # ── LADDER GUARD (+1R CONFIRMATION) ──\n"
end_marker = "        # ── END MAIN CONTEXT CONFLICT GUARD ──\n"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker) + len(end_marker)

if start_idx == -1 or content.find(end_marker) == -1:
    print("Markers not found!")
    exit(1)

new_content = """        # ── MAIN CONTEXT CONFLICT GUARD ──
        def get_main_context_direction(reaction_label, level_side):
            if (reaction_label == "SUPPORT_DEFENSE" and level_side == "SUPPORT") or \\
               (reaction_label == "FALSE_BREAK" and level_side == "SUPPORT") or \\
               (reaction_label == "LEVEL_BREAK" and level_side == "RESISTANCE"):
                return "LONG"
            if (reaction_label == "RESISTANCE_REJECTION" and level_side == "RESISTANCE") or \\
               (reaction_label == "FALSE_BREAK" and level_side == "RESISTANCE") or \\
               (reaction_label == "LEVEL_BREAK" and level_side == "SUPPORT"):
                return "SHORT"
            return "NEUTRAL"
            
        main_context_conflict_active = 0
        main_context_conflict_side = None
        main_context_conflict_reason = None
        main_context_conflict_reaction_label = None
        main_context_conflict_level_side = None
        main_context_conflict_level_price = None
        main_context_conflict_ts = None
        main_context_conflict_age_sec = None
        main_context_conflict_stale = 0
        main_context_conflict_source = None
        latest_main_context_direction = "NONE"
        latest_main_context_reaction_label = None
        latest_main_context_level_side = None
        latest_main_context_age_sec = None
        checked_main_context_count = 0
        
        if status == "ENTRY_CANDIDATE" and actionability == "ACTIONABLE" and bias in ("LONG", "SHORT"):
            try:
                from engine.research_logger import get_db_connection as get_research_db
                import os
                
                cwd = os.getcwd()
                research_db_path = _RESEARCH_DB_PATH
                db_exists = os.path.exists(research_db_path)
                
                conn = get_research_db()
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*), MAX(event_timestamp_utc) FROM event_level_reactions")
                c_row = cursor.fetchone()
                elr_count = c_row[0] if c_row else 0
                elr_max_ts = c_row[1] if c_row else None

                now_sec = current_ts
                cutoff_sec = now_sec - 600
                
                from datetime import datetime
                # Handle iso correctly with fractional seconds
                now_iso = datetime.utcfromtimestamp(now_sec).isoformat() + "Z"
                cutoff_iso = datetime.utcfromtimestamp(cutoff_sec).isoformat() + "Z"
                
                query = '''
                    SELECT event_timestamp_utc, reaction_label, level_side, level_price, event_type
                    FROM event_level_reactions
                    WHERE event_timestamp_utc <= ? AND event_timestamp_utc >= ?
                    ORDER BY event_timestamp_utc DESC
                    LIMIT 20
                '''
                cursor.execute(query, (now_iso, cutoff_iso))
                rows = cursor.fetchall()
                conn.close()
                
                log.info(
                    f"Main Context Conflict Guard Debug:\\n"
                    f"  cwd: {cwd}\\n"
                    f"  research_db_path: {research_db_path}\\n"
                    f"  db_exists: {db_exists}\\n"
                    f"  total_event_level_reactions: {elr_count}\\n"
                    f"  latest_event_level_reaction_ts: {elr_max_ts}\\n"
                    f"  current_manual_ts: {now_sec}\\n"
                    f"  current_manual_ts_iso: {now_iso}\\n"
                    f"  cutoff_600_iso: {cutoff_iso}\\n"
                    f"  SQL_query_params: {(now_iso, cutoff_iso)}\\n"
                    f"  raw_rows_returned: {len(rows)}"
                )
                
                filtered_rows = []
                for row in rows:
                    ev_ts_raw, r_label, l_side, l_price, ev_type = row
                    if not r_label or not l_side: continue
                    if r_label not in ("SUPPORT_DEFENSE", "FALSE_BREAK", "LEVEL_BREAK", "RESISTANCE_REJECTION"): continue
                    if l_side not in ("SUPPORT", "RESISTANCE"): continue
                    
                    mdir = get_main_context_direction(r_label, l_side)
                    if mdir != "NEUTRAL":
                        filtered_rows.append(row)

                checked_main_context_count = len(filtered_rows)
                
                log.info(
                    f"Main Context Conflict Guard Post-Filter Debug:\\n"
                    f"  filtered_rows_count: {checked_main_context_count}"
                )
                
                if filtered_rows:
                    recent_directional_row = filtered_rows[0]
                    ev_ts_raw, r_label, l_side, l_price, ev_type = recent_directional_row
                    latest_main_context_direction = get_main_context_direction(r_label, l_side)
                    latest_main_context_reaction_label = r_label
                    latest_main_context_level_side = l_side
                    
                    ev_ts = _parse_ts_safe(ev_ts_raw)
                    age_sec = now_sec - ev_ts
                    latest_main_context_age_sec = age_sec
                    
                    if latest_main_context_direction != bias:
                        # Conflict!
                        if age_sec <= 360:
                            # Hard block
                            main_context_conflict_active = 1
                            main_context_conflict_side = latest_main_context_direction
                            main_context_conflict_reason = "matched reaction"
                            main_context_conflict_reaction_label = r_label
                            main_context_conflict_level_side = l_side
                            main_context_conflict_level_price = l_price
                            main_context_conflict_ts = ev_ts_raw
                            main_context_conflict_age_sec = age_sec
                            main_context_conflict_stale = 0
                            main_context_conflict_source = "mos_research_db"
                            
                            status = "WATCH"
                            actionability = "FORMING" if forming_key else "NOT_ACTIONABLE"
                            
                            if bias == "LONG":
                                c_msg = "Основной level context против LONG. Ждать исчезновения конфликта или нового подтверждения поддержки."
                            else:
                                c_msg = "Основной level context против SHORT. Ждать rejection / возврата контекста в сторону SHORT."
                            
                            manual_setup["manual_status"] = status
                            manual_setup["actionability"] = actionability
                            manual_setup["decision_blocker"] = "MAIN_CONTEXT_CONFLICT_GUARD"
                            manual_setup["confirmation_needed"] = c_msg
                        
                        elif age_sec <= 600:
                            # Stale caution
                            main_context_conflict_stale = 1
                            main_context_conflict_age_sec = age_sec
                            if manual_setup.get("setup_quality") != "FORMING":
                                manual_setup["setup_quality"] = "CAUTION"
                            
                            caution_msg = "Есть старый конфликт основного level context, нужен осторожный режим."
                            old_msg = manual_setup.get("confirmation_needed", "")
                            if old_msg:
                                manual_setup["confirmation_needed"] = old_msg + " " + caution_msg
                            else:
                                manual_setup["confirmation_needed"] = caution_msg

            except Exception as e:
                log.error(f"Error evaluating MAIN_CONTEXT_CONFLICT_GUARD: {e}")
                latest_main_context_direction = "UNAVAILABLE"
                
            if main_context_conflict_active or main_context_conflict_stale or checked_main_context_count > 0:
                log.info(
                    f"Main Context Conflict Diagnostics:\\n"
                    f"  current_ts: {current_ts}\\n"
                    f"  manual_direction: {bias}\\n"
                    f"  checked_main_context_count: {checked_main_context_count}\\n"
                    f"  latest_main_context_direction: {latest_main_context_direction}\\n"
                    f"  latest_main_context_reaction_label: {latest_main_context_reaction_label}\\n"
                    f"  latest_main_context_level_side: {latest_main_context_level_side}\\n"
                    f"  latest_main_context_age_sec: {latest_main_context_age_sec}\\n"
                    f"  main_context_conflict_active: {main_context_conflict_active}\\n"
                    f"  main_context_conflict_reason: {main_context_conflict_reason}"
                )
                
        manual_setup["main_context_conflict_active"] = main_context_conflict_active
        manual_setup["main_context_conflict_side"] = main_context_conflict_side
        manual_setup["main_context_conflict_reason"] = main_context_conflict_reason
        manual_setup["main_context_conflict_reaction_label"] = main_context_conflict_reaction_label
        manual_setup["main_context_conflict_level_side"] = main_context_conflict_level_side
        manual_setup["main_context_conflict_level_price"] = main_context_conflict_level_price
        manual_setup["main_context_conflict_ts"] = main_context_conflict_ts
        manual_setup["main_context_conflict_age_sec"] = main_context_conflict_age_sec
        manual_setup["main_context_conflict_stale"] = main_context_conflict_stale
        manual_setup["main_context_conflict_source"] = main_context_conflict_source
        manual_setup["latest_main_context_direction"] = latest_main_context_direction
        manual_setup["latest_main_context_reaction_label"] = latest_main_context_reaction_label
        manual_setup["latest_main_context_level_side"] = latest_main_context_level_side
        manual_setup["latest_main_context_age_sec"] = latest_main_context_age_sec
        manual_setup["checked_main_context_count"] = checked_main_context_count
        # ── END MAIN CONTEXT CONFLICT GUARD ──

        # ── LADDER GUARD (+1R CONFIRMATION) ──
        previous_candidate_key = None
        previous_candidate_ts = None
        previous_candidate_entry_price = None
        previous_candidate_level = None
        previous_candidate_invalidation = None
        previous_candidate_mfe_r = None
        previous_candidate_reached_1r = None
        level_ladder_guard_active = 0
        
        if status == "ENTRY_CANDIDATE" and actionability == "ACTIONABLE" and level is not None and inv_level is not None:
            is_long_support = (setup_type == "SUPPORT_DEFENSE_REVERSAL_SETUP" and bias == "LONG" and side == "SUPPORT")
            is_short_resistance = (setup_type == "RESISTANCE_REJECTION_REVERSAL_SETUP" and bias == "SHORT" and side == "RESISTANCE")
            
            if is_long_support or is_short_resistance:
                try:
                    logger = get_manual_logger()
                    import sqlite3
                    conn = sqlite3.connect(logger.db_path)
                    cursor = conn.cursor()
                    now_sec = current_ts
                    cutoff_sec = now_sec - 900
                    
                    query = '''
                        SELECT candidate_key, ts, price, selected_setup_level, invalidation_level 
                        FROM manual_trading_snapshots 
                        WHERE manual_status = 'ENTRY_CANDIDATE'
                          AND actionability = 'ACTIONABLE'
                          AND manual_setup_type = ?
                          AND manual_bias = ?
                          AND selected_setup_side = ?
                          AND selected_setup_level IS NOT NULL
                          AND invalidation_level IS NOT NULL
                          AND price IS NOT NULL
                        ORDER BY id DESC LIMIT 200
                    '''
                    params = [setup_type, bias, side]
                    cursor.execute(query, tuple(params))
                    all_candidates = cursor.fetchall()
                    conn.close()
                    
                    prev_row = None
                    for r in all_candidates:
                        r_key = r[0]
                        r_ts_val = _parse_ts_safe(r[1])
                        
                        if r_ts_val >= now_sec:
                            continue
                        if r_ts_val < cutoff_sec:
                            continue
                            
                        prev_row = r
                        break
                    
                    if prev_row:
                        prev_key, prev_ts_raw, prev_price, prev_level, prev_inval = prev_row
                        prev_ts = _parse_ts_safe(prev_ts_raw)
                        previous_candidate_key = prev_key
                        previous_candidate_ts = prev_ts
                        previous_candidate_entry_price = prev_price
                        previous_candidate_level = prev_level
                        previous_candidate_invalidation = prev_inval
                        
                        if prev_price is not None and prev_inval is not None:
                            if is_long_support:
                                risk = prev_price - prev_inval
                            else:
                                risk = prev_inval - prev_price
                                
                            if risk > 0:
                                max_high = None
                                min_low = None
                                
                                for k in klines:
                                    k_ts = k.get("ts")
                                    if k_ts is not None and prev_ts <= k_ts < current_ts:
                                        h = k.get("h")
                                        l = k.get("l")
                                        if h is not None and (max_high is None or h > max_high):
                                            max_high = h
                                        if l is not None and (min_low is None or l < min_low):
                                            min_low = l
                                
                                is_hard_block = False
                                
                                if is_long_support and max_high is not None:
                                    previous_candidate_mfe_r = (max_high - prev_price) / risk
                                    previous_candidate_reached_1r = 1 if max_high >= prev_price + risk else 0
                                    
                                    if previous_candidate_reached_1r == 0 and level < prev_level:
                                        if main_context_conflict_active:
                                            is_hard_block = True
                                        elif latest_main_context_direction == "NONE" and price_confirmation_status not in ("CONFIRMED_HOLD", "CONFIRMED_REJECTION"):
                                            is_hard_block = True
                                        elif previous_candidate_mfe_r < 0 and latest_main_context_direction != "LONG":
                                            is_hard_block = True
                                            
                                        if is_hard_block:
                                            status = "WATCH"
                                            actionability = "FORMING"
                                            decision_blocker = "SUPPORT_LADDER_DOWN_GUARD"
                                            manual_setup["manual_status"] = status
                                            manual_setup["actionability"] = actionability
                                            manual_setup["setup_quality"] = "FORMING"
                                            manual_setup["decision_blocker"] = decision_blocker
                                            manual_setup["confirmation_needed"] = "Предыдущий LONG от поддержки ещё не дал +1R. Ждать стабилизацию или подтверждение удержания."
                                        level_ladder_guard_active = 1
                                        
                                elif is_short_resistance and min_low is not None:
                                    previous_candidate_mfe_r = (prev_price - min_low) / risk
                                    previous_candidate_reached_1r = 1 if min_low <= prev_price - risk else 0
                                    
                                    if previous_candidate_reached_1r == 0 and level > prev_level:
                                        if main_context_conflict_active:
                                            is_hard_block = True
                                        elif latest_main_context_direction == "NONE" and price_confirmation_status not in ("CONFIRMED_HOLD", "CONFIRMED_REJECTION"):
                                            is_hard_block = True
                                        elif previous_candidate_mfe_r < 0 and latest_main_context_direction != "SHORT":
                                            is_hard_block = True
                                            
                                        if is_hard_block:
                                            status = "WATCH"
                                            actionability = "FORMING"
                                            decision_blocker = "RESISTANCE_LADDER_UP_GUARD"
                                            manual_setup["manual_status"] = status
                                            manual_setup["actionability"] = actionability
                                            manual_setup["setup_quality"] = "FORMING"
                                            manual_setup["decision_blocker"] = decision_blocker
                                            manual_setup["confirmation_needed"] = "Предыдущий SHORT от сопротивления ещё не дал +1R. Ждать rejection / возврат ниже сопротивления."
                                        level_ladder_guard_active = 1

                    log.info(
                        f"Ladder Guard Debug:\\n"
                        f"  current_ts: {current_ts}\\n"
                        f"  previous_candidate_query_count: {len(all_candidates)}\\n"
                        f"  previous_candidate_key: {previous_candidate_key}\\n"
                        f"  previous_candidate_mfe_r: {previous_candidate_mfe_r}\\n"
                        f"  previous_candidate_reached_1r: {previous_candidate_reached_1r}\\n"
                        f"  ladder_guard_decision: {'ACTIVE' if level_ladder_guard_active else 'INACTIVE'}"
                    )
                except Exception as e:
                    log.error(f"Error evaluating ladder guard: {e}")
                    
        manual_setup["previous_candidate_key"] = previous_candidate_key
        manual_setup["previous_candidate_ts"] = previous_candidate_ts
        manual_setup["previous_candidate_entry_price"] = previous_candidate_entry_price
        manual_setup["previous_candidate_level"] = previous_candidate_level
        manual_setup["previous_candidate_invalidation"] = previous_candidate_invalidation
        manual_setup["previous_candidate_mfe_r"] = previous_candidate_mfe_r
        manual_setup["previous_candidate_reached_1r"] = previous_candidate_reached_1r
        manual_setup["level_ladder_guard_active"] = level_ladder_guard_active
        # ── END LADDER GUARD ──\n"""

with open("c:\\Users\\User\\Desktop\\Project\\btc-gpt-codex-v11-targeted-fix\\backend\\routes\\market.py", "w", encoding="utf-8") as f:
    f.write(content[:start_idx] + new_content + content[end_idx:])

print("Successfully replaced.")
