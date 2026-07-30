"""v6 Acceptance — covers all 7 fixes."""
import sqlite3, os
DB = r"c:\Users\User\Desktop\Project\btc-dashboard\backend\data\mos_research.db"

conn = sqlite3.connect(DB, timeout=30)
c = conn.cursor()

c.execute("PRAGMA integrity_check"); print(f"integrity_check: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots"); total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0"); valid = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events"); ev = c.fetchone()[0]
try:
    c.execute("SELECT COUNT(*) FROM future_labels"); fl = c.fetchone()[0]
except: fl = 0
print(f"Snapshots: {total} (valid={valid}) | Events: {ev} | Future labels: {fl}")

# VERSION
c.execute("SELECT DISTINCT code_version FROM snapshots")
print(f"Code versions: {[r[0] for r in c.fetchall()]}")
c.execute("SELECT DISTINCT schema_version FROM snapshots")
print(f"Schema versions: {[r[0] for r in c.fetchall()]}")

# CLUSTER SCORE - distinct values (Fix 2)
c.execute("SELECT COUNT(DISTINCT ROUND(signal_cluster_score, 0)) FROM snapshots WHERE exclude_from_analysis=0")
distinct_cluster = c.fetchone()[0]
c.execute("SELECT MIN(signal_cluster_score), AVG(signal_cluster_score), MAX(signal_cluster_score) FROM snapshots WHERE exclude_from_analysis=0")
r = c.fetchone()
print(f"\n--- CLUSTER SCORE (Fix 2: lerp) ---")
print(f"Distinct values: {distinct_cluster} (target: >25)")
print(f"Range: min={r[0]:.1f}  avg={r[1]:.1f}  max={r[2]:.1f}")

# VOID SCORE (Fix 1)
c.execute("SELECT MIN(liquidity_void_score), AVG(liquidity_void_score), MAX(liquidity_void_score) FROM snapshots WHERE exclude_from_analysis=0")
r = c.fetchone()
void_range = r[2] - r[0] if r[0] is not None else 0
print(f"\n--- VOID SCORE (Fix 1: tiered proximity) ---")
print(f"Range: min={r[0]:.1f}  avg={r[1]:.1f}  max={r[2]:.1f}  spread={void_range:.1f}")

# Void buckets
for lo, hi in [(0,20),(20,30),(30,40),(40,50),(50,60),(60,80),(80,100)]:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND liquidity_void_score>={lo} AND liquidity_void_score<{hi}")
    cnt = c.fetchone()[0]
    pct = cnt/valid*100 if valid else 0
    print(f"  {lo:3d}-{hi:3d}: {cnt:4d} ({pct:5.1f}%)")

# STATE DISTRIBUTION (Fix 3: no circular dep)
print(f"\n--- STATE MACHINE (Fix 3: no circular dep) ---")
c.execute("SELECT current_state, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY current_state ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:25s}: {r[1]}")

# Execution timing vs state correlation (should be different)
print(f"\n--- EXECUTION vs STATE CORRELATION ---")
c.execute("SELECT current_state, execution_timing_state, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY current_state, execution_timing_state ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:20s} x {r[1]:25s}: {r[2]}")

# EXECUTION TIMING distribution (Fix 5)
print(f"\n--- EXECUTION TIMING (Fix 5: calibrated thresholds) ---")
c.execute("SELECT execution_timing_state, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY execution_timing_state ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:25s}: {r[1]}")

# REGIME_CHANGE events (Fix 4)
c.execute("SELECT COUNT(*) FROM events WHERE event_type='REGIME_CHANGE'")
regime_changes = c.fetchone()[0]
print(f"\n--- REGIME_CHANGE (Fix 4: cooldown 300s) ---")
print(f"REGIME_CHANGE count: {regime_changes}")

# All events
c.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC")
print("All events:")
for r in c.fetchall(): print(f"  {r[0]:30s}: {r[1]}")

# State transitions count
c.execute("SELECT current_state FROM snapshots WHERE exclude_from_analysis=0 ORDER BY timestamp_utc")
states = [r[0] for r in c.fetchall()]
transitions = sum(1 for i in range(1,len(states)) if states[i]!=states[i-1])
print(f"State transitions: {transitions}")

# FUTURE LABELS (Fix 6: snapshot_id/sequence_id)
print(f"\n--- FUTURE LABELS (Fix 6: snapshot link) ---")
c.execute("PRAGMA table_info(future_labels)")
fl_cols = [info[1] for info in c.fetchall()]
print(f"Columns: {fl_cols}")
has_snap_id = "snapshot_id" in fl_cols
has_snap_seq = "snapshot_sequence_id" in fl_cols
print(f"snapshot_id column: {'YES' if has_snap_id else 'NO'}")
print(f"snapshot_sequence_id column: {'YES' if has_snap_seq else 'NO'}")

# DEALER HEDGING
print(f"\n--- DEALER HEDGING ---")
c.execute("SELECT dealer_hedging_pressure, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY dealer_hedging_pressure ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:10s}: {r[1]}")

# FLOW PRESSURE
print(f"\n--- FLOW PRESSURE ---")
c.execute("SELECT MIN(synthetic_flow_pressure), AVG(synthetic_flow_pressure), MAX(synthetic_flow_pressure) FROM snapshots WHERE exclude_from_analysis=0")
r = c.fetchone()
if r[0] is not None:
    print(f"Range: min={r[0]:.1f}  avg={r[1]:.1f}  max={r[2]:.1f}")

# EXPANSION PROB
c.execute("SELECT MIN(expansion_probability), AVG(expansion_probability), MAX(expansion_probability) FROM snapshots WHERE exclude_from_analysis=0")
r = c.fetchone()
print(f"\n--- EXPANSION PROBABILITY ---")
if r[0] is not None:
    print(f"Range: min={r[0]:.1f}  avg={r[1]:.1f}  max={r[2]:.1f}")

# Distinct phase hashes
c.execute("SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots WHERE exclude_from_analysis=0")
print(f"\nDistinct phase hashes: {c.fetchone()[0]}")

conn.close()
print("\n=== ACCEPTANCE COMPLETE ===")
