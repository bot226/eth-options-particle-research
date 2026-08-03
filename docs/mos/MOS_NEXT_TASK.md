# MOS_NEXT_TASK.md

## Task: validate v65 Deribit REST circuit breaker

## Goal

Prove that a long Deribit outage produces bounded network probes and fast
cache-only diagnostics, then that the collector recovers automatically when
Deribit becomes reachable. Preserve at least 70% fresh complete core coverage
during healthy collection. Do not change any MOS or candidate formula.

## Runtime design

- Compressed REST `get_instruments` discovers the complete BTC option chain
  once per 15 minutes.
- The last valid chain is stored atomically in
  `backend/data/deribit_instruments_cache.json`; it is not a database and does
  not enter Dataset Exporter output.
- If REST fails and no cached chain exists, one compressed WebSocket
  `public/get_instruments` RPC is allowed as discovery fallback.
- WebSocket `incremental_ticker.<instrument>` remains the primary 240-contract
  core transport and the v63 60-second liveness watchdog remains active.
- While coverage is below 80% or WebSocket is unhealthy, REST recovery uses
  two concurrent `public/ticker` requests per one-second batch.
- Once WebSocket is healthy and core coverage reaches 80%, REST changes to one
  request every four seconds and refreshes only missing or aging observations.
- Coverage below 80% automatically restores the faster recovery mode; live MOS
  polling and the smoke endpoint remain cache-only.
- A fresh WebSocket BTC index price is reused for 60 seconds instead of issuing
  the normal repeated REST spot request.
- Three consecutive transport/HTTP failures across discovery, ticker, or index
  REST calls open one shared circuit for 30 seconds.
- Failed half-open probes extend the pause to 60, 120, 240, and at most 300
  seconds; only one concurrent probe is admitted.
- While open, cached instrument discovery is immediate and the ticker scheduler
  issues no REST requests. A successful probe closes the circuit and resumes
  adaptive recovery.

## Protected behavior

Do not modify State Machine, execution/manual entry logic, analytical formulas,
events, future labels, OHLCV, database schemas, cache freshness, the 70%
aggregation threshold, or Particle Logic scoring.

## Collector validation

Start v58/v65 without clearing databases. Wait three to five minutes, then call:

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
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
deribit_instrument_cache_source = rest_compressed, websocket_rpc, disk, or stale_cache
deribit_instrument_http_accept_encoding contains gzip
deribit_instrument_disk_cache_error_count = 0
deribit_rest_circuit_state = closed
deribit_rest_circuit_failure_threshold = 3
deribit_rest_circuit_consecutive_failures = 0
deribit_rest_circuit_retry_after_sec = 0
deribit_rest_fast_request_timeout_sec = 5
deribit_rest_discovery_timeout_sec = 15
deribit_ws_core_instruments_count = 240
deribit_ws_subscribed_tickers = 240
deribit_ws_pending_tickers = 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_full_tickers >= 168
deribit_ws_receiver_state = receiving
deribit_ws_refresh_task_running = true
deribit_ws_ticker_idle_age_sec < 60
deribit_ws_bootstrap_policy = circuit_breaker_30_to_300s+adaptive_recovery_2rps_healthy_0.25rps
deribit_ws_bootstrap_mode = healthy_low_rate
deribit_ws_bootstrap_current_batch_size = 1
deribit_ws_bootstrap_current_interval_sec = 4
deribit_ws_bootstrap_healthy_core_refresh_age_sec = 240
deribit_ws_bootstrap_healthy_interval_sec = 4
```

Record `deribit_fetch_attempt_count`,
`deribit_ws_bootstrap_recovery_request_count`, and
`deribit_ws_bootstrap_low_rate_request_count`. Repeat the request after 15 and
30 minutes without restarting MOS. In uninterrupted healthy mode the low-rate
counter should grow by no more than about 15 requests per minute. A temporary
faster increase is valid only while `deribit_ws_bootstrap_mode` reports
`warmup_recovery` and must stop after coverage returns to at least 80%.

If Deribit becomes unavailable, repeat the smoke request only after the circuit
reports `open`. It must return in under one second from `stale_cache` when a
cached chain exists. `deribit_fetch_attempt_count` must remain unchanged before
`deribit_rest_circuit_next_probe_ts`; the scheduler must report
`network_backoff/rest_circuit_open` and batch size zero. Observe at least one
half-open probe. A failed probe must increase the backoff level; a successful
probe must close the circuit and increment the recovery count.

Restart MOS once after a successful discovery. The first smoke response may
report `deribit_instrument_cache_source = disk`, and
`deribit_instrument_disk_cache_load_count` must be positive. Do not delete the
runtime cache for ordinary validation.

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

- compressed discovery succeeds or a valid disk/WebSocket fallback supplies
  the instrument chain;
- a complete outage opens the REST circuit after three transport failures and stops
  network requests between scheduled single probes;
- cached smoke diagnostics remain fast while the circuit is open;
- failed probes back off to at most five minutes and a valid reply closes the
  circuit automatically;
- initial core readiness reaches at least 70%;
- healthy collection reaches `healthy_low_rate` and no longer sustains two
  REST ticker requests per second;
- core readiness remains at least 70% across 15- and 30-minute checks;
- degradation restores fast recovery and later returns to low-rate mode;
- ticker idle age remains below 60 seconds or triggers the v63 reconnect;
- the smoke endpoint returns current state without a long wait;
- stale observations remain excluded after five minutes;
- the three-second MOS poll remains non-blocking;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
