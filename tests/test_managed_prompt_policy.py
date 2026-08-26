"""Guards the managed-path prompt contract.

The managed engine (Option B) authors its own Thai, so its policy lives in the
Q in Connect prompts rather than in Python. These tests assert the prompts carry
the same negotiation policy as the deterministic engine, so the two paths cannot
drift apart silently.
"""

import json
import unittest
from pathlib import Path

IAC = Path(__file__).parents[1] / "iac"
KEY = "textFullAIPromptEditTemplateConfiguration"

PROMPTS = {
    "bank": ("ai-prompt-bank.json", "BANK_OUTCOME"),
    "insurance": ("ai-prompt-insurance.json", "INSURANCE_OUTCOME"),
    "broker": ("ai-prompt-broker.json", "BROKERAGE_OUTCOME"),
}

SHARED_OUTCOMES = (
    "vulnerability_referral",
    "complaint_logged",
    "do_not_contact",
    "unresolved_needs_human",
)

SCENARIO_OUTCOMES = {
    "bank": ("partial_payment_agreement", "payment_assistance_referral"),
    "insurance": ("affordability_review",),
    "broker": ("licensed_rep_referral",),
}


def prompt_text(scenario):
    filename, _ = PROMPTS[scenario]
    return json.loads((IAC / filename).read_text())[KEY]["text"]


class SharedPolicyTests(unittest.TestCase):
    def test_every_scenario_declares_the_shared_signals(self):
        for scenario in PROMPTS:
            text = prompt_text(scenario)
            for outcome in SHARED_OUTCOMES:
                self.assertIn(outcome, text, f"{scenario} missing {outcome}")

    def test_every_scenario_has_a_hardship_rule(self):
        for scenario in PROMPTS:
            self.assertIn("Hardship rule:", prompt_text(scenario), scenario)

    def test_every_scenario_forbids_repeating_a_question(self):
        for scenario in PROMPTS:
            self.assertIn("Never ask the same question twice", prompt_text(scenario), scenario)

    def test_every_scenario_keeps_the_spoken_pause_rule(self):
        for scenario in PROMPTS:
            self.assertIn("separate each spoken option with a comma", prompt_text(scenario), scenario)

    def test_each_rule_names_its_own_outcome_tool(self):
        for scenario, (_, tool) in PROMPTS.items():
            self.assertIn(tool, prompt_text(scenario), scenario)


class ScenarioPolicyTests(unittest.TestCase):
    def test_scenario_specific_outcomes_are_declared(self):
        for scenario, outcomes in SCENARIO_OUTCOMES.items():
            text = prompt_text(scenario)
            for outcome in outcomes:
                self.assertIn(outcome, text, f"{scenario} missing {outcome}")

    def test_bank_never_promises_programme_approval(self):
        self.assertIn("Never promise approval", prompt_text("bank"))

    def test_bank_acknowledges_confirmed_customer_by_name(self):
        text = prompt_text("bank")
        self.assertIn("CUSTOMER_NAME", text)
        self.assertIn("ขอบคุณที่ยืนยันตัวนะคะ คุณ", text)
        self.assertIn("ขอบคุณที่ยืนยันตัวนะคะ คุณสมชาย ขณะนี้มียอดที่ต้องชำระ", text)
        self.assertIn("do not ask the customer to confirm it again", text)

    def test_bank_uses_explicit_thai_spoken_decimal_amount(self):
        text = prompt_text("bank")
        self.assertIn("AMOUNT_SPOKEN", text)
        self.assertIn("never vocalize, reconstruct, round", text)
        self.assertIn("สองหมื่นสามพันเจ็ดร้อยห้าสิบเอ็ดบาทยี่สิบสามสตางค์", text)
        self.assertIn("For every call, use the provided AMOUNT_SPOKEN and DUE_DATE", text)
        self.assertIn("do not reuse the example amount or date", text)

    def test_insurance_never_advises_lapsing_a_policy(self):
        self.assertIn("Never advise cancelling", prompt_text("insurance"))

    def test_securities_never_comments_on_a_loss(self):
        self.assertIn("Never comment on the loss", prompt_text("broker"))

    def test_insurance_requires_grounded_priority_discovery_before_booking(self):
        text = prompt_text("insurance")
        self.assertIn("APPROVED INSURANCE FACTS", text)
        self.assertIn("customer's priority", text)
        self.assertIn("Only after permission, collect appointment timing", text)
        self.assertIn("generic interest never jumps to an appointment", text)

    def test_brokerage_requires_topic_and_experience_before_next_step(self):
        text = prompt_text("broker")
        self.assertIn("APPROVED BROKERAGE FACTS", text)
        self.assertIn("topic and experience discovery", text)
        self.assertIn("Generic interest is never a terminal outcome", text)
        self.assertIn("do not add market claims", text)

    def test_marketing_scenarios_forbid_hard_sale_and_ungrounded_content(self):
        for scenario in ("insurance", "broker"):
            text = prompt_text(scenario)
            self.assertIn("not a hard sale" if scenario == "broker" else "not a sales close", text)
            for rule in ("one invitation", "urgency", "scarcity", "FOMO", "customer stated", "warm and curious", "never through hype"):
                self.assertIn(rule, text, f"{scenario} missing {rule}")


class ParityWithDeterministicEngineTests(unittest.TestCase):
    """Both engines must expose the same outcome vocabulary."""

    def test_deterministic_outcomes_are_covered_by_the_prompts(self):
        engine = (Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py").read_text()
        for scenario, outcomes in SCENARIO_OUTCOMES.items():
            text = prompt_text(scenario)
            for outcome in outcomes:
                self.assertIn(outcome, engine, f"engine missing {outcome}")
                self.assertIn(outcome, text, f"{scenario} prompt missing {outcome}")
        for outcome in SHARED_OUTCOMES:
            self.assertIn(outcome, engine, f"engine missing {outcome}")


if __name__ == "__main__":
    unittest.main()
