"""The live transcript panel, and the access control around it.

Amazon Connect has no live transcript in the agent workspace: the CCP shows one only
during After Contact Work. This endpoint polls Contact Lens real-time analysis so a
presenter can display the Thai conversation while it is happening.

The payload is the verbatim content of a phone call, and Contact Lens cannot redact Thai
in any mode, so authorisation is the most important behaviour here and is tested first.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("CONNECT_INSTANCE_ID", "instance-for-tests")
os.environ.setdefault("ROUTING_PROFILE_ID", "profile-for-tests")
os.environ.setdefault("ORIGIN_SECRET", "secret-for-tests")

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("live_transcript",
                                              ROOT / "lambda" / "live_transcript.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

# Set on the module rather than through the environment. Another test module sets
# ORIGIN_SECRET before this one loads, so os.environ.setdefault was a no-op and the
# fixture secret no longer matched what the module had read -- every request came back
# 403 and the whole suite failed, while this file passed on its own.
SECRET = "secret-for-tests"
MODULE.ORIGIN_SECRET = SECRET


def request(secret=SECRET, contact=None):
    event = {"headers": {"X-FSI-Origin-Key": secret} if secret else {}}
    if contact:
        event["queryStringParameters"] = {"contactId": contact}
    return event


def body(response):
    return json.loads(response["body"])


class AuthorisationTests(unittest.TestCase):
    def test_a_request_without_the_origin_secret_is_refused(self):
        """Reaching the API directly must not return a transcript."""
        self.assertEqual(MODULE.handler(request(secret=None), None)["statusCode"], 403)

    def test_a_wrong_secret_is_refused(self):
        self.assertEqual(MODULE.handler(request(secret="wrong"), None)["statusCode"], 403)

    def test_the_fixture_secret_is_the_one_the_module_uses(self):
        """Guards the cross-module interference that made this file pass alone."""
        self.assertEqual(MODULE.ORIGIN_SECRET, SECRET)

    def test_no_transcript_is_fetched_when_unauthorised(self):
        """The refusal must come before any call that could read call content."""
        with patch.object(MODULE, "_segments") as segments:
            MODULE.handler(request(secret="wrong"), None)
        segments.assert_not_called()

    def test_the_secret_is_compared_without_leaking_timing(self):
        source = (ROOT / "lambda" / "live_transcript.py").read_text()
        self.assertIn("compare_digest", source)
        self.assertNotIn('supplied == ORIGIN_SECRET', source)

    def test_an_unset_secret_refuses_rather_than_allowing_everything(self):
        """A blank configuration must fail closed."""
        with patch.object(MODULE, "ORIGIN_SECRET", ""):
            self.assertEqual(MODULE.handler(request(secret=""), None)["statusCode"], 403)


class TranscriptShapeTests(unittest.TestCase):
    SEGMENTS = {"Segments": [
        {"Transcript": {"ParticipantRole": "CUSTOMER", "Content": "ยอดค้างชำระเท่าไหร่",
                        "BeginOffsetMillis": 65000}},
        {"Transcript": {"ParticipantRole": "AGENT", "Content": "สวัสดีค่ะ",
                        "BeginOffsetMillis": 1200}},
        {"Other": {"Ignored": True}},
    ]}

    def test_turns_are_returned_in_spoken_order(self):
        """Contact Lens does not guarantee ordering; a transcript out of order is unreadable."""
        with patch.object(MODULE, "_lens") as lens:
            lens.list_realtime_contact_analysis_segments.return_value = self.SEGMENTS
            result = body(MODULE.handler(request(contact="c1"), None))
        self.assertEqual([t["text"] for t in result["turns"]],
                         ["สวัสดีค่ะ", "ยอดค้างชำระเท่าไหร่"])

    def test_speakers_are_labelled_in_thai(self):
        with patch.object(MODULE, "_lens") as lens:
            lens.list_realtime_contact_analysis_segments.return_value = self.SEGMENTS
            result = body(MODULE.handler(request(contact="c1"), None))
        self.assertEqual({t["speaker"] for t in result["turns"]}, {"ลูกค้า", "เจ้าหน้าที่"})

    def test_segments_without_a_transcript_are_skipped(self):
        with patch.object(MODULE, "_lens") as lens:
            lens.list_realtime_contact_analysis_segments.return_value = self.SEGMENTS
            result = body(MODULE.handler(request(contact="c1"), None))
        self.assertEqual(len(result["turns"]), 2)

    def test_an_idle_agent_is_not_an_error(self):
        """Between calls the panel should wait, not show a failure."""
        with patch.object(MODULE, "_active_contact", return_value=None):
            response = MODULE.handler(request(), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(body(response)["status"], "idle")

    def test_a_failed_fetch_reports_rather_than_crashing(self):
        with patch.object(MODULE, "_lens") as lens:
            lens.list_realtime_contact_analysis_segments.side_effect = RuntimeError("nope")
            result = body(MODULE.handler(request(contact="c1"), None))
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["detail"])

    def test_responses_are_never_cached(self):
        """A cached transcript would show the previous call's conversation."""
        with patch.object(MODULE, "_active_contact", return_value=None):
            response = MODULE.handler(request(), None)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")


class PanelTests(unittest.TestCase):
    PANEL = (ROOT / "web" / "transcript.html").read_text()

    def test_the_panel_states_that_thai_is_not_redacted(self):
        """Contact Lens cannot redact Thai, so whoever opens this must be told."""
        self.assertIn("ยังไม่รองรับการปิดบัง", self.PANEL)

    def test_the_panel_is_not_indexable(self):
        self.assertIn('name="robots" content="noindex,nofollow"', self.PANEL)

    def test_the_panel_calls_the_protected_path(self):
        self.assertIn('fetch("/transcript"', self.PANEL)

    def test_turn_text_is_inserted_as_text_not_markup(self):
        """Transcript content is untrusted input; innerHTML would allow injection."""
        self.assertIn("said.textContent = turn.text", self.PANEL)
        self.assertNotIn("innerHTML = turn", self.PANEL)


if __name__ == "__main__":
    unittest.main()
