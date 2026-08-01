# MOS Particle Logic Shadow v1

Particle Logic v1 is an offline, replay-first research engine. It converts the
option structure already stored by MOS into time-local particles and tests new
structural hypotheses without changing clean MOS.

## Safety boundary

The replay opens source databases with SQLite `mode=ro`, verifies their hashes
before and after processing, and writes only a new standalone shadow database.
It has no API integration and no import path from live State Machine or manual
trading.

## Run

From the project root:

```powershell
python backend/scripts/run_particle_shadow_replay.py `
  C:\path\to\mos_baseline_YYYY-MM-DDTHHMMSSZ.zip
```

Alternatively, drag a MOS dataset ZIP onto:

```text
run_particle_shadow_replay.bat
```

Default output:

```text
mos_baseline_..._particle_shadow_v1.db
mos_baseline_..._particle_shadow_v1.summary.json
```

Existing output is never overwritten unless `--replace` is explicitly passed.

## Interpretation

`particle_observations` contains factual changes, not trade signals. OI changes
do not reveal whether options were bought or sold, so their directional weight
is deliberately small. Wall and gamma-flip migration provide stronger but still
probabilistic directional evidence.

`particle_constellations` separates:

- `movement_score`: structural activity and volatility potential;
- `direction_score`: signed option-structure evidence;
- `trust_score`: data quality, source count, and chain continuity.

Initial constellation labels:

- `PINNING_REVERSION`;
- `NEGATIVE_GAMMA_BREAKOUT`;
- `VOLATILITY_WITHOUT_DIRECTION`;
- `UNCONFIRMED_MOS_EXPANSION`;
- `NO_CLEAR_CONSTELLATION`.

`shadow_candidates` are research observations only:

- `VOLATILITY_WATCH`;
- `DIRECTIONAL_WATCH`;
- `SHADOW_ENTRY_CANDIDATE`.

Consecutive five-minute observations are not independent samples. Repeated
observations in the same setup/direction/level zone share `candidate_key`;
`candidate_is_new=1` marks the beginning of a new 30-minute episode.
The JSON summary reports both raw observation outcomes and episode-start
outcomes; use episode outcomes for early statistical comparisons.

No threshold should be loosened merely to create more candidates. Calibration
must use walk-forward data across multiple market regimes.

## Outcome contract

Future OHLCV is attached only after candidate generation. The database stores
returns and direction-adjusted MFE/MAE for 5, 15, 30, 60, 120, and 240 minutes.
This covers trades like the first MOS paper trade that stopped after 62 minutes
and therefore could not be evaluated by the original 30-minute labels alone.

## SQL verification

```sql
PRAGMA integrity_check;
PRAGMA quick_check;

SELECT status, logic_version, counts_json
FROM shadow_runs;

SELECT particle_type, COUNT(*)
FROM particle_observations
GROUP BY particle_type
ORDER BY COUNT(*) DESC;

SELECT structure_label,
       COUNT(*),
       AVG(movement_score),
       AVG(ABS(direction_score)),
       AVG(trust_score)
FROM particle_constellations
GROUP BY structure_label;

SELECT candidate_status, setup_family, direction, COUNT(*)
FROM shadow_candidates
GROUP BY candidate_status, setup_family, direction;

SELECT c.setup_family,
       c.direction,
       COUNT(*),
       AVG(o.return_60m),
       AVG(o.mfe_60m_pct),
       AVG(o.mae_60m_pct)
FROM shadow_candidates c
JOIN shadow_outcomes o USING (candidate_id)
GROUP BY c.setup_family, c.direction;
```

## Additional collection needed for v2

Keep the existing MOS option collector. Extend persistence, rather than adding
a competing collector, with per-contract mark/bid/ask price, mark/bid/ask IV,
delta, gamma, vega, theta, volume/turnover, forward price, source timestamp,
and latency. Use one-minute storage near spot and immediate full-chain snapshots
when MOS emits an event or manual candidate.

Also preserve exact lineage from manual candidate to research snapshot, event,
reaction, and the candles used for confirmation. These additions improve audit
quality and do not need to alter live decision formulas.
