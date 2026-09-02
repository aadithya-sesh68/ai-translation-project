"""Structural checks for the Redwood host/listener live caption page."""

from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).parents[1]


class LiveFrontendStructureTest(unittest.TestCase):
    def test_role_entry_and_role_specific_caption_surfaces_are_present(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("<title>OraTranslate Live</title>", html)
        self.assertIn('<h1 class="overview-title">', html)
        self.assertIn('class="overview-title__strong">OraTranslate</span>', html)
        self.assertIn("<span>Live</span>", html)
        self.assertIn('id="role-entry"', html)
        self.assertIn('id="host-mode-button"', html)
        self.assertIn('id="listener-mode-button"', html)
        self.assertIn('id="host-workspace"', html)
        self.assertIn('id="listener-join"', html)
        self.assertIn('id="listener-workspace"', html)

        host_surface = html.split('id="host-workspace"', 1)[1].split(
            'id="listener-join"', 1
        )[0]
        listener_surface = html.split('id="listener-workspace"', 1)[1].split(
            'id="session-archives-panel"', 1
        )[0]
        self.assertNotIn('id="current-english-caption"', host_surface)
        self.assertIn('class="history-panel host-transcript-panel"', host_surface)
        self.assertIn('id="transcript-list"', host_surface)
        self.assertNotIn('id="current-french-caption"', host_surface)
        self.assertIn('id="current-french-caption"', listener_surface)
        self.assertIn('id="translation-list"', listener_surface)
        self.assertNotIn('id="current-english-caption"', listener_surface)

    def test_redwood_overview_structure_and_assets_are_present(self) -> None:
        web_root = PROJECT_ROOT / "web"
        html = (web_root / "index.html").read_text(encoding="utf-8")

        frame_position = html.index(
            '<div class="frame-shell">',
            html.index("<main"),
        )
        content_position = html.index('<div class="content-shell">', frame_position)
        primary_position = html.index(
            '<div class="content-shell__primary">',
            content_position,
        )

        self.assertLess(frame_position, content_position)
        self.assertLess(content_position, primary_position)
        self.assertIn('class="overview-strip"', html)
        self.assertIn('class="app-nav"', html)
        self.assertIn('class="brand-pill"', html)

        for relative_path in (
            "assets/fonts/fonts.css",
            "assets/fonts/OracleSans_Rg.ttf",
            "assets/images/bg-left.png",
            "assets/images/bg-right.png",
            "assets/images/strip-desktop.png",
            "assets/images/strip-tablet.png",
            "assets/images/strip-mobile.png",
            "assets/images/oracle-o.svg",
        ):
            self.assertTrue((web_root / relative_path).is_file(), relative_path)

    def test_translation_diagnostics_remains_available_but_is_not_linked(self) -> None:
        web_root = PROJECT_ROOT / "web"
        html = (web_root / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('href="translation-test.html"', html)
        self.assertNotIn("Translation diagnostics", html)
        self.assertTrue((web_root / "translation-test.html").is_file())

    def test_archives_are_separated_from_the_default_live_view(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="live-session-tab"', html)
        self.assertIn('id="session-archives-tab"', html)
        self.assertIn('id="live-session-panel"', html)
        self.assertIn('id="session-archives-panel"', html)
        self.assertIn('class="session-library overview-section"', html)
        self.assertIn('aria-labelledby="session-archives-tab"', html)

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

    def test_host_transcript_scrolls_without_interrupting_review(self) -> None:
        styles = (PROJECT_ROOT / "web" / "styles.css").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        host_panel_styles = styles.split(".host-transcript-panel {", 1)[1].split(
            "}", 1
        )[0]
        host_list_styles = styles.split(
            ".host-transcript-panel .result-list {", 1
        )[1].split("}", 1)[0]

        self.assertIn(
            "height: calc(var(--caption-min-height) + var(--result-list-max-height));",
            host_panel_styles,
        )
        self.assertIn("min-height: 0;", host_list_styles)
        self.assertIn("function isNearScrollEnd(element)", script)
        self.assertIn(
            "const followLatest = isNearScrollEnd(transcriptList);",
            script,
        )
        self.assertIn(
            "if (followLatest) transcriptList.scrollTop = transcriptList.scrollHeight;",
            script,
        )

    def test_only_listener_current_caption_is_a_live_region(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )

        french_caption = html.split('id="current-french-caption"', 1)[1].split(
            ">", 1
        )[0]
        transcript_history = html.split('id="transcript-list"', 1)[1].split(
            ">", 1
        )[0]
        translation_history = html.split('id="translation-list"', 1)[1].split(
            ">", 1
        )[0]

        self.assertIn('aria-live="polite"', french_caption)
        self.assertNotIn('id="current-english-caption"', html)
        self.assertNotIn("aria-live", transcript_history)
        self.assertNotIn("aria-live", translation_history)

    def test_translation_errors_use_a_separate_progressive_message_area(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="translation-alerts"', html)
        self.assertIn('aria-label="Translation notices"', html)
        self.assertIn('detailsSummary.textContent = "Technical details"', script)
        self.assertIn("target.append(banner)", script)
        self.assertNotIn("translationList.append(card)", script)

    def test_saved_session_deletion_uses_an_accessible_dialog(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('<dialog\n      id="delete-session-dialog"', html)
        self.assertIn('aria-labelledby="delete-dialog-title"', html)
        self.assertIn('id="archive-message"', html)
        self.assertIn("deleteSessionDialog.showModal()", script)
        self.assertIn("showArchiveMessage(", script)
        self.assertNotIn("window.confirm", script)
        self.assertNotIn("window.alert", script)

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

    def test_session_name_is_required_unique_and_restored(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        title_input = html.split('id="session-title"', 1)[1].split("/>", 1)[0]
        title_input_position = html.index('id="session-title"')
        assistance_position = html.index('class="session-field-assistance"')
        required_position = html.index('class="required-message"')
        title_validation = script.split(
            "async function validateSessionTitleForStart()", 1
        )[1].split("function activateView", 1)[0]

        self.assertIn("required", title_input)
        self.assertIn('aria-describedby="session-title-message"', title_input)
        self.assertIn('id="session-title-message"', html)
        self.assertLess(title_input_position, assistance_position)
        self.assertLess(assistance_position, required_position)
        self.assertNotIn('class="session-field-label"', html)
        self.assertIn("validateSessionTitleForStart()", script)
        self.assertIn('applicationPath("/api/sessions")', script)
        self.assertIn("SESSION_TITLE_CONFLICT_MESSAGE", script)
        self.assertIn('connectLiveSocket({ type: "start", title })', script)
        self.assertIn('case "session_snapshot":', script)
        self.assertNotIn('setHostStatus("error"', title_validation)

    def test_live_controls_use_an_item_overview_left_card(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        host_surface = html.split('id="host-workspace"', 1)[1].split(
            'id="listener-join"', 1
        )[0]
        overview_position = host_surface.index('class="live-item-overview"')
        command_center = host_surface.split(
            'class="session-command-center"', 1
        )[1].split('class="live-item-overview__content"', 1)[0]

        controls_position = command_center.index('class="session-controls"')
        status_position = command_center.index('class="status-panel"')
        microphone_position = command_center.index('id="microphone-monitor"')
        content_position = host_surface.index('class="live-item-overview__content"')
        transcript_position = host_surface.index(
            'class="history-panel host-transcript-panel"'
        )

        self.assertGreater(overview_position, 0)
        self.assertIn('<aside class="session-command-center"', host_surface)
        self.assertIn('id="session-details-heading"', command_center)
        self.assertLess(controls_position, status_position)
        self.assertLess(status_position, microphone_position)
        self.assertLess(overview_position, content_position)
        self.assertLess(content_position, transcript_position)
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
        self.assertIn("requestAnimationFrame(updateMeter)", script)
        self.assertIn('setMicrophoneState("active"', script)
        self.assertIn('type: "audio_level"', script)
        self.assertIn("renderLevel(speakerLevel", script)

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
        self.assertIn('event.data?.type === "archive_playback_started"', script)
        self.assertIn('event.data?.type === "archive_playback_pause"', script)

    def test_host_refresh_and_listener_rejoin_use_server_snapshots(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('type: "resume"', script)
        self.assertIn('resume_token: storedHost.resume_token', script)
        self.assertIn('type: "join"', script)
        self.assertIn('case "session_snapshot":', script)
        self.assertIn("event.english_segments", script)
        self.assertIn("event.french_segments", script)
        self.assertIn("scheduleListenerReconnect", script)

    def test_listener_surface_has_no_microphone_or_session_controls(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        listener_surface = html.split('id="listener-workspace"', 1)[1].split(
            'id="session-archives-panel"', 1
        )[0]

        self.assertIn('id="speaker-monitor"', listener_surface)
        self.assertIn('aria-label="Speaker microphone level"', listener_surface)
        self.assertNotIn('id="start-button"', listener_surface)
        self.assertNotIn('id="stop-button"', listener_surface)
        self.assertNotIn('id="microphone-monitor"', listener_surface)


if __name__ == "__main__":
    unittest.main()
