"""Baseline parity tests for insurance and securities.

Scenario 1 (bank) resolved every ask with deterministic Thai matching, while
scenarios 2 and 3 relied on the classifier for the same job. Live calls showed
three consequences, each pinned here:

  * insurance: the model answered "รับทราบค่ะ" with no question, so the call
    dead-ended;
  * insurance: "อาทิตย์หน้า ... วันอังคาร" (next week, Tuesday) was read back as
    วันอาทิตย์หน้า (next Sunday), and the caller's correction hit the
    no-progress guardrail;
  * securities: "sound cut out, please repeat" was transferred to a human.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

UNKNOWN = {
    "intent": "unknown",
    "message": "",
    "rawValue": "",
    "confidence": 0.0,
    "model": "stub",
    "latencyMs": 0,
}


def run(scenario, transcript, previous=None):
    values = {
        "scenario": scenario,
        "customerName": "สมหญิง",
        "amount": "15,500",
        "dueDate": "15 สิงหาคม 2569",
        "mantleState": "{}",
    }
    values.update(previous or {})
    event = {
        "inputTranscript": transcript,
        "sessionState": {
            "sessionAttributes": values,
            "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
        },
    }
    with patch.object(MODULE, "_classify", return_value=dict(UNKNOWN)):
        return MODULE.handler(event, None)["sessionState"]["sessionAttributes"]


class DeterministicMatchingTests(unittest.TestCase):
    def test_insurance_needs_are_matched_without_the_model(self):
        cases = {
            "ออมครับ": "need_savings",
            "เก็บเงินไว้ตอนเกษียณครับ": "need_savings",
            "กังวลค่ารักษาพยาบาลครับ": "need_health",
            "อยากคุ้มครองชีวิตครับ": "need_life",
            "ยังไม่แน่ใจครับ": None,
        }
        for text, expected in cases.items():
            self.assertEqual(MODULE._insurance_need(text), expected, text)

    def test_securities_actions_are_matched_without_the_model(self):
        cases = {
            "สนใจครับ": "seminar",
            "ขอรายละเอียดสัมมนาครับ": "seminar",
            "ขอนัดคุยครับ": "consultation",
            "แนะนำหุ้นตัวไหนดีครับ": "advice_request",
            "ไม่สนใจครับ": None,
        }
        for text, expected in cases.items():
            self.assertEqual(MODULE._broker_action(text), expected, text)

    def test_insurance_never_speaks_a_statement_instead_of_a_question(self):
        result = run("insurance", "สะดวกครับ")
        self.assertEqual(result["done"], "false")
        self.assertIn("สุขภาพ, ชีวิต, หรือการออม", result["nextPrompt"])
        self.assertTrue(result["nextPrompt"].endswith(("คะ", "ค่ะ")))

    def test_securities_interest_starts_discovery_not_a_terminal_outcome(self):
        result = run("broker", "สนใจครับ")
        self.assertEqual(result["outcomeType"], "pending")
        self.assertEqual(json.loads(result["mantleState"])["stage"], "discover_topic")
        self.assertIn("พื้นฐานการลงทุน", result["nextPrompt"])

    def test_insurance_need_advances_to_priority_discovery(self):
        result = run("insurance", "ออมครับ")
        state = json.loads(result["mantleState"])
        self.assertEqual(state["productInterest"], "savings_insurance")
        self.assertEqual(state["stage"], "discover_priority")
        self.assertIn("เป้าหมายหลัก", result["nextPrompt"])
        self.assertNotIn("วันใดและเวลาใด", result["nextPrompt"])


class ThaiWeekVersusSundayTests(unittest.TestCase):
    def test_next_week_is_not_turned_into_next_sunday(self):
        self.assertEqual(MODULE._extract_when("อาทิตย์หน้าครับ"), "อาทิตย์หน้า")

    def test_explicit_sunday_is_preserved(self):
        self.assertEqual(MODULE._extract_when("วันอาทิตย์หน้าครับ"), "วันอาทิตย์หน้า")

    def test_a_specific_weekday_wins_over_a_vague_week(self):
        self.assertEqual(
            MODULE._extract_when("สะดวกอาทิตย์หน้าครับ วันอังคารก็ได้ครับ"), "วันอังคาร"
        )

    def test_bare_weekday_still_gains_the_day_prefix(self):
        self.assertEqual(MODULE._extract_when("อ่า เป็นอังคารหน้าครับ"), "วันอังคารหน้า")


class ReadBackCorrectionTests(unittest.TestCase):
    def test_a_corrected_date_is_recaptured_not_repeated(self):
        need = run("insurance", "ออมครับ")
        first = run("insurance", "สะดวกอาทิตย์หน้าครับ วันอังคารก็ได้ครับ", need)
        self.assertIn("วันอังคาร", first["nextPrompt"])
        corrected = run("insurance", "อ่า เป็นอังคารหน้าครับ ตอนประมาณเที่ยงก็ได้ครับ", first)
        self.assertEqual(corrected["done"], "false")
        self.assertNotEqual(corrected["outcomeType"], "unresolved_needs_human")
        self.assertIn("วันอังคารหน้า", corrected["nextPrompt"])
        confirmed = run("insurance", "ถูกต้องครับ", corrected)
        self.assertEqual(confirmed["outcomeType"], "appointment")
        self.assertEqual(confirmed["preferredTime"], "วันอังคารหน้า")


class RepeatRequestTests(unittest.TestCase):
    def test_asking_to_repeat_is_not_a_human_transfer(self):
        need = run("insurance", "ออมครับ")
        again = run("insurance", "ทำ ไม เส ียง ห าย นะครับ ขอใหม่อีกรอบนึงครับ", need)
        self.assertEqual(again["done"], "false")
        self.assertNotEqual(again["outcomeType"], "human_transfer")
        self.assertEqual(again["nextPrompt"], need["nextPrompt"])

    def test_repeating_does_not_count_towards_the_stall_guardrail(self):
        need = run("insurance", "ออมครับ")
        first = run("insurance", "ขอใหม่อีกครั้งครับ", need)
        second = run("insurance", "ยังไม่ได้ยินครับ", first)
        self.assertEqual(second["done"], "false")
        self.assertNotEqual(second["outcomeType"], "unresolved_needs_human")

    def test_a_genuine_stall_still_escalates(self):
        first = run("insurance", "อือ")
        second = run("insurance", "อือ", first)
        self.assertEqual(second["outcomeType"], "unresolved_needs_human")


if __name__ == "__main__":
    unittest.main()
