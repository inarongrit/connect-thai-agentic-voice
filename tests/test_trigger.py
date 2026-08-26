import importlib.util
import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("INSTANCE_ID", "instance-test")
os.environ.setdefault("CONTACT_FLOW_ID", "managed-flow")
os.environ.setdefault("SOURCE_PHONE", "+15551230000")
os.environ.setdefault("ORIGIN_SECRET", "test-origin-secret-1234567890")

SPEC = importlib.util.spec_from_file_location(
    "trigger", Path(__file__).parents[1] / "lambda" / "index.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def request(body):
    return {
        "headers": {"x-fsi-origin-key": os.environ["ORIGIN_SECRET"]},
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(body),
    }


class TriggerFeatureFlagTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = MODULE.MANTLE_ENABLED
        self.original_flow = MODULE.MANTLE_CONTACT_FLOW_ID

    def tearDown(self):
        MODULE.MANTLE_ENABLED = self.original_enabled
        MODULE.MANTLE_CONTACT_FLOW_ID = self.original_flow

    def test_dynamic_bank_facts_cover_range_and_thai_buddhist_date(self):
        cases = (
            ([0, 0, 0], {"amount": "10,000.01", "dueDate": "20 สิงหาคม 2569"}),
            ([89_999, 98, 4], {"amount": "99,999.99", "dueDate": "16 สิงหาคม 2569"}),
        )
        for random_values, expected in cases:
            with self.subTest(random_values=random_values):
                with patch.object(MODULE.secure_random, "randbelow", side_effect=random_values):
                    self.assertEqual(MODULE._dynamic_bank_facts(date(2026, 8, 21)), expected)

    def test_bank_generates_new_facts_once_per_contact(self):
        generated = [
            {"amount": "12,345.67", "dueDate": "20 สิงหาคม 2569"},
            {"amount": "98,765.43", "dueDate": "17 สิงหาคม 2569"},
        ]
        with patch.object(MODULE, "_dynamic_bank_facts", side_effect=generated) as dynamic:
            first = MODULE._attributes({"scenario": "bank", "mode": "webrtc"})[1]
            second = MODULE._attributes({"scenario": "bank", "mode": "webrtc"})[1]
        self.assertEqual(dynamic.call_count, 2)
        self.assertEqual(first["amount"], "12,345.67")
        self.assertEqual(first["dueDate"], "20 สิงหาคม 2569")
        self.assertEqual(second["amount"], "98,765.43")
        self.assertEqual(second["dueDate"], "17 สิงหาคม 2569")

    def test_option_b_remains_reachable_server_side(self):
        """Removed from the web UI, but kept callable for internal comparison."""
        MODULE.MANTLE_ENABLED = True
        MODULE.MANTLE_CONTACT_FLOW_ID = "experimental-flow"
        client = MagicMock()
        client.start_web_rtc_contact.return_value = {
            "ContactId": "12345678-1234-1234-1234-123456789012",
            "ParticipantId": "participant",
            "ParticipantToken": "token",
            "ConnectionData": {"Meeting": {}, "Attendee": {}},
        }
        with patch.object(MODULE, "connect", client):
            response = MODULE.handler(
                request({"mode": "webrtc", "brainMode": "managed", "scenario": "insurance"}), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["brainMode"], "managed")
        self.assertEqual(client.start_web_rtc_contact.call_args.kwargs["ContactFlowId"], "managed-flow")

    def test_pstn_also_uses_option_a_by_default(self):
        """Option A must work on the phone path too, or removing B would break it."""
        MODULE.MANTLE_ENABLED = True
        MODULE.MANTLE_CONTACT_FLOW_ID = "experimental-flow"
        client = MagicMock()
        client.start_outbound_voice_contact.return_value = {
            "ContactId": "12345678-1234-1234-1234-123456789012"}
        with patch.object(MODULE, "connect", client):
            response = MODULE.handler(
                request({"mode": "pstn", "phone": "0812345678", "scenario": "broker"}), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["brainMode"], "mantle")
        self.assertEqual(
            client.start_outbound_voice_contact.call_args.kwargs["ContactFlowId"], "experimental-flow")

    def test_mantle_is_rejected_when_disabled(self):
        MODULE.MANTLE_ENABLED = False
        MODULE.MANTLE_CONTACT_FLOW_ID = "experimental-flow"
        response = MODULE.handler(
            request({"mode": "webrtc", "brainMode": "mantle", "scenario": "insurance"}),
            None,
        )
        self.assertEqual(response["statusCode"], 403)
        self.assertEqual(json.loads(response["body"])["error"], "mantle dialogue path is not enabled")

    def test_explicit_mantle_request_uses_only_experimental_flow(self):
        MODULE.MANTLE_ENABLED = True
        MODULE.MANTLE_CONTACT_FLOW_ID = "experimental-flow"
        result = {
            "ContactId": "12345678-1234-1234-1234-123456789012",
            "ParticipantId": "participant",
            "ParticipantToken": "token",
            "ConnectionData": {"Meeting": {}, "Attendee": {}},
        }
        client = MagicMock()
        client.start_web_rtc_contact.return_value = result
        with patch.object(MODULE, "connect", client):
            response = MODULE.handler(
                request({"mode": "webrtc", "brainMode": "mantle", "scenario": "insurance"}),
                None,
            )
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["brainMode"], "mantle")
        call = client.start_web_rtc_contact.call_args.kwargs
        self.assertEqual(call["ContactFlowId"], "experimental-flow")
        self.assertEqual(call["Attributes"]["brainMode"], "mantle")

    def test_bank_passes_one_generated_fact_set_to_contact(self):
        MODULE.MANTLE_ENABLED = True
        MODULE.MANTLE_CONTACT_FLOW_ID = "experimental-flow"
        result = {
            "ContactId": "12345678-1234-1234-1234-123456789012",
            "ParticipantId": "participant",
            "ParticipantToken": "token",
            "ConnectionData": {"Meeting": {}, "Attendee": {}},
        }
        client = MagicMock()
        client.start_web_rtc_contact.return_value = result
        facts = {"amount": "23,751.23", "dueDate": "20 สิงหาคม 2569"}
        with patch.object(MODULE, "connect", client), patch.object(
            MODULE, "_dynamic_bank_facts", return_value=facts
        ) as dynamic:
            response = MODULE.handler(request({"mode": "webrtc", "scenario": "bank"}), None)
        self.assertEqual(response["statusCode"], 200)
        # Omitting brainMode now selects Option A, the only engine offered in the UI.
        self.assertEqual(json.loads(response["body"])["brainMode"], "mantle")
        dynamic.assert_called_once_with()
        call = client.start_web_rtc_contact.call_args.kwargs
        self.assertEqual(call["ContactFlowId"], "experimental-flow")
        self.assertEqual(call["Attributes"]["amount"], "23,751.23")
        self.assertEqual(call["Attributes"]["dueDate"], "20 สิงหาคม 2569")


if __name__ == "__main__":
    unittest.main()
