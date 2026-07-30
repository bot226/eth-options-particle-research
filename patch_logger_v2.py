import re

def main():
    with open('backend/engine/manual_logger.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the log_snapshot method
    match = re.search(r'(    def log_snapshot\(self, payload: Dict\[str, Any\]\):.*?try:\n)(.*?)(        finally:\n            conn\.close\(\))', content, flags=re.DOTALL)
    if not match:
        print("Could not find log_snapshot method!")
        return

    prefix = match.group(1)
    suffix = match.group(3)

    new_body = """            missing_conditions = payload.get('missing_conditions')
            if isinstance(missing_conditions, (dict, list)):
                import json
                missing_conditions = json.dumps(missing_conditions)
                
            fields = {
                'ts': payload.get('ts'),
                'frontend_session_id': payload.get('frontend_session_id'),
                'source': payload.get('source', 'frontend'),
                'code_version': payload.get('code_version'),
                'manual_logger_version': MANUAL_LOGGER_VERSION,
                'manual_ui_version': payload.get('manual_ui_version'),
                'price': payload.get('price'),
                'manual_status': payload.get('manual_status'),
                'setup_type': payload.get('setup_type'),
                'manual_bias': payload.get('manual_bias'),
                'setup_quality': payload.get('setup_quality'),
                'actionability': payload.get('actionability'),
                'current_state': payload.get('current_state'),
                'execution_timing_state': payload.get('execution_timing_state'),
                'short_term_flow_direction': payload.get('short_term_flow_direction'),
                'event_type': payload.get('event_type'),
                'level_result': payload.get('level_result'),
                'nearest_level': payload.get('nearest_level'),
                'invalidation_level': payload.get('invalidation_level'),
                'manual_reason': payload.get('manual_reason'),
                'confirmation_needed': payload.get('confirmation_needed'),
                'invalidation_condition': payload.get('invalidation_condition'),
                'missing_conditions': missing_conditions,
                'chart_status': payload.get('chart_status'),
                'latest_candle_ts': payload.get('latest_candle_ts'),
                'last_fetch_ts': payload.get('last_fetch_ts'),
                'seconds_since_last_fetch': payload.get('seconds_since_last_fetch'),
                'candles_count': payload.get('candles_count'),
                'source_snapshot_id': payload.get('source_snapshot_id'),
                'source_event_id': payload.get('source_event_id'),
                'level_context_ttl_sec': payload.get('level_context_ttl_sec'),
                'level_context_age_sec': payload.get('level_context_age_sec'),
                'level_context_stale': 1 if payload.get('level_context_stale') else 0,
                'level_context_used': 1 if payload.get('level_context_used') else 0,
                'source_level_reaction_id': payload.get('source_level_reaction_id'),
                'source_level_reaction_ts': payload.get('source_level_reaction_ts'),
                'source_event_ts': payload.get('source_event_ts'),
                'raw_level_result': payload.get('raw_level_result'),
                'raw_nearest_level': payload.get('raw_nearest_level'),
                'raw_level_side': payload.get('raw_level_side'),
                'live_level_result': payload.get('live_level_result'),
                'live_nearest_level': payload.get('live_nearest_level'),
                'live_level_side': payload.get('live_level_side'),
                'live_level_type': payload.get('live_level_type'),
                'live_distance_pct': payload.get('live_distance_pct'),
                'live_context_used': 1 if payload.get('live_context_used') else 0,
                'live_context_source': payload.get('live_context_source'),
                'live_context_ts': payload.get('live_context_ts'),
                'live_source_event_id': payload.get('live_source_event_id'),
                'live_source_snapshot_id': payload.get('live_source_snapshot_id'),
                'live_source_snapshot_sequence_id': payload.get('live_source_snapshot_sequence_id'),
                
                'live_support_level': payload.get('live_support_level'),
                'live_support_distance_pct': payload.get('live_support_distance_pct'),
                'live_support_source': payload.get('live_support_source'),
                'live_support_result': payload.get('live_support_result'),
                'live_support_type': payload.get('live_support_type'),
                
                'live_resistance_level': payload.get('live_resistance_level'),
                'live_resistance_distance_pct': payload.get('live_resistance_distance_pct'),
                'live_resistance_source': payload.get('live_resistance_source'),
                'live_resistance_result': payload.get('live_resistance_result'),
                'live_resistance_type': payload.get('live_resistance_type'),
                
                'primary_live_level': payload.get('primary_live_level'),
                'primary_live_side': payload.get('primary_live_side'),
                'primary_live_distance_pct': payload.get('primary_live_distance_pct'),
                'primary_live_source': payload.get('primary_live_source'),
                'primary_live_result': payload.get('primary_live_result'),
                
                'selected_setup_level': payload.get('selected_setup_level'),
                'selected_setup_side': payload.get('selected_setup_side'),
                'selected_setup_level_source': payload.get('selected_setup_level_source'),
                'selected_setup_level_distance_pct': payload.get('selected_setup_level_distance_pct'),
                'selected_setup_level_result': payload.get('selected_setup_level_result'),
                'selected_setup_level_type': payload.get('selected_setup_level_type'),
                'setup_side_required': payload.get('setup_side_required')
            }
            
            # Replace NaN with None
            import math
            for k, v in fields.items():
                if isinstance(v, float) and math.isnan(v):
                    fields[k] = None

            cols = ", ".join(fields.keys())
            places = ", ".join("?" for _ in fields)
            query = f"INSERT INTO manual_trading_snapshots ({cols}) VALUES ({places})"
            
            cursor.execute(query, tuple(fields.values()))
            conn.commit()
"""

    content = content[:match.start()] + prefix + new_body + suffix + content[match.end():]
    
    with open('backend/engine/manual_logger.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
