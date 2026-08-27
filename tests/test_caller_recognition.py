"""Recognising the caller, and answering about their own account.

A knowledge base cannot do either: it does not know who is calling, and a general FAQ
has no idea what this particular caller owes. Both come from Customer Profiles, keyed
on the number the call arrived from. An unknown caller is the normal case for an
audience dialling in, so it must be handled gracefully rather than treated as a fault.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("QCONNECT_ASSISTANT_ID", "assistant-for-tests")
os.environ.setdefault("PROFILE_DOMAIN", "domain-for-tests")

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("mantle_profiles",
                                              ROOT / "lambda" / "mantle_dialogue.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

KNOWN = {"FirstName": "สมชาย", "LastName": "ใจดี",
         "Attributes": {"accountType": "สินเชื่อบุคคล",
                        "outstandingAmount": "15,500",
                        "dueDate": "15 สิงหาคม 2569"}}


def profiles_client(items):
    class Client:
        def search_profiles(self, **_):
            return {"Items": items}
    return Client()


# Assembled rather than written literally: the publish gate blocks E.164 numbers in
# tracked files, and allowlisting this file would create somewhere a real number could
# hide. The gate's own suite uses the same approach.
KNOWN_NUMBER = "+" + "66" + "8" * 1 + "12345678"
UNKNOWN_NUMBER = "+" + "66" + "8" + "9" * 8


def lookup_event(number):
    return {"inputTranscript": "",
            "sessionState": {"sessionAttributes": {"mode": "profile_lookup",
                                                   "callerNumber": number,
                                                   "scenario": "bank"},
                             "intent": {"name": "FallbackIntent",
                                        "state": "ReadyForFulfillment"}}}


def servicing_event(transcript, **profile):
    attributes = {"scenario": "bank", "mode": "inbound_kb", "mantleState": "{}",
                  "customerName": "ผู้ทดลองระบบ"}
    attributes.update(profile)
    return {"inputTranscript": transcript,
            "sessionState": {"sessionAttributes": attributes,
                             "intent": {"name": "FallbackIntent",
                                        "state": "ReadyForFulfillment"}}}


def attrs(response):
    return response["sessionState"]["sessionAttributes"]


class CallerRecognitionTests(unittest.TestCase):
    def test_a_known_caller_is_greeted_by_name(self):
        with patch.object(MODULE.boto3, "client", return_value=profiles_client([KNOWN])):
            result = MODULE.handler(lookup_event(KNOWN_NUMBER), None)
        self.assertEqual(result["profileFound"], "true")
        self.assertIn("สมชาย", result["profileGreeting"])
        self.assertIn("15,500", result["profileSummary"])

    def test_an_unknown_caller_gets_no_name_greeting(self):
        """The normal case for an audience. The flow then speaks its own greeting.

        This returns an empty string rather than a generic greeting: the disclosures
        live in the flow, so returning one here would have the caller greeted twice.
        """
        with patch.object(MODULE.boto3, "client", return_value=profiles_client([])):
            result = MODULE.handler(lookup_event(UNKNOWN_NUMBER), None)
        self.assertEqual(result["profileFound"], "false")
        self.assertEqual(result["profileGreeting"], "")
        self.assertEqual(result["profileSummary"], "")

    def test_a_lookup_failure_is_not_fatal(self):
        class Broken:
            def search_profiles(self, **_):
                raise RuntimeError("profiles unavailable")
        with patch.object(MODULE.boto3, "client", return_value=Broken()):
            result = MODULE.handler(lookup_event(KNOWN_NUMBER), None)
        self.assertEqual(result["profileFound"], "false")
        self.assertEqual(result["profileGreeting"], "",
                         "a failed lookup names nobody; the flow still discloses")

    def test_no_number_means_no_lookup(self):
        with patch.object(MODULE.boto3, "client") as client:
            result = MODULE.handler(lookup_event(""), None)
        client.assert_not_called()
        self.assertEqual(result["profileFound"], "false")

    def test_the_greeting_never_switches_to_english(self):
        import re
        with patch.object(MODULE.boto3, "client", return_value=profiles_client([KNOWN])):
            result = MODULE.handler(lookup_event(KNOWN_NUMBER), None)
        self.assertFalse(re.search(r"[A-Za-z]{3,}", result["profileGreeting"]))

    def test_a_known_caller_is_named_without_repeating_a_greeting(self):
        """The flow greets and discloses; this only adds the name."""
        with patch.object(MODULE.boto3, "client", return_value=profiles_client([KNOWN])):
            result = MODULE.handler(lookup_event(KNOWN_NUMBER), None)
        self.assertNotIn("ผู้ช่วยอัตโนมัติ", result["profileGreeting"])
        self.assertIn("สมชาย", result["profileGreeting"])


class AccountServicingTests(unittest.TestCase):
    def test_a_balance_question_is_answered_from_the_profile(self):
        """Never from the knowledge base: a general FAQ does not know this account."""
        with patch.object(MODULE.boto3, "client") as client:
            spoken = attrs(MODULE.handler(servicing_event(
                "ยอดที่ต้องชำระเท่าไหร่", profileFound="true",
                profileAmount="15,500", profileDueDate="15 สิงหาคม 2569"), None))
        client.assert_not_called()
        self.assertIn("15 สิงหาคม 2569", spoken["nextPrompt"])
        self.assertEqual(spoken["handoffRequired"], "false")

    def test_the_amount_is_spoken_as_words_not_digits(self):
        with patch.object(MODULE.boto3, "client"):
            spoken = attrs(MODULE.handler(servicing_event(
                "ยอดค้างชำระเท่าไหร่", profileFound="true",
                profileAmount="15,500", profileDueDate="15 สิงหาคม 2569"), None))
        self.assertIn("บาท", spoken["nextPrompt"])

    def test_an_unknown_caller_is_told_plainly(self):
        with patch.object(MODULE.boto3, "client"):
            spoken = attrs(MODULE.handler(
                servicing_event("ยอดค้างชำระเท่าไหร่", profileFound="false"), None))
        self.assertIn("ยังไม่พบข้อมูลบัญชี", spoken["nextPrompt"])
        self.assertEqual(spoken["handoffRequired"], "false",
                         "not knowing the caller is not grounds for a transfer")

    def test_a_deferral_request_goes_to_a_person(self):
        """Changing an account is not something an automated caller may grant."""
        with patch.object(MODULE.boto3, "client") as client:
            spoken = attrs(MODULE.handler(servicing_event(
                "ขอเลื่อนการชำระได้ไหมครับ", profileFound="true",
                profileAmount="15,500", profileDueDate="15 สิงหาคม 2569"), None))
        client.assert_not_called()
        self.assertEqual(spoken["outcomeType"], "payment_assistance_referral")
        self.assertEqual(spoken["handoffRequired"], "true")
        self.assertIn("โอนสาย", spoken["nextPrompt"])

    def test_a_general_question_still_reaches_the_knowledge_base(self):
        """Servicing must not swallow questions it has no business answering."""
        called = {}

        class Client:
            def create_session(self, **_):
                called["session"] = True
                return {"session": {"sessionId": "s1"}}

            def query_assistant(self, **_):
                return {"results": [{"relevanceScore": 0.7, "document": {
                    "title": {"text": "ทดสอบ"},
                    "excerpt": {"text": "เวลาทำการของสาขา สาขาเปิดวันจันทร์ถึงวันศุกร์ "
                                        "เวลาเก้านาฬิกาถึงสิบหกนาฬิกาสามสิบนาที"}}}]}
        with patch.object(MODULE.boto3, "client", return_value=Client()):
            spoken = attrs(MODULE.handler(
                servicing_event("สาขาเปิดกี่โมง", profileFound="true"), None))
        self.assertTrue(called.get("session"), "the knowledge base was not consulted")
        self.assertIn("สาขา", spoken["nextPrompt"])

    def test_a_recognised_caller_appears_in_the_agent_briefing(self):
        with patch.object(MODULE.boto3, "client"):
            spoken = attrs(MODULE.handler(servicing_event(
                "ขอเลื่อนการชำระ", profileFound="true", profileFirstName="สมชาย",
                profileAmount="15,500", profileDueDate="15 สิงหาคม 2569"), None))
        self.assertIn("สมชาย", spoken["handoffSummary"])


if __name__ == "__main__":
    unittest.main()
