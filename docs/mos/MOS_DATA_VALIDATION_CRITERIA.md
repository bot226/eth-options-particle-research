# MOS_DATA_VALIDATION_CRITERIA.md

## Goal

Validate whether `mos_research.db` and `history.db` are usable for replay analysis, signal checking, probabilistic analysis and future Market Memory Engine.

MOS does not predict price. MOS analyzes structure, regimes, dealer positioning, volatility, liquidity, flow pressure and execution environment quality.

## Required files

For Research Layer:

```text
mos_research.db
mos_research.db-wal
mos_research.db-shm
```

Always analyze the latest uploaded/current files.

If SQLite is in WAL mode and only `.db` is copied, fresh data may be missing.

For slow structural archive:

```text
history.db
history.db-wal
history.db-shm
```

Particle Logic v2 adds the optional, backward-compatible table:

```text
option_contract_snapshots
```

New v2 collection runs should populate it every five minutes. Older archives
without this table remain valid for v1-compatible replay.

```sql
SELECT exchange, COUNT(*) AS rows,
       SUM(mark_iv IS NOT NULL AND mark_iv > 0) AS valid_iv,
       SUM(volume_24h IS NOT NULL) AS valid_volume,
       SUM(delta IS NOT NULL AND gamma IS NOT NULL
           AND vega IS NOT NULL AND theta IS NOT NULL) AS valid_greeks
FROM option_contract_snapshots
GROUP BY exchange;
```

Particle Logic v3 replay must also report filter suppression without changing
raw contract counts:

```sql
SELECT metric_name,
       SUM(observed_changes), SUM(material_changes), SUM(emitted_changes),
       SUM(suppressed_below_threshold), SUM(suppressed_by_cap)
FROM particle_filter_audit
GROUP BY metric_name;
```

For v61 Deribit collection, the smoke test must report after the REST ticker
bootstrap reaches safe research-core coverage:

```text
status = ok
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
collector_reused = true
deribit_data_transport = websocket_incremental_ticker_cache+rest_ticker_bootstrap
deribit_ticker_bootstrap_transport = rest_public_ticker
deribit_instrument_cache_count > 0
deribit_ws_subscribed_tickers > 0
deribit_ws_fresh_tickers > 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_instruments_count > 0
deribit_ws_core_fresh_tickers / deribit_ws_core_instruments_count >= 0.7
deribit_ws_core_full_tickers / deribit_ws_core_instruments_count >= 0.7
deribit_ws_bootstrap_state = running or complete
deribit_ws_bootstrap_success_count > 0
deribit_ws_bootstrap_cycle_target_count <= deribit_ws_bootstrap_target_count
```

`deribit_ws_cache_coverage_ratio` is the readiness ratio for the balanced core.
`deribit_ws_chain_coverage_ratio` separately reports how much of the full
discovered chain is fresh. Full-chain bootstrap may continue after the core is
usable and must not block MOS polling.

After the next five-minute structural snapshot, verify that source-level rows
exist for both exchanges:

```sql
SELECT exchange, COUNT(*) AS rows,
       COUNT(DISTINCT snapshot_id) AS snapshots,
       SUM(mark_iv IS NOT NULL AND mark_iv > 0) AS valid_iv,
       SUM(delta IS NOT NULL AND gamma IS NOT NULL
           AND vega IS NOT NULL AND theta IS NOT NULL) AS valid_greeks
FROM option_contract_snapshots
GROUP BY exchange;
```

But for Research Layer, main requirement is `mos_research.db + wal + shm`.

## Required runtime version columns

Snapshots must include:

```text
code_version
research_schema_version
engine_patch_version
```

Check:

```sql
SELECT code_version, research_schema_version, engine_patch_version, COUNT(*)
FROM snapshots
GROUP BY code_version, research_schema_version, engine_patch_version;
```

If versions are missing or stale, do not use the data for new validation.

## Required tables in mos_research.db

Check:

```sql
SELECT name FROM sqlite_master WHERE type='table';
```

Expected:

```text
snapshots
debug_snapshots
events
future_labels
bad_snapshots
bookmarks
event_fingerprint_cache
ohlcv_candles
```

Minimum required:

```text
snapshots
events
future_labels
bad_snapshots
debug_snapshots
ohlcv_candles
```

## Required snapshot fields

Check:

```sql
PRAGMA table_info(snapshots);
```

Important fields:

```text
snapshot_id
snapshot_sequence_id
timestamp_utc
schema_version
code_version
research_schema_version
engine_patch_version
spot_price
current_state
previous_state
candidate_state
transition_state
regime_duration_sec
global_confidence
data_quality
data_quality_reason
active_sources
net_gex
gamma_regime
call_wall
put_wall
atm_iv
iv_velocity
term_structure_state
oi_total
volume_total
gamma_slope
gamma_acceleration
gamma_slope_state
gamma_acceleration_state
liquidity_void_score
dealer_hedging_pressure
synthetic_flow_pressure
synthetic_flow_pressure_scale
expansion_probability
compression_failure_risk
execution_timing_state
breakout_window
signal_cluster_score
market_phase_hash
exclude_from_analysis
exclude_reason
```

## Required debug fields

`debug_snapshots` must contain JSON breakdowns:

```text
flow_breakdown_json
void_breakdown_json
signal_breakdown_json
transition_breakdown_json
execution_breakdown_json
state_breakdown_json
gamma_breakdown_json
replay_alignment_json
```

After v21 it should also contain:

```text
short_term_flow_breakdown_json
```

Check:

```sql
SELECT COUNT(*) FROM debug_snapshots;

SELECT COUNT(*)
FROM debug_snapshots
WHERE flow_breakdown_json IS NULL
   OR void_breakdown_json IS NULL
   OR signal_breakdown_json IS NULL
   OR transition_breakdown_json IS NULL
   OR execution_breakdown_json IS NULL
   OR state_breakdown_json IS NULL
   OR gamma_breakdown_json IS NULL
   OR replay_alignment_json IS NULL;
```

Expected:

```text
debug_snapshots count ≈ snapshots count
null breakdown count = 0 or explainable
```

## Snapshot count expectations

If writing about every 15–20 seconds:

```text
30 minutes ≈ 90–120 snapshots
1 hour ≈ 180–240 snapshots
8 hours ≈ 1440–1920 snapshots
```

If much less: backend stopped.

If much more: possible multiple loggers.

## current_state criteria

No active `UNKNOWN`.

Check:

```sql
SELECT current_state, COUNT(*)
FROM snapshots
GROUP BY current_state;
```

Allowed:

```text
PINNING
COMPRESSION
TRANSITION
EXPANSION
REBALANCE
PANIC
BREAKOUT_SETUP
HEDGE_CHASE
SHORT_SQUEEZE
LONG_LIQUIDATION
EXHAUSTION
```

If `UNKNOWN > 0`, data is dirty.

## active_sources

Must not be empty.

Expected:

```text
["bybit"]
["deribit", "bybit"]
```

Check:

```sql
SELECT active_sources, COUNT(*)
FROM snapshots
GROUP BY active_sources;
```

## data_quality

Must be text:

```text
GOOD
DEGRADED
PARTIAL
CRITICAL
```

Must not be `0.0`, `0` or `NULL`.

Check:

```sql
SELECT data_quality, COUNT(*)
FROM snapshots
GROUP BY data_quality;

SELECT data_quality_reason, COUNT(*)
FROM snapshots
GROUP BY data_quality_reason
ORDER BY COUNT(*) DESC;
```

## Bad values

Critical bad values:

```text
spot_price <= 0
oi_total <= 0
NaN / inf
invalid timestamp
corrupted gamma values
```

They should set:

```text
exclude_from_analysis = 1
data_quality = CRITICAL
exclude_reason filled
```

Checks:

```sql
SELECT COUNT(*) FROM snapshots WHERE spot_price <= 0;
SELECT COUNT(*) FROM snapshots WHERE oi_total <= 0;
SELECT COUNT(*) FROM snapshots WHERE atm_iv <= 0;
SELECT COUNT(*) FROM snapshots WHERE gamma_slope = 0 AND gamma_acceleration = 0;

SELECT COUNT(*) FROM snapshots WHERE exclude_from_analysis = 1;

SELECT exclude_reason, COUNT(*)
FROM snapshots
WHERE exclude_from_analysis = 1
GROUP BY exclude_reason;
```

## Key metric checks

### liquidity_void_score

Expected scale:

```text
0–20 no meaningful void
20–40 weak structural vulnerability
40–60 moderate void
60–80 significant acceleration corridor
80–100 extreme fragility
```

Check:

```sql
SELECT MIN(liquidity_void_score), AVG(liquidity_void_score), MAX(liquidity_void_score)
FROM snapshots;
```

Known current limitation: score often remains 20–30 because void zones are far from spot and OI/volume weakness is low.

### signal_cluster_score

Scale:

```text
0–25 weak alignment
25–50 moderate alignment
50–75 strong structural alignment
75–100 extreme signal cluster
```

Check:

```sql
SELECT MIN(signal_cluster_score), AVG(signal_cluster_score), MAX(signal_cluster_score)
FROM snapshots;
```

Known current limitation: flow_component, gamma_component and term_structure_component often contribute zero.

### execution_timing_state

Allowed:

```text
WAIT
STRUCTURE_UNSTABLE
EXPANSION_CONFIRMING
HEDGE_CHASE_STARTING
EXECUTION_WINDOW_OPEN
```

Check:

```sql
SELECT execution_timing_state, COUNT(*)
FROM snapshots
GROUP BY execution_timing_state;
```

## Events

Check:

```sql
SELECT event_type, COUNT(*)
FROM events
GROUP BY event_type
ORDER BY COUNT(*) DESC;
```

Bad event links:

```sql
SELECT COUNT(*)
FROM events
WHERE snapshot_sequence_id IS NULL
   OR snapshot_sequence_id <= 0;
```

Expected: `0`.

Warmup check:

```sql
SELECT timestamp_utc, snapshot_sequence_id, event_type, event_payload_json
FROM events
WHERE snapshot_sequence_id < 5
ORDER BY snapshot_sequence_id;
```

Expected: no crossing-events before snapshot 5.

## OHLCV

Check:

```sql
SELECT COUNT(*) FROM ohlcv_candles;
SELECT MIN(timestamp_utc), MAX(timestamp_utc), COUNT(*) FROM ohlcv_candles;
SELECT exchange, symbol, timeframe, COUNT(*)
FROM ohlcv_candles
GROUP BY exchange, symbol, timeframe;
```

Expected:

```text
binance / BTCUSDT / 1m
gap around 60 sec
```

OHLCV is READ-ONLY validation layer. It must not directly change live logic.

## Future labels

Check:

```sql
SELECT COUNT(*) FROM future_labels;

SELECT
  COUNT(future_return_5m),
  COUNT(future_return_15m),
  COUNT(future_return_30m),
  COUNT(future_max_up_30m),
  COUNT(future_max_down_30m),
  COUNT(future_realized_vol_30m),
  COUNT(future_range_30m),
  COUNT(future_breakout_strength),
  COUNT(snapshot_id),
  COUNT(snapshot_sequence_id)
FROM future_labels;
```

After startup: first 30 minutes may be empty; after 30 minutes should start filling.

## Readiness thresholds

### Minimum to verify collection works

```text
snapshots > 100
spot_price valid
current_state not UNKNOWN
data_quality text
active_sources not []
debug_snapshots present
OHLCV present
future_labels appear after 30 minutes
```

### Minimum for primary replay analysis

```text
snapshots > 500
debug_snapshots ≈ snapshots
future_labels > 300
events > 10
ohlcv_candles present
event payload filled
debug JSON filled
no UNKNOWN
no data_quality = 0.0
no active_sources = []
```

### Minimum for probabilistic analysis

```text
3–7 days clean history
multiple current_state regimes
future_labels filled
events diverse
debug breakdown available
OHLCV aligned
bad/excluded snapshots explainable
```

### Minimum for Market Memory

```text
2–4 weeks stable schema
schema_version/code_version saved
market_phase_hash stable
future_labels filled
event outcomes available
debug breakdown available
level reaction layer available
```
