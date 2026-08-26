"""Adopts the deployed mantle stack without altering a single resource.

`CfnInclude` parses iac/mantle-template.yaml and re-emits it, so the synthesized
template is the deployed template. That is the point: `cdk diff` should report no
changes, which is the evidence that CDK can take over this stack safely.

The synthesizer and analytics settings exist to keep that diff honest. By default CDK
injects a `CDKMetadata` resource and a bootstrap-version parameter with a matching
rule, all of which would appear as additions and obscure whether the real resources
are untouched.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import cloudformation_include as cfn_inc
from constructs import Construct

# The templates live in iac/ and remain the source of truth until individual
# resources are converted to constructs. Resolved from this file rather than the
# working directory so `cdk synth` from cdk/ and the test runner from the repository
# root both find it.
TEMPLATE = str((Path(__file__).resolve().parents[2] / "iac" / "mantle-template.yaml"))


class MantleStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(
            scope,
            construct_id,
            analytics_reporting=False,
            synthesizer=cdk.DefaultStackSynthesizer(generate_bootstrap_version_rule=False),
            **kwargs,
        )

        # Deliberately no parameter values here. The template's parameters stay
        # parameters so the deployed values are reused on update, exactly as the
        # change-set deploys have been doing.
        self.included = cfn_inc.CfnInclude(
            self,
            "Mantle",
            template_file=TEMPLATE,
            preserve_logical_ids=True,
        )

    def resource(self, logical_id: str) -> cdk.CfnResource:
        """Access an adopted resource, for incremental conversion to constructs."""
        return self.included.get_resource(logical_id)
