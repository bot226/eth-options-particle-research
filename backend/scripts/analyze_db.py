"""Analyze MOS Research DB — acceptance SQL queries."""
import sqlite3
import sys
import os

db_path = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\User\Desktop\Project\btc-dashboard\backend\data\mos_research.db'

if not os.path.exists(db_path):
    print(f"DB NOT FOUND: {db_path}")
    sys.exit(1)

conn = sqlite3.connect(db_path, timeout=10)
conn.row_factory = sqlite3.Row

print(f"\n=== Analyzing: {db_path} ===\n")

# Integrity
print("integrity_check:", conn.execute("PRAGMA integrity_check").fetchone()[0])
print("quick_check:", conn.execute("PRAGMA quick_check").fetchone()[0])
tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
print()

# Counts
for t in ['snapshots', 'future_labels', 'events', 'bad_snapshots']:
    try:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{t}: {c}")
    except Exception as e:
        print(f"{t}: N/A ({e})")

# Schema versions
print("\n=== code_version / schema / patch ===")
try:
    for row in conn.execute("SELECT code_version, research_schema_version, engine_patch_version, COUNT(*) as c FROM snapshots GROUP BY code_version, research_schema_version, engine_patch_version"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# schema_version
print("\n=== schema_version distribution ===")
try:
    for row in conn.execute("SELECT schema_version, COUNT(*) as c FROM snapshots GROUP BY schema_version"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# flow pressure scale
print("\n=== synthetic_flow_pressure_scale ===")
try:
    for row in conn.execute("SELECT synthetic_flow_pressure_scale, COUNT(*) as c FROM snapshots GROUP BY synthetic_flow_pressure_scale"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# State distribution clean
print("\n=== current_state (exclude_from_analysis=0) ===")
try:
    for row in conn.execute("SELECT current_state, COUNT(*) as c FROM snapshots WHERE exclude_from_analysis=0 GROUP BY current_state ORDER BY c DESC"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# Execution timing state
print("\n=== execution_timing_state (clean) ===")
try:
    for row in conn.execute("SELECT execution_timing_state, COUNT(*) as c FROM snapshots WHERE exclude_from_analysis=0 GROUP BY execution_timing_state ORDER BY c DESC"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# Combined state
print("\n=== current_state x execution_timing_state (clean) ===")
try:
    for row in conn.execute("SELECT current_state, execution_timing_state, COUNT(*) as c FROM snapshots WHERE exclude_from_analysis=0 GROUP BY current_state, execution_timing_state ORDER BY c DESC LIMIT 20"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# Events distribution
print("\n=== events by type ===")
try:
    for row in conn.execute("SELECT event_type, COUNT(*) as c FROM events GROUP BY event_type ORDER BY c DESC"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# Events with empty payload
print("\n=== events with NULL/empty payload ===")
try:
    c = conn.execute("SELECT COUNT(*) FROM events WHERE event_payload_json IS NULL OR event_payload_json = ''").fetchone()[0]
    print(f"events_without_payload: {c}")
except Exception as e:
    print("err:", e)

# STRUCTURE_UNSTABLE / EXECUTION_WINDOW_OPEN events detail
print("\n=== STRUCTURE_UNSTABLE / EXECUTION_WINDOW_OPEN events detail ===")
try:
    for row in conn.execute("SELECT event_type, previous_execution_timing_state, current_execution_timing_state, COUNT(*) as c FROM events WHERE event_type IN ('STRUCTURE_UNSTABLE','EXECUTION_WINDOW_OPEN') GROUP BY event_type, previous_execution_timing_state, current_execution_timing_state"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# REGIME_CHANGE / PINNING_BREAK detail
print("\n=== REGIME_CHANGE / PINNING_BREAK detail ===")
try:
    for row in conn.execute("SELECT event_type, previous_state, current_state, COUNT(*) as c FROM events WHERE event_type IN ('REGIME_CHANGE','PINNING_BREAK') GROUP BY event_type, previous_state, current_state"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# Score ranges
print("\n=== metric ranges (clean snapshots) ===")
try:
    for col in ['liquidity_void_score', 'signal_cluster_score', 'expansion_probability', 'synthetic_flow_pressure']:
        row = conn.execute(f"SELECT MIN({col}), AVG({col}), MAX({col}) FROM snapshots WHERE exclude_from_analysis=0").fetchone()
        print(f"{col}: min={row[0]:.2f}, avg={row[1]:.2f}, max={row[2]:.2f}")
except Exception as e:
    print("err:", e)

# market_phase_hash
print("\n=== market_phase_hash distinct count ===")
try:
    c = conn.execute("SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots WHERE exclude_from_analysis=0").fetchone()[0]
    print(f"distinct_market_phase_hashes: {c}")
except Exception as e:
    print("err:", e)

# First 10 snapshots — check warmup artifacts
print("\n=== first 10 snapshots (warmup check) ===")
try:
    for row in conn.execute("SELECT snapshot_sequence_id, iv_velocity, synthetic_flow_pressure, current_state, execution_timing_state FROM snapshots ORDER BY snapshot_sequence_id LIMIT 10"):
        print(dict(row))
except Exception as e:
    print("err:", e)

# First 20 events
print("\n=== first 20 events ===")
try:
    for row in conn.execute("SELECT event_type, snapshot_sequence_id, previous_execution_timing_state, current_execution_timing_state FROM events ORDER BY timestamp_utc LIMIT 20"):
        print(dict(row))
except Exception as e:
    print("err:", e)

conn.close()
print("\n=== Done ===")
