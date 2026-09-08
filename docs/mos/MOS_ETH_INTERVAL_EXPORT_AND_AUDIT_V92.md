# ETH MOS interval export and audit v92.2

Status: research-only infrastructure

This implementation packages and audits independent ETH evidence without
changing the collector, live-entry logic, formulas, thresholds, or source
databases. It was manually adapted from the BTC infrastructure; no BTC
hypothesis, cutoff, result, observer, sample, or protocol hash is present in
the ETH contract.

## Interval exporter

Run:

```powershell
python -m backend.scripts.research_interval_dataset_exporter `
  --from 2026-09-01T00:00:00Z
```

Or double-click `export_research_interval.bat`.

The archive ends at the last fully closed, verified Bybit `ETHUSDT` one-minute
candle. Every database table is sliced with a half-open `[start,end)` rule.
Seven days before `analysis_start` are support context and never enlarge the
new sample. The 720-minute carryover boundary may only mature an already-known
event. Stable source IDs prevent duplicate evidence across adjacent exports.

Required databases:

- `mos_research.db`;
- `history.db`;
- `option_trade_flow.db`;
- `mos_manual.db`.

`particle_shadow_v3.db` is optional. If absent, the audit reconstructs the
same causal Particle Logic lineage from the mandatory `history.db` and
`mos_research.db`. When both sources overlap, exact candidate-field parity is
mandatory.

Required immutable ETH protocols:

- `MOS_OPTION_FLOW_PREREG_V1.json`, SHA-256
  `57B0D1FA0D988F5BCCEE5B674E8A8461E1667D36D7DFA7204FEA5A7D5D723217`;
- `MOS_TREND_BEFORE_COMPRESSION_PREREG_V1.json`, SHA-256
  `E184B0C6FAD1E7849C9C2EA94A0882C479E9007BF61CD3ED1D52DEF9B23C906A`.

The exporter refuses a ZIP on a missing database or protocol, hash mismatch,
mixed asset, missing/duplicate `ETHUSDT` minute, current-minute leakage,
SQLite failure, foreign-key failure, or declared relational orphan.

## Full archive audit

Run:

```powershell
python -m backend.scripts.eth_mos_archive_audit `
  D:\MOS_Exports\mos_interval_....zip `
  --previous D:\MOS_Exports\previous_interval.zip `
  --output-dir D:\MOS_Exports\audits
```

Or drag the ZIP onto `audit_eth_mos_archive.bat`. The batch also updates only
the machine-delimited latest-audit pointers in the ETH registry, current-state,
and next-task documents; it never rewrites frozen evidence rows.

The audit emits separate machine JSON and human Markdown. It checks:

- ZIP, manifest SHA/size, SQLite quick/integrity/FK and relational links;
- ETH identity (`ETHUSDT`, `ETH-*`) with fail-closed mixed-asset handling;
- analysis/support/carryover separation and `[start,end)` boundaries;
- collector sessions, health gaps, outages, drops and clock anomalies;
- live versus backfill trades and past-only same-contract Greek coverage;
- stable-universe gap and 95% overlap rules by exchange;
- completed versus pending future horizons;
- exact overlap removal against supplied earlier ETH archives;
- every registered `ETH-H1` through `ETH-H7` without retuning;
- 5/10/15/20/30/45/60/90/120/180/240/300/360/480/600/720-minute profiles.

Only horizons already registered for a hypothesis are confirmatory. All other
horizons are labelled `diagnostic_only_not_selection`; they cannot be used to
choose a new horizon on the same data. The complete frozen option-flow family
retains Holm and shared-day max-T correction when its quality/sample gate is
open. Costs remain 6/10/15 bps. Incomplete outcomes remain pending, never zero.

## Old archive compatibility

Legacy `mos_baseline_*.zip` archives remain usable for descriptive replay, but
they are not complete interval-confirmation artifacts when they lack embedded
protocols, explicit analysis/support/carryover boundaries, or the 720-minute
maturity boundary. Re-export the corresponding interval from the live source
databases when those rows are still available. Never merge physical BTC, ETH,
or SOL databases to fill a missing ETH interval.
