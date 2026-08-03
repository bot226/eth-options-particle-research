# MOS_NEXT_TASK.md

## Task: validate v66 Deribit heartbeat-qualified liveness

## Goal

Prove that a quiet incremental ticker stream no longer causes repeated full
WebSocket reconnects while a truly dead socket still recovers automatically.
Preserve at least 70% fresh complete core coverage and all v65 REST circuit
breaker behavior. Do not change any MOS or candidate formula.

## Runtime design

- WebSocket `incremental_ticker.<instrument>` remains the primary transport for
  the balanced 240-contract research core.
- Sixty seconds without a ticker notification starts liveness qualification;
  it does not immediately declare the connection dead.
- A protocol-level WebSocket ping is allowed at most once per 30 seconds while
  the ticker stream is quiet. It does not consume REST or JSON-RPC capacity.
- A successful ping requests an in-place unsubscribe/subscribe rebuild of the
  complete core, with a five-minute cooldown between soft recoveries.
- A real ticker snapshot must arrive within 90 seconds after the soft recovery.
  If it does not, or if the protocol heartbeat fails, the existing supervised
  connection loop performs a full reconnect and resubscription.
- A recent heartbeat qualifies transport health, but it never makes stale
  option rows fresh. The five-minute ticker TTL remains unchanged.
- Core coverage below 80% still switches the REST scheduler to two-request-per-
  second recovery. At 80% or above it remains at one request every four seconds.
- The v65 shared REST circuit breaker remains unchanged.

## Protected behavior

Do not modify State Machine, execution/manual entry logic, analytical formulas,
events, future labels, OHLCV, database schemas, ticker freshness, the 70%
aggregation threshold, the 80% adaptive-REST switch, or Particle Logic scoring.

## Collector validation

Start v58/v66 without clearing databases. Wait three to five minutes, then call:

```text
GET /api/research/deribit-smoke-test
```

Expected after warmup:

```text
status = ok
elapsed_ms < 5000 on a reused collector
collector_reused = true
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count = raw_ws_ticker_count
valid_greeks_count = raw_ws_ticker_count
valid_gamma_count = raw_ws_ticker_count
deribit_ws_core_instruments_count = 240
deribit_ws_subscribed_tickers = 240
deribit_ws_pending_tickers = 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_full_tickers >= 168
deribit_ws_receiver_state = receiving
deribit_ws_refresh_task_running = true
deribit_ws_liveness_state = ticker_active, stream_quiet_heartbeat_alive,
    or soft_resubscribe_waiting
deribit_ws_heartbeat_timeout_sec = 10
deribit_ws_heartbeat_recheck_sec = 30
deribit_ws_soft_resubscribe_cooldown_sec = 300
deribit_ws_soft_resubscribe_grace_sec = 90
deribit_rest_circuit_state = closed during healthy access
deribit_ws_bootstrap_mode = healthy_low_rate when coverage >= 0.8
```

Record these counters immediately after warmup and again after 15 and 30
minutes without restarting MOS:

```text
deribit_ws_connection_count
deribit_ws_reconnect_count
deribit_ws_idle_reconnect_count
deribit_ws_heartbeat_attempt_count
deribit_ws_heartbeat_success_count
deribit_ws_heartbeat_error_count
deribit_ws_soft_resubscribe_attempt_count
deribit_ws_soft_resubscribe_success_count
deribit_ws_soft_resubscribe_error_count
deribit_fetch_attempt_count
deribit_ws_bootstrap_recovery_request_count
deribit_ws_bootstrap_low_rate_request_count
```

A quiet but live stream should increase heartbeat and soft-resubscription
counters without increasing idle reconnects. A soft recovery is successful only
after a real ticker notification. One attempt per five minutes is the maximum
normal soft-resubscription rate. If heartbeat fails or the 90-second ticker
grace expires, `deribit_ws_idle_reconnect_count` must increase and a new
connection must rebuild the 240-channel core.

The low-rate REST counter should grow by no more than about 15 requests per
minute while coverage stays at or above 80%. Faster growth is valid only in
`warmup_recovery` and must stop after coverage returns to at least 80%.

After the next five-minute structural snapshot, verify:

```sql
SELECT exchange, COUNT(*), COUNT(DISTINCT snapshot_id),
       SUM(mark_iv IS NOT NULL AND mark_iv > 0),
       SUM(delta IS NOT NULL AND gamma IS NOT NULL
           AND vega IS NOT NULL AND theta IS NOT NULL)
FROM option_contract_snapshots
GROUP BY exchange;
```

Both `bybit` and `deribit` must appear. Do not clear either database.

## Acceptance

- healthy ticker traffic remains `ticker_active`;
- quiet live transport produces successful heartbeat and in-place core
  resubscription instead of repeated full reconnects;
- a real ticker completes soft recovery within 90 seconds;
- failed heartbeat or missing post-resubscribe ticker triggers a full reconnect;
- hard reconnect growth is materially lower than the v65 result of ten idle
  reconnects in about 30 minutes;
- core readiness remains at least 70% across 15- and 30-minute checks;
- stale observations remain excluded after five minutes;
- REST circuit breaker and adaptive request-rate behavior remain valid;
- the smoke endpoint and three-second MOS poll remain non-blocking;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
