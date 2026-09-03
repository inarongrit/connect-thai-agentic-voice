"""Live Sync multimodal relief-options spike: contract and security tests."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WEB = ROOT / "web"
SPIKE = ROOT / "spike" / "acxd"
SPEC = json.loads((SPIKE / "live_sync_relief.json").read_text())
RULES = json.loads((SPIKE / "collections_rules.json").read_text())
HTML = (WEB / "livesync.html").read_text()
CLIENT = (WEB / "touchpoint-client.js").read_text()
PACKAGE = json.loads((WEB / "package.json").read_text())


class LiveSyncContractTests(unittest.TestCase):
    def test_the_visual_options_match_the_deterministic_collections_policy(self):
        expected = RULES["rules"]["assistance_option_choice"]["plan_ids"]
        actual = {item["id"]: item["labelTh"] for item in SPEC["options"]}
        self.assertEqual(actual, expected)
        for option_id, label in actual.items():
            self.assertIn(f'data-option="{option_id}"', HTML)
            self.assertIn(label, HTML)
            self.assertIn(option_id, CLIENT)

    def test_action_schema_allows_only_the_three_approved_ids(self):
        enum = SPEC["flow"]["customAction"]["schema"]["properties"]["option"]["enum"]
        self.assertEqual(enum, ["reduce_installment", "principal_holiday", "extend_term"])
        self.assertIn("const ALLOWED = new Set", CLIENT)
        self.assertIn("Live Sync returned an unapproved relief option", CLIENT)

    def test_live_sync_does_not_replace_the_working_dialogue_engine(self):
        self.assertFalse(SPEC["productionDialogueReplacement"])
        self.assertEqual(SPEC["frontend"]["mode"], "external")
        self.assertIn('input: "external"', CLIENT)
        self.assertIn("opens no chat or voice contact", CLIENT)

    def test_tap_is_sent_back_into_the_same_conversation(self):
        self.assertIn("conversationHandler?.sendText", CLIENT)
        self.assertIn("ฉันเลือก${selected.labelTh}ค่ะ", CLIENT)
        self.assertIn("select_relief_option", CLIENT)

    def test_approval_language_is_forbidden_and_disclosure_is_visible(self):
        self.assertIn(SPEC["flow"]["requiredDisclosure"], HTML)
        # The page may name a forbidden phrase only inside an explicit warning; it must
        # not claim approval. The agent-side contract itself contains none of them.
        self.assertIn("AI จะไม่กล่าวว่า", HTML)
        for phrase in SPEC["flow"]["forbiddenClaims"]:
            self.assertNotIn(phrase, CLIENT)


class LiveSyncCredentialSafetyTests(unittest.TestCase):
    UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")

    def test_no_account_specific_id_or_key_is_in_the_publishable_sources(self):
        for path in (WEB / "livesync.html", WEB / "touchpoint-client.js",
                     SPIKE / "live_sync_relief.json"):
            text = path.read_text()
            self.assertIsNone(self.UUID.search(text), f"resource id leaked in {path}")
            self.assertNotIn("acxd_live_", text, f"admin key leaked in {path}")

    def test_credentials_are_runtime_password_fields_and_are_never_persisted(self):
        self.assertIn('id="deployment-key" type="password"', HTML)
        self.assertIn('id="api-key" type="password"', HTML)
        self.assertIn('autocomplete="new-password"', HTML)
        scripts = "\n".join(re.findall(r"<script(?: [^>]*)?>(.*?)</script>", HTML, re.S))
        executable = scripts + "\n" + CLIENT
        for storage in ("localStorage", "sessionStorage", "indexedDB"):
            # The security notice names storage APIs in prose; what is forbidden is an
            # executable call/property access that persists the credential.
            self.assertIsNone(re.search(rf"\b{storage}\s*[.(]", executable))
        self.assertIn("form.reset()", HTML)

    def test_external_mode_requires_all_three_connection_values(self):
        self.assertIn("deploymentKey, apiKey, and contactId are required", CLIENT)
        self.assertEqual(SPEC["frontend"]["connectionRequirements"],
                         ["deploymentKey", "apiKey", "contactId"])


class LiveSyncBuildTests(unittest.TestCase):
    def test_touchpoint_and_acxd_sdk_versions_are_pinned(self):
        self.assertEqual(PACKAGE["dependencies"]["@amazon-connect-touchpoint/web"], "1.0.0")
        self.assertEqual(PACKAGE["devDependencies"]["amazon-connect-acxd-sdk"], "0.1.0")

    def test_touchpoint_bundle_exists_and_exports_the_action_contract(self):
        bundle = WEB / "touchpoint.bundle.js"
        self.assertTrue(bundle.is_file())
        self.assertGreater(bundle.stat().st_size, 1_000_000)
        text = bundle.read_text(errors="ignore")
        self.assertIn("select_relief_option", text)
        self.assertIn("reduce_installment", text)

    def test_the_page_clearly_labels_runtime_language_risk(self):
        self.assertEqual(SPEC["runtime"]["managedLanguagePersistedByService"], "en-US")
        self.assertTrue(SPEC["runtime"]["thaiUxRetained"])
        self.assertIn("THAI UX ON MANAGED EN-US RUNTIME", HTML)

    def test_script_failure_is_not_hidden_or_put_on_the_critical_path(self):
        self.assertEqual(SPEC["script"]["buildStatus"], "BUILT")
        self.assertEqual(SPEC["script"]["deploymentStatus"], "failed")
        self.assertIn("not on the critical path", SPEC["script"]["note"])


if __name__ == "__main__":
    unittest.main()
