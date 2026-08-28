import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
WEB = ROOT / "web" / "index.html"


class LandingPageFeedbackLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = WEB.read_text()

    def test_desktop_feedback_host_is_in_left_hero(self):
        hero_start = self.html.index('<section class="hero"')
        hero_end = self.html.index('</section>\n<section class="console"', hero_start)
        host = self.html.index('id="feedback-desktop"')
        self.assertLess(hero_start, host)
        self.assertLess(host, hero_end)
        self.assertIn('@media(min-width:981px)', self.html)
        self.assertIn('.hero.feedback-active>.desktop-feedback-host', self.html)

    def test_mobile_keeps_inline_feedback_outside_call_form(self):
        form_start = self.html.index('<form id="demo-form"')
        form_end = self.html.index('</form>', form_start)
        inline_host = self.html.index('id="feedback"', form_start)
        self.assertGreater(inline_host, form_end)
        self.assertIn('matchMedia("(min-width:981px)")', self.html)
        self.assertIn('feedbackDesktop:feedbackInline', self.html)

    def test_new_call_clears_both_feedback_hosts(self):
        self.assertIn(
            'function clearFeedback(){feedbackInline.replaceChildren();feedbackDesktop.replaceChildren();',
            self.html,
        )
        self.assertIn('if(mode==="webrtc"){clearFeedback();', self.html)


class LandingPageGlobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = WEB.read_text()

    def test_globe_uses_clean_original_layers_without_cloud_overlays(self):
        for forbidden in (
            'class="cloud-band"',
            'id="cloudBlur"',
            'class="terrain"',
            'class="border"',
            'class="landmass"',
        ):
            self.assertNotIn(forbidden, self.html)
        for marker in ('id="sphereGlow"', 'class="coast"', 'class="grid"'):
            self.assertIn(marker, self.html)

    def test_globe_retains_route_and_reduced_motion_support(self):
        self.assertGreaterEqual(self.html.count('class="route"'), 4)
        self.assertIn('@media(prefers-reduced-motion:reduce)', self.html)


class LandingPageThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = WEB.read_text()

    def test_accessible_rgb_theme_buttons_are_in_the_topbar(self):
        topbar_start = self.html.index('<header class="topbar">')
        topbar_end = self.html.index('</header>', topbar_start)
        controls = self.html[topbar_start:topbar_end]
        self.assertIn('class="theme-switcher" role="group" aria-label="เลือกโทนสี"', controls)
        for theme, label, initial in (
            ("red", "ใช้ธีมสีแดง", "false"),
            ("green", "ใช้ธีมสีเขียว", "false"),
            ("blue", "ใช้ธีมสีน้ำเงิน", "false"),
        ):
            self.assertIn(f'data-theme-choice="{theme}" aria-label="{label}" aria-pressed="{initial}"', controls)
        self.assertIn('id="theme-status" aria-live="polite"', controls)

    def test_all_three_css_theme_values_are_first_class(self):
        self.assertIn('html[data-theme="green-orbit"]', self.html)
        self.assertIn('html[data-theme="blue-orbit"]', self.html)
        self.assertIn('html[data-theme="red-orbit"]', self.html)
        self.assertIn('--mint:#36d7ff', self.html)
        self.assertIn('--mint:#ff5c70', self.html)
        self.assertIn('--mint:#6eebb5', self.html)

    def test_query_parameter_and_legacy_aliases_are_supported(self):
        self.assertIn('new URLSearchParams(location.search).get("theme")', self.html)
        for alias in ('r:"red"', '"red-orbit":"red"', 'g:"green"', '"green-orbit":"green"', 'b:"blue"', '"blue-orbit":"blue"'):
            self.assertIn(alias, self.html)

    def test_theme_persists_and_updates_shareable_url_without_reload(self):
        self.assertIn('localStorage.getItem("fsi-theme")', self.html)
        self.assertIn('localStorage.setItem("fsi-theme",theme)', self.html)
        self.assertIn('url.searchParams.set("theme",theme)', self.html)
        self.assertIn('history.replaceState(history.state,"",url)', self.html)
        self.assertNotIn('location.assign(url)', self.html)

    def test_theme_change_updates_meta_and_pressed_state(self):
        self.assertIn('document.documentElement.dataset.theme=theme+"-orbit"', self.html)
        self.assertIn('meta[name="theme-color"]', self.html)
        self.assertIn('button.setAttribute("aria-pressed",String(button.dataset.themeChoice===theme))', self.html)
        self.assertIn('window.FsiTheme={apply:theme=>applyTheme(theme),current:()=>window.__fsiTheme}', self.html)


class PostCallCostPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = WEB.read_text()
        cls.call_html = (ROOT / "web" / "call.html").read_text()
        cls.cost_js = (ROOT / "web" / "cost.js").read_text()

    def test_both_pages_load_the_shared_cost_asset(self):
        for page in (self.html, self.call_html):
            self.assertIn('<script src="cost.js?v=20260821-1"></script>', page)

    def test_exactly_one_telephony_line_per_channel(self):
        """A call is either WebRTC or PSTN, so both media lines must never appear."""
        for channel, label in (
            ("webrtc", "เสียง WebRTC"),
            ("pstn-th", "ค่าโทรออกไทย (PSTN)"),
            ("pstn-us", "ค่าโทรออกสหรัฐ (PSTN)"),
        ):
            self.assertIn(f'"{label}"' if '"' not in label else label, self.cost_js)
        self.assertIn('CHANNELS[key].label', self.cost_js)
        self.assertIn('CHANNELS[key].rate * minutes', self.cost_js)
        # One row per channel, chosen by key — not one row per possible channel.
        self.assertEqual(self.cost_js.count("CHANNELS[key].rate"), 1)

    def test_connect_ai_minutes_apply_to_every_channel(self):
        self.assertIn('RATES.aiMinute * minutes', self.cost_js)
        self.assertIn("aiMinute: 0.038", self.cost_js)

    def test_channel_rates_match_the_python_estimator(self):
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location(
            "cost_per_call_parity", ROOT / "tools" / "cost_per_call.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["cost_per_call_parity"] = module
        spec.loader.exec_module(module)
        self.assertIn(f'aiMinute: {module.VOICE_RATES["webrtc"][0]:g}', self.cost_js)
        self.assertIn(f'rate: {module.VOICE_RATES["webrtc"][1]:.3f}', self.cost_js)
        self.assertIn(f'rate: {module.VOICE_RATES["pstn-th"][1]:g}', self.cost_js)
        self.assertIn(f'rate: {module.VOICE_RATES["pstn-us"][1]:g}', self.cost_js)
        self.assertIn(f'contactLensPerMinute: {module.CONTACT_LENS_ASSUMED_PER_MIN:g}', self.cost_js)
        self.assertIn(f'serverlessPerCall: {module.SERVERLESS_ASSUMED_PER_CALL:g}', self.cost_js)

    def test_pstn_alias_and_unknown_channels_fall_back_safely(self):
        self.assertIn('if (channel === "pstn") return "pstn-th"', self.cost_js)
        self.assertIn('return CHANNELS[channel] ? channel : "webrtc"', self.cost_js)

    def test_each_page_passes_its_own_channel(self):
        self.assertIn('FsiCostPopup.show(elapsedSeconds,"webrtc")', self.html)
        self.assertIn('FsiCostPopup.show(Math.max(0,Math.floor((Date.now()-done.startedAt)/1000)),"pstn-th")',
                      self.call_html)

    def test_popup_is_dismissible_and_announced(self):
        for attribute in ('"aria-modal", "false"', '"aria-labelledby", "cost-pop-title"', '"aria-live", "polite"'):
            self.assertIn(attribute, self.cost_js)
        self.assertIn('"ปิดประมาณการค่าใช้จ่าย"', self.cost_js)
        self.assertIn('close.addEventListener("click", hide)', self.cost_js)
        self.assertIn('event.key === "Escape"', self.cost_js)

    def test_new_call_hides_any_previous_estimate(self):
        self.assertIn('if(mode==="webrtc"){clearFeedback();if(window.FsiCostPopup)FsiCostPopup.hide();', self.html)

    def test_dialogue_line_uses_the_measured_single_engine_rate(self):
        self.assertIn("dialoguePerCall: 0.00103", self.cost_js)

    def test_cost_logic_never_names_the_engine_to_testers(self):
        for leak in ("brainmode", "mantle", "managed", "haiku", "gpt"):
            self.assertNotIn(leak, self.cost_js.lower(), f"cost logic must not reference {leak}")

    def test_assumed_components_are_labelled_as_estimates(self):
        self.assertIn("Contact Lens (ประมาณการ)", self.cost_js)
        self.assertIn("Lambda และบริการรอบข้าง (ประมาณการ)", self.cost_js)
        self.assertIn("สมมติฐานการคิดราคา", self.cost_js)
        self.assertIn("ไม่คิดซ้อนกัน", self.cost_js)


class SingleEngineAndChannelOrderTests(unittest.TestCase):
    """Option A is the only engine offered, and WebRTC leads the channel choice."""

    @classmethod
    def setUpClass(cls):
        cls.html = WEB.read_text()
        cls.feedback_js = (ROOT / "web" / "feedback.js").read_text()
        cls.webrtc_js = (ROOT / "web" / "webrtc-client.js").read_text()

    def test_webrtc_channel_is_offered_before_pstn(self):
        group_start = self.html.index('<div class="mode-selector" role="group" aria-label="รูปแบบการสนทนา">')
        group_end = self.html.index("</div>", group_start)
        group = self.html[group_start:group_end]
        self.assertLess(group.index('data-mode="webrtc"'), group.index('data-mode="pstn"'))
        self.assertIn('data-mode="webrtc" aria-pressed="true"', group)
        self.assertIn('data-mode="pstn" aria-pressed="false"', group)

    def test_only_one_channel_selector_group_remains(self):
        self.assertEqual(self.html.count('class="mode-selector"'), 1)

    def test_option_selector_is_gone_from_the_page(self):
        for gone in ('brain-option', 'data-brain', 'ตัวเลือก A', 'ตัวเลือก B',
                     'ชุดสนทนาสำหรับทดสอบเปรียบเทียบ', 'A / B DIALOGUE TEST'):
            self.assertNotIn(gone, self.html, f"{gone} should no longer appear")

    def test_every_call_path_sends_option_a(self):
        self.assertIn('const BRAIN_MODE="mantle"', self.html)
        self.assertIn('brainMode:BRAIN_MODE,audioElement:meetingAudio', self.html)
        self.assertIn('scenario,brainMode:BRAIN_MODE})', self.html)
        self.assertEqual(self.html.count('brainMode:BRAIN_MODE'), 4)

    def test_shared_assets_default_to_option_a_and_drop_ab_labels(self):
        self.assertIn('brainMode || "mantle"', self.webrtc_js)
        self.assertNotIn('managed', self.webrtc_js)
        for gone in ('BRAINS', 'ตัวเลือก A', 'ตัวเลือก B'):
            self.assertNotIn(gone, self.feedback_js)

    def test_live_call_panel_shows_no_option_label(self):
        self.assertIn('callDetail.textContent="Thai voice AI · WebRTC · SUDA"', self.html)

    def test_bundle_matches_source_after_the_voice_lab_rebuild(self):
        """The bundle was rebuilt for the voice lab, so its defaults match source.

        Before the rebuild it still minified a stale `|| "managed"` default that
        source had already changed to `mantle`. Rebuilding removed the mismatch and
        compiled in the `request` override the voice lab posts. Rebuild with
        `npm run build:webrtc` after touching `webrtc-client.js`.
        """
        bundle = (ROOT / "web" / "webrtc.bundle.js").read_text()
        self.assertNotIn('brainMode:i||"managed"', bundle)
        self.assertIn('||"mantle"', bundle)
        # The voice lab depends on this override reaching the compiled bundle.
        self.assertIn('JSON.stringify(s||{mode:"webrtc"', bundle)
        self.assertNotIn("startCall({api:API,scenario,name:nameInput.value.trim(),audioElement",
                         self.html)
        self.assertIn("brainMode:BRAIN_MODE,audioElement", self.html)

    def test_slides_no_longer_claim_an_ab_comparison(self):
        for gone in ('Blind A/B', 'A/B blind', 'blind comparison', 'A/B evidence',
                     'Option B', 'B=prompt-instructed', 'LABEL=A / B only'):
            self.assertNotIn(gone, self.html, f"stale A/B narrative: {gone}")


if __name__ == "__main__":
    unittest.main()


class ReadmePricingTests(unittest.TestCase):
    """The README quotes costs, so it must say where they came from and what they are not.

    A number in a README is read as a quotation unless it says otherwise, and these are
    measurements of one account's past usage in one Region.
    """

    README = (Path(__file__).parents[1] / "README.md").read_text()

    def test_pricing_is_present(self):
        self.assertIn("## What it costs to run", self.README)

    def test_every_channel_is_costed(self):
        for channel in ("WebRTC", "US number", "Thai number"):
            with self.subTest(channel=channel):
                self.assertIn(channel, self.README)

    def test_the_figures_are_not_presented_as_a_quotation(self):
        self.assertIn("not a quotation", self.README)
        self.assertIn("calculator.aws", self.README)

    def test_the_standing_cost_is_stated_separately_from_usage(self):
        """Someone deciding whether to leave this deployed needs the idle figure."""
        self.assertIn("Standing cost", self.README)
        self.assertIn("toll-free", self.README)

    def test_unbilled_services_are_declared(self):
        """Contact Lens real-time was enabled after the measurement window."""
        self.assertIn("Not yet in these figures", self.README)
        self.assertIn("Contact Lens real-time", self.README)

    def test_the_reader_is_pointed_at_the_tool(self):
        self.assertIn("tools/cost_per_call.py --show-sources", self.README)


class RuntimeArchitectureSlideTests(unittest.TestCase):
    """Slide 5 explains what actually executes, not the tempting managed alternative.

    The Connect instance contains FSI AI prompts and AI agents, so a customer could
    reasonably assume those are the engine. They are not referenced by any flow. The
    slide makes that boundary the visual centre of the story and must not drift into a
    generic "AWS services" diagram that implies every resource is in the runtime path.
    """

    HTML = (Path(__file__).parents[1] / "web" / "index.html").read_text()
    START = HTML.index('<section class="slide runtime-slide"')
    END = HTML.index('</section></div><nav class="deck-nav"', START)
    SLIDE = HTML[START:END]

    def test_it_is_a_true_fifth_slide(self):
        # Six slides since the voice lab landed; the runtime architecture is still 05.
        self.assertEqual(self.HTML.count('<section class="slide'), 6)
        self.assertIn('"How It Actually Runs"', self.HTML)
        self.assertIn('content:"05"', self.HTML)

    def test_the_actual_engine_is_lambda(self):
        self.assertIn("ACTUAL ENGINE", self.SLIDE)
        self.assertIn("mantle_dialogue.py", self.SLIDE)
        self.assertIn("1,554 LOC", self.SLIDE)
        self.assertIn("67 FUNCTIONS", self.SLIDE)
        self.assertIn("367 TESTS", self.SLIDE)

    def test_prompts_are_explicitly_not_the_engine(self):
        self.assertIn("PROMPTS ARE NOT THE ENGINE.", self.SLIDE)
        self.assertIn("AI PROMPTS + AI AGENTS", self.SLIDE)
        self.assertIn("0</i> FLOW REFERENCES", self.SLIDE)
        self.assertIn("ไม่มี flow block ใดเรียกใช้", self.SLIDE)

    def test_bedrock_is_described_as_classification_not_authorship(self):
        self.assertIn("Classify, never author", self.SLIDE)
        self.assertIn("NO AUTHORSHIP", self.SLIDE)
        self.assertIn("โมเดลช่วย classify แต่ไม่แต่งคำตอบ", self.SLIDE)

    def test_each_interactive_node_has_a_complete_readout(self):
        import re
        nodes = re.findall(r'<button[^>]*data-runtime-node[^>]*>', self.SLIDE)
        self.assertEqual(len(nodes), 10)
        for node in nodes:
            with self.subTest(node=node[:80]):
                self.assertIn('type="button"', node)
                self.assertIn("data-title=", node)
                self.assertIn("data-copy=", node)
                self.assertIn("data-facts=", node)

    def test_readout_uses_text_content_for_untrusted_data(self):
        """No dataset value may become executable markup."""
        script = self.HTML[self.HTML.index("(function(){const map=document.querySelector"):
                           self.HTML.index("</script>", self.HTML.index("(function(){const map=document.querySelector"))]
        self.assertIn("title.textContent=node.dataset.title", script)
        self.assertIn("copy.textContent=node.dataset.copy", script)
        self.assertNotIn("innerHTML", script)

    def test_runtime_nodes_own_arrow_keys_instead_of_moving_the_deck(self):
        self.assertIn(".runtime-node,.runtime-core", self.HTML)
        self.assertIn('["ArrowDown","ArrowRight","ArrowUp","ArrowLeft"]', self.HTML)

    def test_glitch_effect_does_not_repeat_the_heading_for_screen_readers(self):
        """Pseudo-element text appeared as two extra headings in the first render."""
        self.assertEqual(self.SLIDE.count('id="runtime-title"'), 1)
        self.assertNotIn("data-glitch=", self.SLIDE)
        self.assertNotIn("content:attr(data-glitch)", self.HTML)

    def test_mobile_layout_has_an_explicit_single_column_fallback(self):
        self.assertIn("@media(max-width:560px){.runtime-head", self.HTML)
        self.assertIn(".runtime-services{grid-template-columns:1fr", self.HTML)

    def test_the_old_test_count_is_gone_from_the_deck(self):
        self.assertNotIn("357 regression tests", self.HTML)
        self.assertIn("367 regression tests", self.HTML)


class UnifiedCyberpunkThemeTests(unittest.TestCase):
    """Slides 1–4 and the PSTN page share slide 5's tactical visual language.

    This is intentionally tested as shared primitives rather than screenshots: a deck
    can look coherent today and quietly drift back to green rounded cards one component
    at a time. Cyan signal, magenta boundary, mono telemetry, scanlines and clipped
    corners are the vocabulary that holds the pages together.
    """

    ROOT = Path(__file__).parents[1]
    LANDING = (ROOT / "web" / "index.html").read_text()
    PSTN = (ROOT / "web" / "call.html").read_text()

    def test_both_pages_load_the_monospace_telemetry_face(self):
        for page in (self.LANDING, self.PSTN):
            with self.subTest(page="landing" if page is self.LANDING else "pstn"):
                self.assertIn("IBM+Plex+Mono", page)
                self.assertIn('"IBM Plex Mono",monospace', page)

    def test_slides_one_to_four_have_cyan_and_magenta_signal_tokens(self):
        self.assertIn("--cy-cyan:#5af5ff", self.LANDING)
        self.assertIn("--cy-pink:#ff4fd8", self.LANDING)
        self.assertIn(".slide:not(.runtime-slide)", self.LANDING)

    def test_slides_one_to_four_have_scanlines(self):
        self.assertIn(".slide:not(.runtime-slide)::after", self.LANDING)
        self.assertIn("repeating-linear-gradient(0deg", self.LANDING)

    def test_slide_one_console_uses_an_angular_tactical_frame(self):
        self.assertIn(".slide:first-child .console", self.LANDING)
        self.assertIn('content:"CONSOLE // PSTN + WEBRTC"', self.LANDING)
        self.assertIn("clip-path:polygon(18px 0", self.LANDING)

    def test_atlas_slides_use_the_same_hud_surface_and_corner_language(self):
        self.assertIn(".atlas-board{border-color:var(--cy-line)!important", self.LANDING)
        self.assertIn(".atlas-card{border-color:var(--cy-line)!important", self.LANDING)
        self.assertIn(".atlas-evidence{box-shadow:inset 3px 0 0 var(--cy-pink)", self.LANDING)

    def test_the_existing_rgb_theme_choice_still_changes_the_hud(self):
        """The redesign must not make the earlier theme selector decorative."""
        self.assertIn('html[data-theme="red-orbit"]{--cy-cyan:', self.LANDING)
        self.assertIn('html[data-theme="blue-orbit"]{--cy-cyan:', self.LANDING)

    def test_reduced_motion_still_disables_the_new_animations(self):
        for page in (self.LANDING, self.PSTN):
            self.assertIn("@media(prefers-reduced-motion:reduce)", page)
            self.assertIn("animation:none!important", page)

    def test_pstn_page_has_the_same_cyberpunk_tokens_and_scanlines(self):
        self.assertIn("--cy-cyan:#5af5ff", self.PSTN)
        self.assertIn("--cy-pink:#ff4fd8", self.PSTN)
        self.assertIn("body::before", self.PSTN)
        self.assertIn("PSTN SESSION // CONTACT TELEMETRY", self.PSTN)

    def test_pstn_call_path_is_visually_angular_not_a_generic_card(self):
        self.assertIn(".wave-shell", self.PSTN)
        self.assertIn("AUDIO SPECTRUM // ENCRYPTED", self.PSTN)
        self.assertIn("clip-path:polygon(12px 0", self.PSTN)

    def test_pstn_behavior_is_unchanged_by_the_theme(self):
        self.assertIn('const API="/call"', self.PSTN)
        self.assertIn('sessionStorage.getItem("fsiActiveCall")', self.PSTN)
        self.assertIn('setTimeout(poll,3500)', self.PSTN)
        self.assertIn('FsiCostPopup.show', self.PSTN)

    def test_both_pages_have_mobile_fallbacks(self):
        self.assertIn("@media(max-width:900px)", self.LANDING)
        self.assertIn("@media(max-width:620px)", self.PSTN)
        self.assertIn(".wave-shell{clip-path:none}", self.PSTN)


class CyberpunkQrPageTests(unittest.TestCase):
    """The QR page may look like a HUD, but the code itself stays boring and scannable.

    Decorative overlays, low-contrast backgrounds and animated scan lines across a QR
    all make a poster memorable for the wrong reason. The targeting system therefore
    surrounds a plain black code in a pure-white quiet zone and never covers it.
    """

    QR = (Path(__file__).parents[1] / "web" / "qr.html").read_text()

    def test_deployment_placeholder_and_visible_fallback_remain(self):
        self.assertGreaterEqual(self.QR.count("__DEMO_DOMAIN__"), 2)
        self.assertIn("https://__DEMO_DOMAIN__/", self.QR)

    def test_generator_still_requests_a_high_resolution_code(self):
        self.assertIn("size=600x600", self.QR)
        self.assertIn('width="600" height="600"', self.QR)

    def test_the_code_has_a_pure_white_quiet_zone(self):
        self.assertIn(".qr{", self.QR)
        self.assertIn("background:#fff; padding:24px", self.QR)
        self.assertIn(".qr{padding:20px}", self.QR)

    def test_nothing_decorative_is_inside_the_qr_housing(self):
        start = self.QR.index('<div class="qr">')
        end = self.QR.index("</div>", start)
        housing = self.QR[start:end]
        self.assertEqual(housing.count("<img"), 1)
        for forbidden in ("corner", "scan", "::before", "::after"):
            self.assertNotIn(forbidden, housing)

    def test_the_reticle_explicitly_sits_below_the_code(self):
        self.assertIn(".qr{\n  position:relative; z-index:3", self.QR)
        self.assertIn("Nothing overlays the code itself", self.QR)

    def test_the_page_uses_the_shared_cyberpunk_vocabulary(self):
        for token in ("--cyan:#5af5ff", "--pink:#ff4fd8", "IBM+Plex+Mono",
                      "repeating-linear-gradient", "DEMO ACCESS NODE · ONLINE"):
            with self.subTest(token=token):
                self.assertIn(token, self.QR)

    def test_old_copy_is_gone_and_new_instruction_is_present(self):
        self.assertNotIn("ให้ <em>AI</em> โทรหาคุณ", self.QR)
        self.assertNotIn("สแกนเลย", self.QR)
        self.assertNotIn("พูดไทยได้จริง", self.QR)
        self.assertIn("SCAN // CONNECT", self.QR)
        self.assertIn("SPEAK THAI", self.QR)
        self.assertIn("สแกนเพื่อทดลอง AI เสียงไทย", self.QR)

    def test_mobile_reticle_does_not_create_horizontal_overflow(self):
        """The rotating square expanded scroll width from 390 to 435 pixels."""
        self.assertIn(".target::after{inset:4px;transform:none;animation:targetPulse", self.QR)

    def test_reduced_motion_and_forced_colours_are_supported(self):
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.QR)
        self.assertIn("@media(forced-colors:active)", self.QR)
        self.assertIn("forced-color-adjust:none", self.QR)

    def test_the_page_is_not_indexed(self):
        self.assertIn('name="robots" content="noindex,nofollow"', self.QR)


class VoiceLabSlideTests(unittest.TestCase):
    """Slide 06 reads supplied text aloud through the existing WebRTC path."""

    HTML = (ROOT / "web" / "index.html").read_text()

    def test_it_is_a_sixth_slide_with_its_own_nav_entry(self):
        self.assertIn('id="voice-lab"', self.HTML)
        self.assertIn('content:"06"', self.HTML)
        self.assertIn('"Voice Lab"', self.HTML)

    def test_the_voice_dropdown_carries_engine_and_locale_per_voice(self):
        """Each voice needs its own engine and locale, not one global default.

        Thai is not a multilingual-voice language, so Suda ships th-TH while the
        polyglot voices ship en-US.
        """
        self.assertIn('<option value="SUDA" data-engine="connect:agentic" '
                      'data-language="th-TH" selected>', self.HTML)
        # Somchai was confirmed by listening on a real call, not by the validator,
        # which accepts any string. Both Thai voices must stay on th-TH.
        self.assertIn('<option value="Somchai" data-engine="connect:agentic" '
                      'data-language="th-TH">', self.HTML)
        for voice in ("Katie", "Blake", "Brooke", "Ronald", "Gemma"):
            self.assertIn(f'<option value="{voice}" data-engine="connect:agentic" '
                          'data-language="en-US">', self.HTML)

    def test_it_offers_the_agentic_speech_control_tags(self):
        for tag in ("&lt;break time=&quot;500ms&quot;/&gt;",
                    "&lt;speed ratio=&quot;0.85&quot;/&gt;",
                    "&lt;volume ratio=&quot;1.5&quot;/&gt;",
                    "&lt;emotion value=&quot;content&quot;/&gt;",
                    "[laughter]"):
            self.assertIn(tag, self.HTML)
        self.assertIn('data-wrap="spell"', self.HTML)

    def test_it_documents_that_ssml_is_not_supported(self):
        """The engine speaks malformed tags aloud, so the page must not imply SSML."""
        self.assertIn("Control tags, not SSML", self.HTML)
        self.assertIn("&lt;speak&gt;", self.HTML)

    def test_it_warns_that_multilingual_voices_exclude_thai(self):
        self.assertIn("Thai needs a Thai voice", self.HTML)

    def test_it_translates_microphone_failures_instead_of_showing_browser_english(self):
        """A presenter laptop without a mic otherwise shows "Requested device not found"."""
        self.assertIn('name==="NotFoundError"', self.HTML)
        self.assertIn('name==="NotAllowedError"', self.HTML)
        self.assertIn("ไม่พบไมโครโฟน", self.HTML)
        self.assertIn("กรุณาอนุญาตการใช้ไมโครโฟน", self.HTML)

    def test_it_states_the_microphone_requirement_up_front(self):
        self.assertIn("ต้องมีไมโครโฟน", self.HTML)

    def test_a_typed_voice_name_overrides_the_dropdown(self):
        """No list can be authoritative because Connect accepts unknown voice names."""
        self.assertIn('id="lab-custom-voice"', self.HTML)
        self.assertIn('maxlength="32"', self.HTML)
        self.assertIn("function chosenVoice(){return custom.value.trim()||voice.value}",
                      self.HTML)
        self.assertIn('voice:chosenVoice()', self.HTML)

    def test_a_typed_voice_carries_its_own_locale(self):
        self.assertIn('id="lab-custom-language"', self.HTML)
        self.assertIn('<option value="th-TH" selected>th-TH</option>', self.HTML)

    def test_it_posts_the_voicelab_action_and_caps_length(self):
        self.assertIn('action:"voicelab"', self.HTML)
        self.assertIn('maxlength="600"', self.HTML)


class VoiceLabFlowTests(unittest.TestCase):
    """The lab flow must degrade rather than drop the caller."""

    FLOW = json.loads((ROOT / "iac" / "mantle-voice-lab-flow.json").read_text())

    def _action(self, identifier):
        return next(a for a in self.FLOW["Actions"] if a["Identifier"] == identifier)

    def test_a_known_good_thai_voice_is_set_before_any_override(self):
        """An unsupported requested voice must fall back to something that works."""
        baseline = self._action("lab-default-voice")
        self.assertEqual(baseline["Parameters"]["TextToSpeechVoice"], "SUDA")
        self.assertEqual(baseline["Parameters"]["TextToSpeechEngine"], "connect:agentic")

    def test_the_requested_voice_and_locale_come_from_attributes(self):
        override = self._action("lab-voice")
        self.assertEqual(override["Parameters"]["TextToSpeechVoice"], "$.Attributes.labVoice")
        self.assertEqual(override["Parameters"]["TextToSpeechEngine"], "$.Attributes.labEngine")
        self.assertEqual(self._action("lab-language")["Parameters"]["LanguageCode"],
                         "$.Attributes.labLanguage")

    def test_every_override_failure_still_reaches_the_prompt(self):
        """A failed Set voice or Set language must not disconnect the caller."""
        for identifier in ("lab-language", "lab-voice"):
            errors = self._action(identifier)["Transitions"]["Errors"]
            self.assertTrue(errors, f"{identifier} has no error branch")
            for error in errors:
                self.assertIn(error["NextAction"], {"lab-voice", "lab-speak"})

    def test_it_speaks_the_supplied_text_then_hangs_up(self):
        self.assertEqual(self._action("lab-speak")["Parameters"]["Text"],
                         "$.Attributes.labText")
        self.assertEqual(self._action("lab-end")["Type"], "DisconnectParticipant")
