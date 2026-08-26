"""Helpful, grounded discovery for Insurance and Brokerage before handoff."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "discovery_dialogue", Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

UNKNOWN = {
    "intent": "unknown", "message": "", "rawValue": "", "confidence": 0.0,
    "model": "stub", "latencyMs": 0,
}


def run(scenario, transcript, previous=None):
    values = {
        "scenario": scenario,
        "customerName": "สมหญิง",
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


def state(result):
    return json.loads(result["mantleState"])


class InsuranceDiscoveryTests(unittest.TestCase):
    def test_need_receives_one_grounded_fact_before_any_booking_question(self):
        result = run("insurance", "สนใจความคุ้มครองสุขภาพครับ")
        self.assertEqual(result["done"], "false")
        self.assertEqual(state(result)["stage"], "discover_priority")
        self.assertIn("ตามเงื่อนไขกรมธรรม์", result["nextPrompt"])
        self.assertIn("ค่ารักษา, โรคร้ายแรง, หรือความคุ้มครองที่มีอยู่", result["nextPrompt"])
        self.assertNotIn("วันใดและเวลาใด", result["nextPrompt"])

    def test_priority_is_reflected_before_one_permission_request(self):
        need = run("insurance", "สนใจสุขภาพครับ")
        result = run("insurance", "กังวลค่ารักษาผู้ป่วยในครับ", need)
        self.assertEqual(state(result)["customerGoal"], "กังวลค่ารักษาผู้ป่วยใน")
        self.assertIn("กังวลค่ารักษาผู้ป่วยใน", result["nextPrompt"])
        self.assertIn("ตรวจสอบทางเลือกและเงื่อนไขจริง", result["nextPrompt"])
        self.assertNotIn("วันใดและเวลาใด", result["nextPrompt"])

    def test_declining_handoff_closes_without_another_sales_attempt(self):
        need = run("insurance", "สนใจประกันชีวิตครับ")
        priority = run("insurance", "อยากดูแลครอบครัวครับ", need)
        result = run("insurance", "ไม่ต้องครับ", priority)
        self.assertEqual(result["outcomeType"], "declined")
        self.assertEqual(result["done"], "true")
        self.assertNotIn("นัด", result["nextPrompt"])
        self.assertNotIn("พิเศษ", result["nextPrompt"])


class BrokerageDiscoveryTests(unittest.TestCase):
    def test_generic_interest_never_immediately_sends_or_books(self):
        result = run("broker", "สนใจครับ")
        self.assertEqual(result["outcomeType"], "pending")
        self.assertEqual(state(result)["stage"], "discover_topic")
        self.assertNotIn("ระบบจะส่ง", result["nextPrompt"])
        self.assertNotIn("วันใดและเวลาใด", result["nextPrompt"])

    def test_topic_gets_only_approved_neutral_education(self):
        interested = run("broker", "สนใจครับ")
        result = run("broker", "อยากเรียนรู้การกระจายพอร์ตครับ", interested)
        self.assertEqual(state(result)["topicInterest"], "portfolio_diversification")
        self.assertIn("ไม่ป้องกันการขาดทุนทั้งหมด", result["nextPrompt"])
        for prohibited in ("ผลตอบแทนแน่นอน", "ควรซื้อ", "กำไร", "กำลังขึ้น"):
            self.assertNotIn(prohibited, result["nextPrompt"])

    def test_experience_is_understood_before_one_next_step_offer(self):
        interested = run("broker", "สนใจครับ")
        topic = run("broker", "อยากเริ่มลงทุนครับ", interested)
        result = run("broker", "ยังไม่เคยลงทุนเลยครับ", topic)
        self.assertEqual(state(result)["experienceLevel"], "new_investor")
        self.assertEqual(state(result)["stage"], "offer_next_step")
        self.assertIn("พื้นฐานการลงทุน", result["nextPrompt"])
        self.assertIn("กำลังเริ่มศึกษา", result["nextPrompt"])
        self.assertIn("สัมมนา", result["nextPrompt"])
        self.assertIn("ผู้แนะนำการลงทุน", result["nextPrompt"])

    def test_tailored_seminar_outcome_contains_discovery_summary(self):
        interested = run("broker", "สนใจครับ")
        topic = run("broker", "อยากเริ่มลงทุนครับ", interested)
        experience = run("broker", "ยังไม่เคยลงทุนครับ", topic)
        result = run("broker", "ขอรายละเอียดสัมมนาครับ", experience)
        self.assertEqual(result["outcomeType"], "seminar_details")
        self.assertIn("topic=พื้นฐานการลงทุน", result["outcomeDetail"])
        self.assertIn("experience=กำลังเริ่มศึกษา", result["outcomeDetail"])
        self.assertIn("ไม่ใช่คำแนะนำการลงทุน", result["nextPrompt"])

    def test_consultation_is_scheduled_only_after_discovery(self):
        interested = run("broker", "ขอนัดคุยครับ")
        topic = run("broker", "สนใจวางแผนเกษียณครับ", interested)
        experience = run("broker", "เคยลงทุนอยู่บ้างครับ", topic)
        offered = run("broker", "ขอนัดคุยกับผู้แนะนำครับ", experience)
        self.assertIn("วันใดและเวลาใด", offered["nextPrompt"])
        readback = run("broker", "วันศุกร์บ่ายสองครับ", offered)
        final = run("broker", "ถูกต้องครับ", readback)
        self.assertEqual(final["outcomeType"], "consultation")
        self.assertIn("topic=การวางแผนเกษียณ", final["outcomeDetail"])
        self.assertIn("experience=มีประสบการณ์ลงทุนบ้าง", final["outcomeDetail"])

    def test_stock_recommendation_hits_conduct_barrier_immediately(self):
        result = run("broker", "ช่วยแนะนำหุ้นที่จะขึ้นให้หน่อยครับ")
        self.assertEqual(result["outcomeType"], "advice_request")
        self.assertNotIn("ควรซื้อ", result["nextPrompt"])
        self.assertNotIn("กำไร", result["nextPrompt"])


if __name__ == "__main__":
    unittest.main()
