const startButton = document.querySelector("#start-button");
const stopButton = document.querySelector("#stop-button");
const sessionTitleInput = document.querySelector("#session-title");
const sessionTitleMessage = document.querySelector("#session-title-message");
const statusBadge = document.querySelector("#status-badge");
const statusMessage = document.querySelector("#status-message");
const microphoneMonitor = document.querySelector("#microphone-monitor");
const microphoneLabel = document.querySelector("#microphone-label");
const microphoneMessage = document.querySelector("#microphone-message");
const microphoneLevel = document.querySelector("#microphone-level");
const microphoneBars = [...microphoneLevel.querySelectorAll("span")];
const transcriptList = document.querySelector("#transcript-list");
const translationList = document.querySelector("#translation-list");
const translationAlerts = document.querySelector("#translation-alerts");
const currentFrenchCaption = document.querySelector("#current-french-caption");
const emptyTranscript = document.querySelector("#empty-transcript");
const emptyTranslation = document.querySelector("#empty-translation");
const refreshSessionsButton = document.querySelector("#refresh-sessions");
const sessionList = document.querySelector("#session-list");
const emptySessions = document.querySelector("#empty-sessions");
const emptyViewer = document.querySelector("#empty-viewer");
const sessionDetail = document.querySelector("#session-detail");
const savedSessionStatus = document.querySelector("#saved-session-status");
const savedSessionTitle = document.querySelector("#saved-session-title");
const savedSessionMeta = document.querySelector("#saved-session-meta");
const savedAudioPanel = document.querySelector("#saved-audio-panel");
const savedAudio = document.querySelector("#saved-audio");
const downloadAudio = document.querySelector("#download-audio");
const downloadEnglish = document.querySelector("#download-english");
const downloadFrench = document.querySelector("#download-french");
const downloadReport = document.querySelector("#download-report");
const deleteSessionButton = document.querySelector("#delete-session");
const archiveMessage = document.querySelector("#archive-message");
const deleteSessionDialog = document.querySelector("#delete-session-dialog");
const deleteDialogSessionName = document.querySelector(
  "#delete-dialog-session-name",
);
const cancelDeleteSessionButton = document.querySelector(
  "#cancel-delete-session",
);
const confirmDeleteSessionButton = document.querySelector(
  "#confirm-delete-session",
);
const savedEnglish = document.querySelector("#saved-english");
const savedFrench = document.querySelector("#saved-french");
const viewTabs = [...document.querySelectorAll('[role="tab"]')];
const viewPanels = [...document.querySelectorAll('[role="tabpanel"]')];
const SESSION_RESULTS_KEY = "oci-speech-results-v1";
const ACTIVE_VIEW_KEY = "oratranslate-active-view-v1";
const LIVE_SESSION_CHANNEL_NAME = "oratranslate-live-session-v1";
const FRENCH_CAPTION_PLACEHOLDER = "La traduction française apparaîtra ici.";
const SESSION_TITLE_CONFLICT_MESSAGE =
  "A saved session already uses this name. Choose a different name.";
const { applicationPath, websocketUrl } = window.oraTranslateUrls;
const tabId =
  window.crypto?.randomUUID?.() ||
  `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const liveSessionChannel =
  "BroadcastChannel" in window
    ? new BroadcastChannel(LIVE_SESSION_CHANNEL_NAME)
    : null;

let socket = null;
let mediaStream = null;
let audioContext = null;
let mediaSource = null;
let workletNode = null;
let silentGain = null;
let analyserNode = null;
let microphoneSamples = null;
let microphoneAnimationFrame = null;
let englishSegments = [];
let frenchSegments = [];
let partialTranscript = "";
let transcriptParagraph = null;
let translationParagraph = null;
let diagnosticEvents = [];
let stopping = false;
let sessionStarting = false;
let ownsLiveSession = false;
let anotherLiveSessionActive = false;
let sessionRejected = false;
let availabilityRefreshTimer = null;
let selectedSessionId = null;
let archivePlaybackClaim = null;

function setStatus(state, message) {
  statusBadge.dataset.state = state;
  statusBadge.textContent = state.replaceAll("_", " ");
  statusMessage.textContent = message;
}

function setMicrophoneState(state, label, message) {
  microphoneMonitor.dataset.state = state;
  microphoneLabel.textContent = label;
  microphoneMessage.textContent = message;
}

function normalizeSessionTitle(value) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, 120);
}

function setSessionTitleError(message = "") {
  sessionTitleInput.setCustomValidity(message);
  sessionTitleInput.toggleAttribute("aria-invalid", Boolean(message));
  sessionTitleMessage.textContent = message;
  sessionTitleMessage.hidden = !message;
}

async function validateSessionTitleForStart() {
  const title = normalizeSessionTitle(sessionTitleInput.value);
  sessionTitleInput.value = title;
  setSessionTitleError();

  if (!title) {
    const message = "Enter a session name.";
    setSessionTitleError(message);
    sessionTitleInput.focus();
    return null;
  }

  try {
    const response = await fetch(applicationPath("/api/sessions"), {
      cache: "no-store",
    });
    if (response.ok) {
      const payload = await response.json();
      const titleKey = title.toLocaleLowerCase();
      const duplicate = (payload.sessions || []).some(
        (session) =>
          normalizeSessionTitle(session.title).toLocaleLowerCase() === titleKey,
      );
      if (duplicate) {
        setSessionTitleError(SESSION_TITLE_CONFLICT_MESSAGE);
        sessionTitleInput.focus();
        return null;
      }
    }
  } catch {
    // The WebSocket performs the authoritative validation if this check fails.
  }

  return title;
}

function liveSessionInProgress() {
  return (
    sessionStarting ||
    ownsLiveSession ||
    anotherLiveSessionActive ||
    socket !== null
  );
}

function pauseSavedAudioForCoordination() {
  archivePlaybackClaim = null;
  if (!savedAudio.paused) {
    savedAudio.pause();
  }
}

function pauseArchivePlaybackAcrossTabs() {
  pauseSavedAudioForCoordination();
  liveSessionChannel?.postMessage({
    type: "archive_playback_pause",
    reason: "live_session",
    tabId,
  });
}

function claimArchivePlayback() {
  const claim = {
    type: "archive_playback_started",
    tabId,
    sessionId: selectedSessionId,
    startedAt: Date.now(),
  };
  archivePlaybackClaim = claim;
  liveSessionChannel?.postMessage(claim);
}

function claimComesAfter(candidate, current) {
  if (!current) {
    return true;
  }
  if (candidate.startedAt !== current.startedAt) {
    return candidate.startedAt > current.startedAt;
  }
  return String(candidate.tabId) > String(current.tabId);
}

function handleRemoteArchivePlayback(claim) {
  if (
    !savedAudio.paused &&
    claimComesAfter(claim, archivePlaybackClaim)
  ) {
    pauseSavedAudioForCoordination();
  }
}

function renderMicrophoneLevel(level) {
  const boundedLevel = Math.max(0, Math.min(100, Math.round(level)));
  const activeBarCount = Math.ceil(
    (boundedLevel / 100) * microphoneBars.length,
  );

  microphoneLevel.setAttribute("aria-valuenow", String(boundedLevel));
  microphoneBars.forEach((bar, index) => {
    bar.classList.toggle("is-active", index < activeBarCount);
  });
}

function stopMicrophoneMeter() {
  if (microphoneAnimationFrame !== null) {
    window.cancelAnimationFrame(microphoneAnimationFrame);
  }
  microphoneAnimationFrame = null;
  microphoneSamples = null;
  renderMicrophoneLevel(0);
}

function startMicrophoneMeter() {
  stopMicrophoneMeter();
  microphoneSamples = new Uint8Array(analyserNode.fftSize);

  const updateMeter = () => {
    if (!analyserNode || !microphoneSamples) {
      return;
    }

    analyserNode.getByteTimeDomainData(microphoneSamples);
    let sumOfSquares = 0;
    microphoneSamples.forEach((sample) => {
      const centeredSample = (sample - 128) / 128;
      sumOfSquares += centeredSample * centeredSample;
    });
    const rootMeanSquare = Math.sqrt(
      sumOfSquares / microphoneSamples.length,
    );
    const displayLevel = Math.max(0, rootMeanSquare - 0.01) * 650;
    renderMicrophoneLevel(displayLevel);

    microphoneAnimationFrame = window.requestAnimationFrame(updateMeter);
  };

  microphoneAnimationFrame = window.requestAnimationFrame(updateMeter);
}

function activateView(selectedTab, focusTab = false) {
  if (!selectedTab) {
    return;
  }

  viewTabs.forEach((tab) => {
    const selected = tab === selectedTab;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  viewPanels.forEach((panel) => {
    panel.hidden = panel.id !== selectedTab.dataset.panel;
  });

  try {
    sessionStorage.setItem(ACTIVE_VIEW_KEY, selectedTab.id);
  } catch {
    // Tab navigation remains available without browser storage.
  }

  if (selectedTab.id === "session-archives-tab") {
    loadSessions();
  }
  if (focusTab) {
    selectedTab.focus();
  }
}

function restoreActiveView() {
  let selectedTabId = "live-session-tab";
  try {
    selectedTabId = sessionStorage.getItem(ACTIVE_VIEW_KEY) || selectedTabId;
  } catch {
    // Use the default live-session tab when browser storage is unavailable.
  }

  activateView(
    viewTabs.find((tab) => tab.id === selectedTabId) || viewTabs[0],
  );
}

viewTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateView(tab));
  tab.addEventListener("keydown", (event) => {
    let nextIndex = null;
    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % viewTabs.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + viewTabs.length) % viewTabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = viewTabs.length - 1;
    }

    if (nextIndex !== null) {
      event.preventDefault();
      activateView(viewTabs[nextIndex], true);
    }
  });
});

function updateSessionControls() {
  const localSessionActive =
    sessionStarting || ownsLiveSession || socket !== null;
  startButton.disabled =
    localSessionActive || stopping || anotherLiveSessionActive;
  stopButton.disabled = !ownsLiveSession || stopping;
  sessionTitleInput.disabled = localSessionActive || anotherLiveSessionActive;
}

function scheduleAvailabilityRefresh() {
  window.clearTimeout(availabilityRefreshTimer);
  availabilityRefreshTimer = null;
  if (!anotherLiveSessionActive || ownsLiveSession) {
    return;
  }

  availabilityRefreshTimer = window.setTimeout(() => {
    refreshLiveSessionAvailability().catch(() => {});
  }, 2000);
}

function setAnotherLiveSessionActive(active, showStatus = true) {
  anotherLiveSessionActive = Boolean(active) && !ownsLiveSession;
  updateSessionControls();

  if (anotherLiveSessionActive) {
    pauseSavedAudioForCoordination();
  }

  if (anotherLiveSessionActive && showStatus && !ownsLiveSession) {
    setStatus("unavailable", "Another OraTranslate session is already active.");
    setMicrophoneState(
      "unavailable",
      "Microphone unavailable",
      "Another browser tab or device owns the live session.",
    );
  } else if (
    !anotherLiveSessionActive &&
    statusBadge.dataset.state === "unavailable"
  ) {
    setStatus("idle", "Ready to start a live session.");
    setMicrophoneState(
      "idle",
      "Microphone ready",
      "Start a session to check the audio input.",
    );
  }

  scheduleAvailabilityRefresh();
}

async function refreshLiveSessionAvailability(showStatus = true) {
  try {
    const response = await fetch(applicationPath("/api/live-session"), {
      cache: "no-store",
    });
    if (!response.ok) {
      return anotherLiveSessionActive;
    }
    const payload = await response.json();
    setAnotherLiveSessionActive(payload.active, showStatus);
  } catch {
    // The WebSocket remains authoritative if this convenience check fails.
  }
  return anotherLiveSessionActive;
}

function broadcastLiveSessionState(state) {
  liveSessionChannel?.postMessage({
    type: "live_session_state",
    state,
    tabId,
  });
}

function markLocalSessionActive() {
  if (ownsLiveSession) {
    return;
  }
  ownsLiveSession = true;
  sessionStarting = false;
  sessionRejected = false;
  anotherLiveSessionActive = false;
  updateSessionControls();
  pauseSavedAudioForCoordination();
  broadcastLiveSessionState("active");
}

liveSessionChannel?.addEventListener("message", (event) => {
  if (event.data?.tabId === tabId) {
    return;
  }

  switch (event.data?.type) {
    case "live_session_state":
      if (event.data.state === "active" && !ownsLiveSession) {
        pauseSavedAudioForCoordination();
        setAnotherLiveSessionActive(true);
      } else {
        refreshLiveSessionAvailability().catch(() => {});
      }
      break;
    case "archive_playback_started":
      handleRemoteArchivePlayback(event.data);
      break;
    case "archive_playback_pause":
      pauseSavedAudioForCoordination();
      break;
    default:
      break;
  }
});

function saveResults() {
  try {
    sessionStorage.setItem(
      SESSION_RESULTS_KEY,
      JSON.stringify({
        sessionTitle: normalizeSessionTitle(sessionTitleInput.value),
        englishSegments,
        frenchSegments,
        diagnosticEvents,
      }),
    );
  } catch {
    // Transcription continues if browser storage is unavailable or full.
  }
}

function resetResults(clearStoredResults = true) {
  transcriptList.replaceChildren(emptyTranscript);
  translationList.replaceChildren(emptyTranslation);
  translationAlerts.replaceChildren();
  emptyTranscript.hidden = false;
  emptyTranslation.hidden = false;
  englishSegments = [];
  frenchSegments = [];
  partialTranscript = "";
  transcriptParagraph = null;
  translationParagraph = null;
  diagnosticEvents = [];
  currentFrenchCaption.textContent = FRENCH_CAPTION_PLACEHOLDER;
  currentFrenchCaption.classList.add("waiting-caption");

  if (clearStoredResults) {
    try {
      sessionStorage.removeItem(SESSION_RESULTS_KEY);
    } catch {
      // The visible page can still reset when browser storage is unavailable.
    }
  }
}

function renderTranscript() {
  emptyTranscript.hidden = true;

  if (!transcriptParagraph) {
    transcriptParagraph = document.createElement("p");
    transcriptParagraph.className = "continuous-text";
    transcriptList.append(transcriptParagraph);
  }

  const finalText = englishSegments.join(" ");
  transcriptParagraph.replaceChildren(
    document.createTextNode(finalText),
  );

  if (partialTranscript) {
    if (finalText) {
      transcriptParagraph.append(document.createTextNode(" "));
    }
    const partial = document.createElement("span");
    partial.className = "partial-text";
    partial.textContent = partialTranscript;
    transcriptParagraph.append(partial);
  }

  transcriptList.scrollTop = transcriptList.scrollHeight;
}

function appendTranscript(text, isFinal) {
  const trimmedText = text.trim();
  if (!trimmedText) {
    return;
  }

  if (isFinal) {
    englishSegments.push(trimmedText);
    partialTranscript = "";
  } else {
    partialTranscript = trimmedText;
  }

  renderTranscript();

  if (isFinal) {
    saveResults();
  }
}

function renderTranslation() {
  emptyTranslation.hidden = true;

  const latestTranslation = frenchSegments.at(-1);
  currentFrenchCaption.textContent =
    latestTranslation || FRENCH_CAPTION_PLACEHOLDER;
  currentFrenchCaption.classList.toggle(
    "waiting-caption",
    !latestTranslation,
  );

  if (!translationParagraph) {
    translationParagraph = document.createElement("p");
    translationParagraph.className = "continuous-text french-text";
    translationParagraph.lang = "fr";
    translationList.append(translationParagraph);
  }

  translationParagraph.textContent = frenchSegments.join(" ");
  translationList.scrollTop = translationList.scrollHeight;
}

function appendTranslation(event) {
  const frenchText = event.french.trim();
  if (!frenchText) {
    return;
  }

  frenchSegments.push(frenchText);
  renderTranslation();
  saveResults();
}

function appendError(event, persist = true) {
  const banner = document.createElement("article");
  banner.className = "message-banner";
  banner.dataset.severity = "danger";
  const content = document.createElement("div");
  content.className = "message-banner__content";
  const title = document.createElement("strong");
  title.className = "message-banner__title";
  title.textContent =
    event.stage === "translation"
      ? "A French caption could not be translated."
      : `${event.stage || "OCI"} error`;
  const summary = document.createElement("p");
  summary.className = "message-banner__summary";
  summary.textContent =
    event.stage === "translation"
      ? "The session is continuing, but a short section may be missing."
      : event.message;
  content.append(title, summary);

  const technicalDetails = document.createElement("details");
  const technicalSummary = document.createElement("summary");
  technicalSummary.textContent = "Technical details";
  const technicalContent = document.createElement("div");
  technicalContent.className = "message-banner__technical-details";
  const message = document.createElement("p");
  message.textContent = event.message;
  technicalContent.append(message);

  if (event.opc_request_id) {
    const requestId = document.createElement("code");
    requestId.textContent = `OPC request ID: ${event.opc_request_id}`;
    technicalContent.append(requestId);
  }

  if (event.status || event.code) {
    const status = document.createElement("code");
    status.textContent = `Status: ${[event.status, event.code]
      .filter(Boolean)
      .join(" ")}`;
    technicalContent.append(status);
  }

  technicalDetails.append(technicalSummary, technicalContent);
  content.append(technicalDetails);
  banner.append(content);
  translationAlerts.append(banner);

  if (persist) {
    diagnosticEvents.push({
      stage: event.stage,
      message: event.message,
      opc_request_id: event.opc_request_id,
      status: event.status,
      code: event.code,
    });
    saveResults();
  }
}

function restoreResults() {
  let savedResults;
  try {
    savedResults = JSON.parse(
      sessionStorage.getItem(SESSION_RESULTS_KEY) || "null",
    );
  } catch {
    try {
      sessionStorage.removeItem(SESSION_RESULTS_KEY);
    } catch {
      // Ignore browser storage restrictions and start with an empty page.
    }
    return;
  }

  if (!savedResults || typeof savedResults !== "object") {
    return;
  }

  resetResults(false);
  if (typeof savedResults.sessionTitle === "string") {
    sessionTitleInput.value = normalizeSessionTitle(savedResults.sessionTitle);
    setSessionTitleError();
  }
  englishSegments = Array.isArray(savedResults.englishSegments)
    ? savedResults.englishSegments.filter((text) => typeof text === "string")
    : [];
  frenchSegments = Array.isArray(savedResults.frenchSegments)
    ? savedResults.frenchSegments.filter((text) => typeof text === "string")
    : [];
  diagnosticEvents = Array.isArray(savedResults.diagnosticEvents)
    ? savedResults.diagnosticEvents.filter(
        (event) => event && typeof event.message === "string",
      )
    : [];

  if (englishSegments.length) {
    renderTranscript();
  }
  if (frenchSegments.length) {
    renderTranslation();
  }
  diagnosticEvents.forEach((event) => appendError(event, false));

  if (
    englishSegments.length ||
    frenchSegments.length ||
    diagnosticEvents.length
  ) {
    setStatus(
      "restored",
      "Restored the previous session name and results after refresh.",
    );
  }
}

function formatDuration(seconds) {
  const totalSeconds = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;
  return [hours, minutes, remainingSeconds]
    .filter((_, index) => hours > 0 || index > 0)
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function formatSessionDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unknown date"
    : date.toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

function showSessionListMessage(message) {
  sessionList.replaceChildren(emptySessions);
  emptySessions.hidden = false;
  emptySessions.textContent = message;
}

function clearSessionViewer(message = "Choose a session to see its saved outputs.") {
  selectedSessionId = null;
  savedAudio.pause();
  savedAudio.removeAttribute("src");
  savedAudio.load();
  sessionDetail.hidden = true;
  emptyViewer.hidden = false;
  emptyViewer.textContent = message;
}

async function selectSession(sessionId) {
  selectedSessionId = sessionId;
  sessionList.querySelectorAll(".session-card").forEach((card) => {
    card.setAttribute(
      "aria-current",
      String(card.dataset.sessionId === sessionId),
    );
  });

  emptyViewer.hidden = false;
  emptyViewer.textContent = "Loading saved session...";
  sessionDetail.hidden = true;

  try {
    const response = await fetch(
      applicationPath(`/api/sessions/${encodeURIComponent(sessionId)}`),
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error("The saved session could not be loaded.");
    }
    const session = await response.json();
    if (selectedSessionId !== sessionId) {
      return;
    }

    savedSessionStatus.textContent = session.status || "saved";
    savedSessionStatus.dataset.status = session.status || "saved";
    savedSessionTitle.textContent = session.title || "Live session";
    savedSessionMeta.textContent = `${formatSessionDate(
      session.started_at,
    )} · ${formatDuration(session.duration_seconds)}`;

    savedAudioPanel.hidden = !session.audio_available;
    downloadAudio.hidden = !session.audio_available;
    if (session.audio_available) {
      savedAudio.src = applicationPath(session.audio_url);
      downloadAudio.href = applicationPath(session.audio_url);
    } else {
      savedAudio.pause();
      savedAudio.removeAttribute("src");
      savedAudio.load();
      downloadAudio.removeAttribute("href");
    }

    downloadEnglish.href = applicationPath(session.english_url);
    downloadFrench.href = applicationPath(session.french_url);
    downloadReport.hidden = !session.report_url;
    if (session.report_url) {
      downloadReport.href = applicationPath(session.report_url);
    } else {
      downloadReport.removeAttribute("href");
    }
    savedEnglish.textContent =
      session.english_text || "No English transcript was captured.";
    savedFrench.textContent =
      session.french_text || "No French translation was captured.";

    emptyViewer.hidden = true;
    sessionDetail.hidden = false;
  } catch (error) {
    clearSessionViewer(error?.message || "The saved session could not be loaded.");
  }
}

function renderSessionList(sessions, preferredSessionId) {
  if (!sessions.length) {
    showSessionListMessage("Completed sessions will appear here.");
    clearSessionViewer();
    return;
  }

  emptySessions.hidden = true;
  const cards = sessions.map((session) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "session-card";
    card.dataset.sessionId = session.session_id;

    const title = document.createElement("strong");
    title.textContent = session.title || "Live session";
    const details = document.createElement("span");
    details.textContent = `${formatSessionDate(
      session.started_at,
    )} · ${formatDuration(session.duration_seconds)} · ${
      session.status || "saved"
    }`;
    card.append(title, details);
    card.addEventListener("click", () => selectSession(session.session_id));
    return card;
  });
  sessionList.replaceChildren(...cards);

  const availableIds = new Set(sessions.map((session) => session.session_id));
  const nextSessionId = availableIds.has(preferredSessionId)
    ? preferredSessionId
    : availableIds.has(selectedSessionId)
      ? selectedSessionId
      : sessions[0].session_id;
  selectSession(nextSessionId);
}

async function loadSessions(preferredSessionId = null) {
  refreshSessionsButton.disabled = true;
  try {
    const response = await fetch(applicationPath("/api/sessions"), {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error("Saved sessions could not be loaded.");
    }
    const payload = await response.json();
    renderSessionList(
      Array.isArray(payload.sessions) ? payload.sessions : [],
      preferredSessionId,
    );
  } catch (error) {
    showSessionListMessage(error?.message || "Saved sessions could not be loaded.");
    clearSessionViewer();
  } finally {
    refreshSessionsButton.disabled = false;
  }
}

function hideArchiveMessage() {
  archiveMessage.replaceChildren();
}

function showArchiveMessage(titleText, messageText, severity = "danger") {
  const banner = document.createElement("div");
  banner.className = "message-banner";
  banner.dataset.severity = severity;
  const content = document.createElement("div");
  content.className = "message-banner__content";
  const title = document.createElement("strong");
  title.className = "message-banner__title";
  title.textContent = titleText;
  const message = document.createElement("p");
  message.className = "message-banner__summary";
  message.textContent = messageText;
  content.append(title, message);

  banner.append(content);
  archiveMessage.replaceChildren(banner);
}

function openDeleteSessionDialog() {
  if (!selectedSessionId || deleteSessionDialog.open) {
    return;
  }

  hideArchiveMessage();
  deleteDialogSessionName.textContent =
    savedSessionTitle.textContent || "This saved session";
  deleteSessionDialog.showModal();
  cancelDeleteSessionButton.focus();
}

async function deleteSelectedSession() {
  const sessionId = selectedSessionId;
  if (!sessionId) {
    return;
  }

  deleteSessionButton.disabled = true;
  cancelDeleteSessionButton.disabled = true;
  confirmDeleteSessionButton.disabled = true;

  try {
    const response = await fetch(
      applicationPath(`/api/sessions/${encodeURIComponent(sessionId)}`),
      { method: "DELETE" },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || "The saved session could not be deleted.");
    }

    deleteSessionDialog.close();
    savedAudio.pause();
    savedAudio.removeAttribute("src");
    savedAudio.load();
    selectedSessionId = null;
    await loadSessions();
    showArchiveMessage(
      "Session deleted",
      "The saved recording, transcripts, report, metadata, and diagnostics were deleted.",
      "success",
    );
  } catch (error) {
    deleteSessionDialog.close();
    showArchiveMessage(
      "Session not deleted",
      error?.message || "The saved session could not be deleted.",
    );
  } finally {
    deleteSessionButton.disabled = false;
    cancelDeleteSessionButton.disabled = false;
    confirmDeleteSessionButton.disabled = false;
  }
}

async function beginAudioCapture(sampleRate) {
  await audioContext.audioWorklet.addModule(
    applicationPath("/audio-worklet.js"),
  );
  await audioContext.resume();

  mediaSource = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm16-resampler");
  silentGain = audioContext.createGain();
  analyserNode = audioContext.createAnalyser();
  analyserNode.fftSize = 256;
  analyserNode.smoothingTimeConstant = 0.72;
  silentGain.gain.value = 0;

  workletNode.port.onmessage = (event) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(event.data);
    }
  };

  mediaSource.connect(workletNode);
  mediaSource.connect(analyserNode);
  workletNode.connect(silentGain);
  silentGain.connect(audioContext.destination);

  startMicrophoneMeter();
  setMicrophoneState(
    "active",
    "Microphone active",
    "Audio is being captured. Speak normally and watch the level respond.",
  );
  setStatus("listening", `Listening at ${sampleRate / 1000} kHz PCM`);
}

async function releaseAudio() {
  stopMicrophoneMeter();

  if (workletNode) {
    workletNode.port.postMessage({ type: "flush" });
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  mediaSource?.disconnect();
  analyserNode?.disconnect();
  workletNode?.disconnect();
  silentGain?.disconnect();
  mediaStream?.getTracks().forEach((track) => track.stop());

  if (audioContext && audioContext.state !== "closed") {
    await audioContext.close();
  }

  mediaStream = null;
  audioContext = null;
  mediaSource = null;
  analyserNode = null;
  workletNode = null;
  silentGain = null;
}

function handleServerEvent(event) {
  switch (event.type) {
    case "session_status":
      if (event.state === "connecting") {
        markLocalSessionActive();
        setMicrophoneState(
          "connecting",
          "Microphone connected",
          "Preparing the live speech connection...",
        );
      }
      setStatus(event.state, event.message);
      break;
    case "session_ready":
      if (!stopping) {
        beginAudioCapture(event.sample_rate).catch(handleClientError);
      }
      break;
    case "transcript":
      appendTranscript(event.text, event.is_final);
      break;
    case "translation":
      appendTranslation(event);
      break;
    case "error":
      appendError(event);
      setStatus("error", `${event.stage || "OCI"} error`);
      if (event.stage === "speech" || event.stage === "session") {
        setMicrophoneState(
          "error",
          "Microphone stream interrupted",
          event.message || "The live speech connection reported an error.",
        );
      }
      break;
    case "session_stopped":
      setStatus("stopped", event.message);
      setMicrophoneState(
        "idle",
        "Microphone stopped",
        "The session audio capture has ended.",
      );
      break;
    case "session_saved":
      loadSessions(event.session?.session_id || null);
      break;
    case "session_rejected":
      sessionRejected = true;
      sessionStarting = false;
      ownsLiveSession = false;
      if (event.code === "LIVE_SESSION_ACTIVE") {
        setAnotherLiveSessionActive(true);
      } else {
        setSessionTitleError(event.message);
        setStatus("idle", "Ready to start a live session.");
        setMicrophoneState(
          "idle",
          "Microphone ready",
          "Update the session name and try again.",
        );
        updateSessionControls();
        sessionTitleInput.focus();
      }
      releaseAudio().catch(() => {});
      socket?.close();
      break;
    default:
      break;
  }
}

function handleClientError(error) {
  if (sessionRejected) {
    return;
  }
  const message = error?.message || String(error);
  appendError({ stage: "browser", message });
  setStatus("error", message);
  setMicrophoneState("error", "Microphone unavailable", message);
  sessionStarting = false;
  releaseAudio().catch(() => {});
  socket?.close();
  updateSessionControls();
}

async function startSession() {
  if (sessionStarting || ownsLiveSession || anotherLiveSessionActive) {
    return;
  }

  sessionStarting = true;
  sessionRejected = false;
  updateSessionControls();

  const sessionTitle = await validateSessionTitleForStart();
  if (!sessionTitle) {
    sessionStarting = false;
    updateSessionControls();
    sessionTitleInput.focus();
    return;
  }

  if (await refreshLiveSessionAvailability()) {
    sessionStarting = false;
    updateSessionControls();
    return;
  }

  pauseArchivePlaybackAcrossTabs();
  resetResults();
  saveResults();
  stopping = false;
  updateSessionControls();
  setStatus("microphone", "Waiting for microphone permission...");
  setMicrophoneState(
    "requesting",
    "Microphone permission",
    "Allow microphone access when your browser asks.",
  );

  try {
    audioContext = new AudioContext();
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    setMicrophoneState(
      "connecting",
      "Microphone connected",
      "Connecting securely to the live speech service...",
    );

    socket = new WebSocket(websocketUrl("/ws/live"));
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: "start",
          title: sessionTitle,
        }),
      );
    };

    socket.onmessage = (message) => {
      handleServerEvent(JSON.parse(message.data));
    };
    socket.onerror = () => {
      if (!sessionRejected) {
        handleClientError(new Error("The browser couldn't reach the server."));
      }
    };
    socket.onclose = async () => {
      const wasOwner = ownsLiveSession;
      await releaseAudio();
      socket = null;
      sessionStarting = false;
      ownsLiveSession = false;
      stopping = false;
      updateSessionControls();

      if (wasOwner) {
        broadcastLiveSessionState("check");
      }
      await refreshLiveSessionAvailability(false);

      if (
        !sessionRejected &&
        microphoneMonitor.dataset.state !== "error" &&
        microphoneMonitor.dataset.state !== "idle"
      ) {
        setMicrophoneState(
          "idle",
          "Microphone disconnected",
          "Start a new session when you are ready.",
        );
      }

      if (
        !sessionRejected &&
        statusBadge.dataset.state !== "error" &&
        statusBadge.dataset.state !== "stopped"
      ) {
        setStatus("disconnected", "The server connection closed.");
      }
      if (sessionRejected && sessionTitleInput.hasAttribute("aria-invalid")) {
        sessionTitleInput.focus();
      }
      sessionRejected = false;
    };
  } catch (error) {
    handleClientError(error);
  }
}

async function stopSession() {
  if (stopping) {
    return;
  }

  stopping = true;
  updateSessionControls();
  setStatus("finalizing", "Sending the final audio and translating it...");
  setMicrophoneState(
    "connecting",
    "Microphone stopped",
    "Finalizing the session recording and translations...",
  );
  await releaseAudio();

  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop" }));
  } else {
    socket?.close();
    sessionStarting = false;
    updateSessionControls();
  }
}

startButton.addEventListener("click", startSession);
stopButton.addEventListener("click", stopSession);
sessionTitleInput.addEventListener("input", () => {
  if (sessionTitleInput.value.trim()) {
    setSessionTitleError();
  }
});
refreshSessionsButton.addEventListener("click", () => loadSessions());
deleteSessionButton.addEventListener("click", openDeleteSessionDialog);
cancelDeleteSessionButton.addEventListener("click", () => {
  deleteSessionDialog.close();
});
confirmDeleteSessionButton.addEventListener("click", deleteSelectedSession);
deleteSessionDialog.addEventListener("cancel", (event) => {
  if (confirmDeleteSessionButton.disabled) {
    event.preventDefault();
  }
});
savedAudio.addEventListener("play", () => {
  if (liveSessionInProgress()) {
    pauseSavedAudioForCoordination();
    return;
  }
  claimArchivePlayback();
});
savedAudio.addEventListener("pause", () => {
  archivePlaybackClaim = null;
});
savedAudio.addEventListener("ended", () => {
  archivePlaybackClaim = null;
});
restoreResults();
restoreActiveView();
refreshLiveSessionAvailability().catch(() => {});

window.addEventListener("focus", () => {
  refreshLiveSessionAvailability().catch(() => {});
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshLiveSessionAvailability().catch(() => {});
  }
});
