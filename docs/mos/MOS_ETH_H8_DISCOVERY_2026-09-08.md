# ETH-H8 discovery report — 2026-09-08

Status: `DISCOVERY` / `NOT_READY` / `TECHNICALLY_BLOCKED`. This is an
exploratory replay, not confirmation and not a trading result.

## Scope and isolation

- Asset: ETH only, `ETHUSDT` futures and ETH option observations.
- Archive: `mos_interval_2026-08-29T045600Z_to_2026-09-08T160100Z_210a8c73.zip`.
- Archive SHA-256:
  `C3967758501A1E6364A2FC17FD359B22D6A1E2985591360C87BD6171653A64F5`.
- Analysis interval: `[2026-08-29 04:56:00Z, 2026-09-08 16:01:00Z)`.
- Calendar duration: 10.4618 days; strict healthy common duration: 10.0903 days.
- All replay files were created in a temporary directory. The source ZIP,
  live ETH code, live ETH databases and BTC project were not changed.
- The BTC H8 definition was used only as economic inspiration. No BTC sample,
  outcome, threshold result, cutoff or significance claim enters ETH evidence.

## Frozen economic question

After a new ETH `VOLATILITY_WITHOUT_DIRECTION` option-readiness event, does a
strict one-sided false sweep of the preceding price range predict reversal in
the next 30 minutes?

The transported geometry is explicit and now frozen prospectively for ETH-H8:

1. Deduplicate readiness events globally within 1,800 seconds.
2. Examine the final 900 seconds before the event against the preceding 900-second
   rolling reference.
3. `UPPER`: a one-minute high exceeds the prior rolling high by more than 10 bps
   and closes back below that prior high; map to SHORT.
4. `LOWER`: a one-minute low falls more than 10 bps below the prior rolling low
   and closes back above that prior low; map to LONG.
5. Reject `NONE` and `BOTH`.
6. Enter at the first complete ETHUSDT one-minute close at/after the event,
   hold 1,800 seconds, and stress 0/15/60-second delays.

The separate ETH preregistration freezes this definition at
`2026-09-08T18:30:38.626Z`; only ETH data strictly after that timestamp can be
confirmation data. See
`MOS_ETH_H8_FALSE_SWEEP_REVERSAL_PREREG_V1.json`.
Its SHA-256 is
`329962281504E28B445EBAC926339B91CD96701AD8ABFE48F6C611AF69610D5E`.

## Reconstructed replay population

The archive did not contain the optional `particle_shadow_v3.db`, so a
deterministic replay was rebuilt in temporary storage from the archived ETH
history and MOS databases. Replay counts were:

| Item | Count |
|---|---:|
| Source snapshots replayed | 4,799 |
| Contract observations | 4,503,267 |
| Particle observations | 2,715,625 |
| VWD candidate rows | 1,745 |
| VWD candidates before 1,800s dedup in analysis interval | 245 |
| VWD rows after dedup | 156 |
| VWD sweep classification: UPPER / LOWER / NONE / BOTH | 6 / 6 / 144 / 0 |
| Complete non-overlapping H8 trades | 12 |
| Independent UTC days / weeks | 7 / 3 |

The small number of strict sweeps is not a reason to relax the threshold.

## Primary VWD-conditioned reversal result

Values are percentage points per trade. Confidence intervals are exploratory
ETH UTC-day block intervals; they are not confirmation intervals and no
multiple-testing claim is made.

| Entry delay | n | Gross mean | Net mean 6 bps | Net mean 10 bps | Net mean 15 bps | Net total 15 bps | Day-block 95% CI at 15 bps | Sign-flip p | Positive days / weeks |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 0s | 12 | +0.085621% | +0.025621% | -0.014379% | **-0.064379%** | -0.772551% | [-0.234970%, +0.290044%] | 0.354465 | 42.86% / 66.67% |
| 15s | 12 | +0.099506% | +0.039506% | -0.000494% | **-0.050494%** | -0.605930% | [-0.204780%, +0.316746%] | 0.335866 | 42.86% / 66.67% |
| 60s | 12 | +0.171155% | +0.111155% | +0.071155% | **+0.021155%** | +0.253859% | [-0.177174%, +0.451649%] | 0.258274 | 42.86% / 66.67% |

At the preregistered primary delay of 0 seconds, expectancy is negative after
10 and 15 bps. The positive 60-second result is a latency-sensitive descriptive
change, with a confidence interval crossing zero and only 12 observations.

Risk diagnostics at 15 bps for the primary 0-second path: win rate 25.00%,
profit factor 0.6633, maximum drawdown -2.012374%, median net -0.209534%,
minimum net -0.611797%, maximum net +0.797134%.

## Event-level audit at the primary delay

| UTC event | Sweep and reversal | Gross | Net at 15 bps |
|---|---|---:|---:|
| 2026-08-30 16:45:43 | UPPER → SHORT | +0.559991% | +0.409991% |
| 2026-08-31 09:57:58 | UPPER → SHORT | -0.105496% | -0.255496% |
| 2026-08-31 12:49:07 | LOWER → LONG | +0.101397% | -0.048603% |
| 2026-08-31 14:39:52 | UPPER → SHORT | -0.461797% | -0.611797% |
| 2026-09-02 03:19:12 | UPPER → SHORT | -0.039341% | -0.189341% |
| 2026-09-02 05:20:02 | UPPER → SHORT | -0.103356% | -0.253356% |
| 2026-09-02 08:46:27 | LOWER → LONG | -0.236735% | -0.386735% |
| 2026-09-02 10:57:09 | LOWER → LONG | +0.112682% | -0.037318% |
| 2026-09-03 13:42:28 | UPPER → SHORT | -0.079728% | -0.229728% |
| 2026-09-04 14:52:15 | LOWER → LONG | +0.464603% | +0.314603% |
| 2026-09-06 14:36:14 | LOWER → LONG | -0.131905% | -0.281905% |
| 2026-09-08 13:44:09 | LOWER → LONG | +0.947134% | +0.797134% |

Side split is asymmetric: UPPER→SHORT had n=6, gross mean -0.038288% and net
mean -0.188288% at 15 bps; LOWER→LONG had n=6, gross mean +0.209529% and net
mean +0.059529%. This is a descriptive split, not a direction selection.

## Controls

The continuation control uses exactly the same VWD timestamps and reverses the
H8 direction. The price-only control uses the same false-sweep geometry on the
30-minute grid without an option-readiness event. It is not valid to call the
VWD-minus-price-only difference significant here: the two control populations
are not an independent multi-week test set.

| Family / delay | n | Gross mean | Net mean 15 bps | Day-block 95% CI at 15 bps |
|---|---:|---:|---:|---|
| VWD reversal / 0s | 12 | +0.085621% | **-0.064379%** | [-0.234970%, +0.290044%] |
| VWD continuation / 0s | 12 | -0.085621% | -0.235621% | [-0.609292%, -0.067745%] |
| VWD reversal / 15s | 12 | +0.099506% | -0.050494% | [-0.204780%, +0.316746%] |
| VWD reversal / 60s | 12 | +0.171155% | +0.021155% | [-0.177174%, +0.451649%] |
| Price-only reversal / 0s | 12 | -0.090447% | -0.240447% | [-0.601086%, -0.049920%] |
| Price-only reversal / 15s | 12 | -0.090039% | -0.240039% | [-0.600597%, -0.051637%] |
| Price-only reversal / 60s | 12 | -0.063250% | -0.213250% | [-0.566496%, -0.066373%] |

Descriptively, VWD reversal is less negative than price-only reversal by about
0.176 percentage points at 0s, 0.190 points at 15s and 0.234 points at 60s.
That is an exploratory comparison only: both sides have 12 trades, seven days,
three weeks, and all intervals are far below the ETH confirmation gate.

The continuation control is negative at every delay and cost, which supports
the intended falsification contrast (reversal is less adverse than continuation)
but does not prove positive expectancy.

## Quality gates and blockers

- Archive/database integrity and ETH identity: passed.
- Option trades: 359,313 total (Bybit 321,176; Deribit 38,137), no dropped
  trades; valid IV 100% in the archived trade rows.
- ETHUSDT one-minute OHLCV: 15,065/15,065 complete rows.
- Health: 2,906/3,013 five-minute buckets healthy (96.45%); 10.0903 strict
  healthy common days, below the required 28 days for H8 confirmation.
- Causal Greek match: Bybit 99.659%; Deribit 70.354%, below the mandatory 80%
  gate.
- Stable surface: Deribit 2,876/2,993 strict rows (96.09%); Bybit stable
  surface is absent, so the two-exchange same-contract surface gate cannot pass.
- Outages/reconnects: the main joint reconnect interval around
  2026-08-30 21:04Z–2026-08-31 05:10Z and the Deribit recovery around
  2026-09-05 were excluded; no interpolation was used.
- No Holm/max-T confirmation claim is valid with 7 days, 3 weeks and 12 trades.

## H8 decision

ETH-H8 is recorded as `FROZEN_PENDING` for future confirmation but is currently
`NOT_READY` and `TECHNICALLY_BLOCKED`. The inspected archive weakens the BTC-
motivated prior: the ETH primary 0-second path is negative after normal 10/15
 bps costs, and the 60-second positive result is not robust. The reversal-versus-
continuation contrast is retained as negative/control evidence, not as an entry.

Next action: continue untouched ETH collection after the new H8 cutoff; repair
or complete causal Deribit and Bybit matched-surface coverage before attempting
confirmation; then require the full 28-day/4-week/50-trade/quality/multiple-
testing gate. Entry logic does not change.
