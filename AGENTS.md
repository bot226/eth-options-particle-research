# AGENTS.md — MOS Backend Development Rules

This repository contains **MOS — Institutional Market Structure Intelligence System** for BTC options.

Before making any code changes, always read:

1. `/docs/mos/MOS_CONTEXT.md`
2. `/docs/mos/MOS_CURRENT_STATE.md`
3. `/docs/mos/MOS_DATA_VALIDATION_CRITERIA.md`
4. `/docs/mos/MOS_CODEX_RULES.md`
5. `/docs/mos/MOS_NEXT_TASK.md`

## Core philosophy

MOS does **not** predict price.

MOS analyzes market structure, regime, dealer positioning, Gamma/GEX, IV/skew/term structure, liquidity, synthetic flow pressure, regime transition probability, execution environment quality, replay data, and future Market Memory.

Do **not** turn MOS into a buy/sell signal dashboard.

## Architecture rules

- Backend owns business logic.
- Frontend only renders.
- Strict single-responsibility engines.
- READ-ONLY intelligence layers.
- Replay-first architecture.
- Deterministic research logging.
- No hidden frontend calculations.
- No monolithic god-file refactors.

## Current stable Research Layer version

```python
CODE_VERSION = "research_fix_2026_08_03_v58"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v66_deribit_ws_qualified_liveness"
```

## Known working components

Do not break these:

- snapshots writing;
- debug_snapshots writing;
- future_labels writing;
- OHLCV collector;
- iv_velocity;
- event_payload_json;
- phase_context;
- warmup suppression;
- data_quality;
- active_sources;
- market_phase_hash.

## Protected components

Never change these unless the current task explicitly requests it:

- State Machine formulas;
- ExecutionTimingEngine scoring;
- SignalCluster formula;
- LiquidityVoidEngine formula;
- RegimeTransitionEngine formula;
- VolatilityEngine;
- ResearchLogger snapshots schema;
- MarketState enum;
- flow scale.

## Versioning rule

Every code change must update:

`backend/engine/version.py`

Do not create duplicate version files.

## Required verification after every change

- Backend starts.
- `/api/research/diagnostics` returns correct version.
- snapshots count increases.
- debug_snapshots count increases.
- ohlcv_candles count increases.
- future_labels appear after 30 minutes.
- SQLite `integrity_check = ok`.
- no `active_sources = []`.
- no `data_quality = 0.0`.
- no `current_state = UNKNOWN`.

Current next task is always described in:

`/docs/mos/MOS_NEXT_TASK.md`
