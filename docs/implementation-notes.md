# FSI Outbound Demo — IaC Deployment

Deploys a Thai agentic-voice outbound demo to an existing Amazon Connect instance.
Customers scan a QR code, choose bank / insurance / brokerage, enter a Thai phone
number, and receive an AI-driven outbound call.

## Architecture

```text
CloudFront form → HTTP API → Lambda → StartOutboundVoiceContact
                                      ↓
Connect flow → SUDA Thai voice → Q in Connect session → session-context Lambda
                                                       ├─ select scenario SELF_SERVICE agent
                                                       └─ persist scenarioBrief + JSON callState
                                      ↓
Lex th_TH Advanced ASR → scenario agent → CONVERSATION or structured outcome tool
                                      ↓
BANK_OUTCOME / INSURANCE_OUTCOME / BROKERAGE_OUTCOME
                                      ↓
outcome Lambda → terminal session state + contact attributes → disconnect
```

Each scenario uses a dedicated prompt and SELF_SERVICE AI agent on global Claude
Haiku 4.5. The session Lambda selects the agent with `UpdateSession`; this avoids
cross-scenario leakage while retaining one assistant and one Lex bot.

## Package contents

| File | Purpose |
|------|---------|
| `template.yaml` | CloudFormation: assistant/KB, Lex, deterministic flow, both Lambdas, HTTP API, S3 + CloudFront |
| `post-deploy.sh` | Creates 3 prompts/agents, configures per-session agent IDs, Advanced ASR + Primary assisted NLU, uploads web UI |
| `ai-prompt-bank.json` | Bank collection prompt with `BANK_OUTCOME` |
| `ai-prompt-insurance.json` | Insurance qualification prompt with `INSURANCE_OUTCOME` |
| `ai-prompt-broker.json` | Brokerage outreach prompt with `BROKERAGE_OUTCOME` |
| `web/cost.js` | Shared post-call cost estimate for the WebRTC and PSTN pages |
| `web/index.html` | Customer page (`__API_ENDPOINT__` placeholder) |
| `web/qr.html` | Presenter QR page (`__DEMO_DOMAIN__` placeholder) |

## Conversation design

- Each scenario plays its full introduction in a non-listening `MessageParticipant`
  action, then opens Lex with one short question. This prevents background noise,
  speakerphone echo, or an early response from interrupting the introduction and
  becoming a partial ASR turn.
- **Bank:** requires an unambiguous identity confirmation before debt disclosure,
  collects one payment value per turn, and reads back payment type/date/amount
  before returning a payment commitment.
- **Insurance:** asks at most one coverage-needs question and reads back the
  licensed-agent appointment time before returning an appointment outcome.
- **Brokerage:** sends seminar details or books a licensed consultation; consultation
  timing is read back before booking, while stock recommendations return
  `advice_request` without giving advice.
- Unclear, contradictory, interrupted, or noise-masked speech is never guessed.
  The agent asks the customer to repeat slowly near the phone; after two unclear
  replies it offers a callback at a quieter or more convenient time.
- All scenarios persist an initial structured `callState` and replace it with a
  terminal state when an outcome tool returns control to the flow.
- Thai speech uses Advanced ASR, default voice activity detection, Primary
  assisted NLU, a six-second speech-start window, and a 0.7 confidence /
  1100 ms end-of-turn configuration. Default VAD is intentionally used because
  higher noise-tolerance modes can reject quieter mobile callers. Meaningful
  imperfect transcripts are processed normally; only unusable input is clarified.

## Prerequisites

1. Existing Amazon Connect instance with outbound calling and Thai agentic voice
   (`SUDA`) available.
2. Claimed outbound-capable caller ID on the instance.
3. AWS CLI v2 with administrative deployment permissions.
4. AWS Support approval for Thailand (+66) outbound calling: service **Connect
   (Number Management)**, category **Country allowlisting for outbound calls**.

The public form accepts Thai destinations only (`0xxxxxxxxx` or `+66...`). The
source caller ID may be a supported US number already claimed in Connect.

## Deploy

The template now exceeds CloudFormation's 51,200-byte inline limit, so deploys
and validation must go through S3 (`--s3-bucket`, or `--template-url` for
`validate-template`).

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name fsi-outbound-demo \
  --s3-bucket fsi-demo-deploy-<ACCOUNT>-<REGION> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2 \
  --parameter-overrides \
    ConnectInstanceArn=arn:aws:connect:us-west-2:<ACCOUNT>:instance/<INSTANCE_ID> \
    ConnectInstanceId=<INSTANCE_ID> \
    SourcePhoneNumber=+1XXXXXXXXXX

./post-deploy.sh fsi-outbound-demo us-west-2
```

The script prints the demo and QR URLs.

## Optional blue-orbit visual preview

The deployed page keeps the approved green theme by default. Append
`?theme=blue-orbit` to the demo URL to activate the midnight-navy, cobalt, cyan,
and ice-white preview for that request only:

```text
https://<distribution-id>.cloudfront.net/?theme=blue-orbit
```

Theme detection runs in the document head before CSS paint and sets
`data-theme="blue-orbit"`; it does not persist a preference or change scenario,
channel, A/B, form, call, or backend behavior. The treatment uses the existing
SVG globe and CSS effects rather than third-party artwork. To roll it back,
remove the small query-parameter script and the scoped
`html[data-theme="blue-orbit"]` override block; the default green rules require
no changes.

## Post-contact analytics and console evidence

Conversational analytics is enabled so each demo call produces a recording and
a Thai transcript that a presenter can open in the Connect console. This applies
to **all three scenarios on both dialogue engines**, because the block sits at
the top of every FSI flow. Sentiment and post-contact summaries are not
available in Thai; see the language constraint below.

Flows carrying a `contact-lens-analytics` block, inserted immediately after the
logging block:

| Flow | Purpose |
|------|---------|
| `FSI Outbound Demo Flow` | managed Q in Connect path (Option B, server-side only) |
| `FSI Mantle Experimental Flow` | experimental Bedrock path (ตัวเลือก A) |
| `FSI Inbound Test (CCP dial-out)` | inbound/CCP testing |

Applied configuration:

```json
{
  "RecordingBehavior": {
    "RecordedParticipants": ["Agent", "Customer"],
    "IVRRecordingBehavior": "Enabled"
  },
  "AnalyticsBehavior": {
    "Enabled": "True",
    "AnalyticsLanguage": "th-TH",
    "ChannelConfiguration": { "Voice": { "AnalyticsModes": ["PostContact", "AutomatedInteraction"] } }
  }
}
```

`IVRRecordingBehavior` is the setting that matters most here: these calls are
self-service and never reach an agent, so without it there is no audio to
analyze. `RecordedParticipants` must list both `Agent` and `Customer` or the API
rejects `AnalyticsBehavior`, even when no agent is involved.

### Why `AutomatedInteraction` is required

`PostContact` alone is **not enough** for this demo. It analyzes the
agent-customer recording, and these contacts have no agent leg
(`AgentInfo: null`). A first live test produced playable audio but no transcript,
sentiment, or summary. The recording was written to an `ivr/` prefix with
`ParticipantType: IVR`:

```text
CallRecordings/ivr/2026/08/20/<contactId>_<timestamp>_UTC.wav
```

`AutomatedInteraction` is the mode that analyzes that recording, so both modes
are now set. `PostContact` is retained so agent-assisted calls still work if a
transfer is added later.

Retroactively analyzing an already-completed contact with
`StartContactConversationalAnalyticsJob` was attempted and consistently returned
`TooManyRequestsException` in this account, so treat analysis as forward-looking:
place a new call after changing the configuration rather than expecting older
contacts to backfill.

Instance settings: `CONTACT_LENS` and `ENABLE_BOT_ANALYTICS_AND_TRANSCRIPTS`
were already on; `AUTOMATED_INTERACTION_LOG` was switched to `true`. Analytics
output lands in the existing KMS-encrypted recordings bucket, and
`CONTACT_EVALUATIONS` storage was associated at
`connect/<instance-alias>/ContactEvaluations` so evaluation forms can be added
next.

### Known constraint: Thai supports transcript only

Per the Connect language support table, Thai (`th_TH`) supports **post-call
analytics and real-time call analytics** but **not** the AI features layered on
top:

| Feature | Thai (`th_TH`) |
|---------|----------------|
| Post-call analytics (transcript) | supported |
| Real-time call analytics | supported |
| Sentiment analysis | not supported |
| Post-contact summaries | not supported |
| Redaction | not supported |
| Pattern match rules (categories) | not supported |
| Automated performance evaluations | not supported |

The flow block accepts `SentimentConfiguration` and `SummaryConfiguration` for
`th-TH` because static validation only checks format, not language capability.
Requesting unsupported features is the likely reason a Thai test call produced
audio but no analysis, so `AnalyticsBehavior` is deliberately limited to
transcript only.

Redaction is likewise unavailable, so Thai transcripts are stored
**unredacted**. Treat the recordings bucket as holding sensitive financial
conversation data, keep access restricted, apply a retention policy, and redact
downstream if transcripts leave this account.

Consequences for the analysis roadmap:

- **Contact Lens categories cannot detect Thai hardship phrases.** Pattern match
  rules are unavailable in Thai, so signal detection must happen in the dialogue
  layer (Lambda/prompt) and be written to contact attributes, not inferred by
  Contact Lens rules.
- **Automated evaluations cannot be generated in Thai.** Evaluation forms can
  still be created and completed **manually** in any language; only generative
  auto-fill is unavailable. A programmatic evaluator using
  `StartContactEvaluation` and `SubmitContactEvaluation` remains possible.
- Sentiment and summaries shown in the console for other languages will be
  absent for these Thai calls.


### Verified result: transcripts are produced, output path matters

Analysis output is written to the **bucket root**, not under the recordings
prefix:

```text
s3://amazon-connect-fb8777c56163/Analysis/Voice/ivr/<yyyy>/<mm>/<dd>/<contactId>_analysis_<timestamp>Z.json
```

Four live calls confirm what is required:

| Contact | Engine | Analytics config | Analysis produced |
|---------|--------|------------------|-------------------|
| `67b627e2` | experimental | `PostContact` only | **no** |
| `fc8978b9` | experimental | `+ AutomatedInteraction`, th-TH, sentiment/summary | yes |
| `1857f152` | experimental | `+ AutomatedInteraction`, th-TH transcript only | yes |
| `20407bfd` | managed | `+ AutomatedInteraction`, en-US | yes |

`AutomatedInteraction` is the deciding setting: the only call without analysis is
the one placed before it was added. Processing also takes noticeably longer than
recording availability, so allow several minutes after disconnect.

Thai transcription quality is good, with `SYSTEM` and `CUSTOMER` speaker labels
and `ConversationCharacteristics` (non-talk time, interruptions). Confirmed
empirically against the language table: Thai transcript entries carry
`Sentiment: None`, while the `en-US` control returned `NEUTRAL` / `NEGATIVE`.
`Categories.MatchedCategories` is empty, consistent with Thai not supporting
pattern match rules.

Retroactive analysis of an already-completed contact via
`StartContactConversationalAnalyticsJob` returned `TooManyRequestsException` on
every attempt, so configure analytics **before** the call rather than expecting a
backfill.

### Interim Thai sentiment in the console

Thai has no native Contact Lens sentiment, and Amazon Comprehend does not accept
Thai either (`DetectSentiment` allows only
`en|es|fr|de|it|pt|ar|hi|ja|ko|zh|zh-TW`). The interim approach computes
sentiment outside Contact Lens and writes it back as **contact attributes**,
which render on the console Contact details page and are searchable.

Pipeline, run after the analysis JSON appears:

1. Read the transcript from
   `s3://amazon-connect-fb8777c56163/Analysis/Voice/ivr/<date>/<contactId>_analysis_*.json`
2. Send the Thai turns to Bedrock and ask for sentiment, signal, recommended
   outcome, and a Thai rationale.
3. Optionally cross-check with `Translate th->en` then
   `Comprehend DetectSentiment` for a calibrated probability score.
4. Write results with `UpdateContactAttributes`.

Attributes written:

| Attribute | Example |
|-----------|---------|
| `sentimentOverall` | `NEGATIVE` |
| `sentimentScore` | `0.82` |
| `sentimentEngine` | `bedrock-thai-native` |
| `sentimentComprehendEn` | `NEGATIVE` |
| `primaryCustomerSignal` | `hardship_financial_difficulty` |
| `recommendedOutcome` | `temporary_deferral_request` |
| `analysisRationaleTh` | short Thai explanation |

Bedrock is the primary engine because translating first loses meaning on
colloquial Thai: `ขอไม่จ่ายได้ไหมครับ` became "Can't I pay?" and
`คุยไม่รู้เรื่อง` became "I don't know about it", which drops the frustration.
On the same call Comprehend scored `Neutral 0.36` while Bedrock scored `0.82`
negative. Bedrock also returns the hardship signal and recommended outcome in the
same call, which Contact Lens sentiment could never provide.

For a richer console surface, the same values can be submitted as an evaluation
(`CreateEvaluationForm`, then `StartContactEvaluation` and
`SubmitContactEvaluation` with `NumericValue` / `StringValue` answers). Storage is
already enabled. Note that a `ConnectUserArn` is required as the submitter, and
the AWS-managed Contact Lens sentiment panel itself stays empty for Thai.

### Deployed: automated Thai sentiment and signal analysis

The interim approach is automated. When Contact Lens writes an analysis file, an
S3 event triggers a Lambda that classifies the Thai transcript and publishes the
result to the console.

| Resource | Value |
|----------|-------|
| Lambda | `fsi-thai-post-contact-analyzer` (python3.12, 120s, 512MB) |
| Source | `lambda/thai_post_contact_analyzer.py` |
| Role | `fsi-thai-post-contact-analyzer-role` |
| Trigger | `s3:ObjectCreated:*` on `amazon-connect-fb8777c56163`, prefix `Analysis/Voice/`, suffix `.json` |
| Evaluation form | `Thai Self-Service Conversation Analysis`, id `ebbc97ce-a98e-4277-9e74-f00e8f53c6ed`, active version 1 |
| Form definition | `iac/evaluation/thai-analysis-form.json` |

What it does per contact:

1. Reads the transcript from the analysis JSON.
2. Classifies sentiment, `primarySignal`, `recommendedOutcome` and a Thai
   rationale with Bedrock. **Do not add a temperature**: these models reject the
   field with `This model doesn't support the temperature field`, which silently
   disabled classification for every contact until it was removed. Model
   self-reported confidence therefore varies slightly between runs; the
   deterministic cross-check is `sentimentComprehendNeg`.
3. Cross-checks sentiment via `Translate th->en` then `Comprehend DetectSentiment`.
4. Detects a no-progress loop deterministically by finding repeated identical
   agent turns, rather than trusting the model for it.
5. Compares `recommendedOutcome` with the recorded `fsiOutcome` to derive
   `signalHandled`. A call ended by the dialogue guardrail
   (`unresolved_needs_human`) can never score as fully handled: it is recorded as
   `Partially` with `noProgressLoop=true`, because the guardrail replaces the
   repeated prompt so the transcript contains no visible duplicate.
6. Writes contact attributes and submits an evaluation.

The evaluation form marks an unhandled hardship or vulnerability signal as an
**automatic fail**, so a mishandled call scores 0% and is filterable in the
console. `ContactInteractionType` is `AUTOMATED`, which is the correct target for
self-service contacts.

Verified on two real contacts:

| Contact | Recorded `fsiOutcome` | Detected signal | Recommended | `signalHandled` | Evaluation |
|---------|----------------------|-----------------|-------------|-----------------|------------|
| `1857f152` | `declined` | `hardship_financial_difficulty` | `payment_assistance_referral` | `No` | 0%, automatic fail |
| `fc8978b9` | (none) | `hardship_financial_difficulty` | `temporary_deferral_request` | `No` | 0%, automatic fail |

Both cases show the same defect: a customer asking to postpone or negotiate was
not routed to payment assistance. Runtime is about 3.5 seconds per contact.

Operational notes:

- The analyzer is idempotent: it skips a contact that already has a `SUBMITTED`
  evaluation, so redelivery does not create duplicates.
- Submission requires a `ConnectUserArn`; the demo uses the demo Connect user.
- SINGLESELECT answers must be submitted as the option **Text**, not its `RefId`.
- Numeric questions require at least one answer range with no gaps or overlaps.
- The bucket had no prior notification configuration, and the trigger was added
  by merging rather than replacing, so future notifications can coexist.
- Nothing here runs during a call; it is entirely post-contact.

### Consent

The demo owner accepted consent handling for demo purposes. Recorded as a
caution rather than a blocker: production use needs an approved recording
notice and consent capture before recording Thai collections calls. The default
outbound whisper flow previously announced "This call is not being recorded",
which became false once recording was enabled, so it now plays a Thai
service-quality recording notice instead.

### Demo walkthrough

1. Place a call from the demo page for any scenario.
2. Wait for post-contact processing after disconnect.
3. In the Connect console open **Analytics and optimization**, **Contact
   search**, then the contact ID.
4. Show the recording, Thai transcript, and the `fsiOutcome` contact
   attributes.

### Rollback

Original flow definitions are saved in
`backups/contact-lens-20260820T020457Z/`. To revert one flow:

```bash
aws connect update-contact-flow-content \
  --instance-id <INSTANCE_ID> \
  --contact-flow-id <FLOW_ID> \
  --content file://<backup>/<Flow_Name>.json \
  --region us-west-2
```

`template.yaml`, `mantle-template.yaml`, and `mantle-flow.json` were updated to
match, so redeploying from this repo preserves analytics. Note the deployed
`fsi-mantle-experiment` stack still holds the pre-change flow content, so update
it from this repo rather than from the previously deployed template.

## Consequences of a low evaluation score

A score is only useful if something happens. A low score now has two
consequences; neither of them contacts the customer.

| Consequence | Mechanism |
|-------------|-----------|
| Somebody is told | `MissedCustomerSignal` metric -> `fsi-thai-missed-customer-signal` alarm -> `fsi-thai-evaluation-alerts` SNS topic |
| The conversation is kept for improvement | analyzer emits `fsi.demo.analyzer` / `LowEvaluationScore` -> EventBridge rule `fsi-low-evaluation-score` -> log group `/aws/events/fsi-evaluation-failures`, 90 day retention |

Verified end to end by re-analysing a contact where hardship had been recorded as
`declined`: the analyzer returned `signalHandled: No, flaggedForReview: true`, the
corpus received the event, and the alarm moved to `ALARM` with
`1 datapoint [1.0] was greater than the threshold (0.0)`.

**Alert delivery.** `<alert-recipient@example.com>` is subscribed to
`fsi-thai-evaluation-alerts`. An email subscription stays in
`PendingConfirmation` until the recipient clicks the confirmation link, and SNS
delivers nothing before that. The topic policy explicitly allows
`cloudwatch.amazonaws.com` to publish so alarm actions do not depend on the
default policy.

To add another recipient:

```bash
aws sns subscribe --topic-arn arn:aws:sns:us-west-2:<ACCOUNT_ID>:fsi-thai-evaluation-alerts \
  --protocol email --notification-endpoint <you@example.com>
```

### Finding the contact behind an alert

A CloudWatch alarm fires on a **metric**, so it can never name the contact. The
alarm email says only that something happened. Two things follow from that:

- The **actionable** email is published by the analyzer, not the alarm. It names
  the contact id, links straight to the contact page, and states the detected
  signal, the recommended outcome, what was actually recorded, and the Thai
  rationale.
- The **alarm** now means something different: three or more missed signals within
  15 minutes, which points to a systemic policy problem rather than one case.

If you only have an alarm email, list the flagged contacts from the corpus:

```bash
aws logs filter-log-events --region us-west-2 \
  --log-group-name /aws/events/fsi-evaluation-failures \
  --start-time $(( ($(date +%s) - 3600) * 1000 )) \
  --query 'events[].message' --output json | python3 -c '
import sys, json
for message in json.load(sys.stdin):
    d = json.loads(message)["detail"]
    print(d["contactId"], d["scenario"], d["primarySignal"], "->", d["recordedOutcome"])'
```

Two traps in that command, both hit while writing it: pass `--region us-west-2`
because the CLI default region here is different and the log group appears not to
exist, and use `--output json` rather than `--output text`, because text flattens
the list onto a single tab-separated line and per-record parsing then silently
reports zero flagged contacts.

Or query the submitted evaluations for the contact and its answers with
`ListContactEvaluations` and `DescribeContactEvaluation`.

### Why this is not an Amazon Connect rule

`OnContactEvaluationSubmit` exists as a rule trigger, but this instance's rules API
rejected every useful condition:

```text
Unsupported ComparisonValue: $.ContactLens.ContactEvaluation.Form.Score
Unsupported ComparisonValue: $.ContactLens.ContactEvaluation.Question.QuestionRefId
Unsupported ComparisonValue: $.ContactLens.ContactEvaluation.Section.SectionRefId
```

Only the form-level "results available" condition was accepted, which fires on
every submission including the ones that scored 100%, so a conditional
notification was not possible. The consequence therefore lives in the analyzer,
where the score and the answer are already known. Two further quirks worth
recording: the rule `Name` must match `^[0-9a-zA-Z._-]*$`, and `Function` must be
`{"Version": "2022-11-25", "RuleFunction": {...}}`.

### Deliberately not automated

No task, case or callback is created for the customer. `signal_handled` comes from
a Bedrock classification, and a misclassification would create real work against a
real customer. Calibrate against human-scored samples before enabling
`CreateCaseAction` or `TaskAction`. The internal actions above are reversible and
carry no customer impact.

One trap found while building this: `MissedCustomerSignal` was first published with
a `Scenario` dimension while the alarm watched the undimensioned metric, so the
alarm could never have fired. The undimensioned metric now drives the alarm and
`MissedCustomerSignalByScenario` carries the breakdown.

## Operations and evidence integrity

**Monitoring.** A pinned Bedrock `temperature` once disabled classification for
every contact and nothing surfaced it; it was found only because a tester called.
Two alarms now cover that class of failure, plus a dead letter queue so a failed
S3 event is never lost:

| Resource | Purpose |
|----------|---------|
| `fsi-thai-analyzer-errors` | any analyzer invocation error |
| `fsi-thai-analyzer-classification-unavailable` | analyzer ran but Bedrock classification failed, so contacts were skipped silently |
| `fsi-thai-analyzer-dlq` | failed S3 events, retained 14 days |

**Retention.** The recordings bucket had no lifecycle policy, so unredacted Thai
financial audio and transcripts accumulated indefinitely. Both
`connect/<instance-alias>/` and `Analysis/` now expire after 90 days, with
incomplete multipart uploads aborted after 7.

**The blind A/B is now confounded.** Option A enforces policy in Python; Option B
is instructed through prompts. A tester rating A against B is comparing
**enforcement models**, not two dialogue engines, so "Option A scored better" would
be measuring the wrong variable. Feedback records therefore now carry
`policyVersion` (from `DIALOGUE_POLICY_VERSION`, currently `v2`) and `enforcement`
(`deterministic` for mantle, `prompt-instructed` for managed). Ratings taken before
today's policy work carry neither field and must not be pooled with later ones.

Two ways to remove the confound, neither applied yet: relabel the comparison
honestly as enforcement-versus-instruction, or port the deterministic guardrails to
the managed path so the difference is the model again.

**Infrastructure as code.** `analyzer-template.yaml` now covers the analyzer
Lambda, its role, the DLQ, the evaluation form, the log metric filter and both
alarms, and passes `validate-template`. One step cannot be included: the recordings
bucket is created and owned by Amazon Connect, so its event notification is not a
resource of this stack and must be added after deploy, on prefix `Analysis/Voice/`
and suffix `.json`.

**Deliberately not done.** Evaluations are still submitted as the demo Connect user
rather than a dedicated service identity. Creating one means creating a credentialed
Connect user, which is a worse trade for a demo than the inaccurate attribution.
Revisit before production.

## Lessons learned: Thai language gaps in Amazon Connect

What Thai (`th_TH`) supports today, verified against the language table and by
live calls on this instance, with the workaround used for each gap:

| Capability | Thai | Workaround in this demo |
|------------|------|-------------------------|
| Agentic voice (SUDA) | yes | not needed |
| Advanced ASR (Lex th-TH) | yes | not needed |
| Post-call transcript | yes | must add `AutomatedInteraction`; read output from the bucket root |
| Real-time analytics | yes | not used; this demo is post-contact only |
| Sentiment analysis | **no** | **implemented** - Bedrock classifies the Thai transcript; written to `sentimentOverall` / `sentimentScore` and to the evaluation form by `fsi-thai-post-contact-analyzer` |
| Post-contact summaries | **no** | **partial** - the analyzer writes a one-sentence Thai rationale (`analysisRationaleTh`); a longer summary can be added to the same Bedrock call |
| PII redaction | **no** | **not solved** - mitigated only by KMS, restricted access and retention. See below |
| Pattern match rules / categories | **no** | **implemented** - deterministic Thai regex signals in the dialogue layer (policy v2) plus `primaryCustomerSignal` on the contact |
| Automated performance evaluations | **no** | **implemented** - `StartContactEvaluation` + `SubmitContactEvaluation` with Bedrock-derived answers, including automatic fail |
| Amazon Comprehend sentiment | **no** | **implemented as cross-check** - `Translate th->en` then `DetectSentiment`, stored as `sentimentComprehendEn` |
| Native Contact Lens sentiment panel | **no** | cannot be injected; values surface under Contact attributes and Evaluations instead |
| Amazon Translate | yes | used for the Comprehend cross-check |
| Amazon Bedrock on Thai | yes | primary engine: sentiment plus classification in one call |

So of the seven Thai gaps, five are worked around, one is partially covered, and
one is genuinely unsolved.

### Remaining unsolved gap: Thai PII redaction

Contact Lens cannot redact Thai, so transcripts and analysis JSON are stored in
full. Current mitigations are access controls only:

- S3 objects encrypted with the Connect KMS key.
- Bucket access restricted; analyzer role limited to `s3:GetObject` plus
  `kms:Decrypt` on that one key.
- No transcript text is copied into the public GitHub feedback mirror.
- Contact attributes carry classifications and a short rationale, not raw
  transcript text.

Options if this needs solving, none implemented:

1. A Bedrock redaction pass that writes a masked copy of the transcript and
   restricts access to the original. Keeps Thai, but the redaction is model-based
   rather than a managed guarantee.
2. `Translate th->en` then `Comprehend DetectPiiEntities`. Detects PII in the
   English text only, so offsets do not map back to the Thai source reliably.
3. Keep raw transcripts out of scope entirely: retain audio plus derived
   classifications and delete the transcript after analysis.

Treat this as the open item before any real customer data goes through the
pipeline. It is acceptable for the demo because the only speech is the demo
owner's own test calls.

Practical lessons from building this:

1. **Static validation does not mean the feature works.** The flow block accepted
   `SentimentConfiguration` and `SummaryConfiguration` with `th-TH`; both are
   unsupported and simply produced nothing. Validate against the language table,
   not the API schema.
2. **`AutomatedInteraction` is mandatory for self-service voice.** `PostContact`
   alone analyses the agent-customer recording, and these contacts have no agent
   leg. The first test call produced audio and no transcript for this reason.
3. **Analysis output is not under the recordings prefix.** It is written to
   `s3://<bucket>/Analysis/Voice/ivr/...` at the bucket root. Looking only under
   the recordings prefix produces a false "analysis never ran" conclusion.
4. **Post-contact processing is slower than recording availability.** Allow
   several minutes; a few minutes of polling is not enough to declare failure.
5. **Retroactive analysis is not usable here.**
   `StartContactConversationalAnalyticsJob` returned `TooManyRequestsException`
   on every attempt, so analytics must be configured before the call.
6. **Translating before analysing loses meaning.** `ขอไม่จ่ายได้ไหมครับ` became
   "Can't I pay?" and `คุยไม่รู้เรื่อง` became "I don't know about it". Bedrock on
   raw Thai scored the same call `0.82` negative where Comprehend scored
   `Neutral 0.36`.
7. **Pin model temperature for anything used as evidence.** The same call scored
   `0.78` then `0.98` across runs until `temperature: 0` was set.
8. **Because Thai has no pattern rules, detection belongs in the dialogue layer.**
   This is a better design anyway: deterministic, testable and auditable.
9. **Measure conversation cost drivers; do not reason them out.** A first per-call
   estimate assumed ~7 model turns per call. CloudWatch showed **1.60** Luna turns
   and **0.44** Terra turns, because deterministic stages resolve without calling a
   model. That moved the dialogue share of a call from ~6% to ~0.3% and changed the
   conclusion: voice minutes are ~99% of cost, so call duration and channel are the
   only levers that matter.
10. **A cheap primary with an expensive fallback inverts intuition.** Terra is 28%
   of Option A's model turns but **58% of its dialogue cost**, because it prices at
   10x Luna. Reducing fallback frequency, not prompt size, is the dialogue-cost
   lever.
11. **`aws acm list-certificates` hides certificates by default.** The default
   response is filtered by key type, so an EC-keyed wildcard was absent from the
   first listing and a redundant certificate was requested. Pass `--includes
   keyTypes=...` before concluding a certificate does not exist.
12. **GPT-5.6 Luna/Terra have no AWS Price List API entry.** 2,022 Bedrock products
   across us-east-1 and us-west-2 contain no match, and Cost Explorer had no line
   item yet. Per-token prices come from the model cards instead; token *volumes*
   are still measurable from CloudWatch.
13. **Cost Explorer is unusable for attribution in a shared account, but usage
   types are not.** Account totals are dominated by unrelated workloads; grouping
   by `USAGE_TYPE` and dividing cost by quantity yields real per-minute and
   per-token rates. Allow up to 24 hours of lag.
14. **`openssl s_client -tls1_2` prints the requested protocol, not the negotiated
   one.** Grepping `Protocol` reported a TLS 1.2 success against a TLS 1.3-only
   distribution. The truth is in the exit status and the `alert protocol version`
   (alert 70) line.
15. **TLS 1.3 is a prerequisite for post-quantum key agreement, not the same
   thing.** CloudFront enables hybrid ML-KEM across all its TLS policies
   automatically, so `TLSv1.3_2025` buys protocol strictness rather than PQC
   itself, and certificate signatures remain classical ECDSA.
16. **A blind A/B test constrains what the UI may display.** Showing a per-option
   cost would let a tester infer which engine they heard, since Option A and B
   differ ~4x per call. The post-call estimate therefore shows one blended dialogue
   figure and says so; a test asserts the cost code never references the engine.
17. **Referencing a new asset is not shipping it.** `web/cost.js` was loaded by both
   pages but missing from `web/` and from `post-deploy.sh`, so a clean-account
   deploy would have served a 404 and silently lost the feature. The durable fix is
   generic: parse `<script src>` from every page and assert each asset exists, is
   shipped, and is uploaded.
18. **Know which copy the deploy script reads.** `post-deploy.sh` uploads from
   `web/`, making it the deployment source of truth, while `web/` is the working
   copy. `web/qr.html` therefore keeps its `__DEMO_DOMAIN__` placeholder and
   must not be byte-compared against `web/qr.html`.
19. **Route 53 will not place an alias A/AAAA record where a CNAME already
   exists.** The attempted A+AAAA alias failed with `InvalidChangeBatch`; the
   existing CNAME was already correct, so the right action was to inspect and leave
   it alone rather than force the record type.

## Single dialogue engine in the web UI

The tester-facing page now offers **one** dialogue engine. Option A (deterministic
Luna/Terra) is the default and the only choice; the A/B selector is gone.

- `brainMode` is a constant in the page (`BRAIN_MODE="mantle"`) and is sent on
  every WebRTC and PSTN request.
- The trigger Lambda now defaults to `mantle` when `brainMode` is omitted, and
  still **accepts `managed`**, so Option B remains callable server-side for
  internal comparison. Verified for both channels: `flow_id` is selected from
  `brainMode` and passed to `_start_webrtc` and `_start_pstn` alike, and the live
  trigger has `MANTLE_ENABLED=true` with a Mantle flow ID — without both, removing
  Option B from the UI would have broken every call.
- The channel selector now lists **WebRTC first** (default, recommended) and PSTN
  second.
- Removing a selector row also relieved the tight desktop layout: at 1440x700 the
  form ends at y=541 of 700 with no page scroll.

Consequences that were cleaned up rather than left stale:

- The tech-line badge `A / B DIALOGUE TEST` became `DETERMINISTIC DIALOGUE`.
- The live call panel no longer prints an option label.
- `feedback.js` no longer displays a `ชุดสนทนา` A/B row; the API still records
  `brainMode` and `enforcement` per submission, re-derived from contact attributes.
- The post-call estimate now uses the **measured** single-engine dialogue rate
  ($0.00103/call from 1.60 Luna plus 0.44 Terra turns) instead of the blended
  $0.0025 that existed only to protect the blind comparison. The card still never
  names the model.
- Slides 3 and 4 no longer describe a blind A/B experiment; they describe
  deterministic enforcement and per-policy cohort integrity instead.

`web/webrtc.bundle.js` still contains a minified `|| "managed"` fallback from the
older source. It is unreachable because the page always passes `brainMode`, and a
test pins that; rebuild with `npm run build:webrtc` if a call site ever stops
passing it.

## Open gaps and their status

Consolidated so nothing above has to be re-read to find what is unresolved.

| Gap | Status | Notes |
|-----|--------|-------|
| Thai PII redaction | **open** | Not solvable with Contact Lens; mitigated by KMS, access limits and retention only. Blocker before real customer data. |
| Contact Lens post-contact cost | **assumed, unmeasured** | No billed usage in the measured window. Estimator carries it at 40% of the Connect AI minute and labels it an assumption. |
| Serverless overhead per call | **assumed, unmeasured** | Shared account prevents attribution; carried as one $0.0020/call allowance. |
| Luna/Terra per-token price | **documented, not measurable** | Absent from the Price List API; taken from the model cards. Re-check once Cost Explorer emits a line item. |
| Option A vs B comparison | **retired from the UI** | Only Option A is offered to testers. Option B stays callable server-side; any future comparison still confounds model with enforcement approach. |
| PQC certificate signatures | **not available** | ACM issues classical ECDSA/RSA only; PQC covers key agreement, not authentication. |
| Native Contact Lens sentiment panel | **cannot be solved** | Values surface as contact attributes and evaluations instead. |
| Thai post-contact summaries | **partial** | One-sentence Thai rationale only; a longer summary would need another Bedrock call. |
| End-to-end spoken commit path | **unverified** | A spoken turn that drives `done=true` needs a human Thai speaker; no Thai Polly voice exists to automate it. |
| Generic company names, no PDPA opt-out line | **deliberate** | Acceptable for a demo; required before customer-facing use. |
| Feedback rate limiting | **not implemented** | A tester holding a valid `statusToken` can submit repeatedly. |

## Change summary: discovery, cost and custom domain

Delivered in one session, all validated by the 183-test suite:

**Scenarios 2 and 3 redesigned for conduct-safe discovery.** Generic interest no
longer terminates a brokerage call or jumps to booking; the agent discovers topic
and experience, or need and priority, grounded in enumerated approved facts plus
the customer's own words. One invitation, immediate acceptance of no, no urgency,
scarcity, FOMO, suitability judgement or invented product detail. Handoff context
travels in `outcomeDetail` so the licensed human does not restart discovery.

**Per-call cost model.** `tools/cost_per_call.py` derives rates from this
account's metered usage, prices Option A from the published model cards, states
its assumptions explicitly, and can include or exclude the assumed components.
`web/cost.js` shows the same model to the tester when a call ends, on both the
WebRTC and PSTN pages, charging exactly one channel-appropriate media line.

**Presentation and delivery.** Page 1 gained a persistent red/green/blue theme
selector via CSS `data-theme`; the globe was rolled back to its clean form; the
demo is served from a custom domain over TLS 1.3 only.

**Deployment hardening.** `tests/test_deployment_readiness.py` now proves a clean
account can replicate the solution: both templates validate, inline Lambda source
matches the repository, and every asset a page references is shipped and uploaded.

## Compliance disclosure in the opening

Scenarios 2 and 3 are marketing calls, so their openings now identify the
assistant, disclose that it is automated, state the purpose, give a recording
notice and make the licence position explicit. Bank is a servicing call and is
deliberately left without a self-introduction, at the demo owner's direction.

Insurance:

```text
สวัสดีค่ะ ดิฉันสุดา ผู้ช่วยอัตโนมัติของบริษัทประกันค่ะ โทรมาเพื่อนำเสนอข้อมูลแผนความคุ้มครอง
การสนทนานี้อาจถูกบันทึกเพื่อพัฒนาคุณภาพบริการค่ะ ดิฉันไม่ใช่ตัวแทนที่ได้รับอนุญาต
หากสนใจจะให้เจ้าหน้าที่ผู้ได้รับอนุญาตติดต่อกลับค่ะ
```

Securities:

```text
สวัสดีค่ะ ดิฉันสุดา ผู้ช่วยอัตโนมัติของบริษัทหลักทรัพย์ค่ะ โทรมาแจ้งข้อมูลสัมมนาการลงทุน
ไม่ใช่การให้คำแนะนำการลงทุนค่ะ การสนทนานี้อาจถูกบันทึกเพื่อพัฒนาคุณภาพบริการ
และผู้แนะนำการลงทุนที่ได้รับอนุญาตจะเป็นผู้ดูแลรายละเอียดค่ะ
```

Covered: assistant name, automated-voice disclosure, stated purpose, recording
notice, and licence status with a licensed follow-up. Applied to both engines and
kept in `template.yaml`, `mantle-template.yaml` and `mantle-flow.json`.
`tests/test_compliance_intro.py` asserts each element is present, that the wording
stays in female register with no digits, and that bank keeps no self-introduction.

Still open, and deliberately not added: the company name is generic
(`บริษัทประกัน`, `บริษัทหลักทรัพย์`) rather than a real brand, and there is no PDPA
data-source or explicit opt-out line. Also note the spoken recording notice now
reaches WebRTC callers for scenarios 2 and 3 only; bank WebRTC calls still carry
no notice, because the outbound whisper notice plays on the PSTN path only.
Previous wording is backed up under `backups/compliance-intro-*/`.

## Scenario baseline parity

Bank resolved every ask with deterministic Thai matching, while insurance and
securities depended on the classifier for the same job. Live testing of scenarios
2 and 3 showed three consequences:

| Call | Symptom | Cause |
|------|---------|-------|
| insurance | `รับทราบค่ะ` with no question, call dead-ended | model free text was spoken as the ask |
| insurance | `อาทิตย์หน้า ... วันอังคาร` read back as `วันอาทิตย์หน้า`, then the correction hit the stall guardrail | `อาทิตย์` means both week and Sunday; a corrected value repeated the read-back |
| securities | "sound cut out, please repeat" ended as `human_transfer` | a repeat request was classified as asking for a human |

All three scenarios now share the bank baseline:

- **Deterministic matchers.** `_insurance_need` and `_broker_action` mirror
  `_payment_type`, so a need or an action is resolved without the model. A plain
  expression of interest accepts the seminar, and `BROKER_DECLINE_RE` stops a
  refusal being read as interest.
- **The model never authors an ask.** Both scenarios now speak the deterministic
  question, so wording is fixed and testable exactly as in bank.
- **Week versus Sunday.** Bare `อาทิตย์` followed by `หน้า`/`นี้` is a week, so no
  `วัน` prefix is added; `SPECIFIC_WHEN_RE` prefers a specific weekday over a
  vague week when both are spoken.
- **Read-back corrections.** A caller who restates a different date or amount is
  re-captured instead of hearing the same read-back, which previously stalled the
  call.
- **Repeat requests.** `REPEAT_RE` replays the previous question and resets the
  stall counter, so asking to repeat is never a transfer. A genuine stall still
  escalates.

Regression suite: `tests/test_scenario_baseline.py`, 13 tests, replaying all
three failed conversations verbatim.

## Grounded discovery for marketing scenarios

Insurance and brokerage now use a short discovery sequence before a human handoff.
The purpose is helpful engagement, not a fast booking or hard sale.

- **Insurance:** broad need -> one approved category fact -> one customer priority
  -> recap and permission -> licensed-agent appointment and verbatim time confirmation.
- **Brokerage:** initial interest -> learning topic -> general experience -> one
  approved educational fact -> seminar or licensed consultation -> verbatim time
  confirmation when applicable. Generic interest is never terminal.
- **Grounding:** dynamic wording may use only the approved facts enumerated in the
  managed prompt/deterministic engine and the customer's own words. No current
  market claims, invented products, features, benefits, promotions or statistics.
- **Conduct:** one next-step invitation, immediate acceptance of no, and no urgency,
  scarcity, FOMO, fear, repeated persuasion, personalized advice or suitability
  decision. Engagement should feel warm through relevance and accurate recap,
  never hype.
- **Handoff context:** `outcomeDetail` carries the discovered need/topic and customer
  priority/experience so the licensed human does not restart discovery.

Pinned by `tests/test_discovery_engagement.py` and managed-prompt policy tests.

## Negotiation policy v2

Live bank calls showed three defects: a customer declaring hardship was recorded
as `callback` or `declined`, the agent repeated the same question three times, and
the outcome record lost the hardship fact entirely. Policy v2 addresses all three
across **bank, insurance and securities**.

Enabled with `NEGOTIATION_POLICY_V2=true` on `fsi-mantle-dialogue`
(`MAX_REPEATS=1`). Set the flag to `false` for the previous behaviour; the code
path is fully guarded and reported per turn as `policyVersion`.

**Shared signals**, detected with explicit Thai patterns rather than model
judgement, in priority order:

| Signal | Handling |
|--------|----------|
| `do_not_contact` | log the ban and end politely, no disclosure |
| `vulnerability` | specialist referral |
| `complaint` | log the complaint and offer a human |
| `hardship` | scenario-specific, see below |

**Scenario handling of hardship:**

- **Bank** - acknowledge, then ask one question: partial payment now, or defer.
  A partial offer collects amount and date verbatim, reads them back, and records
  `partial_payment_agreement`. Otherwise it records
  `payment_assistance_referral`. The agent never promises approval or enrolment.
- **Insurance** - stops the sales path and records `affordability_review` for a
  licensed agent to review existing cover and premium. No cancellation advice.
- **Securities** - stops promotion and records `licensed_rep_referral`. Never
  recommends a security or discusses returns.

**No-progress guardrail:** if the same prompt would be sent twice for the same
stage, the call escalates to `unresolved_needs_human` instead of repeating. This
is generic, so it also catches failures nobody enumerated.

**ASR whitespace was a wrong-person disclosure risk.** `_identity_no` matched only
the contiguous `ไม่ใช่`, so the ASR form `ไม่ ใช่ ครับ` fell through to
`_identity_yes`, which still saw `ใช่`. A wrong-person denial was therefore read as
a confirmation and the debt would have been disclosed. Every boolean classifier
(`_is_yes`, `_is_no`, `_identity_yes`, `_identity_no`, `_payment_type`,
`_looks_amount`, `_looks_datetime`) now strips whitespace before matching.
Extraction still works on the caller's original words so read-backs stay verbatim,
but `THAI_NUM` allows spaces between number parts, so `ห้า พัน บาท` is captured
whole instead of collapsing to `ห้า`. Pinned by `tests/test_asr_spacing.py`.

**ASR whitespace:** Advanced ASR returns turns such as
`ตอนนี้ ไม่มี เงิน จ ่าย` and can split vowel marks (`ต ัง ค์`). Thai has no
inter-word spaces, so signal detection strips whitespace before matching.
Without this, `ห้าม โทร มา อีก` and `ป่วย หนัก` were not detected at all - the two
most safety-critical signals. Regression tests use verbatim strings from live
transcripts.

**Reason codes:** every outcome carries `signal=<signal>` in `outcomeDetail`, and
`primarySignal` is published as a session attribute, so a hardship case is no
longer indistinguishable from an ordinary callback.

New outcome types: `payment_assistance_referral`, `partial_payment_agreement`,
`affordability_review`, `licensed_rep_referral`, `vulnerability_referral`,
`complaint_logged`, `do_not_contact`, `unresolved_needs_human`.

Verified live on `fsi-mantle-dialogue`: hardship now yields
`payment_assistance_referral` with `signal=hardship`, insurance yields
`affordability_review`, securities yields `licensed_rep_referral`, and a contact
ban yields `do_not_contact` with no debt disclosure. Regression suite: 103 tests.

**Now applied to the managed path too.** Option B authors its own Thai, so its
policy lives in the Q in Connect prompts. All three `ai-prompt-*.json` files carry
the hardship rule, the shared signals, the anti-repeat rule and the extended
`outcomeType` vocabulary, and were published with
`qconnect update-ai-prompt --visibility-status PUBLISHED`. Previous text is backed
up under `backups/prompt-policy-v2-*/`.

The difference between the two engines is enforcement, not policy: the
deterministic engine guarantees the behaviour in Python and is unit tested, while
the managed engine is instructed and therefore probabilistic.
`tests/test_managed_prompt_policy.py` asserts both engines expose the same outcome
vocabulary so they cannot drift apart, and that each prompt keeps its safety
lines (no promise of programme approval, no advice to lapse a policy, no comment
on an investment loss).

`outcomeType` is not validated by `session_context.py`; it is written straight to
the `fsiOutcome` contact attribute, so new outcome values need no backend
change.

## Cost per call

`tools/cost_per_call.py` estimates cost per call from **this account's own metered
usage**, not from a published price list or from memory. Rate provenance is
printed by `--show-sources`.

```bash
python3 tools/cost_per_call.py --minutes 3 --channel pstn-th
python3 tools/cost_per_call.py --compare
python3 tools/cost_per_call.py --minutes 3 --channel pstn-th --calls 1000 --tollfree-numbers 1
```

Rates derived from Cost Explorer (us-west-2, 2026-08-01..21) and token volumes
from CloudWatch `AWS/Bedrock` over the same 95 calls. Key findings:

- **Voice minutes dominate**: ~99% of a 3-minute Thai PSTN call. Thailand
  outbound telephony is `$0.0699/min` against `$0.010/min` for WebRTC audio, on
  top of `$0.038/min` Connect AI end-customer minutes.
- **Dialogue tokens are marginal**: Option A averages only `1.60` Luna turns and
  `0.44` Terra fallback turns per call (`221` in / `168` out tokens per turn),
  because deterministic stages resolve without invoking a model. That is well
  under a cent per call.
- Option B (Haiku 4.5) measured `$0.001/1K` input and `$0.005/1K` output.

**Option A token prices** come from the published Bedrock model cards (Standard
tier, short context 272K, per 1M tokens):

| Model | in-region / geo | global |
|-------|-----------------|--------|
| Luna  | $0.22 in, $1.32 out | $0.20 in, $1.20 out |
| Terra | $2.20 in, $13.20 out | $2.00 in, $12.00 out |

The dialogue Lambda calls the `us.openai.gpt-5.6-*` Geo cross-Region IDs, so
`geo` is the default; `--inference-option global` prices the cheaper global
routing. These models have no AWS Price List API entry, which is why the rates
are read from the model cards rather than measured:
[Luna](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-luna.html),
[Terra](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-56-terra.html).

At measured volumes Option A costs **$0.00103 per call** — Luna $0.00043 across
1.60 turns and Terra $0.00060 across 0.44 fallback turns. The Terra fallback is
**58% of Option A's dialogue cost despite being 28% of the turns**, because Terra
is ten times Luna's rate; reducing fallback frequency is the only meaningful
dialogue-cost lever. Option B measures $0.00396 per call, so Option A is roughly
4x cheaper per call — but both are under half a cent and immaterial next to voice
minutes.

### Pricing assumptions

Stated explicitly rather than implied, and printed by `--show-assumptions`:

- Talk time bills linearly per minute with no rounding up to whole minutes.
- One outbound call per contact; no retries, queueing or agent handling time.
- Option A averages 1.60 Luna and 0.44 Terra turns per call, as measured.
- Prompts stay inside the 272K short-context tier (measured ~220 tokens).
- **Contact Lens post-contact analytics is ASSUMED** at 40% of the Connect AI
  minute rate ($0.0152/min). There was no billed usage in the window to measure,
  so this is an order-of-magnitude placeholder, adjustable with
  `--contact-lens-per-min`.
- **Serverless overhead is ASSUMED** at $0.0020/call as a single rounded
  allowance for ~12 Lambda invocations plus DynamoDB, S3, CloudFront and API
  Gateway. Adjustable with `--serverless-per-call`.
- Excludes recording/transcript storage growth, data transfer, support plans and
  taxes. Telephony rates are country and carrier specific.

Both assumed components are **excluded by default** and added with
`--include-assumed`, which also prints a note naming them. With them included a
3-minute Thai PSTN call moves from $0.3249 to $0.3725, and the assumed share is
13% of the total — worth stating openly rather than burying.

### Post-call estimate popup

`web/cost.js` is shared by both channels: the WebRTC landing page shows the card
when the call ends, and the PSTN status page shows it when polling reports the
contact completed. Each page passes its own channel, and the card reports the
actual call duration with a per-component breakdown and the assumptions list.

**The telephony line is channel-specific and never doubled.** Connect AI minutes
apply to every channel, but a WebRTC call is charged web-calling audio
($0.010/min) while a PSTN call is charged outbound telephony ($0.0699/min for
Thailand). Exactly one of them applies, so exactly one row is rendered — a 3
minute call therefore estimates $0.1943 on WebRTC and $0.3740 on Thai PSTN with
assumed components included. `pstn` is accepted as an alias for `pstn-th`, and an
unknown channel falls back to WebRTC. Rates mirror `tools/cost_per_call.py` and
parity is asserted by `tests/test_web_landing_ui.py`.

The dialogue line is deliberately a **single blended allowance that is never
split by ตัวเลือก A/B**. A per-option cost would let a tester infer which engine
they heard and break the blind comparison; the popup states this explicitly
instead of hiding it. Assumed rows are visually distinguished and labelled
ประมาณการ.



### Custom domain

The live demo is served at `https://<demo-domain>`, in
addition to the original CloudFront hostname which still works.

| Setting | Value |
|---------|-------|
| Alternate domain name | `<demo-domain>` |
| Certificate | wildcard `*.<your-zone>` (ACM us-east-1, EC-prime256v1) |
| SSL support method | `sni-only` (replaced `vip`, which bills for a dedicated IP) |
| Security policy | `TLSv1.3_2025` — TLS 1.3 only |
| DNS | `CNAME` in zone `<your-zone>` to the distribution |

CloudFront certificates must live in **us-east-1** regardless of where content is
served. `TLSv1.3_2025` is post-quantum ready and TLS 1.3 only; hybrid
post-quantum key agreement (ML-KEM) is enabled automatically across all
CloudFront TLS policies, so PQC key establishment does not require this policy —
but TLS 1.3 is a prerequisite for negotiating it. Certificate *signatures* remain
classical (ECDSA P-256); ACM does not yet issue PQC signature algorithms.
Verified: TLS 1.3 handshake succeeds, a TLS 1.2-only client is refused with alert
70, and the geo restriction (`TH`, `SG`, `JP`) still applies.

For replication, export `DEMO_DOMAIN` before `post-deploy.sh` to put a custom
domain in the presenter QR page instead of the CloudFront hostname:

```bash
DEMO_DOMAIN=connect-demo.example.com ./post-deploy.sh fsi-outbound-demo us-west-2
```

### Replication readiness

Verified for a clean-account deploy:

- Both templates validate against CloudFormation (via `--template-url`, since
  each now exceeds the 51,200-byte inline limit — deploy with `--s3-bucket`).
- Inline Lambda source in `template.yaml` and `mantle-template.yaml` matches
  `lambda/session_context.py`, `lambda/mantle_dialogue.py` and `lambda/index.py`.
- `post-deploy.sh` uploads every script the pages reference. It reads from
  `web/`, which is therefore the deployment source of truth; `web/qr.html`
  keeps its `__DEMO_DOMAIN__` placeholder for per-stack substitution.
- All three managed prompt files exist, parse, and are wired to agent creation.

`tests/test_deployment_readiness.py` enforces these generically, so a newly
referenced asset that is not shipped or uploaded fails the suite instead of
404ing after deployment.

## Validation checklist

1. Submit each scenario using a Thai test number.
2. Confirm the correct scenario-specific greeting.
3. Bank: confirm identity, request installments, provide first date; verify
   `fsiOutcome=payment_commitment` in contact attributes.
4. Insurance: state a need, then a priority, then accept the handoff, then give a
   time; verify `appointment` and that `outcomeDetail` carries both the need and
   the customer's stated priority. A need alone must **not** reach an appointment.
5. Brokerage: say only `สนใจครับ` and verify the call stays `pending` at
   `discover_topic` — generic interest must not send details or book. Then give a
   topic and an experience level and verify `seminar_details` or `consultation`
   with both in `outcomeDetail`. Separately ask for a stock recommendation and
   verify `advice_request` with no stock named.
6. Confirm the post-call estimate appears with exactly one media line: `เสียง
   WebRTC` on a web call, `ค่าโทรออกไทย (PSTN)` on a phone call — never both.
7. Click the R/G/B theme buttons and confirm the palette, `?theme=` query and
   saved preference all follow.
8. Review `/aws/connect/<instance-alias>` and Lambda logs for errors.

## Notes

- Prompt/agent creation and Advanced ASR are API-only, handled by
  `post-deploy.sh`.
- The script creates Q in Connect prompt/agent resources outside CloudFormation;
  delete those manually during teardown.
- The contact flow references a stable Lex alias ARN; post-deploy publishes a new
  bot version and points the alias to it.
- For production, add authentication/WAF, per-device rate limiting, consent,
  approved disclosure text, outcome storage, retries, and campaign governance.

## Experimental Mantle dialogue path (feature-flagged, opt-in)

An isolated second dialogue engine runs GPT-5.6 Luna/Terra on Amazon Bedrock in
place of Q in Connect / Claude Haiku. It is deployed as a **separate stack** and
never replaces the managed path. Inference stays inside AWS via Bedrock Runtime
`Converse` (no public OpenAI egress).

| Resource | Value |
|----------|-------|
| Stack | `fsi-mantle-experiment` (`iac/mantle-template.yaml`) |
| Lambda | `fsi-mantle-dialogue` (Luna primary, Terra fallback) |
| Lex bot | dedicated Thai bot, `MantleDialogue` + `FallbackIntent` |
| Contact flow | separate `FSI Mantle Experimental Flow` |
| Dialogue source | `lambda/mantle_dialogue.py`, flow `iac/mantle-flow.json` |

```bash
aws cloudformation deploy \
  --template-file mantle-template.yaml \
  --stack-name fsi-mantle-experiment \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2 \
  --parameter-overrides \
    ConnectInstanceArn=arn:aws:connect:us-west-2:<ACCOUNT>:instance/<INSTANCE_ID> \
    ConnectInstanceId=<INSTANCE_ID> \
    SessionContextFunctionArn=<SESSION_CONTEXT_LAMBDA_ARN>
```

### Feature flag

The managed Q/Haiku path is the default and the rollback baseline. `POST /call`
accepts an optional `brainMode`:

| Request | Behaviour |
|---------|-----------|
| `brainMode` omitted or `managed` | managed `CONTACT_FLOW_ID` (unchanged) |
| `brainMode: "mantle"` | experimental flow, only if enabled |
| any other value | `400` |

`mantle` returns `403` unless **both** `MANTLE_ENABLED=true` and
`MANTLE_CONTACT_FLOW_ID` are set on the trigger Lambda (`MantleEnabled` /
`MantleContactFlowId` parameters in `template.yaml`). The response and the
Connect contact attributes both echo `brainMode`.

The demo page exposes a **สมองสนทนา** selector with two options, so Haiku and
GPT can be compared side by side on the same scenario:

| Button | Sends | Engine |
|--------|-------|--------|
| Claude Haiku 4.5 (default, pressed on load) | `brainMode: "managed"` | Q in Connect / Claude Haiku 4.5 |
| GPT-5.6 Luna | `brainMode: "mantle"` | Bedrock Luna, Terra on fallback |

The selector applies to both WebRTC and PSTN, and the live call panel names the
active engine. Its buttons reuse the `mode-option` styling but are scoped by
`data-brain`; the conversation-mode handler is scoped to `.mode-option[data-mode]`
so the two groups stay independent. Rebuild the browser bundle
(`npm run build:webrtc` in `web/`) and bump the `webrtc.bundle.js?v=` query
whenever `webrtc-client.js` changes.

### Dialogue guarantees

Deterministic code, not the model, decides bank identity, payment type, dates,
amounts, appointment times, and yes/no confirmation. The model is used only to
classify ambiguous business intent, and its `rawValue` must be an exact
substring of the transcript or the turn falls back to Terra.

- Bank debt facts are withheld from the model context until identity is
  deterministically confirmed; a wrong person never hears an amount, due date,
  `ค้างชำระ`, or `เงินกู้`.
- "Not convenient" is never treated as a wrong-person answer.
- Critical dates, amounts, and appointment times are held pending, read back
  verbatim, and committed only after explicit confirmation.
- Brokerage advice requests close as `advice_request` without a recommendation.

Run `python3 -m unittest tests/test_mantle_dialogue.py tests/test_trigger.py`
(9 tests) before deploying.

### Measured behaviour (us-west-2, short Thai turns)

- Deterministic turns bypass the model entirely; median server duration 3 ms.
- Model turns: median Bedrock latency ~1.5 s, Lambda p95 ~1.9 s.
- Sequential short-prompt medians: Luna ~0.97 s, Terra ~1.19 s, managed Claude
  Haiku 4.5 ~1.44 s.
- Terra fallback did not trigger in any observed run; cold start 457–550 ms.
- One invocation in 53 reached ~7.9 s of Bedrock tail latency. Lambda timeout is
  25 s so the turn still completes, but tail latency is the main voice-UX risk
  to watch.

### Rollback

The pre-Mantle snapshot under `backups/` restores the managed Lambdas, flows,
prompts, and web assets. Enabling the flag only adds environment variables, so
the fastest revert is `MANTLE_ENABLED=false` (managed path is untouched). To
remove the experiment entirely:

```bash
aws cloudformation delete-stack --stack-name fsi-mantle-experiment --region us-west-2
```

### Deployment gotchas

- The Thai locale fails to build unless
  `GenerativeAISettings.RuntimeSettings.NluImprovementSpecification` sets
  `Enabled: true` and `AssistedNluMode: Primary`.
- A Lex bot version is immutable. If a version is created from a locale that
  failed to build, CloudFormation cannot repair it in place (`Internal Failure`);
  add a **new** `AWS::Lex::BotVersion` resource and repoint the alias.
- **Every Lex block must declare an explicit intent condition.** The relay
  design returns `FallbackIntent` on each turn. Amazon Connect sends an executed
  intent with no matching condition down the *Default* branch
  (`NoMatchingCondition`), so with an empty `Conditions` list the call plays the
  error prompt and hangs up after exactly one turn. Each
  `ConnectParticipantWithLexBot` action therefore routes both `FallbackIntent`
  and `MantleDialogue` to `route-result`. This only reproduces over voice — the
  `RecognizeText` API bypasses Connect's intent routing, so text tests pass
  while the voice loop is broken.
- The flow JSON is embedded compactly in the template because CloudFormation
  rejects inline templates larger than 51,200 bytes.
- `lambda/ctxpkg/index.py` is a stale copy. Package the session-context Lambda
  from `lambda/session_context.py`.

### Voice validation status

Verified on a live WebRTC call (`brainMode=mantle`): SUDA `connect:agentic`
voice, `th-TH`, scenario branch, intro spoken exactly once, Lex opened with the
correct alias and session attributes, three consecutive dialogue turns through
`input-resume`, `mantleState` carried across turns, prompts advancing without
duplicate speech, and a silent caller handled gracefully
(`InputTimeLimitExceeded` → one Thai apology → disconnect). `brainMode=mantle`
persists in the contact attributes.

Not yet exercised end-to-end: a spoken turn that commits a critical value and
drives `done=true` → closing message → outcome Lambda. That needs a human Thai
speaker; there is no Thai Polly voice in any region to automate it.

## Thai wording rules

Both paths speak female-register Thai (SUDA), and each needed a different fix
because they author Thai differently.

**Managed / Haiku** generates every sentence, so wording is steered by prompt
only and stays probabilistic. Sampling the original prompts produced colloquial
question words in 15 of 20 replies (`วันที่ไหน`, `เวลาไหน`, `เท่าไหร่`,
`เมื่อไหร่`). All three scenario prompts now carry a formal-register rule plus
explicit examples of the exact wording (`สะดวกชำระงวดแรกวันใดคะ`,
`งวดแรกจำนวนเท่าไรคะ`, `สะดวกนัดคุยวันใดและเวลาใดคะ`). The abstract rule alone
was unreliable; the worked examples are what made it stick. Re-sampling the
deployed prompts gave 0 violations in 24 replies. Prompt text lives in
`ai-prompt-{bank,insurance,broker}.json` and is pushed with
`aws qconnect update-ai-prompt --visibility-status PUBLISHED`.

**Mantle / GPT** builds asks and read-backs as deterministic Python strings, so
its wording is fixed and testable. Three bugs were corrected in
`_readback`/`_set_pending`:

- Customer politeness particles were echoed, making female SUDA say `ครับ`
  (`พรุ่งนี้ครับ`). `_strip_politeness` now removes them at capture, so the
  spoken read-back and the committed outcome value match.
- `ในวันที่` was hardcoded, producing ungrammatical `ในวันที่ พรุ่งนี้`.
  `_date_phrase` now picks the connector: no marker for relative days
  (`พรุ่งนี้`, `สิ้นเดือน`), `ใน` for weekday forms (`ในวันจันทร์`), and
  `ในวันที่` only for bare date words (`ในวันที่ ยี่สิบสิงหาคม`).
- A doubled marker (`ในวันที่ วันที่ยี่สิบสิงหาคม`) when the customer already
  said `วันที่`.

**Pauses between spoken choices.** SUDA does not pause on a plain space between
Thai clauses, so enumerated options were heard as one run-on word
(`เต็มจำนวนชำระบางส่วนหรือแบ่งชำระ`). The flow renders prompts as plain text, so
SSML `<break>` is unavailable; a comma is used instead because it produces an
audible pause and is not spoken aloud. Applied to **both** authoring paths in
**all three scenarios**:

- Mantle deterministic strings go through `_spoken_options`, the single place that
  decides the separator, driven by `BANK_PAYMENT_CHOICES`,
  `BANK_HARDSHIP_CHOICES`, `INSURANCE_NEED_CHOICES` and `BROKER_ACTION_CHOICES`.
- Mantle dynamic replies: the classifier prompt now requires the same comma
  separation, because a model-authored message bypasses the deterministic
  fallbacks.
- Managed prompts: all three `ai-prompt-*.json` files carry the rule plus a
  worked example, and were published live with
  `qconnect update-ai-prompt --visibility-status PUBLISHED`. Previous prompt text
  is backed up under `backups/prompt-pause-*/`.

Spoken result: `สะดวกชำระเต็มจำนวน, ชำระบางส่วน, หรือแบ่งชำระคะ`.

`_safe_model_message` also converts a model's trailing `ครับ` to `ค่ะ` instead of
appending, which previously would have produced `รับทราบครับค่ะ`.

Stripping a politeness particle does not violate the verbatim rule: digits and
date/amount words are never converted, only the trailing particle is removed.

## Tester feedback collection

Both channels show a Thai feedback form when the call ends: the WebRTC page
renders it under the call panel, and the PSTN page renders it once status polling
reports the call completed. `web/feedback.js` is shared by both.

Session context is **pre-filled read-only** (time, scenario, name, channel,
option) so the tester only supplies opinion. The API does not trust those
values: it re-derives scenario, channel and option from the Connect contact
attributes and reads call timing from `DescribeContact`, so a tampered page
cannot mislabel which option was rated.

Collected per submission: five 1-5 ratings (overall required; voice,
understanding, relevance, latency optional), a completion answer
(yes/partial/no), and a free-text comment capped at 1000 characters.

The public GitHub mirror receives a redacted comment: `_redact_for_public()`
masks emails and digit runs of 9+ so a tester cannot accidentally publish their
own phone number or national ID. DynamoDB keeps the raw text. See `SECURITY.md`.

Feedback posts to the existing `/call` endpoint with `action: "feedback"`, so
CloudFront needs no new behaviour and the origin secret plus the TH/SG/JP
restriction still apply. Each submission must carry the HMAC `statusToken`
issued when the call started, which ties feedback to a real contact and expires
after an hour. Rate limiting is **not** implemented; a tester holding a valid
token can submit repeatedly.

Storage is DynamoDB table `fsi-demo-feedback` (`contactId` + `submittedAt`),
created by `FeedbackTable` in `template.yaml`.

### Blind A/B labelling

The UI never names the model. The selector offers **ตัวเลือก A** and
**ตัวเลือก B**, and the live call panel and feedback form use the same neutral
labels, so testers rate what they hear rather than a brand:

| UI label | `brainMode` | Real engine |
|----------|-------------|-------------|
| ตัวเลือก A (default, listed first) | `mantle` | Bedrock GPT-5.6 Luna |
| ตัวเลือก B | `managed` | Q in Connect / Claude Haiku 4.5 |

The landing page lists PSTN before WebRTC and ตัวเลือก A before ตัวเลือก B, and
the first item in each pair is the default. **ตัวเลือก A is the experimental
Bedrock path**, so it is what testers get unless they switch. Because the UI now
defaults to `mantle`, setting `MANTLE_ENABLED=false` makes the default request
return `403` rather than silently falling back — flip the UI default back to
`managed` at the same time if you disable the experiment.

Issue labels follow the UI, so `option-a` means the Bedrock path. If the mapping
is ever swapped again, previously filed issues keep their old label and must be
closed or relabelled before analysing by label.

The true engine is recorded in DynamoDB only. Keep it out of anything a tester
can read, including the public issue tracker, or the blind is broken.

### Optional GitHub issue mirroring

Set `GithubFeedbackEnabled`, `GithubFeedbackRepo` (`owner/repo`) and
`GithubTokenSecret` (a Secrets Manager id, never a literal token) to mirror each
submission into the issue tracker for triage. Issues are labelled
`demo-feedback` plus `option-a` / `option-b`.

DynamoDB is the system of record: the item is written first, and mirroring is
best effort. A GitHub outage logs a warning and returns `issue: null` without
failing the tester's submission. The issue number is written back to the record
when it succeeds, which needs `dynamodb:UpdateItem`.

The issue body deliberately excludes the tester-supplied name and the real model
name. **If the repository is public, tester comments become world-readable**, so
the form warns testers not to enter personal data. Prefer a private repository
for real user testing.

## Landing page slides

`web/index.html` is a horizontal slide deck. Slide 1 is the approved demo
console and is **frozen** - its markup is preserved byte-for-byte inside
`<section class="slide">`; only the wrapper was added. Slides 2-4 document the
technical architecture and are reached by sliding right.

| Slide | Content |
|-------|---------|
| 1 | Frozen demo console: scenario, channel, option A/B, call controls, feedback form |
| 2 | Staged space mission: central DEMO SCENES selector → zoomed Mission Brief → explicit Mission Proof reveal → reset |
| 3 | AWS voice backbone: a clickable left-to-right topology from channel choice through CloudFront/S3, API/Lambda, Connect, Advanced ASR, blind dialogue, safety, and DynamoDB |
| 4 | Evidence walkthrough: select safety scenes or supporting operations and follow the verify-to-recover chain |

Slides 2 and 4 adapt the presenter-flow pattern used by the architecture
reference: primary demo scenes are separated from supporting journeys, every
selector has a service/scenario icon, hop/check count and concise outcome. Slide
3 instead adapts the referenced AWS Connect guidance topology into a truthful
left-to-right backbone with clickable service nodes and side annotations for the
private S3 origin, Contact Flow, and concealed dialogue branch. The reference's
FAQ bucket and human-agent endpoint are intentionally omitted because they are
not part of this demo. In all three slides, the active selection updates a
context canvas rather than a prose-heavy card. The canvas
contains a three-step micro-flow, three operating facts (ownership, boundary,
or channel), and three proof signals alongside the short detail and evidence
summary. A compact four-stage chain stays visible at the bottom, and each slide
provides an `Auto tour` control that cycles only its primary presentation
scenes. Arrow keys can traverse every selector manually. Selection also triggers
an elastic service-orb response and a short HUD dock animation; supported
coarse-pointer devices receive a brief `navigator.vibrate([12,18,12])` pulse.
The detail and evidence HUDs drift independently inside their layout slots, with
an animated starfield and scanner treatment. Browsers without vibration still
receive the elastic visual response, and `prefers-reduced-motion: reduce`
disables all drifting, scanning, and impact animation. Amazon Connect and AWS Advanced ASR for
`th-TH` remain the first two core architecture choices; the speech path is
explicitly AWS-native with no third-party speech service.

Navigation is arrows, dots, keyboard left/right (suppressed while typing in an
input), and touch swipe. The deck is `height:100dvh` with `scroll-snap-type:x
mandatory`. At desktop widths, height-aware CSS compacts the frozen console and
all four slides fit the viewport without a visible vertical scrollbar. At mobile
widths each slide switches to `overflow-y:auto`; selectors stack in one column
and may scroll vertically without creating document-level horizontal overflow.

The architecture copy names AWS services but deliberately **not** the dialogue
models, so the blind A/B test survives someone reading the documentation.

## Teardown

```bash
aws s3 rm s3://fsi-outbound-demo-<ACCOUNT>-<REGION> --recursive
aws cloudformation delete-stack --stack-name fsi-outbound-demo --region us-west-2
```

Also remove, none of which CloudFormation owns:

- Q in Connect AI prompts and agents created by `post-deploy.sh`
  (`FSIBankCollectionThaiPrompt`, `FSIInsuranceThaiPrompt`,
  `FSIBrokerageThaiPrompt` and their agents).
- The `fsi-mantle-experiment` stack, if the experimental path was deployed.
- The custom-domain DNS record, if one was added.

**Do not delete the `*.<your-zone>` certificate.** It is shared with
ten other CloudFront distributions in this account. Removing the alternate domain
name from this distribution is sufficient; ACM refuses to delete an in-use
certificate anyway.

## What is actually under infrastructure-as-code

Worth stating plainly, because the repository layout implies more coverage than
exists. Only `iac/mantle-template.yaml` corresponds to a deployed CloudFormation
stack, `fsi-mantle-experiment` (11 resources, adopted by the CDK app in `cdk/`).

`iac/template.yaml` describes 22 resources for the main path -- the web bucket,
CloudFront distribution, HTTP API, DynamoDB feedback table, the session-context and
trigger Lambdas, the Q in Connect assistant and knowledge base -- and **none of them
are stack-managed**. The live equivalents exist but carry no
`aws:cloudformation:stack-name` tag, so they were created outside CloudFormation:

| Resource | Owning stack |
| --- | --- |
| `fsi-mantle-dialogue` | `fsi-mantle-experiment` |
| `fsi-qic-session-context` | none |
| `fsi-outbound-demo-trigger` | none |
| `fsi-thai-post-contact-analyzer` | none |
| `fsi-outbound-demo-<ACCOUNT_ID>` (bucket) | none |
| `fsi-demo-feedback` (table) | none |

Two consequences follow. First, `iac/template.yaml` has never created anything; it
validates, but it is a reconstruction of resources built live, and deploying it into
this account as-is would collide with the resources it describes. Redeployability of
the main path is therefore unproven, not proven. Second, CDK adoption could only be
applied to the mantle stack, since adoption requires a stack to adopt.

Bringing the unmanaged resources under management is a CloudFormation resource-import
exercise: import them into a stack with matching logical ids, verify an empty change
set, and only then convert to constructs. That is deliberately not attempted while a
demo depends on them.
