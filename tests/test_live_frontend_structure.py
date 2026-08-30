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

    def test_archives_are_separated_from_the_default_live_view(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<details class="source-panel">', html)
        self.assertIn('id="live-session-tab"', html)
        self.assertIn('id="session-archives-tab"', html)
        self.assertIn('id="live-session-panel"', html)
        self.assertIn('id="session-archives-panel"', html)
        self.assertIn('class="session-library"', html)
        self.assertIn('aria-labelledby="session-archives-tab"', html)
        self.assertNotIn('<details class="source-panel" open>', html)
        self.assertNotIn(
            '<details class="session-library"',
            html,
        )

        archive_panel = html.split('id="session-archives-panel"', 1)[1]
        self.assertIn("hidden", archive_panel.split(">", 1)[0])

    def test_tabs_support_click_and_keyboard_navigation(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function activateView", script)
        self.assertIn('tab.addEventListener("click"', script)
        self.assertIn('event.key === "ArrowRight"', script)
        self.assertIn('event.key === "ArrowLeft"', script)
        self.assertIn("panel.hidden", script)

    def test_latest_translation_drives_the_large_caption(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("const latestTranslation = frenchSegments.at(-1);", script)
        self.assertIn("currentFrenchCaption.textContent", script)

    def test_saved_session_report_is_downloadable(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="download-report"', html)
        self.assertIn("session.report_url", script)

    def test_browser_tabs_coordinate_the_single_live_session(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('new BroadcastChannel(LIVE_SESSION_CHANNEL_NAME)', script)
        self.assertIn('applicationPath("/api/live-session")', script)
        self.assertIn('case "session_rejected":', script)
        self.assertIn(
            "Another OraTranslate session is already active.",
            script,
        )

    def test_live_controls_share_one_compact_command_center(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        live_panel = html.split('id="live-session-panel"', 1)[1]
        command_center = live_panel.split(
            'class="session-command-center"', 1
        )[1].split('class="caption-stage"', 1)[0]

        controls_position = command_center.index('class="session-controls"')
        status_position = command_center.index('class="status-panel"')
        microphone_position = command_center.index('id="microphone-monitor"')
        caption_position = live_panel.index('class="caption-stage"')

        self.assertLess(controls_position, status_position)
        self.assertLess(status_position, microphone_position)
        self.assertGreater(caption_position, 0)
        self.assertIn('class="session-feedback"', command_center)
        self.assertIn('role="meter"', command_center)
        self.assertIn(
            'aria-label="Microphone input level"', command_center
        )
        self.assertNotIn("Session setup", command_center)

    def test_microphone_meter_uses_the_live_audio_signal(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("audioContext.createAnalyser()", script)
        self.assertIn("getByteTimeDomainData", script)
        self.assertIn("window.requestAnimationFrame(updateMeter)", script)
        self.assertIn('setMicrophoneState(\n    "active"', script)

    def test_archive_audio_is_coordinated_across_browser_tabs(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("Only one archived recording plays at a time.", html)
        self.assertIn('savedAudio.addEventListener("play"', script)
        self.assertIn("if (liveSessionInProgress())", script)
        self.assertIn('type: "archive_playback_started"', script)
        self.assertIn('type: "archive_playback_pause"', script)
        self.assertIn("pauseArchivePlaybackAcrossTabs();", script)
        self.assertIn('case "archive_playback_started":', script)
        self.assertIn('case "archive_playback_pause":', script)


if __name__ == "__main__":
    unittest.main()
