"""Deployment readiness for a clean-account replication.

These tests exist because `web/cost.js` was added to the pages but not to the
IaC asset set or the upload script, so a fresh deployment would have served the
pages with a 404 script and silently lost the cost estimate. The checks below are
generic: any future asset referenced by a page must also be shipped and uploaded.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WEB = ROOT / "web"
IAC = ROOT / "iac"
POST_DEPLOY = (IAC / "post-deploy.sh").read_text()

PAGES = ("index.html", "call.html", "qr.html")


def referenced_local_scripts(page: str) -> set[str]:
    """Local script filenames a page loads, ignoring version query strings."""
    html = (WEB / page).read_text()
    found = set()
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html):
        if src.startswith(("http://", "https://", "//")):
            continue
        found.add(src.split("?", 1)[0].lstrip("./"))
    return found


class WebAssetShippingTests(unittest.TestCase):
    def test_every_referenced_script_exists_in_web(self):
        for page in PAGES:
            for asset in referenced_local_scripts(page):
                with self.subTest(page=page, asset=asset):
                    self.assertTrue((WEB / asset).is_file(), f"{page} loads missing {asset}")

    def test_web_is_the_single_source_of_truth(self):
        """iac/web/ was a duplicate deploy copy and caused a shipped 404."""
        self.assertFalse((IAC / "web").exists(),
                         "iac/web/ must not come back; post-deploy.sh uploads from web/")
        self.assertIn('WEB_DIR="$REPO_ROOT/web"', POST_DEPLOY)

    def test_every_referenced_script_is_uploaded_by_post_deploy(self):
        for page in PAGES:
            for asset in referenced_local_scripts(page):
                with self.subTest(page=page, asset=asset):
                    self.assertIn(asset, POST_DEPLOY,
                                  f"post-deploy.sh never uploads {asset}")

    def test_pages_themselves_are_uploaded(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.assertTrue((WEB / page).is_file())
                self.assertIn(page, POST_DEPLOY)

    def test_qr_page_stays_templated_for_a_fresh_stack(self):
        self.assertIn("__DEMO_DOMAIN__", (WEB / "qr.html").read_text())
        self.assertIn("s|__DEMO_DOMAIN__|$DOMAIN|g", POST_DEPLOY)

    def test_post_deploy_uploads_from_the_single_web_directory(self):
        """Guards the assumption the other tests rely on."""
        self.assertIn('REPO_ROOT="$(cd "$BASE_DIR/.." && pwd)"', POST_DEPLOY)
        for line in POST_DEPLOY.splitlines():
            if "s3 cp" in line and "$WEB_DIR" in line:
                with self.subTest(line=line.strip()[:70]):
                    self.assertIn('"$WEB_DIR/', line)

    def test_uploaded_scripts_declare_a_javascript_content_type(self):
        for line in POST_DEPLOY.splitlines():
            if "s3 cp" in line and ".js" in line:
                with self.subTest(line=line.strip()[:70]):
                    self.assertIn("application/javascript", line)


class ManagedPromptShippingTests(unittest.TestCase):
    def test_post_deploy_creates_an_agent_for_every_scenario_prompt(self):
        for prompt in ("ai-prompt-bank.json", "ai-prompt-insurance.json", "ai-prompt-broker.json"):
            with self.subTest(prompt=prompt):
                self.assertTrue((IAC / prompt).is_file())
                self.assertIn(prompt, POST_DEPLOY)

    def test_shipped_prompts_are_valid_json_with_a_template(self):
        key = "textFullAIPromptEditTemplateConfiguration"
        for prompt in ("ai-prompt-bank.json", "ai-prompt-insurance.json", "ai-prompt-broker.json"):
            with self.subTest(prompt=prompt):
                data = json.loads((IAC / prompt).read_text())
                self.assertIn("text", data[key])
                self.assertGreater(len(data[key]["text"]), 500)


class InlineLambdaParityTests(unittest.TestCase):
    """CloudFormation carries Lambda source inline, so it must match the sources."""

    CASES = (
        ("template.yaml", "lambda/session_context.py",
         "  SessionContextFunction:", "  SessionContextPermission:"),
        ("mantle-template.yaml", "lambda/mantle_dialogue.py",
         "  MantleDialogueFunction:", "  MantleLexRole:"),
    )

    def test_inline_sources_match_the_repository_sources(self):
        for template, source, start, following in self.CASES:
            with self.subTest(template=template):
                text = (IAC / template).read_text()
                begin = text.index(start)
                prefix = "      Code:\n        ZipFile: |\n"
                code_start = text.index(prefix, begin) + len(prefix)
                code_end = text.index("\n" + following, code_start)
                block = text[code_start:code_end].rstrip("\n")
                extracted = "\n".join(
                    line[10:] if line.startswith(" " * 10) else line
                    for line in block.splitlines()
                ) + "\n"
                self.assertEqual(extracted, (ROOT / source).read_text(),
                                 f"{template} inline code has drifted from {source}")


if __name__ == "__main__":
    unittest.main()
