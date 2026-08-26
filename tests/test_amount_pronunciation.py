import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_REGION", "us-west-2")
ROOT = Path(__file__).parents[1]


def load_module(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


SESSION = load_module("lambda/session_context.py", "session_context_amount_test")


class ThaiAmountPronunciationTests(unittest.TestCase):
    def test_session_context_formatter_matches_requested_phrase(self):
        self.assertEqual(
            SESSION._thai_baht_words("23,751.23"),
            "สองหมื่นสามพันเจ็ดร้อยห้าสิบเอ็ดบาทยี่สิบสามสตางค์",
        )

    def test_formatter_supports_dynamic_baht_and_satang_values(self):
        cases = {
            "1,250.50": "หนึ่งพันสองร้อยห้าสิบบาทห้าสิบสตางค์",
            "42.05": "สี่สิบสองบาทห้าสตางค์",
            "100.00": "หนึ่งร้อยบาทถ้วน",
        }
        for numeric, spoken in cases.items():
            with self.subTest(numeric=numeric):
                self.assertEqual(SESSION._thai_baht_words(numeric), spoken)

    def test_managed_scenario_brief_carries_numeric_and_spoken_amount(self):
        SESSION.AGENT_IDS = {"bank": "agent-bank"}
        SESSION.qconnect = MagicMock()
        SESSION._setup_session({
            "sessionArn": "arn:aws:wisdom:us-west-2:123456789012:session/assistant/session",
            "scenario": "bank",
            "customerName": "สมชาย",
            "amount": "23,751.23",
            "dueDate": "21 สิงหาคม 2569",
        })
        data = SESSION.qconnect.update_session_data.call_args.kwargs["data"]
        brief = next(item["value"]["stringValue"] for item in data if item["key"] == "scenarioBrief")
        self.assertIn("CUSTOMER_NAME=สมชาย", brief)
        self.assertIn("AMOUNT_NUMERIC=23,751.23", brief)
        self.assertIn(
            "AMOUNT_SPOKEN=สองหมื่นสามพันเจ็ดร้อยห้าสิบเอ็ดบาทยี่สิบสามสตางค์",
            brief,
        )
        self.assertIn("DUE_DATE=21 สิงหาคม 2569", brief)


if __name__ == "__main__":
    unittest.main()
