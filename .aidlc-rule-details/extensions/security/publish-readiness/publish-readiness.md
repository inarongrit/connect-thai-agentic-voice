# Publish readiness (security extension)

AI-DLC extension. This directory has **no `.opt-in.md` file**, so per the
extension contract these rules are **always enforced** and are **blocking**: if a
verification criterion is not met, the stage cannot proceed until the finding is
resolved.

Category: `security` · Name: `publish-readiness` · Applies to: Construction and
Operations stages that create, modify, or publish repository content.

## Why this extension exists

This project was built against a live AWS account and only later prepared for a
public repository — the reverse of the AI-DLC order, where Inception would have
established these constraints first. Retrofitting them found a real defect: the
CloudFront origin secret, the AWS account id, the Connect instance id and a
claimed phone number were all sitting in the working tree under `backups/`.

The lesson generalises: when a demo is built live-first, *every* artifact is
guilty until proven parameterised. These rules encode that suspicion so it is
checked by a machine rather than remembered by a person.

## Rules

### R1 — No credential may ever enter the repository (blocking)

Access keys, secret access keys, private keys, personal access tokens, bearer
tokens, and shared secrets such as a CloudFront origin secret. High-entropy hex
strings of 32+ characters are treated as secrets by default.

*Verification:* `python3 tools/publish_gate.py` exits `0`.

### R2 — No account-specific identifier may be hard-coded (blocking)

AWS account ids, Connect instance ids, contact flow ids, distribution ids,
claimed phone numbers, hosted-zone names, personal domains and email addresses.
These belong in CloudFormation parameters, environment variables, or documented
placeholders such as `<ACCOUNT_ID>` and `__DEMO_DOMAIN__`.

*Verification:* the gate's identifier and deny-list checks report nothing.
Conventional documentation values (`123456789012`) are allowlisted in the gate.

### R3 — Operational state stays out of the repository (blocking)

Rollback snapshots, live service dumps, `node_modules`, archives and local
patches are not source. They must be matched by `.gitignore`. Reproduce them from
AWS instead of publishing them.

*Verification:* `.gitignore` excludes `node_modules`, `backups` and `.env`; the
gate scans only what git would publish.

### R4 — What is published must redeploy in a clean account (blocking)

Every asset a page references must exist, be shipped under `iac/web/`, and be
uploaded by `post-deploy.sh`. Templates must be deployable as documented. A
placeholder that the deploy script substitutes must survive in the shipped copy.

*Verification:* the gate's redeployability check, plus
`tests/test_deployment_readiness.py`.

### R5 — Secrets are generated, never authored (blocking)

Values such as the API origin secret are supplied at deploy time as `NoEcho`
CloudFormation parameters. Never commit a default, an example with a real value,
or a "temporary" literal.

*Verification:* R1 plus review that new parameters carrying secrets set
`NoEcho: true`.

### R6 — A tester-facing surface must not leak internals (blocking)

Model identity, flow ids, and engine names must not reach the browser or the
public issue mirror. This is a product requirement here — the demo deliberately
avoids naming models to its audience.

*Verification:* `tests/test_web_landing_ui.py` asserts the cost logic names no
engine; `tests/test_feedback.py` asserts the GitHub mirror leaks no model name.

### R7 — The gate runs before publish, and its failures are not narrated away
(blocking)

The gate is the authority, run locally before every publish. If it fails, the
correct action is to fix the finding
or add an allowlist entry **with a written justification** in
`tools/publish_gate.py`. Do not weaken a pattern to make output green.

*Verification:* `tests/test_publish_gate.py` proves the gate still detects a
planted secret, so a future edit cannot silently disable it.

## Human in the loop

Consistent with the AI-DLC tenet, the agent proposes and the human approves.
Deleting history, rotating a live secret, force-pushing, or making a repository
public are decisions for the human. The agent's job is to make the risk visible
and the remedy obvious.

## If a secret was already committed

Sanitising the working tree is not enough — git history retains it.

1. Rotate the credential in AWS first; treat it as compromised.
2. Then decide on history: rewrite (`git filter-repo`) or start a fresh
   repository with no history.
3. Only then publish.

For this project the origin secret was never committed (it existed only in the
untracked `backups/` tree, now gitignored), so rotation was not forced. Verify
that claim with `git log -p -S'<secret>'` before trusting it on any other repo.
