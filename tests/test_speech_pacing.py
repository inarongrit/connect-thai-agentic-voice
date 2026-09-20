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
            {"eotThreshold": "0.6", "eotTimeoutMs": "1500", "allowInterrupt": "true"},
        )

    def test_a_readback_cannot_be_talked_over(self):
        # The read-back carries both the commitment and the date. Consent captured after
        # only the first clause is consent to something the caller never heard.
        state = MODULE._initial_state("bank")
        state["pending"] = {"field": "paymentDate", "raw": "ยี่สิบ"}
        self.assertEqual(MODULE._speech_tuning(state)["allowInterrupt"], "false")

    def test_menus_and_open_questions_stay_interruptible(self):
        # Blocking a caller from cutting in with their choice is what makes a bot feel
        # like an IVR.
        for stage in ("payment_type", "assistance_options", "choose_journey", "paymentDate"):
            state = MODULE._initial_state("bank")
            state["stage"] = stage
            self.assertEqual(MODULE._speech_tuning(state)["allowInterrupt"], "true", stage)

    def test_confirmation_turn_ends_on_confidence_not_silence(self):
        state = MODULE._initial_state("bank")
        state["pending"] = {"field": "paymentDate", "raw": "ยี่สิบห้า"}
        tuning = MODULE._speech_tuning(state)
        # Low threshold so a plain ใช่ค่ะ ends the turn at once...
        self.assertEqual(tuning["eotThreshold"], "0.5")
        # ...but a tolerant silence window, so "ไม่ใช่ค่ะ ... วันที่ยี่สิบห้าค่ะ" is not
        # truncated at the beat before the correction.
        self.assertEqual(tuning["eotTimeoutMs"], "600")

    def test_dictated_readback_keeps_a_tolerant_timeout(self):
        # A caller who rejects a date readback usually restates the date in the same
        # breath, pausing mid-utterance, so the timeout must not collapse to yes/no.
        state = MODULE._initial_state("bank")
        state["pending"] = {"field": "paymentDate", "raw": "สิบห้า"}
        self.assertEqual(MODULE._speech_tuning(state)["eotTimeoutMs"], "600")

    def test_amount_dictation_is_conservative(self):
        state = MODULE._initial_state("bank")
        state["stage"] = "payment_amount"
        self.assertEqual(
            MODULE._speech_tuning(state),
            {"eotThreshold": "0.8", "eotTimeoutMs": "2000", "allowInterrupt": "true"},
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
                self.assertEqual(attrs[THRESHOLD], "0.6", name)
                self.assertEqual(attrs[TIMEOUT], "1500", name)
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
            # primarySignal is sticky; the tag is only applied on the turn it was raised.
            state["signalTurn"] = state["turn"]
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
        # Retail: a delivery date is dictated exactly like a payment date, which is why
        # the retail journey needed no new pacing rule -- only this entry.
        "deliveryDate": "deliveryDate",
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
            self.assertEqual(tuning["eotTimeoutMs"], "600", field)
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
            self.assertEqual(tuning["eotThreshold"], "0.8", stage)
            self.assertEqual(tuning["eotTimeoutMs"], "2000", stage)

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
        # Retuned down from 7000 after a real call felt sluggish: the window is the
        # FALLBACK for when confidence misses, so a generous value is dead air, not
        # safety. Still comfortably longer than a trailing ค่ะ.
        self.assertGreaterEqual(int(MODULE.EOT_CONFIRMATION[1]), 500)


class MarkupInjectionTest(unittest.TestCase):
    """Model output is derived from a caller's own words, so it is untrusted input.

    The voice engine interprets markup rather than speaking it, so a caller who steers the
    model into emitting a tag would otherwise have it executed. The classifier prompt asks
    for plain Thai, but a prompt is guidance; these are the boundary.
    """

    PAYLOADS = [
        '<emotion value="angry"/>ยอดค้างชำระค่ะ',
        "[laughter]ยอดค้างชำระค่ะ",
        '<volume ratio="2.0"/>ยอดค้างชำระค่ะ',
        "<spell>ABC</spell>ยอดค้างชำระค่ะ",
        '<break time="10s"/>ยอดค้างชำระค่ะ',
        'ยอดค้างชำระค่ะ<emotion value="excited"/>',
        'ยอด<emotion value="sad"/>ค้างชำระค่ะ',
        '<emotion value="angry"',          # unterminated: engine speaks the fragment
        "[laughter",                        # unterminated bracket
        '<<emotion value="angry"/>>',
        '<emotion value="angry"/><emotion value="angry"/>ยอดค้างชำระค่ะ',
    ]

    def test_no_payload_survives_into_spoken_output(self):
        state = MODULE._initial_state("bank")
        for payload in self.PAYLOADS:
            spoken = MODULE._for_speech(payload, state)
            self.assertNotIn("<", spoken, payload)
            self.assertNotIn(">", spoken, payload)
            self.assertNotIn("[", spoken, payload)
            self.assertNotIn("]", spoken, payload)
            self.assertNotIn("laughter", spoken, payload)
            self.assertNotIn("emotion", spoken, payload)
            self.assertNotIn("volume", spoken, payload)

    def test_the_only_tag_that_survives_is_the_one_this_module_adds(self):
        # An injected angry tag must not defeat, duplicate or precede the sympathetic one.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        state["signalTurn"] = state["turn"]
        spoken = MODULE._for_speech('<emotion value="angry"/>เข้าใจค่ะ', state)
        self.assertEqual(spoken.count("<emotion"), 1)
        self.assertTrue(spoken.startswith(MODULE.SYMPATHETIC_TAG))
        self.assertNotIn("angry", spoken)

    def test_sanitisation_applies_even_with_tags_disabled(self):
        # The kill switch turns off what this module adds; it must not turn off the filter.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        with patch.object(MODULE, "SPEECH_TAGS_ENABLED", False):
            spoken = MODULE._for_speech("[laughter]เข้าใจค่ะ", state)
        self.assertNotIn("laughter", spoken)
        self.assertNotIn("[", spoken)

    def test_ordinary_thai_is_untouched(self):
        state = MODULE._initial_state("bank")
        for text in (
            "สะดวกชำระแบบไหนคะ",
            "ลดค่างวด, พักเงินต้น, หรือขยายเวลาคะ",
            "ยอดที่ต้องชำระคือ หนึ่งหมื่นห้าพันบาทถ้วน ครบกำหนด 15 สิงหาคม 2569 ค่ะ",
        ):
            self.assertEqual(MODULE._for_speech(text, state), text)

    def test_stripping_does_not_leave_ragged_whitespace(self):
        state = MODULE._initial_state("bank")
        spoken = MODULE._for_speech('ยอด <emotion value="angry"/> ค้างค่ะ', state)
        self.assertEqual(spoken, "ยอด ค้างค่ะ")

    def test_both_output_paths_sanitise(self):
        # The inbound knowledge-base path builds its own attribute dict and previously
        # bypassed the filter entirely.
        source = (ROOT / "lambda" / "mantle_dialogue.py").read_text()
        self.assertEqual(
            source.count('"nextPrompt": _for_speech('), 2,
            "every nextPrompt must go through _for_speech; one path is unsanitised",
        )
        self.assertNotIn('"nextPrompt": _compact(', source)


class ReportedCallDefectsTest(unittest.TestCase):
    """Regressions for two faults a caller reported from a real Thai call.

    Both were invisible to the existing suite. The first because the tests asserted the
    pacing values were what we had chosen, not whether those values were sensible; the
    second because no test looked at what the caller hears last.
    """

    def test_no_pacing_tier_leaves_the_caller_in_long_dead_air(self):
        # The window is the fallback for when end-of-turn confidence misses, so it is the
        # failure case. 5000/7000 ms made the failure case 4-6x the entire model latency.
        for tier in (MODULE.EOT_CONFIRMATION, MODULE.EOT_DICTATED, MODULE.EOT_DEFAULT):
            self.assertLessEqual(int(tier[1]), 2000, tier)
            self.assertGreaterEqual(int(tier[1]), 500, tier)

    def test_open_ended_turns_are_the_briskest_tier(self):
        # Most turns are open-ended, so this tier sets the conversational feel.
        self.assertLessEqual(int(MODULE.EOT_DEFAULT[1]), int(MODULE.EOT_DICTATED[1]))

    def test_a_closing_line_always_thanks_the_caller(self):
        state = MODULE._initial_state("bank")
        state["stage"] = "closed"
        spoken = MODULE._for_speech("ยืนยันแผนชำระบางส่วนเรียบร้อยแล้ว", state, done=True)
        self.assertIn("ขอบคุณ", spoken)

    def test_a_closing_line_is_not_thanked_twice(self):
        state = MODULE._initial_state("bank")
        state["stage"] = "closed"
        spoken = MODULE._for_speech("ยืนยันแล้ว ขอบคุณค่ะ", state, done=True)
        self.assertEqual(spoken.count("ขอบคุณ"), 1)

    def test_a_handoff_is_not_thanked_goodbye(self):
        # That line ends by asking the caller to hold, so the call is not over.
        state = MODULE._initial_state("bank")
        state["stage"] = "closed"
        spoken = MODULE._for_speech("รับทราบค่ะ กรุณาถือสายรอสักครู่ค่ะ", state,
                                    done=True, handoff=True)
        self.assertNotIn("ขอบคุณ", spoken)

    def test_the_closing_line_is_never_tagged(self):
        # done=true leaves the Lex block: the final line is spoken by a plain
        # MessageParticipant with TextToSpeechType "text", which cannot parse a tag. A flow
        # log from the reported call showed the tag arriving there verbatim.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        state["signalTurn"] = state["turn"]
        state["stage"] = "closed"
        spoken = MODULE._for_speech("ยืนยันแล้ว", state, done=True)
        self.assertNotIn("<", spoken)
        self.assertNotIn("emotion", spoken)

    def test_sympathy_does_not_bleed_across_the_whole_call(self):
        # primarySignal is sticky, so without a turn check one hardship disclosure tagged
        # every later prompt including the closing line.
        state = MODULE._initial_state("bank")
        state["primarySignal"] = "hardship"
        state["turn"] = 2
        state["signalTurn"] = 2
        self.assertIn("<emotion", MODULE._for_speech("เข้าใจค่ะ", state))
        state["turn"] = 5
        self.assertNotIn("<emotion", MODULE._for_speech("สะดวกชำระแบบไหนคะ", state))

    def test_both_flows_have_an_audible_closing_fallback(self):
        # The closing block's only error edge went to the outcome Lambda and then to
        # disconnect, so a failed playback was inaudible and invisible in the flow log.
        for name in ("mantle-flow.json", "mantle-inbound-flow.json"):
            flow = json.loads((ROOT / "iac" / name).read_text())
            actions = {a["Identifier"]: a for a in flow["Actions"]}
            closing = next(a for a in flow["Actions"]
                           if a.get("Type") == "MessageParticipant"
                           and a["Parameters"].get("Text") == "$.Lex.SessionAttributes.nextPrompt")
            for error in closing["Transitions"]["Errors"]:
                target = actions[error["NextAction"]]
                self.assertEqual(target["Type"], "MessageParticipant", name)
                self.assertIn("ขอบคุณ", target["Parameters"]["Text"], name)

    def test_entry_blocks_match_the_open_ended_tier(self):
        # The entry blocks are not driven by _speech_tuning, so the Lambda retune alone
        # would not have fixed the opening turn of a call.
        threshold, timeout = MODULE.EOT_DEFAULT
        for name in ("mantle-flow.json", "mantle-inbound-flow.json"):
            flow = json.loads((ROOT / "iac" / name).read_text())
            for action in flow["Actions"]:
                if action.get("Type") != "ConnectParticipantWithLexBot":
                    continue
                attrs = action["Parameters"]["LexSessionAttributes"]
                if attrs[TIMEOUT].startswith("$."):
                    continue
                self.assertEqual(attrs[THRESHOLD], threshold, action["Identifier"])
                self.assertEqual(attrs[TIMEOUT], timeout, action["Identifier"])


if __name__ == "__main__":
    unittest.main()
