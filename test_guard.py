import sqlite3
import os
from datetime import datetime

db_path = "c:\\Users\\User\\Desktop\\Project\\eth-gpt-codex-v11-targeted-fix\\backend\\data\\mos_research.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Test the query
now_iso = "2026-06-10T07:03:45.000000Z"
cutoff_iso = "2026-06-10T06:53:45.000000Z"

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

print(f"Found {len(rows)} rows between {cutoff_iso} and {now_iso}")
for r in rows:
    print(r)
