# MOS_NEXT_TASK.md

## Task: v21 Experimental Short-Term Flow Context

## Goal

Add:

```text
short_term_flow_pressure
```

as an experimental READ-ONLY debug layer.

Do not replace:

```text
synthetic_flow_pressure
```

## Why

v20 proved that current `synthetic_flow_pressure` is slow and based on 24h price velocity / flow_pressure around neutral.

Known v20 debug:

```text
price_velocity_window_sec = 86400
volume_acceleration_input = 0
oi_delta_input = 0
flow_pressure_input around 50
```

This does not react well to 5m/15m OHLCV moves.

We need a separate short-term flow diagnostic to compare against OHLCV.

## Create

```text
backend/engine/short_term_flow_context_engine.py
```

## It should calculate

From OHLCV 1m and/or available spot context:

- return_1m;
- return_5m;
- return_15m;
- range_5m;
- range_15m;
- volume_change_5m;
- volume_change_15m;
- short_term_flow_pressure;
- short_term_flow_intensity;
- direction.

## Scale

```text
-100 to +100
0 = neutral
```

## Direction

```text
STRONG_SELL
MODERATE_SELL
WEAK_SELL
NEUTRAL
WEAK_BUY
MODERATE_BUY
STRONG_BUY
```

## Suggested conservative scoring

```python
score = 0.0

# 1m return
if return_1m > 0.15:
    score += 8
elif return_1m > 0.05:
    score += 3
elif return_1m < -0.15:
    score -= 8
elif return_1m < -0.05:
    score -= 3

# 5m return
if return_5m > 0.30:
    score += 15
elif return_5m > 0.15:
    score += 8
elif return_5m < -0.30:
    score -= 15
elif return_5m < -0.15:
    score -= 8

# 15m return
if return_15m > 0.60:
    score += 25
elif return_15m > 0.30:
    score += 12
elif return_15m < -0.60:
    score -= 25
elif return_15m < -0.30:
    score -= 12

# range expansion
if range_15m > 0.60:
    score += 8 if return_15m > 0 else -8

# volume acceleration
if volume_change_15m > 40:
    score += 7 if return_15m > 0 else -7

score = max(-100, min(100, score))
```

## Direction classification

```python
if score >= 50:
    direction = "STRONG_BUY"
elif score >= 25:
    direction = "MODERATE_BUY"
elif score >= 8:
    direction = "WEAK_BUY"
elif score <= -50:
    direction = "STRONG_SELL"
elif score <= -25:
    direction = "MODERATE_SELL"
elif score <= -8:
    direction = "WEAK_SELL"
else:
    direction = "NEUTRAL"
```

## Store in debug_snapshots

Add safe migration:

```sql
ALTER TABLE debug_snapshots ADD COLUMN short_term_flow_breakdown_json TEXT;
```

Only if column does not exist.

Do not change `snapshots` schema.

`short_term_flow_breakdown_json` should contain:

```json
{
  "status": "experimental",
  "short_term_flow_pressure": 0.0,
  "short_term_flow_intensity": 0.0,
  "direction": "NEUTRAL",
  "flow_scale": "-100_to_100_neutral_0",
  "inputs": {
    "return_1m": 0.0,
    "return_5m": 0.0,
    "return_15m": 0.0,
    "range_5m": 0.0,
    "range_15m": 0.0,
    "volume_change_5m": 0.0,
    "volume_change_15m": 0.0
  },
  "components": {
    "return_1m_component": 0.0,
    "return_5m_component": 0.0,
    "return_15m_component": 0.0,
    "range_expansion_component": 0.0,
    "volume_acceleration_component": 0.0,
    "breakout_component": 0.0,
    "reversal_component": 0.0
  },
  "comparison": {
    "synthetic_flow_pressure": 0.7,
    "short_term_minus_synthetic": -0.7,
    "old_flow_reason": "weak_directional_components"
  },
  "reason": "neutral_short_term_flow"
}
```

## Replay alignment

Extend `replay_alignment_json` with:

- short_term_flow_pressure;
- comparison against synthetic_flow_pressure;
- alignment flags.

Flags:

```text
short_term_flow_active_but_legacy_flow_neutral
legacy_flow_active_but_short_term_flow_neutral
both_flow_layers_neutral
both_flow_layers_aligned_buy
both_flow_layers_aligned_sell
flow_layers_divergent
```

## Endpoints

Extend:

```text
GET /api/research/debug-snapshots/latest
GET /api/research/debug-snapshots/summary?from=...&to=...
```

Add short-term flow summary:

```json
{
  "short_term_flow": {
    "min": 0,
    "avg": 0,
    "max": 0,
    "distribution": {
      "STRONG_SELL": 0,
      "MODERATE_SELL": 0,
      "WEAK_SELL": 0,
      "NEUTRAL": 0,
      "WEAK_BUY": 0,
      "MODERATE_BUY": 0,
      "STRONG_BUY": 0
    },
    "max_positive_snapshot": {},
    "max_negative_snapshot": {},
    "avg_components": {},
    "max_components": {}
  }
}
```

## Forbidden

Do not use short_term_flow_pressure in:

- State Machine;
- ExecutionTimingEngine;
- SignalCluster;
- RegimeTransitionEngine;
- Event generation;
- FLOW_SURGE;
- Narrative;
- trading logic.

It is debug-only.

## Version update

Update:

```python
CODE_VERSION = "research_fix_2026_05_22_v21"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v21_experimental_short_term_flow_debug_v1"
```

## Acceptance SQL

After backend runs 30–60 minutes on a clean `mos_research.db`:

```sql
PRAGMA integrity_check;
PRAGMA quick_check;

SELECT COUNT(*) FROM snapshots;
SELECT COUNT(*) FROM debug_snapshots;
SELECT COUNT(*) FROM future_labels;
SELECT COUNT(*) FROM events;
SELECT COUNT(*) FROM ohlcv_candles;

SELECT code_version, research_schema_version, engine_patch_version, COUNT(*)
FROM snapshots
GROUP BY code_version, research_schema_version, engine_patch_version;

PRAGMA table_info(debug_snapshots);

SELECT COUNT(*)
FROM debug_snapshots
WHERE short_term_flow_breakdown_json IS NULL
   OR short_term_flow_breakdown_json = '';

SELECT timestamp_utc,
       current_state,
       execution_timing_state,
       short_term_flow_breakdown_json
FROM debug_snapshots
ORDER BY timestamp_utc DESC
LIMIT 5;
```

Expected:

```text
debug_snapshots ≈ snapshots
short_term_flow_breakdown_json filled
OHLCV still works
future_labels still works
synthetic_flow_pressure unchanged
no live logic changed
```
