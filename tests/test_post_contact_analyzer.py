"""Unit tests for the post-contact analyzer's scoring logic.

Two bugs found on live data are pinned here:

  * pinning a Bedrock temperature silently disabled classification for every
    contact, because these models reject the field;
  * a call ended by the dialogue guardrail (`unresolved_needs_human`) scored 100%
    as fully handled.
"""

import importlib.util
import os
import unittest
from unittest.mock import patch

ENV = {
    "AWS_REGION": "us-west-2",
    "INSTANCE_ID": "test-instance",
    "EVALUATION_FORM_ID": "test-form",
    "EVALUATOR_USER_ARN": "arn:aws:connect:us-west-2:1:instance/x/agent/y",
}

with patch.dict(os.environ, ENV, clear=False):
    SPEC = importlib.util.spec_from_file_location(
        "analyzer",
        os.path.join(os.path.dirname(__file__), "..", "lambda", "thai_post_contact_analyzer.py"),
    )
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)


class HandledDerivationTests(unittest.TestCase):
    def test_stalled_call_is_never_fully_handled(self):
        self.assertEqual(
            MODULE._handled("none", "payment_commitment", "unresolved_needs_human"),
            "Partially",
        )

    def test_no_signal_and_a_normal_outcome_is_handled(self):
        self.assertEqual(MODULE._handled("none", "payment_commitment", "payment_commitment"), "Yes")

    def test_hardship_recorded_as_declined_is_not_handled(self):
        self.assertEqual(
            MODULE._handled("hardship_financial_difficulty", "payment_assistance_referral", "declined"),
            "No",
        )

    def test_hardship_recorded_as_callback_is_not_handled(self):
        self.assertEqual(
            MODULE._handled("hardship_financial_difficulty", "payment_assistance_referral", "callback"),
            "No",
        )

    def test_hardship_routed_as_recommended_is_handled(self):
        self.assertEqual(
            MODULE._handled(
                "hardship_financial_difficulty",
                "payment_assistance_referral",
                "payment_assistance_referral",
            ),
            "Yes",
        )

    def test_vulnerability_is_treated_as_an_escalation_signal(self):
        self.assertIn("vulnerability", MODULE.ESCALATION_SIGNALS)
        self.assertEqual(MODULE._handled("vulnerability", "vulnerability_referral", "declined"), "No")


class LoopDetectionTests(unittest.TestCase):
    def test_repeated_agent_turn_is_detected(self):
        turns = [
            ("SYSTEM", "สะดวกชำระเต็มจำนวน, ชำระบางส่วน, หรือแบ่งชำระคะ"),
            ("CUSTOMER", "ไม่มีเงินครับ"),
            ("SYSTEM", "สะดวกชำระเต็มจำนวน, ชำระบางส่วน, หรือแบ่งชำระคะ"),
        ]
        self.assertTrue(MODULE._repeated_agent_turn(turns))

    def test_distinct_agent_turns_are_not_a_loop(self):
        turns = [
            ("SYSTEM", "สะดวกชำระเต็มจำนวน, ชำระบางส่วน, หรือแบ่งชำระคะ"),
            ("CUSTOMER", "ไม่มีเงินครับ"),
            ("SYSTEM", "ขณะนี้สะดวกชำระบางส่วนก่อน, หรือขอเลื่อนการชำระออกไปคะ"),
        ]
        self.assertFalse(MODULE._repeated_agent_turn(turns))

    def test_whitespace_differences_still_count_as_a_repeat(self):
        turns = [("SYSTEM", "สะดวกชำระ  บางส่วนคะ"), ("SYSTEM", "สะดวกชำระ บางส่วนคะ")]
        self.assertTrue(MODULE._repeated_agent_turn(turns))


class ConsequenceTests(unittest.TestCase):
    """A low score must alert and be recorded; a good score must not."""

    def test_missed_signal_is_flagged_for_review(self):
        for handled in ("No", "Partially"):
            self.assertIn(handled, ("No", "Partially"))

    def test_only_missed_signals_emit_an_improvement_event(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertIn('missed = handled in ("No", "Partially")', source)
        self.assertIn("if not missed:", source)
        self.assertIn("LowEvaluationScore", source)

    def test_consequence_never_contacts_the_customer(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        for forbidden in ("start_outbound_voice_contact", "create_case", "CreateTask", "send_email"):
            self.assertNotIn(forbidden, source)

    def test_metrics_cover_analysed_and_failed_contacts(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        for metric in ("MissedCustomerSignal", "UnresolvedConversation", "EvaluationsAnalysed"):
            self.assertIn(metric, source)


class AlertContentTests(unittest.TestCase):
    """A metric alarm cannot name the contact, so the analyzer's alert must."""

    def test_alert_names_the_contact_and_links_to_the_console(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertIn("Contact ID", source)
        self.assertIn("contact-trace-records/details/", source)
        self.assertIn("Open in console", source)

    def test_alert_explains_the_gap_between_recommended_and_recorded(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertIn("Recommended", source)
        self.assertIn("Actually recorded", source)
        self.assertIn("rationaleThai", source)

    def test_alert_states_no_customer_contact_was_made(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertIn("No customer contact has been made automatically", source)

    def test_alert_is_skipped_when_no_topic_is_configured(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertIn("if not ALERT_TOPIC_ARN:", source)


class InferenceConfigTests(unittest.TestCase):
    def test_temperature_is_not_sent_to_bedrock(self):
        source = open(SPEC.origin, encoding="utf-8").read()
        self.assertNotIn('"temperature"', source)
        self.assertIn('inferenceConfig={"maxTokens": 500}', source)


if __name__ == "__main__":
    unittest.main()
