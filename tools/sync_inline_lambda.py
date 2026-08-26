#!/usr/bin/env python3
"""Copy the Lambda sources and the contact flow into the templates that inline them.

Both templates carry their function source inline via `Code: ZipFile`, so the
templates and `lambda/*.py` must agree exactly. `tests/test_deployment_readiness.py`
asserts that agreement and fails the suite when it drifts, but nothing performed
the copy -- every edit to a Lambda meant re-indenting the source into YAML by hand,
which is tedious and easy to get subtly wrong.

This is the writer for that invariant: edit `lambda/*.py`, run this, and the
templates follow. The test remains the authority; this only makes it satisfiable.

    python3 tools/sync_inline_lambda.py            # apply
    python3 tools/sync_inline_lambda.py --check     # report drift, change nothing

`--check` exits non-zero when a template is stale, so it can gate a publish the
same way the tests do.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IAC = ROOT / "iac"
INDENT = " " * 10
PREFIX = "      Code:\n        ZipFile: |\n"

# (template, source, resource the block belongs to, resource that follows it).
# Kept identical to the CASES in tests/test_deployment_readiness.py; the following
# resource is the terminator because YAML block scalars have no closing token.
CASES = (
    ("template.yaml", "lambda/session_context.py",
     "  SessionContextFunction:", "  SessionContextPermission:"),
    ("mantle-template.yaml", "lambda/mantle_dialogue.py",
     "  MantleDialogueFunction:", "  MantleLexRole:"),
)

# The contact flow is inlined the same way but as one compact JSON line inside a
# !Sub block, so it needs its own handling. Nothing checked this copy before, which
# meant the readable iac/mantle-flow.json could silently disagree with what actually
# deploys -- the more dangerous of the two drifts, because the flow is the behaviour.
FLOW_CASES = (
    ("mantle-template.yaml", "mantle-agent-whisper-flow.json",
     "  MantleAgentWhisperFlow:", "  MantleQueueFlow:"),
    ("mantle-template.yaml", "mantle-queue-flow.json",
     "  MantleQueueFlow:", "  MantleContactFlow:"),
    ("mantle-template.yaml", "mantle-flow.json",
     "  MantleContactFlow:", "Outputs:"),
)
FLOW_PREFIX = "      Content: !Sub |\n"
FLOW_INDENT = " " * 8


def sync_flow(check, case):
    """Render one flow JSON into the template as a single compact line."""
    template, source, start, following = case
    path = IAC / template
    text = path.read_text()
    begin = text.index(start)
    code_start = text.index(FLOW_PREFIX, begin) + len(FLOW_PREFIX)
    code_end = text.index("\n" + following, code_start)
    flow = json.loads((IAC / source).read_text())
    desired = FLOW_INDENT + json.dumps(flow, ensure_ascii=False, separators=(",", ":"))
    if text[code_start:code_end] == desired:
        print(f"  in sync   {template} <- {source}")
        return False
    if check:
        print(f"  STALE     {template} <- {source}")
        return True
    path.write_text(text[:code_start] + desired + text[code_end:])
    print(f"  updated   {template} <- {source}")
    return True


def _bounds(text, start, following):
    """Locate the inline code block for one resource."""
    begin = text.index(start)
    code_start = text.index(PREFIX, begin) + len(PREFIX)
    code_end = text.index("\n" + following, code_start)
    return code_start, code_end


def _extract(block_text):
    """Recover the source from a YAML block, exactly as the test does.

    The test treats a truly blank line and a line of ten spaces as equivalent, so
    equality must be judged the same way here. Comparing raw bytes instead would
    make this tool rewrite whitespace in templates that are already correct, which
    produces diffs that look like changes but are not.
    """
    return "\n".join(
        line[10:] if line.startswith(INDENT) else line
        for line in block_text.rstrip("\n").splitlines()
    ) + "\n"


def _render(source_text):
    """Indent the source into the YAML block, leaving blank lines truly blank.

    Trailing whitespace on an otherwise empty line is legal YAML but shows up as a
    diff against the source when the test strips the indent back off, so blank
    lines must stay empty rather than becoming ten spaces.
    """
    lines = source_text.rstrip("\n").split("\n")
    return "\n".join(INDENT + line if line else "" for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report drift without writing")
    args = parser.parse_args()

    stale = []
    for template, source, start, following in CASES:
        path = IAC / template
        text = path.read_text()
        code_start, code_end = _bounds(text, start, following)
        source_text = (ROOT / source).read_text()
        desired = _render(source_text)
        if _extract(text[code_start:code_end]) == source_text:
            print(f"  in sync   {template} <- {source}")
            continue
        stale.append(template)
        if args.check:
            print(f"  STALE     {template} <- {source}")
            continue
        path.write_text(text[:code_start] + desired + text[code_end:])
        print(f"  updated   {template} <- {source}")

    for case in FLOW_CASES:
        if sync_flow(args.check, case) and args.check:
            stale.append(case[1])

    if args.check and stale:
        print(f"\n{len(stale)} template(s) stale. Run: python3 tools/sync_inline_lambda.py")
        return 1
    print("\nall inline Lambda sources match." if not stale else "\ntemplates synced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
