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


def event(scenario, transcript, attributes=None):
    values = {
        "scenario": scenario,
        "customerName": "สมชาย",
        "amount": "15,500",
        "dueDate": "15 สิงหาคม 2569",
        "mantleState": "{}",
    }
    values.update(attributes or {})
    return {
        "inputTranscript": transcript,
        "sessionState": {
            "sessionAttributes": values,
            "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
        },
    }


def attributes(response):
    return response["sessionState"]["sessionAttributes"]


class MantleDialogueTests(unittest.TestCase):
    def test_bank_decimal_amount_has_exact_thai_baht_and_satang_pronunciation(self):
        self.assertEqual(
            MODULE._thai_baht_words("23,751.23"),
            "สองหมื่นสามพันเจ็ดร้อยห้าสิบเอ็ดบาทยี่สิบสามสตางค์",
        )
        first = attributes(MODULE.handler(event("bank", "ใช่ครับ", {
            "amount": "23,751.23",
            "dueDate": "21 สิงหาคม 2569",
        }), None))
        self.assertTrue(
            first["nextPrompt"].startswith(
                "ขอบคุณที่ยืนยันตัวนะคะ คุณสมชาย ขณะนี้มียอดที่ต้องชำระ "
            )
        )
        self.assertIn("สองหมื่นสามพันเจ็ดร้อยห้าสิบเอ็ดบาทยี่สิบสามสตางค์", first["nextPrompt"])
        self.assertIn("21 สิงหาคม 2569", first["nextPrompt"])
        self.assertNotIn("23,751.23", first["nextPrompt"])

    def test_bank_wrong_person_never_discloses_debt(self):
        result = attributes(MODULE.handler(event("bank", "ไม่ใช่ครับ โทรผิดคนแล้ว"), None))
        self.assertEqual(result["done"], "true")
        self.assertEqual(result["outcomeType"], "unavailable")
        self.assertNotIn("สมชาย", result["nextPrompt"])
        forbidden = "15,500 15 สิงหาคม ค้างชำระ เงินกู้"
        for token in forbidden.split():
            self.assertNotIn(token, result["nextPrompt"])

    def test_bank_not_convenient_is_not_wrong_person(self):
        result = attributes(MODULE.handler(event("bank", "ตอนนี้ไม่สะดวกคุยครับ"), None))
        self.assertEqual(result["done"], "false")
        self.assertEqual(result["outcomeType"], "pending")
        self.assertNotIn("15,500", result["nextPrompt"])

    def test_bank_identity_then_adaptive_commitment(self):
        first = attributes(MODULE.handler(event("bank", "ใช่ครับ ผมเอง"), None))
        self.assertEqual(first["done"], "false")
        self.assertIn("หนึ่งหมื่นห้าพันห้าร้อยบาทถ้วน", first["nextPrompt"])
        self.assertNotIn("15,500", first["nextPrompt"])
        state = json.loads(first["mantleState"])
        self.assertTrue(state["identityConfirmed"])

        second = attributes(MODULE.handler(event("bank", "ขอแบ่งชำระครับ", first), None))
        self.assertEqual(json.loads(second["mantleState"])["paymentType"], "installment")

        third = attributes(MODULE.handler(event("bank", "วันที่ยี่สิบสิงหาคม", second), None))
        self.assertIn("วันที่ยี่สิบสิงหาคม", third["nextPrompt"])
        self.assertIn("pending", json.loads(third["mantleState"]))

        fourth = attributes(MODULE.handler(event("bank", "ถูกต้องครับ", third), None))
        self.assertIn("จำนวนเงินงวดแรก", fourth["nextPrompt"])

        fifth = attributes(MODULE.handler(event("bank", "ห้าพันบาท", fourth), None))
        self.assertIn("ห้าพันบาท", fifth["nextPrompt"])
        self.assertIn("วันที่ยี่สิบสิงหาคม", fifth["nextPrompt"])

        final = attributes(MODULE.handler(event("bank", "ยืนยันครับ", fifth), None))
        self.assertEqual(final["done"], "true")
        self.assertEqual(final["outcomeType"], "payment_commitment")
        self.assertEqual(final["paymentDate"], "วันที่ยี่สิบสิงหาคม")
        self.assertEqual(final["paymentAmount"], "ห้าพันบาท")

    def test_insurance_appointment_requires_confirmation(self):
        classified = {
            "intent": "need_health",
            "message": "เข้าใจว่าต้องการความคุ้มครองสุขภาพค่ะ",
            "rawValue": "",
            "confidence": 0.94,
            "model": MODULE.LUNA_MODEL_ID,
            "latencyMs": 900,
        }
        with patch.object(MODULE, "_classify", return_value=classified):
            first = attributes(MODULE.handler(event("insurance", "กังวลค่ารักษาพยาบาลครับ"), None))
            self.assertEqual(json.loads(first["mantleState"])["productInterest"], "health_rider")
            priority = attributes(MODULE.handler(event("insurance", "อยากวางแผนค่ารักษาผู้ป่วยในครับ", first), None))
            self.assertIn("เจ้าหน้าที่ผู้ได้รับอนุญาต", priority["nextPrompt"])
            accepted = attributes(MODULE.handler(event("insurance", "ได้ครับ", priority), None))
            self.assertIn("วันใดและเวลาใด", accepted["nextPrompt"])
            second = attributes(MODULE.handler(event("insurance", "วันจันทร์สิบโมง", accepted), None))
            self.assertEqual(second["done"], "false")
            self.assertIn("วันจันทร์สิบโมง", second["nextPrompt"])
            final = attributes(MODULE.handler(event("insurance", "ถูกต้องค่ะ", second), None))
        self.assertEqual(final["done"], "true")
        self.assertEqual(final["outcomeType"], "appointment")
        self.assertEqual(final["preferredTime"], "วันจันทร์สิบโมง")
        self.assertIn("customer_priority=อยากวางแผนค่ารักษาผู้ป่วยใน", final["outcomeDetail"])

    def test_broker_advice_request_closes_without_recommendation(self):
        classified = {
            "intent": "advice_request",
            "message": "คำขอนี้ต้องให้ผู้แนะนำการลงทุนดูแลค่ะ",
            "rawValue": "",
            "confidence": 0.98,
            "model": MODULE.LUNA_MODEL_ID,
            "latencyMs": 800,
        }
        with patch.object(MODULE, "_classify", return_value=classified):
            result = attributes(MODULE.handler(event("broker", "ช่วยแนะนำหุ้นที่จะขึ้นให้หน่อย"), None))
        self.assertEqual(result["done"], "true")
        self.assertEqual(result["outcomeType"], "advice_request")
        self.assertNotIn("ซื้อ", result["nextPrompt"])

    def test_readback_uses_female_register_and_valid_date_grammar(self):
        first = attributes(MODULE.handler(event("bank", "ใช่ครับ ผมเอง"), None))
        second = attributes(MODULE.handler(event("bank", "ชำระเต็มจำนวนครับ", first), None))
        third = attributes(MODULE.handler(event("bank", "พรุ่งนี้ครับ", second), None))
        prompt = third["nextPrompt"]
        self.assertNotIn("ครับ", prompt)
        self.assertNotIn("ในวันที่ พรุ่งนี้", prompt)
        self.assertNotIn("วันที่พรุ่งนี้", prompt)
        self.assertIn("ชำระเต็มจำนวนพรุ่งนี้", prompt)
        self.assertTrue(prompt.endswith("ถูกต้องไหมคะ"))
        final = attributes(MODULE.handler(event("bank", "ถูกต้องครับ", third), None))
        self.assertEqual(final["paymentDate"], "พรุ่งนี้")

    def test_readback_never_doubles_the_date_marker(self):
        state = {"paymentType": "full"}
        prompt = MODULE._readback(state, "paymentDate", "วันที่ยี่สิบสิงหาคม")
        self.assertNotIn("วันที่ วันที่", prompt)
        self.assertNotIn("ในวันที่วันที่", prompt)
        self.assertIn("ในวันที่ยี่สิบสิงหาคม", prompt)

    def test_politeness_stripping_preserves_the_value(self):
        for raw, expected in [
            ("ห้าพันบาทครับ", "ห้าพันบาท"),
            ("วันจันทร์สิบโมงค่ะ", "วันจันทร์สิบโมง"),
            ("มะรืนนี้นะครับ", "มะรืนนี้"),
            ("สิ้นเดือนครับผม", "สิ้นเดือน"),
            ("วันที่ยี่สิบสิงหาคม", "วันที่ยี่สิบสิงหาคม"),
        ]:
            self.assertEqual(MODULE._strip_politeness(raw), expected)

    def test_model_message_never_speaks_male_particle(self):
        for message in ["รับทราบครับ", "เข้าใจแล้วนะครับ", "ยินดีครับผม"]:
            spoken = MODULE._safe_model_message(message, "สำรองค่ะ")
            self.assertNotIn("ครับ", spoken)
            self.assertTrue(spoken.endswith(("ค่ะ", "คะ")))

    def test_readback_quotes_only_the_value_not_the_whole_sentence(self):
        cases = [
            ("เดี๋ยวจ่ายให้พรุ่งนี้เลยครับ", "พรุ่งนี้", "ชำระเต็มจำนวนพรุ่งนี้"),
            ("จ่ายวันนี้เลยครับ", "วันนี้", "ชำระเต็มจำนวนวันนี้"),
            ("ผมจะโอนให้วันจันทร์ครับ", "วันจันทร์", "ในวันจันทร์"),
            ("ขอเป็นสิ้นเดือนนี้ได้ไหมครับ", "สิ้นเดือนนี้", "ชำระเต็มจำนวนสิ้นเดือนนี้"),
            ("น่าจะจ่ายได้ศุกร์หน้าครับ", "วันศุกร์หน้า", "ในวันศุกร์หน้า"),
        ]
        for utterance, expected_value, expected_phrase in cases:
            first = attributes(MODULE.handler(event("bank", "ใช่ครับ ผมเอง"), None))
            second = attributes(MODULE.handler(event("bank", "ชำระเต็มจำนวนครับ", first), None))
            third = attributes(MODULE.handler(event("bank", utterance, second), None))
            prompt = third["nextPrompt"]
            self.assertIn(expected_phrase, prompt, utterance)
            self.assertNotIn("ครับ", prompt, utterance)
            for echo in ["เดี๋ยว", "ผมจะ", "ขอเป็น", "น่าจะ", "โอนให้", "จ่ายให้"]:
                self.assertNotIn(echo, prompt, f"{utterance} leaked {echo}")
            final = attributes(MODULE.handler(event("bank", "ถูกต้องครับ", third), None))
            self.assertEqual(final["paymentDate"], expected_value, utterance)

    def test_amount_readback_quotes_only_the_amount(self):
        first = attributes(MODULE.handler(event("bank", "ใช่ครับ ผมเอง"), None))
        second = attributes(MODULE.handler(event("bank", "ขอแบ่งชำระครับ", first), None))
        third = attributes(MODULE.handler(event("bank", "พรุ่งนี้ครับ", second), None))
        fourth = attributes(MODULE.handler(event("bank", "ถูกต้องครับ", third), None))
        fifth = attributes(MODULE.handler(event("bank", "ผมจ่ายห้าพันบาทก่อนนะครับ", fourth), None))
        self.assertIn("จำนวน ห้าพันบาท", fifth["nextPrompt"])
        for echo in ["ผมจ่าย", "ก่อน", "ครับ"]:
            self.assertNotIn(echo, fifth["nextPrompt"])
        final = attributes(MODULE.handler(event("bank", "ยืนยันครับ", fifth), None))
        self.assertEqual(final["paymentAmount"], "ห้าพันบาท")

    def test_appointment_time_keeps_day_and_clock_together(self):
        classified = {
            "intent": "need_health",
            "message": "รับทราบค่ะ",
            "rawValue": "",
            "confidence": 0.95,
            "model": MODULE.LUNA_MODEL_ID,
            "latencyMs": 900,
        }
        for utterance, expected in [
            ("ขอเป็นบ่ายสองโมงวันพุธครับ", "บ่ายสองโมงวันพุธ"),
            ("สะดวกวันจันทร์สิบโมงเช้าครับ", "วันจันทร์สิบโมงเช้า"),
        ]:
            with patch.object(MODULE, "_classify", return_value=classified):
                first = attributes(MODULE.handler(event("insurance", "กังวลค่ารักษาครับ"), None))
                priority = attributes(MODULE.handler(event("insurance", "อยากดูค่ารักษาผู้ป่วยในครับ", first), None))
                accepted = attributes(MODULE.handler(event("insurance", "ได้ครับ", priority), None))
                second = attributes(MODULE.handler(event("insurance", utterance, accepted), None))
            self.assertIn(expected, second["nextPrompt"], utterance)
            self.assertNotIn("ขอเป็น", second["nextPrompt"])

    def test_invalid_luna_output_falls_back_to_terra(self):
        responses = [
            ({"intent": "seminar", "message": "รับทราบค่ะ", "rawValue": "ข้อความที่ไม่มีจริง", "confidence": 0.9}, 400),
            ({"intent": "seminar", "message": "รับทราบค่ะ", "rawValue": "สัมมนา", "confidence": 0.9}, 500),
        ]
        with patch.object(MODULE, "_invoke", side_effect=responses):
            result = MODULE._classify("broker", MODULE._initial_state("broker"), "สนใจสัมมนา", {"customerName": "ลูกค้า"})
        self.assertEqual(result["model"], MODULE.TERRA_MODEL_ID)
        self.assertEqual(result["latencyMs"], 900)
        self.assertEqual(result["rawValue"], "สัมมนา")


if __name__ == "__main__":
    unittest.main()
