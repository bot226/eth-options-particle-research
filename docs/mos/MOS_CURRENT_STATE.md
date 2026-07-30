# MOS_CURRENT_STATE.md

## Current stable backend state

Current stable Research Layer version:

```python
CODE_VERSION = "research_fix_2026_05_21_v20"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v20_execution_why_not_and_pinning_absorption_debug_v1"
```

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

Add:

```text
experimental short_term_flow_pressure
```

as READ-ONLY debug layer.

It must NOT affect current_state, execution_timing_state, signal_cluster_score, expansion_probability, event generation, FLOW_SURGE or trading logic.

Purpose: compare legacy synthetic_flow_pressure vs short-term OHLCV-based flow.
