# MOS Public Option Trade Flow v1

## Purpose

Collect the missing event-level option evidence needed to test direction without
changing MOS decisions. Existing contract snapshots contain OI, rolling 24-hour
volume, IV and Greeks, but they do not identify individual trades or the taker
side.

The observer uses public, unauthenticated feeds:

- Bybit: `publicTrade.BTC`;
- Deribit: `trades.option.BTC.100ms`.

It performs a best-effort recent-trade REST backfill at startup/reconnection, no
more than once per exchange per five minutes. WebSocket streams remain primary.

## Isolation contract

The observer:

- writes only `backend/data/option_trade_flow.db`;
- never imports State Engine or a candidate engine;
- never writes `history.db`, `mos_research.db` or `mos_manual.db`;
- never changes scores, events, entries or execution;
- may be disabled with `MOS_OPTION_TRADE_FLOW_ENABLED=0`.

## Stored evidence

`option_trades` preserves:

- exchange trade ID and sequence;
- exchange and receive timestamps;
- raw and canonical instrument identifiers;
- expiry, strike and CALL/PUT type;
- taker BUY/SELL side;
- price, amount and contract count when supplied;
- index and mark prices;
- raw IV plus normalized decimal IV;
- block/combo flags;
- the complete source JSON.

Primary key `(exchange, trade_id)` makes reconnect backfills idempotent.

`collector_status` records connection/reconnect/message/trade counts, queue drops,
last timestamps and errors. A dropped trade invalidates that collection window.

Schema 1.1 also appends the same evidence every five seconds to
`collector_status_history`, keyed by a unique process session. This makes
disconnects, restarts, stale intervals and queue drops auditable after the fact;
the latest-state table alone is not accepted as proof of a clean research window.

## Runtime verification

Start the normal dashboard and open:

```text
http://localhost:8005/api/research/option-trade-flow-status
```

Healthy result after startup/backfill:

```text
status = ok
quick_check = ok
collector_status_fresh = true
all_collectors_subscribed = true
dropped_trades = 0
collector_status contains bybit and deribit
```

Trade counts may pause during genuinely quiet markets, but `updated_at_utc` must
stay fresh and message/trade counters must resume when public trades occur.

SQL checks:

```sql
PRAGMA integrity_check;

SELECT exchange, COUNT(*) AS trades,
       MIN(trade_timestamp_utc), MAX(trade_timestamp_utc),
       SUM(taker_side = 'BUY') AS taker_buys,
       SUM(taker_side = 'SELL') AS taker_sells,
       SUM(contract_id IS NOT NULL) AS normalized_contracts,
       SUM(trade_iv_decimal IS NOT NULL) AS valid_iv,
       SUM(is_block_trade) AS block_trades,
       SUM(is_combo_trade) AS combo_trades
FROM option_trades
GROUP BY exchange;

SELECT * FROM collector_status ORDER BY exchange;
```

## Dataset export

Dataset Exporter v1.2.1 keeps the original three databases required. If
`option_trade_flow.db` exists, it creates a consistent SQLite online backup,
checks integrity, adds time ranges and SHA-256 metadata, and includes it in the
same ZIP. No collector shutdown is required.

## Research rule

Taker BUY is not automatically bullish and taker SELL is not automatically
bearish. CALL/PUT type, delta sign, maturity, moneyness, block/combo structure and
the nearest fresh contract Greeks must be considered. All transformations remain
offline until multi-week walk-forward validation beats price-only controls after
0.06%, 0.10% and 0.15% futures costs.

The preregistered protocol is frozen in
`docs/mos/MOS_OPTION_FLOW_PREREG_V1.json`. It fixes the feature families,
lookbacks, horizons, costs, training period, quantile, false-sweep definition,
minimum sample sizes, daily block bootstrap, Holm correction, shared-day max-T
test and exchange-confirmation gate before any v68 archive is inspected.

Readiness can be audited without changing a database:

```text
python -m backend.scripts.option_flow_research <dataset-directory-or-zip>
```

The command returns `ready` only when both trade feeds, schema 1.1 quality
history, contract Greeks, futures OHLCV, zero-drop sessions and at least 14
healthy common days are present. Its output includes a canonical SHA-256 of the
loaded protocol. A changed threshold therefore creates a different research
identity and cannot silently replace the frozen test.

After readiness passes, run the complete frozen family:

```text
python -m backend.scripts.option_flow_research <dataset-directory-or-zip> --run-analysis --output option_flow_result.json
```

For every trade the analyzer uses only the latest same-contract Greek snapshot
whose timestamp is not later than the trade and whose age is at most ten
minutes. It builds 5/15/30-minute CALL-versus-PUT and signed-delta imbalance for
direction, plus absolute signed-gamma, signed-vega and trade-intensity features
for future range. Tests are repeated for all trades versus non-block/non-combo,
six maturity/moneyness segments, combined/Bybit/Deribit scopes, 15/30/60-minute
outcomes, all observations versus the frozen false-sweep context, and direct
versus inverse economic direction. Every inspected rule remains inside its
declared Holm and shared-day max-T family.

The same causal join is applied to MOS regime snapshots. Separate preregistered
contexts cover COMPRESSION/PINNING, EXPANSION/BREAKOUT, positive and negative
Gamma, and an active execution window. Fixed combinations test agreement of
contract flow with signed delta, delta weighted by gamma, joint gamma/vega
pressure and delta pressure weighted by trade activity. They are hypotheses in
the original family, not extra post-hoc searches.

For every selected directional timestamp the analyzer also trades two frozen
price-only controls using the sign of the trailing return over the same lookback:
continuation and reversal. Option flow must beat both controls with a positive
day-block 95% lower confidence bound in addition to producing positive net PnL,
passing Holm/max-T and agreeing across Bybit and Deribit.

A feature is eligible only when every five-minute health bucket across its full
lookback contains at least 48 subscribed five-second samples on both exchanges
(80% observed coverage). A recovered final bucket cannot validate a
15/30-minute window that crossed an outage. Price-control and future outcome
paths also require at least 95% of their expected one-minute candles; incomplete
paths receive no label rather than a biased smaller range.
