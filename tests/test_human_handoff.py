"""Human handoff: the assistant must hand a person the context it gathered.

Before this feature every referral outcome recorded an attribute and hung up, so a
customer who asked for a human, disclosed a vulnerability, or made a complaint was
promised a callback by an automated voice and then dropped. These tests pin the two
halves of the fix: the dialogue must signal a handoff and carry a briefing, and the
flow must actually route to a queue with a fallback for when nobody can answer.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue_handoff", ROOT / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

FLOW = json.loads((ROOT / "iac" / "mantle-flow.json").read_text())
ACTIONS = {a["Identifier"]: a for a in FLOW["Actions"]}

# Blocks confirmed against Amazon Connect by creating and deleting a throwaway flow.
# CheckStaffing was rejected as an invalid action type, so it must never reappear:
# a flow that fails validation cannot deploy at all.
VERIFIED_BLOCKS = {
    "UpdateFlowLoggingBehavior", "UpdateContactRecordingBehavior",
    "UpdateContactTextToSpeechVoice", "UpdateContactData", "Compare",
    "MessageParticipant", "ConnectParticipantWithLexBot", "InvokeLambdaFunction",
    "UpdateContactAttributes", "UpdateContactTargetQueue", "CheckHoursOfOperation",
    "TransferContactToQueue", "DisconnectParticipant", "UpdateContactEventHooks",
}

WHISPER = json.loads((ROOT / "iac" / "mantle-agent-whisper-flow.json").read_text())
QUEUE = json.loads((ROOT / "iac" / "mantle-queue-flow.json").read_text())


def _texts(flow):
    """Every spoken string, including the ones inside a loop block's Messages."""
    spoken = []
    for action in flow["Actions"]:
        params = action.get("Parameters", {})
        if action["Type"] in ("MessageParticipant", "MessageParticipantIteratively"):
            if params.get("Text"):
                spoken.append(params["Text"])
            for message in params.get("Messages", []):
                if message.get("Text"):
                    spoken.append(message["Text"])
    return spoken


def _has_latin_words(text):
    """True when the text contains English words, ignoring JSONPath references.

    $.Attributes.handoffReason is Latin script but is substituted at runtime, so it
    is not something the customer or agent ever hears.
    """
    import re
    stripped = re.sub(r"\$\.[A-Za-z.]+", "", text)
    return bool(re.search(r"[A-Za-z]{3,}", stripped))


def _terminal(state, outcome):
    return MODULE._complete(dict(state), outcome, "ข้อความปิดการสนทนา")


class HandoffContractTests(unittest.TestCase):
    def test_referral_outcomes_request_a_person(self):
        for outcome in ("human_transfer", "vulnerability_referral", "complaint_logged",
                        "unresolved_needs_human", "licensed_rep_referral",
                        "affordability_review"):
            with self.subTest(outcome=outcome):
                self.assertEqual(_terminal({"scenario": "bank"}, outcome)["handoffRequired"],
                                 "true", f"{outcome} needs a live agent")

    def test_contact_ban_and_ordinary_endings_do_not_request_a_person(self):
        """A contact ban is honoured by ending the call, not by fetching a human.

        Routing do_not_contact to an agent would put a person on the line with
        someone who just asked never to be contacted again.
        """
        for outcome in ("do_not_contact", "callback", "declined", "payment_scheduled"):
            with self.subTest(outcome=outcome):
                result = _terminal({"scenario": "bank"}, outcome)
                self.assertEqual(result["handoffRequired"], "false")
                self.assertEqual(result["handoffSummary"], "")

    def test_handoff_outcomes_promise_a_transfer_and_never_a_callback(self):
        """The customer must not be told "we will call back" and then transferred.

        The flow decides whether an agent is reachable, so the dialogue promises a
        transfer only; the callback wording belongs to the fallback branch.
        """
        self.assertIn("โอนสาย", MODULE.HANDOFF_HOLD_TH)
        self.assertNotIn("ติดต่อกลับ", MODULE.HANDOFF_HOLD_TH)

    def test_summary_carries_the_discovery_context_to_the_agent(self):
        state = {"scenario": "broker", "topicInterest": "กองทุนรวม",
                 "customerGoal": "ออมเพื่อเกษียณ", "experienceLevel": "มือใหม่",
                 "primarySignal": "hardship"}
        summary = _terminal(state, "licensed_rep_referral")["handoffSummary"]
        for expected in ("หลักทรัพย์", "กองทุนรวม", "ออมเพื่อเกษียณ", "มือใหม่", "hardship"):
            self.assertIn(expected, summary)

    def test_summary_stays_short_enough_to_read_aloud(self):
        state = {"scenario": "insurance", "productInterest": "แผนคุ้มครองสุขภาพ" * 20,
                 "customerGoal": "ก" * 200, "primarySignal": "vulnerability"}
        self.assertLessEqual(len(_terminal(state, "vulnerability_referral")["handoffSummary"]), 260)

    def test_every_handoff_outcome_has_a_thai_reason(self):
        for outcome in MODULE.HANDOFF_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertTrue(MODULE.HANDOFF_REASON_TH.get(outcome, "").strip(),
                                f"{outcome} has no Thai reason for the agent")


class HandoffContractReachesTheFlowTests(unittest.TestCase):
    """The contract is the returned session attributes, not the internal result.

    The first version of this feature deployed with handoffRequired missing from the
    Lambda response: _complete produced it, the handler never copied it out, and the
    flow's comparison matched nothing, so no call would ever have been transferred.
    Tests that called _complete directly could not see that. These go through
    handler() so the attribute the flow actually reads is the thing asserted.
    """

    @staticmethod
    def _attributes(transcript, scenario="insurance"):
        event = {
            "inputTranscript": transcript,
            "sessionState": {
                "sessionAttributes": {
                    "scenario": scenario, "customerName": "สมชาย",
                    "amount": "15,500", "dueDate": "15 สิงหาคม 2569",
                    "mantleState": "{}",
                },
                "intent": {"name": "FallbackIntent", "state": "ReadyForFulfillment"},
            },
        }
        return MODULE.handler(event, None)["sessionState"]["sessionAttributes"]

    def test_asking_for_a_person_reaches_the_flow_as_a_handoff(self):
        attrs = self._attributes("ขอคุยกับเจ้าหน้าที่ครับ")
        self.assertEqual(attrs["outcomeType"], "human_transfer")
        self.assertEqual(attrs["handoffRequired"], "true")
        self.assertTrue(attrs["handoffSummary"].strip())
        self.assertIn("โอนสาย", attrs["nextPrompt"])
        self.assertNotIn("ติดต่อกลับ", attrs["nextPrompt"])

    def test_every_response_carries_the_handoff_keys(self):
        """The flow reads these on every turn, so they must always be present."""
        for transcript in ("ขอคุยกับเจ้าหน้าที่ครับ", "ไม่สนใจครับ", "สวัสดีครับ"):
            with self.subTest(transcript=transcript):
                attrs = self._attributes(transcript)
                for key in ("handoffRequired", "handoffReason", "handoffSummary"):
                    self.assertIn(key, attrs)
                self.assertIn(attrs["handoffRequired"], ("true", "false"))

    def test_declining_does_not_fetch_an_agent(self):
        self.assertEqual(self._attributes("ไม่สนใจครับ")["handoffRequired"], "false")


class HandoffFlowTests(unittest.TestCase):
    def test_outcome_is_recorded_before_the_handoff_decision(self):
        """A transfer ends the flow, so anything after it would never run."""
        self.assertEqual(ACTIONS["record-outcome"]["Transitions"]["NextAction"],
                         "handoff-branch")

    def test_only_flagged_contacts_are_transferred(self):
        branch = ACTIONS["handoff-branch"]
        self.assertEqual(branch["Parameters"]["ComparisonValue"],
                         "$.Lex.SessionAttributes.handoffRequired")
        self.assertEqual(branch["Transitions"]["NextAction"], "disconnect")
        self.assertEqual([c["NextAction"] for c in branch["Transitions"]["Conditions"]],
                         ["handoff-set-attributes"])

    def test_discovery_context_is_promoted_to_contact_attributes(self):
        """Only contact attributes reach the CCP; Lex session attributes do not."""
        attrs = ACTIONS["handoff-set-attributes"]["Parameters"]["Attributes"]
        self.assertEqual(attrs["handoffSummary"], "$.Lex.SessionAttributes.handoffSummary")
        self.assertEqual(attrs["handoffReason"], "$.Lex.SessionAttributes.handoffReason")

    def test_queue_is_set_before_the_hours_check(self):
        """CheckHoursOfOperation evaluates the working queue, not the instance."""
        self.assertEqual(ACTIONS["handoff-set-queue"]["Transitions"]["NextAction"],
                         "handoff-hours")
        self.assertEqual(ACTIONS["handoff-set-queue"]["Parameters"]["QueueId"],
                         "${HandoffQueueArn}")

    def test_no_path_can_drop_the_caller_silently(self):
        """Every failure in the handoff chain must reach the callback promise.

        This is the guarantee the feature rests on: a caller who was told an agent
        is coming must hear something, never a bare disconnect.
        """
        for ident in ("handoff-set-queue", "handoff-hours", "handoff-transfer"):
            with self.subTest(action=ident):
                errors = {e["NextAction"] for e in ACTIONS[ident]["Transitions"]["Errors"]}
                self.assertEqual(errors, {"handoff-fallback"},
                                 f"{ident} has an error path that is not the fallback")

    def test_transfer_covers_capacity_as_well_as_generic_failure(self):
        errors = {e["ErrorType"] for e in ACTIONS["handoff-transfer"]["Transitions"]["Errors"]}
        self.assertEqual(errors, {"NoMatchingError", "QueueAtCapacity"})

    def test_outside_hours_reaches_the_fallback(self):
        conditions = ACTIONS["handoff-hours"]["Transitions"]["Conditions"]
        outcomes = {c["Condition"]["Operands"][0]: c["NextAction"] for c in conditions}
        self.assertEqual(outcomes, {"True": "handoff-transfer", "False": "handoff-fallback"})

    def test_fallback_promises_a_callback_and_then_ends(self):
        fallback = ACTIONS["handoff-fallback"]
        self.assertIn("ติดต่อกลับ", fallback["Parameters"]["Text"])
        self.assertEqual(fallback["Transitions"]["NextAction"], "disconnect")

    def test_flow_uses_only_blocks_connect_accepts(self):
        """Guards against reintroducing a block type Connect rejects.

        CheckStaffing reads as though it should exist and does not; a flow containing
        it fails validation, so the stack cannot deploy.
        """
        used = {a["Type"] for a in FLOW["Actions"]}
        self.assertEqual(used - VERIFIED_BLOCKS, set())
        self.assertNotIn("CheckStaffing", used)

    def test_every_transition_points_at_a_real_block(self):
        known = set(ACTIONS)
        for action in FLOW["Actions"]:
            transitions = action["Transitions"]
            targets = [transitions["NextAction"]] if transitions.get("NextAction") else []
            targets += [e["NextAction"] for e in transitions.get("Errors", [])]
            targets += [c["NextAction"] for c in transitions.get("Conditions", [])]
            for target in targets:
                with self.subTest(action=action["Identifier"], target=target):
                    self.assertIn(target, known)


class ThaiHandoffAudioTests(unittest.TestCase):
    """Everything the customer and agent hear during a transfer must be Thai.

    The instance defaults were both wrong for this demo. The customer queue flow said
    "Thank you for calling. Your call is very important to us and will be answered in
    the order it was received." -- English, and inbound phrasing on a call the system
    placed itself. The agent whisper read $.Queue.Name aloud, so the agent heard the
    English words "BasicQueue" and nothing about why the call was escalated.
    """

    def test_the_contact_is_pointed_at_the_thai_flows_before_transferring(self):
        hooks = ACTIONS["handoff-set-hooks"]["Parameters"]["EventHooks"]
        self.assertEqual(hooks["CustomerQueue"], "${MantleQueueFlow.ContactFlowArn}")
        self.assertEqual(hooks["AgentWhisper"], "${MantleAgentWhisperFlow.ContactFlowArn}")
        self.assertEqual(ACTIONS["handoff-set-attributes"]["Transitions"]["NextAction"],
                         "handoff-set-hooks")
        self.assertEqual(ACTIONS["handoff-set-hooks"]["Transitions"]["NextAction"],
                         "handoff-set-queue")

    def test_no_english_is_spoken_in_either_flow(self):
        for label, flow in (("agent whisper", WHISPER), ("customer queue", QUEUE)):
            for text in _texts(flow):
                with self.subTest(flow=label, text=text[:40]):
                    self.assertFalse(_has_latin_words(text),
                                     f"{label} speaks English: {text}")

    def test_neither_flow_uses_inbound_phrasing(self):
        """This is an outbound demo: the customer did not call us."""
        for flow in (WHISPER, QUEUE):
            for text in _texts(flow):
                self.assertNotIn("Thank you for calling", text)
                self.assertNotIn("ขอบคุณที่โทรมา", text)

    def test_both_flows_speak_with_the_thai_voice(self):
        for label, flow in (("agent whisper", WHISPER), ("customer queue", QUEUE)):
            with self.subTest(flow=label):
                voices = [a["Parameters"]["TextToSpeechVoice"] for a in flow["Actions"]
                          if a["Type"] == "UpdateContactTextToSpeechVoice"]
                self.assertEqual(voices, ["SUDA"])

    def test_agent_hears_why_the_call_was_escalated(self):
        brief = " ".join(_texts(WHISPER))
        self.assertIn("$.Attributes.handoffReason", brief)
        self.assertIn("$.Attributes.handoffSummary", brief)

    def test_hold_uses_the_loop_block_not_wait(self):
        """Wait fails at runtime in a queue flow and must not come back.

        A Wait block here errored after 170ms with no error type, so every caller
        fell straight through to the no-agent message and was disconnected -- while
        the agent was in fact being connected, its whisper firing 1.7 seconds later.
        MessageParticipantIteratively is what the instance default and the AWS sample
        both use to hold a caller, and Connect interrupts it when an agent answers.
        """
        types = {a["Type"] for a in QUEUE["Actions"]}
        self.assertIn("MessageParticipantIteratively", types)
        self.assertNotIn("Wait", types)

    def test_hold_is_bounded_and_ends_with_the_callback_promise(self):
        actions = {a["Identifier"]: a for a in QUEUE["Actions"]}
        loop = actions["queue-hold-loop"]
        interrupt = int(loop["Parameters"]["InterruptFrequencySeconds"])
        # Long enough that a staffed agent connects first -- observed at about 8
        # seconds after enqueue -- so the bound only fires when nobody answers.
        self.assertGreaterEqual(interrupt, 45)
        self.assertLessEqual(interrupt, 180)
        self.assertEqual(loop["Transitions"]["Conditions"][0]["Condition"]["Operands"],
                         ["MessagesInterrupted"])
        self.assertIn("ติดต่อกลับ", actions["queue-no-agent"]["Parameters"]["Text"])
        self.assertEqual(actions["queue-end"]["Type"], "DisconnectParticipant")

    def test_hold_audio_is_not_committed_as_an_account_specific_arn(self):
        loop = {a["Identifier"]: a for a in QUEUE["Actions"]}["queue-hold-loop"]
        prompts = [m["PromptId"] for m in loop["Parameters"]["Messages"] if "PromptId" in m]
        self.assertEqual(prompts, ["${HoldPromptArn}"])


class HandoffDeploymentTests(unittest.TestCase):
    def test_queue_is_a_deploy_time_parameter(self):
        """No account-specific queue id may be committed to a public repository."""
        template = (ROOT / "iac" / "mantle-template.yaml").read_text()
        self.assertIn("HandoffQueueArn:", template)
        self.assertIn("^arn:aws:connect:[a-z0-9-]+:\\d{12}:instance/[a-f0-9-]+/queue/",
                      template)

    def test_every_inline_flow_matches_its_readable_source(self):
        """All three flows are inlined; all three must match their JSON files."""
        template = (ROOT / "iac" / "mantle-template.yaml").read_text()
        cases = (("  MantleAgentWhisperFlow:", "  MantleQueueFlow:",
                  "mantle-agent-whisper-flow.json"),
                 ("  MantleQueueFlow:", "  MantleContactFlow:", "mantle-queue-flow.json"),
                 ("  MantleContactFlow:", "  MantleInboundFlow:", "mantle-flow.json"),
                 ("  MantleInboundFlow:", "\nOutputs:", "mantle-inbound-flow.json"))
        for start, following, source in cases:
            with self.subTest(source=source):
                begin = template.index(start)
                prefix = "      Content: !Sub |\n"
                code = template.index(prefix, begin) + len(prefix)
                end = template.index(following, code)
                self.assertEqual(json.loads(template[code:end].strip()),
                                 json.loads((ROOT / "iac" / source).read_text()),
                                 "run python3 tools/sync_inline_lambda.py")


class HandoffParityTests(unittest.TestCase):
    """The inbound flow repeats the handoff chain, so the two must not diverge.

    A shared contact flow module would remove the duplication, but modules do not
    support queue transfer, so the wiring is repeated deliberately. This test is what
    makes that safe: the same blocks, in the same order, with the same fallbacks.
    """

    INBOUND = json.loads((ROOT / "iac" / "mantle-inbound-flow.json").read_text())

    @staticmethod
    def _chain(flow, prefix):
        return [a for a in flow["Actions"] if a["Identifier"].startswith(prefix)]

    def test_both_flows_use_the_same_blocks_in_the_same_order(self):
        outbound = [a["Type"] for a in self._chain(FLOW, "handoff-")]
        inbound = [a["Type"] for a in self._chain(self.INBOUND, "inbound-handoff-")]
        self.assertEqual(outbound, inbound)

    def test_both_target_the_same_queue_and_support_flows(self):
        def params(flow, prefix, suffix):
            action = {a["Identifier"]: a for a in flow["Actions"]}[prefix + suffix]
            return action["Parameters"]
        self.assertEqual(params(FLOW, "handoff-", "set-queue")["QueueId"],
                         params(self.INBOUND, "inbound-handoff-", "queue")["QueueId"])
        self.assertEqual(params(FLOW, "handoff-", "set-hooks")["EventHooks"],
                         params(self.INBOUND, "inbound-handoff-", "hooks")["EventHooks"])

    def test_inbound_failures_also_reach_a_spoken_fallback(self):
        actions = {a["Identifier"]: a for a in self.INBOUND["Actions"]}
        for ident in ("inbound-handoff-queue", "inbound-handoff-hours",
                      "inbound-handoff-transfer"):
            with self.subTest(action=ident):
                targets = {e["NextAction"] for e in actions[ident]["Transitions"]["Errors"]}
                self.assertEqual(targets, {"inbound-handoff-fallback"})
        self.assertIn("ติดต่อกลับ",
                      actions["inbound-handoff-fallback"]["Parameters"]["Text"])

if __name__ == "__main__":
    unittest.main()
