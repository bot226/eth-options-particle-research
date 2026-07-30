import unittest

from engine.manual_setup_classifier import ManualSetupClassifier


class ManualSetupClassifierTest(unittest.TestCase):
    def test_low_cluster_low_expansion_is_no_trade_chop(self):
        result = ManualSetupClassifier.classify(
            {
                "current_state": "PINNING",
                "execution_timing_state": "WAIT",
                "signal_cluster_score": 20,
                "expansion_probability": 15,
                "price": 100000,
            }
        )

        self.assertEqual(result["manual_status"], "AVOID")
        self.assertEqual(result["manual_setup_type"], "NO_TRADE_CHOP")
        self.assertEqual(result["manual_bias"], "NEUTRAL")
        self.assertTrue(result["avoid_reason"])

    def test_support_defense_can_be_entry_candidate_only_with_confirmation_language(self):
        result = ManualSetupClassifier.classify(
            {
                "current_state": "TRANSITION",
                "execution_timing_state": "EXECUTION_WINDOW_OPEN",
                "_live_context_used": True,
                "_live_level_result": "DEFENDED",
                "_live_level_side": "SUPPORT",
                "_live_nearest_level": 100000,
                "_live_level_ctx": {
                    "live_context_used": True,
                    "live_support_level": 100000,
                    "live_support_result": "DEFENDED",
                    "live_support_distance_pct": 0.05,
                    "primary_live_level": 100000,
                    "primary_live_side": "SUPPORT",
                    "primary_live_result": "DEFENDED"
                },
                "short_term_flow_direction": "BULLISH",
                "signal_cluster_score": 72,
                "expansion_probability": 48,
                "price": 100050,
            }
        )

        self.assertEqual(result["manual_status"], "ENTRY_CANDIDATE")
        self.assertEqual(result["manual_setup_type"], "SUPPORT_DEFENSE_REVERSAL_SETUP")
        self.assertEqual(result["manual_bias"], "LONG")
        self.assertIn("Искать подтверждение на графике", result["confirmation_needed"])
        self.assertIn("не сигнал покупки/продажи", result["disclaimer"])

    def test_bearish_flow_exhaustion_without_reversal_confirmation_is_incomplete_neutral_watch(self):
        result = ManualSetupClassifier.classify(
            {
                "event_type": "BREAKOUT_ALERT",
                "current_state": "COMPRESSION",
                "execution_timing_state": "WAIT",
                "short_term_flow_direction": "BEARISH",
                "signal_cluster_score": 40,
                "expansion_probability": 55,
                "gamma_slope_state": "weakening",
                "price": 100000,
            }
        )

        self.assertEqual(result["manual_status"], "WATCH")
        self.assertEqual(result["manual_setup_type"], "FLOW_EXHAUSTION_REVERSAL_SETUP")
        self.assertEqual(result["manual_bias"], "NEUTRAL")
        self.assertEqual(result["manual_confidence"], "LOW")
        self.assertEqual(result["setup_quality"], "INCOMPLETE")
        self.assertIsNone(result["invalidation_level"])
        self.assertIsNone(result["avoid_reason"])
        self.assertIn("no confirmed reversal level reaction", result["missing_conditions"])
        self.assertIn("flow has not flipped bullish", result["missing_conditions"])
        self.assertIn("no actionable invalidation level", result["missing_conditions"])
        self.assertEqual(
            result["confirmation_needed"],
            "Ждать неудачного нисходящего продолжения, возврата выше поддержки и разворота потока.",
        )

    def test_flow_exhaustion_can_form_long_only_after_support_reclaim_and_bullish_flow(self):
        result = ManualSetupClassifier.classify(
            {
                "current_state": "TRANSITION",
                "execution_timing_state": "EXECUTION_WINDOW_OPEN",
                "_live_context_used": True,
                "_live_level_result": "SUPPORT_DEFENSE",
                "_live_level_side": "SUPPORT",
                "_live_nearest_level": 100000,
                "_live_level_ctx": {
                    "live_context_used": True,
                    "live_support_level": 100000,
                    "live_support_result": "SUPPORT_DEFENSE",
                    "live_support_distance_pct": 0.10,
                    "primary_live_level": 100000,
                    "primary_live_side": "SUPPORT",
                    "primary_live_result": "SUPPORT_DEFENSE"
                },
                "short_term_flow_direction": "BULLISH",
                "signal_cluster_score": 62,
                "expansion_probability": 58,
                "gamma_slope_state": "weakening",
                "price": 100050,
            }
        )

        self.assertEqual(result["manual_setup_type"], "SUPPORT_DEFENSE_REVERSAL_SETUP")
        self.assertEqual(result["manual_status"], "ENTRY_CANDIDATE")
        self.assertEqual(result["manual_bias"], "LONG")
        self.assertEqual(result["setup_quality"], "ACTIONABLE")
        self.assertEqual(result["missing_conditions"], [])

    def test_entry_candidate_requires_level_invalidation_and_flow_alignment(self):
        result = ManualSetupClassifier.classify(
            {
                "current_state": "TRANSITION",
                "execution_timing_state": "EXECUTION_WINDOW_OPEN",
                "_live_level_ctx": {
                    "live_context_used": True
                },
                "short_term_flow_direction": "BULLISH",
                "signal_cluster_score": 80,
                "expansion_probability": 80,
                "gamma_slope_state": "weakening",
                "price": 100050,
            }
        )

        self.assertNotEqual(result["manual_status"], "ENTRY_CANDIDATE")
        self.assertEqual(result["setup_quality"], "INCOMPLETE")
        self.assertIn("нет рабочего level_result", result["missing_conditions"])
        self.assertIn("нет рабочего уровня отмены", result["missing_conditions"])

    def test_source_fields_uses_same_normalized_inputs_as_classifier(self):
        source_fields = ManualSetupClassifier.source_fields(
            {
                "spot": 100050,
                "state_machine": {"current_state": "transition"},
                "events": [{"event_type": "breakout-alert"}],
                "advanced_intelligence": {
                    "signal_cluster_score": 72,
                    "phase_1": {
                        "gamma_surface": {"metrics": {"gamma_slope_state": "Weakening"}},
                        "regime_transition": {"metrics": {"expansion_probability": 61}},
                        "short_term_flow_context": {
                            "features": {"short_term_flow_direction": "buying"}
                        },
                    },
                    "phase_2": {
                        "execution_timing": {
                            "features": {"execution_state": "execution window open"}
                        }
                    },
                },
            }
        )

        self.assertEqual(source_fields["event_type"], "BREAKOUT_ALERT")
        self.assertEqual(source_fields["execution_timing_state"], "EXECUTION_WINDOW_OPEN")
        self.assertEqual(source_fields["current_state"], "TRANSITION")
        self.assertEqual(source_fields["short_term_flow_direction"], "BUYING")
        self.assertEqual(source_fields["gamma_slope_state"], "weakening")
        self.assertEqual(source_fields["signal_cluster_score"], 72.0)
        self.assertEqual(source_fields["expansion_probability"], 61.0)
        self.assertEqual(source_fields["price"], 100050.0)


if __name__ == "__main__":
    unittest.main()
