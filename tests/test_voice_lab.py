"""The voice lab speaks caller-supplied text, so its input validation matters."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda"))

ENV = {
    "INSTANCE_ID": "i-1", "CONTACT_FLOW_ID": "f-1", "MANTLE_CONTACT_FLOW_ID": "f-2",
    "VOICE_LAB_FLOW_ID": "f-lab", "SOURCE_PHONE": "+15551230000",
    "ORIGIN_SECRET": "secret", "TABLE_NAME": "t", "MANTLE_ENABLED": "true",
}


def _load():
    with patch.dict(os.environ, ENV, clear=False), patch("boto3.client"):
        for module in ("index",):
            sys.modules.pop(module, None)
        import index
        return index


class VoiceLabValidationTests(unittest.TestCase):
    def setUp(self):
        self.index = _load()
        self.index.connect = MagicMock()
        self.index.connect.start_web_rtc_contact.return_value = {
            "ContactId": "c-1", "ParticipantId": "p-1",
            "ParticipantToken": "t-1", "ConnectionData": {"Meeting": {}, "Attendee": {}},
        }

    def _call(self, body):
        event = {
            "headers": {"x-fsi-origin-key": "secret"},
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"action": "voicelab", **body}),
        }
        response = self.index.handler(event, None)
        return response["statusCode"], json.loads(response["body"])

    def test_it_starts_a_contact_with_the_supplied_script(self):
        status, payload = self._call({"text": "สวัสดีค่ะ", "voice": "SUDA"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["voice"], "SUDA")
        attributes = self.index.connect.start_web_rtc_contact.call_args
        self.assertEqual(attributes.kwargs["Attributes"]["labText"], "สวัสดีค่ะ")
        self.assertEqual(attributes.kwargs["ContactFlowId"], "f-lab")

    def test_empty_text_is_rejected_before_a_contact_is_started(self):
        status, payload = self._call({"text": "   "})
        self.assertEqual(status, 400)
        self.assertIn("text is required", payload["error"])
        self.index.connect.start_web_rtc_contact.assert_not_called()

    def test_text_beyond_the_cap_is_rejected(self):
        """TTS is billed per character, so an unbounded textarea is a cost hole."""
        status, payload = self._call({"text": "ก" * 601})
        self.assertEqual(status, 400)
        self.assertIn("600 characters", payload["error"])
        self.index.connect.start_web_rtc_contact.assert_not_called()

    def test_the_cap_boundary_itself_is_accepted(self):
        status, _ = self._call({"text": "ก" * 600})
        self.assertEqual(status, 200)

    def test_a_voice_name_with_unexpected_characters_is_rejected(self):
        for voice in ("../etc", "Suda Somchai", "$.Attributes.x", "", "9Suda"):
            with self.subTest(voice=voice):
                status, _ = self._call({"text": "hello", "voice": voice})
                self.assertEqual(status, 400)

    def test_any_plausible_console_voice_name_is_accepted(self):
        """The Connect validator accepts unknown voice names, so the lab must too.

        Operators read names from the console dropdown; the flow falls back to Thai
        when a name turns out to be unsupported at runtime.
        """
        for voice in ("Katie", "Blake", "Somchai", "Gemma"):
            with self.subTest(voice=voice):
                status, _ = self._call({"text": "hello", "voice": voice})
                self.assertEqual(status, 200)

    def test_an_unknown_engine_is_rejected(self):
        status, payload = self._call({"text": "hello", "engine": "magic"})
        self.assertEqual(status, 400)
        self.assertIn("engine must be one of", payload["error"])

    def test_a_malformed_locale_is_rejected(self):
        for language in ("thai", "th_TH", "TH-th", "th-THX"):
            with self.subTest(language=language):
                status, _ = self._call({"text": "hello", "language": language})
                self.assertEqual(status, 400)

    def test_it_requires_the_origin_secret_like_every_other_action(self):
        event = {
            "headers": {},
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps({"action": "voicelab", "text": "hello"}),
        }
        self.assertEqual(self.index.handler(event, None)["statusCode"], 403)
        self.index.connect.start_web_rtc_contact.assert_not_called()

    def test_it_refuses_when_no_lab_flow_is_configured(self):
        self.index.VOICE_LAB_FLOW_ID = ""
        status, payload = self._call({"text": "hello"})
        self.assertEqual(status, 403)
        self.assertIn("not enabled", payload["error"])


if __name__ == "__main__":
    unittest.main()
