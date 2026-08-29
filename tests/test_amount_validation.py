"""Shape checks were standing in for meaning checks on amounts as well as dates.

"จะจ่าย 1 บาท" against a 15,500 baht balance was accepted as a partial payment plan.
"""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"


def _load():
    with patch.dict(os.environ, {"ASSISTANCE_PROGRAM": "true"}, clear=False):
        spec = importlib.util.spec_from_file_location("mantle_dialogue_amounts", SRC)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class ThaiAmountParsingTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_it_reads_digits_thai_numerals_and_separators(self):
        for text, expected in (("15500", 15500), ("2,000 บาท", 2000),
                               ("๕๐๐ บาท", 500), ("1 บาท", 1)):
            with self.subTest(text=text):
                self.assertEqual(self.module._thai_amount_to_int(text), expected)

    def test_it_reads_spoken_thai_amounts(self):
        for text, expected in (("ห้าพันบาท", 5000), ("สามหมื่นสองพันบาท", 32000),
                               ("หนึ่งแสนบาท", 100000), ("ยี่สิบเอ็ด", 21)):
            with self.subTest(text=text):
                self.assertEqual(self.module._thai_amount_to_int(text), expected)

    def test_it_returns_none_when_there_is_no_number(self):
        for text in ("", "ไม่ทราบ", "แล้วแต่"):
            with self.subTest(text=text):
                self.assertIsNone(self.module._thai_amount_to_int(text))


class ImplausibleAmountTests(unittest.TestCase):
    BALANCE = {"amount": "15500"}

    def setUp(self):
        self.module = _load()

    def test_zero_and_negative_are_rejected(self):
        for text in ("0 บาท", "0"):
            with self.subTest(text=text):
                self.assertIsNotNone(self.module._implausible_amount(self.BALANCE, text))

    def test_more_than_the_balance_is_rejected_and_points_at_full_payment(self):
        message = self.module._implausible_amount(self.BALANCE, "20000 บาท")
        self.assertIsNotNone(message)
        self.assertIn("ชำระเต็มจำนวน", message)

    def test_a_token_amount_is_rejected_with_the_minimum_stated(self):
        """One baht against fifteen thousand is not a payment plan."""
        message = self.module._implausible_amount(self.BALANCE, "1 บาท")
        self.assertIsNotNone(message)
        self.assertIn("ขั้นต่ำ", message)

    def test_reasonable_partial_amounts_pass(self):
        for text in ("5000 บาท", "15500 บาท", "สามพันบาท", "200 บาท"):
            with self.subTest(text=text):
                self.assertIsNone(self.module._implausible_amount(self.BALANCE, text))

    def test_the_floor_boundary_is_accepted(self):
        """155 is exactly one percent of 15,500 and must not be rejected."""
        self.assertIsNone(self.module._implausible_amount(self.BALANCE, "155 บาท"))

    def test_without_a_known_balance_only_nonsense_is_rejected(self):
        """An unknown balance must not turn into a blanket refusal."""
        state = {"amount": ""}
        self.assertIsNone(self.module._implausible_amount(state, "1 บาท"))
        self.assertIsNotNone(self.module._implausible_amount(state, "0 บาท"))

    def test_the_choke_point_rejects_before_the_read_back(self):
        state = {"scenario": "bank", "paymentType": "partial", "amount": "15500"}
        message = self.module._set_pending(state, "paymentAmount", "1 บาท")
        self.assertNotIn("pending", state)
        self.assertNotIn("ถูกต้องไหม", message)
        message = self.module._set_pending(state, "paymentAmount", "5000 บาท")
        self.assertIn("pending", state)
        self.assertIn("ถูกต้องไหม", message)


class UnusableDateTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()

    def test_explicit_past_words_are_rejected(self):
        for text in ("เมื่อวานนี้", "สัปดาห์ที่แล้ว", "เดือนที่แล้ว", "ปีที่แล้ว"):
            with self.subTest(text=text):
                message = self.module._unusable_date(text)
                self.assertIsNotNone(message)
                self.assertIn("อนาคต", message)

    def test_a_past_year_is_rejected_in_either_era(self):
        for text in ("วันที่ 1 มกราคม 2560", "วันที่ 1 มกราคม 2017"):
            with self.subTest(text=text):
                self.assertIsNotNone(self.module._unusable_date(text))

    def test_a_year_far_ahead_is_rejected(self):
        self.assertIsNotNone(self.module._unusable_date("ปี 2600"))

    def test_a_bare_day_and_month_is_never_treated_as_past(self):
        """"วันที่ 1 มกราคม" said in August means next January, not last one."""
        self.assertIsNone(self.module._unusable_date("วันที่ 1 มกราคม"))

    def test_today_and_near_future_answers_pass(self):
        for text in ("วันนี้", "พรุ่งนี้", "มะรืนนี้", "วันศุกร์หน้า",
                     "อีก 3 วัน", "ปลายเดือนนี้", "14:30"):
            with self.subTest(text=text):
                self.assertIsNone(self.module._unusable_date(text))

    def test_the_current_and_next_year_pass(self):
        year = self.module._bangkok_today().year
        for offset in (0, 1):
            buddhist = year + offset + 543
            with self.subTest(year=buddhist):
                self.assertIsNone(self.module._unusable_date(f"วันที่ 5 มกราคม {buddhist}"))

    def test_the_buddhist_era_is_converted(self):
        self.assertEqual(self.module._explicit_year("ปี 2569"), 2026)
        self.assertEqual(self.module._explicit_year("ปี 2026"), 2026)


class DefectsFoundOnlyByRunningAConversationTests(unittest.TestCase):
    """Three defects that unit tests missed and a live call exposed."""

    def setUp(self):
        self.module = _load()

    def test_an_amount_is_never_read_as_a_year(self):
        """"20000 บาท" contains "2000", which was rejected as a past year."""
        for text in ("2000 บาท", "20000 บาท", "จะจ่าย 2569 บาท", "1900 บาท"):
            with self.subTest(text=text):
                self.assertIsNone(self.module._explicit_year(text))
                self.assertIsNone(self.module._unusable_date(text))

    def test_a_year_still_reads_with_pi_or_a_month_beside_it(self):
        self.assertEqual(self.module._explicit_year("ปี 2560"), 2017)
        self.assertEqual(self.module._explicit_year("วันที่ 5 มกราคม 2570"), 2027)

    def test_past_words_are_date_shaped_so_they_reach_the_validator(self):
        """Otherwise the turn fell through to the model and ended in a transfer."""
        self.assertTrue(self.module._looks_datetime("เมื่อวานนี้"))
        state = {"scenario": "bank", "paymentType": "full"}
        message = self.module._set_pending(state, "paymentDate", "จะชำระเมื่อวานนี้")
        self.assertNotIn("pending", state)
        self.assertIn("อนาคต", message)

    def test_an_amount_reply_is_not_captured_as_a_corrected_date(self):
        """It produced "ในวันที่ จะจ่าย 5000 บาท" -- a nonsense read-back."""
        for text in ("จะจ่าย 1 บาท", "จะจ่าย 5000 บาท", "14:30"):
            with self.subTest(text=text):
                self.assertFalse(self.module._has_date_expression(text))
        for text in ("พรุ่งนี้", "วันที่ 20 ธันวาคม", "เมื่อวานนี้", "วันศุกร์หน้า"):
            with self.subTest(text=text):
                self.assertTrue(self.module._has_date_expression(text))

    def test_the_balance_reaches_state_so_the_amount_check_can_run(self):
        """The flow sends the balance as an attribute; nothing carried it into state.

        Every amount therefore passed live while the unit tests, which set state
        directly, all went green.
        """
        state = self.module._load_state({"amount": "15500", "mantleState": "{}"}, "bank")
        self.assertEqual(state.get("amount"), "15500")
        self.assertIsNotNone(self.module._implausible_amount(state, "1 บาท"))
        self.assertIsNotNone(self.module._implausible_amount(state, "20000 บาท"))
        self.assertIsNone(self.module._implausible_amount(state, "5000 บาท"))

    def test_confirming_a_date_is_re_read_when_the_caller_answers_with_money(self):
        state = {"scenario": "bank", "paymentType": "partial", "amount": "15500"}
        self.module._set_pending(state, "paymentDate", "พรุ่งนี้")
        result = self.module._handle_pending(state, "จะจ่าย 5000 บาท")
        self.assertIn("ถูกต้องไหม", result["message"])
        # The pending date must be untouched, not replaced by the amount.
        self.assertEqual(state["pending"]["field"], "paymentDate")
        self.assertNotIn("บาท", state["pending"]["raw"])


if __name__ == "__main__":
    unittest.main()
