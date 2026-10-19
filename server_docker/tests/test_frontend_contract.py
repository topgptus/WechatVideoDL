import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_frontend_has_three_theme_modes_and_persists_preference(self):
        base = (ROOT / "templates" / "base.html").read_text("utf-8")
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        for theme in ["light", "dark", "system"]:
            self.assertIn(f'value="{theme}"', base)
        self.assertIn("localStorage", js)
        self.assertIn("wvd-theme", js)

    def test_fetch_helper_checks_content_type_before_json_parse(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn("async function safeFetch", js)
        self.assertIn("content-type", js.lower())
        self.assertIn("response.text()", js)
        self.assertIn("JSON.parse", js)
        self.assertIn("服务器返回了非 JSON 错误", js)
        self.assertNotIn("await response.json()", js)

    def test_preset_prompt_is_editable_and_markdown_is_web_default(self):
        index = (ROOT / "templates" / "index.html").read_text("utf-8")
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn('id="preset-select"', index)
        self.assertIn('id="prompt-input"', index)
        self.assertIn('<option value="md" selected>', index)
        self.assertIn('routePath("api/presets")', js)
        self.assertIn("promptInput.value", js)

    def test_fallback_stages_and_partial_success_rendering(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        for expected in ("local_transcription", "yuanbao_transcript", "partial",
                         "transcript?.download_path", "旁白"):
            self.assertIn(expected, js)

    def test_history_missing_cover_placeholder_distinguishes_text_only_tasks(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn('job.thumbnail?.download_path', js)
        self.assertIn('job.download_requested === false ? "No VIDEO" : "No Cover"', js)
        self.assertNotIn('placeholder.textContent = "VIDEO"', js)

    def test_history_supports_download_detail_and_confirmed_delete(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn('routePath("api/history")', js)
        self.assertIn("window.confirm", js)
        self.assertIn("method: \"DELETE\"", js)
        self.assertIn("download_path", js)

    def test_frontend_routes_respect_reverse_proxy_prefix(self):
        base = (ROOT / "templates" / "base.html").read_text("utf-8")
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn('data-base-path="{{ request.script_root }}"', base)
        self.assertIn("url_for('history_page')", base)
        self.assertIn("function routePath", js)
        self.assertIn('routePath("api/presets")', js)
        self.assertIn('routePath("api/history")', js)
        self.assertNotIn('safeFetch("/api/', js)
        self.assertNotIn('safeFetch(`/api/', js)

    def test_css_has_light_dark_and_responsive_layout(self):
        css = (ROOT / "static" / "app.css").read_text("utf-8")
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn(':root[data-theme="light"]', css)
        self.assertIn("@media", css)
        self.assertIn(".workspace-grid", css)

    def test_titled_thumbnail_history_bundle_and_source_link_contract(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        history = (ROOT / "templates" / "history.html").read_text("utf-8")
        self.assertIn("job.title || job.id", js)
        self.assertIn("job.thumbnail?.download_path", js)
        self.assertIn("job.bundle?.download_path", js)
        self.assertIn('"ALL"', js)
        self.assertIn("function copySourceLink", js)
        self.assertIn("navigator.clipboard.writeText", js)
        self.assertIn('"复制链接"', js)
        self.assertNotIn('window.location.href = "weixin://"', js)
        self.assertIn('target = "_blank"', js)
        self.assertIn('rel = "noopener noreferrer"', js)
        self.assertIn("job.title", js[js.index("const visible = jobs.filter"):])
        self.assertIn("按标题", history)

    def test_async_polling_and_refresh_recovery_contract(self):
        js = (ROOT / "static" / "app.js").read_text("utf-8")
        self.assertIn('routePath("api/parse")', js)
        self.assertIn("async function watchJob", js)
        self.assertIn("poll_interval_seconds", js)
        self.assertNotIn("await sleep(3000)", js)
        self.assertIn("result-progress", (ROOT / "templates" / "index.html").read_text("utf-8"))
        self.assertIn('routePath(`api/history/${encodeURIComponent(jobId)}`)', js)
        self.assertIn("wvd-active-job", js)
        self.assertIn('localStorage.setItem(activeJobKey', js)
        self.assertIn('localStorage.removeItem(activeJobKey)', js)
        self.assertIn('data.status === "queued"', js)
        self.assertIn('data.status === "completed"', js)
        self.assertIn('data.status === "failed"', js)

    def test_workspace_template_has_title_thumbnail_and_source_action_slots(self):
        index = (ROOT / "templates" / "index.html").read_text("utf-8")
        for token in ['id="result-title"', 'id="result-thumbnail"', 'id="result-source-actions"']:
            self.assertIn(token, index)

    def test_mobile_header_wraps_actions_instead_of_overflowing(self):
        css = (ROOT / "static" / "app.css").read_text("utf-8")
        self.assertIn(".app-header{flex-wrap:wrap", css)
        self.assertIn(".header-actions{width:100%", css)
        self.assertNotIn(".theme-control{margin-left:auto}", css)


if __name__ == "__main__":
    unittest.main()
