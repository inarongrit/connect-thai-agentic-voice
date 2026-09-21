"""The Lex intents exist to make the console reflect the design, not to run the dialogue.

Understanding lives in lambda/mantle_dialogue.py: ALLOWED_INTENTS is a set of Python strings
matched by Thai regexes. These tests pin the two properties that keep the console mirror from
becoming a live behaviour change, because both failure modes break real calls silently.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
IAC = ROOT / "iac"

SPEC = importlib.util.spec_from_file_location(
    "mantle_dialogue", ROOT / "lambda" / "mantle_dialogue.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

GEN = importlib.util.spec_from_file_location(
    "gen_display_intents", ROOT / "tools" / "gen_display_intents.py"
)
GENERATOR = importlib.util.module_from_spec(GEN)
GEN.loader.exec_module(GENERATOR)


def locale():
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor(
        "!",
        lambda l, s, n: (
            l.construct_scalar(n) if isinstance(n, yaml.ScalarNode)
            else l.construct_sequence(n) if isinstance(n, yaml.SequenceNode)
            else l.construct_mapping(n)
        ),
    )
    template = yaml.load((IAC / "mantle-template.yaml").read_text(), Loader=Loader)
    return template["Resources"]["MantleLexBot"]["Properties"]["BotLocales"][0]


class ConsoleMirrorTest(unittest.TestCase):
    def test_every_generated_intent_mirrors_a_python_intent(self):
        every = set().union(*MODULE.ALLOWED_INTENTS.values())
        for python_name, lex_name, _, _ in GENERATOR.INTENTS:
            self.assertIn(python_name, every, f"{lex_name} mirrors nothing real")

    def test_the_bot_carries_them(self):
        names = {i["Name"] for i in locale()["Intents"]}
        for _, lex_name, _, _ in GENERATOR.INTENTS:
            self.assertIn(lex_name, names, lex_name)
        # The original pass-through pair must survive.
        self.assertIn("MantleDialogue", names)
        self.assertIn("FallbackIntent", names)

    def test_thai_slot_types_are_present_for_the_closed_choice_sets(self):
        declared = {s["Name"] for s in locale().get("SlotTypes", [])}
        for name, _, _ in [(n, d, v) for n, d, v in GENERATOR.SLOT_TYPES]:
            self.assertIn(name, declared, name)


class DialogueStaysInPythonTest(unittest.TestCase):
    def test_no_intent_declares_a_slot(self):
        # A required slot makes Lex elicit it, which takes the turn away from the Python
        # handler. Slot types are declared standalone precisely to avoid this.
        for intent in locale()["Intents"]:
            self.assertNotIn("Slots", intent, intent["Name"])

    def test_no_slot_type_is_referenced_by_an_intent(self):
        declared = {s["Name"] for s in locale().get("SlotTypes", [])}
        blob = json.dumps(locale()["Intents"], ensure_ascii=False)
        for name in declared:
            self.assertNotIn(f'"{name}"', blob, name)

    def test_every_intent_routes_to_the_lambda(self):
        for intent in locale()["Intents"]:
            hook = intent.get("FulfillmentCodeHook", {})
            self.assertTrue(hook.get("Enabled"), intent["Name"])
            self.assertTrue(hook.get("IsActive"), intent["Name"])


class FlowRoutingTest(unittest.TestCase):
    """The one way this change could have broken live calls.

    The Lex blocks previously matched only FallbackIntent and MantleDialogue. Any other
    intent name fell to NoMatchingCondition, which routes to error-message and drops the
    caller. Adding intents without adding routing would have broken every call where Lex
    matched one.
    """

    def expected_names(self):
        return {"FallbackIntent", "MantleDialogue"} | {
            lex for _, lex, _, _ in GENERATOR.INTENTS
        }

    def test_every_lex_block_routes_every_intent_name(self):
        for filename in ("mantle-flow.json", "mantle-inbound-flow.json"):
            flow = json.loads((IAC / filename).read_text())
            blocks = 0
            for action in flow["Actions"]:
                if action.get("Type") != "ConnectParticipantWithLexBot":
                    continue
                blocks += 1
                routed = {
                    c["Condition"]["Operands"][0]
                    for c in action["Transitions"]["Conditions"]
                }
                self.assertEqual(
                    routed, self.expected_names(),
                    f"{filename}:{action['Identifier']} would drop callers on "
                    f"{sorted(self.expected_names() - routed)}",
                )
            self.assertGreater(blocks, 0, filename)

    def test_all_conditions_share_one_destination(self):
        # Every intent must continue into the same Python-driven path.
        for filename in ("mantle-flow.json", "mantle-inbound-flow.json"):
            flow = json.loads((IAC / filename).read_text())
            for action in flow["Actions"]:
                if action.get("Type") != "ConnectParticipantWithLexBot":
                    continue
                targets = {
                    c["NextAction"] for c in action["Transitions"]["Conditions"]
                }
                self.assertEqual(len(targets), 1, action["Identifier"])


class GeneratorIsReproducibleTest(unittest.TestCase):
    def test_regenerating_does_not_change_the_committed_files(self):
        # The template block is machine-written between markers; drift means someone
        # hand-edited it and the next run would silently revert them.
        before = (IAC / "mantle-template.yaml").read_text()
        GENERATOR.patch_template()
        self.assertEqual((IAC / "mantle-template.yaml").read_text(), before)

    def test_sample_utterances_are_thai(self):
        for _, lex_name, description, utterances in GENERATOR.INTENTS:
            self.assertTrue(utterances, lex_name)
            for utterance in utterances:
                self.assertTrue(
                    any("\u0e00" <= ch <= "\u0e7f" for ch in utterance),
                    f"{lex_name}: {utterance!r}",
                )
            self.assertTrue(
                any("\u0e00" <= ch <= "\u0e7f" for ch in description), lex_name
            )


if __name__ == "__main__":
    unittest.main()
