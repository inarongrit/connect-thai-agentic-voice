"""Tests for the per-call cost estimator.

These pin the measured provenance and the arithmetic, so a future edit cannot
silently swap a measured rate for a guessed one or break the Option A price
disclosure.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("cost_per_call", ROOT / "tools" / "cost_per_call.py")
COST = importlib.util.module_from_spec(SPEC)
# `dataclasses` resolves string annotations through sys.modules, so register the
# module before executing it.
sys.modules["cost_per_call"] = COST
SPEC.loader.exec_module(COST)


class MeasuredRateTests(unittest.TestCase):
    def test_voice_rates_match_measured_cost_over_quantity(self):
        self.assertAlmostEqual(COST.VOICE_RATES["pstn-th"][1], 1.128885 / 16.15, places=6)
        self.assertAlmostEqual(COST.VOICE_RATES["webrtc"][1], 0.2363333335 / 23.6333333335, places=6)
        self.assertAlmostEqual(COST.VOICE_RATES["webrtc"][0], 4.5302333327 / 119.2166666673, places=6)

    def test_token_drivers_match_measured_cloudwatch_sums(self):
        self.assertAlmostEqual(COST.LUNA_IN_PER_INVOCATION, 33_583 / 152, places=4)
        self.assertAlmostEqual(COST.LUNA_OUT_PER_INVOCATION, 25_488 / 152, places=4)
        self.assertAlmostEqual(COST.TERRA_IN_PER_INVOCATION, 6_070 / 42, places=4)
        self.assertAlmostEqual(COST.LUNA_CALLS_PER_CALL, 152 / 95, places=4)
        self.assertAlmostEqual(COST.TERRA_FALLBACK_PER_CALL, 42 / 95, places=4)


class OptionAPublishedPriceTests(unittest.TestCase):
    """Pin the model-card rates so an edit cannot silently change them."""

    def test_model_card_rates_are_recorded_per_1m_tokens(self):
        self.assertEqual(COST.OPTION_A_PRICES_PER_1M["in-region"], (0.22, 1.32, 2.20, 13.20))
        self.assertEqual(COST.OPTION_A_PRICES_PER_1M["geo"], (0.22, 1.32, 2.20, 13.20))
        self.assertEqual(COST.OPTION_A_PRICES_PER_1M["global"], (0.20, 1.20, 2.00, 12.00))

    def test_default_matches_the_geo_ids_the_lambda_actually_calls(self):
        self.assertEqual(COST.DEFAULT_INFERENCE_OPTION, "geo")
        prices = COST.Prices()
        self.assertAlmostEqual(prices.luna_input_per_1k, 0.22 / 1000, places=8)
        self.assertAlmostEqual(prices.luna_output_per_1k, 1.32 / 1000, places=8)
        self.assertAlmostEqual(prices.terra_input_per_1k, 2.20 / 1000, places=8)
        self.assertAlmostEqual(prices.terra_output_per_1k, 13.20 / 1000, places=8)

    def test_global_inference_is_cheaper_than_geo(self):
        geo = COST.estimate(3, "webrtc", "mantle", COST.Prices.for_option("geo"))
        glob = COST.estimate(3, "webrtc", "mantle", COST.Prices.for_option("global"))
        self.assertLess(glob.dialogue, geo.dialogue)

    def test_unknown_inference_option_is_rejected(self):
        with self.assertRaises(ValueError):
            COST.Prices.for_option("lunar")

    def test_terra_fallback_dominates_option_a_despite_fewer_turns(self):
        """Terra is 10x Luna's price, so the 28% fallback rate carries most of the cost."""
        prices = COST.Prices.for_option("geo")
        luna = COST.LUNA_CALLS_PER_CALL * (
            COST.LUNA_IN_PER_INVOCATION / 1000 * prices.luna_input_per_1k
            + COST.LUNA_OUT_PER_INVOCATION / 1000 * prices.luna_output_per_1k
        )
        terra = COST.TERRA_FALLBACK_PER_CALL * (
            COST.TERRA_IN_PER_INVOCATION / 1000 * prices.terra_input_per_1k
            + COST.TERRA_OUT_PER_INVOCATION / 1000 * prices.terra_output_per_1k
        )
        self.assertLess(COST.TERRA_FALLBACK_PER_CALL, COST.LUNA_CALLS_PER_CALL)
        self.assertGreater(terra, luna)

    def test_option_a_is_cheaper_than_option_b_per_call(self):
        a = COST.estimate(3, "webrtc", "mantle")
        b = COST.estimate(3, "webrtc", "managed")
        self.assertLess(a.dialogue, b.dialogue)


class EstimateTests(unittest.TestCase):
    def test_voice_scales_linearly_with_talk_time(self):
        one = COST.estimate(1, "pstn-th")
        three = COST.estimate(3, "pstn-th")
        self.assertAlmostEqual(three.voice, one.voice * 3, places=6)

    def test_thailand_pstn_costs_more_than_webrtc(self):
        self.assertGreater(COST.estimate(3, "pstn-th").total, COST.estimate(3, "webrtc").total)

    def test_voice_dominates_the_call_cost(self):
        breakdown = COST.estimate(3, "pstn-th")
        self.assertGreater(breakdown.voice / breakdown.total, 0.9)

    def test_option_a_dialogue_uses_measured_token_volume(self):
        prices = COST.Prices(luna_input_per_1k=1.0, luna_output_per_1k=0.0,
                             terra_input_per_1k=0.0, terra_output_per_1k=0.0)
        breakdown = COST.estimate(3, "webrtc", "mantle", prices)
        expected = COST.LUNA_CALLS_PER_CALL * COST.LUNA_IN_PER_INVOCATION / 1000
        self.assertAlmostEqual(breakdown.dialogue, expected, places=8)

    def test_option_a_includes_the_terra_fallback_share(self):
        with_fallback = COST.estimate(3, "webrtc", "mantle")
        without = COST.estimate(3, "webrtc", "mantle", fallback_turns=0)
        self.assertGreater(with_fallback.dialogue, without.dialogue)

    def test_supplied_option_a_prices_override_the_model_card_rates(self):
        default = COST.estimate(3, "webrtc", "mantle")
        dearer = COST.estimate(3, "webrtc", "mantle",
                               COST.Prices(luna_input_per_1k=0.01, luna_output_per_1k=0.05,
                                           terra_input_per_1k=0.01, terra_output_per_1k=0.05))
        self.assertGreater(dearer.dialogue, default.dialogue * 5)

    def test_option_a_price_source_is_always_disclosed(self):
        breakdown = COST.estimate(3, "webrtc", "mantle")
        self.assertTrue(any("model cards" in note for note in breakdown.notes))
        self.assertTrue(any("Token volumes measured" in note for note in breakdown.notes))

    def test_managed_engine_discloses_shared_token_attribution(self):
        breakdown = COST.estimate(3, "webrtc", "managed")
        self.assertTrue(any("upper bound" in note for note in breakdown.notes))

    def test_post_call_analysis_can_be_excluded(self):
        self.assertEqual(COST.estimate(3, "webrtc", post_call_analysis=False).post_call, 0.0)

    def test_invalid_inputs_are_rejected(self):
        for kwargs in ({"channel": "satellite"}, {"engine": "telepathy"}, {"minutes": -1}):
            with self.subTest(**kwargs):
                params = {"minutes": 3, "channel": "webrtc", "engine": "mantle"}
                params.update(kwargs)
                with self.assertRaises(ValueError):
                    COST.estimate(**params)


class MonthlyTests(unittest.TestCase):
    def test_monthly_scales_variable_cost_and_adds_fixed_rental(self):
        breakdown = COST.estimate(3, "pstn-th")
        scaled = COST.monthly(breakdown, 1000, tollfree_numbers=1, days=30)
        self.assertAlmostEqual(scaled["variable"], breakdown.total * 1000, places=6)
        self.assertAlmostEqual(scaled["fixed"], 30 * COST.TOLLFREE_NUMBER_DAY, places=6)
        self.assertAlmostEqual(scaled["total"], scaled["variable"] + scaled["fixed"], places=6)


class CliTests(unittest.TestCase):
    def test_show_sources_documents_provenance_and_gaps(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(COST.main(["--show-sources"]), 0)
        output = buffer.getvalue()
        self.assertIn("Cost Explorer", output)
        self.assertIn("CloudWatch AWS/Bedrock", output)
        self.assertIn("Not measurable here", output)
        self.assertIn("model-card-openai-gpt-56-luna", output)
        self.assertIn("model-card-openai-gpt-56-terra", output)
        self.assertIn("Luna", output)
        self.assertIn("Terra", output)

    def test_inference_option_flag_is_accepted(self):
        import io
        from contextlib import redirect_stdout
        for option in sorted(COST.OPTION_A_PRICES_PER_1M):
            with self.subTest(option=option):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    self.assertEqual(COST.main(["--inference-option", option]), 0)
                self.assertIn(option, buffer.getvalue())

    def test_compare_and_single_modes_run(self):
        import io
        from contextlib import redirect_stdout
        for argv in (["--compare"], ["--minutes", "3", "--channel", "pstn-th", "--calls", "500"]):
            with self.subTest(argv=argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    self.assertEqual(COST.main(argv), 0)
                self.assertIn("TOTAL", buffer.getvalue().upper())


if __name__ == "__main__":
    unittest.main()
