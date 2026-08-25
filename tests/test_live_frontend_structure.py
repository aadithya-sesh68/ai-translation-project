"""Structural checks for the audience-first live caption page."""

from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).parents[1]


class LiveFrontendStructureTest(unittest.TestCase):
    def test_french_caption_is_the_primary_live_surface(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("<title>OraTranslate Live</title>", html)
        self.assertIn("<h1>OraTranslate Live</h1>", html)
        self.assertIn('id="current-french-caption"', html)
        self.assertIn('class="current-french-caption waiting-caption"', html)
        self.assertIn('id="translation-list"', html)

    def test_secondary_content_is_disclosed_on_demand(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<details class="source-panel">', html)
        self.assertIn(
            '<details class="session-library" id="session-library">',
            html,
        )
        self.assertNotIn('<details class="source-panel" open>', html)
        self.assertNotIn(
            '<details class="session-library" id="session-library" open>',
            html,
        )

    def test_latest_translation_drives_the_large_caption(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const latestTranslation = frenchSegments.at(-1);", script)
        self.assertIn("currentFrenchCaption.textContent", script)


if __name__ == "__main__":
    unittest.main()
