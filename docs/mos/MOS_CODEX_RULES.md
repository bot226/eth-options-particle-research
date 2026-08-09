# MOS_CODEX_RULES.md

## Core rules for Codex

Do not rely on chat memory.

Always read:

- `AGENTS.md`
- `/docs/mos/MOS_CONTEXT.md`
- `/docs/mos/MOS_CURRENT_STATE.md`
- `/docs/mos/MOS_DATA_VALIDATION_CRITERIA.md`
- `/docs/mos/MOS_NEXT_TASK.md`

before making changes.

## Work style

Make small, testable changes.

Do not rewrite MOS.

Do not perform large refactors unless explicitly requested.

Do not change working components.

Do not improve formulas unless the task explicitly says so.

Do not lower thresholds to make signals appear.

Do not create artificial events.

Do not tune system to one quiet or one active market sample.

After completing code or documentation changes, commit and push the finished work
to the current working branch unless the user explicitly asks not to push.

## Protected components

Do not modify without explicit task approval:

- State Machine formulas;
- ExecutionTimingEngine scoring;
- SyntheticOrderflowEngine formula;
- LiquidityVoidEngine formula;
- SignalCluster formula;
- RegimeTransitionEngine formula;
- GammaSurfaceEngine formula;
- VolatilityEngine;
- OHLCV collector;
- future_labels logic;
- snapshots schema;
- event logic;
- MarketState enum;
- flow scale.

## Current stable behavior to preserve

- snapshots writing;
- debug_snapshots writing;
- future_labels;
- OHLCV collector;
- iv_velocity;
- event_payload_json;
- phase_context;
- warmup suppression;
- active_sources;
- data_quality;
- bad_snapshots = 0;
- market_phase_hash.

## Versioning

Update only:

`backend/engine/version.py`

Current development branch:

```python
CODE_VERSION = "research_fix_2026_08_09_v66"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v68_option_trade_flow_quality_history"
```

Do not create duplicate version files.

## Research Layer safety

Research debug layers are allowed to observe and record.

They must not change current_state, execution_timing_state, signal_cluster_score, expansion_probability, events, FLOW_SURGE or trading logic unless explicitly promoted after validation.

## Debug principle

Every score should be explainable.

If an engine outputs a weak value, debug should show inputs, components, thresholds, guards, reasons, why_not and blocked transitions.

## Response after changes

After each task, report:

1. Files changed.
2. Functions added/modified.
3. What was not touched.
4. Version update.
5. How to verify via SQL.
6. How to verify via API.
7. Risks remaining.
8. Whether backend needs clean DB before testing.
