"""The CDK app must adopt the deployed stack without changing it.

CDK earns its place here by removing the inline duplication that forced
`tools/sync_inline_lambda.py` and three parity tests into existence. But the first
CDK commit must not alter a single live resource, so this suite asserts that the
synthesized template is the repository template -- same logical ids, same types, same
properties. Logical ids matter most: a changed id means CloudFormation replaces the
resource, which for a contact flow would mint a new id and break the web trigger.

A note on `cdk diff`, because the output is misleading here. CloudFormation's
GetTemplate returns this stack's Thai text as runs of "?" -- 1215 of them, in both
the Original and Processed stages -- so every diff reports the Thai as changed. The
deployed resources are fine: all three live contact flows read back from the Connect
API as proper Thai with no "?" at all. The mangling is in GetTemplate, not in what
customers hear, and it is why these tests compare against the repository template
rather than against GetTemplate output.
"""

import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
CDK_DIR = ROOT / "cdk"


class _CfnLoader(yaml.SafeLoader):
    pass


def _multi(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {f"Fn::{suffix}": loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {f"Fn::{suffix}": loader.construct_sequence(node)}
    return {f"Fn::{suffix}": loader.construct_mapping(node)}


_CfnLoader.add_multi_constructor("!", _multi)


def _normalise(node):
    """Collapse differences CloudFormation treats as identical.

    `!Ref x` and `Ref: x`, `!GetAtt a.b` and `Fn::GetAtt: [a, b]`, and a scalar
    `DependsOn` versus a single-item list are the same instruction written two ways.
    Comparing raw structures would report these as changes and hide real ones.
    """
    if isinstance(node, dict):
        if set(node) == {"Fn::Ref"}:
            return {"Ref": _normalise(node["Fn::Ref"])}
        if set(node) == {"Fn::GetAtt"} and isinstance(node["Fn::GetAtt"], str):
            return {"Fn::GetAtt": node["Fn::GetAtt"].split(".", 1)}
        result = {}
        for key, value in sorted(node.items()):
            if key == "DependsOn" and isinstance(value, str):
                value = [value]
            result[key] = _normalise(value)
        return result
    if isinstance(node, list):
        return [_normalise(item) for item in node]
    return node


def _synthesised():
    """Synthesise in-process so the test needs no CDK CLI and no network."""
    if str(CDK_DIR) not in sys.path:
        sys.path.insert(0, str(CDK_DIR))
    import aws_cdk as cdk
    from aws_cdk import assertions

    from stacks.mantle_stack import MantleStack

    app = cdk.App()
    stack = MantleStack(app, "MantleStack", stack_name="fsi-mantle-experiment",
                        env=cdk.Environment(account="123456789012", region="us-west-2"))
    return stack, json.loads(json.dumps(assertions.Template.from_stack(stack).to_json()))


def _repository_template():
    return yaml.load((ROOT / "iac" / "mantle-template.yaml").read_text(), Loader=_CfnLoader)


class CdkAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.stack, cls.synth = _synthesised()
        except ImportError as exc:  # pragma: no cover - environment without the library
            raise unittest.SkipTest(f"aws_cdk unavailable: {exc}")
        cls.repo = _repository_template()

    def test_stack_name_matches_the_deployed_stack(self):
        """A different name would create a second stack and duplicate every resource."""
        self.assertEqual(self.stack.stack_name, "fsi-mantle-experiment")

    def test_logical_ids_are_preserved(self):
        """Changed logical ids mean replacement; a new flow id breaks the web trigger."""
        self.assertEqual(sorted(self.synth["Resources"]), sorted(self.repo["Resources"]))

    def test_every_resource_is_unchanged(self):
        for logical_id, resource in self.repo["Resources"].items():
            with self.subTest(resource=logical_id):
                self.assertEqual(_normalise(resource),
                                 _normalise(self.synth["Resources"][logical_id]))

    def test_parameters_and_outputs_are_unchanged(self):
        self.assertEqual(_normalise(self.repo.get("Parameters")),
                         _normalise(self.synth.get("Parameters")))
        self.assertEqual(_normalise(self.repo.get("Outputs")),
                         _normalise(self.synth.get("Outputs")))

    def test_cdk_adds_nothing_of_its_own(self):
        """Metadata and bootstrap rules would make every future diff noisy.

        They are harmless to deploy but they obscure the only question that matters
        when adopting a live stack: is anything real changing?
        """
        self.assertNotIn("CDKMetadata", self.synth["Resources"])
        self.assertNotIn("Rules", self.synth)
        self.assertNotIn("BootstrapVersion", self.synth.get("Parameters", {}))

    def test_the_flow_still_carries_thai(self):
        """Guards the encoding path that GetTemplate loses.

        Synthesis goes through JSON, so this proves the Thai survives the CDK
        round-trip even though CloudFormation reports it back as "?".
        """
        content = self.synth["Resources"]["MantleContactFlow"]["Properties"]["Content"]
        rendered = json.dumps(content, ensure_ascii=False)
        self.assertTrue(any("\u0e00" <= ch <= "\u0e7f" for ch in rendered),
                        "Thai lost during synthesis")


class CdkProjectLayoutTests(unittest.TestCase):
    def test_templates_remain_the_source_of_truth_for_now(self):
        """Adoption reads iac/, so the templates must stay where the tests expect."""
        self.assertTrue((ROOT / "iac" / "mantle-template.yaml").is_file())

    def test_synth_output_is_not_publishable(self):
        """cdk.out is build output and would leak account ids once deployed."""
        self.assertIn("cdk.out", (ROOT / ".gitignore").read_text())


if __name__ == "__main__":
    unittest.main()
