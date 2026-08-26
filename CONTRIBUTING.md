# Contributing

## Before you push

```bash
python3 -m unittest discover -s tests -p 'test_*.py'   # full suite must pass
python3 tools/publish_gate.py                          # must exit 0
```

CI runs both on every pull request. A red gate blocks the merge.

## Ground rules

1. **Pin behaviour with a test.** Every behavioural claim in this repository is
   asserted somewhere under `tests/`. If you change behaviour, change the test in
   the same commit; if you add behaviour, add a test.
2. **Measure, do not estimate.** Cost rates and token volumes come from this
   account's metered usage, and the tool that uses them prints their provenance
   (`python3 tools/cost_per_call.py --show-sources`). Do not substitute a
   remembered price.
3. **Parameterise anything account-specific.** See `SECURITY.md`.
4. **Keep one source of truth.** `web/` is the only copy of the web assets;
   `post-deploy.sh` uploads from it. CloudFormation carries Lambda source inline
   and tests assert it matches `lambda/`, so edit `lambda/` and re-sync.
5. **Do not weaken a check to make output green.** Fix the finding, or add an
   allowlist entry with a written justification.

## Method

This project follows [AI-DLC](https://github.com/awslabs/aidlc-workflows),
retrofitted after the fact. The publish-readiness extension in
`.aidlc-rule-details/extensions/security/publish-readiness/` is **always enforced**
and blocking. `docs/aidlc-reverse-inception.md` records what applying the method
in reverse cost.

## Deploying your own copy

See `docs/implementation-notes.md`. You need an existing Amazon Connect instance
with agentic voice available and a claimed outbound-capable number. Both
CloudFormation templates exceed the 51,200-byte inline limit, so deploy with
`--s3-bucket`.
