# MOS_NEXT_TASK.md

## Task: validate v67 public option trade-flow observer

## Goal

Prove that raw public BTC option trades are collected continuously and exported
without changing MOS formulas, databases, candidates or execution. Preserve all
v66 Deribit heartbeat, freshness, circuit-breaker and core-coverage behavior.

## Runtime design

- `run.py` starts `workers.option_trade_flow_worker` as a separate process.
- Bybit subscribes to `publicTrade.BTC`.
- Deribit subscribes to `trades.option.BTC.100ms`; public `raw` is not used
  because it can require authorization.
- Reconnects use bounded exponential backoff.
- Best-effort REST backfill runs at startup/reconnection no more than once per
  five minutes per exchange.
- SQLite writes are batched through a bounded queue.
- `(exchange, trade_id)` deduplicates WebSocket/backfill overlap.
- The observer writes only `option_trade_flow.db`.
- Dataset Exporter v1.2 includes the database only when present.

## Protected behavior

Do not modify State Machine, analytical formulas, Particle Logic scoring,
execution/manual entry, events, future labels, OHLCV or any existing database
schema. Do not interpret taker side as a trading signal.

## Collector validation

Start v59/v67 without clearing any database. After two to five minutes call:

```text
GET /api/research/option-trade-flow-status
```

Expected during healthy access:

```text
status = ok
engine_patch_version = v67_option_trade_flow_observer
quick_check = ok
collector_status_fresh = true
all_collectors_subscribed = true
dropped_trades = 0
collector_status contains bybit and deribit
connection_state = subscribed for both
connection_count > 0 for both
```

When trades are present:

```text
trades > 0
normalized_contracts = trades
taker_buys + taker_sells = trades
valid_trade_iv > 0
```

Record the endpoint immediately, after 15 minutes and after 30 minutes without
restarting. `updated_at_utc` must remain fresh, dropped counts must remain zero,
and trade counts must never decrease. Quiet periods may leave last-trade age high;
they do not justify artificial rows.

Also repeat the existing v66 Deribit smoke check. Core coverage must remain at
least 70%; the new observer must not increase the main adapter REST counters or
break heartbeat-qualified liveness.

## Export validation

Run `export_mos_dataset.bat` while all processes continue running. The archive
must contain:

```text
mos_research.db
mos_manual.db
history.db
option_trade_flow.db
manifest.json
```

The manifest must list the first three under `required_databases`, the new file
under `optional_databases` and all four under `included_databases`. Every backup
must have `integrity_check = ok`, `quick_check = ok` and a SHA-256 hash.

## Acceptance

- both public streams stay subscribed for 30 minutes;
- no queue drops;
- duplicate trade IDs do not create duplicate rows;
- canonical option identity and IV are populated for valid option trades;
- reconnect/backfill overlap remains idempotent;
- existing three MOS database schemas are unchanged;
- standard diagnostics retain v59/v67 versions;
- v66 Deribit core coverage and liveness remain valid;
- Dataset Exporter includes and verifies the optional flow database;
- all existing and new unit tests pass;
- no clean database is required.

After acceptance, collect at least 2–4 untouched weeks before deriving signed
delta/vega flow or considering any scoring change.
