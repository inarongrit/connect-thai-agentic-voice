---
inclusion: always
name: publish-readiness
description: Blocking publish-readiness gate for this repository. Enforced on any change that creates, modifies or publishes repository content.
---

This project ships to a public repository from a live AWS account, so
publish-readiness is a blocking constraint, not a review preference.

Read and enforce the rules in
`.aidlc-rule-details/extensions/security/publish-readiness/publish-readiness.md`.
That file is the single source of truth; do not restate its rules here.

Before reporting any repository change complete, run:

```bash
python3 tools/publish_gate.py
```

If it exits non-zero, the change is not complete. Fix the finding, or add an
allowlist entry with a written justification. Never weaken a detection pattern to
turn the output green.

Rotating a live secret, rewriting git history, force-pushing, or making a
repository public require explicit human approval.
