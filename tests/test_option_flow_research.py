import io
import json
import math
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.scripts.option_flow_research import (
    EnrichedOptionTrade,
    DecisionContext,
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
    load_decision_contexts,
    main,
    prior_day_quantile_observations,
    promotion_decision,
    protocol_content_sha256,
    render_human_summary,
    rolling_flow_feature_points,
    run_frozen_analysis,
    shared_day_max_t_p_values,
    _feature_value,
    _context_matches,
    _healthy_lookback_window,
)
from backend.workers.option_trade_flow_worker import (
    OptionTradeFlowStore,
    normalize_bybit_trade,
    normalize_deribit_trade,
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
            "ci_95_vs_price_continuation": [0.01, 0.08],
            "ci_95_vs_price_reversal": [0.01, 0.08],
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
            "ci_95_vs_price_continuation": [0.01, 0.08],
            "ci_95_vs_price_reversal": [0.01, 0.08],
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
            "contract_id": "ETH-20260925-70000-C",
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
            contract_id="ETH-20260925-70000-P",
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

    def test_preregistered_combinations_have_fixed_economic_formulas(self):
        point = FlowFeaturePoint(
            timestamp_utc=300,
            scope="combined",
            trade_filter="all",
            segment="all",
            lookback_sec=300,
            call_put_contract_imbalance=0.6,
            signed_delta_imbalance=0.4,
            absolute_signed_gamma_imbalance=0.5,
            absolute_signed_vega_imbalance=0.3,
            trade_intensity=9,
            greek_match_ratio=1.0,
        )
        self.assertAlmostEqual(_feature_value(point, "delta_contract_consensus"), 0.5)
        self.assertAlmostEqual(_feature_value(point, "delta_gamma_interaction"), 0.2)
        self.assertAlmostEqual(_feature_value(point, "gamma_vega_joint"), 0.3)
        self.assertAlmostEqual(
            _feature_value(point, "delta_activity_interaction"),
            0.4 * math.log(10),
        )
        disagree = FlowFeaturePoint(
            **{
                **point.__dict__,
                "signed_delta_imbalance": -0.4,
            }
        )
        self.assertEqual(_feature_value(disagree, "delta_contract_consensus"), 0.0)

    def test_long_lookback_requires_every_health_bucket(self):
        timestamp = 1800.0
        complete = {1200.0, 1500.0, 1800.0}
        self.assertTrue(
            _healthy_lookback_window(
                timestamp, 900, complete, interval_sec=300
            )
        )
        self.assertFalse(
            _healthy_lookback_window(
                timestamp, 900, {1500.0, 1800.0}, interval_sec=300
            )
        )


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
                "('bybit','t1',1000,'ETH-20260925-70000-C','20260925',70000,'C',"
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
                    ("bybit", "ETH-20260925-70000-C", 900, 0.4, 0.01, 2, 0.49, 70000),
                    ("bybit", "ETH-20260925-70000-C", 1010, 0.9, 0.09, 9, 0.80, 80000),
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
        self.assertAlmostEqual(outcome.trailing_returns_pct[300], (130 / 125 - 1) * 100)

    def test_incomplete_future_candle_path_is_not_labeled(self):
        candles = [
            FuturesCandle(float(minute * 60), 101.0, 99.0, 100.0)
            for minute in range(61)
            if minute != 40
        ]
        outcomes = build_futures_outcomes(
            candles,
            [1800.0],
            horizons_sec=[900],
            max_alignment_sec=1,
            minimum_path_coverage_ratio=0.95,
        )
        self.assertNotIn((1800.0, 900), outcomes)


class EarlierMosContextJoinTest(unittest.TestCase):
    def test_decision_context_uses_only_latest_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mos_research.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    """
                    CREATE TABLE snapshots (
                        timestamp_utc REAL, current_state TEXT,
                        gamma_regime TEXT, execution_timing_state TEXT,
                        exclude_from_analysis INTEGER
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO snapshots VALUES (?,?,?,?,0)",
                    [
                        (900, "COMPRESSION", "POSITIVE_GAMMA", "WAIT"),
                        (1010, "EXPANSION", "NEGATIVE_GAMMA", "EXECUTION_WINDOW"),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            contexts = load_decision_contexts(
                {"mos_research.db": path}, [1000], max_age_sec=120
            )

        self.assertEqual(contexts[1000].snapshot_timestamp_utc, 900)
        self.assertEqual(contexts[1000].current_state, "COMPRESSION")
        self.assertEqual(contexts[1000].gamma_regime, "POSITIVE_GAMMA")

    def test_frozen_regime_contexts_are_exact_not_fuzzy(self):
        context = DecisionContext(
            decision_timestamp_utc=1000,
            snapshot_timestamp_utc=990,
            current_state="COMPRESSION",
            gamma_regime="POSITIVE_GAMMA",
            execution_timing_state="WAIT",
        )
        outcome = type("Outcome", (), {"false_sweep_direction": -1})()
        self.assertTrue(_context_matches("mos_compression_or_pinning", outcome, context))
        self.assertTrue(_context_matches("positive_gamma", outcome, context))
        self.assertTrue(_context_matches("false_sweep_30m_15m_2bps", outcome, context))
        self.assertFalse(_context_matches("negative_gamma", outcome, context))
        self.assertFalse(_context_matches("execution_active", outcome, context))


class FrozenDirectionEvaluationTest(unittest.TestCase):
    def test_profitable_synthetic_flow_requires_cross_exchange_confirmation(self):
        protocol = load_protocol()
        protocol.update(
            {
                "training_days": 2,
                "minimum_test_days": 3,
                "minimum_trades": 3,
                "minimum_positive_day_ratio": 0.6,
                "signal_quantile": 0.4,
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
                        "trailing_returns_pct": {300: 0.1},
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


class EndToEndDatasetTest(unittest.TestCase):
    def test_full_sqlite_pipeline_runs_after_readiness_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flow_path = root / "option_trade_flow.db"
            history_path = root / "history.db"
            research_path = root / "mos_research.db"
            protocol_path = root / "protocol.json"

            store = OptionTradeFlowStore(flow_path)
            store.initialize()
            decisions = [
                _timestamp(day, 60 + slot * 10)
                for day in range(3)
                for slot in range(2)
            ]
            trades = []
            for index, decision in enumerate(decisions):
                timestamp_ms = int((decision - 20) * 1000)
                side = "Buy" if index % 2 == 0 else "Sell"
                trades.append(
                    normalize_bybit_trade(
                        {
                            "T": timestamp_ms,
                            "s": "ETH-25SEP26-70000-C-USDT",
                            "S": side,
                            "v": "2",
                            "p": "0.02",
                            "i": f"bybit-{index}",
                            "iv": "0.50",
                        }
                    )
                )
                trades.append(
                    normalize_deribit_trade(
                        {
                            "timestamp": timestamp_ms,
                            "instrument_name": "ETH-25SEP26-70000-C",
                            "direction": side.lower(),
                            "amount": 2,
                            "price": 0.02,
                            "trade_id": f"deribit-{index}",
                            "iv": 50,
                        }
                    )
                )
            store.insert_trades(trades)

            connection = sqlite3.connect(flow_path)
            try:
                status_rows = []
                for exchange in ("bybit", "deribit"):
                    for decision in decisions:
                        for sample in range(30):
                            updated = decision - 295 + sample * 5
                            status_rows.append(
                                (
                                    f"session-{exchange}",
                                    decisions[0] - 600,
                                    exchange,
                                    "subscribed",
                                    1,
                                    0,
                                    10,
                                    10,
                                    10,
                                    0,
                                    updated,
                                    updated,
                                    "",
                                    updated,
                                )
                            )
                connection.executemany(
                    """
                    INSERT INTO collector_status_history (
                        session_id, process_started_at_utc, exchange,
                        connection_state, connection_count, reconnect_count,
                        message_count, normalized_trade_count, queued_trade_count,
                        dropped_trade_count, last_message_utc, last_trade_utc,
                        last_error, updated_at_utc
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    status_rows,
                )
                connection.commit()
            finally:
                connection.close()

            connection = sqlite3.connect(history_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE option_contract_snapshots (
                        ts REAL, exchange TEXT, contract_id TEXT, delta REAL,
                        gamma REAL, vega REAL, mark_iv REAL,
                        underlying_price REAL
                    )
                    """
                )
                snapshot_rows = []
                for decision in decisions:
                    for exchange in ("bybit", "deribit"):
                        snapshot_rows.append(
                            (
                                decision - 50,
                                exchange,
                                "ETH-20260925-70000-C",
                                0.5,
                                0.01,
                                2.0,
                                0.49,
                                70000.0,
                            )
                        )
                connection.executemany(
                    "INSERT INTO option_contract_snapshots VALUES (?,?,?,?,?,?,?,?)",
                    snapshot_rows,
                )
                connection.execute(
                    "CREATE INDEX idx_contract_test ON "
                    "option_contract_snapshots(exchange, contract_id, ts)"
                )
                connection.commit()
            finally:
                connection.close()

            connection = sqlite3.connect(research_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE ohlcv_candles (
                        timestamp_utc REAL, symbol TEXT, timeframe TEXT,
                        high REAL, low REAL, close REAL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE snapshots (
                        timestamp_utc REAL, current_state TEXT,
                        gamma_regime TEXT, execution_timing_state TEXT,
                        exclude_from_analysis INTEGER
                    )
                    """
                )
                first = int(decisions[0] - 1800)
                last = int(decisions[-1] + 600)
                candle_rows = []
                for timestamp in range(first, last + 1, 60):
                    price = 70000.0 + ((timestamp - first) // 60) * 0.5
                    candle_rows.append(
                        (timestamp, "ETHUSDT", "1m", price + 5, price - 5, price)
                    )
                connection.executemany(
                    "INSERT INTO ohlcv_candles VALUES (?,?,?,?,?,?)", candle_rows
                )
                connection.executemany(
                    "INSERT INTO snapshots VALUES (?,?,?,?,0)",
                    [
                        (
                            decision - 40,
                            "COMPRESSION",
                            "POSITIVE_GAMMA",
                            "WAIT",
                        )
                        for decision in decisions
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            protocol = load_protocol()
            protocol.update(
                {
                    "lookbacks_sec": [300],
                    "horizons_sec": [300],
                    "training_days": 1,
                    "minimum_total_days": 0,
                    "minimum_status_samples_per_5m_per_exchange": 30,
                    "minimum_test_days": 1,
                    "minimum_trades": 1,
                    "signal_quantile": 0.5,
                    "contexts": ["all"],
                    "direction_features": ["call_put_contract_imbalance"],
                    "direction_modes": ["direct"],
                    "range_features": ["trade_intensity"],
                    "bootstrap_samples": 20,
                    "permutation_samples": 20,
                }
            )
            protocol_path.write_text(
                json.dumps(protocol), encoding="utf-8"
            )

            result = run_frozen_analysis(root, protocol_path)
            archive_path = root / "dataset.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name in ("mos_research.db", "history.db", "option_trade_flow.db"):
                    archive.write(root / name, arcname=name)
            zip_result = run_frozen_analysis(archive_path, protocol_path)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["audit"]["status"], "ready")
        self.assertEqual(result["enriched_trades"], 12)
        self.assertEqual(result["greek_matched_trades"], 12)
        self.assertGreater(result["direction_rule_count"], 0)
        self.assertEqual(result["live_entry_change_count"], 0)
        self.assertEqual(zip_result["status"], "complete")
        self.assertEqual(zip_result["protocol_sha256"], result["protocol_sha256"])


class CollectorFacingReadinessTest(unittest.TestCase):
    def test_human_summary_reports_progress_and_remaining_days(self):
        summary = render_human_summary(
            {
                "status": "not_ready",
                "trades": 125,
                "common_overlap_days": 5.5,
                "healthy_full_lookback_days": 4.0,
                "healthy_days_remaining": 10.0,
                "healthy_progress_ratio": 4.0 / 14.0,
                "historical_dropped_trades": 0,
                "blockers": ["insufficient_healthy_dual_exchange_days"],
            }
        )

        self.assertIn("COLLECTING CLEAN DATA", summary)
        self.assertIn("Clean days remaining: 10.00", summary)
        self.assertIn("Progress: 28.6%", summary)
        self.assertIn("Historical queue drops: 0", summary)

    def test_human_summary_explains_unavailable_input(self):
        summary = render_human_summary(
            {
                "status": "error",
                "message": "required database is missing",
            }
        )

        self.assertIn("DATA NOT AVAILABLE", summary)
        self.assertIn("required database is missing", summary)

    def test_main_handles_missing_dataset_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-created"
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main([str(missing), "--human"])

        self.assertEqual(exit_code, 2)
        self.assertIn("DATA NOT AVAILABLE", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
