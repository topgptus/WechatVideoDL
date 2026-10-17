"""Contract tests for the Apple-Design responsive UI; browser QA is separate."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppleUIDesignTests(unittest.TestCase):
    def source(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_history_actions_have_exact_labels_order_and_original_links(self):
        js = self.source("static/app.js")
        segment = js.split(
            'const actions = document.createElement("div"); actions.className = "history-actions"'
        )[1].split('const detail = document.createElement("div")')[0]
        labels = ['"ALL"', '"AI解析"', '"旁白"', '"MP4"', '"详情"', '"删除"']
        offsets = [segment.index(label) for label in labels]
        self.assertEqual(offsets, sorted(offsets))
        expected_mapping = [
            'job.bundle?.download_path',
            'job.text?.download_path',
            'job.transcript?.download_path',
            'job.video?.download_path',
        ]
        self.assertEqual([segment.index(x) for x in expected_mapping],
                         sorted(segment.index(x) for x in expected_mapping))
        self.assertIn("actions.append(detailBtn, del)", segment)
        self.assertIn("detailBtn.setAttribute", js)
        self.assertIn('"aria-expanded", "false"', js)
        self.assertIn('"No VIDEO"', js)

    def test_workspace_uses_matching_download_semantics(self):
        js = self.source("static/app.js")
        segment = js.split('downloadLinks.replaceChildren();')[1].split('    }', 1)[0]
        labels = ['"ALL"', '"AI解析"', '"旁白"', '"MP4"']
        offsets = [segment.index(label) for label in labels]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('data.transcript?.download_path', segment)

    def test_history_title_metadata_source_and_prompt_disclosure(self):
        js = self.source("static/app.js")
        css = self.source("static/app.css")
        self.assertIn("function conciseHistoryTitle(fullTitle)", js)
        self.assertIn("const maxLength = 36", js)
        self.assertIn('heading.title = titleText', js)
        self.assertIn('detailRow("完整标题"', js)
        self.assertIn('detailRow("原视频链接"', js)
        self.assertIn('detailRow("本次 Prompt"', js)
        self.assertIn('const meta = document.createElement("div"); meta.className = "history-meta"', js)
        self.assertIn('"history-job-id"', js)
        self.assertIn('id.setAttribute("aria-label", "任务 ID："', js)
        self.assertIn('timeZone: "Asia/Shanghai"', js)
        # Source and Prompt must not appear in the default visible card body.
        card_start = js.index('  function historyCard(job, reload) {')
        compact = js[card_start:js.index('    const actions = document.createElement("div");',card_start)]
        self.assertNotIn("renderSourceActions", compact)
        self.assertNotIn('history-prompt', compact)
        self.assertIn('.history-detail-info', css)
        self.assertIn('.history-info-row', css)

    def test_source_links_copy_real_url_and_never_open_generic_wechat_scheme(self):
        js = self.source("static/app.js")
        self.assertIn("function copySourceLink", js)
        self.assertIn("navigator.clipboard.writeText(url)", js)
        self.assertIn('"浏览器打开"', js)
        self.assertIn('"复制链接"', js)
        self.assertIn('window.prompt("复制以下视频号链接', js)
        self.assertNotIn('window.location.href = "weixin://"', js)
        self.assertIn('["http:", "https:"]', js)

    def test_mobile_safe_area_controls_and_contrast(self):
        css = self.source("static/app.css")
        self.assertIn('@media (max-width:640px)', css)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', css)
        self.assertIn('min-height:44px', css)
        self.assertIn('safe-area-inset-bottom', css)
        self.assertIn('prefers-reduced-motion:reduce', css)
        self.assertIn('prefers-reduced-transparency:reduce', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('var(--accent-ink)', css)
        self.assertIn('#235dbe', css)
        self.assertIn('#ad2839', css)

    def test_semantic_navigation_and_form_labels(self):
        base = self.source("templates/base.html")
        history = self.source("templates/history.html")
        login = self.source("templates/login.html")
        self.assertIn('aria-current="page"', base)
        self.assertIn('class="primary-nav"', base)
        self.assertIn('class="header-utilities"', base)
        self.assertIn('aria-label="外观主题"', base)
        self.assertIn('for="history-filter"', history)
        self.assertNotIn('autofocus', login)
        self.assertIn('autocomplete="current-password"', login)


if __name__ == "__main__":
    unittest.main()
