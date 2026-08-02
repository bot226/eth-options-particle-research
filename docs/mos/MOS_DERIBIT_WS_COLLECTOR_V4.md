# MOS Deribit WebSocket Option Ticker Collector v4/v5

## Why it exists

On the collector host, Deribit `get_instruments` returned 866 BTC option
instruments, but `get_book_summary_by_currency` exhausted three HTTP attempts
and returned no rows after approximately 49 seconds. The live MOS manager has
an eight-second adapter guard, so Deribit was always excluded before the bulk
request could finish.

## Data path

```text
REST get_instruments
    -> active BTC option names
    -> WebSocket ticker.<instrument>.agg2
    -> per-instrument raw ticker cache
    -> existing InstrumentNormalizer
    -> existing MultiExchangeDataManager
    -> existing option_contract_snapshots every five minutes
```

The ticker stream contains open interest, 24-hour statistics, mark/bid/ask IV,
underlying and option prices, plus nested delta, gamma, vega, and theta.

## Safety

- No private API key is used.
- The live poll path performs no Deribit bulk REST call.
- Cache older than 30 seconds is excluded.
- A partially warmed cache is excluded until at least 70% of discovered
  instruments have delivered ticker snapshots.
- Instrument discovery retries independently from message handling.
- Subscription requests are bounded to 500 channels per message and paced.
- A channel becomes confirmed only after its JSON-RPC subscription response.
- Failed or partially acknowledged batches retry only missing channels.
- Diagnostics expose both pending requests and pending ticker counts.
- The active instrument set is refreshed every 15 minutes.
- Existing MOS formulas and Particle Logic scoring are unchanged.

## Deployment check

Open:

```text
http://localhost:8005/api/research/deribit-smoke-test
```

The response should show `status: ok`, positive WebSocket ticker and Greek
counts, and `deribit_data_transport: websocket_ticker_cache`.

No database cleanup is required. Existing Bybit-only rows remain valid and the
first mixed-source row establishes the Deribit activation boundary.

## v54 collector result

The first approximately 40-minute collector export contained 138 research
snapshots and nine structural snapshots, but all remained Bybit-only. The
Deribit cache stayed in `deribit_ws_ticker_cache_warming`; this is the reason
for the v55 subscription backpressure and acknowledgement patch.
