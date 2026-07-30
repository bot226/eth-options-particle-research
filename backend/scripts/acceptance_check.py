"""Full Acceptance SQL — MOS Research Layer v5 per specification."""
import sqlite3
import os
import json

db = os.path.join(os.path.dirname(__file__), '..', 'data', 'mos_research.db')
if not os.path.exists(db):
    print(f"ERROR: DB not found at {db}")
    exit(1)

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 70)
print("MOS RESEARCH LAYER v5 — FULL ACCEPTANCE REPORT")
print("=" * 70)

# PRAGMA checks
c.execute("PRAGMA integrity_check"); print(f"\nintegrity_check: {c.fetchone()[0]}")
c.execute("PRAGMA quick_check"); print(f"quick_check: {c.fetchone()[0]}")

# Tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(f"\nTables: {[r[0] for r in c.fetchall()]}")

# Schema columns
c.execute("PRAGMA table_info(snapshots)")
cols = [info[1] for info in c.fetchall()]
required = ["snapshot_id","code_version","research_schema_version","engine_patch_version",
            "synthetic_flow_pressure_scale","data_quality_reason","exclude_from_analysis",
            "exclude_reason","schema_version","market_phase_hash","signal_cluster_score",
            "execution_timing_state","dealer_hedging_pressure"]
missing = [col for col in required if col not in cols]
print(f"\nSchema valid: {len(missing)==0}")
if missing: print(f"MISSING COLUMNS: {missing}")

# Counts
c.execute("SELECT COUNT(*) FROM snapshots"); total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events"); events = c.fetchone()[0]
try: c.execute("SELECT COUNT(*) FROM future_labels"); fl = c.fetchone()[0]
except: fl = 0
try: c.execute("SELECT COUNT(*) FROM bad_snapshots"); bad = c.fetchone()[0]
except: bad = 0
print(f"\nSnapshots: {total} | Events: {events} | Future labels: {fl} | Bad: {bad}")

# Version distribution
print("\n--- VERSION DISTRIBUTION ---")
c.execute("SELECT schema_version, COUNT(*) FROM snapshots GROUP BY schema_version")
for r in c.fetchall(): print(f"  schema_version={r[0]} : {r[1]}")

c.execute("SELECT code_version, research_schema_version, engine_patch_version, COUNT(*) FROM snapshots GROUP BY code_version, research_schema_version, engine_patch_version")
for r in c.fetchall(): print(f"  {r[0]} | {r[1]} | {r[2]} : {r[3]}")

c.execute("SELECT synthetic_flow_pressure_scale, COUNT(*) FROM snapshots GROUP BY synthetic_flow_pressure_scale")
for r in c.fetchall(): print(f"  flow_scale={r[0]} : {r[1]}")

# NULL check
c.execute("""SELECT COUNT(*) FROM snapshots WHERE code_version IS NULL OR research_schema_version IS NULL 
  OR engine_patch_version IS NULL OR snapshot_id IS NULL OR synthetic_flow_pressure_scale IS NULL""")
nulls = c.fetchone()[0]
print(f"\nNULL version fields: {nulls} (must be 0)")

# State distributions
print("\n--- STATE DISTRIBUTIONS ---")
for col, label in [("current_state","Current State"), ("execution_timing_state","Execution Timing"),
                    ("dealer_hedging_pressure","Dealer Hedging"), ("gamma_slope_state","Gamma Slope"),
                    ("gamma_acceleration_state","Gamma Accel"), ("data_quality","Data Quality"),
                    ("active_sources","Active Sources")]:
    try:
        c.execute(f"SELECT {col}, COUNT(*) FROM snapshots GROUP BY {col} ORDER BY COUNT(*) DESC")
        rows = c.fetchall()
        print(f"\n  {label}:")
        for r in rows: print(f"    {str(r[0]):30s} : {r[1]}")
    except: print(f"\n  {label}: ERROR")

# Signal ranges
print("\n--- SIGNAL RANGES ---")
for col, label in [("signal_cluster_score","Cluster Score"),("expansion_probability","Expansion Prob"),
                    ("liquidity_void_score","Void Score"),("iv_velocity","IV Velocity"),
                    ("synthetic_flow_pressure","Flow Pressure")]:
    try:
        c.execute(f"SELECT MIN({col}), AVG({col}), MAX({col}) FROM snapshots WHERE exclude_from_analysis=0")
        r = c.fetchone()
        print(f"  {label:20s}: min={r[0]:.1f}  avg={r[1]:.1f}  max={r[2]:.1f}")
    except Exception as e: print(f"  {label:20s}: ERROR {e}")

# Exclusions
c.execute("SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis = 1"); exc = c.fetchone()[0]
print(f"\nExcluded snapshots: {exc}")
if exc > 0:
    c.execute("SELECT exclude_reason, COUNT(*) FROM snapshots WHERE exclude_from_analysis=1 GROUP BY exclude_reason ORDER BY COUNT(*) DESC")
    for r in c.fetchall(): print(f"  {r[0]} : {r[1]}")

# Zero checks
c.execute("SELECT COUNT(*) FROM snapshots WHERE spot_price <= 0"); print(f"\nspot_price<=0: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots WHERE oi_total <= 0"); print(f"oi_total<=0: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots WHERE atm_iv <= 0"); print(f"atm_iv<=0: {c.fetchone()[0]}")

# Bucket distributions
print("\n--- BUCKET DISTRIBUTIONS ---")
for col, label, buckets in [
    ("liquidity_void_score", "Void Score", [(0,20),(20,40),(40,60),(60,80),(80,100)]),
    ("signal_cluster_score", "Cluster Score", [(0,25),(25,50),(50,75),(75,100)]),
    ("expansion_probability", "Expansion Prob", [(0,25),(25,50),(50,75),(75,100)]),
]:
    print(f"\n  {label}:")
    for lo, hi in buckets:
        c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND {col}>={lo} AND {col}<{hi}")
        print(f"    {lo}-{hi}: {c.fetchone()[0]}")

# Flow directional buckets
print(f"\n  Flow Pressure (directional):")
flow_buckets = [("strong_sell",-100,-75),("mod_sell",-75,-35),("weak_sell",-35,-10),
                ("neutral",-10,10),("weak_buy",10,35),("mod_buy",35,75),("strong_buy",75,100)]
for name, lo, hi in flow_buckets:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND synthetic_flow_pressure>={lo} AND synthetic_flow_pressure<{hi}")
    print(f"    {name:15s}: {c.fetchone()[0]}")

# Events
print("\n--- EVENTS ---")
c.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:30s} : {r[1]}")

# Distinct hashes
c.execute("SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots WHERE exclude_from_analysis=0")
print(f"\nDistinct phase hashes: {c.fetchone()[0]}")

conn.close()
print("\n" + "=" * 70)
print("ACCEPTANCE COMPLETE")
print("=" * 70)
