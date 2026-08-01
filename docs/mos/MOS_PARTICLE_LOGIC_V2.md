# MOS Particle Logic Shadow v2

Particle Logic v2 extends the replay dataset with source-level option contract
observations and deterministic candidate lineage. It remains an offline,
read-only research layer and does not feed live MOS decisions.

## Collection contract

Every five-minute `history.db` snapshot may now include rows in
`option_contract_snapshots` for each exchange and canonical contract:

- OI and rolling 24-hour volume;
- mark, bid, and ask IV;
- delta, gamma, vega, and theta;
- mark and underlying prices;
- exchange, source symbol, expiry, strike, and option type.

The new table is additive. The existing `snapshots` table and every live MOS
formula remain unchanged. Old databases without this table remain replayable.

## Replay contract

The standalone shadow database adds:

- `contract_observations`: immutable copies of source contract rows;
- `particle_contract_links`: direct metric and OI provenance;
- `constellation_particle_links`: every particle used or observed by a structure;
- `candidate_particle_lineage`: ranked evidence behind every candidate.

Per-contract IV, volume, delta, gamma, vega, and theta changes are emitted as
`CONTRACT_*` particles. In v2 they are marked `OBSERVATION_ONLY`: they are
collected, replayed, and evaluated, but they do not increase candidate scores.
Promotion into scoring requires walk-forward evidence across multiple regimes.

Candidates with no option particles are not emitted. This is a provenance
guard, not a threshold change.

## SQL verification

```sql
-- On the collector history.db
SELECT COUNT(*), MIN(ts), MAX(ts)
FROM option_contract_snapshots;

SELECT exchange,
       COUNT(*) AS rows,
       SUM(mark_iv IS NOT NULL AND mark_iv > 0) AS valid_iv,
       SUM(volume_24h IS NOT NULL) AS valid_volume,
       SUM(delta IS NOT NULL AND gamma IS NOT NULL
           AND vega IS NOT NULL AND theta IS NOT NULL) AS valid_greeks
FROM option_contract_snapshots
GROUP BY exchange;

-- On particle_shadow_v2.db
SELECT value FROM schema_metadata WHERE key = 'particle_logic_version';

SELECT COUNT(*)
FROM shadow_candidates c
WHERE NOT EXISTS (
    SELECT 1 FROM candidate_particle_lineage l
    WHERE l.candidate_id = c.candidate_id
);

SELECT c.candidate_id, l.evidence_rank, p.particle_type,
       l.evidence_role, l.component,
       l.movement_contribution, l.direction_contribution,
       l.included_reason
FROM shadow_candidates c
JOIN candidate_particle_lineage l USING (candidate_id)
JOIN particle_observations p USING (particle_id)
ORDER BY c.timestamp_utc, l.evidence_rank;
```

The missing-lineage query must return zero.
