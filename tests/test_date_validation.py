"""A tester offered "วันที่ 32 ธันวาคม" and the agent confirmed it as a commitment.

The gate on date capture is a shape check -- it only looks for digits -- so nothing
stopped an impossible date from being read back, agreed and stored.
"""
import importlib.util
import os
import unittest
from unittest.mock import patch
from pathlib import Path

SRC = Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"


def _load():
    with patch.dict(os.environ, {"ASSISTANCE_PROGRAM": "true"}, clear=False):
        spec = importlib.util.spec_from_file_location("mantle_dialogue_dates", SRC)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class ImpossibleDatesAreRejectedTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_the_reported_utterance_is_rejected(self):
        self.assertIsNotNone(self.module._impossible_date("จะชำระในวันที่ 32 ธันวาคม"))

    def test_days_beyond_the_month_length_are_rejected(self):
        for utterance in ("วันที่ 30 กุมภาพันธ์", "วันที่ 31 เมษายน",
                          "วันที่ 31 มิถุนายน", "วันที่ 31 กันยายน",
                          "วันที่ 31 พฤศจิกายน"):
            with self.subTest(utterance=utterance):
                message = self.module._impossible_date(utterance)
                self.assertIsNotNone(message, "impossible date accepted")
                self.assertIn("กรุณาระบุวันที่ใหม่", message)

    def test_days_outside_one_to_thirty_one_are_rejected(self):
        for utterance in ("วันที่ 0", "วันที่ 32", "วันที่ 45 มกราคม", "วันที่ 99"):
            with self.subTest(utterance=utterance):
                self.assertIsNotNone(self.module._impossible_date(utterance))

    def test_thai_numerals_and_spoken_numbers_are_checked_too(self):
        """ASR may return either form, and a tester can say the number in words."""
        self.assertIsNotNone(self.module._impossible_date("วันที่ ๓๒ ธันวาคม"))
        self.assertIsNotNone(self.module._impossible_date("วันที่สามสิบสอง"))

    def test_the_correction_names_the_month_length(self):
        message = self.module._impossible_date("วันที่ 32 ธันวาคม")
        self.assertIn("ธันวาคม", message)
        self.assertIn("31", message)


class ValidDatesAreNotDisturbedTests(unittest.TestCase):
    """A false positive here would block a real payment promise, which is worse."""

    def setUp(self):
        self.module = _load()

    def test_ordinary_dates_pass(self):
        for utterance in ("วันที่ 15 ธันวาคม", "วันที่ 1", "วันที่ 31 ธันวาคม",
                          "วันที่ 5 มกราคม 2570", "วันที่สิบห้า", "วันที่ยี่สิบเอ็ด"):
            with self.subTest(utterance=utterance):
                self.assertIsNone(self.module._impossible_date(utterance))

    def test_the_leap_day_is_allowed(self):
        self.assertIsNone(self.module._impossible_date("วันที่ 29 กุมภาพันธ์"))

    def test_month_ends_that_do_exist_pass(self):
        for utterance in ("วันที่ 30 เมษายน", "วันที่ 30 มิถุนายน", "วันที่ 31 มกราคม"):
            with self.subTest(utterance=utterance):
                self.assertIsNone(self.module._impossible_date(utterance))

    def test_relative_and_weekday_answers_pass(self):
        for utterance in ("พรุ่งนี้", "มะรืนนี้", "วันศุกร์หน้า", "วันจันทร์",
                          "ปลายเดือนนี้", "สิ้นเดือนนี้", "อีก 3 วัน"):
            with self.subTest(utterance=utterance):
                self.assertIsNone(self.module._impossible_date(utterance))

    def test_clock_times_are_never_read_as_a_day_of_month(self):
        """callbackTime and preferredTime run through the same validator."""
        for utterance in ("14:30", "บ่าย 2 โมง", "เช้า 9 โมง", "18:45"):
            with self.subTest(utterance=utterance):
                self.assertIsNone(self.module._impossible_date(utterance))


class ThaiWordNumberTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_it_parses_spoken_days(self):
        for word, expected in (("ห้า", 5), ("สิบ", 10), ("สิบห้า", 15),
                               ("ยี่สิบ", 20), ("ยี่สิบเอ็ด", 21),
                               ("สามสิบ", 30), ("สามสิบสอง", 32)):
            with self.subTest(word=word):
                self.assertEqual(self.module._thai_words_to_int(word), expected)

    def test_it_returns_none_for_words_that_are_not_numbers(self):
        for word in ("จันทร์", "พรุ่งนี้", "", "อะไร"):
            with self.subTest(word=word):
                self.assertIsNone(self.module._thai_words_to_int(word))


class ConversationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_an_impossible_date_is_not_stored_as_pending(self):
        """Pending drives the read-back, so it must not be set for a bad date."""
        state = {"scenario": "bank", "paymentType": "full"}
        message = self.module._set_pending(state, "paymentDate", "วันที่ 32 ธันวาคม")
        self.assertNotIn("pending", state)
        self.assertIn("ขออภัย", message)
        self.assertNotIn("ถูกต้องไหม", message, "must not ask to confirm a bad date")

    def test_a_valid_date_after_a_rejection_is_captured_normally(self):
        state = {"scenario": "bank", "paymentType": "full"}
        self.module._set_pending(state, "paymentDate", "วันที่ 32 ธันวาคม")
        message = self.module._set_pending(state, "paymentDate", "วันที่ 15 ธันวาคม")
        self.assertIn("pending", state)
        self.assertEqual(state["pending"]["field"], "paymentDate")
        self.assertIn("ถูกต้องไหม", message)

    def test_amounts_are_left_alone_by_the_date_validator(self):
        state = {"scenario": "bank"}
        message = self.module._set_pending(state, "paymentAmount", "สามหมื่นสองพันบาท")
        self.assertIn("pending", state)
        self.assertNotIn("ปฏิทิน", message)


if __name__ == "__main__":
    unittest.main()
