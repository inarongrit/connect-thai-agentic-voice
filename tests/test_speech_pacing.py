"""End-of-turn pacing is chosen per turn by the Lambda, not pinned globally by the flow.

The flow used to set x-amz-lex:audio:end-timeout-ms:*:* to 1100 on every Lex block. That
is one aggressive global default applied to every turn, which AWS guidance calls out as
the wrong tool, and it cut off any caller who paused before a numeral. These tests pin
the replacement: the Lambda emits the values and the loop block reads them back.
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

FLOWS = ("mantle-flow.json", "mantle-inbound-flow.json")
THRESHOLD = "x-amz-lex:audio:end-confidence-threshold:*:*"
TIMEOUT = "x-amz-lex:audio:end-timeout-ms:*:*"
INTERRUPT = "x-amz-lex:allow-interrupt:*:*"


def lex_blocks(name):
    flow = json.loads((ROOT / "iac" / name).read_text())
    return [
        action["Parameters"]
        for action in flow["Actions"]
        if action.get("Type") == "ConnectParticipantWithLexBot"
    ]


class SpeechTuningTest(unittest.TestCase):
    def test_open_ended_turn_uses_documented_defaults(self):
        state = MODULE._initial_state("bank")
        self.assertEqual(
            MODULE._speech_tuning(state),
            {"eotThreshold": "0.7", "eotTimeoutMs": "5000", "allowInterrupt": "true"},
        )

    def test_confirmation_turn_ends_on_confidence_not_silence(self):
        state = MODULE._initial_state("bank")
        state["pending"] = {"field": "paymentDate", "raw": "ยี่สิบห้า"}
        tuning = MODULE._speech_tuning(state)
        # Low threshold so a plain ใช่ค่ะ ends the turn at once...
        self.assertEqual(tuning["eotThreshold"], "0.5")
        # ...but a tolerant silence window, so "ไม่ใช่ค่ะ ... วันที่ยี่สิบห้าค่ะ" is not
        # truncated at the beat before the correction.
        self.assertEqual(tuning["eotTimeoutMs"], "7000")

    def test_dictated_readback_keeps_a_tolerant_timeout(self):
        # A caller who rejects a date readback usually restates the date in the same
        # breath, pausing mid-utterance, so the timeout must not collapse to yes/no.
        state = MODULE._initial_state("bank")
        state["pending"] = {"field": "paymentDate", "raw": "สิบห้า"}
        self.assertEqual(MODULE._speech_tuning(state)["eotTimeoutMs"], "7000")

    def test_amount_dictation_is_conservative(self):
        state = MODULE._initial_state("bank")
        state["stage"] = "payment_amount"
        self.assertEqual(
            MODULE._speech_tuning(state),
            {"eotThreshold": "0.9", "eotTimeoutMs": "7000", "allowInterrupt": "true"},
        )

    def test_emitted_values_stay_inside_supported_ranges(self):
        # Lex rejects a threshold outside 0.5-0.9 outright and silently clamps a timeout
        # outside 500-10000, so an out-of-range value would fail quietly in production.
        states = []
        for stage in ("payment_amount", "assistance_options", "closed"):
            state = MODULE._initial_state("bank")
            state["stage"] = stage
            states.append(state)
        for field in sorted(MODULE.DICTATED_FIELDS) + ["paymentType"]:
            state = MODULE._initial_state("bank")
            state["pending"] = {"field": field, "raw": "x"}
            states.append(state)
        for state in states:
            tuning = MODULE._speech_tuning(state)
            self.assertTrue(0.5 <= float(tuning["eotThreshold"]) <= 0.9, tuning)
            self.assertTrue(500 <= int(tuning["eotTimeoutMs"]) <= 10000, tuning)


class FlowWiringTest(unittest.TestCase):
    def test_no_flow_pins_the_old_aggressive_timeout(self):
        for name in FLOWS:
            for params in lex_blocks(name):
                self.assertNotEqual(
                    params["LexSessionAttributes"].get(TIMEOUT), "1100", name
                )

    def test_loop_block_reads_pacing_from_the_lambda(self):
        # The loop block is the only one that runs after a Lambda turn, so it is the only
        # one that can read computed values back out of the Lex session.
        loops = 0
        for name in FLOWS:
            for params in lex_blocks(name):
                if "SessionAttributes.nextPrompt" not in str(params.get("Text", "")):
                    continue
                loops += 1
                attrs = params["LexSessionAttributes"]
                self.assertEqual(attrs[THRESHOLD], "$.Lex.SessionAttributes.eotThreshold")
                self.assertEqual(attrs[TIMEOUT], "$.Lex.SessionAttributes.eotTimeoutMs")
                self.assertEqual(attrs[INTERRUPT], "$.Lex.SessionAttributes.allowInterrupt")
        self.assertEqual(loops, 2, "expected one loop block per flow")

    def test_first_turn_blocks_carry_static_defaults(self):
        # The greeting plays before any Lambda invocation, so nothing is in session yet.
        for name in FLOWS:
            for params in lex_blocks(name):
                if "SessionAttributes.nextPrompt" in str(params.get("Text", "")):
                    continue
                attrs = params["LexSessionAttributes"]
                self.assertEqual(attrs[THRESHOLD], "0.7", name)
                self.assertEqual(attrs[TIMEOUT], "5000", name)
                self.assertEqual(attrs[INTERRUPT], "true", name)


class HandlerContractTest(unittest.TestCase):
    def test_handler_emits_pacing_attributes_to_the_flow(self):
        # The attribute dict is the whole contract with the flow: a key the flow reads
        # but the Lambda never sends simply never arrives.
        event = {
            "sessionState": {
                "sessionAttributes": {
                    "scenario": "bank",
                    "customerName": "สมชาย",
                    "amount": "15,500",
                    "dueDate": "15 สิงหาคม 2569",
                    "mantleState": "{}",
                },
                "intent": {"name": "FallbackIntent"},
            },
            "inputTranscript": "ขอเลื่อนการชำระ",
        }
        attributes = MODULE.handler(event, None)["sessionState"]["sessionAttributes"]
        for key in ("eotThreshold", "eotTimeoutMs", "allowInterrupt"):
            self.assertIn(key, attributes)
            self.assertIsInstance(attributes[key], str)


class SpeechRenderingTest(unittest.TestCase):
    def test_sympathetic_signals_get_an_emotion_tag(self):
        for signal in sorted(MODULE.SYMPATHETIC_SIGNALS):
            state = MODULE._initial_state("bank")
            state["primarySignal"] = signal
            spoken = MODULE._for_speech("เข้าใจสถานการณ์ค่ะ", state)
            self.assertTrue(spoken.startswith(MODULE.SYMPATHETIC_TAG), signal)
            self.assertIn("เข้าใจสถานการณ์ค่ะ", spoken)

    def test_do_not_contact_gets_no_sympathy(self):
        # A caller asking to be left alone wants a brief neutral acknowledgement.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "do_not_contact"
        self.assertNotIn("<emotion", MODULE._for_speech("รับทราบค่ะ", state))

    def test_ordinary_turns_are_untagged(self):
        state = MODULE._initial_state("bank")
        self.assertEqual(MODULE._for_speech("สะดวกชำระแบบไหนคะ", state), "สะดวกชำระแบบไหนคะ")

    def test_empty_message_stays_empty(self):
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        self.assertEqual(MODULE._for_speech("", state), "")
        self.assertEqual(MODULE._for_speech(None, state), "")

    def test_no_break_tags_are_emitted(self):
        # Options are separated by commas by prompt contract; AWS guidance is that
        # punctuation is the first tool for pausing, so break markup would be redundant.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        self.assertNotIn("<break", MODULE._for_speech("ลดค่างวด, พักเงินต้น, หรือขยายเวลาคะ", state))

    def test_tag_is_a_complete_literal(self):
        # The engine speaks a malformed tag aloud rather than dropping it.
        self.assertTrue(MODULE.SYMPATHETIC_TAG.startswith("<"))
        self.assertTrue(MODULE.SYMPATHETIC_TAG.endswith("/>"))
        self.assertEqual(MODULE.SYMPATHETIC_TAG.count("<"), 1)
        self.assertEqual(MODULE.SYMPATHETIC_TAG.count(">"), 1)

    def test_kill_switch_returns_plain_thai(self):
        # Emotion is beta with no stated th-TH guarantee, so it has to be disableable
        # without a redeploy. Off must reproduce exactly the pre-change output.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        with patch.object(MODULE, "SPEECH_TAGS_ENABLED", False):
            self.assertEqual(MODULE._for_speech("เข้าใจสถานการณ์ค่ะ", state), "เข้าใจสถานการณ์ค่ะ")

    def test_kill_switch_is_declared_in_the_template(self):
        # Declared explicitly so toggling it does not require reconstructing the whole
        # environment block, which update-function-configuration replaces wholesale.
        template = (ROOT / "iac" / "mantle-template.yaml").read_text()
        for name in ("SPEECH_TAGS_ENABLED", "CLASSIFIER_MAX_TOKENS", "CLASSIFIER_STRUCTURED_OUTPUT"):
            self.assertIn(f"{name}:", template, name)


class ClassifierConfigTest(unittest.TestCase):
    def test_max_tokens_clears_the_slowest_model_output(self):
        # Measured: LUNA spends 202-220 output tokens on this task and hit
        # stopReason=max_tokens at 220, producing unparseable truncated JSON.
        self.assertGreaterEqual(MODULE.CLASSIFIER_MAX_TOKENS, 512)

    def test_schema_is_a_json_encoded_string(self):
        # The Converse API takes jsonSchema.schema as a string, not a nested object.
        schema = MODULE.CLASSIFIER_OUTPUT_CONFIG["textFormat"]["structure"]["jsonSchema"]["schema"]
        self.assertIsInstance(schema, str)
        decoded = json.loads(schema)
        self.assertEqual(
            set(decoded["required"]), {"intent", "message", "rawValue", "confidence"}
        )
        self.assertEqual(
            MODULE.CLASSIFIER_OUTPUT_CONFIG["textFormat"]["type"], "json_schema"
        )

    def test_broker_prompt_disambiguates_consultation_from_human(self):
        # Without this the models routed "I'd like to talk to an investment adviser" to
        # human, transferring a caller who picked an offered option.
        state = MODULE._initial_state("broker")
        state["stage"] = "choose_action"
        prompt = MODULE._classifier_prompt("broker", state, "อยากคุยกับผู้แนะนำการลงทุนค่ะ", {})
        self.assertIn("consultation means", prompt)
        self.assertIn("human means", prompt)
        # The clarification is scenario-scoped, so it must not inflate other prompts.
        bank = MODULE._classifier_prompt("bank", MODULE._initial_state("bank"), "สวัสดี", {})
        self.assertNotIn("investment adviser", bank)


class WarmerTest(unittest.TestCase):
    def test_warming_ping_short_circuits_before_any_parsing(self):
        # Answered before event parsing so a ping can never be mistaken for a caller turn.
        self.assertEqual(MODULE.handler({"warmer": True}, None), {"warmed": True})

    def test_a_real_turn_is_not_treated_as_a_ping(self):
        event = {
            "sessionState": {
                "sessionAttributes": {"scenario": "bank", "mantleState": "{}"},
                "intent": {"name": "FallbackIntent"},
            },
            "inputTranscript": "สวัสดีค่ะ",
        }
        result = MODULE.handler(event, None)
        self.assertNotIn("warmed", result)
        self.assertIn("sessionState", result)

    def test_falsy_and_stringy_warmer_values_are_not_pings(self):
        # Only the literal boolean counts, so a stray attribute cannot silence a turn.
        for value in ("true", 1, None, False):
            event = {
                "warmer": value,
                "sessionState": {
                    "sessionAttributes": {"scenario": "bank", "mantleState": "{}"},
                    "intent": {"name": "FallbackIntent"},
                },
                "inputTranscript": "สวัสดีค่ะ",
            }
            self.assertNotIn("warmed", MODULE.handler(event, None), repr(value))


class DictatedStageCoverageTest(unittest.TestCase):
    """Guards the snake_case/camelCase trap in stage naming.

    Every prompt in _ask_for asks the caller to dictate a date, time or amount, so every
    one of them must run on the tolerant end-of-turn settings. The stage names do not
    follow the field names (payment_amount vs paymentDate), so this cannot be derived and
    has to be checked.
    """

    DICTATED_FIELD_TO_STAGE = {
        "paymentDate": "paymentDate",
        "paymentAmount": "payment_amount",
        "preferredTime": "preferredTime",
        "callbackTime": "callbackTime",
    }

    def test_every_dictated_prompt_has_a_dictated_stage(self):
        for field, stage in self.DICTATED_FIELD_TO_STAGE.items():
            self.assertIn(stage, MODULE.DICTATED_STAGES, f"{field} -> {stage}")

    def test_every_dictated_prompt_is_also_a_dictated_readback_field(self):
        # DICTATED_STAGES governs the turn where the value is asked for; DICTATED_FIELDS
        # governs the turn after the readback, when a caller who disagrees restates it.
        # callbackTime was present in one and absent from the other, which cut off a
        # caller restating a callback time. The two sets must cover the same prompts.
        for field in self.DICTATED_FIELD_TO_STAGE:
            self.assertIn(field, MODULE.DICTATED_FIELDS, field)

    def test_rejected_dictated_readback_keeps_the_tolerant_timeout(self):
        for field in sorted(MODULE.DICTATED_FIELDS):
            state = MODULE._initial_state("bank")
            state["pending"] = {"field": field, "raw": "x"}
            tuning = MODULE._speech_tuning(state)
            self.assertEqual(tuning["eotTimeoutMs"], "7000", field)
            # Still ends on confidence, because the likely answer is one word.
            self.assertEqual(tuning["eotThreshold"], "0.5", field)

    def test_ask_for_table_has_not_grown_unnoticed(self):
        # If a new dictated prompt is added, this fails until the mapping above and
        # DICTATED_STAGES are both updated.
        import inspect

        source = inspect.getsource(MODULE._ask_for)
        for field in self.DICTATED_FIELD_TO_STAGE:
            self.assertIn(f'"{field}"', source)
        quoted = {line.split('"')[1] for line in source.splitlines() if line.strip().startswith('"')}
        self.assertEqual(
            quoted, set(self.DICTATED_FIELD_TO_STAGE),
            "_ask_for gained or lost a dictated prompt; update DICTATED_STAGES too",
        )

    def test_dictated_stages_actually_get_the_tolerant_settings(self):
        for stage in sorted(MODULE.DICTATED_STAGES):
            state = MODULE._initial_state("bank")
            state["stage"] = stage
            tuning = MODULE._speech_tuning(state)
            self.assertEqual(tuning["eotThreshold"], "0.9", stage)
            self.assertEqual(tuning["eotTimeoutMs"], "7000", stage)

    def test_every_dictated_stage_is_a_real_stage_value(self):
        # A typo here would silently never match, which is the bug this test exists for.
        import re

        source = (ROOT / "lambda" / "mantle_dialogue.py").read_text()
        assigned = set(re.findall(r'state\["stage"\] = "([A-Za-z_]+)"', source))
        for stage in MODULE.DICTATED_STAGES:
            self.assertIn(stage, assigned, f"{stage} is never assigned to state['stage']")


class PendingFieldCoverageTest(unittest.TestCase):
    """Pins the assumption _speech_tuning relies on: every readback field is dictated.

    _speech_tuning gives all pending readbacks the tolerant silence window without
    branching on the field. That is only safe while every field reaching a readback is one
    the caller dictates. If a plain yes/no readback is ever added -- confirming a payment
    type or a relief option -- this test fails, and whoever adds it has to decide the
    pacing deliberately instead of inheriting it.
    """

    def _pending_fields(self):
        source = (ROOT / "lambda" / "mantle_dialogue.py").read_text()
        return set(re.findall(r'_set_pending\(state,\s*"([A-Za-z]+)"', source))

    def test_every_pending_field_is_dictated(self):
        fields = self._pending_fields()
        self.assertTrue(fields, "found no _set_pending call sites; the regex has drifted")
        self.assertEqual(
            fields - MODULE.DICTATED_FIELDS, set(),
            "a non-dictated readback was added; choose its pacing explicitly in "
            "_speech_tuning rather than letting it inherit the confirmation window",
        )

    def test_dictated_fields_has_no_unreachable_entries(self):
        # Keeps the set honest in the other direction, so it documents real behaviour.
        self.assertEqual(MODULE.DICTATED_FIELDS - self._pending_fields(), set())

    def test_confirmation_window_is_not_shorter_than_a_thai_correction_beat(self):
        # The failure mode is a pause inside the turn, not a long word: end-timeout-ms
        # measures silence after speech. 800 ms was the original value and is too tight
        # for "ไม่ใช่ค่ะ ... วันที่ยี่สิบห้าค่ะ".
        self.assertGreaterEqual(int(MODULE.EOT_CONFIRMATION[1]), 5000)


if __name__ == "__main__":
    unittest.main()
