# MOS ETH Research Charter

Status: permanent ETH project mandate

Authorized by the project owner: 2026-08-15

Scope: offline ETH research, replay, validation and research tooling

## Objective

The ETH research program must autonomously search for a statistically durable
and executable expectancy for ETH linear futures after fees, slippage and
latency.

The primary evidence is ETH option structure: per-contract trades, IV, Greeks,
OI, volume, matched-contract surface changes and Particle Logic lineage. ETH
futures price is used for context, direction hypotheses, controls, outcomes and
execution realism.

MOS remains a market-structure intelligence system. A research result is not a
live entry and must not silently change State Machine, candidates, execution or
manual trading.

## Asset isolation

- This charter governs ETH evidence only.
- ETH hypothesis IDs and results use the `ETH-` prefix.
- BTC and SOL definitions may be reused only as economic definitions or
  falsification context; their observations, thresholds, effect sizes and
  significance are never ETH evidence.
- Asset databases remain physically separate.
- Correlated BTC, ETH and SOL periods are not counted as independent periods.
- A threshold learned from BTC or SOL is not a frozen ETH threshold unless the
  ETH registry explicitly preregisters it before eligible ETH data arrives.

## Autonomous mandate

The research owner must not have to enumerate every hypothesis. For each clean
ETH archive, the research process must independently:

1. audit integrity, provenance, ETH identity and collection quality;
2. run every already-frozen ETH validator whose data gate is satisfied;
3. compare results with every earlier untouched ETH window and report sign
   changes;
4. search the declared discovery families for new causal hypotheses;
5. record negative, unstable and technically untestable results;
6. freeze promising new hypotheses before they see confirmation data;
7. specify missing evidence and the highest-value next ETH collection task;
8. preserve a reproducible report and update the ETH hypothesis registry.

## Mandatory hypothesis map

The autonomous search covers at least:

- movement readiness and future range magnitude;
- direction of expansion separately from movement readiness;
- trend before compression, trend continuation and trend exhaustion;
- false breaks, sweeps, failed auctions and returns into range;
- IV level, velocity, acceleration, skew and term structure;
- delta, gamma, vega, theta, OI and volume;
- signed option trade flow and trade intensity;
- DTE, moneyness, calls/puts, block/combo and individual contracts;
- Bybit/Deribit agreement, disagreement and lead-lag;
- wall and gamma-flip migration, concentration and dealer pressure;
- particle sequences, interactions, persistence and delayed effects;
- regime transitions, market-state conditioning and execution timing;
- interpretable nonlinear interactions and unsupervised regime discovery;
- falsification controls and alternative non-option explanations.

The list is a minimum, not a limit. New families may be added to the ETH
registry, but their confirmatory test must be frozen before eligible future ETH
data arrives.

## Two separate prediction problems

Every analysis must distinguish:

1. whether a material move is becoming more likely and how large it may be;
2. which direction that move may take.

The central composite hypothesis is:

> ETH option evidence may identify readiness for range expansion, while the
> quality and exhaustion state of the preceding ETH futures trend may condition
> direction.

Neither continuation nor reversal is assumed. Both must be evaluated at the
same timestamps against a neutral/no-edge control.

## Research stages

### 1. Quality gate

No inference is allowed until database integrity, timestamp continuity, causal
joins, source health and outcome coverage are audited. A technically degraded
family may be described but cannot be confirmed.

### 2. Discovery

Discovery may use broad factor enumeration, interactions, sequences,
clustering and interpretable machine learning. Its outputs are explicitly
exploratory and cannot be called an edge.

### 3. Freeze

A promising discovery becomes a versioned, ETH-specific preregistration
containing exact features, timestamps, thresholds, outcomes, costs, exclusions,
validation splits, multiple-testing family and promotion gates. Its eligible
ETH data start is strictly later than the freeze time.

### 4. Confirmation

Confirmation uses untouched chronological ETH data, non-overlapping outcomes,
day/week blocks, negative controls, realistic costs and the full declared
multiple-testing family. Failed confirmation remains in the registry.

### 5. Promotion

Research confirmation does not itself change live behavior. Promotion requires
a separate reviewed task, explicit owner approval, a new code version, shadow
operation and rollback criteria.

## Anti-leakage and anti-overfitting rules

- Every feature must exist at or before the decision timestamp.
- Greeks must come from the most recent earlier snapshot of the same ETH
  contract on the same exchange.
- Data age, chain overlap and path coverage must be explicit.
- Thresholds are fixed or learned from prior complete ETH UTC days only.
- Outcomes for a rule do not overlap unless the protocol explicitly models it.
- Train, validation and test splits follow time; random row splits are invalid.
- All searched variants belong to a declared correction family.
- Use Holm/FDR, shared-block max-T or an equivalent familywise control.
- Use day/week block confidence intervals and permutation tests.
- Report effect size and uncertainty, not only p-values.
- Run negative controls and futures-only alternatives at the same timestamps.
- Never lower a gate because the sample failed it.
- Never discard a negative, weakened or sign-reversed result from the registry.

## Economic validation

Every directional result must report at least:

- gross and net expectancy at 6, 10 and 15 bps round-trip costs;
- number of independent signals and their time distribution;
- win rate, profit factor, drawdown and tail loss;
- stability by UTC day, week and market regime;
- sensitivity to 0, 15 and 60 second execution delay and plausible slippage;
- comparison with price-only continuation, reversal and no-trade controls.

A movement/range result must report calibration, MAE/rank quality, top-range
precision and whether ETH option evidence adds value beyond ETH futures-only
controls.

## Evidence states

Only these states may be used:

- `DISCOVERY`: observed in ETH data already inspected; no confirmatory claim.
- `FROZEN_PENDING`: exact ETH test frozen; eligible future ETH data not yet
  sufficient.
- `NOT_READY`: implementation is valid but the ETH data gate is closed.
- `REJECTED_CURRENT_SAMPLE`: frozen ETH test failed on the current eligible
  sample.
- `TECHNICALLY_BLOCKED`: required causal ETH data quality is inadequate.
- `CONFIRMED_RESEARCH`: all frozen ETH statistical gates passed.
- `PROMOTED_SHADOW`: explicitly approved ETH shadow behavior only.
- `PROMOTED_LIVE`: separately reviewed and explicitly owner-approved ETH live
  behavior.

Words such as “confirmed”, “edge” and “profitable” are prohibited outside their
corresponding evidence state.

## Per-archive deliverable

Each ETH archive must produce:

1. integrity and provenance result;
2. asset, duration and clean-coverage result;
3. causal same-contract and matched-surface coverage;
4. frozen ETH hypothesis results;
5. discovery results clearly separated from confirmation;
6. comparison with earlier untouched ETH windows, including weakened or
   reversed effects;
7. registry updates and exact next gate;
8. a normalized asset-isolated report to the MOS Multi-Asset Research task;
9. a plain-language conclusion stating what may and may not change.

## Current operational decision

Continue the untouched ETH collection. Do not reset databases and do not change
thresholds or live entry logic. The raw-flow family requires at least 14 healthy
common ETH days. ETH-H6 keeps its stricter 28-day, 100-sweep and 14-test-day
gate. ETH-H7 uses only ETH data strictly after its own freeze timestamp.
