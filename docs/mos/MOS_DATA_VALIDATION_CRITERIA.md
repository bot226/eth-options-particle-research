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

v68 uses the optional, standalone public trade database:

```text
option_trade_flow.db
option_trade_flow.db-wal
option_trade_flow.db-shm
```

It is not required for backward-compatible MOS replay and must never make the
three core databases optional. Dataset Exporter v1.2.1 includes it automatically
when present.

```sql
PRAGMA integrity_check;

SELECT exchange, COUNT(*) AS trades,
       SUM(taker_side = 'BUY') AS buys,
       SUM(taker_side = 'SELL') AS sells,
       SUM(contract_id IS NOT NULL) AS normalized_contracts,
       SUM(trade_iv_decimal IS NOT NULL) AS valid_iv,
       MIN(trade_timestamp_utc), MAX(trade_timestamp_utc)
FROM option_trades
GROUP BY exchange;

SELECT exchange, connection_state, connection_count, reconnect_count,
       session_id, process_started_at_utc,
       message_count, normalized_trade_count, queued_trade_count,
       dropped_trade_count, last_message_utc, last_trade_utc,
       last_error, updated_at_utc
FROM collector_status
ORDER BY exchange;

SELECT exchange, session_id,
       MIN(updated_at_utc) AS first_sample,
       MAX(updated_at_utc) AS last_sample,
       COUNT(*) AS samples,
       SUM(connection_state != 'subscribed') AS unhealthy_samples,
       MAX(dropped_trade_count) AS session_drops
FROM collector_status_history
GROUP BY exchange, session_id
ORDER BY first_sample;
```

Acceptance for a valid option-flow window:

```text
quick_check = ok
both collector_status rows exist
connection_state = subscribed during healthy network access
updated_at_utc age <= 15 seconds while the launcher is running
dropped_trade_count = 0
collector_status_history exists for both exchanges
session IDs survive restart and prior rows are not overwritten
no session used for inference has MAX(dropped_trade_count) > 0
trade IDs remain unique per exchange
all parsed option rows have canonical contract_id, expiry, strike and option_type
```

Use `/api/research/option-trade-flow-status` for the same read-only runtime
summary. A network gap that cannot be covered by the recent-trade backfill must
be marked invalid during later research; never interpolate missing trades.

For v66 Deribit collection, allow three to five minutes for initial core
warmup, then the non-blocking smoke test must report:

```text
status = ok
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
collector_reused = true
deribit_data_transport = compressed_rest_discovery+websocket_incremental_ticker_cache+circuit_broken_adaptive_rest_ticker_recovery
deribit_ticker_bootstrap_transport = circuit_broken_adaptive_rest_public_ticker
deribit_rest_circuit_state = closed during healthy collection
deribit_rest_circuit_failure_threshold = 3
deribit_rest_circuit_consecutive_failures = 0 during healthy collection
deribit_rest_circuit_retry_after_sec = 0 during healthy collection
deribit_rest_fast_request_timeout_sec = 5
deribit_rest_discovery_timeout_sec = 15
deribit_instrument_cache_count > 0
deribit_instrument_cache_source = rest_compressed, websocket_rpc, disk, or stale_cache
deribit_instrument_http_accept_encoding contains gzip
deribit_instrument_disk_cache_error_count = 0
deribit_ws_subscribed_tickers > 0
deribit_ws_fresh_tickers > 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_instruments_count > 0
deribit_ws_core_fresh_tickers / deribit_ws_core_instruments_count >= 0.7
deribit_ws_core_full_tickers / deribit_ws_core_instruments_count >= 0.7
deribit_ws_bootstrap_state = running or complete
deribit_ws_bootstrap_phase = maintaining_core, backfilling_chain, or recovering_core
deribit_ws_bootstrap_policy = circuit_breaker_30_to_300s+adaptive_recovery_2rps_healthy_0.25rps
deribit_ws_bootstrap_mode = healthy_low_rate after warmup
deribit_ws_bootstrap_current_batch_size = 1 in healthy_low_rate
deribit_ws_bootstrap_current_interval_sec = 4 in healthy_low_rate
deribit_ws_bootstrap_low_rate_coverage_ratio = 0.8
deribit_ws_bootstrap_success_count > 0
deribit_ws_bootstrap_core_request_count > 0
deribit_ws_bootstrap_tail_request_count >= 0
deribit_ws_bootstrap_recovery_request_count > 0
deribit_ws_bootstrap_low_rate_request_count >= 0
deribit_ws_bootstrap_core_batches_per_tail_batch = 9
deribit_ws_bootstrap_core_refresh_age_sec = 60
deribit_ws_bootstrap_healthy_core_refresh_age_sec = 240
deribit_ws_bootstrap_healthy_interval_sec = 4
deribit_ws_bootstrap_cycle_target_count <= deribit_ws_bootstrap_target_count
deribit_ws_receiver_state = receiving
deribit_ws_refresh_task_running = true
deribit_ws_ticker_idle_timeout_sec = 60
deribit_ws_liveness_state = ticker_active, stream_quiet_heartbeat_alive,
    or soft_resubscribe_waiting
deribit_ws_heartbeat_timeout_sec = 10
deribit_ws_heartbeat_recheck_sec = 30
deribit_ws_soft_resubscribe_cooldown_sec = 300
deribit_ws_soft_resubscribe_grace_sec = 90
deribit_ws_connection_count > 0
deribit_ws_reconnect_count >= 0
deribit_ws_idle_reconnect_count >= 0
```

`deribit_ws_cache_coverage_ratio` is the readiness ratio for the balanced core.
`deribit_ws_chain_coverage_ratio` separately reports how much of the full
discovered chain is fresh. Full-chain bootstrap may continue after the core is
usable and must not block MOS polling.

Repeat the smoke check after 15 and 30 minutes without restarting MOS. Core
coverage must remain at or above 70%, the receiver must remain in `receiving`,
and liveness must be qualified by a recent ticker, heartbeat, or an active soft
resubscription. After warmup, low-rate REST requests should grow no faster than
about 15 per minute, excluding a temporary return to `warmup_recovery` when
core coverage falls below 80%. Quiet ticker periods may increase heartbeat and
soft-resubscription counters without increasing idle reconnects. A failed
heartbeat or a soft recovery without a real ticker for 90 seconds must increase
the connection/reconnect and idle-reconnect counters, followed by fast REST
recovery and a return to `healthy_low_rate`. A growing total cache with stale
core coverage and neither heartbeat qualification nor reconnect is a liveness
failure and invalidates that collection window.

During an observed Deribit outage, the valid degraded contract is:

```text
status = degraded
elapsed_ms < 1000 once the circuit is already open and cache is reused
raw_instruments_count > 0 when memory/disk cache exists
raw_ws_ticker_count = 0 after all option observations exceed five minutes
deribit_instrument_cache_source = stale_cache
deribit_rest_circuit_state = open
deribit_rest_circuit_consecutive_failures >= 3
deribit_rest_circuit_retry_after_sec > 0
deribit_ws_bootstrap_mode = network_backoff
deribit_ws_bootstrap_phase = rest_circuit_open
deribit_ws_bootstrap_current_batch_size = 0
```

While the circuit remains open, `deribit_fetch_attempt_count` must not increase.
At expiry exactly one half-open probe may increase it. A failed probe increases
the backoff through 30, 60, 120, 240, and at most 300 seconds. A successful
probe must set `deribit_rest_circuit_state = closed`, increment the recovery
counter, and resume `warmup_recovery` followed by `healthy_low_rate`. Stale
option rows must never be returned as current observations.

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
