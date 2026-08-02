# MOS Deribit WebSocket Option Ticker Collector v4/v5/v6/v7

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
    -> WebSocket incremental_ticker.<instrument>
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
- Each contract snapshot older than 90 seconds is excluded.
- A partially warmed cache is excluded until at least 70% of discovered
  instruments have delivered ticker snapshots.
- Instrument discovery retries independently from message handling.
- Subscription requests are bounded to 500 channels per message; every batch
  must be acknowledged before the next one is sent.
- A channel becomes confirmed only after its JSON-RPC subscription response.
- Failed or partially acknowledged batches retry only missing channels.
- The first incremental-ticker notification seeds a full contract snapshot;
  later partial `stats` and `greeks` changes are deep-merged into it.
- Diagnostics expose both pending requests and pending ticker counts.
- The active instrument set is refreshed every 15 minutes.
- Existing MOS formulas and Particle Logic scoring are unchanged.

## Deployment check

Open:

```text
http://localhost:8005/api/research/deribit-smoke-test
```

The response should show `status: ok`, positive WebSocket ticker and Greek
counts, and `deribit_data_transport: websocket_incremental_ticker_cache`.

No database cleanup is required. Existing Bybit-only rows remain valid and the
first mixed-source row establishes the Deribit activation boundary.

## v54 collector result

The first approximately 40-minute collector export contained 138 research
snapshots and nine structural snapshots, but all remained Bybit-only. The
Deribit cache stayed in `deribit_ws_ticker_cache_warming`; this is the reason
for the v55 subscription backpressure and acknowledgement patch.

## v55 smoke-test result

The endpoint's direct REST call returned 866 instruments, but the simultaneous
background discovery returned an empty result. Diagnostics therefore showed
`deribit_ws_instruments_count = 0` and no subscription requests. v56 serializes
those calls and reuses the successful instrument list.

## v56 smoke-test result and v57 correction

Discovery returned all 866 instruments and the first 500 subscriptions were
confirmed, but only 26 ordinary ticker updates arrived. Coverage therefore
remained about 3%, while the second 366-channel batch was still pending. v57
uses the incremental ticker stream because its first notification supplies the
full current ticker for every subscribed contract, then merges later changes.
