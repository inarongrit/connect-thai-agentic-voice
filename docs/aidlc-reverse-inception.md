# AI-DLC applied in reverse

AI-DLC runs Inception → Construction → Operations. This project ran the other
way: it was built live against an AWS account, reached Operations, and only then
had its Inception artifacts reconstructed. This file records that honestly rather
than pretending the order was followed, and captures what the reversal cost.

Source of method: [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows).

## Why reverse, and what it cost

Working live first produced a working demo quickly. It also produced defects that
an Inception phase would have prevented outright:

| Defect found by retrofitting | Would Inception have prevented it? |
|---|---|
| CloudFront origin secret, account id, instance id and a claimed phone number sitting in the working tree | Yes — a security constraint stated up front makes `backups/` untracked from day one |
| `web/cost.js` referenced by both pages but absent from the deploy script and `post-deploy.sh`, so a clean-account deploy would 404 | Yes — "must redeploy in a clean account" as an acceptance criterion forces the asset list into the deploy script |
| Cost model assuming ~7 model turns per call when the measured figure is 1.60 | Partly — a requirement to measure rather than estimate would have caught it sooner |
| Validation checklist describing behaviour the code no longer had | Yes — traceability from requirement to test keeps the checklist honest |

The general lesson: **live-first development leaves account-specific state
everywhere, and no amount of care substitutes for a machine check.** Hence
`tools/publish_gate.py`.

## Reconstructed Inception

### Intent

Demonstrate that Amazon Connect can hold a useful, compliant, Thai-language voice
conversation for financial services — driven by an AI agent, with guardrails that
a regulated institution would recognise, and with evidence after every call.

### Requirements, as actually built

Functional:

1. Three outbound scenarios: loan collection, insurance discovery, brokerage
   education.
2. Two channels: WebRTC from the browser and a real PSTN call to a Thai number.
3. Thai speech in, Thai speech out, female register, no Arabic digits spoken.
4. Verbatim read-back and explicit confirmation before any value is committed.
5. Discovery before any human handoff; generic interest is never a terminal
   outcome.
6. Post-call evidence: transcript, Thai sentiment and rationale, an evaluation
   score, and an alert carrying the Contact ID.
7. Tester feedback captured per call.

Non-functional and conduct:

8. Market-conduct guardrails outrank the call objective: hardship, vulnerability,
   complaint and do-not-contact are detected deterministically and handled first.
9. Dynamic wording is grounded in approved facts or the customer's own words —
   never invented product detail, price, or market claim.
10. No advice, suitability judgement, or promise that requires a licence.
11. The tester-facing surface must not reveal model identity.
12. Reproducible from source in a clean AWS account.
13. No credential or account-specific identifier in the published repository.

### Units of work

Independent enough to have been built in parallel; in practice they were built
sequentially:

- **Voice path** — contact flow, Lex relay, agentic voice, channel handling.
- **Dialogue engine** — deterministic Thai matching, policy v2 signals,
  read-back, no-progress guardrail.
- **Grounded discovery** — insurance and brokerage discovery stages and approved
  fact sets.
- **Post-contact evidence** — analyzer, evaluation form, alerting, corpus.
- **Presentation surface** — landing page, slide deck, themes, cost estimate.
- **Delivery** — CloudFormation, post-deploy, custom domain, publish gate.

## Construction, as practised

The habits that held up and are worth keeping:

- Every behavioural claim is pinned by a test; the suite is the regression
  contract, currently 213 tests.
- Rates and volumes are **measured** from the account, not estimated, and their
  provenance is printed by the tool that uses them.
- CloudFormation carries Lambda source inline, and tests assert the inline copy
  matches the repository file, so the template cannot drift.
- Both web copies (`web/` and `iac/web/`) are asserted byte-identical, except the
  deliberately templated `qr.html`.

## Operations, as practised

- Every deploy takes a timestamped S3 rollback copy first.
- Deploys are verified by checksum equality between the local artifact and the
  live object, then by CloudFront invalidation completion, then in a browser.
- The live site is a private S3 origin behind CloudFront with a geo restriction
  and an origin secret, over TLS 1.3 only.

## Gaps this reversal did not close

See "Open gaps and their status" in `docs/implementation-notes.md`. The material one is Thai PII
redaction: Contact Lens cannot redact Thai, so transcripts are protected by
access control alone. That is acceptable for a demo whose only speech is the
owner's own test calls, and it is a blocker before real customer data.

## Extension registered

`.aidlc-rule-details/extensions/security/publish-readiness/` — always enforced,
blocking, verified by `tools/publish_gate.py` and `tests/test_publish_gate.py`.
