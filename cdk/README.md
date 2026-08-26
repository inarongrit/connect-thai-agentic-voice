# CDK app

Adopts the deployed `fsi-mantle-experiment` stack via `CfnInclude`, so the
synthesized template equals the deployed one and `cdk diff` reports no changes.
That empty diff is the safety evidence for letting CDK manage the stack.

```bash
cd cdk
cdk synth              # writes cdk.out/
cdk diff MantleStack   # note: the construct id, not the stack name
```

## Reading `cdk diff` on this stack

The diff always reports the contact flow `Content` as changed, and it is not drift.
CloudFormation's `GetTemplate` returns this stack's Thai text as runs of `?` -- 1215
of them, in both the `Original` and `Processed` stages -- so the deployed side of the
comparison has lost the characters. The resources themselves are correct: all three
live flows read back from the Connect API as proper Thai with no `?`.

Judge adoption by `tests/test_cdk_adoption.py`, which compares synthesis against
`iac/mantle-template.yaml` rather than against `GetTemplate`. Also note the CLI must
be at least 2.1128.1 to match the installed CDK library; older CLIs fail with a cloud
assembly schema mismatch.

Deploying is the same stack CDK now describes:

```bash
cdk deploy fsi-mantle-experiment \
  --parameters HandoffQueueArn=<queue arn> \
  --parameters HoldPromptArn=<prompt arn>
```

Only this stack is adopted. `iac/template.yaml` describes the main path, but those
resources were built live and belong to no stack, so there is nothing to adopt --
see the note in `app.py`.
