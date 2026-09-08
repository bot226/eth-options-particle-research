# MOS_NEXT_TASK.md

## Task: validate the independent ETH fork

## Goal

Prove that the ETH backend and frontend run independently from BTC, use only
ETH market channels and symbols, and write only project-local ETH databases.
Preserve the inherited BTC v70 formulas, schemas, heartbeat behavior, Research
Layer isolation, and read-only option-flow observer design.

## Runtime contract

- frontend: `http://127.0.0.1:5174`;
- backend: `http://127.0.0.1:8101`;
- Bybit option topic: `tickers.ETH`;
- Bybit linear topic and OHLCV symbol: `ETHUSDT`;
- Bybit public option trades: `publicTrade.ETH`;
- Deribit option trades: `trades.option.ETH.100ms`;
- Deribit index: `eth_usd`;
- canonical option IDs start with `ETH-`;
- runtime databases live only under this repository's `backend/data`;
- `stop_dashboard.bat` may stop only the process tree recorded by this ETH launcher.

## Acceptance

- backend starts on port 8101;
- frontend starts on port 5174 and proxies only to port 8101;
- `/` identifies the ETH service;
- `/api/research/diagnostics` reports
  `eth_fork_2026_08_14_v1_from_btc_v70`;
- Deribit and Bybit diagnostics expose ETH instruments and nonzero valid data
  after network warmup;
- SQLite `integrity_check = ok` for every ETH runtime database;
- BTC source commit and working tree remain unchanged;
- starting or stopping ETH does not start, stop, lock, or write any BTC process
  or database;
- all inherited and ETH-adapted tests pass.

## Research safety

Do not promote inherited thresholds as ETH evidence. Collect a clean ETH-only
archive and perform separate walk-forward validation before any scoring or
execution change.

## Next collection task after v92.2 tooling

1. Keep the existing ETH collector and databases running unchanged.
2. Export the next untouched interval with `export_research_interval.bat`,
   choosing the first UTC instant not already counted as new ETH evidence.
3. Audit it with `audit_eth_mos_archive.bat` and pass every earlier ETH interval
   with `--previous` when using the command directly.
4. Do not promote or retune ETH-H1 through ETH-H7 unless the corresponding
   frozen day/event/quality gates pass.

Legacy baseline ZIPs lacking embedded frozen protocols and explicit
analysis/support/carryover boundaries must be re-exported from the live ETH
databases if those time rows are still retained. They remain descriptive
artifacts, not interval-confirmation packages.
