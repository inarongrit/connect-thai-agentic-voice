"""Inbound callers ask questions, so inbound answers them from the knowledge base.

The first inbound flow replayed the outbound collections script, which asked a caller
who had dialled in whether they were the person we had called, then read out a balance
they never asked about. This mode replaces that: retrieve, answer in Thai with a
citation, and escalate to the handoff when the answer is not grounded.
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

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("mantle_kb", ROOT / "lambda" / "mantle_dialogue.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CONTENT = ROOT / "content" / "kb"


def excerpt_for(name):
    """Rebuild the excerpt shape retrieval returns: sections joined by whitespace."""
    raw = (CONTENT / name).read_text()
    return "  ".join(part.replace("\n", " ").strip()
                     for part in raw.split("\n\n") if part.strip())


def inbound_event(transcript, state="{}"):
    return {
        "inputTranscript": transcript,
        "sessionState": {
            "sessionAttributes": {"scenario": "insurance", "mode": "inbound_kb",
                                  "customerName": "ผู้ทดลองระบบ", "mantleState": state},
            "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
        },
    }


def fake_query(score, name):
    """Stand in for QueryAssistant so the tests need no AWS and no index."""
    class Client:
        def create_session(self, **_):
            return {"session": {"sessionId": "session-1"}}

        def query_assistant(self, **_):
            return {"results": [{
                "relevanceScore": score,
                "document": {"title": {"text": "คำถามที่พบบ่อย ทดสอบ"},
                             "excerpt": {"text": excerpt_for(name)}}}]}
    return Client()


class ContentTests(unittest.TestCase):
    def test_content_is_versioned_with_the_code(self):
        """Answers are only auditable if the source text is reviewable."""
        files = sorted(p.name for p in CONTENT.glob("*.txt"))
        self.assertEqual(files, ["bank-th.txt", "brokerage-th.txt", "insurance-th.txt"])

    def test_content_is_thai_and_marked_as_demo_data(self):
        for path in CONTENT.glob("*.txt"):
            text = path.read_text()
            with self.subTest(file=path.name):
                self.assertTrue(any("\u0e00" <= ch <= "\u0e7f" for ch in text))
                self.assertIn("ข้อมูลสาธิต", text,
                              "content must state it is demonstration data")

    def test_securities_content_carries_the_investment_warning(self):
        text = (CONTENT / "brokerage-th.txt").read_text()
        self.assertIn("การลงทุนมีความเสี่ยง", text)
        self.assertIn("ไม่ใช่คำแนะนำการลงทุน", text)


class PassageSelectionTests(unittest.TestCase):
    def test_the_answering_section_is_chosen_not_the_whole_document(self):
        """Reading a whole FAQ aloud would bury the answer."""
        excerpt = excerpt_for("insurance-th.txt")
        section = MODULE._best_section("ระยะเวลารอคอยกี่วัน", excerpt)
        self.assertIn("ระยะเวลารอคอย", section)
        self.assertNotIn("การยกเลิกกรมธรรม์", section)

    def test_a_different_question_selects_a_different_section(self):
        excerpt = excerpt_for("insurance-th.txt")
        self.assertIn("การเคลม", MODULE._best_section("การเคลมต้องใช้เอกสารอะไร", excerpt))

    def test_sections_shorter_than_a_heading_are_ignored(self):
        self.assertEqual(MODULE._kb_sections("สั้น  x"), [])


class GroundedAnswerTests(unittest.TestCase):
    @staticmethod
    def _attributes(response):
        return response["sessionState"]["sessionAttributes"]

    def test_a_grounded_answer_is_spoken_with_its_citation(self):
        with patch.object(MODULE.boto3, "client",
                          return_value=fake_query(0.70, "insurance-th.txt")):
            attrs = self._attributes(MODULE.handler(inbound_event("ระยะเวลารอคอยกี่วัน"), None))
        self.assertEqual(attrs["done"], "false", "the caller may ask again")
        self.assertIn("สามสิบวัน", attrs["nextPrompt"])
        self.assertIn("อ้างอิงจาก", attrs["nextPrompt"], "an answer must cite its source")
        self.assertEqual(attrs["handoffRequired"], "false")
        self.assertEqual(attrs["modelUsed"], "knowledge-base")

    def test_a_weak_match_escalates_instead_of_guessing(self):
        """Below the retrieval floor the honest answer is that we do not know.

        Off-topic questions still return a document -- "what is the weather" scored
        0.426 against the securities FAQ -- so a floor is the only thing preventing a
        confident answer drawn from an unrelated page.
        """
        with patch.object(MODULE.boto3, "client",
                          return_value=fake_query(0.42, "brokerage-th.txt")):
            attrs = self._attributes(MODULE.handler(inbound_event("อากาศวันนี้เป็นอย่างไร"), None))
        self.assertEqual(attrs["handoffRequired"], "true")
        self.assertEqual(attrs["outcomeType"], "unresolved_needs_human")
        self.assertIn("ยังไม่มีข้อมูล", attrs["nextPrompt"])

    def test_asking_for_a_person_skips_retrieval(self):
        """Someone asking for a human should not be read an FAQ first."""
        with patch.object(MODULE.boto3, "client") as client:
            attrs = self._attributes(MODULE.handler(inbound_event("ขอคุยกับเจ้าหน้าที่ครับ"), None))
        client.assert_not_called()
        self.assertEqual(attrs["outcomeType"], "human_transfer")
        self.assertEqual(attrs["handoffRequired"], "true")

    def test_a_retrieval_failure_escalates_rather_than_erroring(self):
        """A caller must never hear a stack trace or silence."""
        class Broken:
            def create_session(self, **_):
                raise RuntimeError("qconnect unavailable")
        with patch.object(MODULE.boto3, "client", return_value=Broken()):
            attrs = self._attributes(MODULE.handler(inbound_event("ค่าธรรมเนียมเท่าไหร่"), None))
        self.assertEqual(attrs["handoffRequired"], "true")
        self.assertTrue(attrs["nextPrompt"].strip())

    def test_the_answer_stays_short_enough_to_listen_to(self):
        with patch.object(MODULE.boto3, "client",
                          return_value=fake_query(0.70, "brokerage-th.txt")):
            attrs = self._attributes(MODULE.handler(inbound_event("ค่าธรรมเนียมการซื้อขาย"), None))
        self.assertLessEqual(len(attrs["nextPrompt"]), 300)

    def test_the_retrieval_session_is_reused_across_turns(self):
        """A new session per question would lose the thread of the conversation."""
        with patch.object(MODULE.boto3, "client",
                          return_value=fake_query(0.70, "bank-th.txt")):
            first = self._attributes(MODULE.handler(inbound_event("เวลาทำการของสาขา"), None))
            state = json.loads(first["mantleState"])
            self.assertEqual(state["kbSessionId"], "session-1")
            second = self._attributes(
                MODULE.handler(inbound_event("ค่าธรรมเนียมเท่าไหร่", first["mantleState"]), None))
            self.assertEqual(json.loads(second["mantleState"])["kbSessionId"], "session-1")

    def test_outbound_behaviour_is_untouched_without_the_mode(self):
        event = inbound_event("ขอคุยกับเจ้าหน้าที่ครับ")
        del event["sessionState"]["sessionAttributes"]["mode"]
        attrs = self._attributes(MODULE.handler(event, None))
        self.assertNotEqual(attrs.get("modelUsed"), "knowledge-base")

    def test_answers_never_switch_to_english(self):
        import re
        with patch.object(MODULE.boto3, "client",
                          return_value=fake_query(0.70, "insurance-th.txt")):
            attrs = self._attributes(MODULE.handler(inbound_event("การเคลมทำอย่างไร"), None))
        self.assertFalse(re.search(r"[A-Za-z]{3,}", attrs["nextPrompt"]))


if __name__ == "__main__":
    unittest.main()
