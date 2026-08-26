# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately to the
repository owner, or to AWS via
<https://aws.amazon.com/security/vulnerability-reporting/> if it concerns an AWS
service rather than this sample.

## What this repository must never contain

Enforced mechanically by `tools/publish_gate.py`, which is blocking in CI:

- AWS access keys, secret keys, session tokens, private keys
- Personal access tokens or bearer tokens
- Shared secrets such as the CloudFront origin secret
- AWS account ids, Connect instance or flow ids, claimed phone numbers,
  hosted-zone names, personal domains or email addresses

Those belong in CloudFormation parameters (`NoEcho: true` where secret),
environment variables, or documented placeholders such as `<ACCOUNT_ID>` and
`__DEMO_DOMAIN__`.

Operational rollback snapshots are deliberately **not** published: they contain
live ARNs and secrets. `backups/` is gitignored and reproducible from AWS.

## Tester feedback mirrored to a public repository

Feedback submitted in the demo is stored in DynamoDB and, when
`GITHUB_FEEDBACK_ENABLED=true`, also mirrored as a GitHub issue. That repository
may be public, so the mirrored copy is deliberately reduced:

| Field | Public issue | Private DynamoDB record |
|---|---|---|
| Ratings, scenario, channel, duration | yes | yes |
| Contact ID | yes — needed to audit a call back to source | yes |
| Tester name | **no** | yes |
| Dialogue engine / model name | **no** | recorded as `brainMode` |
| Free-text comment | **redacted** | raw |

The comment box is free text, so a tester can type their own phone number,
national ID or email into it. `_redact_for_public()` masks email addresses and any
digit run of 9 or more before the comment reaches the public issue; the private
record keeps the original for analysis. The threshold is 9 digits so an ISO date
survives while Thai mobile numbers (10), bank accounts (10–12) and national IDs
(13) are caught.

This is defence in depth, not a guarantee. Free text can always carry disclosure
a pattern will not catch. If the mirror repository is public, review issues
periodically, or point `GithubFeedbackRepo` at a private repository.

**Token scope.** The mirror authenticates with a token from Secrets Manager. Use a
fine-grained token limited to *issues: write* on that single repository. A classic
token with broad `repo` scope would grant far more than the mirror needs.

## If a secret is ever committed

1. **Rotate it in AWS first.** Treat it as compromised regardless of how briefly
   it was exposed.
2. Then decide on history: rewrite with `git filter-repo`, or start a fresh
   repository.
3. Only then publish.

Sanitising the working tree is not sufficient — git history retains the value.

## Demo scope and data

This is a demonstration. The only speech in it is the demo owner's own test
calls, and all customer facts are synthetic. Before any real customer data:

- Thai PII redaction is **unsolved** — Contact Lens cannot redact Thai, so
  transcripts are protected by access control alone. See
  `docs/implementation-notes.md`.
- Add authentication and WAF in front of the demo endpoint, per-device rate
  limiting, recorded consent, and legally approved disclosure wording.
