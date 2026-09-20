"""Retail: the first non-FSI vertical, built from the primitives the FSI scenarios prove.

The point of retail is that it introduced no new dialogue pattern -- a journey choice, a
dictated date with a read-back, a reason choice and a handoff were all already there. These
tests assert that, and guard the two places where retail could quietly break something else:
the shared dispatch, and the Thai negation used to end a call.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ATTRIBUTES = {
    "scenario": "retail",
    "customerName": "สมชาย",
    "amount": "1,290",
    "dueDate": "15 สิงหาคม 2569",
}
UNCLASSIFIED = {"intent": "unknown", "message": "", "rawValue": "", "confidence": 0.0}


def walk(utterances, attributes=None):
    """Drive a retail conversation, honouring a pending read-back like the handler does."""
    attributes = attributes or ATTRIBUTES
    state = MODULE._initial_state("retail")
    results = []
    for utterance in utterances:
        if state.get("pending"):
            result = MODULE._handle_pending(state, utterance)
        else:
            result = None
        if result is None:
            result = MODULE._handle_retail(state, utterance, dict(UNCLASSIFIED), attributes)
        results.append(result)
    return state, results


class RegistrationTest(unittest.TestCase):
    def test_retail_is_a_known_scenario(self):
        self.assertIn("retail", MODULE.SCENARIOS)
        self.assertIn("retail", MODULE.ALLOWED_INTENTS)
        self.assertEqual(MODULE._initial_state("retail")["stage"], "choose_journey")

    def test_every_journey_intent_is_allowed(self):
        self.assertTrue(MODULE.RETAIL_JOURNEYS <= MODULE.ALLOWED_INTENTS["retail"])

    def test_dispatch_refuses_an_unknown_scenario(self):
        # Broker used to be the implicit else, so a bad scenario name was silently handled
        # as a brokerage call. With four verticals that fallback is a real hazard.
        with self.assertRaises(ValueError):
            MODULE._dispatch_scenario("telco", MODULE._initial_state("retail"), "สวัสดี",
                                      dict(UNCLASSIFIED), ATTRIBUTES)

    def test_each_scenario_still_dispatches(self):
        for scenario in sorted(MODULE.SCENARIOS):
            state = MODULE._initial_state(scenario)
            result = MODULE._dispatch_scenario(scenario, state, "สวัสดีค่ะ",
                                               dict(UNCLASSIFIED), ATTRIBUTES)
            self.assertIn("message", result, scenario)


class JourneyMatcherTest(unittest.TestCase):
    def test_reschedule_phrasings(self):
        for text in ("ขอเลื่อนวันจัดส่งค่ะ", "เปลี่ยนวันส่งได้ไหมคะ", "วันนั้นไม่อยู่บ้านค่ะ",
                     "ขอรับของวันอื่นค่ะ", "รับของไม่ได้ค่ะ"):
            self.assertEqual(MODULE._retail_journey(text), "reschedule", text)

    def test_tracking_phrasings(self):
        for text in ("ขอติดตามพัสดุค่ะ", "พัสดุอยู่ไหนคะ", "ของจะถึงเมื่อไหร่คะ",
                     "เช็คสถานะได้ไหมคะ", "ของยังไม่ถึงเลยค่ะ"):
            self.assertEqual(MODULE._retail_journey(text), "track_order", text)

    def test_return_phrasings(self):
        for text in ("ขอคืนสินค้าค่ะ", "อยากส่งคืนค่ะ", "ขอคืนเงินค่ะ", "ขอเปลี่ยนสินค้าค่ะ"):
            self.assertEqual(MODULE._retail_journey(text), "return_request", text)

    def test_tracking_wins_over_rescheduling_when_both_could_match(self):
        # "when will it arrive" is a question about the delivery, not a request to move it.
        # Ordering these the other way made every tracking question look like a reschedule.
        self.assertEqual(MODULE._retail_journey("พัสดุจะถึงวันไหนคะ"), "track_order")

    def test_unrelated_speech_matches_no_journey(self):
        for text in ("สวัสดีค่ะ", "ตกงานเลยยังไม่มีเงินจ่ายค่ะ", "ขอคุยกับเจ้าหน้าที่ค่ะ"):
            self.assertIsNone(MODULE._retail_journey(text), text)

    def test_return_reasons(self):
        cases = {
            "สินค้าเสียหายค่ะ": "damaged",
            "กล่องบุบมาค่ะ": "damaged",
            "ได้ของผิดรุ่นค่ะ": "wrong_item",
            "ส่งผิดสีค่ะ": "wrong_item",
            "เปลี่ยนใจค่ะ": "changed_mind",
            "สั่งผิดค่ะ": "changed_mind",
        }
        for text, expected in cases.items():
            self.assertEqual(MODULE._retail_return_reason(text), expected, text)


class NothingElseTest(unittest.TestCase):
    def test_thai_ways_of_declining_more_help(self):
        for text in ("ไม่มีค่ะ", "ไม่มีแล้วค่ะ", "พอแล้วค่ะ", "เท่านี้ค่ะ", "ขอบคุณค่ะ"):
            self.assertTrue(MODULE._retail_nothing_else(text), text)

    def test_hardship_phrasing_is_not_treated_as_declining(self):
        # This is why the pattern is retail-local and not added to the shared NO_WORDS:
        # "ไม่มีเงิน" is a collections hardship signal, not a refusal of further help.
        for text in ("ไม่มีเงินค่ะ", "ไม่มีเงินจ่ายเลยค่ะ", "ตอนนี้ไม่มีเงินค่ะ"):
            self.assertFalse(MODULE._retail_nothing_else(text), text)


class RescheduleJourneyTest(unittest.TestCase):
    def test_reschedule_confirms_and_closes(self):
        state, results = walk(["ขอเลื่อนวันจัดส่งค่ะ", "วันที่ยี่สิบห้าค่ะ", "ใช่ค่ะ"])
        self.assertEqual(state["stage"], "closed")
        self.assertTrue(results[-1]["done"])
        self.assertEqual(results[-1]["outcomeType"], "delivery_rescheduled")
        self.assertNotEqual(state.get("deliveryDate", ""), "")

    def test_readback_is_about_delivery_not_a_callback(self):
        # _readback falls through to callback wording for unknown fields, which is what it
        # did for deliveryDate before it had a case of its own.
        _, results = walk(["ขอเลื่อนวันจัดส่งค่ะ", "วันที่ยี่สิบห้าค่ะ"])
        readback = results[-1]["message"]
        self.assertIn("จัดส่ง", readback)
        self.assertNotIn("โทรกลับ", readback)

    def test_rejecting_the_readback_asks_again(self):
        state, results = walk(["ขอเลื่อนวันจัดส่งค่ะ", "วันที่ยี่สิบห้าค่ะ", "ไม่ใช่ค่ะ"])
        self.assertEqual(state["stage"], "deliveryDate")
        self.assertFalse(results[-1]["done"])

    def test_a_non_date_reply_re_asks_rather_than_guessing(self):
        state, results = walk(["ขอเลื่อนวันจัดส่งค่ะ", "อะไรนะคะ"])
        self.assertEqual(state["stage"], "deliveryDate")
        self.assertFalse(results[-1]["done"])


class TrackJourneyTest(unittest.TestCase):
    def test_tracking_states_the_scheduled_date_then_offers_more(self):
        state, results = walk(["ขอติดตามพัสดุค่ะ"])
        self.assertEqual(state["stage"], "offer_more")
        self.assertIn("15 สิงหาคม 2569", results[0]["message"])

    def test_declining_more_help_closes_the_call(self):
        state, results = walk(["ขอติดตามพัสดุค่ะ", "ไม่มีค่ะ"])
        self.assertEqual(state["stage"], "closed")
        self.assertEqual(results[-1]["outcomeType"], "order_status_given")

    def test_missing_delivery_date_does_not_speak_a_placeholder(self):
        attributes = {**ATTRIBUTES, "dueDate": "-"}
        _, results = walk(["ขอติดตามพัสดุค่ะ"], attributes)
        self.assertNotIn("-", results[0]["message"])

    def test_tracking_then_switching_journey_is_followed(self):
        state, _ = walk(["พัสดุถึงเมื่อไหร่คะ", "ขอเลื่อนวันส่งค่ะ"])
        self.assertEqual(state["stage"], "deliveryDate")
        self.assertEqual(state["journey"], "reschedule")


class ReturnJourneyTest(unittest.TestCase):
    def test_wrong_item_is_accepted_on_the_call(self):
        state, results = walk(["ขอคืนสินค้าค่ะ", "ได้ของผิดรุ่นค่ะ"])
        self.assertEqual(results[-1]["outcomeType"], "return_requested")
        self.assertEqual(state["returnReason"], "wrong_item")
        self.assertEqual(results[-1]["handoffRequired"], "false")

    def test_changed_mind_is_accepted_on_the_call(self):
        _, results = walk(["ขอคืนสินค้าค่ะ", "เปลี่ยนใจค่ะ"])
        self.assertEqual(results[-1]["outcomeType"], "return_requested")

    def test_damage_goes_to_a_person(self):
        # Photographs, courier liability and any goodwill decision are outside what this
        # assistant may promise, so damage must transfer rather than resolve.
        _, results = walk(["ขอคืนสินค้าค่ะ", "สินค้าเสียหายค่ะ"])
        self.assertEqual(results[-1]["outcomeType"], "return_inspection_referral")
        self.assertEqual(results[-1]["handoffRequired"], "true")

    def test_the_damage_outcome_is_registered_as_a_transfer(self):
        # An outcome missing from this set plays transfer wording and then hangs up.
        self.assertIn("return_inspection_referral", MODULE.HANDOFF_OUTCOMES)
        self.assertIn("return_inspection_referral", MODULE.HANDOFF_REASON_TH)

    def test_an_unmatched_reason_re_asks_with_the_options(self):
        state, results = walk(["ขอคืนสินค้าค่ะ", "ก็ไม่รู้สิคะ"])
        self.assertEqual(state["stage"], "return_reason")
        for option in MODULE.RETAIL_RETURN_CHOICES:
            self.assertIn(option, results[-1]["message"])


class SharedBehaviourTest(unittest.TestCase):
    def test_asking_for_a_person_transfers_from_any_retail_stage(self):
        state = MODULE._initial_state("retail")
        classified = {**UNCLASSIFIED, "intent": "human"}
        result = MODULE._handle_retail(state, "ขอคุยกับเจ้าหน้าที่ค่ะ", classified, ATTRIBUTES)
        self.assertEqual(result["outcomeType"], "human_transfer")
        self.assertEqual(result["handoffRequired"], "true")

    def test_declining_the_call_closes_politely(self):
        state = MODULE._initial_state("retail")
        classified = {**UNCLASSIFIED, "intent": "declined"}
        result = MODULE._handle_retail(state, "ไม่สนใจค่ะ", classified, ATTRIBUTES)
        self.assertEqual(result["outcomeType"], "declined")

    def test_retail_outcome_fields_reach_the_flow(self):
        # The attribute dict is the whole contract; a key the flow reads but the Lambda
        # never sends simply never arrives.
        _, results = walk(["ขอเลื่อนวันจัดส่งค่ะ", "วันที่ยี่สิบห้าค่ะ", "ใช่ค่ะ"])
        for key in ("deliveryDate", "outcomeType", "handoffRequired"):
            self.assertIn(key, results[-1], key)


class PacingTest(unittest.TestCase):
    def test_a_delivery_date_turn_is_tolerant(self):
        # Same reasoning as a payment date: Thai speakers pause before a numeral.
        state = MODULE._initial_state("retail")
        state["stage"] = "deliveryDate"
        tuning = MODULE._speech_tuning(state)
        self.assertEqual(tuning["eotThreshold"], "0.9")
        self.assertEqual(tuning["eotTimeoutMs"], "7000")

    def test_delivery_date_is_registered_as_dictated(self):
        self.assertIn("deliveryDate", MODULE.DICTATED_STAGES)
        self.assertIn("deliveryDate", MODULE.DICTATED_FIELDS)


class FastPathTest(unittest.TestCase):
    """The deterministic path is why a turn costs ~216 ms instead of ~1000 ms.

    If these regress, retail still works but the demo gets several times slower on exactly
    the turns that currently feel instant.
    """

    def test_recognised_input_needs_no_model(self):
        cases = [
            ("choose_journey", "ขอเลื่อนวันจัดส่งค่ะ"),
            ("choose_journey", "ขอติดตามพัสดุค่ะ"),
            ("choose_journey", "ขอคืนสินค้าค่ะ"),
            ("deliveryDate", "วันที่ยี่สิบห้าค่ะ"),
            ("return_reason", "สินค้าเสียหายค่ะ"),
            ("offer_more", "ไม่มีค่ะ"),
            ("offer_more", "ขอเลื่อนวันจัดส่งค่ะ"),
        ]
        for stage, utterance in cases:
            state = MODULE._initial_state("retail")
            state["stage"] = stage
            self.assertFalse(MODULE._needs_model("retail", state, utterance),
                             f"{stage}: {utterance}")

    def test_genuinely_open_speech_still_reaches_the_model(self):
        state = MODULE._initial_state("retail")
        state["stage"] = "choose_journey"
        self.assertTrue(MODULE._needs_model("retail", state, "คือว่าเมื่อวานมีปัญหานิดหน่อยค่ะ"))


class WiringTest(unittest.TestCase):
    def test_flow_has_a_retail_branch(self):
        flow = json.loads((ROOT / "iac" / "mantle-flow.json").read_text())
        identifiers = {a["Identifier"] for a in flow["Actions"]}
        self.assertIn("intro-retail", identifiers)
        self.assertIn("input-retail", identifiers)
        routed = [
            condition["NextAction"]
            for action in flow["Actions"]
            if action.get("Type") == "Compare"
            and action["Parameters"].get("ComparisonValue") == "$.Attributes.scenario"
            for condition in action["Transitions"]["Conditions"]
            if condition["Condition"]["Operands"] == ["retail"]
        ]
        self.assertEqual(routed, ["intro-retail"])

    def test_retail_first_turn_offers_the_three_journeys(self):
        flow = json.loads((ROOT / "iac" / "mantle-flow.json").read_text())
        text = next(a["Parameters"]["Text"] for a in flow["Actions"]
                    if a["Identifier"] == "input-retail")
        for option in MODULE.RETAIL_JOURNEY_CHOICES:
            self.assertIn(option, text)

    def test_spoken_retail_text_has_no_arabic_digits(self):
        flow = json.loads((ROOT / "iac" / "mantle-flow.json").read_text())
        for identifier in ("intro-retail", "input-retail"):
            text = next(a["Parameters"]["Text"] for a in flow["Actions"]
                        if a["Identifier"] == identifier)
            self.assertFalse(any(character.isdigit() for character in text), identifier)

    def test_brief_and_initial_state_exist(self):
        source = (ROOT / "lambda" / "session_context.py").read_text()
        self.assertIn('"retail": (', source)
        self.assertIn('"scenario": "retail"', source)

    def test_trigger_accepts_retail(self):
        self.assertIn('"retail"', (ROOT / "lambda" / "index.py").read_text())

    def test_retail_order_facts_are_in_the_future(self):
        # A delivery you can still reschedule has not happened yet, which is the opposite
        # of the collections due date the generator was modelled on.
        import datetime

        spec = importlib.util.spec_from_file_location("ix", ROOT / "lambda" / "index.py")
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001 - index.py needs deploy-time env; skip if absent
            self.skipTest("index.py requires deployment environment variables")
        today = datetime.date(2026, 5, 10)
        facts = module._dynamic_retail_facts(today)
        self.assertIn("dueDate", facts)
        self.assertIn("amount", facts)


class SignalScopeTest(unittest.TestCase):
    """The shared signal patterns were written for collections and do not all transfer.

    "ขอเลื่อน" sits in the hardship pattern because on a collections call it means "I cannot
    pay yet". In retail it is simply "please move my delivery" -- the ordinary request. Before
    SIGNAL_SCOPE, every retail reschedule was detected as hardship and routed to a licensed
    representative before the retail handler saw the turn. Unit tests missed it because they
    call _handle_retail directly; only a call through the real handler exposed it.
    """

    def test_rescheduling_language_is_still_detected_as_hardship(self):
        # The pattern itself is not wrong for collections, so it is deliberately unchanged.
        self.assertEqual(MODULE._detect_signal("ขอเลื่อนวันจัดส่งค่ะ"), "hardship")

    def test_hardship_does_not_apply_to_retail(self):
        self.assertFalse(MODULE._signal_applies("retail", "hardship"))

    def test_hardship_still_applies_to_the_fsi_scenarios(self):
        for scenario in ("bank", "insurance", "broker"):
            self.assertTrue(MODULE._signal_applies(scenario, "hardship"), scenario)

    def test_protective_signals_still_apply_to_retail(self):
        # A distressed or angry retail caller must be handled the same as any other.
        for signal in ("vulnerability", "complaint", "do_not_contact"):
            self.assertTrue(MODULE._signal_applies("retail", signal), signal)

    def test_retail_intents_match_the_retail_signal_scope(self):
        # The classifier is offered exactly what the engine will accept; otherwise it can
        # return hardship, have it rejected as unsupported, and waste the second model call.
        self.assertNotIn("hardship", MODULE.ALLOWED_INTENTS["retail"])
        self.assertTrue(MODULE.SIGNAL_SCOPE["retail"] <= MODULE.ALLOWED_INTENTS["retail"])

    def test_reschedule_survives_the_full_handler(self):
        # The regression test for the actual bug: through handler(), not _handle_retail.
        event = {
            "sessionState": {
                "sessionAttributes": {**ATTRIBUTES, "mantleState": "{}"},
                "intent": {"name": "FallbackIntent"},
            },
            "inputTranscript": "ขอเลื่อนวันจัดส่งค่ะ",
        }
        attributes = MODULE.handler(event, None)["sessionState"]["sessionAttributes"]
        self.assertEqual(attributes["done"], "false")
        self.assertNotEqual(attributes["outcomeType"], "licensed_rep_referral")
        self.assertEqual(json.loads(attributes["mantleState"])["stage"], "deliveryDate")


if __name__ == "__main__":
    unittest.main()
