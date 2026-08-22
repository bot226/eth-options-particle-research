# MOS stable surface universe v71

## Why it exists

The prior full-chain Deribit snapshots mixed genuine IV/GEX/OI movement with
contracts entering and leaving the fresh cache. Median adjacent membership was
well below the frozen 95% gate, so aggregate changes could not be interpreted
causally.

v71 does not replace or clean the raw chain. It creates a second, research-only
panel with explicit membership and quality evidence.

## Invariants

1. One `universe_id` names one exact ordered set of ETH option symbols.
2. Membership is fixed within the epoch and persisted across restart.
3. The target is 240 ATM-focused contracts balanced across expiries.
4. Normal REST tail rotation cannot enter the panel.
5. An epoch rotates after 24 hours or when a member no longer exists.
6. A snapshot is valid only when at least 95% of target contracts have fresh,
   complete and successfully normalized IV plus all four Greeks.
7. Invalid snapshots retain quality metadata but no panel contract rows.
8. Confirmatory comparisons require the same `universe_id` at both endpoints.

## Storage

- `option_surface_universes`: immutable membership and selection provenance.
- `option_surface_snapshots`: coverage and validity for every observation.
- `option_surface_contract_snapshots`: contract metrics for valid observations.

The existing `option_contract_snapshots` table remains the untouched raw
full-chain record. Public option trades remain in `option_trade_flow.db`.

## Interpretation

The 95% rule controls missing observations inside a fixed panel. It is not a
license to compare different universes. A rotation creates a boundary: analysis
may work within either epoch, but an adjacent change must not cross the boundary.

The first valid collector snapshot after deployment starts eligible v71 surface
evidence. Older surface findings remain discovery evidence and cannot confirm
H6.
