"""Collections feedback: a caller who cannot pay must be offered help, not a transfer.

These exercise the handler directly because the branch is deterministic — the turn
never reaches the model, by design.
"""
import importlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"


def _load(assistance):
    with patch.dict(os.environ, {"ASSISTANCE_PROGRAM": assistance}, clear=False):
        spec = importlib.util.spec_from_file_location("mantle_dialogue_assist", SRC)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class FlagOffKeepsTodaysBehaviourTests(unittest.TestCase):
    """The change must ship inert so it can be enabled deliberately."""

    def test_cannot_pay_still_transfers_when_the_flag_is_off(self):
        module = _load("false")
        self.assertFalse(module.ASSISTANCE_PROGRAM)
        state = {"scenario": "bank", "identityConfirmed": "true",
                 "stage": "hardship_options", "primarySignal": "hardship"}
        result = module._handle_hardship_options(state, "ไม่ไหวเลยค่ะ ไม่มีเงินจริง ๆ")
        self.assertEqual(state["outcomeType"], "payment_assistance_referral")
        self.assertIn(module.HANDOFF_HOLD_TH, result["message"])


class AssistanceProgramTests(unittest.TestCase):
    def setUp(self):
        self.module = _load("true")
        self.assertTrue(self.module.ASSISTANCE_PROGRAM)

    def _cannot_pay(self):
        state = {"scenario": "bank", "identityConfirmed": "true",
                 "stage": "hardship_options", "primarySignal": "hardship"}
        result = self.module._handle_hardship_options(state, "ไม่ไหวเลยค่ะ ไม่มีเงินจริง ๆ")
        return state, result

    def test_it_offers_relief_options_instead_of_transferring(self):
        state, result = self._cannot_pay()
        self.assertFalse(result["done"], "the call must continue, not transfer")
        self.assertNotIn("outcomeType", state)
        self.assertEqual(state["stage"], "assistance_options")
        for option in self.module.BANK_ASSISTANCE_CHOICES:
            self.assertIn(option, result["message"])

    def test_the_options_are_relief_not_ways_to_pay_sooner(self):
        """The original two choices both asked for money; these must not."""
        self.assertEqual(self.module.BANK_ASSISTANCE_CHOICES,
                         ("ลดค่างวดชั่วคราว", "พักชำระเงินต้น", "ขยายระยะเวลาผ่อนชำระ"))

    def test_choosing_an_option_completes_without_a_human(self):
        state, _ = self._cannot_pay()
        result = self.module._handle_assistance_options(state, "ขอลดค่างวดได้ไหมคะ")
        self.assertTrue(result["done"])
        self.assertEqual(state["outcomeType"], "assistance_plan_requested")
        self.assertEqual(state["assistancePlan"], "reduce_installment")
        self.assertNotIn("assistance_plan_requested", self.module.HANDOFF_OUTCOMES)

    def test_each_option_is_recognised(self):
        for utterance, expected in (
            ("ขอลดค่างวดชั่วคราวค่ะ", "reduce_installment"),
            ("อยากพักชำระเงินต้นก่อนค่ะ", "principal_holiday"),
            ("ขอขยายระยะเวลาผ่อนได้ไหม", "extend_term"),
        ):
            with self.subTest(utterance=utterance):
                state, _ = self._cannot_pay()
                self.module._handle_assistance_options(state, utterance)
                self.assertEqual(state["assistancePlan"], expected)

    def test_it_never_claims_the_plan_is_approved(self):
        """Only a person can approve relief; the agent may only record a request."""
        state, _ = self._cannot_pay()
        result = self.module._handle_assistance_options(state, "ขอพักชำระเงินต้นค่ะ")
        message = result["message"]
        for forbidden in ("อนุมัติแล้ว", "อนุมัติเรียบร้อย", "ได้รับการอนุมัติ"):
            self.assertNotIn(forbidden, message)
        self.assertIn("ขึ้นอยู่กับการตรวจสอบ", message)
        self.assertIn("ติดต่อกลับ", message)

    def test_asking_for_a_person_still_works_immediately(self):
        """More self-service must not become a trap."""
        state, _ = self._cannot_pay()
        result = self.module._handle_assistance_options(state, "ขอคุยกับเจ้าหน้าที่ค่ะ")
        self.assertTrue(result["done"])
        self.assertEqual(state["outcomeType"], "human_transfer")
        self.assertIn("human_transfer", self.module.HANDOFF_OUTCOMES)

    def test_a_caller_who_can_pay_something_returns_to_the_partial_path(self):
        state, _ = self._cannot_pay()
        result = self.module._handle_assistance_options(state, "จ่ายได้บางส่วนค่ะ")
        self.assertFalse(result["done"])
        self.assertEqual(state["paymentType"], "partial")

    def test_an_unclear_answer_is_asked_once_then_referred(self):
        """Repeating a menu forever is its own kind of unhelpful."""
        state, _ = self._cannot_pay()
        first = self.module._handle_assistance_options(state, "อืม ไม่รู้ค่ะ")
        self.assertFalse(first["done"])
        second = self.module._handle_assistance_options(state, "ยังไม่แน่ใจค่ะ")
        self.assertTrue(second["done"])
        self.assertEqual(state["outcomeType"], "payment_assistance_referral")

    def test_the_branch_stays_deterministic_and_never_calls_the_model(self):
        state = {"stage": "assistance_options"}
        self.assertFalse(self.module._needs_model("bank", state, "ขอลดค่างวด"))


class LicensedScenariosStillTransferTests(unittest.TestCase):
    """Insurance and broker hardship transfers are a regulatory boundary, not laziness."""

    def setUp(self):
        self.module = _load("true")

    def test_insurance_affordability_still_reaches_a_licensed_person(self):
        state = {"scenario": "insurance"}
        self.module._apply_signal(state, "hardship", "insurance")
        self.assertEqual(state["outcomeType"], "affordability_review")
        self.assertIn("affordability_review", self.module.HANDOFF_OUTCOMES)

    def test_broker_hardship_still_reaches_a_licensed_person(self):
        state = {"scenario": "broker"}
        self.module._apply_signal(state, "hardship", "broker")
        self.assertEqual(state["outcomeType"], "licensed_rep_referral")
        self.assertIn("licensed_rep_referral", self.module.HANDOFF_OUTCOMES)


if __name__ == "__main__":
    unittest.main()
