# MOS_NEXT_TASK.md

## Task: validate v59 Deribit adaptive core bootstrap

## Goal

Restore usable source-level Deribit BTC option observations on the collector
host without requiring the entire illiquid 866-contract chain to refresh inside
one short validation window and without changing any MOS or candidate formula.

## Runtime design

- REST `get_instruments` discovers the complete active BTC option chain.
- A 240-contract research core selects near-ATM contracts evenly across every
  available expiry.
- WebSocket `incremental_ticker.<instrument>` continuously updates the core in
  100-channel subscription batches.
- A separate WebSocket calls `public/ticker` in two-request batches, at no more
  than two requests per second, prioritizing the core and then backfilling the
  complete discovered chain.
- Three consecutive empty RPC batches trigger a connection restart and another
  pass retries only observations not refreshed during the current cycle.
- The live MOS poll reads the local cache and never waits for bulk Deribit REST.
- Observations older than five minutes are excluded.
- Core coverage below 70% is warmup. Full-chain coverage remains diagnostic and
  does not block safe core aggregation.

## Protected behavior

Do not modify State Machine, execution/manual entry logic, analytical formulas,
events, future labels, OHLCV, database schemas, or Particle Logic scoring.

## Collector validation

With the backend running, call:

```text
GET /api/research/deribit-smoke-test
```

Expected:

```text
status = ok
collector_reused = true
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
deribit_ws_core_instruments_count > 0
deribit_ws_core_fresh_tickers > 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_core_full_tickers >= 0.7 * deribit_ws_core_instruments_count
deribit_ws_chain_coverage_ratio >= 0
deribit_ws_bootstrap_success_count > 0
```

The first smoke test may wait up to 120 seconds. The adaptive bootstrap can
remain `running` after the endpoint returns because non-core contracts continue
to backfill in the background.

After at least five minutes, export a dataset and verify:

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

- the three-second MOS poll remains non-blocking;
- research-core readiness succeeds on the collector host;
- calls, puts, multiple expiries, IV, volume, and complete Greeks are present;
- stale observations are rejected after five minutes;
- full-chain backfill continues independently;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
