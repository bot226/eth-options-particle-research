import re

def main():
    with open('backend/engine/manual_logger.py', 'r', encoding='utf-8') as f:
        content = f.read()

    new_migrations = """            # Live-safe context columns
            ("live_level_result",          "TEXT"),
            ("live_nearest_level",         "REAL"),
            ("live_level_side",            "TEXT"),
            ("live_level_type",            "TEXT"),
            ("live_distance_pct",          "REAL"),
            ("live_context_used",          "INTEGER"),
            ("live_context_source",        "TEXT"),
            ("live_context_ts",            "TEXT"),
            ("live_source_event_id",       "TEXT"),
            ("live_source_snapshot_id",    "TEXT"),
            ("live_source_snapshot_sequence_id", "TEXT"),
            
            ("live_support_level", "REAL"),
            ("live_support_distance_pct", "REAL"),
            ("live_support_source", "TEXT"),
            ("live_support_result", "TEXT"),
            ("live_support_type", "TEXT"),
            
            ("live_resistance_level", "REAL"),
            ("live_resistance_distance_pct", "REAL"),
            ("live_resistance_source", "TEXT"),
            ("live_resistance_result", "TEXT"),
            ("live_resistance_type", "TEXT"),
            
            ("primary_live_level", "REAL"),
            ("primary_live_side", "TEXT"),
            ("primary_live_distance_pct", "REAL"),
            ("primary_live_source", "TEXT"),
            ("primary_live_result", "TEXT"),
            
            ("selected_setup_level", "REAL"),
            ("selected_setup_side", "TEXT"),
            ("selected_setup_level_source", "TEXT"),
            ("selected_setup_level_distance_pct", "REAL"),
            ("selected_setup_level_result", "TEXT"),
            ("selected_setup_level_type", "TEXT"),
            ("setup_side_required", "TEXT"),
        ]"""

    content = re.sub(
        r'# Live-safe context columns.*?\]',
        new_migrations,
        content,
        flags=re.DOTALL
    )

    # Update columns
    content = content.replace(
        "live_source_event_id, live_source_snapshot_id, live_source_snapshot_sequence_id\n                ) VALUES",
        "live_source_event_id, live_source_snapshot_id, live_source_snapshot_sequence_id,\n                    "
        "live_support_level, live_support_distance_pct, live_support_source, live_support_result, live_support_type,\n                    "
        "live_resistance_level, live_resistance_distance_pct, live_resistance_source, live_resistance_result, live_resistance_type,\n                    "
        "primary_live_level, primary_live_side, primary_live_distance_pct, primary_live_source, primary_live_result,\n                    "
        "selected_setup_level, selected_setup_side, selected_setup_level_source, selected_setup_level_distance_pct,\n                    "
        "selected_setup_level_result, selected_setup_level_type, setup_side_required\n                ) VALUES"
    )

    # Update question marks
    content = content.replace(
        "?, ?, ?\n                )\n            ''', (",
        "?, ?, ?,\n                    "
        "?, ?, ?, ?, ?,\n                    "
        "?, ?, ?, ?, ?,\n                    "
        "?, ?, ?, ?, ?,\n                    "
        "?, ?, ?, ?,\n                    "
        "?, ?, ?\n                )\n            ''', ("
    )

    # Update values
    content = content.replace(
        "payload.get('live_source_snapshot_sequence_id')\n            )",
        "payload.get('live_source_snapshot_sequence_id'),\n                "
        "payload.get('live_support_level'),\n                "
        "payload.get('live_support_distance_pct'),\n                "
        "payload.get('live_support_source'),\n                "
        "payload.get('live_support_result'),\n                "
        "payload.get('live_support_type'),\n                "
        "payload.get('live_resistance_level'),\n                "
        "payload.get('live_resistance_distance_pct'),\n                "
        "payload.get('live_resistance_source'),\n                "
        "payload.get('live_resistance_result'),\n                "
        "payload.get('live_resistance_type'),\n                "
        "payload.get('primary_live_level'),\n                "
        "payload.get('primary_live_side'),\n                "
        "payload.get('primary_live_distance_pct'),\n                "
        "payload.get('primary_live_source'),\n                "
        "payload.get('primary_live_result'),\n                "
        "payload.get('selected_setup_level'),\n                "
        "payload.get('selected_setup_side'),\n                "
        "payload.get('selected_setup_level_source'),\n                "
        "payload.get('selected_setup_level_distance_pct'),\n                "
        "payload.get('selected_setup_level_result'),\n                "
        "payload.get('selected_setup_level_type'),\n                "
        "payload.get('setup_side_required')\n            )"
    )

    with open('backend/engine/manual_logger.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
