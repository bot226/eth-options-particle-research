"""30-minute validation script for MOS Research Layer."""
import sqlite3
import time

conn = sqlite3.connect('data/mos_research.db')
c = conn.cursor()

c.execute('SELECT COUNT(*) FROM snapshots')
total = c.fetchone()[0]

print('='*60)
print('MOS RESEARCH LAYER — 30 MINUTE VALIDATION')
print('='*60)

print()
print('=== GENERAL STATS ===')
print('snapshots:', total)
c.execute('SELECT COUNT(*) FROM events')
print('events:', c.fetchone()[0])
c.execute('SELECT COUNT(*) FROM future_labels')
print('future_labels:', c.fetchone()[0])
c.execute('SELECT COUNT(*) FROM bad_snapshots')
print('bad_snapshots:', c.fetchone()[0])
c.execute('SELECT MIN(timestamp_utc), MAX(timestamp_utc) FROM snapshots')
mn, mx = c.fetchone()
if mn and mx:
    print(f'period: {(mx-mn)/60:.1f} min')

print()
print('=== VERSION CHECK ===')
c.execute('SELECT code_version, research_schema_version, engine_patch_version, COUNT(*) FROM snapshots GROUP BY code_version, research_schema_version, engine_patch_version')
for r in c.fetchall():
    print(r)

if total == 0:
    print("NO DATA - exiting")
    exit()

print()
print('=== CURRENT_STATE ===')
c.execute('SELECT current_state, COUNT(*) FROM snapshots GROUP BY current_state ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[1]*100/total:.1f}%)')

print()
print('=== EXECUTION_TIMING_STATE ===')
c.execute('SELECT execution_timing_state, COUNT(*) FROM snapshots GROUP BY execution_timing_state ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[1]*100/total:.1f}%)')

print()
print('=== GAMMA_SLOPE_STATE ===')
c.execute('SELECT gamma_slope_state, COUNT(*) FROM snapshots GROUP BY gamma_slope_state ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[1]*100/total:.1f}%)')

print()
print('=== GAMMA_ACCELERATION_STATE ===')
c.execute('SELECT gamma_acceleration_state, COUNT(*) FROM snapshots GROUP BY gamma_acceleration_state ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[1]*100/total:.1f}%)')

print()
print('=== DATA QUALITY ===')
c.execute('SELECT data_quality, COUNT(*) FROM snapshots GROUP BY data_quality ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[1]*100/total:.1f}%)')

print()
print('=== DATA QUALITY REASON ===')
c.execute('SELECT data_quality_reason, COUNT(*) FROM snapshots GROUP BY data_quality_reason ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  "{r[0]}": {r[1]}')

print()
print('=== EXCLUDE_FROM_ANALYSIS ===')
c.execute('SELECT exclude_from_analysis, exclude_reason, COUNT(*) FROM snapshots GROUP BY exclude_from_analysis, exclude_reason ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  excluded={r[0]} reason="{r[1]}" count={r[2]}')

print()
print('=== LIQUIDITY_VOID_SCORE DISTRIBUTION ===')
c.execute("""SELECT
    CASE
        WHEN liquidity_void_score < 10 THEN '0-9'
        WHEN liquidity_void_score < 20 THEN '10-19'
        WHEN liquidity_void_score < 30 THEN '20-29'
        WHEN liquidity_void_score < 40 THEN '30-39'
        WHEN liquidity_void_score < 50 THEN '40-49'
        WHEN liquidity_void_score < 60 THEN '50-59'
        WHEN liquidity_void_score < 70 THEN '60-69'
        WHEN liquidity_void_score < 80 THEN '70-79'
        ELSE '80+'
    END as band, COUNT(*) FROM snapshots GROUP BY band ORDER BY band""")
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')

print()
print('=== SIGNAL_CLUSTER_SCORE DISTRIBUTION ===')
c.execute("""SELECT
    CASE
        WHEN signal_cluster_score < 10 THEN '0-9'
        WHEN signal_cluster_score < 20 THEN '10-19'
        WHEN signal_cluster_score < 30 THEN '20-29'
        WHEN signal_cluster_score < 40 THEN '30-39'
        WHEN signal_cluster_score < 50 THEN '40-49'
        WHEN signal_cluster_score < 60 THEN '50-59'
        WHEN signal_cluster_score < 70 THEN '60-69'
        ELSE '70+'
    END as band, COUNT(*) FROM snapshots GROUP BY band ORDER BY band""")
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')

print()
print('=== EVENTS BY TYPE ===')
c.execute('SELECT event_type, severity, COUNT(*) FROM events GROUP BY event_type, severity ORDER BY COUNT(*) DESC')
for r in c.fetchall():
    print(f'  {r[0]} [{r[1]}]: {r[2]}')

print()
print('=== EVENT seq_id validation ===')
c.execute('SELECT COUNT(*) FROM events WHERE snapshot_sequence_id <= 0 OR snapshot_sequence_id IS NULL')
print('events with bad seq_id:', c.fetchone()[0])

print()
print('=== ZERO VALUES (non-excluded) ===')
c.execute("""SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis = 0 AND (
    spot_price = 0 OR oi_total = 0 OR atm_iv = 0 OR volume_total = 0)""")
print('zero-value rows:', c.fetchone()[0])

print()
print('=== MARKET_PHASE_HASH ===')
c.execute('SELECT COUNT(DISTINCT market_phase_hash) FROM snapshots')
print('unique hashes:', c.fetchone()[0])

print()
print('=== KEY METRICS RANGES ===')
metrics = ['spot_price','expansion_probability','synthetic_flow_pressure','iv_velocity','global_confidence']
for m in metrics:
    c.execute(f'SELECT ROUND(MIN({m}),2), ROUND(AVG({m}),2), ROUND(MAX({m}),2) FROM snapshots')
    r = c.fetchone()
    print(f'  {m}: min={r[0]}  avg={r[1]}  max={r[2]}')

print()
print('=== ACTIVE_SOURCES ===')
c.execute('SELECT active_sources, COUNT(*) FROM snapshots GROUP BY active_sources')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')

print()
print('=== DEALER HEDGING ===')
c.execute('SELECT dealer_hedging_pressure, COUNT(*) FROM snapshots GROUP BY dealer_hedging_pressure')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')

print()
print('='*60)
print('VALIDATION COMPLETE')
print('='*60)

conn.close()
