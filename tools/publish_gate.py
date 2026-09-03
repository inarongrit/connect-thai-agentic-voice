#!/usr/bin/env python3
"""Publish-readiness gate for this repository.

A blocking check, not a report. Run it before any push to a public remote:

    python3 tools/publish_gate.py            # fail on any BLOCKER
    python3 tools/publish_gate.py --verbose  # list every file scanned

It exists because this project was built against a live AWS account first and
published second, so live identifiers accumulated in the working tree. The gate
proves, mechanically, that what would be published contains no credential, no
account-specific identifier, and nothing that stops a clean-account redeploy.

Scope: files that git would actually publish. Anything matched by .gitignore is
out of scope, because it is never pushed — `backups/` in particular holds live
ARNs and the CloudFront origin secret and must stay local.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories never published, mirrored from .gitignore for the no-git fallback.
IGNORED_DIRS = {"node_modules", "backups", ".git", "__pycache__", ".aidlc",
                ".playwright-mcp", ".vscode", ".idea"}
# Binary or vendored assets whose content is not authored here.
SKIP_FILES = {"webrtc.bundle.js", "touchpoint.bundle.js", "package-lock.json"}
TEXT_SUFFIXES = {".py", ".js", ".json", ".yaml", ".yml", ".md", ".html", ".sh",
                 ".txt", ".toml", ".cfg", ".ini", ""}

# ── Credential patterns: any hit is a blocker ────────────────────────────────
CREDENTIAL_PATTERNS = [
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("AWS secret access key assignment", re.compile(r"aws_secret_access_key\s*[=:]", re.I)),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")),
    ("GitHub personal access token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    # Agentic CX Designer SDK keys are long-lived static credentials of the form
    # acxd_live_<prefix>.<secret>, generated in Admin Hub and shown only once. They
    # are not hex, so the entropy rule below would not reliably catch them.
    ("Agentic CX Designer SDK API key",
     re.compile(r"\bacxd_(?:live|test)_[A-Za-z0-9]{4,}\.[A-Za-z0-9_-]{12,}\b")),
    ("bearer token literal", re.compile(r"Authorization\s*:\s*['\"]?Bearer\s+[A-Za-z0-9._-]{20,}")),
    ("high-entropy hex secret (>=32 hex chars)", re.compile(r"\b[0-9a-f]{32,}\b")),
]

# ── Account-specific identifiers: publishable code must be parameterised ─────
# 12-digit ids that are conventional documentation placeholders are allowed.
ALLOWED_ACCOUNT_IDS = {"123456789012", "111122223333", "444455556666", "000000000000"}
IDENTIFIER_PATTERNS = [
    ("AWS account id", re.compile(r"\b\d{12}\b")),
    ("E.164 phone number", re.compile(r"\+\d{9,15}\b")),
    ("CloudFront distribution hostname", re.compile(r"\b[a-z0-9]{13,14}\.cloudfront\.net\b")),
]
# Example values that are safe to ship in docs and fixtures.
ALLOWED_PHONES = {"+15551230000", "+15551234567", "+66812345678", "+18000000000"}

# Personal or environment-specific strings that must never ship. Extend freely.
DENY_SUBSTRINGS = [
    "people.aws.dev",
    "@amazon.com",
    "amzn-",
    # Bare distribution id: the hostname pattern above misses it when the id and
    # ".cloudfront.net" are split across a concatenation, which is exactly how it
    # slipped into a test fixture. Naming it makes reintroduction fail.
    "drii8p37tf1qx",
]
# Files permitted to mention a denied substring, with justification.
DENY_ALLOWLIST: dict[str, set[str]] = {
    # The gate documents its own deny list, and the guideline explains why.
    "tools/publish_gate.py": {"people.aws.dev", "@amazon.com", "amzn-", "drii8p37tf1qx"},
    ".aidlc-rule-details/extensions/security/publish-readiness/publish-readiness.md": {"people.aws.dev", "@amazon.com"},
    # Standard Amazon open-source CoC reporting address: a published public
    # mailbox, not a personal or environment-specific identifier.
    "CODE_OF_CONDUCT.md": {"@amazon.com"},
    # SECURITY.md and CONTRIBUTING.md may reference AWS public reporting URLs.
    "SECURITY.md": {"@amazon.com"},
}

# High-entropy hex is common in legitimate places; allow those explicitly.
HEX_ALLOWLIST_SUFFIXES = {".png", ".jpg", ".ico"}
HEX_ALLOWLIST_FILES = {"webrtc.bundle.js", "package-lock.json"}

REQUIRED_GITIGNORE_ENTRIES = ["node_modules", "backups", ".env"]


def tracked_files(verbose: bool = False) -> list[Path]:
    """Files git would publish; falls back to a filtered walk outside a repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=60).stdout
        names = [n for n in out.splitlines() if n.strip()]
        if names:
            paths = [ROOT / n for n in names]
            # git ls-files at a nested prefix can return the whole parent repo;
            # keep only paths inside this project.
            return [p for p in paths if p.is_file() and ROOT in p.parents or p.parent == ROOT]
    except (subprocess.SubprocessError, FileNotFoundError):
        if verbose:
            print("git unavailable; falling back to filesystem walk")
    results = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        results.append(path)
    return results


def scannable(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    return path.suffix.lower() in TEXT_SUFFIXES


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def check_file(path: Path, relative: str) -> list[str]:
    findings: list[str] = []
    text = read(path)
    if not text:
        return findings

    for label, pattern in CREDENTIAL_PATTERNS:
        if label.startswith("high-entropy") and (
                path.suffix.lower() in HEX_ALLOWLIST_SUFFIXES or path.name in HEX_ALLOWLIST_FILES):
            continue
        for match in pattern.finditer(text):
            findings.append(f"{relative}: {label}: {match.group()[:24]}…")

    for label, pattern in IDENTIFIER_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group()
            if label == "AWS account id" and value in ALLOWED_ACCOUNT_IDS:
                continue
            if label == "E.164 phone number" and value in ALLOWED_PHONES:
                continue
            findings.append(f"{relative}: {label} not parameterised: {value}")

    allowed = DENY_ALLOWLIST.get(relative, set())
    for needle in DENY_SUBSTRINGS:
        if needle in text and needle not in allowed:
            findings.append(f"{relative}: environment-specific string: {needle}")
    return findings


def check_repo_hygiene() -> list[str]:
    findings = []
    gitignore = ROOT / ".gitignore"
    if not gitignore.is_file():
        return [".gitignore is missing; node_modules and backups would be published"]
    body = gitignore.read_text()
    for entry in REQUIRED_GITIGNORE_ENTRIES:
        if entry not in body:
            findings.append(f".gitignore does not exclude {entry}")
    return findings


def check_redeployability() -> list[str]:
    """A clean account must be able to redeploy from what is published."""
    findings = []
    required = [
        "README.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "iac/template.yaml",
        "iac/post-deploy.sh",
        "docs/implementation-notes.md",
        "iac/ai-prompt-bank.json",
        "iac/ai-prompt-insurance.json",
        "iac/ai-prompt-broker.json",
    ]
    for name in required:
        if not (ROOT / name).is_file():
            findings.append(f"redeploy artifact missing: {name}")

    post_deploy = ROOT / "iac/post-deploy.sh"
    if post_deploy.is_file():
        script = post_deploy.read_text()
        pages = ["index.html", "call.html", "qr.html"]
        for page in pages:
            page_path = ROOT / "web" / page
            if not page_path.is_file():
                continue
            for src in re.findall(r'<script[^>]+src="([^"]+)"', page_path.read_text()):
                if src.startswith(("http://", "https://", "//")):
                    continue
                asset = src.split("?", 1)[0].lstrip("./")
                if asset not in script:
                    findings.append(f"post-deploy.sh never uploads {asset} (referenced by {page})")
                if not (ROOT / "web" / asset).is_file():
                    findings.append(f"web/{asset} missing; a fresh deploy would 404")

    if (ROOT / "iac" / "web").exists():
        findings.append("iac/web/ reintroduced: web/ must stay the single source "
                        "of truth or assets drift out of the deploy script")

    qr = ROOT / "web/qr.html"
    if qr.is_file() and "__DEMO_DOMAIN__" not in qr.read_text():
        findings.append("web/qr.html lost its __DEMO_DOMAIN__ placeholder; "
                        "a fresh stack would advertise the wrong host")
    return findings


def run(verbose: bool = False) -> tuple[list[str], int]:
    findings = check_repo_hygiene() + check_redeployability()
    scanned = 0
    for path in tracked_files(verbose):
        try:
            relative = str(path.relative_to(ROOT))
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in Path(relative).parts):
            continue
        if not scannable(path):
            continue
        scanned += 1
        if verbose:
            print(f"scan {relative}")
        findings.extend(check_file(path, relative))
    return findings, scanned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block publishing until the repo is safe and repeatable.")
    parser.add_argument("--verbose", action="store_true", help="list each scanned file")
    args = parser.parse_args(argv)

    findings, scanned = run(args.verbose)
    print(f"publish gate: scanned {scanned} publishable file(s)")
    if findings:
        print(f"\nBLOCKED — {len(findings)} finding(s):\n")
        for item in sorted(set(findings)):
            print(f"  - {item}")
        print("\nResolve every finding, or add a justified entry to the allowlist in this file.")
        return 1
    print("PASS — no credentials, no unparameterised identifiers, redeploy artifacts intact.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
