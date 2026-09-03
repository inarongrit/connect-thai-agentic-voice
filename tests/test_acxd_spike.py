"""The ACXD spike artifacts must describe the engine that exists, not a hoped-for one.

A rule specification that drifts from the code is worse than none: it would be used to
judge an agentic CX designer rebuild against behaviour we never actually shipped. These
tests pin the specification to the live engine, and pin the harness to reporting
UNAVAILABLE rather than inventing a score for a target that cannot be reached yet.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPIKE = ROOT / "spike" / "acxd"
RULES = json.loads((SPIKE / "collections_rules.json").read_text())

sys.path.insert(0, str(SPIKE))
import compare_slot_capture as harness  # noqa: E402


class SpecificationMatchesTheEngineTests(unittest.TestCase):
    """Every constant in the spec is read back from the module it claims to describe."""

    @classmethod
    def setUpClass(cls):
        cls.module = harness.load_engine()

    def test_the_declared_source_of_truth_exists(self):
        self.assertEqual(RULES["source_of_truth"], "lambda/mantle_dialogue.py")
        self.assertTrue((ROOT / RULES["source_of_truth"]).is_file())

    def test_payment_choices_match_the_engine(self):
        self.assertEqual(
            tuple(RULES["rules"]["payment_method_choice"]["choices_th"]),
            self.module.BANK_PAYMENT_CHOICES)

    def test_assistance_choices_and_plan_ids_match_the_engine(self):
        assistance = RULES["rules"]["assistance_option_choice"]
        self.assertEqual(tuple(assistance["choices_th"]), self.module.BANK_ASSISTANCE_CHOICES)
        self.assertEqual(assistance["plan_ids"], self.module.ASSISTANCE_PLAN_TH)

    def test_numeric_policy_thresholds_match_the_engine(self):
        self.assertEqual(
            RULES["rules"]["payment_amount_capture"]["parameters"]["token_amount_fraction"],
            self.module.TOKEN_AMOUNT_FRACTION)
        self.assertEqual(
            RULES["rules"]["payment_date_capture"]["parameters"]["max_years_ahead"],
            self.module.MAX_PROMISE_YEARS_AHEAD)

    def test_signal_priority_order_matches_the_engine(self):
        self.assertEqual(
            RULES["rules"]["protective_signal_routing"]["priority_order"],
            [name for name, _ in self.module.SIGNAL_PATTERNS])

    def test_handoff_outcomes_match_the_engine(self):
        self.assertEqual(
            sorted(RULES["handoff_contract"]["outcomes_that_transfer"]),
            sorted(self.module.HANDOFF_OUTCOMES))

    def test_do_not_contact_is_recorded_as_not_transferring(self):
        """Honouring a contact ban means ending the call, not routing it to a person."""
        routing = RULES["rules"]["protective_signal_routing"]["routing"]
        self.assertFalse(routing["do_not_contact"]["transfers_to_human"])
        self.assertNotIn("do_not_contact", self.module.HANDOFF_OUTCOMES)

    def test_licensed_boundary_outcomes_still_transfer(self):
        boundary = RULES["rules"]["protective_signal_routing"]["licensed_boundary"]
        for scenario in ("insurance", "broker"):
            with self.subTest(scenario=scenario):
                self.assertIn(boundary[scenario], self.module.HANDOFF_OUTCOMES)


class CorpusIsBalancedTests(unittest.TestCase):
    """A rejection corpus alone would reward a validator that rejects everything."""

    def test_date_corpus_has_both_polarities_in_volume(self):
        rule = RULES["rules"]["payment_date_capture"]
        reject = harness._flatten(rule["must_reject"])
        accept = harness._flatten(rule["must_accept"])
        self.assertGreaterEqual(len(reject), 20)
        self.assertGreaterEqual(len(accept), 20)
        self.assertEqual(set(reject) & set(accept), set())

    def test_amount_corpus_has_both_polarities(self):
        rule = RULES["rules"]["payment_amount_capture"]
        self.assertGreaterEqual(len(harness._flatten(rule["must_reject"])), 5)
        self.assertGreaterEqual(len(rule["must_accept"]), 5)

    def test_corpus_keeps_the_cases_that_were_real_bypasses(self):
        """These specific forms each walked through the gate at some point."""
        reject = harness._flatten(RULES["rules"]["payment_date_capture"]["must_reject"])
        for utterance in ("32 มกรา", "32 ม.ค.", "สามสิบสองมกราคม", "32/1", "1/13"):
            self.assertIn(utterance, reject)

    def test_corpus_keeps_the_forms_that_must_not_be_read_as_dates(self):
        accept = harness._flatten(RULES["rules"]["payment_date_capture"]["must_accept"])
        for utterance in ("14:30", "14.30", "2000 บาท", "20000 บาท"):
            self.assertIn(utterance, accept)

    def test_ownership_split_keeps_money_and_identity_deterministic(self):
        deterministic = RULES["ownership"]["deterministic"]
        for rule in ("identity_verification", "payment_amount_capture",
                     "payment_date_capture", "protective_signal_routing"):
            self.assertIn(rule, deterministic)
        self.assertNotIn("payment_amount_capture", RULES["ownership"]["agentic"])


class HarnessBehaviourTests(unittest.TestCase):
    def test_control_target_passes_every_case(self):
        """The control must score perfectly or the corpus does not describe the engine."""
        results = harness.evaluate(harness.EngineTarget(), RULES)
        _text, passed, total = harness.report("engine", results)
        self.assertEqual(passed, total)
        self.assertGreaterEqual(total, 70)

    def test_acxd_target_reports_unavailable_instead_of_a_fake_score(self):
        target = harness.AcxdTarget()
        self.assertFalse(target.available)
        self.assertIn("agentic CX designer console", target.reason)
        with self.assertRaises(NotImplementedError):
            target.date_rejected("วันที่ 32 ธันวาคม")

    def test_acxd_target_becomes_available_only_with_all_three_ids(self):
        self.assertFalse(harness.AcxdTarget("w", "a", None).available)
        self.assertTrue(harness.AcxdTarget("w", "a", "al").available)

    def test_harness_runs_as_a_command_and_exits_zero_for_the_control(self):
        done = subprocess.run(
            [sys.executable, str(SPIKE / "compare_slot_capture.py")],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("TOTAL:", done.stdout)

    def test_harness_exits_nonzero_when_acxd_ids_are_missing(self):
        done = subprocess.run(
            [sys.executable, str(SPIKE / "compare_slot_capture.py"), "--target", "acxd"],
            capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("UNAVAILABLE", done.stdout)


if __name__ == "__main__":
    unittest.main()
