import asyncio
import json
import sys
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


class _FakeTickerRpcWebSocket:
    def __init__(self):
        self.messages = []
        self.responses = asyncio.Queue()

    async def send(self, message):
        parsed = json.loads(message)
        self.messages.append(parsed)
        instrument_name = parsed["params"]["instrument_name"]
        await self.responses.put(json.dumps({
            "jsonrpc": "2.0",
            "id": parsed["id"],
            "result": {
                "instrument_name": instrument_name,
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
            },
        }))

    async def recv(self):
        return await self.responses.get()


class DeribitWsTickerCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = DeribitAdapter()

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
            "websocket_incremental_ticker_cache+rpc_bootstrap",
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

    async def test_full_ticker_rpc_batch_seeds_greeks_for_every_contract(self):
        websocket = _FakeTickerRpcWebSocket()
        instrument_names = [
            "BTC-14AUG26-65000-C",
            "BTC-14AUG26-65000-P",
        ]

        successes = await self.adapter._bootstrap_ticker_batch(
            websocket,
            instrument_names,
        )

        self.assertEqual(successes, 2)
        self.assertEqual(len(websocket.messages), 2)
        self.assertTrue(all(
            message["method"] == "public/ticker"
            for message in websocket.messages
        ))
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
        ):
            first = await self.adapter.fetch_instruments()
            self.adapter._instruments_cache_ts = 0.0
            fallback = await self.adapter.fetch_instruments()

        self.assertEqual(first, instruments)
        self.assertEqual(fallback, instruments)

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
        self.assertEqual(len(websocket.messages), 3)
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
