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


def flow_messages():
    """Return {source: {identifier: spoken text}} for every contact flow definition."""
    found = {}
    for name in ("template.yaml", "mantle-template.yaml"):
        document = yaml.load((IAC / name).read_text(), Loader=CfnLoader)
        for key, resource in document["Resources"].items():
            if resource.get("Type") != "AWS::Connect::ContactFlow":
                continue
            doc = json.loads(resource["Properties"]["Content"])
            found[f"{name}:{key}"] = {
                action["Identifier"]: action["Parameters"].get("Text", "")
                for action in doc["Actions"]
                if action.get("Type") == "MessageParticipant"
            }
    doc = json.loads((IAC / "mantle-flow.json").read_text())
    found["mantle-flow.json"] = {
        action["Identifier"]: action["Parameters"].get("Text", "")
        for action in doc["Actions"]
        if action.get("Type") == "MessageParticipant"
    }
    return found


class MarketingScenarioDisclosureTests(unittest.TestCase):
    REQUIRED = {
        "ผู้ช่วยอัตโนมัติ": "automated-voice disclosure",
        "บันทึก": "recording notice",
        "สุดา": "assistant self-introduction",
    }

    def test_every_definition_declares_the_disclosures(self):
        for source, messages in flow_messages().items():
            for identifier in ("intro-insurance", "intro-broker"):
                text = messages.get(identifier, "")
                self.assertTrue(text, f"{source}:{identifier} missing")
                for token, description in self.REQUIRED.items():
                    self.assertIn(token, text, f"{source}:{identifier} lacks {description}")

    def test_insurance_states_it_is_not_a_licensed_agent(self):
        for source, messages in flow_messages().items():
            text = messages["intro-insurance"]
            self.assertIn("ไม่ใช่ตัวแทนที่ได้รับอนุญาต", text, source)
            self.assertIn("ผู้ได้รับอนุญาต", text, source)

    def test_securities_disclaims_investment_advice(self):
        for source, messages in flow_messages().items():
            text = messages["intro-broker"]
            self.assertIn("ไม่ใช่การให้คำแนะนำการลงทุน", text, source)
            self.assertIn("ผู้แนะนำการลงทุนที่ได้รับอนุญาต", text, source)

    def test_disclosures_use_female_register_and_no_digits(self):
        for source, messages in flow_messages().items():
            for identifier in ("intro-insurance", "intro-broker"):
                text = messages[identifier]
                self.assertTrue(text.rstrip().endswith(("ค่ะ", "คะ")), f"{source}:{identifier}")
                self.assertNotIn("ครับ", text, f"{source}:{identifier}")
                self.assertFalse(any(ch.isdigit() for ch in text), f"{source}:{identifier}")


class BankRemainsAServicingCallTests(unittest.TestCase):
    def test_bank_has_no_self_introduction(self):
        for source, messages in flow_messages().items():
            text = messages.get("intro-bank", "")
            self.assertTrue(text, source)
            self.assertNotIn("ผู้ช่วยอัตโนมัติ", text, source)
            self.assertNotIn("สุดา", text, source)


if __name__ == "__main__":
    unittest.main()
