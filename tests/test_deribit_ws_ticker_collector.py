import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api.deribit_adapter import DeribitAdapter
from engine.instrument_normalizer import InstrumentNormalizer


class _FakeWebSocket:
    def __init__(self, adapter=None):
        self.messages = []
        self.adapter = adapter

    async def send(self, message):
        parsed = json.loads(message)
        self.messages.append(parsed)
        if self.adapter is not None:
            await self.adapter._handle_ws_message({
                "jsonrpc": "2.0",
                "id": parsed["id"],
                "result": parsed["params"]["channels"],
            })


class _SilentWebSocket:
    async def recv(self):
        await asyncio.sleep(1.0)


class _InstrumentDiscoveryWebSocket:
    def __init__(self, instruments):
        self.instruments = instruments
        self.request = None

    async def send(self, message):
        self.request = json.loads(message)

    async def recv(self):
        return json.dumps({
            "jsonrpc": "2.0",
            "id": self.request["id"],
            "result": self.instruments,
        })


class _WebSocketContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class DeribitWsTickerCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = DeribitAdapter(instrument_cache_path=None)

    async def asyncTearDown(self):
        await self.adapter._http.aclose()

    async def test_ws_ticker_message_populates_non_blocking_cache(self):
        now_ms = int(time.time() * 1000)
        raw_ticker = {
            "instrument_name": "BTC-14AUG26-65000-C",
            "timestamp": now_ms,
            "open_interest": 123.5,
            "mark_iv": 55.2,
            "bid_iv": 54.9,
            "ask_iv": 55.5,
            "mark_price": 0.042,
            "last_price": 0.041,
            "best_bid_price": 0.040,
            "best_ask_price": 0.043,
            "underlying_price": 63_000.0,
            "stats": {"volume": 17.25},
            "greeks": {
                "delta": 0.42,
                "gamma": 0.000031,
                "vega": 18.5,
                "theta": -7.2,
            },
        }
        await self.adapter._handle_ws_message({
            "method": "subscription",
            "params": {
                "channel": "incremental_ticker.BTC-14AUG26-65000-C",
                "data": raw_ticker,
            },
        })

        tickers = await self.adapter.fetch_option_tickers()

        self.assertEqual(len(tickers), 1)
        self.assertEqual(tickers[0]["instrument_name"], raw_ticker["instrument_name"])
        self.assertEqual(self.adapter.get_total_oi(), 123.5)
        self.assertEqual(self.adapter.get_total_volume(), 17.25)
        self.assertTrue(await self.adapter.wait_for_option_tickers(timeout=0.01))
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_data_transport"],
            "compressed_rest_discovery+websocket_incremental_ticker_cache"
            "+circuit_broken_adaptive_rest_ticker_recovery",
        )
        self.assertEqual(diagnostics["deribit_ws_cached_tickers"], 1)
        self.assertEqual(diagnostics["deribit_ws_fresh_tickers"], 1)

    async def test_incremental_ticker_merges_partial_nested_updates(self):
        instrument_name = "BTC-14AUG26-65000-C"
        channel = f"incremental_ticker.{instrument_name}"
        await self.adapter._handle_ws_message({
            "method": "subscription",
            "params": {
                "channel": channel,
                "data": {
                    "instrument_name": instrument_name,
                    "mark_iv": 55.2,
                    "stats": {"volume": 17.25},
                    "greeks": {"delta": 0.42, "gamma": 0.000031},
                },
            },
        })
        await self.adapter._handle_ws_message({
            "method": "subscription",
            "params": {
                "channel": channel,
                "data": {
                    "instrument_name": instrument_name,
                    "mark_iv": 56.0,
                    "greeks": {"delta": 0.45},
                },
            },
        })

        ticker = self.adapter.get_cached_tickers()[0]
        self.assertEqual(ticker["mark_iv"], 56.0)
        self.assertEqual(ticker["stats"]["volume"], 17.25)
        self.assertEqual(ticker["greeks"]["delta"], 0.45)
        self.assertEqual(ticker["greeks"]["gamma"], 0.000031)

    async def test_spot_price_uses_fresh_websocket_cache_without_rest(self):
        await self.adapter._handle_ws_message({
            "method": "subscription",
            "params": {
                "channel": "deribit_price_index.btc_usd",
                "data": {"price": 63_500.0},
            },
        })

        with patch.object(
            self.adapter,
            "_rpc_get",
            new=AsyncMock(),
        ) as rpc_get:
            price = await self.adapter.fetch_spot_price()

        self.assertEqual(price, 63_500.0)
        self.assertEqual(rpc_get.await_count, 0)
        self.assertEqual(
            self.adapter.get_diagnostics()[
                "deribit_spot_rest_fallback_count"
            ],
            0,
        )

    async def test_rest_ticker_batch_seeds_greeks_for_every_contract(self):
        instrument_names = [
            "BTC-14AUG26-65000-C",
            "BTC-14AUG26-65000-P",
        ]

        async def ticker_result(method, params, max_retries=3):
            self.assertEqual(method, "ticker")
            self.assertEqual(max_retries, 1)
            return {
                "instrument_name": params["instrument_name"],
                "timestamp": int(time.time() * 1000),
                "open_interest": 12.5,
                "mark_iv": 55.0,
                "stats": {"volume": 3.25},
                "greeks": {
                    "delta": 0.4,
                    "gamma": 0.00003,
                    "vega": 15.0,
                    "theta": -6.0,
                },
            }

        with patch.object(
            self.adapter,
            "_rpc_get",
            new=AsyncMock(side_effect=ticker_result),
        ) as rpc_get:
            successes = await self.adapter._bootstrap_ticker_rest_batch(
                instrument_names
            )

        self.assertEqual(successes, 2)
        self.assertEqual(rpc_get.await_count, 2)
        self.assertEqual(len(self.adapter.get_cached_tickers()), 2)
        self.assertTrue(all(
            self.adapter._ticker_has_full_option_data(name)
            for name in instrument_names
        ))
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_ws_full_tickers"], 2)
        self.assertEqual(diagnostics["deribit_ws_bootstrap_request_count"], 2)
        self.assertEqual(diagnostics["deribit_ws_bootstrap_success_count"], 2)
        self.assertEqual(diagnostics["deribit_ws_bootstrap_error_count"], 0)
        self.assertEqual(
            diagnostics["deribit_ticker_bootstrap_transport"],
            "circuit_broken_adaptive_rest_public_ticker",
        )

    async def test_rest_ticker_batch_counts_missing_results(self):
        self.adapter.last_error = "rest_timeout"
        with patch.object(
            self.adapter,
            "_rpc_get",
            new=AsyncMock(return_value=None),
        ):
            successes = await self.adapter._bootstrap_ticker_rest_batch([
                "BTC-14AUG26-65000-C",
                "BTC-14AUG26-65000-P",
            ])

        self.assertEqual(successes, 0)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_ws_bootstrap_request_count"], 2)
        self.assertEqual(diagnostics["deribit_ws_bootstrap_error_count"], 2)
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_last_error"],
            "rest_timeout",
        )

    async def test_rest_circuit_opens_and_skips_network_after_failures(self):
        with patch.object(
            self.adapter._http,
            "get",
            new=AsyncMock(side_effect=TimeoutError()),
        ) as http_get:
            for _ in range(3):
                result = await self.adapter._rpc_get(
                    "ticker",
                    {"instrument_name": "BTC-14AUG26-65000-C"},
                    max_retries=1,
                )
                self.assertIsNone(result)
            skipped = await self.adapter._rpc_get(
                "ticker",
                {"instrument_name": "BTC-14AUG26-65000-P"},
                max_retries=1,
            )

        self.assertIsNone(skipped)
        self.assertEqual(http_get.await_count, 3)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_fetch_attempt_count"], 3)
        self.assertEqual(diagnostics["deribit_rest_circuit_state"], "open")
        self.assertEqual(
            diagnostics["deribit_rest_circuit_consecutive_failures"],
            3,
        )
        self.assertEqual(diagnostics["deribit_rest_circuit_open_count"], 1)
        self.assertEqual(diagnostics["deribit_rest_circuit_skip_count"], 1)
        self.assertEqual(
            diagnostics["deribit_rest_circuit_last_error"],
            "TimeoutError",
        )
        self.assertGreater(
            diagnostics["deribit_rest_circuit_retry_after_sec"],
            0.0,
        )

    async def test_json_rpc_errors_do_not_open_network_circuit(self):
        class _ErrorResponse:
            headers = {}
            content = b'{"error":{"message":"instrument not found"}}'
            num_bytes_downloaded = len(content)

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"error": {"message": "instrument not found"}}

        with patch.object(
            self.adapter._http,
            "get",
            new=AsyncMock(return_value=_ErrorResponse()),
        ) as http_get:
            for _ in range(3):
                self.assertIsNone(await self.adapter._rpc_get(
                    "ticker",
                    {"instrument_name": "BTC-EXPIRED-C"},
                    max_retries=1,
                ))

        self.assertEqual(http_get.await_count, 3)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_rest_circuit_state"], "closed")
        self.assertEqual(
            diagnostics["deribit_rest_circuit_consecutive_failures"],
            0,
        )
        self.assertEqual(diagnostics["deribit_rest_circuit_open_count"], 0)

    async def test_rest_circuit_allows_one_probe_and_closes_on_success(self):
        class _Response:
            headers = {}
            content = b'{"result":{"index_price":63500.0}}'
            num_bytes_downloaded = len(content)

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"result": {"index_price": 63_500.0}}

        self.adapter._rest_circuit_consecutive_failures = 3
        self.adapter._rest_circuit_backoff_level = 1
        self.adapter._rest_circuit_open_until_ts = time.time() - 0.01
        probe_started = asyncio.Event()
        release_probe = asyncio.Event()

        async def delayed_response(*args, **kwargs):
            probe_started.set()
            await release_probe.wait()
            return _Response()

        with patch.object(
            self.adapter._http,
            "get",
            new=AsyncMock(side_effect=delayed_response),
        ) as http_get:
            first_task = asyncio.create_task(self.adapter._rpc_get(
                "get_index_price",
                {"index_name": "btc_usd"},
                max_retries=1,
            ))
            await probe_started.wait()
            second = await self.adapter._rpc_get(
                "get_index_price",
                {"index_name": "btc_usd"},
                max_retries=1,
            )
            release_probe.set()
            first = await first_task

        self.assertEqual(http_get.await_count, 1)
        self.assertIn({"index_price": 63_500.0}, (first, second))
        self.assertIn(None, (first, second))
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_rest_circuit_state"], "closed")
        self.assertEqual(
            diagnostics["deribit_rest_circuit_consecutive_failures"],
            0,
        )
        self.assertEqual(diagnostics["deribit_rest_circuit_probe_count"], 1)
        self.assertEqual(diagnostics["deribit_rest_circuit_recovery_count"], 1)
        self.assertEqual(diagnostics["deribit_rest_circuit_skip_count"], 1)
        self.assertEqual(http_get.await_args.kwargs["timeout"], 5.0)

    async def test_open_rest_circuit_returns_stale_instruments_without_io(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
        ]
        self.adapter._instruments_cache = instruments
        self.adapter._instruments_cache_ts = 0.0
        self.adapter._rest_circuit_open_until_ts = time.time() + 30.0
        self.adapter._rest_circuit_backoff_level = 1

        with patch.object(
            self.adapter._http,
            "get",
            new=AsyncMock(),
        ) as http_get:
            result = await self.adapter.fetch_instruments()

        self.assertEqual(result, instruments)
        self.assertEqual(http_get.await_count, 0)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_instrument_cache_source"],
            "stale_cache",
        )
        self.assertEqual(diagnostics["deribit_rest_circuit_skip_count"], 1)

    def test_failed_half_open_probe_doubles_rest_backoff(self):
        for _ in range(3):
            self.adapter._record_rest_failure("timeout")
        first_backoff = (
            self.adapter._rest_circuit_open_until_ts
            - self.adapter._rest_circuit_last_open_ts
        )

        self.adapter._rest_circuit_open_until_ts = time.time() - 0.01
        self.assertTrue(self.adapter._acquire_rest_request_slot())
        self.adapter._record_rest_failure("timeout-again")
        second_backoff = (
            self.adapter._rest_circuit_open_until_ts
            - self.adapter._rest_circuit_last_open_ts
        )

        self.assertEqual(first_backoff, 30.0)
        self.assertEqual(second_backoff, 60.0)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_rest_circuit_backoff_level"], 2)
        self.assertEqual(diagnostics["deribit_rest_circuit_probe_count"], 1)
        self.assertEqual(diagnostics["deribit_rest_circuit_open_count"], 2)

    async def test_refresh_scheduler_makes_no_requests_while_circuit_open(self):
        name = "BTC-14AUG26-65000-C"
        self.adapter._ws_instruments_count = 1
        self.adapter._ws_core_instrument_names = {name}
        self.adapter._ticker_bootstrap_instrument_names = [name]
        self.adapter._rest_circuit_open_until_ts = time.time() + 30.0
        self.adapter._rest_circuit_backoff_level = 1
        self.adapter._running = True
        sleep_count = 0

        async def stop_after_backoff_sleep(_delay):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                self.adapter._running = False

        with (
            patch(
                "api.deribit_adapter._TICKER_BOOTSTRAP_START_DELAY_SEC",
                0.0,
            ),
            patch(
                "api.deribit_adapter.asyncio.sleep",
                side_effect=stop_after_backoff_sleep,
            ),
            patch.object(
                self.adapter,
                "_bootstrap_ticker_rest_batch",
                new=AsyncMock(),
            ) as rest_batch,
        ):
            await self.adapter._bootstrap_full_tickers([name])

        self.assertEqual(rest_batch.await_count, 0)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_mode"],
            "network_backoff",
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_phase"],
            "rest_circuit_open",
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_current_batch_size"],
            0,
        )

    async def test_bootstrap_singleflight_does_not_duplicate_connections(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_bootstrap(instrument_names):
            started.set()
            await release.wait()

        instrument_names = ["BTC-14AUG26-65000-C"]
        with patch.object(
            self.adapter,
            "_bootstrap_full_tickers",
            side_effect=delayed_bootstrap,
        ) as bootstrap:
            self.adapter._ensure_ticker_bootstrap(instrument_names)
            first_task = self.adapter._ticker_bootstrap_task
            await started.wait()
            self.adapter._ensure_ticker_bootstrap(instrument_names)
            self.assertIs(self.adapter._ticker_bootstrap_task, first_task)
            self.assertEqual(bootstrap.call_count, 1)
            release.set()
            await first_task

    def test_bootstrap_cycle_skips_fresh_full_core_tickers(self):
        instrument_names = [
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(240)
        ]
        for name in instrument_names[:100]:
            self.adapter._store_ticker_snapshot(
                {
                    "instrument_name": name,
                    "mark_iv": 55.0,
                    "greeks": {
                        "delta": 0.4,
                        "gamma": 0.00003,
                        "vega": 15.0,
                        "theta": -6.0,
                    },
                },
                stream_message=False,
            )

        baselines = self.adapter._bootstrap_refresh_baselines(
            instrument_names
        )

        self.assertEqual(len(baselines), 140)
        self.assertTrue(all(
            name not in baselines
            for name in instrument_names[:100]
        ))
        self.assertTrue(all(
            name in baselines
            for name in instrument_names[100:]
        ))

    def test_round_robin_refresh_batch_skips_recent_core_tickers(self):
        instrument_names = [
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(4)
        ]
        for name in instrument_names:
            self.adapter._store_ticker_snapshot(
                {
                    "instrument_name": name,
                    "mark_iv": 55.0,
                    "greeks": {
                        "delta": 0.4,
                        "gamma": 0.00003,
                        "vega": 15.0,
                        "theta": -6.0,
                    },
                },
                stream_message=False,
            )
        self.adapter._ticker_received_ts_by_instrument[
            instrument_names[1]
        ] = time.time() - 61
        self.adapter._ticker_received_ts_by_instrument[
            instrument_names[3]
        ] = time.time() - 61

        batch, cursor = self.adapter._next_ticker_refresh_batch(
            instrument_names,
            0,
            max_age_sec=60.0,
        )

        self.assertEqual(batch, [instrument_names[1], instrument_names[3]])
        self.assertEqual(cursor, 0)

    async def test_refresh_scheduler_reserves_nine_batches_for_core(self):
        core_names = [
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(20)
        ]
        tail_names = [
            f"BTC-21AUG26-{70_000 + index}-P"
            for index in range(4)
        ]
        self.adapter._ws_instruments_count = len(core_names) + len(tail_names)
        self.adapter._ws_core_instrument_names = set(core_names)
        for name in core_names:
            self.adapter._store_ticker_snapshot(
                {
                    "instrument_name": name,
                    "mark_iv": 55.0,
                    "greeks": {
                        "delta": 0.4,
                        "gamma": 0.00003,
                        "vega": 15.0,
                        "theta": -6.0,
                    },
                },
                stream_message=False,
            )
            self.adapter._ticker_received_ts_by_instrument[name] = (
                time.time() - 241
            )
        self.adapter.health.ws_connected = True
        self.adapter._ws_receiver_state = "receiving"
        self.adapter._ws_ticker_watch_started_ts = time.time()
        self.adapter._ws_last_ticker_ts = time.time()
        self.adapter._subscribed_ticker_channels = {
            f"incremental_ticker.{name}" for name in core_names
        }

        batches = []

        async def record_batch(instrument_names):
            batches.append(list(instrument_names))
            if len(batches) >= 10:
                self.adapter._running = False
            return len(instrument_names)

        self.adapter._running = True
        with (
            patch(
                "api.deribit_adapter._TICKER_BOOTSTRAP_START_DELAY_SEC",
                0.0,
            ),
            patch(
                "api.deribit_adapter._TICKER_HEALTHY_BATCH_INTERVAL_SEC",
                0.0,
            ),
            patch.object(
                self.adapter,
                "_bootstrap_ticker_rest_batch",
                side_effect=record_batch,
            ),
        ):
            await self.adapter._bootstrap_full_tickers(
                core_names + tail_names
            )

        self.assertEqual(len(batches), 10)
        self.assertTrue(all(
            set(batch).issubset(set(core_names))
            for batch in batches[:9]
        ))
        self.assertTrue(set(batches[9]).issubset(set(tail_names)))
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_core_request_count"],
            9,
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_tail_request_count"],
            1,
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_policy"],
            "circuit_breaker_30_to_300s+"
            "adaptive_recovery_2rps_healthy_0.25rps",
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_mode"],
            "healthy_low_rate",
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_current_batch_size"],
            1,
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_low_rate_request_count"],
            10,
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_recovery_request_count"],
            0,
        )

    async def test_refresh_scheduler_does_not_backfill_before_core_ready(self):
        core_names = [
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(10)
        ]
        tail_names = [
            f"BTC-21AUG26-{70_000 + index}-P"
            for index in range(4)
        ]
        self.adapter._ws_instruments_count = len(core_names) + len(tail_names)
        self.adapter._ws_core_instrument_names = set(core_names)
        batches = []

        async def record_batch(instrument_names):
            batches.append(list(instrument_names))
            if len(batches) >= 3:
                self.adapter._running = False
            return len(instrument_names)

        self.adapter._running = True
        with (
            patch(
                "api.deribit_adapter._TICKER_BOOTSTRAP_START_DELAY_SEC",
                0.0,
            ),
            patch(
                "api.deribit_adapter._TICKER_BOOTSTRAP_BATCH_INTERVAL_SEC",
                0.0,
            ),
            patch.object(
                self.adapter,
                "_bootstrap_ticker_rest_batch",
                side_effect=record_batch,
            ),
        ):
            await self.adapter._bootstrap_full_tickers(
                core_names + tail_names
            )

        self.assertEqual(len(batches), 3)
        self.assertTrue(all(
            set(batch).issubset(set(core_names))
            for batch in batches
        ))
        self.assertEqual(
            self.adapter.get_diagnostics()[
                "deribit_ws_bootstrap_tail_request_count"
            ],
            0,
        )
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_mode"],
            "warmup_recovery",
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_current_batch_size"],
            2,
        )
        self.assertEqual(
            diagnostics["deribit_ws_bootstrap_recovery_request_count"],
            6,
        )

    async def test_concurrent_instrument_discovery_uses_one_rest_request(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
            {"instrument_name": "BTC-14AUG26-65000-P"},
        ]

        async def delayed_discovery(*args, **kwargs):
            await asyncio.sleep(0.01)
            return instruments

        rpc_get = AsyncMock(side_effect=delayed_discovery)
        with patch.object(self.adapter, "_rpc_get", new=rpc_get):
            first, second = await asyncio.gather(
                self.adapter.fetch_instruments(),
                self.adapter.fetch_instruments(),
            )

        self.assertEqual(first, instruments)
        self.assertEqual(second, instruments)
        self.assertEqual(rpc_get.await_count, 1)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_instrument_cache_count"], 2)

    async def test_instrument_discovery_falls_back_to_last_good_cache(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
        ]
        with patch.object(
            self.adapter,
            "_rpc_get",
            new=AsyncMock(side_effect=[instruments, None]),
        ), patch.object(
            self.adapter,
            "_fetch_instruments_ws_fallback",
            new=AsyncMock(return_value=[]),
        ):
            first = await self.adapter.fetch_instruments()
            self.adapter._instruments_cache_ts = 0.0
            fallback = await self.adapter.fetch_instruments()

        self.assertEqual(first, instruments)
        self.assertEqual(fallback, instruments)

    async def test_instrument_discovery_uses_websocket_rpc_after_rest_failure(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
            {"instrument_name": "BTC-14AUG26-65000-P"},
        ]
        with patch.object(
            self.adapter,
            "_rpc_get",
            new=AsyncMock(return_value=None),
        ), patch.object(
            self.adapter,
            "_fetch_instruments_ws_fallback",
            new=AsyncMock(return_value=instruments),
        ) as ws_fallback:
            result = await self.adapter.fetch_instruments()

        self.assertEqual(result, instruments)
        self.assertEqual(ws_fallback.await_count, 1)
        self.assertEqual(
            self.adapter.get_diagnostics()["deribit_instrument_cache_source"],
            "websocket_rpc",
        )

    async def test_websocket_instrument_fallback_requests_compressed_chain(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
        ]
        websocket = _InstrumentDiscoveryWebSocket(instruments)
        with patch(
            "api.deribit_adapter.websockets.connect",
            return_value=_WebSocketContext(websocket),
        ) as connect:
            result = await self.adapter._fetch_instruments_ws_fallback()

        self.assertEqual(result, instruments)
        self.assertEqual(
            websocket.request["method"],
            "public/get_instruments",
        )
        self.assertIs(websocket.request["params"]["expired"], False)
        self.assertEqual(connect.call_args.kwargs["compression"], "deflate")
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(
            diagnostics["deribit_instrument_ws_fallback_attempt_count"],
            1,
        )
        self.assertEqual(
            diagnostics["deribit_instrument_ws_fallback_success_count"],
            1,
        )

    async def test_instrument_discovery_records_compression_diagnostics(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
        ]

        class _CompressedResponse:
            headers = {"content-encoding": "gzip"}
            content = json.dumps({
                "jsonrpc": "2.0",
                "result": instruments,
            }).encode("utf-8")
            num_bytes_downloaded = 42

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"jsonrpc": "2.0", "result": instruments}

        response = _CompressedResponse()
        with patch.object(
            self.adapter._http,
            "get",
            new=AsyncMock(return_value=response),
        ):
            result = await self.adapter.fetch_instruments()

        self.assertEqual(result, instruments)
        diagnostics = self.adapter.get_diagnostics()
        self.assertIn(
            "gzip",
            diagnostics["deribit_instrument_http_accept_encoding"],
        )
        self.assertEqual(
            diagnostics["deribit_instrument_http_content_encoding"],
            "gzip",
        )
        self.assertGreater(
            diagnostics["deribit_instrument_http_download_bytes"],
            0,
        )
        self.assertGreater(
            diagnostics["deribit_instrument_http_decoded_bytes"],
            0,
        )

    async def test_instrument_discovery_persists_and_reloads_disk_cache(self):
        instruments = [
            {"instrument_name": "BTC-14AUG26-65000-C"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "deribit_instruments_cache.json"
            writer = DeribitAdapter(instrument_cache_path=cache_path)
            try:
                with patch.object(
                    writer,
                    "_rpc_get",
                    new=AsyncMock(return_value=instruments),
                ):
                    self.assertEqual(
                        await writer.fetch_instruments(),
                        instruments,
                    )
                self.assertTrue(cache_path.exists())
            finally:
                await writer._http.aclose()

            reader = DeribitAdapter(instrument_cache_path=cache_path)
            try:
                with patch.object(
                    reader,
                    "_rpc_get",
                    new=AsyncMock(),
                ) as rpc_get:
                    self.assertEqual(
                        await reader.fetch_instruments(),
                        instruments,
                    )
                self.assertEqual(rpc_get.await_count, 0)
                diagnostics = reader.get_diagnostics()
                self.assertEqual(
                    diagnostics["deribit_instrument_cache_source"],
                    "disk",
                )
                self.assertEqual(
                    diagnostics["deribit_instrument_disk_cache_load_count"],
                    1,
                )
            finally:
                await reader._http.aclose()

    async def test_subscription_refresh_batches_channels_and_purges_expired_cache(self):
        instruments = [
            {"instrument_name": f"BTC-14AUG26-{50_000 + index}-C"}
            for index in range(866)
        ]
        self.adapter._ticker_cache_by_instrument["BTC-3AUG26-40000-P"] = {
            "instrument_name": "BTC-3AUG26-40000-P"
        }
        websocket = _FakeWebSocket(self.adapter)

        with patch.object(
            self.adapter,
            "fetch_instruments",
            new=AsyncMock(return_value=instruments),
        ):
            refreshed = await self.adapter._refresh_ws_option_subscriptions(
                websocket
            )

        self.assertTrue(refreshed)
        self.assertEqual(len(websocket.messages), 1)
        subscribed = {
            channel
            for message in websocket.messages
            for channel in message["params"]["channels"]
        }
        self.assertEqual(len(subscribed), 240)
        self.assertTrue(all(
            channel.startswith("incremental_ticker.BTC-")
            for channel in subscribed
        ))
        self.assertEqual(len(self.adapter._subscribed_ticker_channels), 240)
        self.assertEqual(self.adapter._pending_ticker_channels, {})
        self.assertGreater(self.adapter._ws_ticker_watch_started_ts, 0.0)
        self.assertNotIn(
            "BTC-3AUG26-40000-P",
            self.adapter._ticker_cache_by_instrument,
        )

    async def test_subscription_refresh_unsubscribes_contracts_outside_core(self):
        old_channel = "incremental_ticker.BTC-3AUG26-40000-P"
        self.adapter._subscribed_ticker_channels.add(old_channel)
        instruments = [
            {
                "instrument_name": "BTC-14AUG26-65000-C",
                "expiration_timestamp": 1,
                "strike": 65_000,
            },
            {
                "instrument_name": "BTC-14AUG26-65000-P",
                "expiration_timestamp": 1,
                "strike": 65_000,
            },
        ]
        websocket = _FakeWebSocket(self.adapter)

        with patch.object(
            self.adapter,
            "fetch_instruments",
            new=AsyncMock(return_value=instruments),
        ):
            refreshed = await self.adapter._refresh_ws_option_subscriptions(
                websocket
            )

        self.assertTrue(refreshed)
        self.assertEqual(
            [message["method"] for message in websocket.messages],
            ["public/unsubscribe", "public/subscribe"],
        )
        self.assertNotIn(old_channel, self.adapter._subscribed_ticker_channels)
        self.assertEqual(len(self.adapter._subscribed_ticker_channels), 2)

    async def test_stale_ws_cache_is_not_returned_to_live_mos(self):
        name = "BTC-14AUG26-65000-P"
        self.adapter._ticker_cache_by_instrument[name] = {
            "instrument_name": name
        }
        self.adapter._ticker_received_ts_by_instrument[name] = time.time() - 301
        self.adapter._tickers_cache_ts = time.time() - 301

        self.assertEqual(await self.adapter.fetch_option_tickers(), [])
        self.assertEqual(
            self.adapter.disabled_reason,
            "deribit_ws_ticker_cache_stale",
        )

    async def test_partially_warmed_chain_is_not_returned_to_live_mos(self):
        self.adapter._ws_instruments_count = 10
        self.adapter._ws_core_instrument_names = {
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(10)
        }
        for index in range(6):
            name = f"BTC-14AUG26-{60_000 + index}-C"
            self.adapter._ticker_cache_by_instrument[name] = {
                "instrument_name": name
            }
            self.adapter._ticker_received_ts_by_instrument[name] = time.time()
        self.adapter._tickers_cache_ts = time.time()

        self.assertEqual(await self.adapter.fetch_option_tickers(), [])
        self.assertEqual(
            self.adapter.disabled_reason,
            "deribit_ws_ticker_cache_warming",
        )

    async def test_core_coverage_can_be_ready_while_full_chain_backfills(self):
        self.adapter._ws_instruments_count = 866
        self.adapter._ws_core_instrument_names = {
            f"BTC-14AUG26-{60_000 + index}-C"
            for index in range(10)
        }
        for index in range(7):
            name = f"BTC-14AUG26-{60_000 + index}-C"
            self.adapter._store_ticker_snapshot(
                {
                    "instrument_name": name,
                    "mark_iv": 55.0,
                    "greeks": {
                        "delta": 0.4,
                        "gamma": 0.00003,
                        "vega": 15.0,
                        "theta": -6.0,
                    },
                },
                stream_message=False,
            )

        self.assertEqual(len(await self.adapter.fetch_option_tickers()), 7)
        diagnostics = self.adapter.get_diagnostics()
        self.assertEqual(diagnostics["deribit_ws_core_fresh_tickers"], 7)
        self.assertEqual(diagnostics["deribit_ws_cache_coverage_ratio"], 0.7)
        self.assertLess(diagnostics["deribit_ws_chain_coverage_ratio"], 0.01)

    def test_core_universe_balances_contracts_across_expiries(self):
        instruments = []
        for expiry_index in range(4):
            for strike_index in range(100):
                instruments.append({
                    "instrument_name": (
                        f"BTC-{expiry_index}-{50_000 + strike_index}-C"
                    ),
                    "expiration_timestamp": expiry_index + 1,
                    "strike": 50_000 + strike_index,
                })
        self.adapter._spot_price = 50_050.0

        selected = self.adapter._select_core_instrument_names(instruments)

        self.assertEqual(len(selected), 240)
        self.assertEqual(
            {int(name.split("-")[1]) for name in selected},
            {0, 1, 2, 3},
        )
        for expiry_index in range(4):
            self.assertEqual(
                sum(
                    name.startswith(f"BTC-{expiry_index}-")
                    for name in selected
                ),
                60,
            )

    def test_ticker_idle_watchdog_starts_only_after_subscription_ack(self):
        now = time.time()
        self.assertFalse(self.adapter._is_ws_ticker_stream_idle(now=now))

        self.adapter._subscribed_ticker_channels.add(
            "incremental_ticker.BTC-14AUG26-65000-C"
        )
        self.adapter._ws_ticker_watch_started_ts = now - 61.0
        self.assertTrue(self.adapter._is_ws_ticker_stream_idle(now=now))

        self.adapter._ws_last_ticker_ts = now
        self.assertFalse(self.adapter._is_ws_ticker_stream_idle(now=now))

    async def test_silent_ticker_stream_forces_reconnect(self):
        self.adapter._running = True
        self.adapter._subscribed_ticker_channels.add(
            "incremental_ticker.BTC-14AUG26-65000-C"
        )
        self.adapter._ws_ticker_watch_started_ts = time.time() - 1.0

        with patch(
            "api.deribit_adapter._WS_RECEIVE_POLL_SEC",
            0.001,
        ), patch(
            "api.deribit_adapter._WS_TICKER_IDLE_TIMEOUT_SEC",
            0.01,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "deribit_ws_ticker_idle_timeout",
            ):
                await self.adapter._ws_receive_loop(_SilentWebSocket())

        self.assertEqual(self.adapter._ws_idle_reconnect_count, 1)
        self.assertGreater(self.adapter._ws_last_idle_reconnect_ts, 0.0)

    async def test_subscription_refresh_failure_is_not_silenced(self):
        self.adapter._running = True
        with patch.object(
            self.adapter,
            "_refresh_ws_option_subscriptions",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await self.adapter._ws_subscription_refresh_loop(
                    _FakeWebSocket()
                )

        self.assertFalse(self.adapter._ws_refresh_task_running)
        self.assertEqual(self.adapter._ws_refresh_loop_error_count, 1)
        self.assertEqual(
            self.adapter.last_error,
            "ws_refresh_loop_error:boom",
        )

    async def test_connection_supervisor_reconnects_if_refresh_loop_stops(self):
        self.adapter._running = True

        async def wait_for_messages(_ws):
            await asyncio.Event().wait()

        with patch.object(
            self.adapter,
            "_ws_receive_loop",
            new=AsyncMock(side_effect=wait_for_messages),
        ), patch.object(
            self.adapter,
            "_ws_subscription_refresh_loop",
            new=AsyncMock(side_effect=RuntimeError("refresh stopped")),
        ):
            with self.assertRaisesRegex(RuntimeError, "refresh stopped"):
                await self.adapter._supervise_ws_connection(_FakeWebSocket())

    async def test_ws_rpc_error_requests_subscription_retry(self):
        self.adapter._subscribed_ticker_channels.add(
            "incremental_ticker.BTC-14AUG26-65000-C"
        )
        failed_channel = "incremental_ticker.BTC-14AUG26-66000-C"
        self.adapter._pending_ticker_channels[7] = {failed_channel}

        await self.adapter._handle_ws_message({
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": 10028, "message": "too_many_requests"},
        })

        self.assertEqual(
            self.adapter._subscribed_ticker_channels,
            {"incremental_ticker.BTC-14AUG26-65000-C"},
        )
        self.assertNotIn(7, self.adapter._pending_ticker_channels)
        self.assertTrue(self.adapter._ws_subscription_retry_event.is_set())
        self.assertEqual(self.adapter._ws_subscription_error_count, 1)

    async def test_partial_subscription_ack_retries_only_missing_channels(self):
        websocket = _FakeWebSocket()
        channels = [
            "incremental_ticker.BTC-14AUG26-65000-C",
            "incremental_ticker.BTC-14AUG26-66000-C",
        ]
        request_id = await self.adapter._ws_subscribe(
            websocket,
            channels,
            track_ticker_channels=True,
        )

        await self.adapter._handle_ws_message({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": channels[:1],
        })
        acknowledged = await self.adapter._wait_for_subscription_ack(
            request_id,
            timeout=0.01,
        )

        self.assertFalse(acknowledged)
        self.assertEqual(
            self.adapter._subscribed_ticker_channels,
            {channels[0]},
        )
        self.assertEqual(self.adapter._pending_ticker_channels, {})
        self.assertTrue(self.adapter._ws_subscription_retry_event.is_set())
        self.assertEqual(
            self.adapter.last_error,
            "ws_subscription_partial_ack:1",
        )


class DeribitTickerNormalizerTest(unittest.TestCase):
    def test_normalizes_nested_ws_greeks_stats_and_prices(self):
        normalized = InstrumentNormalizer.normalize_ticker("deribit", {
            "instrument_name": "BTC-14AUG26-65000-P",
            "open_interest": 44.0,
            "mark_iv": 62.5,
            "bid_iv": 61.0,
            "ask_iv": 64.0,
            "mark_price": 0.031,
            "last_price": 0.030,
            "best_bid_price": 0.029,
            "best_ask_price": 0.032,
            "underlying_price": 63_200.0,
            "stats": {"volume": 8.75},
            "greeks": {
                "delta": -0.61,
                "gamma": 0.000028,
                "vega": 14.2,
                "theta": -6.4,
            },
        })

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["canonical_id"], "BTC-20260814-65000-P")
        self.assertEqual(normalized["volume"], 8.75)
        self.assertAlmostEqual(normalized["markIv"], 0.625)
        self.assertEqual(normalized["delta"], -0.61)
        self.assertEqual(normalized["gamma"], 0.000028)
        self.assertEqual(normalized["vega"], 14.2)
        self.assertEqual(normalized["theta"], -6.4)
        self.assertEqual(normalized["lastPrice"], 0.030)
        self.assertEqual(normalized["bidPrice"], 0.029)
        self.assertEqual(normalized["askPrice"], 0.032)


if __name__ == "__main__":
    unittest.main()
