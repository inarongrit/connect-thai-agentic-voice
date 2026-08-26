"""CDK application for the Thai agentic voice demo.

Why CDK, and why it starts as an adoption rather than a rewrite
--------------------------------------------------------------
The CloudFormation templates carry their Lambda source and their contact flows
inline, which forced `tools/sync_inline_lambda.py` and three parity tests into
existence purely to stop the copies drifting. CDK removes that duplication at the
root, so new work belongs here rather than in more inlined YAML.

Rewriting the stack in CDK constructs immediately would be the wrong first move: it
risks replacing live resources that a working demo depends on. Instead this app
adopts the existing template verbatim through `CfnInclude`, which produces the same
CloudFormation and lets `cdk diff` prove that nothing changes. Once adoption is
proven, resources can be converted to native constructs one at a time, each time
with an empty diff as the evidence.

What is and is not managed
--------------------------
Only `fsi-mantle-experiment` is a CloudFormation stack, so only it can be adopted.
`iac/template.yaml` describes the main path -- web bucket, CloudFront, HTTP API,
DynamoDB, the session-context and trigger Lambdas, the Q in Connect assistant -- but
those resources were built live and carry no `aws:cloudformation:stack-name` tag.
That template has therefore never created anything, and deploying it into this
account as-is would collide with the resources it describes. Bringing them under
management is a resource-import exercise, deliberately not attempted here.
"""

import aws_cdk as cdk

from stacks.mantle_stack import MantleStack

app = cdk.App()

MantleStack(
    app,
    "MantleStack",
    # Must match the deployed stack exactly, otherwise CDK would create a second
    # stack alongside it and every Connect resource would be duplicated.
    stack_name="fsi-mantle-experiment",
    env=cdk.Environment(region="us-west-2"),
    # No description override: CfnInclude carries the template's own Description, and
    # setting one here showed up as the only real difference in `cdk diff`.
)

app.synth()
