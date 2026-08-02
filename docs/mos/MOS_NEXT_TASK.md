# MOS_NEXT_TASK.md

## Task: validate v58 Deribit full-ticker bootstrap

## Goal

Restore source-level Deribit BTC option observations without blocking the MOS
poll loop and without changing any market-structure or candidate formula.

## Runtime design

- REST `get_instruments` discovers active BTC option contracts.
- Concurrent callers share one REST discovery request and the last successful
  instrument list remains available for 15 minutes.
- WebSocket `incremental_ticker.<instrument>` supplies a full initial ticker,
  followed by partial OI, volume, IV, price, and Greek updates.
- A separate temporary WebSocket calls `public/ticker` at no more than ten
  requests per second to seed contracts delayed in the subscription stream.
- Subscriptions use at most 500 channels per request; each batch is confirmed
  by JSON-RPC response before the next is sent, and refreshed every 15 minutes.
- The live MOS poll reads a local cache and never waits for the slow bulk REST
  `get_book_summary_by_currency` endpoint.
- Per-contract cache observations older than 120 seconds are excluded.
- Cache coverage below 70% of discovered instruments is treated as warmup and
  excluded from live aggregation.

## Protected behavior

Do not modify:

- State Machine or state formulas;
- ExecutionTimingEngine or manual entry logic;
- SignalCluster, LiquidityVoid, RegimeTransition, or VolatilityEngine;
- events, future labels, OHLCV, or the existing snapshots schema;
- Particle Logic v3 scoring or materiality thresholds.

## Collector validation

With the backend running, call:

```text
GET /api/research/deribit-smoke-test
```

Expected:

```text
status = ok
raw_instruments_count > 0
raw_ws_ticker_count > 0
valid_iv_count > 0
valid_greeks_count > 0
valid_gamma_count > 0
collector_reused = true
deribit_ws_cache_age_sec < 120
deribit_instrument_cache_count > 0
deribit_ws_subscribed_tickers > 0
deribit_ws_fresh_tickers > 0
deribit_ws_cache_coverage_ratio >= 0.7
deribit_ws_full_tickers >= 0.7 * deribit_ws_instruments_count
deribit_ws_bootstrap_success_count > 0
```

The first run can take roughly 60–120 seconds. Subscription pending counts may
remain non-zero during that window; verify that they converge later without
blocking the full-ticker bootstrap.

After at least five minutes, export a dataset and verify:

```sql
SELECT exchange, COUNT(*), COUNT(DISTINCT snapshot_id)
FROM option_contract_snapshots
GROUP BY exchange;
```

Both `bybit` and `deribit` must appear. New research snapshots should include
Deribit in `active_sources` and should not report `deribit_timeout`.

## Acceptance

- the MOS three-second poll is not delayed by Deribit REST;
- a failed or stale Deribit stream falls back to Bybit without stale reuse;
- WebSocket reconnect re-subscribes all active instruments;
- expired instruments are removed from the cache;
- normalizer preserves nested Deribit Greeks and 24-hour volume;
- existing tests and Particle Logic replay tests pass;
- no clean database is required.
