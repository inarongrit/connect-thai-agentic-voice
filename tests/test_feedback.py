import hashlib
import hmac
import importlib.util
import json
import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

for key, value in {
    "INSTANCE_ID": "instance-1",
    "CONTACT_FLOW_ID": "managed-flow",
    "SOURCE_PHONE": "+18000000000",
    "ORIGIN_SECRET": "x" * 40,
    "MANTLE_CONTACT_FLOW_ID": "mantle-flow",
    "MANTLE_ENABLED": "true",
    "FEEDBACK_TABLE": "fsi-demo-feedback",
    "AWS_REGION": "us-west-2",
}.items():
    os.environ.setdefault(key, value)

SECRET = os.environ["ORIGIN_SECRET"]

SPEC = importlib.util.spec_from_file_location("trigger", Path(__file__).parents[1] / "lambda" / "index.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CONTACT_ID = "1234abcd-12ab-34cd-56ef-1234567890ab"


def token(contact_id=CONTACT_ID, offset=3600):
    expires = int(time.time()) + offset
    payload = f"{contact_id}.{expires}"
    signature = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def request(body):
    return {
        "headers": {"x-fsi-origin-key": SECRET},
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(body, ensure_ascii=False),
    }


def call(body):
    response = MODULE.handler(request(body), None)
    return response["statusCode"], json.loads(response["body"])


class FeedbackTests(unittest.TestCase):
    def test_valid_feedback_is_stored_with_server_side_context(self):
        put = MagicMock()
        attributes = MagicMock(
            return_value={
                "Attributes": {
                    "brainMode": "mantle",
                    "channelMode": "webrtc",
                    "scenario": "bank",
                    "customerName": "สมชาย",
                }
            }
        )
        contact = MagicMock(
            return_value={
                "Contact": {
                    "InitiationTimestamp": datetime(2026, 8, 19, 2, 30, 0),
                    "DisconnectTimestamp": datetime(2026, 8, 19, 2, 31, 40),
                }
            }
        )
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", attributes
        ), patch.object(MODULE.connect, "describe_contact", contact):
            status, payload = call(
                {
                    "action": "feedback",
                    "statusToken": token(),
                    "overall": 5,
                    "voice": 4,
                    "understanding": 3,
                    "completed": "yes",
                    "comment": "เสียงเป็นธรรมชาติค่ะ",
                    "testerRole": "QA",
                }
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["context"]["brainMode"], "mantle")
        item = put.call_args.kwargs["Item"]
        self.assertEqual(item["contactId"]["S"], CONTACT_ID)
        self.assertEqual(item["overall"]["N"], "5")
        self.assertEqual(item["brainMode"]["S"], "mantle")
        self.assertEqual(item["scenario"]["S"], "bank")
        self.assertEqual(item["customerName"]["S"], "สมชาย")
        self.assertEqual(item["callStartedAt"]["S"], "2026-08-19T02:30:00Z")
        self.assertEqual(item["durationSeconds"]["N"], "100")
        self.assertEqual(item["comment"]["S"], "เสียงเป็นธรรมชาติค่ะ")
        self.assertNotIn("relevance", item)

    def test_feedback_still_records_when_context_lookup_fails(self):
        put = MagicMock()
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", MagicMock(side_effect=RuntimeError("denied"))
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(side_effect=RuntimeError("denied"))):
            status, _ = call({"action": "feedback", "statusToken": token(), "overall": 2})
        self.assertEqual(status, 200)
        item = put.call_args.kwargs["Item"]
        self.assertEqual(item["brainMode"]["S"], "unknown")
        self.assertNotIn("callStartedAt", item)

    def test_client_cannot_spoof_the_engine(self):
        put = MagicMock()
        attributes = MagicMock(return_value={"Attributes": {"brainMode": "managed", "scenario": "insurance"}})
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", attributes
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})):
            call({"action": "feedback", "statusToken": token(), "overall": 1, "brainMode": "mantle"})
        self.assertEqual(put.call_args.kwargs["Item"]["brainMode"]["S"], "managed")

    def test_feedback_requires_a_valid_signed_token(self):
        for bad in ["", "not-a-token", f"{CONTACT_ID}.{int(time.time()) + 60}.deadbeef", token(offset=-120)]:
            with patch.object(MODULE.dynamodb, "put_item") as put:
                status, payload = call({"action": "feedback", "statusToken": bad, "overall": 5})
            self.assertEqual(status, 403, bad)
            self.assertIn("status token", payload["error"])
            put.assert_not_called()

    def test_ratings_are_range_checked_and_overall_is_required(self):
        cases = [
            ({"overall": 6}, "overall"),
            ({"overall": 0}, "overall"),
            ({"overall": "ห้า"}, "overall"),
            ({"voice": 3}, "overall"),
            ({"overall": 4, "latency": 9}, "latency"),
        ]
        for extra, field in cases:
            body = {"action": "feedback", "statusToken": token()}
            body.update(extra)
            with patch.object(MODULE.dynamodb, "put_item") as put:
                status, payload = call(body)
            self.assertEqual(status, 400, extra)
            self.assertIn(field, payload["error"])
            put.assert_not_called()

    def test_completion_value_is_validated_and_comment_is_capped(self):
        with patch.object(MODULE.dynamodb, "put_item") as put:
            status, payload = call(
                {"action": "feedback", "statusToken": token(), "overall": 3, "completed": "maybe"}
            )
        self.assertEqual(status, 400)
        self.assertIn("completed", payload["error"])
        put.assert_not_called()

        put = MagicMock()
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", MagicMock(return_value={"Attributes": {}})
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})):
            call({"action": "feedback", "statusToken": token(), "overall": 3, "comment": "ก" * 4000})
        self.assertEqual(len(put.call_args.kwargs["Item"]["comment"]["S"]), 1000)

    def test_feedback_never_starts_a_call(self):
        with patch.object(MODULE.connect, "start_web_rtc_contact") as web, patch.object(
            MODULE.connect, "start_outbound_voice_contact"
        ) as pstn, patch.object(MODULE.dynamodb, "put_item"), patch.object(
            MODULE.connect, "get_contact_attributes", MagicMock(return_value={"Attributes": {}})
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})):
            call({"action": "feedback", "statusToken": token(), "overall": 5, "mode": "webrtc"})
        web.assert_not_called()
        pstn.assert_not_called()

    def test_github_mirror_is_skipped_when_disabled(self):
        with patch.object(MODULE, "GITHUB_FEEDBACK_ENABLED", False), patch.object(
            MODULE, "_github_token_value"
        ) as secret:
            self.assertIsNone(
                MODULE._mirror_to_github(CONTACT_ID, {"overall": 5}, "yes", "ดี", {"brainMode": "mantle"}, {})
            )
        secret.assert_not_called()

    def test_github_issue_body_excludes_tester_name(self):
        body = MODULE._issue_body(
            CONTACT_ID,
            {"overall": 4, "voice": 5},
            "partial",
            "ตอบช้าไปเล็กน้อย",
            {"scenario": "bank", "channelMode": "webrtc", "brainMode": "mantle", "customerName": "สมชาย"},
            {"callStartedAt": "2026-08-19T02:30:00Z", "durationSeconds": "100"},
        )
        self.assertNotIn("สมชาย", body)
        self.assertIn("ตัวเลือก A", body)
        for leak in ["GPT", "Luna", "Claude", "Haiku", "mantle", "managed"]:
            self.assertNotIn(leak, body, leak)
        self.assertIn("ตอบช้าไปเล็กน้อย", body)
        self.assertIn("4 / 5", body)

    def test_public_mirror_redacts_contact_details_from_the_comment(self):
        """The mirror repo may be public and the comment box is free text."""
        cases = {
            "โทรกลับที่ 0812345678 ได้ครับ": "[หมายเลขถูกซ่อน]",
            "อีเมล somchai@example.com นะ": "[อีเมลถูกซ่อน]",
            "บัตรประชาชน 1-2345-67890-12-3": "[หมายเลขถูกซ่อน]",
            "+66 81 234 5678 ครับ": "[หมายเลขถูกซ่อน]",
        }
        for raw, marker in cases.items():
            with self.subTest(raw=raw):
                redacted = MODULE._redact_for_public(raw)
                self.assertIn(marker, redacted)
                self.assertNotIn("0812345678", redacted)
                self.assertNotIn("somchai@example.com", redacted)

    def test_redaction_leaves_ordinary_feedback_intact(self):
        for safe in ("เสียงดีมาก คะแนน 5 เต็ม 5",
                     "วันที่ 2026-08-26 คุยดีครับ",
                     "ตอบช้าไปเล็กน้อย"):
            with self.subTest(safe=safe):
                self.assertEqual(MODULE._redact_for_public(safe), safe)

    def test_private_record_keeps_the_raw_comment(self):
        """DynamoDB is access-controlled, so analysis keeps the original text."""
        raw = "โทรกลับที่ 0812345678 ได้ครับ"
        put = MagicMock()
        mirror = MagicMock(return_value=None)
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE, "_mirror_to_github", mirror
        ), patch.object(
            MODULE.connect, "get_contact_attributes",
            MagicMock(return_value={"Attributes": {"brainMode": "mantle", "scenario": "bank"}})
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})):
            status, _ = call({"action": "feedback", "statusToken": token(),
                              "overall": 4, "comment": raw})
        self.assertEqual(status, 200)
        self.assertEqual(put.call_args.kwargs["Item"]["comment"]["S"], raw)
        self.assertIn("[หมายเลขถูกซ่อน]", mirror.call_args.args[3])

    def test_feedback_survives_a_github_outage(self):
        put = MagicMock()
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", MagicMock(return_value={"Attributes": {"brainMode": "managed"}})
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})), patch.object(
            MODULE, "_mirror_to_github", MagicMock(side_effect=RuntimeError("github down"))
        ):
            with self.assertRaises(RuntimeError):
                MODULE._feedback({"statusToken": token(), "overall": 4}, {})
        put.assert_called_once()

    def test_github_mirror_failure_is_reported_as_no_issue(self):
        put = MagicMock()
        with patch.object(MODULE.dynamodb, "put_item", put), patch.object(
            MODULE.connect, "get_contact_attributes", MagicMock(return_value={"Attributes": {"brainMode": "managed"}})
        ), patch.object(MODULE.connect, "describe_contact", MagicMock(return_value={"Contact": {}})), patch.object(
            MODULE, "_mirror_to_github", MagicMock(return_value=None)
        ):
            status, payload = call({"action": "feedback", "statusToken": token(), "overall": 4})
        self.assertEqual(status, 200)
        self.assertIsNone(payload["issue"])
        put.assert_called_once()


if __name__ == "__main__":
    unittest.main()
