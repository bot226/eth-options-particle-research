# MOS_NEXT_TASK.md

## Task: v51 Particle Logic Shadow Replay v1

## Goal

Build an options-particle-first research layer from existing MOS databases while
keeping clean MOS behavior unchanged.

## Inputs

```text
history.db       per-contract OI, GEX by strike, term structure
mos_research.db  MOS context, 1m OHLCV, future context
manifest.json    dataset provenance
```

All input databases must be opened read-only.

## Output

One standalone `particle_shadow_v1.db` containing:

```text
shadow_runs
source_snapshots
particle_observations
particle_constellations
shadow_candidates
shadow_outcomes
```

## Particle families

- `OI_BUILD`, `OI_UNWIND` per contract;
- `GEX_BUILD`, `GEX_DECAY`, `GEX_SIGN_FLIP` per strike/component;
- `CALL_WALL_MIGRATION`, `PUT_WALL_MIGRATION`;
- `GAMMA_FLIP_MIGRATION`;
- `FRONT_IV_RISE`, `FRONT_IV_FALL`.

## Logic contract

- options particles create the structural hypothesis;
- price and synthetic flow may confirm but must not create the hypothesis;
- movement probability and direction evidence are separate;
- every score and blocker is stored;
- no look-ahead is allowed when generating a constellation or candidate;
- future OHLCV is used only by `shadow_outcomes` after the candidate exists.

## Forbidden

Do not modify or feed:

- State Machine;
- ExecutionTimingEngine;
- SignalCluster;
- RegimeTransitionEngine;
- VolatilityEngine;
- event generation;
- future_labels;
- live `snapshots` schema;
- manual trading or paper-trade opening.

## Acceptance

- source database hashes unchanged before/after replay;
- generated database passes `PRAGMA integrity_check`;
- replay is deterministic for the same database hashes and logic version;
- excluded/stale source snapshots do not generate particles;
- all shadow candidates have an explainable constellation and blockers;
- 60/120/240-minute outcomes are stored when OHLCV coverage permits.
