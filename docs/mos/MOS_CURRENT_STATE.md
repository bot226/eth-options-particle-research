# MOS_CURRENT_STATE.md

## Current stable backend state

Current development branch version:

```python
CODE_VERSION = "research_fix_2026_08_03_v54"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v61_deribit_rest_ticker_bootstrap"
PARTICLE_LOGIC_VERSION = "particle_shadow_v3"
```

The latest collected clean-MOS baseline remains on `research_fix_2026_08_01_v45`
with engine patch `v52_particle_contract_lineage_v2`. It is a valid Bybit-only
baseline. Particle Logic remains an offline shadow layer and is not connected
to live MOS decisions.

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

Deploy v54 to the collector without clearing databases, run the Deribit smoke
test, and verify that both Bybit and Deribit contract rows reach the next
five-minute history snapshot. Do not promote contract particles into scoring.
