"""Turn-latency changes driven by Contact Lens timings from real calls.

Each class pins one change and the evidence for it, so a later edit has to be deliberate
about giving the gain back. Measured with spike/call_gaps.py against the Contact Lens IVR
analysis files Connect writes to S3.
"""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import botocore.exceptions

ROOT = Path(__file__).parents[1]

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

FACTS = {"customerName": "สมชาย"}


def bank_event(transcript, state="{}"):
    return {
        "sessionState": {
            "sessionAttributes": {
                "scenario": "bank", "customerName": "สมชาย", "amount": "15,500",
                "dueDate": "15 สิงหาคม 2569", "mantleState": state,
            },
            "intent": {"name": "FallbackIntent"},
        },
        "inputTranscript": transcript,
    }


def attrs(event):
    return MODULE.handler(event, None)["sessionState"]["sessionAttributes"]


class RecoveryPromptTest(unittest.TestCase):
    """A real call spent ~7 s on each re-ask re-reading every full option name."""

    MATCHERS = {
        "payment_type": MODULE._payment_type,
        "assistance_options": MODULE._assistance_plan,
        "choose_journey": MODULE._retail_journey,
        "return_reason": MODULE._retail_return_reason,
        "qualify_need": MODULE._insurance_need,
        "choose_action": MODULE._broker_action,
    }

    def labels(self, stage):
        ask = MODULE.RECOVERY_SHORT_ASK_TH[stage]()
        body = ask.removesuffix("คะ")
        parts = [p.strip() for p in body.split(",")]
        parts[-1] = parts[-1].removeprefix("หรือ")
        # Drop the lead-in verb phrase on the first label so it can be matched on its own.
        for lead in ("สะดวกชำระแบบ", "สนใจแบบ", "ต้องการ", "สนใจด้าน", "สนใจ"):
            if parts[0].startswith(lead):
                parts[0] = parts[0][len(lead):]
                break
        return parts

    def test_every_short_label_is_still_recognised(self):
        # Shortening must never make a correct answer unrecognisable.
        for stage, matcher in self.MATCHERS.items():
            for label in self.labels(stage):
                self.assertIsNotNone(matcher(label), f"{stage}: {label!r}")

    def test_short_labels_stay_distinct(self):
        for stage, matcher in self.MATCHERS.items():
            got = [matcher(label) for label in self.labels(stage)]
            self.assertEqual(len(set(got)), len(got), f"{stage}: {got}")

    def test_every_matcher_stage_has_a_short_reask(self):
        self.assertEqual(set(MODULE.RECOVERY_SHORT_ASK_TH), set(self.MATCHERS))

    def test_reask_is_shorter_than_the_original_prompt(self):
        original = f"สะดวก{MODULE._spoken_options(MODULE.BANK_PAYMENT_CHOICES)}คะ"
        reask = MODULE._recovery_message(original, 1, "payment_type")
        self.assertLess(len(reask), len("ขออภัยค่ะ ยังฟังไม่ชัดเจน ") + len(original))

    def test_recovery_does_not_claim_the_line_was_unclear(self):
        # Lex logs showed ASR heard these callers perfectly; the answer simply was not one
        # of the options. Saying "ยังฟังไม่ชัดเจน" was untrue.
        for lead in MODULE.RECOVERY_LEAD_TH.values():
            self.assertNotIn("ไม่ชัด", lead)

    def test_unknown_stage_falls_back_to_the_full_message(self):
        self.assertEqual(MODULE._recovery_message("กรุณาระบุวันที่ค่ะ", 1, "paymentDate"),
                         MODULE.RECOVERY_LEAD_TH[1] + "กรุณาระบุวันที่ค่ะ")

    def test_a_stalled_payment_turn_gets_the_short_reask(self):
        first = attrs(bank_event("ใช่ครับ"))
        second = attrs(bank_event("อือ", first["mantleState"]))
        self.assertEqual(second["done"], "false")
        self.assertIn(MODULE.RECOVERY_SHORT_ASK_TH["payment_type"](), second["nextPrompt"])


class DisclosureTest(unittest.TestCase):
    """The amount line was cut off mid-amount by a one-syllable "อ๋อ" on a real call."""

    def test_disclosure_cannot_be_talked_over(self):
        result = attrs(bank_event("ใช่ครับ"))
        self.assertIn("ขณะนี้มียอดที่ต้องชำระ", result["nextPrompt"])
        self.assertEqual(result["allowInterrupt"], "false")

    def test_the_turn_after_the_disclosure_is_interruptible_again(self):
        first = attrs(bank_event("ใช่ครับ"))
        second = attrs(bank_event("ชำระเต็มจำนวนครับ", first["mantleState"]))
        self.assertEqual(second["allowInterrupt"], "true")

    def test_disclosure_still_carries_amount_date_and_options(self):
        prompt = attrs(bank_event("ใช่ครับ"))["nextPrompt"]
        self.assertIn("หนึ่งหมื่นห้าพันห้าร้อยบาท", prompt)
        self.assertIn("15 สิงหาคม 2569", prompt)
        for label in MODULE.PAYMENT_SHORT_LABELS:
            self.assertIn(label, prompt)

    def test_short_payment_labels_are_recognised(self):
        got = [MODULE._payment_type(label) for label in MODULE.PAYMENT_SHORT_LABELS]
        self.assertEqual(got, ["full", "partial", "installment"])


class BoundedModelCallTest(unittest.TestCase):
    """Luna, the reviewer, drifted to a 5-6.5 s p50 on 2026-10-06."""

    def test_reviewer_has_a_short_read_timeout_and_no_retry(self):
        config = MODULE.bedrock_reviewer.meta.config
        self.assertLessEqual(config.read_timeout, 3)
        self.assertEqual(config.retries["total_max_attempts"], 1)

    def test_lead_is_bounded_but_above_its_worst_observed_latency(self):
        # Terra's slowest observed call was ~2.7 s; the cap must not cut normal answers.
        config = MODULE.bedrock_lead.meta.config
        self.assertGreaterEqual(config.read_timeout, 3)
        self.assertLessEqual(config.read_timeout, 5)
        self.assertEqual(config.retries["total_max_attempts"], 1)

    def test_each_model_gets_its_own_client(self):
        lead, reviewer = MODULE.CLASSIFIER_MODELS
        seen = []

        def fake(model_id, prompt, client=None):
            seen.append((model_id, client))
            return ({"intent": "unknown", "message": "", "rawValue": "", "confidence": 0.3}, 10)

        with patch.object(MODULE, "_invoke", side_effect=fake):
            MODULE._classify("bank", MODULE._initial_state("bank"), "สวัสดี", FACTS)
        self.assertEqual(seen[0], (lead, MODULE.bedrock_lead))
        self.assertEqual(seen[1], (reviewer, MODULE.bedrock_reviewer))

    def test_a_reviewer_timeout_falls_back_and_is_counted(self):
        timeout = botocore.exceptions.ReadTimeoutError(endpoint_url="https://bedrock")
        calls = [
            ({"intent": "unknown", "message": "", "rawValue": "", "confidence": 0.3}, 1200),
            timeout,
        ]

        def fake(model_id, prompt, client=None):
            item = calls.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(MODULE, "_invoke", side_effect=fake):
            result = MODULE._classify("bank", MODULE._initial_state("bank"), "สวัสดี", FACTS)
        self.assertEqual(result["model"], "fallback")
        self.assertIn("ReadTimeoutError", result["errors"])
        # The lead's 1200 ms is counted even though the reviewer is what failed.
        self.assertGreaterEqual(result["latencyMs"], 1200)


class OpenEndedPacingTest(unittest.TestCase):
    def test_open_ended_window_clears_the_observed_mid_utterance_pause(self):
        # The only real mid-utterance pause measured was 0.6 s ("ขยาย" ... "เวลาชําระครับ").
        timeout = int(MODULE.EOT_DEFAULT[1])
        self.assertGreaterEqual(timeout, 600 + 300, "too close to the observed 0.6 s pause")
        self.assertLessEqual(timeout, 1200, "fallback window crept back up")

    def test_entry_blocks_follow_the_open_ended_tier(self):
        for name in ("mantle-flow.json", "mantle-inbound-flow.json"):
            flow = json.loads((ROOT / "iac" / name).read_text())
            for action in flow["Actions"]:
                if action.get("Type") != "ConnectParticipantWithLexBot":
                    continue
                value = action["Parameters"]["LexSessionAttributes"][
                    "x-amz-lex:audio:end-timeout-ms:*:*"]
                if not value.startswith("$."):
                    self.assertEqual(value, MODULE.EOT_DEFAULT[1], action["Identifier"])


class MeasurementToolTest(unittest.TestCase):
    def test_gap_arithmetic(self):
        spec = importlib.util.spec_from_file_location("call_gaps", ROOT / "spike" / "call_gaps.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        turns = [
            {"ParticipantId": "SYSTEM", "BeginOffsetMillis": 0, "EndOffsetMillis": 4000, "Content": "a"},
            {"ParticipantId": "CUSTOMER", "BeginOffsetMillis": 2000, "EndOffsetMillis": 2300, "Content": "อือ"},
            {"ParticipantId": "CUSTOMER", "BeginOffsetMillis": 5000, "EndOffsetMillis": 6000, "Content": "ใช่"},
            {"ParticipantId": "SYSTEM", "BeginOffsetMillis": 8500, "EndOffsetMillis": 20000, "Content": "b"},
        ]
        gaps, prompts, overlaps = tool.measure(turns)
        self.assertEqual([g for g, _ in gaps], [2.5])
        self.assertEqual(prompts, [4.0, 11.5])
        self.assertEqual(overlaps, ["อือ"])

    def test_tool_does_not_hardcode_the_instance(self):
        source = (ROOT / "spike" / "call_gaps.py").read_text()
        self.assertIn("--instance-id", source)
        self.assertNotRegex(source, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


if __name__ == "__main__":
    unittest.main()


class FillerShortCircuitTest(unittest.TestCase):
    """A bare "อือ" at a menu reached the model and cost 3.7 s to come back "unknown"."""

    def test_fillers_and_empty_turns_skip_the_model_at_menus(self):
        for stage, scenario in (("payment_type", "bank"), ("choose_journey", "retail"),
                                ("qualify_need", "insurance"), ("choose_action", "broker")):
            state = MODULE._initial_state(scenario)
            state["stage"] = stage
            for text in ("อือ", "เออ", "อ๋อ", "อือครับ", "เอ่อ ค่ะ", "", "  "):
                self.assertFalse(MODULE._needs_model(scenario, state, text), f"{stage}: {text!r}")

    def test_real_answers_are_not_mistaken_for_filler(self):
        for text in ("ชำระเต็มจำนวนครับ", "ขอคืนสินค้าค่ะ", "ตกงานครับ", "อือ เต็มจำนวน",
                     "เดือนนี้คงต้องขอคิดดูก่อนนะคะ"):
            self.assertFalse(MODULE._is_choice_filler(text), text)

    def test_yes_no_stages_are_untouched(self):
        # At a yes/no stage a bare particle can genuinely mean yes.
        self.assertNotIn("offer_more", MODULE.CHOICE_STAGES)
        self.assertNotIn("offer_next_step", MODULE.CHOICE_STAGES)
        self.assertNotIn("verify_identity", MODULE.CHOICE_STAGES)

    def test_a_filler_still_gets_the_reask(self):
        first = attrs(bank_event("ใช่ครับ"))
        second = attrs(bank_event("อือ", first["mantleState"]))
        self.assertEqual(second["modelUsed"], "deterministic")
        self.assertEqual(second["done"], "false")
        self.assertIn("เต็มจำนวน", second["nextPrompt"])
