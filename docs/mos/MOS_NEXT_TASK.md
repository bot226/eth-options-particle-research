# MOS_NEXT_TASK.md

## Task: validate v52 Particle Contract Lineage v2 collection

## Goal

Collect and validate source-level option contract metrics while keeping clean
MOS behavior and all live formulas unchanged.

## Inputs

```text
history.db       structural snapshots plus option_contract_snapshots
mos_research.db  MOS context, 1m OHLCV, future context
manifest.json    dataset provenance
```

All input databases must be opened read-only.

## Output

One standalone `particle_shadow_v2.db` containing the v1 tables plus:

```text
shadow_runs
source_snapshots
particle_observations
particle_constellations
shadow_candidates
shadow_outcomes
contract_observations
particle_contract_links
constellation_particle_links
candidate_particle_lineage
```

## New observation-only particle families

- contract IV rise/fall;
- contract rolling-volume rise/fall;
- contract delta up/down;
- contract gamma, vega, and theta magnitude build/decay.

## Logic contract

- options particles create the structural hypothesis;
- price and synthetic flow may confirm but must not create the hypothesis;
- movement probability and direction evidence are separate;
- every score and blocker is stored;
- no look-ahead is allowed when generating a constellation or candidate;
- future OHLCV is used only by `shadow_outcomes` after the candidate exists.
- contract-metric particles remain observation-only until validated;
- candidates without option-particle lineage are forbidden.

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
- all shadow candidates have at least one `candidate_particle_lineage` row;
- new collector snapshots contain per-contract IV, volume, and Greeks coverage;
- v1 archives without contract rows still replay successfully;
- 60/120/240-minute outcomes are stored when OHLCV coverage permits.
