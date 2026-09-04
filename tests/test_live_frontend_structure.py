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

    def test_saved_session_report_remains_internal(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        session_store = (PROJECT_ROOT / "session_store.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('id="download-report"', html)
        self.assertNotIn("Download session report", html)
        self.assertNotIn("downloadReport", script)
        self.assertIn('result["report_url"]', session_store)
        self.assertIn('"session_report.json"', session_store)

    def test_saved_session_actions_follow_the_archive_header(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        viewer = html.split('id="session-viewer"', 1)[1].split("</article>", 1)[0]
        title_position = viewer.index('id="saved-session-title"')
        status_position = viewer.index('id="saved-session-status"')
        delete_position = viewer.index('id="delete-session"')
        audio_position = viewer.index('id="saved-audio-panel"')

        self.assertLess(title_position, status_position)
        self.assertLess(delete_position, audio_position)
        self.assertEqual(3, viewer.count('class="btn btn--secondary download-action"'))
        self.assertEqual(3, viewer.count('aria-hidden="true" focusable="false"'))
        self.assertIn("function formatSessionStatus(value)", script)
        self.assertIn("formatSessionStatus(session.status)", script)

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

    def test_host_uses_the_selected_slot_without_a_manual_session_name(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('id="session-title"', html)
        self.assertNotIn('id="session-title-message"', html)
        self.assertNotIn("validateSessionTitleForStart", script)
        self.assertNotIn("SESSION_TITLE_CONFLICT_MESSAGE", script)
        self.assertIn(
            'connectLiveSocket({ type: "prepare", session_code: selectedHostSessionCode })',
            script,
        )
        self.assertIn('case "session_snapshot":', script)

    def test_waiting_room_precedes_microphone_and_live_activation(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn(">Prepare session</button>", html)
        self.assertNotIn('id="host-share-panel"', html)
        self.assertNotIn('id="copy-join-code"', html)
        self.assertIn('id="start-without-listener-dialog"', html)
        self.assertIn("No listener is connected yet.", html)
        self.assertIn('case "session_prepared":', script)
        self.assertIn('hostSessionState = "prepared";\n      sessionStarting = false;\n      updateHostListenerCount(0);', script)
        self.assertIn(
            'connectLiveSocket({ type: "prepare", session_code: selectedHostSessionCode })',
            script,
        )
        self.assertIn('socket.send(JSON.stringify({ type: "activate" }))', script)
        self.assertIn('socket.send(JSON.stringify({ type: "cancel" }))', script)
        self.assertLess(
            script.index("await acquireMicrophone();", script.index("async function activatePreparedSession")),
            script.index('socket.send(JSON.stringify({ type: "activate" }))'),
        )
        prepare_flow = script.split("if (ownsHostLease || socket) return;", 1)[1].split(
            "async function activatePreparedSession()", 1
        )[0]
        self.assertNotIn("acquireMicrophone", prepare_flow)
        self.assertIn('listenerLiveMarker.textContent = "Waiting"', script)

    def test_live_controls_use_an_item_overview_left_card(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )
        styles = (PROJECT_ROOT / "web" / "styles.css").read_text(
            encoding="utf-8"
        )
        host_surface = html.split('id="host-workspace"', 1)[1].split(
            'id="listener-join"', 1
        )[0]
        overview_position = host_surface.index('class="live-item-overview"')
        command_center = host_surface.split(
            'class="session-command-center"', 1
        )[1].split('class="live-item-overview__content"', 1)[0]
        content = host_surface.split(
            'class="live-item-overview__content"', 1
        )[1]

        controls_position = command_center.index('class="session-controls"')
        status_position = command_center.index('class="status-panel"')
        listener_position = command_center.index('id="host-listener-message"')
        microphone_position = command_center.index('id="microphone-monitor"')
        content_position = host_surface.index('class="live-item-overview__content"')
        transcript_position = host_surface.index(
            'class="history-panel host-transcript-panel"'
        )

        self.assertGreater(overview_position, 0)
        self.assertIn('<aside class="session-command-center"', host_surface)
        self.assertIn('id="session-details-heading"', command_center)
        self.assertIn('<label for="host-session-code">', command_center)
        self.assertNotIn("Event session", command_center)
        self.assertNotIn("Reusable code", command_center)
        self.assertGreater(controls_position, 0)
        self.assertIn('class="status-panel"', command_center)
        self.assertIn('id="microphone-monitor"', command_center)
        self.assertLess(controls_position, status_position)
        self.assertLess(status_position, microphone_position)
        self.assertLess(microphone_position, listener_position)
        self.assertLess(overview_position, content_position)
        self.assertLess(content_position, transcript_position)
        self.assertNotIn('class="live-session-operations"', host_surface)
        self.assertIn('role="meter"', command_center)
        self.assertIn('id="host-listener-label"', command_center)
        self.assertIn("hostListenerLabel.textContent", script)
        schedule_label_rule = styles.split(".schedule-summary label {", 1)[1].split(
            "}", 1
        )[0]
        select_rule = styles.split("select.join-code-input {", 1)[1].split("}", 1)[0]
        self.assertIn("font-size: var(--rds-font-size-sm);", schedule_label_rule)
        self.assertIn("font-size: var(--rds-font-size-md);", select_rule)
        self.assertIn("font-weight: 400;", select_rule)
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

    def test_closed_host_tab_can_offer_server_validated_recovery(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        role_entry = html.split('id="role-entry"', 1)[1].split(">", 1)[0]
        restore_role = script.split("async function restoreRole()", 1)[1].split(
            "hostModeButton.addEventListener", 1
        )[0]

        self.assertIn("hidden", role_entry)
        self.assertIn(
            "key === HOST_SESSION_KEY ? localStorage : sessionStorage",
            script,
        )
        self.assertLess(
            restore_role.index("readSessionValue(LISTENER_SESSION_KEY)"),
            restore_role.index("readSessionValue(HOST_SESSION_KEY)"),
        )
        self.assertIn("await fetchLiveSessionStatus()", restore_role)
        self.assertIn("status.session_id === host.session_id", restore_role)
        self.assertIn("!status.host_connected", restore_role)
        self.assertIn("showStoredHostRecovery(host", restore_role)
        self.assertIn('storedHost.state !== "prepared"', script)
        self.assertIn('event.code !== "LIVE_SESSION_ACTIVE"', script)

    def test_listener_leave_restores_the_join_action(self) -> None:
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        show_join = script.split("function showListenerJoin()", 1)[1].split(
            "function showListenerWorkspace()", 1
        )[0]
        leave_listener = script.split("function leaveListenerSession()", 1)[1].split(
            "function liveSessionInProgress()", 1
        )[0]

        self.assertIn(
            "setListenerCodeInputsDisabled(false);",
            show_join,
        )
        self.assertIn(
            "joinListenerButton.disabled = false;",
            show_join,
        )
        self.assertIn("showListenerJoin();", leave_listener)
        self.assertIn(
            'event.key === "Enter" && !joinListenerButton.disabled',
            script,
        )

    def test_invalid_listener_code_uses_inline_error_styling(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (PROJECT_ROOT / "web" / "styles.css").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        message = html.split('id="listener-code-message"', 1)[1].split(">", 1)[0]
        self.assertIn('role="alert"', message)
        self.assertIn('aria-atomic="true"', message)
        self.assertIn('.listener-join__card > .field-message {', styles)
        self.assertIn('color: var(--rds-color-danger-text);', styles)
        self.assertNotIn('.listener-join__card > .field-message::before', styles)
        self.assertIn('.join-code-entry[aria-invalid="true"]', styles)
        self.assertIn('listenerCodeEntry.toggleAttribute("aria-invalid"', script)

    def test_saved_transcripts_scroll_independently(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (PROJECT_ROOT / "web" / "styles.css").read_text(
            encoding="utf-8"
        )

        self.assertEqual(2, html.count('class="saved-transcript-scroll"'))
        self.assertEqual(2, html.count('role="region"'))
        self.assertIn('aria-labelledby="saved-english-heading"', html)
        self.assertIn('aria-labelledby="saved-french-heading"', html)
        self.assertEqual(2, html.count('tabindex="0"', html.index('id="session-detail"')))
        scroll_rule = styles.split(".saved-transcript-scroll {", 1)[1].split("}", 1)[0]
        self.assertIn("max-height: var(--result-list-max-height);", scroll_rule)
        self.assertIn("overflow-y: auto;", scroll_rule)
        self.assertIn("overscroll-behavior: contain;", scroll_rule)
        self.assertIn("scrollbar-gutter: stable;", scroll_rule)

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

    def test_reusable_event_codes_are_selected_and_server_authoritative(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('<select id="host-session-code"', html)
        self.assertIn('id="listener-code"', html)
        self.assertEqual(6, html.count('class="join-code-character"'))
        self.assertIn('class="join-code-segment join-code-segment--period"', html)
        self.assertIn('data-code-index="5"', html)
        self.assertIn('id="listener-code-guidance"', html)
        self.assertIn('aria-describedby="listener-code-guidance listener-code-message"', html)
        self.assertIn("Enter the code for the session you want to join.", html)
        self.assertLess(
            html.index('id="listener-code-guidance"'),
            html.index('id="listener-code-label"'),
        )
        self.assertIn(
            "September 15: <strong>DAY1-AM</strong> or "
            "<strong>DAY1-PM</strong>",
            html,
        )
        self.assertIn(
            "September 16: <strong>DAY2-AM</strong> or "
            "<strong>DAY2-PM</strong>",
            html,
        )
        self.assertIn('function listenerCodeValue()', script)
        self.assertIn(
            'setListenerCodeError("Enter one of the event codes shown above.");',
            script,
        )
        self.assertNotIn(
            "Enter DAY1-AM, DAY1-PM, DAY2-AM, or DAY2-PM.",
            script,
        )
        self.assertIn('input.addEventListener("paste"', script)
        self.assertIn('setListenerCodeValue(characters);', script)
        self.assertIn('applicationPath("/api/session-slots")', script)
        self.assertIn('selectedHostSessionCode', script)
        self.assertIn('function formatHostSessionOption(slot)', script)
        self.assertIn('`${slot.code} · ${dateLabel}`', script)
        self.assertIn('hostSessionCodeInput.addEventListener("change"', script)
        self.assertIn(
            'setListenerStatus("host_ready", "The host is connected.',
            script,
        )
        self.assertIn('/^DAY[12]-(AM|PM)$/.test(joinCode)', script)
        self.assertNotIn("SESSION_STATUS_REFRESH_MILLISECONDS", script)
        self.assertNotIn('case "session_waiting":', script)
        self.assertNotIn('id="host-session-slots"', html)
        self.assertIn('session_code: selectedHostSessionCode', script)

    def test_ending_a_live_slot_requires_explicit_confirmation(self) -> None:
        html = (PROJECT_ROOT / "web" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "web" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="end-session-dialog"', html)
        self.assertIn('id="confirm-end-session"', html)
        self.assertIn("endSessionDialog.showModal();", script)
        self.assertIn("async function confirmEndHostSession()", script)
        self.assertIn('type: "stop"', script)
        self.assertIn("The event code remains", html)
        self.assertIn("available if another run is needed.", html)
        self.assertIn("The event code remains available", script)


if __name__ == "__main__":
    unittest.main()
