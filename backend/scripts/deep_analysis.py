"""Deep analysis of MOS Research DB from C:\\Users\\User\\Documents\\76654."""
import sqlite3
import os
import json
import math

DB = r"C:\Users\User\Documents\76654\mos_research.db"

conn = sqlite3.connect(DB, timeout=30)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 75)
print("MOS RESEARCH LAYER — DEEP ANALYSIS REPORT")
print("=" * 75)

# === SECTION 1: Infrastructure ===
print("\n" + "=" * 75)
print("SECTION 1: INFRASTRUCTURE & SCHEMA")
print("=" * 75)

c.execute("PRAGMA integrity_check"); print(f"integrity_check: {c.fetchone()[0]}")
c.execute("PRAGMA quick_check"); print(f"quick_check: {c.fetchone()[0]}")
c.execute("PRAGMA journal_mode"); print(f"journal_mode: {c.fetchone()[0]}")

c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print(f"Tables: {[r[0] for r in c.fetchall()]}")

c.execute("PRAGMA table_info(snapshots)")
cols = [info[1] for info in c.fetchall()]
required = ["snapshot_id","code_version","research_schema_version","engine_patch_version",
            "synthetic_flow_pressure_scale","data_quality_reason","exclude_from_analysis",
            "exclude_reason","schema_version","market_phase_hash","signal_cluster_score",
            "execution_timing_state","dealer_hedging_pressure"]
missing = [col for col in required if col not in cols]
print(f"Total columns: {len(cols)}")
print(f"Schema valid: {len(missing)==0}")
if missing: print(f"MISSING: {missing}")

# Counts
c.execute("SELECT COUNT(*) FROM snapshots"); total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0"); valid = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=1"); excluded = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events"); events = c.fetchone()[0]
try: c.execute("SELECT COUNT(*) FROM future_labels"); fl = c.fetchone()[0]
except: fl = 0
try: c.execute("SELECT COUNT(*) FROM bad_snapshots"); bad = c.fetchone()[0]
except: bad = 0
print(f"\nSnapshots: {total} (valid={valid}, excluded={excluded})")
print(f"Events: {events} | Future labels: {fl} | Bad: {bad}")

# Time range
c.execute("SELECT MIN(timestamp_utc), MAX(timestamp_utc) FROM snapshots WHERE exclude_from_analysis=0")
r = c.fetchone()
if r[0] and r[1]:
    dur_h = (r[1]-r[0])/3600
    print(f"History: {dur_h:.1f} hours ({dur_h/24:.1f} days)")

# === SECTION 2: Version Contract ===
print("\n" + "=" * 75)
print("SECTION 2: VERSION CONTRACT")
print("=" * 75)

c.execute("SELECT schema_version, COUNT(*) FROM snapshots GROUP BY schema_version")
for r in c.fetchall(): print(f"  schema_version={r[0]} : {r[1]}")

c.execute("SELECT code_version, research_schema_version, engine_patch_version, COUNT(*) FROM snapshots GROUP BY code_version, research_schema_version, engine_patch_version")
for r in c.fetchall(): print(f"  {r[0]} | {r[1]} | {r[2]} : {r[3]}")

c.execute("SELECT synthetic_flow_pressure_scale, COUNT(*) FROM snapshots GROUP BY synthetic_flow_pressure_scale")
for r in c.fetchall(): print(f"  flow_scale={r[0]} : {r[1]}")

c.execute("""SELECT COUNT(*) FROM snapshots WHERE code_version IS NULL OR research_schema_version IS NULL 
  OR engine_patch_version IS NULL OR snapshot_id IS NULL OR synthetic_flow_pressure_scale IS NULL""")
print(f"\nNULL version fields: {c.fetchone()[0]}")

# === SECTION 3: State Distributions ===
print("\n" + "=" * 75)
print("SECTION 3: STATE DISTRIBUTIONS")
print("=" * 75)

for col, label in [("current_state","Current State"), ("execution_timing_state","Execution Timing"),
                    ("dealer_hedging_pressure","Dealer Hedging"), ("gamma_slope_state","Gamma Slope"),
                    ("gamma_acceleration_state","Gamma Accel"), ("data_quality","Data Quality"),
                    ("breakout_window","Breakout Window"), ("gamma_regime","Gamma Regime"),
                    ("term_structure_state","Term Structure")]:
    try:
        c.execute(f"SELECT {col}, COUNT(*) as cnt FROM snapshots WHERE exclude_from_analysis=0 GROUP BY {col} ORDER BY cnt DESC")
        rows = c.fetchall()
        pcts = [(r[0], r[1], r[1]/valid*100) for r in rows]
        print(f"\n  {label}:")
        for name, cnt, pct in pcts:
            bar = "#" * int(pct/2)
            print(f"    {str(name):30s} : {cnt:5d} ({pct:5.1f}%) {bar}")
    except Exception as e: print(f"\n  {label}: ERROR {e}")

# === SECTION 4: Signal Ranges ===
print("\n" + "=" * 75)
print("SECTION 4: SIGNAL RANGES (exclude_from_analysis=0)")
print("=" * 75)

for col, label in [("signal_cluster_score","Cluster Score"),("expansion_probability","Expansion Prob"),
                    ("liquidity_void_score","Void Score"),("iv_velocity","IV Velocity"),
                    ("synthetic_flow_pressure","Flow Pressure"),("global_confidence","Global Confidence"),
                    ("gamma_slope","Gamma Slope Val"),("gamma_acceleration","Gamma Accel Val"),
                    ("net_gex","Net GEX"),("compression_failure_risk","Compression Fail Risk"),
                    ("spot_price","Spot Price"),("atm_iv","ATM IV"),("oi_total","OI Total"),
                    ("volume_total","Volume Total"),("regime_duration_sec","Regime Duration")]:
    try:
        c.execute(f"SELECT MIN({col}), AVG({col}), MAX({col}), COUNT(DISTINCT ROUND({col},1)) FROM snapshots WHERE exclude_from_analysis=0")
        r = c.fetchone()
        if r[0] is not None:
            print(f"  {label:22s}: min={r[0]:12.2f}  avg={r[1]:12.2f}  max={r[2]:12.2f}  distinct≈{r[3]}")
    except Exception as e: print(f"  {label:22s}: ERROR {e}")

# === SECTION 5: Bucket Distributions ===
print("\n" + "=" * 75)
print("SECTION 5: BUCKET DISTRIBUTIONS")
print("=" * 75)

# Cluster Score
print("\n  Signal Cluster Score:")
for lo, hi in [(0,15),(15,30),(30,45),(45,60),(60,75),(75,100)]:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND signal_cluster_score>={lo} AND signal_cluster_score<{hi}")
    cnt = c.fetchone()[0]
    pct = cnt/valid*100 if valid else 0
    print(f"    {lo:3d}-{hi:3d}: {cnt:5d} ({pct:5.1f}%) {'#'*int(pct/2)}")

# Expansion Prob
print("\n  Expansion Probability:")
for lo, hi in [(0,15),(15,30),(30,45),(45,60),(60,75),(75,100)]:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND expansion_probability>={lo} AND expansion_probability<{hi}")
    cnt = c.fetchone()[0]
    pct = cnt/valid*100 if valid else 0
    print(f"    {lo:3d}-{hi:3d}: {cnt:5d} ({pct:5.1f}%) {'#'*int(pct/2)}")

# Void Score
print("\n  Liquidity Void Score:")
for lo, hi in [(0,10),(10,20),(20,30),(30,40),(40,50),(50,60),(60,80),(80,100)]:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND liquidity_void_score>={lo} AND liquidity_void_score<{hi}")
    cnt = c.fetchone()[0]
    pct = cnt/valid*100 if valid else 0
    print(f"    {lo:3d}-{hi:3d}: {cnt:5d} ({pct:5.1f}%) {'#'*int(pct/2)}")

# Flow Pressure directional
print("\n  Flow Pressure (directional):")
flow_buckets = [("strong_sell",-100,-60),("mod_sell",-60,-30),("weak_sell",-30,-10),
                ("neutral",-10,10),("weak_buy",10,30),("mod_buy",30,60),("strong_buy",60,100)]
for name, lo, hi in flow_buckets:
    c.execute(f"SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND synthetic_flow_pressure>={lo} AND synthetic_flow_pressure<{hi}")
    cnt = c.fetchone()[0]
    pct = cnt/valid*100 if valid else 0
    print(f"    {name:15s}: {cnt:5d} ({pct:5.1f}%) {'#'*int(pct/2)}")

# === SECTION 6: Stickiness Analysis ===
print("\n" + "=" * 75)
print("SECTION 6: STICKINESS / MONOTONICITY ANALYSIS")
print("=" * 75)

for col, label in [("current_state","Current State"),("execution_timing_state","Execution Timing"),
                    ("dealer_hedging_pressure","Dealer Hedging"),("gamma_slope_state","Gamma Slope"),
                    ("gamma_acceleration_state","Gamma Accel")]:
    c.execute(f"SELECT {col}, MIN(timestamp_utc), MAX(timestamp_utc), COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY {col}")
    rows = c.fetchall()
    if rows:
        # Count transitions
        c.execute(f"SELECT {col} FROM snapshots WHERE exclude_from_analysis=0 ORDER BY timestamp_utc")
        values = [r[0] for r in c.fetchall()]
        transitions = sum(1 for i in range(1,len(values)) if values[i]!=values[i-1])
        dominant = max(rows, key=lambda r: r[3])
        dom_pct = dominant[3]/valid*100
        print(f"\n  {label}:")
        print(f"    Distinct values: {len(rows)}")
        print(f"    Transitions: {transitions}")
        print(f"    Dominant: {dominant[0]} ({dom_pct:.1f}%)")
        if dom_pct > 95:
            print(f"    ⚠️  STICKY: {dominant[0]} dominates at {dom_pct:.1f}%")
        elif dom_pct > 80:
            print(f"    ⚠️  HIGH DOMINANCE: {dominant[0]} at {dom_pct:.1f}%")

# === SECTION 7: Events Analysis ===
print("\n" + "=" * 75)
print("SECTION 7: EVENTS")
print("=" * 75)

c.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY COUNT(*) DESC")
for r in c.fetchall(): print(f"  {r[0]:30s} : {r[1]}")

# Check for spam
c.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type HAVING COUNT(*) > 20 ORDER BY COUNT(*) DESC")
spam = c.fetchall()
if spam:
    print("\n  ⚠️  Potential event spam:")
    for r in spam: print(f"    {r[0]} : {r[1]}")

# Events with NULL seq
c.execute("SELECT COUNT(*) FROM events WHERE snapshot_sequence_id IS NULL OR snapshot_sequence_id <= 0")
print(f"\n  Events with NULL/0 sequence_id: {c.fetchone()[0]}")

# Forbidden event types
c.execute("SELECT event_type, COUNT(*) FROM events WHERE event_type IN ('EXECUTION_WINDOW','STRUCTURE UNSTABLE') GROUP BY event_type")
forbidden = c.fetchall()
if forbidden:
    print("  ⚠️  FORBIDDEN event types found:")
    for r in forbidden: print(f"    {r[0]} : {r[1]}")
else:
    print("  ✓ No forbidden event types")

# === SECTION 8: Future Labels ===
print("\n" + "=" * 75)
print("SECTION 8: FUTURE LABELS")
print("=" * 75)

try:
    c.execute("""SELECT COUNT(future_return_5m), COUNT(future_return_15m), COUNT(future_return_30m),
        COUNT(future_max_up_30m), COUNT(future_max_down_30m), COUNT(future_realized_vol_30m),
        COUNT(future_range_30m), COUNT(future_breakout_strength) FROM future_labels""")
    r = c.fetchone()
    labels = ["return_5m","return_15m","return_30m","max_up_30m","max_down_30m","real_vol_30m","range_30m","breakout_str"]
    for i, lab in enumerate(labels):
        print(f"  {lab:15s}: {r[i]} filled")
    
    # Label ranges
    for col, label in [("future_return_5m","Return 5m"),("future_return_15m","Return 15m"),
                        ("future_return_30m","Return 30m"),("future_realized_vol_30m","RealVol 30m"),
                        ("future_breakout_strength","Breakout Str")]:
        c.execute(f"SELECT MIN({col}), AVG({col}), MAX({col}) FROM future_labels WHERE {col} IS NOT NULL")
        r = c.fetchone()
        if r[0] is not None:
            print(f"  {label:15s}: min={r[0]:.4f}  avg={r[1]:.4f}  max={r[2]:.4f}")
except Exception as e:
    print(f"  ERROR: {e}")

# === SECTION 9: Exclusions ===
print("\n" + "=" * 75)
print("SECTION 9: EXCLUSIONS & DATA QUALITY")
print("=" * 75)

print(f"\nExcluded: {excluded} / {total}")
if excluded > 0:
    c.execute("SELECT exclude_reason, COUNT(*) FROM snapshots WHERE exclude_from_analysis=1 GROUP BY exclude_reason ORDER BY COUNT(*) DESC")
    for r in c.fetchall(): print(f"  {r[0]:40s} : {r[1]}")

c.execute("SELECT data_quality_reason, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 AND data_quality_reason != '' GROUP BY data_quality_reason ORDER BY COUNT(*) DESC LIMIT 10")
print("\nData quality reasons (top 10):")
for r in c.fetchall(): print(f"  {r[0]:50s} : {r[1]}")

# Zero checks
c.execute("SELECT COUNT(*) FROM snapshots WHERE spot_price <= 0"); print(f"\nspot_price<=0: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots WHERE oi_total <= 0"); print(f"oi_total<=0: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots WHERE atm_iv <= 0"); print(f"atm_iv<=0: {c.fetchone()[0]}")
c.execute("SELECT COUNT(*) FROM snapshots WHERE gamma_slope = 0 AND gamma_acceleration = 0"); print(f"gamma both=0: {c.fetchone()[0]}")

# === SECTION 10: Phase Hash ===
print("\n" + "=" * 75)
print("SECTION 10: MARKET PHASE HASH")
print("=" * 75)

c.execute("SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots WHERE exclude_from_analysis=0")
hashes = c.fetchone()[0]
print(f"Distinct hashes: {hashes}")

c.execute("SELECT market_phase_hash, COUNT(*) FROM snapshots WHERE exclude_from_analysis=0 GROUP BY market_phase_hash ORDER BY COUNT(*) DESC LIMIT 10")
print("Top 10 hashes:")
for r in c.fetchall():
    pct = r[1]/valid*100
    print(f"  {r[0]:10s} : {r[1]:5d} ({pct:5.1f}%)")

# === SECTION 11: Temporal Stability ===
print("\n" + "=" * 75)
print("SECTION 11: TEMPORAL STABILITY (hourly averages)")
print("=" * 75)

c.execute("""
    SELECT CAST((timestamp_utc / 3600) AS INT) * 3600 as hour_ts,
           COUNT(*) as cnt,
           AVG(signal_cluster_score) as avg_cluster,
           AVG(expansion_probability) as avg_exp,
           AVG(liquidity_void_score) as avg_void,
           AVG(synthetic_flow_pressure) as avg_flow
    FROM snapshots WHERE exclude_from_analysis=0
    GROUP BY hour_ts ORDER BY hour_ts
""")
rows = c.fetchall()
print(f"{'Hour':>5s} | {'N':>4s} | {'Cluster':>8s} | {'ExpProb':>8s} | {'Void':>8s} | {'Flow':>8s}")
print("-" * 55)
for i, r in enumerate(rows):
    print(f"{i:5d} | {r[1]:4d} | {r[2]:8.1f} | {r[3]:8.1f} | {r[4]:8.1f} | {r[5]:8.1f}")

conn.close()
print("\n" + "=" * 75)
print("ANALYSIS COMPLETE")
print("=" * 75)
