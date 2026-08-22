# MOS_CODEX_RULES.md

## Core rules for Codex

Do not rely on chat memory.

Always read:

- `AGENTS.md`
- `/docs/mos/MOS_RESEARCH_CHARTER.md`
- `/docs/mos/MOS_HYPOTHESIS_REGISTRY.md`
- `/docs/mos/MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json`
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

## Permanent ETH research governance

- Use only ETH-prefixed hypothesis/result IDs for this repository.
- Keep ETH, BTC and SOL databases, thresholds, cutoffs and evidence separate.
- BTC/SOL findings may motivate an ETH hypothesis but cannot confirm it.
- Separate movement readiness/range from direction.
- Separate discovery, freeze, confirmation and promotion.
- Require causal same-contract joins, chronological splits, non-overlapping
  outcomes, day/week blocks, negative controls, full multiple-testing
  correction, 6/10/15 bps costs and latency stress.
- Retain negative, weakened, sign-reversed and technically blocked results.
- Do not reset a clean database to adopt a new freeze. Data at or before the
  cutoff remains discovery/baseline; only later data is eligible confirmation.
- Never promote research into live-entry behavior without a separate reviewed
  version and explicit owner approval.

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
CODE_VERSION = "eth_fork_2026_08_14_v1_from_btc_v70"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v73_deribit_valid_iv_diagnostic_key"
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
