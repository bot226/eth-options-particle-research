# MOS_CURRENT_STATE.md

## Current stable backend state

Current development branch version:

```python
CODE_VERSION = "research_fix_2026_08_02_v48"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v55_deribit_ws_subscription_backpressure"
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

## Deribit WebSocket option ticker collector v4

- uses REST only to discover active BTC option instruments;
- subscribes to `ticker.<instrument>.agg2` in bounded batches;
- reads per-contract OI, volume, IV, prices, and nested Greeks from WebSocket;
- exposes a fresh cache to the existing three-second MOS aggregation loop;
- refuses stale cache data after 30 seconds;
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

Deploy v48 to the collector without clearing databases, run the Deribit smoke
test, and verify that both Bybit and Deribit contract rows reach the next
five-minute history snapshot. Do not promote contract particles into scoring.
