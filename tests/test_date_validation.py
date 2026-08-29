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


class BypassMatrixTests(unittest.TestCase):
    """A tester got "32 มกรา" through, so the whole input space is enumerated here.

    The first version only inspected two shapes -- "วันที่ N" and "N <full month name>"
    -- which left abbreviations, spoken numbers without วันที่, and dd/mm formats open.
    Add any new form a tester finds to these lists rather than fixing it ad hoc.
    """

    IMPOSSIBLE = (
        # Abbreviated months, dotted and bare, as ASR returns them.
        "32 ม.ค.", "32 ก.พ.", "32 ธ.ค.", "วันที่ 32 ม.ค.", "32 มค", "32 กพ",
        # Short month names.
        "32 มกรา", "32 ธันวา", "วันที่ 32 ธันวาคม",
        # Spoken numbers with no วันที่ to anchor them.
        "สามสิบสองมกราคม", "สามสิบสอง มกราคม", "สามสิบสองธันวาคม", "วันที่สามสิบสอง",
        # Numeric day/month.
        "32/1", "32/12", "32-12", "1/32", "99/9", "1/13",
        # A month named by number.
        "วันที่ 1 เดือน 13",
        # Days that exceed the specific month.
        "30 กุมภาพันธ์", "31 เมษายน", "31 มิถุนายน", "31 กันยายน", "31 พฤศจิกายน",
        # Out of range outright.
        "วันที่ 32", "0 มกรา", "วันที่ ๓๒ ธันวาคม",
    )

    POSSIBLE = (
        # Real dates, including month ends that do exist and the leap day.
        "วันที่ 15 ธันวาคม", "วันที่ 29 กุมภาพันธ์", "วันที่ 30 เมษายน", "31 ธันวาคม",
        "15 ม.ค.", "1 ก.พ.", "วันที่ 1", "วันที่สิบห้า", "วันที่ยี่สิบเอ็ด",
        "15/12", "1/1", "28/2", "30/4",
        "วันที่ 5 มกราคม 2570", "วันที่ 20 ธันวาคม 2569",
        # Relative answers.
        "พรุ่งนี้", "วันนี้", "มะรืนนี้", "วันศุกร์หน้า", "วันจันทร์หน้า",
        "อีก 3 วัน", "ปลายเดือนนี้", "สิ้นเดือนนี้", "ต้นเดือนหน้า", "สัปดาห์หน้า",
        # Times, because callback and appointment slots use the same validator. A dot
        # separator is a Thai clock format, so dd.mm is deliberately not inspected.
        "14:30", "14.30", "บ่าย 2 โมง", "เช้า 9 โมง",
        # Amounts must never be read as dates.
        "2000 บาท", "20000 บาท",
    )

    def setUp(self):
        self.module = _load()

    def test_every_impossible_form_is_rejected(self):
        for text in self.IMPOSSIBLE:
            with self.subTest(text=text):
                self.assertIsNotNone(self.module._impossible_date(text),
                                     f"{text} walked through the gate")

    def test_no_legitimate_answer_is_rejected(self):
        """A false positive blocks a real payment promise, which is worse than the bug."""
        for text in self.POSSIBLE:
            with self.subTest(text=text):
                message = (self.module._impossible_date(text)
                           or self.module._unusable_date(text))
                self.assertIsNone(message, f"{text} was wrongly rejected: {message}")

    def test_abbreviations_map_to_the_right_month_length(self):
        for text in ("30 ก.พ.", "31 เม.ย.", "31 พ.ย."):
            with self.subTest(text=text):
                self.assertIsNotNone(self.module._impossible_date(text))
        for text in ("29 ก.พ.", "30 เม.ย.", "30 พ.ย."):
            with self.subTest(text=text):
                self.assertIsNone(self.module._impossible_date(text))

    def test_corrections_speak_the_full_month_name(self):
        """Corrections are spoken, so "ม.ค." must not be read out as an abbreviation."""
        for text, expected in (("32 ม.ค.", "มกราคม"), ("32 มกรา", "มกราคม"),
                               ("30 ก.พ.", "กุมภาพันธ์"), ("32/1", "มกราคม"),
                               ("32 ธ.ค.", "ธันวาคม")):
            with self.subTest(text=text):
                message = self.module._impossible_date(text)
                self.assertIn(expected, message)
                self.assertNotIn(".", message)

    def test_the_day_before_a_month_is_parsed_out_of_a_sentence(self):
        self.assertEqual(self.module._leading_day("จะชำระสามสิบสอง"), 32)
        self.assertEqual(self.module._leading_day("จะชำระวันที่15"), 15)
        self.assertIsNone(self.module._leading_day("จะชำระ"))
