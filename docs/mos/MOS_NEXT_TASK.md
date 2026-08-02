# MOS_NEXT_TASK.md

## Task: validate v53 Particle Materiality Filter v3

## Goal

Suppress contract-level micro-noise in offline replay while retaining every raw
contract observation and keeping live MOS unchanged.

## Inputs

```text
history.db       structural snapshots plus option_contract_snapshots
mos_research.db  MOS context, 1m OHLCV, future context
manifest.json    dataset provenance
```

All input databases must be opened read-only.

## Output

One standalone `particle_shadow_v3.db` containing the v2 tables plus:

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
particle_filter_audit
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
- materiality floors and the per-metric cap are stored for every snapshot;
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
- v2 and v3 have identical `contract_observations` counts on the same archive;
- every emitted contract particle passes its recorded materiality gate;
- emitted contract particles never exceed 48 per metric per snapshot;
- v1 archives without contract rows still replay successfully;
- 60/120/240-minute outcomes are stored when OHLCV coverage permits.
