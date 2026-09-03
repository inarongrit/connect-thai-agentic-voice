#!/usr/bin/env python3
"""Measure Thai slot capture for the bank collections rules.

Runs every utterance in spike/acxd/collections_rules.json against a target and
reports pass/fail per rule. Two targets exist:

  engine  the deterministic Lambda engine in lambda/mantle_dialogue.py. This is the
          CONTROL. It is expected to score 100 percent, because the corpora were
          derived from its regression suite.

  acxd    an agentic CX designer application. Requires a workspace, application and
          alias id, which can only be created in the designer console or through the
          ACXD REST API with an admin-generated API key. Until those exist this target
          reports that it is unavailable rather than inventing a score.

The point of the control is that any ACXD number is meaningless on its own. A Thai
date validator that rejects 32 ธันวาคม is only interesting if it also accepts
วันที่ 29 กุมภาพันธ์, and the control proves the corpus contains both.

Usage:
    python3 spike/acxd/compare_slot_capture.py
    python3 spike/acxd/compare_slot_capture.py --target acxd \
        --workspace-id W --application-id A --alias-id AL
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = Path(__file__).resolve().parent / "collections_rules.json"


def load_engine():
    """Import the live dialogue engine with the assistance programme enabled."""
    os.environ.setdefault("ASSISTANCE_PROGRAM", "true")
    spec = importlib.util.spec_from_file_location(
        "mantle_dialogue_spike", ROOT / "lambda" / "mantle_dialogue.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flatten(group):
    """Collect utterance strings from a rules subtree, ignoring $comment keys."""
    found = []
    if isinstance(group, str):
        return [group]
    if isinstance(group, list):
        for item in group:
            found.extend(_flatten(item))
        return found
    if isinstance(group, dict):
        for key, value in group.items():
            if key.startswith("$"):
                continue
            found.extend(_flatten(value))
    return found


class EngineTarget:
    """The deterministic engine, used as the control."""

    name = "engine"
    available = True

    def __init__(self):
        self.module = load_engine()

    def date_rejected(self, utterance):
        module = self.module
        return bool(module._impossible_date(utterance) or module._unusable_date(utterance))

    def amount_rejected(self, utterance, balance):
        state = {"amount": balance}
        return self.module._implausible_amount(state, utterance) is not None

    def assistance_plan(self, utterance):
        state = {"scenario": "bank", "identityConfirmed": "true",
                 "stage": "assistance_options", "amount": "15500"}
        self.module._handle_assistance_options(state, utterance)
        return state.get("assistancePlan")

    def asks_for_human(self, utterance):
        state = {"scenario": "bank", "identityConfirmed": "true",
                 "stage": "assistance_options", "amount": "15500"}
        self.module._handle_assistance_options(state, utterance)
        return state.get("outcomeType")

    def first_hardship_reply(self):
        state = {"scenario": "bank", "identityConfirmed": "true",
                 "stage": "payment_type", "amount": "15500"}
        result = self.module._apply_signal(state, "hardship", "bank")
        return (result or {}).get("message", "")


class AcxdTarget:
    """An agentic CX designer application.

    Left deliberately unimplemented rather than stubbed with plausible numbers. The
    flow block type is confirmed to exist on this instance as
    ConnectParticipantWithAgenticCX, but invoking an application needs real workspace,
    application and alias ids.
    """

    name = "acxd"

    def __init__(self, workspace_id=None, application_id=None, alias_id=None):
        self.ids = (workspace_id, application_id, alias_id)
        self.available = all(self.ids)
        self.reason = (
            "no workspace/application/alias id supplied — create an application in the "
            "agentic CX designer console, or via the ACXD REST API with an "
            "admin-generated API key, then re-run with --workspace-id/--application-id/--alias-id"
        )

    def _unsupported(self, *_args, **_kwargs):
        raise NotImplementedError(
            "Driving a live ACXD application requires a text or voice session against "
            "the deployed alias. Wire that here once an application exists."
        )

    date_rejected = amount_rejected = assistance_plan = _unsupported
    asks_for_human = first_hardship_reply = _unsupported


def evaluate(target, rules):
    """Return per-rule results for a target that is available."""
    results = {}
    date_rule = rules["rules"]["payment_date_capture"]
    reject = _flatten(date_rule["must_reject"])
    accept = _flatten(date_rule["must_accept"])
    results["payment_date_capture"] = {
        "must_reject": [(u, target.date_rejected(u)) for u in reject],
        "must_accept": [(u, not target.date_rejected(u)) for u in accept],
    }

    amount_rule = rules["rules"]["payment_amount_capture"]
    balance = amount_rule["balance_for_examples"]
    results["payment_amount_capture"] = {
        "must_reject": [(u, target.amount_rejected(u, balance))
                        for u in _flatten(amount_rule["must_reject"])],
        "must_accept": [(u, not target.amount_rejected(u, balance))
                        for u in amount_rule["must_accept"]],
    }

    assistance = rules["rules"]["assistance_option_choice"]
    results["assistance_option_choice"] = {
        "plan_match": [(u, target.assistance_plan(u) == expected)
                       for u, expected in assistance["utterance_to_plan"].items()],
        "escape_hatch": [(u, target.asks_for_human(u) == assistance["escape_hatch"]["outcome"])
                         for u in assistance["escape_hatch"]["utterances"]],
    }

    reply = target.first_hardship_reply()
    results["first_hardship_reply"] = {
        "offers_every_relief_option": [
            (option, option in reply) for option in assistance["choices_th"]],
        "avoids_payment_only_menu": [
            ("ขอเลื่อนการชำระออกไป", "ขอเลื่อนการชำระออกไป" not in reply)],
    }
    return results


def report(target_name, results):
    total_pass = total = 0
    lines = [f"target: {target_name}", "=" * 68]
    for rule, checks in results.items():
        lines.append(rule)
        for check_name, outcomes in checks.items():
            passed = sum(1 for _, ok in outcomes if ok)
            total_pass += passed
            total += len(outcomes)
            mark = "ok " if passed == len(outcomes) else "FAIL"
            lines.append(f"  [{mark}] {check_name}: {passed}/{len(outcomes)}")
            for utterance, ok in outcomes:
                if not ok:
                    lines.append(f"         MISMATCH: {utterance}")
        lines.append("")
    lines.append(f"TOTAL: {total_pass}/{total}")
    return "\n".join(lines), total_pass, total


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=("engine", "acxd"), default="engine")
    parser.add_argument("--workspace-id")
    parser.add_argument("--application-id")
    parser.add_argument("--alias-id")
    args = parser.parse_args()

    rules = json.loads(RULES_PATH.read_text())
    if args.target == "engine":
        target = EngineTarget()
    else:
        target = AcxdTarget(args.workspace_id, args.application_id, args.alias_id)

    if not target.available:
        print(f"target: {target.name}\nUNAVAILABLE — {target.reason}")
        return 2

    text, passed, total = report(target.name, evaluate(target, rules))
    print(text)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
