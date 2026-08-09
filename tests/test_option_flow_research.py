import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.scripts.option_flow_research import (
    day_block_bootstrap_ci,
    holm_adjust,
    load_protocol,
    prior_day_quantile_observations,
    promotion_decision,
    protocol_content_sha256,
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


if __name__ == "__main__":
    unittest.main()
