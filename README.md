# Thai Agentic Voice for Financial Services on Amazon Connect

A working demonstration that Amazon Connect can hold a useful, compliant,
**Thai-language** voice conversation for financial services — driven by an AI
agent, with market-conduct guardrails a regulated institution would recognise,
and with evidence produced after every call.

> Demonstration only. Every customer fact is synthetic and the only speech is the
> demo owner's own test calls. Read [SECURITY.md](SECURITY.md) before pointing it
> at anything real — Thai PII redaction is unsolved.

## What it does

Three outbound financial-services scenarios, each reachable from a browser over
WebRTC or by dialling a US toll-free number. There is no Thai number: Amazon Connect
had none available to claim in this region, and US toll-free generally cannot be
dialled from outside the US, so callers in Thailand use the WebRTC path.

| Scenario | Behaviour |
|---|---|
| **Loan collection** | Verifies identity before disclosing anything, offers full / partial / instalment, reads the amount back in spoken Thai, commits only on explicit confirmation |
| **Insurance** | Discovers one coverage need and the customer's own priority, shares one approved fact, then asks permission for a licensed-agent handoff |
| **Brokerage** | Discovers learning topic and experience before offering a tailored seminar or a licensed consultation — generic interest never books or closes |

## Inbound, and what happens when the assistant cannot help

| Capability | Behaviour |
|---|---|
| **Live handoff** | Six referral outcomes transfer to a real agent, who hears a Thai whisper naming why the call was escalated and the discovery already gathered. `do_not_contact` is excluded: a contact ban is honoured by ending the call, not by connecting a person. |
| **Inbound questions** | A caller dials in and asks in Thai. Answers are retrieved from a Thai knowledge base and read back with a citation. An ungroundable question is not guessed at — the caller is told so and handed to a person. |
| **Caller recognition** | A known caller is greeted by name from Customer Profiles, and the agent sees their details on transfer. An unknown caller gets a neutral greeting and no apology. |
| **Account servicing** | Balance and due date come from the caller's profile, never from the knowledge base, because a general FAQ has no idea what a specific caller owes. A deferral request is not granted by a machine; it goes to a person with the request recorded. |

Inbound and outbound share one dialogue, one outcome record and one handoff path, so
the inbound experience cannot drift away from the tested outbound one.

## What makes it more than a phone chatbot

- **Conduct guardrails outrank the objective.** Hardship, vulnerability,
  complaint and do-not-contact are detected with deterministic Thai patterns —
  not left to model judgement — and handled before the sales or collection goal.
- **Grounded, not improvised.** Dynamic wording may only use an enumerated set of
  approved facts plus the customer's own words. No invented product, price,
  market claim, or suitability judgement.
- **Nothing is committed unheard.** Dates and amounts are read back verbatim in
  Thai and require explicit confirmation.
- **Evidence after every call.** Thai transcript, sentiment and rationale, a
  scored evaluation, and an alert carrying the Contact ID so any call can be
  audited back to source.
- **Cost is measured, not guessed.** `tools/cost_per_call.py` derives per-call
  cost from this account's metered usage and prints its provenance.

## Repository layout

```
web/          Tester-facing site: landing page, PSTN status page, presenter QR,
              shared feedback and cost-estimate assets. Single source of truth —
              post-deploy.sh uploads from here.
lambda/       Call trigger/API, Q in Connect session context, dialogue engine,
              Thai post-contact analyzer.
iac/          CloudFormation templates, Q in Connect prompts, contact flow JSON,
              evaluation form, post-deploy.sh.
tests/        Regression suite. Every behavioural claim in this repo is pinned here.
tools/        cost_per_call.py (cost model), publish_gate.py (blocking publish gate).
docs/         Implementation notes and the AI-DLC reverse-Inception record.
```

## Quick start

Prerequisites: an existing Amazon Connect instance with agentic voice available,
a claimed outbound-capable phone number, and Bedrock model access in the target
region.

```bash
# Templates exceed CloudFormation's 51,200-byte inline limit, so stage via S3.
aws cloudformation deploy \
  --template-file iac/template.yaml \
  --stack-name fsi-outbound-demo \
  --s3-bucket <your-deploy-bucket> \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2 \
  --parameter-overrides \
    ConnectInstanceArn=arn:aws:connect:us-west-2:<ACCOUNT_ID>:instance/<INSTANCE_ID> \
    ConnectInstanceId=<INSTANCE_ID> \
    SourcePhoneNumber=+15551230000 \
    ApiOriginSecret=$(openssl rand -hex 32)

# Creates Q in Connect prompts/agents, configures Thai ASR, uploads the site.
./iac/post-deploy.sh fsi-outbound-demo us-west-2

# Optional: put a custom domain in the presenter QR page instead of the
# CloudFront hostname.
DEMO_DOMAIN=demo.example.com ./iac/post-deploy.sh fsi-outbound-demo us-west-2
```

Full detail, including the Thai capability gaps and their workarounds, is in
[docs/implementation-notes.md](docs/implementation-notes.md).

## Verify before you publish or deploy

Requires Python 3.12 with `boto3` (the tests patch every AWS client, but the
Lambda modules import `boto3` at load time).

```bash
pip install boto3
python3 -m unittest discover -s tests -p 'test_*.py'   # regression suite
python3 tools/publish_gate.py                          # blocking publish gate
python3 tools/cost_per_call.py --show-sources          # rate provenance
```

Run both before publishing. There is no CI workflow here by choice; see
CONTRIBUTING.md if you want the gate enforced automatically.

## Thai language reality

Amazon Connect supports Thai for agentic voice and transcription, but not for
several Contact Lens features. This repo documents each gap and the workaround
used, including the one that is **not** solved:

| Capability | Thai | Approach here |
|---|---|---|
| Agentic voice, Advanced ASR, transcript | yes | used directly |
| Sentiment analysis | no | Bedrock classifies the Thai transcript |
| Automated evaluations | no | analyzer submits a Connect evaluation |
| Pattern-match rules | no | deterministic Thai regex in the dialogue layer |
| **PII redaction** | **no** | **unsolved** — access controls only |

## Method

Built with [AI-DLC](https://github.com/awslabs/aidlc-workflows), applied in
reverse: the system was built live first and the Inception artifacts
reconstructed afterwards. What that cost, and the defects it caused, are recorded
in [docs/aidlc-reverse-inception.md](docs/aidlc-reverse-inception.md). The
publish-readiness extension under `.aidlc-rule-details/extensions/security/` is
always enforced and blocking.

## License

MIT-0. See [LICENSE](LICENSE).
