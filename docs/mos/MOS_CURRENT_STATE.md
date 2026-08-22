# MOS_CURRENT_STATE.md

## ETH fork current state

This repository is an independent ETH runtime fork of BTC v70 commit
`424c5692be09bd8693b41acbc65127d20f69cbb1`. It uses ETH-only exchange
symbols and channels, frontend port `5174`, backend port `8101`, and its own
project-local databases. No BTC database was migrated.

```python
CODE_VERSION = "eth_fork_2026_08_14_v1_from_btc_v70"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v74_deribit_call_put_diagnostic_value"
DATASET_EXPORTER_VERSION = "1.2.2"
PARTICLE_LOGIC_VERSION = "particle_shadow_v3"
```

The analytical formulas and thresholds are inherited unchanged. They have not
yet been validated on ETH and must not be treated as proven ETH trading logic.

## ETH stable surface v71 and expiry normalizer v72

The ETH collector now maintains an additive, research-only Deribit panel using
rule `v71_balanced_core_24h`. Its target is 240 contracts balanced across
expiries and by distance to ETH spot. Membership is fixed for 24 hours,
persisted across restarts, and rotated only at the scheduled boundary or when a
member disappears or expires. Every rotation creates a new `universe_id`.

`history.db` keeps the fixed panel in `option_surface_universes`,
`option_surface_snapshots`, and `option_surface_contract_snapshots`. A valid
snapshot requires at least 95% fresh normalized members with mark IV and delta,
gamma, vega, and theta. Invalid snapshots retain an exact reason but never
retain a partial contract panel. The original changing full-chain table and
public option trade flow continue unchanged in parallel.

Patch `v72_single_digit_expiry_normalizer` accepts both `DMMMYY` and `DDMMMYY`
Deribit/Bybit expiries while still rejecting unknown months and impossible
calendar dates. Confirmatory ETH surface evidence begins only with the first
valid v72 fixed-universe snapshot; earlier rotating-chain observations cannot
be mixed with it. No entry logic, candidate thresholds, State Machine formula,
Particle Logic scoring, trade-flow semantics, OHLCV, event, or future-label
behavior changed.

Patch `v73_deribit_valid_iv_diagnostic_key` corrects research telemetry to
count normalized Deribit `markIv` values. Before v73, contract and stable-surface
IV storage was complete, but `mos_research.snapshots.valid_iv_count` was written
as zero because the diagnostic looked for the raw `mark_iv` key after
normalization. Existing rows are retained as a documented telemetry artifact;
new rows report the correct count. No scoring or live-entry behavior changed.

Patch `v74_deribit_call_put_diagnostic_value` corrects the adjacent research
telemetry counters to recognize normalized option types `C` and `P`, while
retaining compatibility with `call` and `put`. Before v74, contract rows were
classified correctly but `mos_research.snapshots.calls_count` and `puts_count`
were written as zero. Existing rows remain unchanged; new rows report the
actual counts. No formulas, acceptance thresholds, or live-entry behavior
changed.

## Upstream BTC v70 baseline

Current development branch version:

```python
CODE_VERSION = "research_fix_2026_08_10_v70"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v68_option_trade_flow_quality_history"
PARTICLE_LOGIC_VERSION = "particle_shadow_v3"
```

The latest collected clean-MOS baseline remains on `research_fix_2026_08_01_v45`
with engine patch `v52_particle_contract_lineage_v2`. It is a valid Bybit-only
baseline. Particle Logic remains an offline shadow layer and is not connected
to live MOS decisions.

Research tool v1.1.2 adds a read-only, one-click Windows readiness check. It
reports clean dual-exchange progress and turns missing or incomplete input into
a clear collector-facing result. The frozen protocol, signal thresholds and live
entry behavior are unchanged.

v65 accepts both Bybit option-trade subscription acknowledgement formats: the
legacy `op=subscribe` response and the current `COMMAND_RESP` response whose
`successTopics` contains `publicTrade.BTC`. A successful acknowledgement can no
longer be misclassified as a rejection and trigger a reconnect loop. Deribit
network timeouts remain visible and are not treated as healthy data.

v66 keeps the direct Deribit option-ticker WebSocket as the source of live
contract data but qualifies quiet connections with JSON-RPC `public/test` on
that same socket. This avoids false reconnects on collector network paths that
pass WebSocket data frames while dropping protocol Ping/Pong control frames.
REST remains only the existing bounded cache-recovery path; no source, formula,
threshold, database schema, candidate, or execution behavior changed.

v70 enables Deribit's official `public/set_heartbeat` protocol at ten seconds
on both direct WebSocket connections, while treating any real ticker, trade, or
control message as transport liveness. The collectors answer every
`test_request` with `public/test`. REST does not replace either live stream.

## Particle Logic shadow v1

- reads MOS Dataset Exporter archives;
- opens `history.db` and `mos_research.db` read-only;
- derives per-contract OI, per-strike GEX, wall, gamma-flip, and front-IV particles;
- separates movement evidence from directional evidence;
- writes a standalone `particle_shadow.db`;
- records shadow watches/candidates and 5/15/30/60/120/240-minute outcomes;
- does not modify live databases, State Machine, execution, events, or manual trading.

## Particle Logic shadow v2

- adds source-level `option_contract_snapshots` to `history.db` without changing
  the existing structural snapshot schema;
- persists per-contract IV, 24-hour volume, delta, gamma, vega, and theta;
- copies contract observations into the standalone replay database;
- links contract observations to particles and every candidate to its complete,
  ranked particle lineage;
- keeps new contract-metric particles observation-only until walk-forward
  validation supports scoring weights;
- remains backward-compatible with v1 MOS archives.

## Particle Logic shadow v3

- keeps every raw contract observation unchanged;
- emits contract particles only after absolute and relative materiality gates;
- caps each contract metric at 48 strongest changes per five-minute snapshot;
- writes `particle_filter_audit` with all emitted and suppressed counts;
- keeps contract particles observation-only and leaves candidate scoring intact.

## Deribit WebSocket option ticker collector v4/v7

- uses REST only to discover active BTC option instruments;
- subscribes to `incremental_ticker.<instrument>` in bounded batches;
- reads per-contract OI, volume, IV, prices, and nested Greeks from WebSocket;
- exposes a fresh cache to the existing three-second MOS aggregation loop;
- refuses per-contract cache data older than 120 seconds;
- refreshes active instrument subscriptions every 15 minutes;
- keeps the existing Bybit adapter and all MOS formulas unchanged;
- keeps `PARTICLE_LOGIC_VERSION = particle_shadow_v3` because candidate scoring
  and offline replay logic are unchanged.

## Deribit subscription backpressure v5

The first collector run on v54 lasted about 40 minutes and proved that the hot
MOS loop stayed responsive, but every research snapshot remained Bybit-only.
The Deribit ticker cache never warmed and no Deribit contract row reached
`history.db`.

v55 therefore:

- sends at most 500 ticker channels per subscription request, reducing an
  approximately 866-instrument chain from nine immediate requests to two;
- paces consecutive subscription batches;
- treats channels as subscribed only after the matching JSON-RPC response;
- retries only rejected or partially acknowledged channels;
- exposes pending request and pending ticker counts in diagnostics;
- leaves cache freshness, 70% coverage, normalization, MOS formulas, and
  Particle Logic scoring unchanged.

## Deribit instrument discovery single-flight v6

The first v55 smoke test exposed a second startup issue: the endpoint and the
background WebSocket task started two simultaneous `get_instruments` calls.
The endpoint received 866 instruments, while the background call returned an
empty result and therefore created zero ticker subscriptions.

v56 therefore:

- performs only one in-flight instrument discovery request per adapter;
- shares a successful instrument list with the WebSocket refresh task;
- caches the last good list for 15 minutes and uses it after a transient REST
  failure;
- runs smoke-test discovery before starting the background WebSocket task;
- exposes instrument cache count and age in diagnostics;
- leaves subscription acknowledgement, cache coverage, MOS formulas, and
  Particle Logic scoring unchanged.

## Deribit initial ticker snapshots v7

The v56 smoke test proved that discovery and the first 500-channel subscription
worked: 866 instruments were cached, 500 channels were confirmed, and ticker
messages arrived. However, `ticker.<instrument>.agg2` emitted only 26 updates
for contracts that changed during the smoke-test window, so cache coverage
remained about 3% and the second batch was still pending.

v57 therefore:

- uses `incremental_ticker.<instrument>`, whose first notification is a full
  ticker snapshot and later notifications contain changed fields;
- waits for each subscription acknowledgement before sending the next batch;
- deep-merges partial `stats` and `greeks` updates into each contract snapshot;
- tracks freshness per contract and excludes observations older than 90 seconds;
- exposes fresh-ticker count separately from total cached ticker count;
- leaves normalization, MOS formulas, databases, candidate scoring, and
  Particle Logic unchanged.

## Deribit full-ticker bootstrap v8

The v57 collector-host smoke test received 113 incremental messages but only
57 unique contract snapshots in 30 seconds. The first 500 subscriptions were
confirmed while the next 366-channel request remained queued. The documented
initial snapshots were therefore correct but too slow to warm a complete
866-contract chain inside the validation window.

v58 therefore:

- keeps `incremental_ticker` as the continuous low-volume update stream;
- opens a separate temporary public WebSocket for one-time `public/ticker`
  snapshots containing per-contract IV, volume, prices, and complete Greeks;
- sends at most ten ticker requests per second, below Deribit's documented
  default non-matching request rate;
- retries only contracts that still lack a complete ticker;
- increases per-contract freshness to 120 seconds for the bounded bootstrap;
- makes the smoke endpoint reuse the already-running MOS adapter instead of
  creating a second competing collector;
- reports bootstrap target, success, error, pending, and full-ticker counts;
- leaves all MOS formulas, databases, manual entries, and particle scoring
  unchanged.

## Deribit adaptive core bootstrap v9

The first v58 collector-host run proved that `public/ticker` works, but the
host could sustain only about two complete replies per second. Ten concurrent
requests produced 76 timeouts out of 220 attempts, while the 120-second cache
window expired early responses before 70% of all 866 instruments could coexist.
The second 366-channel subscription acknowledgement also timed out behind the
initial snapshot backlog.

v59 therefore:

- builds a 240-contract research core, selecting near-ATM contracts evenly
  across every available expiry instead of concentrating on one maturity;
- requires 70% fresh coverage of that core before Deribit enters aggregation,
  while continuing to bootstrap the full discovered chain in the background;
- subscribes only the core in 100-channel batches with a 30-second
  acknowledgement window, avoiding an 866-channel initial notification burst;
- sends `public/ticker` in two-request batches at no more than two requests per
  second and reconnects after three empty batches;
- delays the independent bootstrap for five seconds so core subscription
  snapshots can arrive first and duplicate RPC work can be skipped;
- keeps observations for at most five minutes, matching one slow structural
  snapshot interval while still rejecting stale data;
- reports core and full-chain coverage separately;
- leaves MOS formulas, database schemas, manual entries, and Particle Logic
  scoring unchanged.

## Deribit deduplicated core bootstrap v10

The second v59 collector-host run reached 107 complete core contracts, but 99
successful RPC replies increased the unique cache by only one contract. The
bootstrap cycle had frozen its start timestamp before the first subscription
snapshots arrived, so it treated the already-fresh first 100 contracts as
refresh targets and requested them again. The `100 + 100 + 40` subscription
split also stalled on the second acknowledgement exactly as earlier large-chain
splits had done.

v60 therefore:

- freezes bootstrap baselines only after the five-second subscription head
  start and completely excludes every ticker that is already fresh and full;
- considers a stale or missing target complete only after its receive timestamp
  advances beyond that target's frozen baseline;
- subscribes the complete 240-contract core in one bounded request, which is
  smaller than the previously confirmed 500-channel request;
- reports the actual target count of the current deduplicated bootstrap cycle;
- leaves the core definition, two-request RPC pacing, five-minute freshness,
  MOS formulas, databases, manual entries, and Particle Logic unchanged.

## Deribit REST ticker bootstrap v11

The v60 collector-host run confirmed correct deduplication and one-request core
subscription: all 240 subscriptions were acknowledged with no pending channels,
and 36 successful bootstrap replies increased unique coverage. However, the
temporary bootstrap WebSocket closed without a close frame and completed only
48 requests in 116 seconds before ending degraded at 86 core contracts.

v61 therefore:

- keeps `incremental_ticker` WebSocket subscriptions as the continuous live
  update stream for the complete 240-contract core;
- replaces only the temporary bootstrap socket with Deribit's lightweight
  per-instrument REST `public/ticker` method;
- requests two contracts concurrently per one-second batch, preserving the
  proven conservative rate and the existing deduplicated target baselines;
- uses the existing persistent HTTP client rather than opening another socket;
- applies bounded backoff after three empty REST batches and keeps retry passes;
- reports `rest_public_ticker` as the bootstrap transport;
- leaves the research core, readiness threshold, freshness, MOS formulas,
  databases, manual entries, and Particle Logic unchanged.

## Deribit core refresh scheduler v12

The first v61 collector-host run reached 169 complete core contracts and
entered `status = ok`. After roughly 998 seconds it had cached 783 complete
contracts, but all 240 core snapshots had expired from the five-minute fresh
window while the sequential bootstrap was still traversing the chain. The
second smoke request therefore returned zero usable core contracts even though
Deribit and the REST bootstrap remained healthy.

v62 therefore:

- runs the REST ticker bootstrap as a continuous refresh scheduler instead of
  a finite sequential full-chain pass;
- spends all REST capacity on the 240-contract core until fresh complete
  coverage reaches 70%;
- after warmup, reserves nine refresh batches for core maintenance for every
  one rotating tail batch;
- refreshes aging core observations from 60 seconds onward so the slow host
  has time to revisit them before the strict five-minute TTL;
- uses fair round-robin cursors so missing or quiet contracts cannot pin the
  scheduler on the same instruments;
- accepts updated instrument/core universes without starting a second task;
- makes the smoke endpoint report the current cache immediately instead of
  waiting up to 120 seconds;
- distinguishes the last actual WebSocket ticker time from REST-bootstrap
  success time in diagnostics;
- leaves cache TTL, readiness threshold, MOS formulas, database schemas,
  manual entries, and Particle Logic scoring unchanged.

## Deribit WebSocket liveness watchdog v13

v62 initially reached 730 fresh complete contracts and all 240 core contracts,
but a later smoke check found a zombie WebSocket: the cache still contained all
866 contracts while only 100 core tickers remained fresh. The last actual
WebSocket ticker was about 20 minutes old and the last subscription refresh was
about 30 minutes old, although the adapter still reported 240 confirmed
subscriptions. REST maintenance alone could not keep the core above 70%.

v63 therefore:

- supervises the WebSocket receiver and subscription-refresh task as one
  connection lifecycle, so either task ending forces reconnection;
- starts a ticker liveness clock only after a ticker subscription is confirmed;
- forces reconnection and complete resubscription after 60 seconds without an
  actual incremental ticker message;
- distinguishes REST cache writes from real WebSocket activity;
- exposes receiver state, connection/reconnect counts, idle age, idle timeout,
  idle-reconnect count, and refresh-task health in diagnostics;
- leaves the REST core scheduler, five-minute data TTL, readiness threshold,
  MOS formulas, database schemas, manual entries, and Particle Logic unchanged.

## Deribit adaptive REST guard v14

Collector-host diagnostics proved that Deribit was reachable and returned
`HTTP 200`, but an uncompressed `get_instruments` response temporarily fell to
about 850 bytes per second and timed out after 30 seconds. The same request with
compression completed in about 0.27 seconds. The v63 scheduler also continued
up to two unauthenticated `public/ticker` requests per second after WebSocket
recovery, creating avoidable sustained REST traffic.

v64 therefore:

- explicitly advertises gzip/deflate compression for REST discovery and
  reports response encoding, transferred bytes, decoded bytes, and latency;
- persists the last successful instrument chain to an atomic JSON disk cache,
  uses it after transient discovery failure, and keeps databases unchanged;
- uses one compressed WebSocket `public/get_instruments` RPC only when REST
  fails and no memory/disk instrument cache exists;
- keeps the two-request-per-second REST ticker mode only for initial warmup or
  recovery below 80% core coverage;
- switches to at most one REST ticker request every four seconds when the full
  core subscription, WebSocket receiver, and at least 80% core coverage are
  healthy;
- revisits core contracts from four minutes in healthy mode, while returning
  to the faster 60-second recovery schedule if coverage deteriorates;
- serves the BTC index price from its WebSocket subscription for up to 60
  seconds, eliminating the normal three-second REST spot poll;
- exposes adaptive mode, active batch size/interval, recovery versus low-rate
  request counts, recent WebSocket-served contracts, and disk-cache status;
- leaves the five-minute ticker TTL, 70% aggregation threshold, MOS formulas,
  database schemas, manual entries, and Particle Logic scoring unchanged.

## Deribit REST circuit breaker v15

The long v64 collector test later encountered a complete Deribit outage. The
instrument disk cache correctly preserved 826 contracts and stale tickers were
excluded, but all 240 core contracts became stale. REST recovery accumulated
224 failed ticker calls while the WebSocket reconnected, and a smoke request
waited about 15 seconds for another discovery timeout.

v65 therefore:

- shares one REST circuit breaker across instrument discovery, per-contract
  ticker recovery, and the BTC index fallback;
- opens the circuit after three consecutive transport/HTTP failures and retries with one
  half-open probe after 30, 60, 120, 240, then at most 300 seconds;
- prevents concurrent calls during the half-open probe and closes the circuit
  immediately after any valid REST response;
- pauses the ticker recovery scheduler without issuing network requests while
  the circuit is open, then resumes core warmup automatically after recovery;
- returns the stale instrument set immediately while the circuit is open, so
  the smoke endpoint remains cache-only instead of waiting for another timeout;
- limits ticker and spot REST calls to five seconds and disables repeated spot
  retries; compressed instrument discovery retains its 15-second timeout;
- exposes circuit state, retry delay, backoff level, failures, skipped calls,
  probes, recoveries, and the last circuit error in diagnostics;
- continues to reject option ticker observations older than five minutes and
  leaves formulas, database schemas, manual entries, and particle scoring
  unchanged.

## Deribit heartbeat-qualified WebSocket liveness v16

The 30-minute v65 validation window kept 80.8% of the research core fresh, but
the 60-second ticker-silence watchdog caused ten idle reconnects. Intermittent
incremental updates were quiet even while the socket transport remained alive,
so full reconnects created unnecessary subscription churn.

v66 therefore:

- qualifies ticker silence with a protocol-level WebSocket ping that does not
  consume a Deribit JSON-RPC or REST request;
- rebuilds the complete core subscription in-place after a successful ping,
  with a five-minute cooldown between soft recoveries;
- requires a real ticker snapshot within 90 seconds after resubscription and
  reconnects only when the heartbeat fails or that snapshot grace expires;
- treats a recent successful heartbeat as healthy transport while preserving
  the existing 80% adaptive-REST switch and 70% aggregation threshold;
- exposes heartbeat, soft-resubscription, recovery, and liveness-state counters
  for 15/30-minute validation;
- leaves ticker freshness, core selection, REST circuit breaker, MOS formulas,
  databases, manual entries, and Particle Logic scoring unchanged.

## Deribit JSON-RPC heartbeat compatibility v17

Collector-host validation after the DNS route was repaired proved that direct
WebSocket JSON data and subscriptions worked, while protocol Ping/Pong control
frames still timed out. The old watchdog therefore disconnected a usable quiet
ticker stream every 60 seconds and forced the bounded REST recovery path to keep
the core fresh.

Research fix v66 therefore:

- sends public JSON-RPC `public/test` through the already-open Deribit
  WebSocket when the incremental ticker stream is quiet;
- receives the matching response in the single existing receive loop and still
  processes any ticker notifications or subscription acknowledgements that
  arrive first;
- reconnects on a real JSON-RPC timeout or error exactly as before;
- exposes `deribit_ws_heartbeat_transport = json_rpc_public_test`;
- does not use REST for the heartbeat and does not replace Deribit ticker data;
- leaves source data, cache freshness, core coverage, formulas, thresholds,
  schemas, candidates, manual entries, and execution behavior unchanged.

## Deribit proactive JSON-RPC keepalive v18

The first collector-host v66 run showed that a heartbeat sent only after 60
seconds of ticker silence was too late for that network route. Every connection
received an initial ticker burst, then the heartbeat timed out and the socket
reconnected.

Research fix v67 therefore:

- sends `public/test` after 20 seconds without a WebSocket data message;
- keeps the existing 60-second ticker-silence rule for deciding whether the
  240-contract core needs an in-place resubscription;
- disables protocol Ping/Pong in the separate Deribit option-trade observer
  and gives it the same 20-second JSON-RPC keepalive;
- still reconnects if `public/test` itself fails within ten seconds;
- leaves direct Deribit subscriptions, raw data, REST recovery limits, formulas,
  thresholds, schemas, candidates, and execution unchanged.

## Deribit traffic-independent keepalive v19

Collector-host validation of v67 exposed 52 main-ticker reconnects and 12
option-trade reconnects in about 40 minutes. All 52 JSON-RPC heartbeats were
attempted only after the incoming stream had already become silent and all
timed out. The implementation used a receive timeout as the heartbeat clock, so
every incoming message postponed the outbound keepalive.

Research fix v68 therefore:

- checks the fixed 20-second outbound heartbeat deadline even while ticker
  messages are continuously arriving;
- gives the option-trade observer an absolute heartbeat deadline instead of a
  receive-idle timeout, so trades cannot postpone `public/test`;
- preserves all notifications received while waiting for the JSON-RPC reply;
- reconnects only when the scheduled application heartbeat actually fails;
- leaves sources, databases, formulas, thresholds, candidates, and execution
  behavior unchanged.

## Deribit server-driven heartbeat v20

The first v68 collector run sent all scheduled client heartbeats on time, but
the subscribed ticker socket returned zero replies across eight attempts and
reconnected eight times in about six minutes. The trade stream reconnected
twice in the same window. This disproved receive scheduling as the remaining
cause.

Research fix v69 therefore follows Deribit's documented WebSocket session
protocol instead of sending unsolicited liveness probes:

- enables `public/set_heartbeat` with the minimum supported ten-second
  interval on each direct Deribit socket;
- records server `heartbeat` notifications as transport liveness;
- answers every server `test_request` immediately with `public/test` and
  correlates the JSON-RPC response;
- reconnects when the server heartbeat is absent for 30 seconds;
- retains the previous client `public/test` only as a pre-ack fallback;
- leaves raw sources, REST recovery, databases, formulas, thresholds,
  candidates, and execution unchanged.

## Deribit any-frame liveness and non-blocking setup v21

The first v69 collector run proved that `set_heartbeat` was accepted and a
`test_request` round trip succeeded in 52.5 ms, but the main adapter still
reconnected 56 times in about 42 minutes. It incorrectly required a dedicated
server heartbeat every 30 seconds even while thousands of real ticker frames
were arriving. The trade observer separately waited synchronously for the
`set_heartbeat` acknowledgement and entered a roughly ten-second reconnect
loop when that reply was delayed.

Research fix v70 therefore:

- treats every received WebSocket frame as route liveness, including ticker,
  trade, subscription, heartbeat, and JSON-RPC response frames;
- reconnects only after 30 seconds with no WebSocket frame at all or after an
  unanswered server `test_request`;
- sends trade-stream `set_heartbeat` without blocking the live receive loop;
- preserves the latest Deribit disconnect reason across a successful reconnect
  so diagnostics cannot erase the cause before it is inspected;
- leaves raw sources, REST recovery, databases, formulas, thresholds,
  candidates, and execution unchanged.

## Public option trade-flow observer v68

Research on the first six-day contract-level archive found no durable futures
direction edge in OI, 24-hour volume, IV or Greek snapshots. Several apparent
Deribit surface factors were caused by contracts disappearing from and returning
to the cache. Clean matched-contract Bybit changes retained a possible
movement-magnitude effect after false breakouts, but reversal and continuation
both remained negative after costs.

v67 added only missing raw evidence; it did not add a signal:

- runs a standalone observer process for public BTC option trades;
- subscribes to Bybit `publicTrade.BTC` and Deribit
  `trades.option.BTC.100ms`, both public channels;
- records taker side, size, trade/mark/index price, trade IV, block/combo flags,
  exchange sequence and the untouched raw payload;
- normalizes every option symbol to the existing canonical contract ID;
- deduplicates by `(exchange, trade_id)` and reports queue drops and connection
  health;
- writes only `option_trade_flow.db`; existing MOS databases and schemas are
  unchanged;
- exposes `/api/research/option-trade-flow-status` as a read-only diagnostic;
- makes Dataset Exporter v1.2.1 include the new database when it exists;
- remains an observation-only Research Layer and cannot change State Machine,
  events, candidates, execution or manual trading.

v68 makes that evidence auditable across restarts without changing the stream or
any MOS decision:

- assigns an immutable session ID and process start time to each worker run;
- appends both exchanges' state every five seconds to
  `collector_status_history` instead of retaining only the latest row;
- preserves cumulative drop and reconnect counters per session;
- exposes 30-minute sample gaps, unhealthy samples, sessions and historical
  queue drops in the read-only status endpoint;
- upgrades the standalone database schema in place from 1.0 to 1.1; no clean
  database is required;
- Dataset Exporter 1.2.1 includes the new history time range and row count.

The first trade-flow hypothesis family is frozen before collection in
`MOS_OPTION_FLOW_PREREG_V1.json`. The offline readiness tool rejects archives
without 14 healthy dual-exchange days, clean queue history, contract Greeks or
futures OHLCV. Statistical promotion requires prior-day-only thresholds,
positive performance at 6/10/15 bps, a positive day-block 95% lower bound, Holm
and shared-day max-T significance, and same-sign exchange confirmation. Even a
statistically confirmed result remains prohibited from live entry changes until
a separate reviewed version explicitly promotes it.

Offline research model v1.1.1 is implemented in v63. It performs the causal
same-contract Greek join, builds the frozen 5/15/30-minute direction and range
features, aligns 15/30/60-minute Bybit linear outcomes, detects only the frozen
30/15/2-bps false-sweep context, suppresses overlapping positions, charges
6/10/15 bps, and evaluates every declared scope/filter/segment under its full
multiple-testing family. Synthetic tests prove that later Greek snapshots and
future-day observations cannot enter earlier features or thresholds. It has not
yet seen a qualifying v68 dataset and therefore has produced no trading claim.

v1.1.0 additionally joins each decision only to an earlier MOS snapshot and
tests the frozen COMPRESSION/PINNING, EXPANSION/BREAKOUT, positive/negative
Gamma and active-execution regimes. Predeclared delta/contract,
delta-times-gamma, gamma/vega and delta-times-activity combinations remain in
the same multiple-testing families. A directional rule must beat both trailing
price continuation and price reversal on the exact same timestamps with a
positive day-block 95% lower confidence bound; positive standalone PnL is no
longer sufficient.

v1.1.1 rejects a 15/30-minute flow feature unless every five-minute bucket in
its full lookback has clean status history for both exchanges. Trailing
price-control paths and future return/range paths require at least 95% of the
expected one-minute candles. Thus recovery after an outage cannot silently make
an incomplete pre-outage flow window or an underestimated future range eligible.

The worker is enabled by the standard launcher. Set
`MOS_OPTION_TRADE_FLOW_ENABLED=0` before launch to disable it without affecting
MOS. Raw trades require later joining to the nearest fresh contract snapshot
before delta-, gamma- or vega-weighted flow is researched.

## Latest validated v20 database

Latest validated database showed approximately:

```text
snapshots:        856
debug_snapshots:  856
future_labels:    758
events:            23
bad_snapshots:      0
ohlcv_candles:    271
```

## Working components

The following components are working and must not be broken:

- ResearchLogger writes snapshots.
- debug_snapshots writes one row per snapshot.
- All debug JSON fields are populated.
- future_labels works.
- future_labels are linked to snapshots by snapshot_id and snapshot_sequence_id.
- OHLCV collector works.
- OHLCV 1m candles are saved without gaps.
- iv_velocity works.
- event_payload_json is populated.
- phase_context exists in event payload.
- warmup suppression works.
- active_sources is not empty.
- data_quality is categorical.
- bad_snapshots = 0.
- market_phase_hash is alive.

## Current known analytical limitations

### 1. synthetic_flow_pressure is still slow-flow

v20 debug showed:

```text
price_velocity_window_sec = 86400
volume_acceleration_input = 0
oi_delta_input = 0
flow_pressure_input around 50
```

Conclusion: current synthetic_flow_pressure is slow and based mostly on 24h price velocity / flow_pressure around neutral. It does not react well to 5m/15m OHLCV moves.

### 2. FLOW_SURGE is absent

This is acceptable on calm markets, but must be compared against short-term OHLCV movement.

### 3. liquidity_void_score stays mostly 20–30

Debug shows that void zones exist, but they are mostly far from spot; OI weakness is near zero; volume weakness is near zero.

### 4. signal_cluster_score is weak

Because flow_component is near zero, gamma_component is zero, liquidity_void_component is low, and term_structure_component is zero.

### 5. expansion_probability stays below 50

Because these components contribute almost nothing: flow_intensity, flow_pressure_imbalance, liquidity_void, gamma_slope_state, term_structure.

### 6. State Machine

State Machine now alternates between PINNING and COMPRESSION better than before. But TRANSITION remains rare, PINNING vs RANGE_COMPRESSION conflict should be monitored, and pinning_score can exceed 100 in debug.

### 7. Execution

Execution mostly remains WAIT. This is acceptable on calm markets, but must be checked during active OHLCV movement.

## Next recommended step

Deploy v59/v70 to the collector without clearing databases. Preserve the v70
heartbeat and core-coverage checks, then validate that `option_trade_flow.db`
grows, both public trade streams remain subscribed, no queue drops occur and the
Dataset Exporter includes the optional database. Do not derive or promote a
direction score from the new trades until an untouched multi-week archive proves
positive futures expectancy after costs.
