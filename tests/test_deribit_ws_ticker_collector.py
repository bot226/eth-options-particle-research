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
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


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
                "channel": "ticker.BTC-14AUG26-65000-C.agg2",
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
        self.assertEqual(diagnostics["deribit_data_transport"], "websocket_ticker_cache")
        self.assertEqual(diagnostics["deribit_ws_cached_tickers"], 1)

    async def test_subscription_refresh_batches_channels_and_purges_expired_cache(self):
        instruments = [
            {"instrument_name": f"BTC-14AUG26-{50_000 + index}-C"}
            for index in range(866)
        ]
        self.adapter._ticker_cache_by_instrument["BTC-3AUG26-40000-P"] = {
            "instrument_name": "BTC-3AUG26-40000-P"
        }
        websocket = _FakeWebSocket()

        with patch.object(
            self.adapter,
            "fetch_instruments",
            new=AsyncMock(return_value=instruments),
        ):
            refreshed = await self.adapter._refresh_ws_option_subscriptions(
                websocket
            )

        self.assertTrue(refreshed)
        self.assertEqual(len(websocket.messages), 2)
        subscribed = {
            channel
            for message in websocket.messages
            for channel in message["params"]["channels"]
        }
        self.assertEqual(len(subscribed), 866)
        self.assertEqual(len(self.adapter._subscribed_ticker_channels), 0)
        self.assertEqual(
            sum(
                len(channels)
                for channels in self.adapter._pending_ticker_channels.values()
            ),
            866,
        )
        self.assertNotIn(
            "BTC-3AUG26-40000-P",
            self.adapter._ticker_cache_by_instrument,
        )

        for message in websocket.messages:
            await self.adapter._handle_ws_message({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": message["params"]["channels"],
            })

        self.assertEqual(len(self.adapter._subscribed_ticker_channels), 866)
        self.assertEqual(self.adapter._pending_ticker_channels, {})

    async def test_stale_ws_cache_is_not_returned_to_live_mos(self):
        self.adapter._ticker_cache_by_instrument["BTC-14AUG26-65000-P"] = {
            "instrument_name": "BTC-14AUG26-65000-P"
        }
        self.adapter._tickers_cache_ts = time.time() - 31

        self.assertEqual(await self.adapter.fetch_option_tickers(), [])
        self.assertEqual(
            self.adapter.disabled_reason,
            "deribit_ws_ticker_cache_stale",
        )

    async def test_partially_warmed_chain_is_not_returned_to_live_mos(self):
        self.adapter._ws_instruments_count = 10
        for index in range(6):
            name = f"BTC-14AUG26-{60_000 + index}-C"
            self.adapter._ticker_cache_by_instrument[name] = {
                "instrument_name": name
            }
        self.adapter._tickers_cache_ts = time.time()

        self.assertEqual(await self.adapter.fetch_option_tickers(), [])
        self.assertEqual(
            self.adapter.disabled_reason,
            "deribit_ws_ticker_cache_warming",
        )

    async def test_ws_rpc_error_requests_subscription_retry(self):
        self.adapter._subscribed_ticker_channels.add(
            "ticker.BTC-14AUG26-65000-C.agg2"
        )
        failed_channel = "ticker.BTC-14AUG26-66000-C.agg2"
        self.adapter._pending_ticker_channels[7] = {failed_channel}

        await self.adapter._handle_ws_message({
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": 10028, "message": "too_many_requests"},
        })

        self.assertEqual(
            self.adapter._subscribed_ticker_channels,
            {"ticker.BTC-14AUG26-65000-C.agg2"},
        )
        self.assertNotIn(7, self.adapter._pending_ticker_channels)
        self.assertTrue(self.adapter._ws_subscription_retry_event.is_set())
        self.assertEqual(self.adapter._ws_subscription_error_count, 1)

    async def test_partial_subscription_ack_retries_only_missing_channels(self):
        websocket = _FakeWebSocket()
        channels = [
            "ticker.BTC-14AUG26-65000-C.agg2",
            "ticker.BTC-14AUG26-66000-C.agg2",
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
