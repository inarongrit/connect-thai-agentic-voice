"""Tests for the publish-readiness gate.

The gate is only worth having if it still fails when something is wrong, so these
tests plant violations in a temporary tree and assert each one is caught. Without
this, a future edit could weaken a pattern and the gate would report PASS on an
unsafe repository — the exact failure mode rule R7 forbids.
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("publish_gate", ROOT / "tools" / "publish_gate.py")
GATE = importlib.util.module_from_spec(SPEC)
sys.modules["publish_gate"] = GATE
SPEC.loader.exec_module(GATE)


# Fixture values are assembled at runtime so this file contains no literal
# secret or live identifier. Writing them out would make the gate flag its own
# test suite, and allowlisting the suite would create a place for a real secret
# to hide.
FAKE_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_PRIVATE_KEY = "-----BEGIN RSA PRIVATE" + " KEY-----"
FAKE_GH_TOKEN = "ghp" + "_" + "a" * 30
LIVE_ACCOUNT_ID = "9017" + "17345697"
LIVE_PHONE = "+1833" + "8519388"
SAMPLE_DISTRIBUTION_HOST = "e2abc3def4ghi" + "." + "cloudfront.net"
LIVE_ZONE = "people" + ".aws.dev"
LIVE_EMAIL_DOMAIN = "@" + "amazon.com"


class DetectionTests(unittest.TestCase):
    """Each planted violation must produce a finding."""

    def check(self, name: str, body: str) -> list[str]:
        return GATE.check_file(_written(self, name, body), name)

    def test_aws_access_key_is_detected(self):
        findings = self.check("leak.py", f'KEY = "{FAKE_ACCESS_KEY}"\n')
        self.assertTrue(any("access key" in f for f in findings), findings)

    def test_private_key_is_detected(self):
        findings = self.check("id_rsa.txt", FAKE_PRIVATE_KEY + "\nabc\n")
        self.assertTrue(any("private key" in f for f in findings), findings)

    def test_github_token_is_detected(self):
        findings = self.check("ci.yaml", f"token: {FAKE_GH_TOKEN}\n")
        self.assertTrue(any("GitHub" in f for f in findings), findings)

    def test_origin_secret_style_hex_is_detected(self):
        findings = self.check("conf.json", '{"ORIGIN_SECRET": "%s"}' % ("3d19" * 16))
        self.assertTrue(any("high-entropy" in f for f in findings), findings)

    def test_real_account_id_is_detected_but_placeholder_is_not(self):
        self.assertTrue(any("account id" in f for f in self.check("a.md", f"arn:aws:sns:us-west-2:{LIVE_ACCOUNT_ID}:t")))
        self.assertFalse(any("account id" in f for f in self.check("b.md", "arn:aws:sns:us-west-2:123456789012:t")))

    def test_claimed_phone_is_detected_but_example_is_not(self):
        self.assertTrue(any("phone" in f for f in self.check("c.md", f"call {LIVE_PHONE} now")))
        self.assertFalse(any("phone" in f for f in self.check("d.md", "call +15551230000 now")))

    def test_cloudfront_hostname_is_detected(self):
        findings = self.check("e.md", f"https://{SAMPLE_DISTRIBUTION_HOST}/")
        self.assertTrue(any("CloudFront" in f for f in findings), findings)

    def test_environment_specific_strings_are_detected(self):
        self.assertTrue(any("environment-specific" in f
                            for f in self.check("f.md", f"host connect-demo.example.{LIVE_ZONE}")))
        self.assertTrue(any("environment-specific" in f
                            for f in self.check("g.md", f"mail someone{LIVE_EMAIL_DOMAIN}")))

    def test_clean_content_produces_no_findings(self):
        body = "Deploy with <ACCOUNT_ID> and __DEMO_DOMAIN__, example +15551230000.\n"
        self.assertEqual(self.check("clean.md", body), [])


def _written(case: unittest.TestCase, name: str, body: str) -> Path:
    import tempfile
    directory = Path(tempfile.mkdtemp())
    case.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class RepoHygieneTests(unittest.TestCase):
    def test_gitignore_excludes_operational_state(self):
        self.assertEqual(GATE.check_repo_hygiene(), [])
        body = (ROOT / ".gitignore").read_text()
        for entry in ("node_modules", "backups", ".env"):
            self.assertIn(entry, body)

    def test_operational_snapshots_are_not_publishable(self):
        """backups/ holds live ARNs and the origin secret; it must stay local."""
        self.assertTrue((ROOT / "backups").is_dir(), "fixture: backups/ expected locally")
        published = {str(p.relative_to(ROOT)) for p in GATE.tracked_files()}
        self.assertFalse([p for p in published if p.startswith("backups/")],
                         "backups/ must never be publishable")
        self.assertFalse([p for p in published if "node_modules" in p],
                         "node_modules must never be publishable")


class RedeployabilityTests(unittest.TestCase):
    def test_redeploy_artifacts_are_present_and_wired(self):
        self.assertEqual(GATE.check_redeployability(), [])

    def test_qr_placeholder_survives_in_the_shipped_copy(self):
        self.assertIn("__DEMO_DOMAIN__", (ROOT / "web/qr.html").read_text())


class GateOutcomeTests(unittest.TestCase):
    def test_repository_currently_passes_the_gate(self):
        findings, scanned = GATE.run()
        self.assertGreater(scanned, 20, "gate scanned suspiciously few files")
        self.assertEqual(findings, [], f"repository is not publish-ready: {findings}")

    def test_gate_exits_zero_as_a_command(self):
        result = subprocess.run([sys.executable, "tools/publish_gate.py"],
                                cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)


class ExtensionContractTests(unittest.TestCase):
    """The AI-DLC extension must stay blocking and stay published."""

    RULES = ROOT / ".aidlc-rule-details/extensions/security/publish-readiness/publish-readiness.md"

    def test_extension_exists_and_is_always_enforced(self):
        self.assertTrue(self.RULES.is_file())
        opt_in = self.RULES.with_name("publish-readiness.opt-in.md")
        self.assertFalse(opt_in.exists(),
                         "an .opt-in.md file would make publish-readiness optional")

    def test_extension_declares_blocking_rules_with_verification(self):
        body = self.RULES.read_text()
        self.assertIn("always enforced", body)
        for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7"):
            self.assertIn(f"### {rule}", body)
        self.assertGreaterEqual(body.count("*Verification:*"), 7)

    def test_steering_points_at_the_single_source_of_truth(self):
        steering = ROOT / ".kiro/steering/publish-readiness.md"
        self.assertTrue(steering.is_file())
        body = steering.read_text()
        self.assertIn(".aidlc-rule-details/extensions/security/publish-readiness", body)
        self.assertIn("tools/publish_gate.py", body)
        # No duplication tenet: steering must not restate the rules.
        self.assertNotIn("### R1", body)

    def test_extension_and_steering_are_publishable(self):
        published = {str(p.relative_to(ROOT)) for p in GATE.tracked_files()}
        self.assertIn(".aidlc-rule-details/extensions/security/publish-readiness/publish-readiness.md",
                      published)
        self.assertIn(".kiro/steering/publish-readiness.md", published)


if __name__ == "__main__":
    unittest.main()
