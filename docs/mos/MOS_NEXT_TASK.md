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
