# MOS ETH Hypothesis Registry

Last updated: 2026-08-15

Latest audited ETH archive: `mos_baseline_2026-08-14T141427Z.zip`

Clean common duration: 0.12215 days

Latest live observation (not an audited archive): 13,105 option trades

Frozen ETH-H7 protocol SHA-256:
`E184B0C6FAD1E7849C9C2EA94A0882C479E9007BF61CD3ED1D52DEF9B23C906A`.

ETH-H7 freeze and eligible-data cutoff:
`2026-08-15T18:19:19.565Z`.

This file is the durable, asset-isolated memory of the ETH research program. A
failed, weakened or sign-reversed hypothesis is retained; ETH IDs are never
silently reused. BTC and SOL results are context only and are not evidence in
this registry.

## Confirmatory registry

| ID | Hypothesis | Frozen evidence | Current state | Latest ETH result | Next gate |
|---|---|---|---|---|---|
| ETH-H1 | VWD/ETH option structure predicts greater future ETH range without direction | Particle Logic v3 and ETH archive protocol | NOT_READY | 2 independent VWD episodes; one complete 30m range 0.333%, second complete only 15m range 0.302%; one UTC day | At least 14 clean ETH days and adequate day blocks; no retuning |
| ETH-H2 | Strict one-sided 30m/15m/2bps false sweep precedes greater future ETH range | `MOS_OPTION_FLOW_PREREG_V1.json` | NOT_READY | 2 non-overlapping 30m outcomes, mean range 0.422%; 1 non-overlapping 60m outcome, range 0.681%; direction control negative at 6/10/15 bps | More independent ETH days and sweeps without threshold changes |
| ETH-H3 | Prior public ETH option-trade intensity adds future-range information | `MOS_OPTION_FLOW_PREREG_V1.json` | NOT_READY | Only 0.10417 clean full-lookback days; descriptive signs change across lookbacks/exchanges | 14 healthy dual-exchange ETH days and full frozen family |
| ETH-H4 | Absolute gamma/vega plus DTE/moneyness/exchange add future-range information | `MOS_OPTION_FLOW_PREREG_V1.json` | NOT_READY / TECHNICALLY_BLOCKED | Non-overlapping n=1–5; signs unstable; Deribit live causal Greek match 66.48% versus required 80% | 14 healthy days and valid Deribit causal coverage on untouched ETH data |
| ETH-H5 | Bybit/Deribit ETH option-flow agreement improves range inference | `MOS_OPTION_FLOW_PREREG_V1.json` | NOT_READY | Paired raw observations 15/10/8 at 5m/15m/30m; signed-delta agreement 46.7%/60.0%/37.5%; no independent-day threshold test | Prior-day thresholds, independent days and valid source-specific quality |
| ETH-H6 | Matched ETH option surface improves 60m range after strict sweep | Strict matched-surface definition and H6 gate | TECHNICALLY_BLOCKED | 8 overlapping sweep timestamps, 0 with eligible matched surfaces on both exchanges; Deribit strict surface pairs 0/34 | 28 ETH days, >=100 sweeps, >14 test days and >=95% same-contract overlap |
| ETH-H7 | ETH option readiness selects expansion; preceding ETH trend quality conditions continuation versus reversal | `MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json` | FROZEN_PENDING | No eligible post-freeze ETH data inspected; all earlier ETH data remain discovery/baseline | Only ETH data strictly after 2026-08-15T18:19:19.565Z; 28-day/100-event gate |

## BTC governance context retained without evidence transfer

BTC governance motivated the economic definition of ETH-H7. BTC observations,
effect sizes, thresholds, confidence intervals and evidence states do not count
toward any ETH sample size or confirmation gate. ETH-H7 has its own freeze
timestamp, protocol hash and eligible ETH window.

## Direction findings retained as negative or null evidence

| ID | Test | State | ETH evidence retained |
|---|---|---|---|
| ETH-D1 | Strict-sweep reversal direction control | NOT_READY | n=2 at 30m: gross -0.224%; net -0.284%/-0.324%/-0.374% at 6/10/15 bps; n=1 at 60m also negative |
| ETH-D2 | Particle directional-watch labels as entries | DISCOVERY | Two complete 30m observations +0.028% and -0.121%; mean gross -0.046%; no authorization or confirmed expectancy |
| ETH-D3 | Unconditional continuation/reversal after option readiness | FROZEN_PENDING | Neither direction is assumed; ETH-H7 will test both after its own cutoff |

## Discovery backlog

These are search families, not claims. A promising result receives a new ETH ID
and a frozen ETH protocol before future confirmation.

| Family | Search space | Required falsification |
|---|---|---|
| ETH-U1 Trend quality | efficiency, persistence, multi-horizon agreement, exhaustion, compression depth | matched ETH price-only continuation/reversal and random event times |
| ETH-U2 Option-flow sequences | bursts, persistence, sign flips, lead-lag, block/combo separation | shuffled within-day timestamps and exchange-label swaps |
| ETH-U3 Surface geometry | IV/skew/term/gamma curvature and migration | stable same-contract overlap and fixed-universe controls |
| ETH-U4 Contract cohorts | DTE, moneyness, call/put, liquidity and concentration | cohort-size and changing-universe controls |
| ETH-U5 Cross-exchange structure | Bybit/Deribit agreement, divergence and lead-lag | source availability, latency and stale-cache controls |
| ETH-U6 Particle grammar | ordered particle motifs, persistence and interaction | sequence permutation and simpler aggregate baselines |
| ETH-U7 Regime discovery | interpretable clusters and transition hazards | time-block replication and label-free stability |
| ETH-U8 Execution realism | delayed entry, spread/slippage, signal decay and crowding | 6/10/15 bps plus 0/15/60 second latency stress |
| ETH-U9 Alternative explanations | realized volatility, trend, time-of-day and expiry calendar | incremental value beyond ETH futures-only controls |

## Known technical blockers

- Latest audited Deribit same-contract Greek coverage: 66.48% versus Bybit
  99.82% and the frozen 80% minimum.
- Deribit adjacent surface Jaccard range: 0.598–0.610; strict required overlap:
  0.95. Deribit surface confirmation is technically blocked even though live
  transport and trade collection are healthy.
- `mos_research.snapshots.valid_iv_count` is zero in the audited archive while
  ATM IV and contract-level mark IV are present; treat this as a diagnostic
  telemetry inconsistency until separately resolved.
- Git branch/commit provenance is absent from the audited exporter manifest.

## Registry update rule

Every ETH archive audit updates the latest result and evidence state. New IDs
require an explicit causal definition. Results may move from pending to rejected
or confirmed only under the applicable frozen ETH protocol, but negative rows
are never deleted. No registry state alone authorizes a live-entry change.
