# MOS Particle Materiality Filter v3

Particle Logic v3 keeps every source row in `contract_observations`, but emits
contract particles only for changes that pass deterministic materiality gates.
The filter is offline and does not alter live MOS, collection, or trading logic.

## Default gates

| Metric | Absolute floor | Relative floor |
|---|---:|---:|
| mark IV | 0.001 (0.10 vol-point) | 0.25% |
| rolling 24h volume | 0.10 contracts | none |
| delta | 0.001 (0.10 delta-point) | none |
| gamma magnitude | 0.0000001 | 1.00% |
| vega magnitude | 0.10 | 0.50% |
| theta magnitude | 0.05 | 0.50% |

A change must pass both configured floors. A transition from a zero previous
value is evaluated by its absolute floor. After filtering, at most 48 changes
per metric and five-minute snapshot are emitted, ranked deterministically by
their distance above both floors.

These are measurement/materiality gates, not candidate thresholds. They were
not selected from future returns and do not make entry candidates easier.

## Audit

`particle_filter_audit` stores, for every snapshot and metric:

- observed non-zero changes;
- changes passing the materiality floors;
- emitted particles;
- changes suppressed below the floors;
- changes suppressed by the per-snapshot cap;
- the exact floors and cap used.

Each emitted contract particle also stores its materiality score and filter
configuration in `features_json`.

```sql
SELECT metric_name,
       SUM(observed_changes),
       SUM(material_changes),
       SUM(emitted_changes),
       SUM(suppressed_below_threshold),
       SUM(suppressed_by_cap)
FROM particle_filter_audit
GROUP BY metric_name;
```

Raw `contract_observations` counts must remain unchanged between v2 and v3 for
the same input archive.
