# MOS_CONTEXT.md

## MOS — Institutional Market Structure Intelligence System

MOS is a Market Operating System for ETH options market structure intelligence.

MOS does **not** predict price.

MOS analyzes what the market is doing now, what regime it is in, what regime it may transition into, who controls the market, where the structure is fragile, where range expansion may become possible, and whether the environment is suitable for execution.

MOS should be:

- institutional market structure intelligence terminal;
- historical replay laboratory;
- future Market Memory foundation.

## Core principles

- Strict single-responsibility engines.
- READ-ONLY intelligence layers.
- Replay-first architecture.
- Market structure, not price prediction.
- Deterministic historical replay.
- Future probabilistic analysis support.
- Backend owns business logic.
- Frontend only renders.

Frontend must not calculate regimes, confidence, squeeze risk, execution timing, or business logic.

Frontend should render market_state, events, replay, visual layers and charts.

## Main backend contract

Main object:

```python
market_state = {
    "timestamp": "...",
    "state_machine": {
        "current_state": "...",
        "previous_state": "...",
        "candidate_state": "...",
        "transition_state": "...",
        "transition_speed": "...",
        "state_persistence_sec": 0,
        "regime_stability": "..."
    },
    "gamma": {},
    "volatility": {},
    "skew": {},
    "liquidity": {},
    "flow": {},
    "meta": {},
    "scenario": {},
    "execution": {},
    "events": []
}
```

Advanced intelligence container:

```python
advanced_intelligence = {
    "status": "experimental",
    "phase_1": {
        "gamma_surface": {},
        "liquidity_voids": {},
        "dealer_hedging": {},
        "regime_transition": {}
    },
    "phase_2": {
        "term_structure": {},
        "synthetic_orderflow": {},
        "breakout_timing": {},
        "execution_timing": {}
    }
}
```

Phase 1 and Phase 2 are READ-ONLY: they calculate, explain, score and diagnose, but do not directly change current_state, bias, execution quality or trading logic unless explicitly promoted after validation.

## State Machine

```python
class MarketState(Enum):
    PINNING = "PINNING"
    COMPRESSION = "COMPRESSION"
    TRANSITION = "TRANSITION"
    BREAKOUT_SETUP = "BREAKOUT_SETUP"
    HEDGE_CHASE = "HEDGE_CHASE"
    EXPANSION = "EXPANSION"
    SHORT_SQUEEZE = "SHORT_SQUEEZE"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    EXHAUSTION = "EXHAUSTION"
    REBALANCE = "REBALANCE"
    PANIC = "PANIC"
```

UNKNOWN may remain in enum for compatibility, but should not be actively emitted.

Rule:

```text
UNKNOWN → TRANSITION
```

Initial state:

```python
_current_state = MarketState.TRANSITION
```

State Machine should answer not only what market is doing, but what it is transitioning into.

## Engines

Base engines:

- gamma_engine.py
- volatility_engine.py
- skew_engine.py
- liquidity_engine.py
- flow_engine.py
- meta_state_engine.py
- scenario_engine.py
- execution_engine.py
- state_engine.py
- narrative_engine.py

`state_engine.py` is an orchestrator only. It should collect outputs, maintain temporal memory, run state machine, emit events and build unified market_state. It must not become a god file.

## Temporal Memory

Use:

```python
market_state_history = deque(maxlen=500)
```

For state persistence, transition velocity, regime deterioration, volatility velocity, skew acceleration, gamma instability and pressure buildup.

Main idea: transition is more important than snapshot.

## Research Layer databases

### history.db

Slow structural database. Interval: every 5 minutes. Stores OI snapshots, GEX, PDF, term structure, call wall, put wall, dealer positioning and long-term structural states.

### mos_research.db

Fast research database. Interval: about every 15–20 seconds. Stores snapshots, debug_snapshots, events, future_labels, bad_snapshots, bookmarks, event_fingerprint_cache and ohlcv_candles.

Meaning:

```text
history.db = what was on the market
mos_research.db = what the system thought at that moment
```

## Current direction

Current phase is Research Layer stabilization and diagnostic replay. Do not build Market Memory yet.

First prove stable clean collection, debug layer, OHLCV alignment, future labels, event timing and score explanations.
