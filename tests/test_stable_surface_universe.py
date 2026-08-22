import asyncio
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api.deribit_adapter import (
    DeribitAdapter,
    _STABLE_SURFACE_UNIVERSE_TTL_SEC,
)
from engine.history_db import HistoryDB
from engine.instrument_normalizer import InstrumentNormalizer
from engine.multi_data_manager import MultiExchangeDataManager


def _instruments(count=260):
    expiry = int((time.time() + (30 * 24 * 3600)) * 1000)
    result = []
    for index in range(count):
        strike = 2_000 + (index // 2) * 500
        option_type = "C" if index % 2 == 0 else "P"
        result.append({
            "instrument_name": f"ETH-30AUG26-{strike}-{option_type}",
            "expiration_timestamp": expiry,
            "strike": strike,
        })
    return result


def _full_ticker(instrument_name):
    return {
        "instrument_name": instrument_name,
        "mark_iv": 55.0,
        "greeks": {
            "delta": 0.5,
            "gamma": 0.0001,
            "vega": 10.0,
            "theta": -5.0,
        },
    }


def _normalized_ticker(symbol, index=0):
    return {
        "canonical_id": f"ETH:2026-08-30:{2_000 + index * 500}:C",
        "exchange": "deribit",
        "symbol": symbol,
        "expiry": "2026-08-30",
        "strike": 2_000 + index * 500,
        "type": "C",
        "oi": 10.0,
        "volume": 3.0,
        "markIv": 55.0,
        "bidIv": 54.0,
        "askIv": 56.0,
        "delta": 0.5,
        "gamma": 0.0001,
        "vega": 10.0,
        "theta": -5.0,
        "markPrice": 0.01,
        "underlyingPrice": 4_000.0,
    }


class StableSurfaceUniverseTests(unittest.TestCase):
    def test_single_digit_expiry_reaches_normalized_surface_payload(self):
        names = [
            f"ETH-4SEP26-{3_000 + index * 500}-{option_type}"
            for index in range(10)
            for option_type in ("C", "P")
        ]

        class _Adapter:
            exchange_id = "deribit"

            @staticmethod
            def get_stable_surface_snapshot():
                return {
                    "universe_id": "single-digit-expiry",
                    "target_contracts": len(names),
                    "fresh_contracts": len(names),
                    "coverage_ratio": 1.0,
                    "minimum_coverage_ratio": 0.95,
                    "snapshot_valid": True,
                    "invalid_reason": "",
                    "contract_names": names,
                    "target_contract_names": names,
                }

        raw_tickers = [_full_ticker(name) for name in names]
        normalized = [
            InstrumentNormalizer.normalize_ticker("deribit", ticker)
            for ticker in raw_tickers
        ]
        self.assertNotIn(None, normalized)

        manager = MultiExchangeDataManager.__new__(MultiExchangeDataManager)
        manager.adapters = {"deribit": _Adapter()}
        manager.per_exchange_tickers = {"deribit": normalized}
        payload = manager._stable_surface_history_payload()

        self.assertTrue(payload["snapshot_valid"])
        self.assertEqual(len(names), payload["fresh_contracts"])
        self.assertEqual(1.0, payload["coverage_ratio"])
        self.assertEqual(len(names), len(payload["contract_data"]))

    def test_universe_is_fixed_before_24h_and_restored_after_restart(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temp_dir:
                state_path = Path(temp_dir) / "surface.json"
                instruments = _instruments()
                adapter = DeribitAdapter(
                    instrument_cache_path=None,
                    stable_surface_state_path=state_path,
                )
                try:
                    adapter._spot_price = 4_000.0
                    first_names, changed = adapter._resolve_stable_surface_universe(
                        instruments,
                        now=1_000_000.0,
                    )
                    self.assertTrue(changed)
                    self.assertEqual(240, len(first_names))
                    first_id = adapter._stable_surface_universe_id
                    first_snapshot = adapter.get_stable_surface_snapshot()
                    self.assertEqual(
                        1_000_000.0 + _STABLE_SURFACE_UNIVERSE_TTL_SEC,
                        first_snapshot["next_planned_rotation_ts"],
                    )
                    await adapter._persist_stable_surface_universe()

                    adapter._spot_price = 6_000.0
                    same_names, changed = adapter._resolve_stable_surface_universe(
                        list(reversed(instruments)),
                        now=1_000_100.0,
                    )
                    self.assertFalse(changed)
                    self.assertEqual(first_names, same_names)
                    self.assertEqual(first_id, adapter._stable_surface_universe_id)
                finally:
                    await adapter._http.aclose()

                restored = DeribitAdapter(
                    instrument_cache_path=None,
                    stable_surface_state_path=state_path,
                )
                try:
                    self.assertEqual(first_id, restored._stable_surface_universe_id)
                    self.assertEqual(first_names, restored._stable_surface_universe_names)
                    self.assertEqual(1, restored._stable_surface_disk_load_count)
                finally:
                    await restored._http.aclose()

        asyncio.run(scenario())

    def test_scheduled_rotation_changes_universe_id(self):
        async def scenario():
            adapter = DeribitAdapter(instrument_cache_path=None)
            try:
                instruments = _instruments()
                adapter._spot_price = 4_000.0
                first_names, _ = adapter._resolve_stable_surface_universe(
                    instruments,
                    now=1_000_000.0,
                )
                first_id = adapter._stable_surface_universe_id
                adapter._spot_price = 5_000.0
                second_names, changed = adapter._resolve_stable_surface_universe(
                    instruments,
                    now=1_000_000.0 + _STABLE_SURFACE_UNIVERSE_TTL_SEC,
                )
                self.assertTrue(changed)
                self.assertNotEqual(first_id, adapter._stable_surface_universe_id)
                self.assertNotEqual(first_names, second_names)
                self.assertEqual("scheduled_24h_rotation", adapter._stable_surface_last_rotation_reason)
                self.assertEqual(1, adapter._stable_surface_replacement_count)
            finally:
                await adapter._http.aclose()

        asyncio.run(scenario())

    def test_snapshot_excludes_rotating_tail_and_enforces_95pct_gate(self):
        async def scenario():
            adapter = DeribitAdapter(instrument_cache_path=None)
            try:
                instruments = _instruments()
                core_names, _ = adapter._resolve_stable_surface_universe(instruments)
                tail_name = next(
                    item["instrument_name"]
                    for item in instruments
                    if item["instrument_name"] not in set(core_names)
                )
                for name in core_names:
                    adapter._store_ticker_snapshot(
                        _full_ticker(name),
                        stream_message=False,
                    )
                adapter._store_ticker_snapshot(
                    _full_ticker(tail_name),
                    stream_message=False,
                )
                snapshot = adapter.get_stable_surface_snapshot()
                self.assertTrue(snapshot["snapshot_valid"])
                self.assertEqual(240, snapshot["fresh_contracts"])
                self.assertNotIn(tail_name, snapshot["contract_names"])

                adapter._ticker_cache_by_instrument[core_names[0]]["mark_iv"] = 0.0
                snapshot = adapter.get_stable_surface_snapshot()
                self.assertTrue(snapshot["snapshot_valid"])
                self.assertEqual(239, snapshot["fresh_contracts"])

                for name in core_names[1:13]:
                    adapter._ticker_received_ts_by_instrument[name] = 0.0
                snapshot = adapter.get_stable_surface_snapshot()
                self.assertFalse(snapshot["snapshot_valid"])
                self.assertEqual("fresh_coverage_below_95pct", snapshot["invalid_reason"])
                self.assertEqual([], snapshot["contract_names"])
            finally:
                await adapter._http.aclose()

        asyncio.run(scenario())

    def test_normalized_payload_cannot_pass_with_missing_members(self):
        class _Adapter:
            exchange_id = "deribit"

            @staticmethod
            def get_stable_surface_snapshot():
                names = [f"ETH-30AUG26-{2_000 + index * 500}-C" for index in range(20)]
                return {
                    "universe_id": "stable-1",
                    "target_contracts": 20,
                    "fresh_contracts": 20,
                    "coverage_ratio": 1.0,
                    "minimum_coverage_ratio": 0.95,
                    "snapshot_valid": True,
                    "invalid_reason": "",
                    "contract_names": names,
                    "target_contract_names": names,
                }

        manager = MultiExchangeDataManager.__new__(MultiExchangeDataManager)
        manager.adapters = {"deribit": _Adapter()}
        manager.per_exchange_tickers = {
            "deribit": [
                _normalized_ticker(
                    f"ETH-30AUG26-{2_000 + index * 500}-C",
                    index,
                )
                for index in range(18)
            ]
        }
        payload = manager._stable_surface_history_payload()
        self.assertFalse(payload["snapshot_valid"])
        self.assertEqual("normalized_coverage_below_95pct", payload["invalid_reason"])
        self.assertEqual([], payload["contract_data"])


class StableSurfaceStorageTests(unittest.TestCase):
    def test_surface_changes_require_same_valid_universe(self):
        first = {"universe_id": "epoch-a", "snapshot_valid": True}
        same_epoch = {"universe_id": "epoch-a", "snapshot_valid": True}
        next_epoch = {"universe_id": "epoch-b", "snapshot_valid": True}
        invalid = {"universe_id": "epoch-a", "snapshot_valid": False}

        self.assertTrue(
            HistoryDB.surface_snapshots_are_comparable(first, same_epoch)
        )
        self.assertFalse(
            HistoryDB.surface_snapshots_are_comparable(first, next_epoch)
        )
        self.assertFalse(
            HistoryDB.surface_snapshots_are_comparable(first, invalid)
        )

    def test_valid_and_invalid_surface_snapshots_are_separate_from_raw_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "history.db")
            history = HistoryDB(db_path)
            try:
                valid_contracts = [
                    _normalized_ticker("ETH-30AUG26-2000-C", 0),
                    _normalized_ticker("ETH-30AUG26-2500-C", 1),
                ]
                surface = {
                    "exchange": "deribit",
                    "asset": "ETH",
                    "universe_id": "stable-1",
                    "created_ts": 100.0,
                    "selection_spot": 4_000.0,
                    "selection_rule_version": "v71_balanced_core_24h",
                    "target_contracts": 2,
                    "fresh_contracts": 2,
                    "coverage_ratio": 1.0,
                    "universe_age_sec": 10.0,
                    "replacement_count": 0,
                    "snapshot_valid": True,
                    "invalid_reason": "",
                    "target_contract_names": [item["symbol"] for item in valid_contracts],
                    "contract_data": valid_contracts,
                }
                history.save_snapshot(
                    110.0,
                    {},
                    contract_data=valid_contracts,
                    stable_surface_data=surface,
                )

                invalid = dict(surface)
                invalid.update({
                    "fresh_contracts": 2,
                    "coverage_ratio": 1.0,
                    "snapshot_valid": True,
                    "invalid_reason": "",
                    "contract_data": valid_contracts[:1],
                })
                history.save_snapshot(
                    120.0,
                    {},
                    stable_surface_data=invalid,
                )
            finally:
                history.close()

            conn = sqlite3.connect(db_path)
            try:
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM option_contract_snapshots").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM option_surface_universes").fetchone()[0])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM option_surface_snapshots").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT SUM(snapshot_valid) FROM option_surface_snapshots").fetchone()[0])
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM option_surface_contract_snapshots").fetchone()[0])
                self.assertEqual(
                    "persisted_coverage_below_95pct",
                    conn.execute(
                        "SELECT invalid_reason FROM option_surface_snapshots WHERE snapshot_valid = 0"
                    ).fetchone()[0],
                )
                self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
                stored_names = json.loads(conn.execute(
                    "SELECT contract_ids_json FROM option_surface_universes"
                ).fetchone()[0])
                self.assertEqual(2, len(stored_names))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
