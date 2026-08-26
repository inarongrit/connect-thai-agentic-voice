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

    def test_bundle_fallback_is_unreachable_so_no_rebuild_is_required(self):
        """`webrtc.bundle.js` still minifies an older `|| "managed"` default.

        It can never fire because the page passes `brainMode` on every call. If a
        call site ever stops passing it, rebuild the bundle
        (`npm run build:webrtc`) before relying on the default.
        """
        bundle = (ROOT / "web" / "webrtc.bundle.js").read_text()
        self.assertIn('brainMode:i||"managed"', bundle)
        self.assertNotIn("startCall({api:API,scenario,name:nameInput.value.trim(),audioElement",
                         self.html)
        self.assertIn("brainMode:BRAIN_MODE,audioElement", self.html)

    def test_slides_no_longer_claim_an_ab_comparison(self):
        for gone in ('Blind A/B', 'A/B blind', 'blind comparison', 'A/B evidence',
                     'Option B', 'B=prompt-instructed', 'LABEL=A / B only'):
            self.assertNotIn(gone, self.html, f"stale A/B narrative: {gone}")


if __name__ == "__main__":
    unittest.main()
