# MOS_NEXT_TASK.md

## Task: validate v62 Deribit core refresh scheduler

## Goal

Keep at least 70% of the balanced 240-contract Deribit research core fresh for
long-running collection while rotating spare REST capacity through the full BTC
option chain. Do not change any MOS or candidate formula.

## Runtime design

- REST `get_instruments` discovers the complete active BTC option chain.
- A 240-contract research core selects near-ATM contracts evenly across every
  available expiry.
- WebSocket `incremental_ticker.<instrument>` continuously updates the complete
  core through one 240-channel subscription request.
- The persistent HTTP client calls per-instrument REST `public/ticker` in
  two-request batches.
- Before 70% fresh complete core coverage, every REST batch targets the core.
- After readiness, nine batches maintain core and one batch rotates through
  non-core contracts.
- Core snapshots become eligible for proactive refresh after 60 seconds; all
  observations remain subject to the strict five-minute aggregation TTL.
- Fair round-robin cursors prevent repeated failures or quiet contracts from
  starving the rest of either universe.
- Instrument refreshes update the scheduler's active target list without
  starting a second bootstrap task.
- The live MOS poll and smoke endpoint only read the local cache. Neither waits
  for bulk REST work.

## Protected behavior

Do not modify State Machine, execution/manual entry logic, analytical formulas,
events, future labels, OHLCV, database schemas, or Particle Logic scoring.

## Collector validation

Start v55/v62, wait three to five minutes, then call:

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
deribit_ws_bootstrap_state = running
deribit_ws_bootstrap_phase = maintaining_core or backfilling_chain
deribit_ws_bootstrap_policy = core_9_to_tail_1_round_robin
deribit_ws_bootstrap_core_request_count > 0
deribit_ws_bootstrap_tail_request_count >= 0
deribit_ws_bootstrap_core_batches_per_tail_batch = 9
deribit_ws_bootstrap_core_refresh_age_sec = 60
deribit_ticker_bootstrap_transport = rest_public_ticker
```

Repeat the same request after at least ten minutes without restarting MOS.
Coverage must remain at or above 70%; request counters must increase; and the
oldest fresh core ticker must remain within the five-minute TTL.

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
- core readiness remains at least 70% across a ten-minute check;
- the smoke endpoint returns current state without a 120-second wait;
- full-chain tail request count eventually increases after core readiness;
- stale observations remain excluded after five minutes;
- the three-second MOS poll remains non-blocking;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
