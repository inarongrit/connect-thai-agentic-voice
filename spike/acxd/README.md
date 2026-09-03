# Agentic CX Designer spike

Findings from evaluating [agentic CX designer](https://aws.amazon.com/about-aws/whats-new/2026/09/agentic-cx-designer/)
(GA 2026-09-02) against this project's Thai bank-collections journey.

## What was verified on this instance

| Question | Answer | How it was established |
| --- | --- | --- |
| Is the Agentic CX flow block available here? | **Yes** | `ConnectParticipantWithAgenticCX` returns `Invalid Action property value` (a parameter error) while 15 other candidate names return `Invalid Action type`. Probed with throwaway flows that were deleted immediately. |
| Is this an Amazon Connect Customer instance? | **Yes** | Cost Explorer shows usage type `USW2-ai-end-customer-mins` at exactly $0.038/min, the documented Connect Customer voice service rate. The block is documented as Connect Customer only. |
| Is the region supported? | **Yes** | `us-west-2` (US West Oregon) is on the GA region list. |
| Can the block's parameter schema be derived by probing? | **No** | The validator returns only a generic `Invalid Action property value` with no property names, and rejects placeholder ids. Real workspace, application and alias ids are required. |
| Is ACXD manageable through boto3 or the AWS CLI? | **No** | No `acxd` service exists in botocore 1.43.38. The ACXD SDK is a separate REST API authenticated by an API key an Account Admin generates. Connect's `CreateWorkspace` takes `Theme`/`Title` — that is the agent workspace, not ACXD. |

## Blocked: the one manual step

Creating an ACXD application cannot be automated from here. Someone with console access
must either:

1. Open the Amazon Connect Customer console → **Agentic CX designer** → create a
   workspace and application, then note the workspace, application and alias ids; or
2. Ask an Account Admin to generate an **ACXD SDK API key**, after which the application
   can be created as code and kept in this repository.

### Getting an ACXD SDK API key

Per the [SDK getting-started guide](https://docs.aws.amazon.com/connect/latest/devguide/acxd-getting-started.html):

**Prerequisites** — an Agentic CX Designer workspace, and Administrator access to it
(Admin Hub). The workspace must exist first, so the console step cannot be skipped
entirely.

1. **Create a programmatic user.** Admin Hub → Programmatic Users → *Create Programmatic
   User*. Only account administrators can do this. Assign permissions through
   `roleConfig`: either `accountRole: administrator` for every workspace, or
   `workspaceRoles` scoped per workspace using a pre-defined role (administrator,
   developer, content manager, read-only) or a custom role.
2. **Generate the key.** Select the user → *Generate API Key*. It is shown **once**;
   copy it immediately. Format `acxd_live_<prefix>.<secret>`. Maximum two keys per user.
3. **Install the SDK.** `npm install amazon-connect-acxd-sdk` — the SDK is
   JavaScript/TypeScript, not Python.

The key carries no permissions of its own; they resolve at request time from the
programmatic user's role, and role changes take effect immediately. So prefer a
workspace-scoped `developer` role over `accountRole: administrator` for this spike.

**Never commit the key.** It is a long-lived static credential, and `tools/publish_gate.py`
now blocks the `acxd_live_`/`acxd_test_` format so it cannot reach the repository. Keep it
in the environment or Secrets Manager.

Option 2 is strongly preferred. The whole value of this project is that behaviour is
reproducible and test-covered; a console-only application would be clickops and would
lose that property.

## Artifacts

### `collections_rules.json`

The written-down process, which the blog correctly identifies as the part that takes
longest. It records, for the bank scenario:

- which rules must stay **deterministic** (identity, money, dates, protective signals)
  and which may be **agentic** (understanding, tone, grounded answers);
- the Thai utterance corpora that must be rejected and accepted;
- the approval language that is forbidden, because only a human may approve relief;
- the handoff contract and the licensed boundary for insurance and brokerage.

Every constant in it is asserted against `lambda/mantle_dialogue.py` by
`tests/test_acxd_spike.py`, so it states what the engine does rather than what a rebuild
should aspire to.

### `compare_slot_capture.py`

Runs the corpora against a target and reports pass/fail per rule.

```bash
# control: the deterministic Lambda engine
python3 spike/acxd/compare_slot_capture.py

# once an ACXD application exists
python3 spike/acxd/compare_slot_capture.py --target acxd \
    --workspace-id W --application-id A --alias-id AL
```

The control currently scores **78/78**. The `acxd` target reports `UNAVAILABLE` and exits
`2` rather than printing a fabricated score.

## Why the control matters

An ACXD score in isolation would be misleading. A Thai date validator that rejects
`32 ธันวาคม` is only interesting if it *also* accepts `วันที่ 29 กุมภาพันธ์`,
`14.30` and `2000 บาท`. The corpus deliberately contains 26 forms that must be rejected
and 31 that must be accepted, including the specific forms that were real bypasses here:
`32 มกรา`, `32 ม.ค.`, `สามสิบสองมกราคม`, `32/1` and `1/13`.

## What to measure once an application exists

In priority order, because these are the answers that decide whether to adopt it for Thai:

1. **Thai slot capture** — run the harness. Our own knowledge-base work measured Thai
   retrieval in a narrow band (on-topic 0.518–0.620 versus off-topic 0.409–0.446). If
   ACXD's semantic routing and guardrails are tuned on English, that margin may not hold.
2. **Guardrail behaviour in Thai** — whether the approval-language prohibition and the
   protective signals (`do_not_contact`, `vulnerability`, `complaint`, `hardship`) are
   caught per turn as documented.
3. **Live Sync for the relief options** — three options spoken in Thai is exactly the
   "reading three options aloud is how you lose people" problem from the blog. It may
   also remove our keypad-verification workaround, which exists only because Contact Lens
   cannot redact Thai audio.
