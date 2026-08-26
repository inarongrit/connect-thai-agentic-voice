"""Regression tests for the cross-scenario negotiation policy (v2).

Covers the defect found on a live bank call: a customer who declared hardship and
asked to postpone was recorded as a callback or as a refusal to pay, and the agent
repeated the same payment-type question instead of changing course.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

UNKNOWN = {
    "intent": "unknown",
    "message": "",
    "rawValue": "",
    "confidence": 0.0,
    "model": "stub",
    "latencyMs": 0,
}


def event(scenario, transcript, previous=None):
    values = {
        "scenario": scenario,
        "customerName": "สมชาย",
        "amount": "15,500",
        "dueDate": "15 สิงหาคม 2569",
        "mantleState": "{}",
    }
    values.update(previous or {})
    return {
        "inputTranscript": transcript,
        "sessionState": {
            "sessionAttributes": values,
            "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
        },
    }


def run(scenario, transcript, previous=None):
    with patch.object(MODULE, "_classify", return_value=dict(UNKNOWN)):
        response = MODULE.handler(event(scenario, transcript, previous), None)
    return response["sessionState"]["sessionAttributes"]


def state_of(result):
    return json.loads(result["mantleState"])


class BankHardshipTests(unittest.TestCase):
    def _identified(self):
        first = run("bank", "ใช่ครับ ผมเอง")
        self.assertTrue(state_of(first)["identityConfirmed"])
        return first

    def test_hardship_opens_options_instead_of_repeating_payment_question(self):
        after = run("bank", "ตอนนี้ไม่มีเงินจ่าย ขอเลื่อนไปเดือนหน้าได้ไหมครับ", self._identified())
        self.assertEqual(after["done"], "false")
        self.assertEqual(after["primarySignal"], "hardship")
        self.assertEqual(state_of(after)["stage"], "hardship_options")
        self.assertIn("บางส่วน", after["nextPrompt"])

    def test_hardship_is_never_recorded_as_callback_or_refusal(self):
        after = run("bank", "ตอนนี้ไม่มีเงินจ่าย ขอเลื่อนไปเดือนหน้าได้ไหมครับ", self._identified())
        final = run("bank", "ขอเลื่อนออกไปก่อนครับ", after)
        self.assertEqual(final["done"], "true")
        self.assertEqual(final["outcomeType"], "payment_assistance_referral")
        self.assertNotIn(final["outcomeType"], {"callback", "declined"})
        self.assertIn("signal=hardship", final["outcomeDetail"])

    def test_partial_payment_collects_amount_and_date_verbatim(self):
        after = run("bank", "ตอนนี้ไม่มีเงินก้อนครับ", self._identified())
        offered = run("bank", "จ่ายได้ห้าพันบาทครับ", after)
        self.assertIn("ห้าพันบาท", offered["nextPrompt"])
        confirmed = run("bank", "ถูกต้องครับ", offered)
        self.assertEqual(confirmed["done"], "false")
        dated = run("bank", "วันที่ยี่สิบสิงหาคม", confirmed)
        final = run("bank", "ถูกต้องครับ", dated)
        self.assertEqual(final["outcomeType"], "partial_payment_agreement")
        self.assertEqual(final["paymentAmount"], "ห้าพันบาท")
        self.assertEqual(final["paymentDate"], "วันที่ยี่สิบสิงหาคม")

    def test_hardship_before_identity_never_discloses_the_debt(self):
        first = run("bank", "ตอนนี้ไม่มีเงินครับ")
        self.assertEqual(first["done"], "false")
        self.assertNotIn("15,500", first["nextPrompt"])
        self.assertFalse(state_of(first).get("identityConfirmed"))

    def test_assistance_referral_does_not_promise_approval(self):
        after = run("bank", "ตอนนี้ไม่มีเงินจ่ายครับ", self._identified())
        final = run("bank", "ขอเลื่อนออกไปก่อนครับ", after)
        for promise in ("อนุมัติ", "ได้รับสิทธิ", "รับรอง"):
            self.assertNotIn(promise, final["nextPrompt"])


class InsuranceAndSecuritiesTests(unittest.TestCase):
    def test_insurance_affordability_stops_the_sales_path(self):
        result = run("insurance", "เบี้ยแพงเกินไป ตอนนี้ไม่มีเงินจ่ายครับ")
        self.assertEqual(result["done"], "true")
        self.assertEqual(result["outcomeType"], "affordability_review")
        self.assertEqual(result["primarySignal"], "hardship")
        self.assertNotIn("นัด", result["nextPrompt"])

    def test_securities_loss_goes_to_licensed_representative(self):
        result = run("broker", "พอร์ตขาดทุนหนักมากครับ")
        self.assertEqual(result["done"], "true")
        self.assertEqual(result["outcomeType"], "licensed_rep_referral")
        # The consultant's job title contains แนะนำ, so assert on actual advice instead.
        for forbidden in ("แนะนำหุ้น", "ควรซื้อ", "ควรขาย", "ราคาเป้าหมาย", "กำไร"):
            self.assertNotIn(forbidden, result["nextPrompt"])
        self.assertIn("ผู้แนะนำการลงทุน", result["nextPrompt"])


class SharedSignalTests(unittest.TestCase):
    def test_do_not_contact_is_honoured_immediately(self):
        result = run("bank", "ห้ามโทรมาอีกนะครับ")
        self.assertEqual(result["done"], "true")
        self.assertEqual(result["outcomeType"], "do_not_contact")
        self.assertNotIn("15,500", result["nextPrompt"])

    def test_vulnerability_routes_to_a_specialist(self):
        result = run("insurance", "ตอนนี้ป่วยหนักนอนโรงพยาบาลอยู่ครับ")
        self.assertEqual(result["outcomeType"], "vulnerability_referral")

    def test_complaint_is_logged_for_every_scenario(self):
        for scenario in ("bank", "insurance", "broker"):
            result = run(scenario, "ไม่พอใจมาก โทรมาบ่อยเกินไป จะร้องเรียนครับ")
            self.assertEqual(result["outcomeType"], "complaint_logged", scenario)


class NoProgressGuardrailTests(unittest.TestCase):
    def test_repeated_identical_prompt_escalates_instead_of_looping(self):
        first = run("insurance", "อือ")
        second = run("insurance", "อือ", first)
        self.assertEqual(second["done"], "true")
        self.assertEqual(second["outcomeType"], "unresolved_needs_human")

    def test_progress_resets_the_repeat_counter(self):
        first = run("bank", "ใช่ครับ ผมเอง")
        self.assertEqual(state_of(first).get("noProgress", 0), 0)


class AsrRobustnessTests(unittest.TestCase):
    """Advanced ASR inserts spaces and can split vowel marks ("ต ัง ค์").

    These strings are copied verbatim from the Contact Lens transcripts of live
    calls, plus spaced variants that previously defeated contiguous matching.
    """

    REAL_TURNS = (
        ("ตอนนี้ ไม่มี เงิน จ ่าย ขอเลื่อนไปก่อนได้ไหมครับ", "hardship"),
        ("ตอนนี้ ไม่มี เงิน จ ่าย ขอเลื่อนไปเดือนหน้าได้ไหมครับ", "hardship"),
        ("ตอนนี้ ไม่มี ต ัง ค์ ครับ ขอไม่จ่ายได้ไหมครับ", "hardship"),
        ("อ่า คุยไม่รู้เรื่องนะครับ ขออนุญาตวางสายนะครับ", "complaint"),
    )
    SPACED = (
        ("ตอนนี้ ไม่มี เงิน ครับ", "hardship"),
        ("ผม ตก งาน ครับ", "hardship"),
        ("ขอ ผ่อน ผัน ได้ ไหม ครับ", "hardship"),
        ("ห้าม โทร มา อีก นะ ครับ", "do_not_contact"),
        ("ป่วย หนัก อยู่ ครับ", "vulnerability"),
    )

    def test_real_transcript_turns_are_detected(self):
        for text, expected in self.REAL_TURNS:
            self.assertEqual(MODULE._detect_signal(text), expected, text)

    def test_asr_spacing_does_not_hide_a_signal(self):
        for text, expected in self.SPACED:
            self.assertEqual(MODULE._detect_signal(text), expected, text)

    def test_ordinary_answers_are_not_flagged(self):
        for text in ("ใช่ครับ ผมเอง", "ขอแบ่งชำระครับ", "วันที่ยี่สิบสิงหาคม", "ถูกต้องครับ"):
            self.assertIsNone(MODULE._detect_signal(text), text)

    def test_spaced_hardship_reaches_assistance_referral(self):
        identified = run("bank", "ใช่ครับ ผมเอง")
        after = run("bank", "ตอนนี้ ไม่มี เงิน ครับ", identified)
        self.assertEqual(after["primarySignal"], "hardship")
        final = run("bank", "ขอ เลื่อน ออก ไป ก่อน ครับ", after)
        self.assertEqual(final["outcomeType"], "payment_assistance_referral")


class SpokenOptionPauseTests(unittest.TestCase):
    """SUDA does not pause on a plain space, so enumerated choices ran together.

    Every scenario that offers choices must separate them audibly.
    """

    def test_helper_inserts_a_pause_and_a_final_connector(self):
        self.assertEqual(MODULE._spoken_options(["ก", "ข", "ค"]), "ก, ข, หรือค")
        self.assertEqual(MODULE._spoken_options(["ก", "ข"]), "ก, หรือข")
        self.assertEqual(MODULE._spoken_options(["ก"]), "ก")
        self.assertEqual(MODULE._spoken_options([]), "")

    def test_bank_disclosure_separates_payment_options(self):
        result = run("bank", "ใช่ครับ ผมเอง")
        prompt = result["nextPrompt"]
        self.assertIn("ชำระเต็มจำนวน, ชำระบางส่วน, หรือแบ่งชำระ", prompt)
        self.assertNotIn("เต็มจำนวนชำระ", prompt)

    def test_bank_hardship_options_are_separated(self):
        after = run("bank", "ตอนนี้ไม่มีเงินครับ", run("bank", "ใช่ครับ ผมเอง"))
        self.assertIn("ชำระบางส่วนก่อน, หรือขอเลื่อนการชำระออกไป", after["nextPrompt"])

    def test_insurance_need_options_are_separated(self):
        result = run("insurance", "ยังไม่แน่ใจครับ")
        self.assertIn("สุขภาพ, ชีวิต, หรือการออม", result["nextPrompt"])

    def test_securities_action_options_are_separated(self):
        result = run("broker", "ยังไม่แน่ใจครับ")
        self.assertIn("สนใจรับรายละเอียดสัมมนา, หรือนัดคุยกับผู้แนะนำการลงทุน",
                      result["nextPrompt"])

    def test_prompts_have_no_space_before_the_pause(self):
        prompts = [
            run("bank", "ใช่ครับ ผมเอง")["nextPrompt"],
            run("insurance", "ยังไม่แน่ใจครับ")["nextPrompt"],
            run("broker", "ยังไม่แน่ใจครับ")["nextPrompt"],
        ]
        for prompt in prompts:
            self.assertNotIn(" ,", prompt)
            self.assertNotIn("  ", prompt)


class SpokenProsodyAuditTests(unittest.TestCase):
    """No spoken enumeration may run together without an audible pause.

    `หรือไม่` is a yes/no question particle rather than a choice, so it is exempt.
    """

    def test_no_spoken_string_enumerates_without_a_pause(self):
        import re
        lines = (Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py").read_text().splitlines()
        source = "\n".join(l for l in lines if not l.lstrip().startswith("#"))
        thai = re.compile(r"[\u0E00-\u0E7F]")
        offenders = []
        for match in re.finditer(r'"((?:[^"\\]|\\.){6,})"', source):
            spoken = match.group(1)
            if not thai.search(spoken) or "|" in spoken:
                continue
            if "หรือ" not in spoken or "," in spoken:
                continue
            if "หรือไม่" in spoken:
                continue
            offenders.append(spoken)
        self.assertEqual(offenders, [], f"missing pause before หรือ: {offenders}")

    def test_appointment_asks_use_the_approved_phrasing(self):
        source = (Path(__file__).parents[1] / "lambda" / "mantle_dialogue.py").read_text()
        self.assertNotIn("วันหรือเวลาใด", source)
        self.assertIn("วันใดและเวลาใด", source)


class PolicyFlagTests(unittest.TestCase):
    def test_legacy_behaviour_when_policy_disabled(self):
        with patch.object(MODULE, "POLICY_V2", False):
            first = run("bank", "ใช่ครับ ผมเอง")
            after = run("bank", "ตอนนี้ไม่มีเงินจ่าย ขอเลื่อนไปเดือนหน้าได้ไหมครับ", first)
        self.assertEqual(after["primarySignal"], "none")
        self.assertEqual(after["policyVersion"], "v1")
        self.assertNotEqual(state_of(after).get("stage"), "hardship_options")

    def test_policy_version_is_reported_when_enabled(self):
        result = run("bank", "ใช่ครับ ผมเอง")
        self.assertEqual(result["policyVersion"], "v2")


if __name__ == "__main__":
    unittest.main()
