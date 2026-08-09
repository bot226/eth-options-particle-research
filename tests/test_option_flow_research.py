import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.scripts.option_flow_research import (
    EnrichedOptionTrade,
    FlowFeaturePoint,
    FuturesCandle,
    aggregate_trade_flow_buckets,
    build_futures_outcomes,
    day_block_bootstrap_ci,
    evaluate_direction_rules,
    evaluate_range_rules,
    holm_adjust,
    load_protocol,
    load_enriched_option_trades,
    prior_day_quantile_observations,
    promotion_decision,
    protocol_content_sha256,
    rolling_flow_feature_points,
    shared_day_max_t_p_values,
)


def _timestamp(day: int, minute: int = 0) -> float:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return (start + timedelta(days=day, minutes=minute)).timestamp()


class WalkForwardThresholdTest(unittest.TestCase):
    def test_future_outlier_cannot_change_earlier_threshold_or_selection(self):
        base = [
            (_timestamp(0), 1.0),
            (_timestamp(1), -1.0),
            (_timestamp(2), 2.0),
            (_timestamp(3), 3.0),
        ]
        before = prior_day_quantile_observations(
            base, training_days=2, quantile=0.8
        )
        after = prior_day_quantile_observations(
            [*base, (_timestamp(4), 1_000_000.0)],
            training_days=2,
            quantile=0.8,
        )

        earlier_after = [item for item in after if item.timestamp_utc < _timestamp(4)]
        self.assertEqual(before, earlier_after)
        self.assertEqual(before[0].threshold, 1.0)

    def test_invalid_walk_forward_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            prior_day_quantile_observations([], training_days=0, quantile=0.8)
        with self.assertRaises(ValueError):
            prior_day_quantile_observations([], training_days=1, quantile=1.0)


class MultipleTestingTest(unittest.TestCase):
    def test_holm_adjustment_is_step_down_and_bounded(self):
        adjusted = holm_adjust({"a": 0.01, "b": 0.02, "c": 0.5})
        self.assertAlmostEqual(adjusted["a"], 0.03)
        self.assertAlmostEqual(adjusted["b"], 0.04)
        self.assertAlmostEqual(adjusted["c"], 0.5)

    def test_shared_day_max_t_penalizes_against_best_rule(self):
        positive = {f"2026-08-{day:02d}": 0.10 for day in range(1, 13)}
        mixed = {
            f"2026-08-{day:02d}": (0.10 if day % 2 else -0.10)
            for day in range(1, 13)
        }
        result = shared_day_max_t_p_values(
            {"positive": positive, "mixed": mixed}, samples=1000, seed=226
        )
        self.assertGreaterEqual(result["mixed"], result["positive"])
        self.assertTrue(all(0 < value <= 1 for value in result.values()))

    def test_day_bootstrap_resamples_whole_days_deterministically(self):
        blocks = {
            "2026-08-01": [0.1, 0.2],
            "2026-08-02": [-0.1, 0.0],
            "2026-08-03": [0.2, 0.3],
        }
        first = day_block_bootstrap_ci(blocks, samples=500, seed=10)
        second = day_block_bootstrap_ci(blocks, samples=500, seed=10)
        self.assertEqual(first, second)
        self.assertTrue(all(math.isfinite(value) for value in first))


class FrozenPromotionGateTest(unittest.TestCase):
    def setUp(self):
        self.protocol = load_protocol()

    def test_statistical_confirmation_still_cannot_change_live_entries(self):
        summary = {
            "trades": 100,
            "test_days": 10,
            "positive_day_ratio_15bps": 0.8,
            "mean_net_pct_by_cost": {"6": 0.10, "10": 0.08, "15": 0.03},
            "ci_95_15bps": [0.01, 0.05],
            "holm_p": 0.01,
            "max_t_p": 0.02,
            "exchange_sign_confirmation": True,
        }
        decision = promotion_decision(summary, self.protocol)
        self.assertTrue(decision["statistically_confirmed"])
        self.assertFalse(decision["promote"])

    def test_failure_at_highest_cost_rejects_confirmation(self):
        summary = {
            "trades": 100,
            "test_days": 10,
            "positive_day_ratio_15bps": 0.8,
            "mean_net_pct_by_cost": {"6": 0.10, "10": 0.04, "15": -0.01},
            "ci_95_15bps": [0.01, 0.05],
            "holm_p": 0.01,
            "max_t_p": 0.02,
            "exchange_sign_confirmation": True,
        }
        decision = promotion_decision(summary, self.protocol)
        self.assertFalse(decision["statistically_confirmed"])
        self.assertFalse(decision["checks"]["positive_every_cost"])

    def test_protocol_content_hash_changes_if_threshold_changes(self):
        first = protocol_content_sha256(self.protocol)
        changed = json.loads(json.dumps(self.protocol))
        changed["signal_quantile"] = 0.81
        self.assertNotEqual(first, protocol_content_sha256(changed))


class FeatureConstructionTest(unittest.TestCase):
    def _trade(self, **overrides):
        values = {
            "exchange": "bybit",
            "trade_id": "trade",
            "timestamp_utc": 100.0,
            "contract_id": "BTC-20260925-70000-C",
            "expiry": "20260925",
            "strike": 70000.0,
            "option_type": "C",
            "taker_side": "BUY",
            "size": 2.0,
            "trade_iv_decimal": 0.5,
            "is_block_trade": False,
            "is_combo_trade": False,
            "snapshot_timestamp_utc": 90.0,
            "delta": 0.5,
            "gamma": 0.01,
            "vega": 2.0,
            "snapshot_mark_iv": 0.49,
            "underlying_price": 70000.0,
        }
        values.update(overrides)
        return EnrichedOptionTrade(**values)

    def test_contract_and_delta_flow_respect_call_put_and_taker_side(self):
        call_buy = self._trade(trade_id="call", size=2.0, delta=0.5)
        put_buy = self._trade(
            trade_id="put",
            option_type="P",
            contract_id="BTC-20260925-70000-P",
            size=1.0,
            delta=-0.4,
        )
        buckets = aggregate_trade_flow_buckets([call_buy, put_buy])
        points = rolling_flow_feature_points(buckets, lookbacks_sec=[300])
        point = next(
            item
            for item in points
            if item.scope == "bybit"
            and item.trade_filter == "all"
            and item.segment == "all"
        )
        self.assertAlmostEqual(point.call_put_contract_imbalance, 1.0 / 3.0)
        self.assertAlmostEqual(point.signed_delta_imbalance, 0.6 / 1.4)
        self.assertEqual(point.trade_intensity, 2)
        self.assertEqual(point.greek_match_ratio, 1.0)

    def test_block_trade_is_retained_in_all_but_excluded_from_clean_filter(self):
        ordinary = self._trade(trade_id="ordinary")
        block = self._trade(trade_id="block", is_block_trade=True)
        buckets = aggregate_trade_flow_buckets([ordinary, block])
        points = rolling_flow_feature_points(buckets, lookbacks_sec=[300])
        all_point = next(
            item
            for item in points
            if item.scope == "bybit"
            and item.trade_filter == "all"
            and item.segment == "all"
        )
        clean_point = next(
            item
            for item in points
            if item.scope == "bybit"
            and item.trade_filter == "non_block_non_combo"
            and item.segment == "all"
        )
        self.assertEqual(all_point.trade_intensity, 2)
        self.assertEqual(clean_point.trade_intensity, 1)

    def test_rolling_window_never_includes_a_later_bucket(self):
        earlier = self._trade(trade_id="earlier", timestamp_utc=100.0, size=1.0)
        later = self._trade(
            trade_id="later", timestamp_utc=400.0, size=9.0, taker_side="SELL"
        )
        points = rolling_flow_feature_points(
            aggregate_trade_flow_buckets([earlier, later]), lookbacks_sec=[900]
        )
        first = next(
            item
            for item in points
            if item.scope == "bybit"
            and item.trade_filter == "all"
            and item.segment == "all"
            and item.timestamp_utc == 300.0
        )
        second = next(
            item
            for item in points
            if item.scope == "bybit"
            and item.trade_filter == "all"
            and item.segment == "all"
            and item.timestamp_utc == 600.0
        )
        self.assertEqual(first.trade_intensity, 1)
        self.assertAlmostEqual(first.call_put_contract_imbalance, 1.0)
        self.assertEqual(second.trade_intensity, 2)
        self.assertAlmostEqual(second.call_put_contract_imbalance, -0.8)


class EarlierGreekJoinTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = {
            "option_trade_flow.db": root / "option_trade_flow.db",
            "history.db": root / "history.db",
            "mos_research.db": root / "mos_research.db",
        }
        flow = sqlite3.connect(self.paths["option_trade_flow.db"])
        try:
            flow.execute(
                """
                CREATE TABLE option_trades (
                    exchange TEXT, trade_id TEXT, trade_timestamp_utc REAL,
                    contract_id TEXT, expiry TEXT, strike REAL, option_type TEXT,
                    taker_side TEXT, contracts REAL, amount REAL,
                    trade_iv_decimal REAL, is_block_trade INTEGER,
                    is_combo_trade INTEGER
                )
                """
            )
            flow.execute(
                "INSERT INTO option_trades VALUES "
                "('bybit','t1',1000,'BTC-20260925-70000-C','20260925',70000,'C',"
                "'BUY',1,1,0.5,0,0)"
            )
            flow.commit()
        finally:
            flow.close()
        history = sqlite3.connect(self.paths["history.db"])
        try:
            history.execute(
                """
                CREATE TABLE option_contract_snapshots (
                    exchange TEXT, contract_id TEXT, ts REAL, delta REAL,
                    gamma REAL, vega REAL, mark_iv REAL, underlying_price REAL
                )
                """
            )
            history.executemany(
                "INSERT INTO option_contract_snapshots VALUES (?,?,?,?,?,?,?,?)",
                [
                    ("bybit", "BTC-20260925-70000-C", 900, 0.4, 0.01, 2, 0.49, 70000),
                    ("bybit", "BTC-20260925-70000-C", 1010, 0.9, 0.09, 9, 0.80, 80000),
                ],
            )
            history.execute(
                "CREATE INDEX idx_contract ON option_contract_snapshots(exchange, contract_id, ts)"
            )
            history.commit()
        finally:
            history.close()

    def test_join_uses_latest_prior_snapshot_never_future_snapshot(self):
        trades = load_enriched_option_trades(self.paths, greek_max_age_sec=600)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].snapshot_timestamp_utc, 900.0)
        self.assertEqual(trades[0].delta, 0.4)

    def test_stale_prior_snapshot_drops_greeks_but_preserves_raw_trade(self):
        trades = load_enriched_option_trades(self.paths, greek_max_age_sec=50)
        self.assertEqual(len(trades), 1)
        self.assertIsNone(trades[0].snapshot_timestamp_utc)
        self.assertIsNone(trades[0].delta)


class FuturesOutcomeAlignmentTest(unittest.TestCase):
    def test_outcome_uses_only_candles_after_decision_for_label(self):
        candles = [
            FuturesCandle(float(minute * 60), 100.0 + minute, 99.0, 100.0 + minute)
            for minute in range(91)
        ]
        outcome = build_futures_outcomes(
            candles,
            [1800.0],
            horizons_sec=[900],
            max_alignment_sec=1,
        )[(1800.0, 900)]
        self.assertEqual(outcome.start_close, 130.0)
        self.assertEqual(outcome.end_close, 145.0)
        self.assertAlmostEqual(outcome.future_return_pct, (145 / 130 - 1) * 100)


class FrozenDirectionEvaluationTest(unittest.TestCase):
    def test_profitable_synthetic_flow_requires_cross_exchange_confirmation(self):
        protocol = load_protocol()
        protocol.update(
            {
                "training_days": 2,
                "minimum_test_days": 3,
                "minimum_trades": 3,
                "minimum_positive_day_ratio": 0.6,
                "signal_quantile": 0.5,
                "horizons_sec": [300],
                "direction_features": ["call_put_contract_imbalance"],
                "contexts": ["all"],
                "direction_modes": ["direct"],
                "bootstrap_samples": 200,
                "permutation_samples": 1000,
            }
        )
        points = []
        outcomes = {}
        healthy = set()
        for day in range(10):
            for slot, value in enumerate((0.8, -0.9)):
                timestamp = _timestamp(day, 60 + slot * 10)
                healthy.add(timestamp)
                for scope in ("combined", "bybit", "deribit"):
                    points.append(
                        FlowFeaturePoint(
                            timestamp_utc=timestamp,
                            scope=scope,
                            trade_filter="all",
                            segment="all",
                            lookback_sec=300,
                            call_put_contract_imbalance=value,
                            signed_delta_imbalance=value,
                            absolute_signed_gamma_imbalance=abs(value),
                            absolute_signed_vega_imbalance=abs(value),
                            trade_intensity=10,
                            greek_match_ratio=1.0,
                        )
                    )
                outcomes[(timestamp, 300)] = type(
                    "Outcome",
                    (),
                    {
                        "future_return_pct": (0.5 if value > 0 else -0.5),
                        "false_sweep_direction": 0,
                    },
                )()

        rules = evaluate_direction_rules(points, outcomes, healthy, protocol)
        combined = next(rule for rule in rules if rule["spec"]["scope"] == "combined")

        self.assertTrue(combined["exchange_sign_confirmation"])
        self.assertGreater(combined["mean_net_pct_by_cost"]["15"], 0)
        self.assertTrue(combined["promotion"]["statistically_confirmed"])
        self.assertFalse(combined["promotion"]["promote"])

    def test_high_synthetic_gamma_flow_predicts_range_without_becoming_entry(self):
        protocol = load_protocol()
        protocol.update(
            {
                "training_days": 2,
                "minimum_test_days": 3,
                "minimum_trades": 3,
                "signal_quantile": 0.5,
                "horizons_sec": [300],
                "range_features": ["absolute_signed_gamma_imbalance"],
                "contexts": ["all"],
                "bootstrap_samples": 200,
                "permutation_samples": 1000,
            }
        )
        points = []
        outcomes = {}
        healthy = set()
        for day in range(10):
            for slot, value in enumerate((0.9, 0.1)):
                timestamp = _timestamp(day, 60 + slot * 10)
                healthy.add(timestamp)
                for scope in ("combined", "bybit", "deribit"):
                    points.append(
                        FlowFeaturePoint(
                            timestamp_utc=timestamp,
                            scope=scope,
                            trade_filter="all",
                            segment="all",
                            lookback_sec=300,
                            call_put_contract_imbalance=value,
                            signed_delta_imbalance=value,
                            absolute_signed_gamma_imbalance=value,
                            absolute_signed_vega_imbalance=value,
                            trade_intensity=10,
                            greek_match_ratio=1.0,
                        )
                    )
                outcomes[(timestamp, 300)] = type(
                    "Outcome",
                    (),
                    {
                        "future_range_pct": (1.0 if value > 0.5 else 0.2),
                        "false_sweep_direction": 0,
                    },
                )()

        rules = evaluate_range_rules(points, outcomes, healthy, protocol)
        combined = next(rule for rule in rules if rule["spec"]["scope"] == "combined")

        self.assertTrue(combined["exchange_sign_confirmation"])
        self.assertGreater(combined["mean_range_lift_pct_points"], 0)
        self.assertTrue(combined["statistically_confirmed"])
        self.assertFalse(combined["live_entry_changes_allowed"])


if __name__ == "__main__":
    unittest.main()
