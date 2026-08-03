# MOS Deribit WebSocket Option Ticker Collector v4-v14

## Why it exists

On the collector host, Deribit `get_instruments` returned 866 BTC option
instruments, but `get_book_summary_by_currency` exhausted three HTTP attempts
and returned no rows after approximately 49 seconds. The live MOS manager has
an eight-second adapter guard, so Deribit was always excluded before the bulk
request could finish.

## Data path

```text
compressed REST get_instruments
    -> active BTC option names
    -> atomic disk cache / compressed WebSocket discovery fallback
    -> adaptive REST public/ticker warmup and recovery
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
- Each contract snapshot older than five minutes is excluded.
- A partially warmed cache is excluded until at least 70% of the balanced
  240-contract research core has delivered ticker snapshots.
- Instrument discovery retries independently from message handling.
- The complete 240-contract core is sent in one bounded subscription request.
- A channel becomes confirmed only after its JSON-RPC subscription response.
- Failed or partially acknowledged batches retry only missing channels.
- The first incremental-ticker notification seeds a full contract snapshot;
  later partial `stats` and `greeks` changes are deep-merged into it.
- Diagnostics expose both pending requests and pending ticker counts.
- The active instrument set is refreshed every 15 minutes.
- Successful instrument discovery is persisted outside SQLite and reused after
  a transient REST failure.
- REST ticker recovery runs at up to two requests per second only during
  warmup/degradation; healthy WebSocket collection uses at most one request
  every four seconds.
- The WebSocket BTC index price replaces normal repeated REST spot polling.
- Existing MOS formulas and Particle Logic scoring are unchanged.
- Before 70% core readiness, REST capacity is dedicated to the core. After
  readiness, the scheduler alternates nine core batches with one rotating
  full-chain tail batch.

## Deployment check

Open:

```text
http://localhost:8005/api/research/deribit-smoke-test
```

The response should show `status: ok`, positive ticker and Greek counts, and
`deribit_data_transport: compressed_rest_discovery+websocket_incremental_ticker_cache+circuit_broken_adaptive_rest_ticker_recovery`, with
`deribit_ticker_bootstrap_transport: circuit_broken_adaptive_rest_public_ticker`.
`deribit_ws_cache_coverage_ratio` reports core readiness, while
`deribit_ws_chain_coverage_ratio` reports full-chain backfill progress.
The endpoint reports the current cache immediately; it does not wait for a
120-second warmup window.

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

## v57 smoke-test result and v58 bootstrap

On the collector host, v57 received 113 messages but only 57 unique contracts
in 30 seconds. This showed that Deribit was serializing or throttling the large
initial snapshot stream despite accepting the first 500 subscriptions. v58
therefore seeds missing complete tickers on a separate public WebSocket using
`public/ticker` in ten-request batches at no more than ten requests per second.
This one-time path supplies IV, volume, prices, and complete Greeks; the normal
MOS poll remains cache-only and `incremental_ticker` maintains the result.

The smoke endpoint now reuses the live adapter. It no longer creates a second
866-contract collector while the first one is warming.

## v58 smoke-test result and v59 adaptive core

v58 confirmed that full `public/ticker` replies contain the required IV and
Greeks, but only 98 of 220 bootstrap requests succeeded during the measured
window and 76 timed out. The cache reached 159 complete contracts, while only
121 remained inside the 120-second freshness window. The bootstrap therefore
could not reach 70% of all 866 contracts before its earliest results expired.

v59 selects a 240-contract, near-ATM core distributed across every expiry and
subscribes only that core continuously. It requests two full tickers per second,
reconnects after three empty batches, and continues the remaining full-chain
backfill after the core becomes usable. Five-minute freshness matches the slow
structural snapshot cadence without permitting indefinite stale reuse.

## v59 smoke-test result and v60 deduplication

After two v59 smoke windows, the core remained at 107 unique contracts even
though the bootstrap recorded 99 successful RPC replies. Those replies were
mostly duplicate refreshes of the first 100 subscription snapshots. v60 freezes
per-contract baselines after the subscription head start, skips every already
fresh complete ticker, and subscribes the complete 240-contract core in one
request instead of waiting on three serialized acknowledgements.

## v60 smoke-test result and v61 REST bootstrap

v60 confirmed all 240 core subscriptions with zero pending channels, but the
temporary bootstrap WebSocket closed without a close frame. It completed only
48 requests in 116 seconds and stopped degraded at 86 unique core tickers. v61
keeps the live subscription WebSocket and moves only the one-time full-ticker
bootstrap to lightweight per-instrument REST `public/ticker` calls, two at a
time through the existing persistent HTTP client.

## v61 smoke-test result and v62 core maintenance

v61 first reached `status = ok` with 169 complete core contracts, valid IV and
Greeks, 88 calls, 81 puts, and 13 expiries. The long sequential chain pass then
cached 783 contracts over about 998 seconds, but its first 240 core snapshots
aged beyond the strict five-minute TTL. Core coverage fell from 70.4% to zero
while the bootstrap continued succeeding.

v62 replaces the finite chain pass with a continuous weighted scheduler. Until
core readiness, it requests core contracts only. Afterwards it sends nine
round-robin core batches for every one tail batch, begins revisiting core
contracts after 60 seconds, and preserves the strict five-minute rejection of
stale data. Diagnostics expose the active scheduler phase, core/tail request
counts, policy, refresh age, and the last REST-bootstrap success timestamp.

## v62 long-run result and v63 liveness watchdog

v62 first reached 730 fresh complete contracts with full 240-contract core
coverage. Later, the adapter still showed 240 confirmed subscriptions and all
866 contracts cached, but only 100 core contracts were fresh. No real
WebSocket ticker had arrived for about 20 minutes and subscription maintenance
had not run for about 30 minutes. The connection had become logically dead
without producing a normal close event.

v63 supervises the receiver and subscription-maintenance tasks together. Once
ticker subscriptions are acknowledged, 60 seconds without an actual
incremental ticker message raises a liveness failure, closes the old lifecycle,
and reconnects/resubscribes. Diagnostics expose receiver state, ticker idle
age, connection and reconnect counters, idle reconnects, refresh-loop errors,
and whether the refresh task is currently running. REST snapshots do not reset
the WebSocket liveness clock.

## v63 network result and v64 adaptive REST guard

The collector host resolved Deribit and established TLS normally. A small
`public/test` call completed in 0.27 seconds, while one uncompressed
`get_instruments` response returned `HTTP 200` but transferred only 25,498 bytes
at about 850 bytes per second before a 30-second client timeout. Repeating the
same request with HTTP compression transferred 12,783 bytes and completed in
0.27 seconds. This ruled out a hard IP block and showed a transient large-body
delivery problem.

v64 makes compressed discovery explicit, records its wire/decoded sizes, saves
the last valid chain atomically to `backend/data/deribit_instruments_cache.json`,
and uses a compressed WebSocket RPC only when REST and both memory/disk caches
cannot supply instruments. The runtime file is ignored by Git and does not
change either MOS database.

The ticker scheduler is now adaptive. During startup, degraded WebSocket state,
or core coverage below 80%, it retains the proven two-request-per-second
recovery rate. With a healthy 240-channel subscription and at least 80% fresh
complete core coverage, it sends at most one REST ticker request every four
seconds and begins proactive core refresh only at four minutes. If coverage
falls, it automatically returns to fast recovery. The existing 70% aggregation
threshold and five-minute stale-data rejection remain unchanged.

## v64 outage result and v65 REST circuit breaker

After a healthy v64 warmup, Deribit became unavailable over both REST and
WebSocket. MOS preserved the 826-instrument chain from its disk-backed cache
and correctly stopped emitting stale option rows, but the fast recovery mode
kept retrying `public/ticker`. The smoke route also waited for a new 15-second
`get_instruments` timeout although a valid stale chain was already available.

v65 treats the REST endpoints as one failure domain. Three consecutive
transport/HTTP failures open a shared circuit. Backoff progresses through 30,
60, 120, and 240 seconds,
then remains capped at 300 seconds. Only one request is admitted when the timer
expires; other concurrent calls remain cache-only. A failed probe advances the
backoff, while any valid REST result closes the circuit and allows normal
adaptive recovery to resume.

While the circuit is open, the ticker scheduler reports
`network_backoff/rest_circuit_open`, issues no REST requests, and retains stale
records only for diagnostics. Stale option observations remain excluded from
MOS after five minutes. Instrument smoke checks return the existing chain
immediately. Per-contract ticker and spot requests use a five-second timeout;
compressed discovery keeps a 15-second timeout.
