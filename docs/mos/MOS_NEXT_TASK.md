# MOS_NEXT_TASK.md

## Task: validate v63 Deribit WebSocket liveness watchdog

## Goal

Prove that a silent or stopped Deribit ticker stream is detected within 60
seconds, automatically reconnected and fully resubscribed, while the balanced
240-contract research core remains usable. Do not change any MOS or candidate
formula.

## Runtime design

- REST `get_instruments` discovers the complete active BTC option chain.
- WebSocket `incremental_ticker.<instrument>` continuously updates the balanced
  240-contract core.
- The receiver and subscription-refresh task are supervised as one lifecycle;
  either task stopping forces a new connection.
- The ticker liveness clock begins only after ticker subscription acknowledgement.
- Sixty seconds without a real WebSocket ticker forces reconnect and complete
  resubscription. REST cache writes never reset this clock.
- The v62 continuous REST scheduler still warms and maintains the core, then
  rotates spare capacity through the full chain at a 9:1 core-to-tail ratio.
- The live MOS poll and smoke endpoint remain local-cache reads and do not wait
  for bulk network work.

## Protected behavior

Do not modify State Machine, execution/manual entry logic, analytical formulas,
events, future labels, OHLCV, database schemas, or Particle Logic scoring.

## Collector validation

Start v56/v63, wait three to five minutes, then call:

```text
GET /api/research/deribit-smoke-test
```

Expected:

```text
status = ok
elapsed_ms < 5000 on a reused collector
collector_reused = true
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
deribit_ws_core_instruments_count = 240
deribit_ws_subscribed_tickers = 240
deribit_ws_pending_tickers = 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_full_tickers >= 168
deribit_ws_receiver_state = receiving
deribit_ws_refresh_task_running = true
deribit_ws_ticker_idle_timeout_sec = 60
deribit_ws_ticker_idle_age_sec < 60
deribit_ws_connection_count > 0
deribit_ws_bootstrap_state = running
deribit_ws_bootstrap_phase = maintaining_core or backfilling_chain
deribit_ws_bootstrap_policy = core_9_to_tail_1_round_robin
deribit_ticker_bootstrap_transport = rest_public_ticker
```

Repeat the request after 15 and 30 minutes without restarting MOS. Ticker idle
age must remain below 60 seconds and core coverage at or above 70%. If a silent
connection occurs, `deribit_ws_connection_count`, `deribit_ws_reconnect_count`,
and `deribit_ws_idle_reconnect_count` must increase, followed by a return to
`receiver_state = receiving` and renewed ticker messages.

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

- initial core readiness reaches at least 70%;
- ticker idle age remains below 60 seconds during healthy collection;
- a silent stream causes automatic reconnect/resubscribe;
- core readiness remains at least 70% across the 15- and 30-minute checks;
- the smoke endpoint returns current state without a long wait;
- stale observations remain excluded after five minutes;
- the three-second MOS poll remains non-blocking;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
