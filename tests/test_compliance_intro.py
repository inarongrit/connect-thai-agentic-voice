"""Guards the compliance disclosures in the insurance and securities introductions.

Scenarios 2 and 3 are marketing calls, so the opening must identify the assistant,
disclose that it is automated, state the purpose, give a recording notice and make
the licence position clear. Bank is a servicing call and is deliberately left
without a self-introduction.

The introductions live in the contact flow, so these tests read the flow JSON out
of the templates rather than exercising the dialogue Lambda.
"""

import json
import unittest
from pathlib import Path

import yaml

IAC = Path(__file__).parents[1] / "iac"


class CfnLoader(yaml.SafeLoader):
    pass


def _multi(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor("!", _multi)


# Support flows -- agent whisper, customer queue, hold -- are not customer-facing
# dialogue and carry no marketing disclosures. Only CONTACT_FLOW opens a conversation
# with a customer, so only CONTACT_FLOW must declare that the voice is automated, that
# the call may be recorded, and who is speaking.
DIALOGUE_FLOW_TYPE = "CONTACT_FLOW"


def flow_messages():
    """Return {source: {identifier: spoken text}} for every customer-facing flow."""
    found = {}
    for name in ("template.yaml", "mantle-template.yaml"):
        document = yaml.load((IAC / name).read_text(), Loader=CfnLoader)
        for key, resource in document["Resources"].items():
            if resource.get("Type") != "AWS::Connect::ContactFlow":
                continue
            if resource["Properties"].get("Type", DIALOGUE_FLOW_TYPE) != DIALOGUE_FLOW_TYPE:
                continue
            doc = json.loads(resource["Properties"]["Content"])
            found[f"{name}:{key}"] = {
                action["Identifier"]: action["Parameters"].get("Text", "")
                for action in doc["Actions"]
                if action.get("Type") == "MessageParticipant"
            }
    for name in ("mantle-flow.json", "mantle-inbound-flow.json"):
        doc = json.loads((IAC / name).read_text())
        found[name] = {
            action["Identifier"]: action["Parameters"].get("Text", "")
            for action in doc["Actions"]
            if action.get("Type") == "MessageParticipant"
        }
    return found


# Outbound and inbound owe different disclosures. An outbound call interrupts someone
# who did not ask to be called, so each marketing scenario opens with its own intro.
# An inbound caller dialled in, but has no idea the account details are fictional, so
# the inbound greeting must say that as well.
OUTBOUND_DIALOGUE = {
    "template.yaml:ContactFlow",
    "mantle-template.yaml:MantleContactFlow",
    "mantle-flow.json",
}
INBOUND_DIALOGUE = {
    "mantle-template.yaml:MantleInboundFlow",
    "mantle-inbound-flow.json",
}


class MarketingScenarioDisclosureTests(unittest.TestCase):
    REQUIRED = {
        "ผู้ช่วยอัตโนมัติ": "automated-voice disclosure",
        "บันทึก": "recording notice",
        "สุดา": "assistant self-introduction",
    }

    def test_every_customer_facing_flow_is_classified(self):
        """A new flow must be assigned its disclosure duty, not silently exempted.

        Scoping the earlier checks to CONTACT_FLOW let the whisper and queue flows
        past, correctly. This guard stops that becoming a loophole: any flow added
        later fails here until someone decides which disclosures it owes.
        """
        discovered = set(flow_messages())
        unclassified = discovered - OUTBOUND_DIALOGUE - INBOUND_DIALOGUE
        self.assertEqual(unclassified, set(),
                         "classify these flows as outbound or inbound")

    def test_every_definition_declares_the_disclosures(self):
        for source, messages in flow_messages().items():
            if source not in OUTBOUND_DIALOGUE:
                continue
            for identifier in ("intro-insurance", "intro-broker"):
                text = messages.get(identifier, "")
                self.assertTrue(text, f"{source}:{identifier} missing")
                for token, description in self.REQUIRED.items():
                    self.assertIn(token, text, f"{source}:{identifier} lacks {description}")

    def test_insurance_states_it_is_not_a_licensed_agent(self):
        for source, messages in flow_messages().items():
            if source not in OUTBOUND_DIALOGUE:
                continue
            text = messages["intro-insurance"]
            self.assertIn("ไม่ใช่ตัวแทนที่ได้รับอนุญาต", text, source)
            self.assertIn("ผู้ได้รับอนุญาต", text, source)

    def test_securities_disclaims_investment_advice(self):
        for source, messages in flow_messages().items():
            if source not in OUTBOUND_DIALOGUE:
                continue
            text = messages["intro-broker"]
            self.assertIn("ไม่ใช่การให้คำแนะนำการลงทุน", text, source)
            self.assertIn("ผู้แนะนำการลงทุนที่ได้รับอนุญาต", text, source)

    def test_disclosures_use_female_register_and_no_digits(self):
        for source, messages in flow_messages().items():
            if source not in OUTBOUND_DIALOGUE:
                continue
            for identifier in ("intro-insurance", "intro-broker"):
                text = messages[identifier]
                self.assertTrue(text.rstrip().endswith(("ค่ะ", "คะ")), f"{source}:{identifier}")
                self.assertNotIn("ครับ", text, f"{source}:{identifier}")
                self.assertFalse(any(ch.isdigit() for ch in text), f"{source}:{identifier}")


class BankRemainsAServicingCallTests(unittest.TestCase):
    def test_bank_has_no_self_introduction(self):
        for source, messages in flow_messages().items():
            if source not in OUTBOUND_DIALOGUE:
                continue
            text = messages.get("intro-bank", "")
            self.assertTrue(text, source)
            self.assertNotIn("ผู้ช่วยอัตโนมัติ", text, source)
            self.assertNotIn("สุดา", text, source)


class InboundDisclosureTests(unittest.TestCase):
    """An inbound caller must be told what they have reached before anything else.

    They dialled a number without knowing it leads to a demonstration, so alongside
    the automated-voice and recording notices the greeting has to say the account
    details are fictional. Otherwise a caller could reasonably believe the balance
    read back to them is their own.
    """

    REQUIRED = {
        "ผู้ช่วยอัตโนมัติ": "automated-voice disclosure",
        "บันทึก": "recording notice",
        "สุดา": "assistant self-introduction",
        "สมมติ": "fictional-data notice",
    }

    def _greetings(self):
        return {source: messages for source, messages in flow_messages().items()
                if source in INBOUND_DIALOGUE}

    def test_inbound_is_actually_present(self):
        """Guards against the tests passing because nothing was found."""
        self.assertEqual(set(self._greetings()), INBOUND_DIALOGUE)

    def test_greeting_carries_every_disclosure(self):
        for source, messages in self._greetings().items():
            text = messages.get("inbound-greeting", "")
            with self.subTest(source=source):
                self.assertTrue(text, f"{source} has no greeting")
                for token, description in self.REQUIRED.items():
                    self.assertIn(token, text, f"{source} lacks {description}")

    def test_greeting_precedes_the_menu(self):
        """Disclosures must come before anything that collects input."""
        doc = json.loads((IAC / "mantle-inbound-flow.json").read_text())
        order = [a["Identifier"] for a in doc["Actions"]]
        actions = {a["Identifier"]: a for a in doc["Actions"]}
        self.assertLess(order.index("inbound-greeting"), order.index("inbound-menu"))
        self.assertEqual(actions["inbound-greeting"]["Transitions"]["NextAction"],
                         "inbound-menu")

    def test_no_keypress_still_reaches_the_dialogue(self):
        """A dead end in front of an audience is worse than a default choice."""
        actions = {a["Identifier"]: a for a in
                   json.loads((IAC / "mantle-inbound-flow.json").read_text())["Actions"]}
        menu = actions["inbound-menu"]
        for error in menu["Transitions"]["Errors"]:
            self.assertEqual(error["NextAction"], "inbound-menu-fallback")
        self.assertEqual(actions["inbound-menu-fallback"]["Transitions"]["NextAction"],
                         "inbound-set-insurance")

    def test_inbound_reuses_the_outbound_dialogue(self):
        """One dialogue, one outcome record, one handoff path -- inbound cannot drift."""
        actions = {a["Identifier"]: a for a in
                   json.loads((IAC / "mantle-inbound-flow.json").read_text())["Actions"]}
        transfer = actions["inbound-to-dialogue"]
        self.assertEqual(transfer["Type"], "TransferToFlow")
        self.assertEqual(transfer["Parameters"]["ContactFlowId"],
                         "${MantleContactFlow.ContactFlowArn}")

    def test_every_scenario_can_be_reached(self):
        actions = {a["Identifier"]: a for a in
                   json.loads((IAC / "mantle-inbound-flow.json").read_text())["Actions"]}
        reached = {actions[f"inbound-set-{s}"]["Parameters"]["Attributes"]["scenario"]
                   for s in ("bank", "insurance", "broker")}
        self.assertEqual(reached, {"bank", "insurance", "broker"})

    def test_inbound_speaks_thai_only(self):
        import re
        for action in json.loads((IAC / "mantle-inbound-flow.json").read_text())["Actions"]:
            text = action.get("Parameters", {}).get("Text", "")
            if not text:
                continue
            with self.subTest(action=action["Identifier"]):
                self.assertFalse(re.search(r"[A-Za-z]{3,}", re.sub(r"\$\.[A-Za-z.]+", "", text)),
                                 f"{action['Identifier']} speaks English: {text}")


if __name__ == "__main__":
    unittest.main()
