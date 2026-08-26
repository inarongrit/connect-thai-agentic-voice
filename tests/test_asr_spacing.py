"""ASR whitespace tolerance across every matcher.

Advanced ASR inserts spaces between Thai words and sometimes splits vowel marks.
The most serious consequence found was in the identity gate: `ไม่ ใช่ ครับ`
("no, wrong person") was read as a confirmation, because `_identity_no` matched
only the contiguous `ไม่ใช่` while `_identity_yes` still saw `ใช่`. A wrong-person
call would then have had the debt disclosed to it.

Boolean classifiers now ignore spacing. Extraction keeps the caller's words so
read-backs stay verbatim, but spoken numbers are captured whole.
"""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IdentityGateTests(unittest.TestCase):
    """The highest-risk case: a negation must never become a confirmation."""

    NEGATIVE = (
        "ไม่ใช่ครับ",
        "ไม่ ใช่ ครับ",
        "ไม่ ใช่ ค รับ",
        "ผิด คน ครับ",
        "โทร ผิด ครับ",
    )
    POSITIVE = ("ใช่ครับ", "ใช่ ครับ", "ใช่ครับ ผมเอง", "ครับผม")

    def test_spaced_negation_is_never_a_confirmation(self):
        for text in self.NEGATIVE:
            self.assertTrue(MODULE._identity_no(text), text)
            self.assertFalse(MODULE._identity_yes(text), text)

    def test_confirmation_still_works(self):
        for text in self.POSITIVE:
            self.assertTrue(MODULE._identity_yes(text), text)
            self.assertFalse(MODULE._identity_no(text), text)

    def test_spaced_negation_does_not_disclose_the_debt(self):
        event = {
            "inputTranscript": "ไม่ ใช่ ครับ",
            "sessionState": {
                "sessionAttributes": {
                    "scenario": "bank",
                    "customerName": "สมชาย",
                    "amount": "15,500",
                    "dueDate": "15 สิงหาคม 2569",
                    "mantleState": "{}",
                },
                "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
            },
        }
        result = MODULE.handler(event, None)["sessionState"]["sessionAttributes"]
        self.assertEqual(result["outcomeType"], "unavailable")
        self.assertNotIn("15,500", result["nextPrompt"])
        self.assertNotIn("สิงหาคม", result["nextPrompt"])


class YesNoTests(unittest.TestCase):
    def test_spaced_yes_and_no(self):
        for text in ("ถูก ต้อง ครับ", "ยืน ยัน ครับ", "ตก ลง ครับ"):
            self.assertTrue(MODULE._is_yes(text), text)
        for text in ("ไม่ ถูก ครับ", "ขอ แก้ ครับ", "ไม่ ใช่ ครับ"):
            self.assertTrue(MODULE._is_no(text), text)
            self.assertFalse(MODULE._is_yes(text), text)


class PaymentTypeTests(unittest.TestCase):
    def test_spaced_payment_choices_are_recognised(self):
        cases = {
            "ขอชำระ บาง ส่วน ครับ": "partial",
            "ชำระ เต็ม จำนวน ครับ": "full",
            "ขอ แบ่ง ชำระ ครับ": "installment",
            "ขอชำระบางส่วนครับ": "partial",
        }
        for text, expected in cases.items():
            self.assertEqual(MODULE._payment_type(text), expected, text)


class SpokenNumberTests(unittest.TestCase):
    def test_a_spaced_amount_is_captured_whole(self):
        self.assertEqual(MODULE._extract_amount("ห้า พัน บาท ครับ"), "ห้า พัน บาท")
        self.assertEqual(
            MODULE._extract_amount("หนึ่ง หมื่น ห้า พัน บาท ครับ"), "หนึ่ง หมื่น ห้า พัน บาท"
        )

    def test_contiguous_amount_is_unchanged(self):
        self.assertEqual(MODULE._extract_amount("ห้าพันบาทครับ"), "ห้าพันบาท")

    def test_amount_is_never_truncated_to_a_single_digit_word(self):
        for text in ("ห้า พัน บาท ครับ", "สอง หมื่น บาท ครับ"):
            self.assertNotEqual(MODULE._extract_amount(text), text.split()[0])

    def test_spaced_date_keeps_the_month(self):
        self.assertEqual(
            MODULE._extract_when("วันที่ ยี่ สิบ สิงหาคม"), "วันที่ ยี่ สิบ สิงหาคม"
        )

    def test_looks_helpers_tolerate_spacing(self):
        self.assertTrue(MODULE._looks_amount("ห้า พัน บาท"))
        self.assertTrue(MODULE._looks_datetime("พรุ่ง นี้"))


if __name__ == "__main__":
    unittest.main()
