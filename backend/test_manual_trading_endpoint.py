import unittest

from routes.market import (
    _MANUAL_WATCHLIST,
    _build_manual_chart_overlay,
    _build_manual_trading_payload,
    _build_manual_watchlist_row,
    _filter_manual_watchlist,
    _record_manual_watchlist,
)
from unittest.mock import patch


class ManualTradingEndpointPayloadTest(unittest.TestCase):
    @patch('routes.market.ManualSetupClassifier.classify')
    @patch('routes.market.PriceSourceEngine.get')
    def test_payload_contains_classifier_output_and_source_fields(self, mock_pse, mock_classify):
        from engine.price_source_engine import PriceSourceInfo
        mock_pse.return_value = PriceSourceInfo(execution_price=100050.0, reference_price=100050.0)
        mock_classify.return_value = {
            "manual_status": "ENTRY_CANDIDATE",
            "manual_setup_type": "SUPPORT_DEFENSE_REVERSAL_SETUP",
            "manual_bias": "LONG",
            "decision_ladder": {"inputs": {
                "current_state": "TRANSITION",
                "execution_timing_state": "EXECUTION_WINDOW_OPEN"
            }},
            "invalidation_level": 99000.0,
        }
        market_state = {
            "timestamp": 123456,
            "spot": 100050,
            "state_machine": {"current_state": "TRANSITION"},
            "advanced_intelligence": {
                "signal_cluster_score": 72,
                "phase_1": {
                    "gamma_surface": {"metrics": {"gamma_slope_state": "weakening"}},
                    "regime_transition": {"metrics": {"expansion_probability": 61}},
                    "short_term_flow_context": {
                        "features": {"short_term_flow_direction": "buying"}
                    },
                },
                "phase_2": {
                    "execution_timing": {
                        "features": {"execution_state": "EXECUTION_WINDOW_OPEN"}
                    }
                },
            },
        }

        payload = _build_manual_trading_payload(market_state)

        self.assertEqual(payload["status"], "ok")
        self.assertIsInstance(payload["timestamp"], str)
        self.assertIn("manual_status", payload["manual_setup"])
        self.assertEqual(payload["source_fields"]["current_state"], "TRANSITION")
        self.assertEqual(payload["source_fields"]["execution_timing_state"], "EXECUTION_WINDOW_OPEN")

    @patch('routes.market.ManualSetupClassifier.classify')
    @patch('routes.market.PriceSourceEngine.get')
    def test_watchlist_row_contains_table_columns(self, mock_pse, mock_classify):
        from engine.price_source_engine import PriceSourceInfo
        mock_pse.return_value = PriceSourceInfo(execution_price=100050.0, reference_price=100050.0)
        mock_classify.return_value = {
            "manual_status": "ENTRY_CANDIDATE",
            "manual_setup_type": "SUPPORT_DEFENSE_REVERSAL_SETUP",
            "manual_bias": "LONG",
            "setup_quality": "ACTIONABLE",
            "invalidation_level": 100000.0,
            "confirmation_needed": "Искать подтверждение на графике",
            "missing_conditions": []
        }
        market_state = {
            "timestamp": 123456,
            "spot": 100050,
            "current_state": "TRANSITION",
            "execution_timing_state": "EXECUTION_WINDOW_OPEN",
            "event_type": "BREAKOUT_ALERT",
            "level_result": "DEFENDED",
            "level_side": "SUPPORT",
            "short_term_flow_direction": "BULLISH",
            "signal_cluster_score": 75,
            "expansion_probability": 65,
            "nearest_level": 100000.0,
            "_live_context_used": True,
            "_live_level_ctx": {
                "support_level": 100000.0,
                "support_result": "DEFENDED",
            },
            "price_confirmation_status": "CONFIRMED_HOLD"
        }
        payload = _build_manual_trading_payload(market_state)
        row = _build_manual_watchlist_row(
            market_state,
            payload["manual_setup"],
            payload["source_fields"],
        )

        self.assertEqual(row["time"], 123456)
        self.assertEqual(row["setup_type"], "SUPPORT_DEFENSE_REVERSAL_SETUP")
        self.assertEqual(row["manual_status"], "ENTRY_CANDIDATE")
        self.assertEqual(row["manual_bias"], "LONG")
        self.assertEqual(row["price"], 100050.0)
        self.assertIsInstance(row["nearest_level"], float)
        self.assertIsInstance(row["current_state"], str)
        self.assertIsInstance(row["execution_timing_state"], str)
        self.assertIsInstance(row["event_type"], str)
        self.assertIsInstance(row["level_result"], str)
        self.assertIsInstance(row["short_term_flow_direction"], str)
        self.assertIn("подтверждение на графике", row["confirmation_needed"])
        self.assertEqual(row["invalidation_level"], 100000.0)
        self.assertEqual(row["setup_quality"], "ACTIONABLE")
        self.assertEqual(row["missing_conditions"], [])

    def test_watchlist_filter_supports_status_and_bias(self):
        rows = [
            {"manual_status": "ENTRY_CANDIDATE", "manual_bias": "LONG"},
            {"manual_status": "WATCH", "manual_bias": "SHORT"},
            {"manual_status": "AVOID", "manual_bias": "NEUTRAL"},
        ]

        self.assertEqual(
            _filter_manual_watchlist(rows, statuses="WATCH,ENTRY_CANDIDATE"),
            rows[:2],
        )
        self.assertEqual(
            _filter_manual_watchlist(rows, biases="SHORT"),
            [rows[1]],
        )
        self.assertEqual(
            _filter_manual_watchlist(rows, statuses="AVOID", biases="LONG"),
            [],
        )

    def test_watchlist_dedupes_identical_watch_context_inside_cooldown(self):
        _MANUAL_WATCHLIST.clear()
        base_state = {
            "timestamp": 1000,
            "spot": 100050,
            "current_state": "COMPRESSION",
            "execution_timing_state": "WAIT",
            "short_term_flow_direction": "BEARISH",
            "signal_cluster_score": 40,
            "expansion_probability": 55,
            "gamma_slope_state": "weakening",
        }
        second_state = {**base_state, "timestamp": 1060, "spot": 100040}

        first = _record_manual_watchlist(base_state)
        second = _record_manual_watchlist(second_state)

        self.assertIs(first, second)
        self.assertEqual(len(_MANUAL_WATCHLIST), 1)
        self.assertEqual(_MANUAL_WATCHLIST[0]["updated_count"], 2)
        self.assertEqual(_MANUAL_WATCHLIST[0]["manual_status"], "WATCH")
        self.assertEqual(_MANUAL_WATCHLIST[0]["manual_bias"], "NEUTRAL")
        self.assertEqual(_MANUAL_WATCHLIST[0]["setup_quality"], "INCOMPLETE")
        self.assertIn("поток еще не развернулся", _MANUAL_WATCHLIST[0]["missing_conditions"][1])

    @patch('routes.market.ManualSetupClassifier.classify')
    @patch('routes.market.PriceSourceEngine.get')
    def test_chart_overlay_contains_manual_overlay_groups(self, mock_pse, mock_classify):
        from engine.price_source_engine import PriceSourceInfo
        mock_pse.return_value = PriceSourceInfo(execution_price=100050.0, reference_price=100050.0)
        mock_classify.return_value = {
            "manual_status": "ENTRY_CANDIDATE",
            "manual_setup_type": "SUPPORT_DEFENSE_REVERSAL_SETUP",
            "manual_bias": "LONG",
            "setup_quality": "ACTIONABLE",
            "invalidation_level": 100000.0,
            "confirmation_needed": "Искать подтверждение на графике",
            "missing_conditions": []
        }
        market_state = {
            "timestamp": 123456,
            "spot": 100050,
            "current_state": "TRANSITION",
            "execution_timing_state": "EXECUTION_WINDOW_OPEN",
            "event_type": "BREAKOUT_ALERT",
            "level_result": "DEFENDED",
            "level_side": "SUPPORT",
            "short_term_flow_direction": "BULLISH",
            "signal_cluster_score": 75,
            "expansion_probability": 65,
            "nearest_level": 100000,
            "_live_context_used": True,
            "_live_level_ctx": {
                "support_level": 100000.0,
                "support_result": "DEFENDED",
            },
            "price_confirmation_status": "CONFIRMED_HOLD",
            "events": [
                {
                    "timestamp": 123456,
                    "event_type": "BREAKOUT_ALERT",
                    "severity": "HIGH",
                    "message": "Breakout window detected.",
                }
            ],
        }
        payload = _build_manual_trading_payload(market_state)
        watchlist_row = _build_manual_watchlist_row(
            market_state,
            payload["manual_setup"],
            payload["source_fields"],
        )

        overlay = _build_manual_chart_overlay(market_state, [watchlist_row])

        self.assertIn("key_zones", overlay)
        self.assertIn("current_nearest_level", overlay)
        self.assertIn("event_markers", overlay)
        self.assertIn("level_reaction_markers", overlay)
        self.assertIn("setup_labels", overlay)
        self.assertIn("invalidation_line", overlay)
        self.assertIn("confirmation_zone", overlay)
        self.assertEqual(overlay["current_nearest_level"]["price"], 100000.0)
        self.assertEqual(overlay["event_markers"][0]["event_type"], "BREAKOUT_ALERT")
        self.assertEqual(overlay["level_reaction_markers"][0]["level_result"], "DEFENDED")
        self.assertEqual(overlay["setup_labels"][0]["manual_status"], "ENTRY_CANDIDATE")
        self.assertEqual(overlay["invalidation_line"]["price"], 100000.0)
        self.assertEqual(overlay["confirmation_zone"]["bias"], "LONG")
        self.assertTrue(overlay["key_zones"])


if __name__ == "__main__":
    unittest.main()
