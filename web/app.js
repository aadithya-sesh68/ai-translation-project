const $ = (selector) => document.querySelector(selector);

const roleEntry = $("#role-entry");
const hostWorkspace = $("#host-workspace");
const listenerJoin = $("#listener-join");
const listenerWorkspace = $("#listener-workspace");
const hostModeButton = $("#host-mode-button");
const listenerModeButton = $("#listener-mode-button");
const hostBackButton = $("#host-back-button");
const listenerBackButton = $("#listener-back-button");
const leaveListenerButton = $("#leave-listener-button");
const startButton = $("#start-button");
const stopButton = $("#stop-button");
const statusBadge = $("#status-badge");
const statusMessage = $("#status-message");
const microphoneMonitor = $("#microphone-monitor");
const microphoneLabel = $("#microphone-label");
const microphoneMessage = $("#microphone-message");
const microphoneLevel = $("#microphone-level");
const microphoneBars = [...microphoneLevel.querySelectorAll("span")];
const hostListenerCount = $("#host-listener-count");
const hostListenerMessage = $("#host-listener-message");
const hostListenerLabel = $("#host-listener-label");
const hostSessionCodeInput = $("#host-session-code");
const transcriptList = $("#transcript-list");
const emptyTranscript = $("#empty-transcript");
const hostAlerts = $("#host-alerts");
const listenerCodeEntry = $("#listener-code");
const listenerCodeInputs = [...listenerCodeEntry.querySelectorAll(".join-code-character")];
const listenerCodeMessage = $("#listener-code-message");
const joinListenerButton = $("#join-listener-button");
const listenerSessionTitle = $("#listener-session-title");
const listenerSessionCode = $("#listener-session-code");
const listenerStatusBadge = $("#listener-status-badge");
const listenerStatusMessage = $("#listener-status-message");
const speakerMonitor = $("#speaker-monitor");
const speakerMonitorLabel = $("#speaker-monitor-label");
const speakerMonitorMessage = $("#speaker-monitor-message");
const speakerLevel = $("#speaker-level");
const speakerBars = [...speakerLevel.querySelectorAll("span")];
const currentFrenchCaption = $("#current-french-caption");
const listenerLiveMarker = $("#listener-live-marker");
const translationList = $("#translation-list");
const emptyTranslation = $("#empty-translation");
const translationAlerts = $("#translation-alerts");
const refreshSessionsButton = $("#refresh-sessions");
const sessionList = $("#session-list");
const emptySessions = $("#empty-sessions");
const emptyViewer = $("#empty-viewer");
const sessionDetail = $("#session-detail");
const savedSessionStatus = $("#saved-session-status");
const savedSessionTitle = $("#saved-session-title");
const savedSessionMeta = $("#saved-session-meta");
const savedAudioPanel = $("#saved-audio-panel");
const savedAudio = $("#saved-audio");
const downloadAudio = $("#download-audio");
const downloadEnglish = $("#download-english");
const downloadFrench = $("#download-french");
const deleteSessionButton = $("#delete-session");
const archiveMessage = $("#archive-message");
const deleteSessionDialog = $("#delete-session-dialog");
const deleteDialogSessionName = $("#delete-dialog-session-name");
const cancelDeleteSessionButton = $("#cancel-delete-session");
const confirmDeleteSessionButton = $("#confirm-delete-session");
const savedEnglish = $("#saved-english");
const savedFrench = $("#saved-french");
const viewTabs = [...document.querySelectorAll('[role="tab"]')];
const viewPanels = [...document.querySelectorAll('[role="tabpanel"]')];
const startWithoutListenerDialog = $("#start-without-listener-dialog");
const cancelStartWithoutListenerButton = $("#cancel-start-without-listener");
const confirmStartWithoutListenerButton = $("#confirm-start-without-listener");
const endSessionDialog = $("#end-session-dialog");
const endSessionDialogDescription = $("#end-session-dialog-description");
const cancelEndSessionButton = $("#cancel-end-session");
const confirmEndSessionButton = $("#confirm-end-session");

const ACTIVE_VIEW_KEY = "oratranslate-active-view-v1";
const HOST_SESSION_KEY = "oratranslate-host-session-v2";
const LISTENER_SESSION_KEY = "oratranslate-listener-session-v2";
const LIVE_SESSION_CHANNEL_NAME = "oratranslate-live-session-v2";
const FRENCH_CAPTION_PLACEHOLDER = "La traduction française apparaîtra ici.";
const AUTO_SCROLL_THRESHOLD = 48;
const HOST_RESTORE_SETTLE_MILLISECONDS = 750;
const { applicationPath, websocketUrl } = window.oraTranslateUrls;
const tabId = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
const liveSessionChannel =
  "BroadcastChannel" in window
    ? new BroadcastChannel(LIVE_SESSION_CHANNEL_NAME)
    : null;

let currentRole = null;
let socket = null;
let mediaStream = null;
let audioContext = null;
let mediaSource = null;
let workletNode = null;
let silentGain = null;
let analyserNode = null;
let microphoneSamples = null;
let microphoneAnimationFrame = null;
let lastAudioLevelSentAt = 0;
let englishSegments = [];
let frenchSegments = [];
let partialTranscript = "";
let transcriptParagraph = null;
let translationParagraph = null;
let ownsHostLease = false;
let sessionStarting = false;
let stopping = false;
let sessionRejected = false;
let sessionEnded = false;
let intentionalDisconnect = false;
let resumePending = false;
let hostSessionState = "idle";
let hostListenerTotal = 0;
let hostPreparedReconnectTimer = null;
let listenerReconnectTimer = null;
let selectedSessionId = null;
let archivePlaybackClaim = null;
let scheduleState = null;
let selectedHostSessionCode = null;

function storageForSessionKey(key) {
  return key === HOST_SESSION_KEY ? localStorage : sessionStorage;
}

function readSessionValue(key) {
  const storage = storageForSessionKey(key);
  try {
    return JSON.parse(storage.getItem(key) || "null");
  } catch {
    try { storage.removeItem(key); } catch { /* ignore */ }
    return null;
  }
}

function writeSessionValue(key, value) {
  try { storageForSessionKey(key).setItem(key, JSON.stringify(value)); } catch { /* snapshots remain authoritative */ }
}

function removeSessionValue(key) {
  try { storageForSessionKey(key).removeItem(key); } catch { /* ignore */ }
}

function showLiveSurface(surface) {
  [roleEntry, hostWorkspace, listenerJoin, listenerWorkspace].forEach((item) => {
    item.hidden = item !== surface;
  });
}

function showRoleEntry() {
  currentRole = null;
  showLiveSurface(roleEntry);
  hostModeButton.focus();
}

function showHostWorkspace() {
  currentRole = "host";
  showLiveSurface(hostWorkspace);
}

function showListenerJoin() {
  currentRole = "listener";
  showLiveSurface(listenerJoin);
  setListenerCodeInputsDisabled(false);
  joinListenerButton.disabled = false;
  setListenerCodeError();
  focusListenerCodeInput();
}

function showListenerWorkspace() {
  currentRole = "listener";
  showLiveSurface(listenerWorkspace);
}

function setHostStatus(state, message) {
  statusBadge.dataset.state = state;
  statusBadge.textContent = state.replaceAll("_", " ");
  statusMessage.textContent = message;
}

function setListenerStatus(state, message) {
  listenerStatusBadge.dataset.state = state;
  listenerStatusBadge.textContent = state.replaceAll("_", " ");
  listenerStatusMessage.textContent = message;
}

function setMicrophoneState(state, label, message) {
  microphoneMonitor.dataset.state = state;
  microphoneLabel.textContent = label;
  microphoneMessage.textContent = message;
}

function setSpeakerState(state, label, message) {
  speakerMonitor.dataset.state = state;
  speakerMonitorLabel.textContent = label;
  speakerMonitorMessage.textContent = message;
}

function normalizeJoinCode(value) {
  const raw = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const match = raw.match(/^DAY([12])(AM|PM)$/);
  return match ? `DAY${match[1]}-${match[2]}` : String(value || "").trim().toUpperCase();
}

function listenerCodeValue() {
  return listenerCodeInputs.map((input) => input.value).join("");
}

function setListenerCodeValue(value) {
  const characters = String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, listenerCodeInputs.length);
  listenerCodeInputs.forEach((input, index) => {
    input.value = characters[index] || "";
  });
}

function setListenerCodeInputsDisabled(disabled) {
  listenerCodeInputs.forEach((input) => {
    input.disabled = disabled;
  });
}

function focusListenerCodeInput() {
  const nextInput = listenerCodeInputs.find((input) => !input.value);
  (nextInput || listenerCodeInputs.at(-1))?.focus();
}

function setListenerCodeError(message = "") {
  listenerCodeInputs[0].setCustomValidity(message);
  listenerCodeEntry.toggleAttribute("aria-invalid", Boolean(message));
  listenerCodeInputs.forEach((input) => {
    input.toggleAttribute("aria-invalid", Boolean(message));
  });
  listenerCodeMessage.textContent = message;
  listenerCodeMessage.hidden = !message;
}

function formatHostSessionOption(slot) {
  const dateLabel = String(slot.label || "")
    .split(" · ")[0]
    .replace(/^September /, "Sep ");
  return dateLabel ? `${slot.code} · ${dateLabel}` : slot.code;
}

function renderSessionSchedule() {
  const slots = scheduleState?.slots || [];
  const activeCode = normalizeJoinCode(scheduleState?.active_code);
  const slotCodes = slots.map((slot) => slot.code);
  const previousHostSelection = normalizeJoinCode(
    hostSessionCodeInput.value || selectedHostSessionCode,
  );
  hostSessionCodeInput.replaceChildren();
  slots.forEach((slot) => {
    const option = document.createElement("option");
    option.value = slot.code;
    option.textContent = formatHostSessionOption(slot);
    hostSessionCodeInput.append(option);
  });
  selectedHostSessionCode = activeCode
    || (slotCodes.includes(previousHostSelection) ? previousHostSelection : slotCodes[0])
    || null;
  hostSessionCodeInput.value = selectedHostSessionCode || "";
  updateHostControls();
}

function updateActiveScheduleStatus(state) {
  if (!scheduleState?.active_code) return;
  const slot = scheduleState.slots.find(
    (candidate) => candidate.code === scheduleState.active_code,
  );
  if (!slot) return;
  slot.status = state;
  renderSessionSchedule();
}

async function loadSessionSchedule() {
  try {
    const response = await fetch(applicationPath("/api/session-slots"), { cache: "no-store" });
    if (!response.ok) throw new Error("Scheduled sessions could not be loaded.");
    scheduleState = await response.json();
    renderSessionSchedule();
  } catch (error) {
    scheduleState = null;
    selectedHostSessionCode = null;
    hostSessionCodeInput.replaceChildren(new Option("Event sessions unavailable", ""));
    hostSessionCodeInput.disabled = true;
    setHostStatus("error", error.message);
    updateHostControls();
  }
}

function activateView(selectedTab, focusTab = false) {
  if (!selectedTab) return;
  viewTabs.forEach((tab) => {
    const selected = tab === selectedTab;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  viewPanels.forEach((panel) => { panel.hidden = panel.id !== selectedTab.dataset.panel; });
  try { sessionStorage.setItem(ACTIVE_VIEW_KEY, selectedTab.id); } catch { /* ignore */ }
  if (selectedTab.id === "session-archives-tab") loadSessions();
  if (focusTab) selectedTab.focus();
}

function restoreActiveView() {
  let selectedTabId = "live-session-tab";
  try { selectedTabId = sessionStorage.getItem(ACTIVE_VIEW_KEY) || selectedTabId; } catch { /* ignore */ }
  activateView(viewTabs.find((tab) => tab.id === selectedTabId) || viewTabs[0]);
}

viewTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateView(tab));
  tab.addEventListener("keydown", (event) => {
    let nextIndex = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % viewTabs.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + viewTabs.length) % viewTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = viewTabs.length - 1;
    if (nextIndex !== null) {
      event.preventDefault();
      activateView(viewTabs[nextIndex], true);
    }
  });
});

function updateHostControls() {
  const prepared = ownsHostLease && hostSessionState === "prepared";
  const liveOrConnecting = ownsHostLease && ["connecting", "live"].includes(hostSessionState);
  const sessionOwned = ownsHostLease || resumePending;
  startButton.textContent = resumePending
    ? (hostSessionState === "prepared" ? "Reconnect waiting room" : "Reconnect microphone")
    : (prepared
      ? "Start live session"
      : (selectedHostSessionCode ? "Prepare session" : "Choose a session"));
  startButton.disabled = sessionStarting
    || stopping
    || liveOrConnecting
    || (ownsHostLease && !prepared)
    || (!sessionOwned && !selectedHostSessionCode);
  hostSessionCodeInput.disabled = sessionOwned
    || sessionStarting
    || stopping
    || !scheduleState?.slots?.length;
  stopButton.hidden = !sessionOwned;
  stopButton.textContent = hostSessionState === "prepared" ? "Cancel session" : "End session";
  stopButton.disabled = !ownsHostLease || stopping || hostSessionState === "connecting";
  hostBackButton.disabled = sessionOwned;
}

function updateHostListenerCount(value) {
  hostListenerTotal = Math.max(0, Number(value) || 0);
  hostListenerCount.textContent = String(hostListenerTotal);
  hostListenerLabel.textContent = hostListenerTotal === 1
    ? " listener connected"
    : " listeners connected";
}

function renderLevel(levelElement, bars, level) {
  const bounded = Math.max(0, Math.min(100, Math.round(Number(level) || 0)));
  const active = Math.ceil((bounded / 100) * bars.length);
  levelElement.setAttribute("aria-valuenow", String(bounded));
  bars.forEach((bar, index) => bar.classList.toggle("is-active", index < active));
}

function stopMicrophoneMeter() {
  if (microphoneAnimationFrame !== null) cancelAnimationFrame(microphoneAnimationFrame);
  microphoneAnimationFrame = null;
  microphoneSamples = null;
  renderLevel(microphoneLevel, microphoneBars, 0);
}

function startMicrophoneMeter() {
  stopMicrophoneMeter();
  microphoneSamples = new Uint8Array(analyserNode.fftSize);
  const updateMeter = (timestamp) => {
    if (!analyserNode || !microphoneSamples) return;
    analyserNode.getByteTimeDomainData(microphoneSamples);
    let sum = 0;
    microphoneSamples.forEach((sample) => {
      const centered = (sample - 128) / 128;
      sum += centered * centered;
    });
    const level = Math.max(0, Math.sqrt(sum / microphoneSamples.length) - 0.01) * 650;
    renderLevel(microphoneLevel, microphoneBars, level);
    if (timestamp - lastAudioLevelSentAt >= 150 && socket?.readyState === WebSocket.OPEN && ownsHostLease) {
      socket.send(JSON.stringify({ type: "audio_level", level }));
      lastAudioLevelSentAt = timestamp;
    }
    microphoneAnimationFrame = requestAnimationFrame(updateMeter);
  };
  microphoneAnimationFrame = requestAnimationFrame(updateMeter);
}

function resetHostTranscript() {
  transcriptList.replaceChildren(emptyTranscript);
  emptyTranscript.hidden = false;
  englishSegments = [];
  partialTranscript = "";
  transcriptParagraph = null;
  hostAlerts.replaceChildren();
}

function resetListenerTranscript() {
  translationList.replaceChildren(emptyTranslation);
  emptyTranslation.hidden = false;
  frenchSegments = [];
  translationParagraph = null;
  currentFrenchCaption.textContent = FRENCH_CAPTION_PLACEHOLDER;
  currentFrenchCaption.classList.add("waiting-caption");
  translationAlerts.replaceChildren();
}

function isNearScrollEnd(element) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= AUTO_SCROLL_THRESHOLD;
}

function renderTranscript() {
  const followLatest = isNearScrollEnd(transcriptList);
  emptyTranscript.hidden = true;
  if (!transcriptParagraph) {
    transcriptParagraph = document.createElement("p");
    transcriptParagraph.className = "continuous-text";
    transcriptList.append(transcriptParagraph);
  }
  const finalText = englishSegments.join(" ");
  transcriptParagraph.replaceChildren(document.createTextNode(finalText));
  if (partialTranscript) {
    if (finalText) transcriptParagraph.append(document.createTextNode(" "));
    const partial = document.createElement("span");
    partial.className = "partial-text";
    partial.textContent = partialTranscript;
    transcriptParagraph.append(partial);
  }
  if (followLatest) transcriptList.scrollTop = transcriptList.scrollHeight;
}

function appendTranscript(text, isFinal) {
  const value = String(text || "").trim();
  if (!value) return;
  if (isFinal) { englishSegments.push(value); partialTranscript = ""; }
  else partialTranscript = value;
  renderTranscript();
}

function renderTranslation() {
  emptyTranslation.hidden = true;
  const latestTranslation = frenchSegments.at(-1);
  currentFrenchCaption.textContent = latestTranslation || FRENCH_CAPTION_PLACEHOLDER;
  currentFrenchCaption.classList.toggle("waiting-caption", !latestTranslation);
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
  const value = String(event.french || "").trim();
  if (!value) return;
  frenchSegments.push(value);
  renderTranslation();
}

function appendNotice(event) {
  const target = currentRole === "host" ? hostAlerts : translationAlerts;
  const banner = document.createElement("article");
  banner.className = "message-banner";
  banner.dataset.severity = "danger";
  const content = document.createElement("div");
  content.className = "message-banner__content";
  const title = document.createElement("strong");
  title.className = "message-banner__title";
  title.textContent = event.stage === "translation"
    ? "A French caption could not be translated."
    : `${event.stage || "Session"} error`;
  const summary = document.createElement("p");
  summary.className = "message-banner__summary";
  summary.textContent = event.stage === "translation"
    ? "The session is continuing, but a short section may be missing."
    : event.message;
  content.append(title, summary);
  const details = document.createElement("details");
  const detailsSummary = document.createElement("summary");
  detailsSummary.textContent = "Technical details";
  const detailsContent = document.createElement("div");
  detailsContent.className = "message-banner__technical-details";
  const message = document.createElement("p");
  message.textContent = event.message;
  detailsContent.append(message);
  if (event.opc_request_id) {
    const code = document.createElement("code");
    code.textContent = `OPC request ID: ${event.opc_request_id}`;
    detailsContent.append(code);
  }
  details.append(detailsSummary, detailsContent);
  content.append(details);
  banner.append(content);
  target.append(banner);
}

function showListenerSessionState(state, hostConnected = true, resumeState = "") {
  if (state === "prepared") {
    listenerLiveMarker.textContent = "Waiting";
    listenerLiveMarker.dataset.state = "waiting";
    setListenerStatus("host_ready", "The host is connected. Waiting for the session to start.");
    setSpeakerState("idle", "Speaker microphone off", "Recording has not started.");
    renderLevel(speakerLevel, speakerBars, 0);
    return;
  }
  if (state === "connecting") {
    listenerLiveMarker.textContent = "Starting";
    listenerLiveMarker.dataset.state = "starting";
    setListenerStatus("connecting", "The host is starting the live session.");
    setSpeakerState("connecting", "Speaker microphone", "Connecting to the host audio level.");
    return;
  }
  if (state === "host_reconnecting") {
    if (resumeState === "prepared") {
      listenerLiveMarker.textContent = "Waiting";
      listenerLiveMarker.dataset.state = "waiting";
      setListenerStatus("host_reconnecting", "The host is reconnecting. The waiting room remains open.");
      setSpeakerState("idle", "Speaker microphone off", "Recording has not started.");
      renderLevel(speakerLevel, speakerBars, 0);
      return;
    }
    listenerLiveMarker.textContent = "Paused";
    listenerLiveMarker.dataset.state = "paused";
    setListenerStatus("host_reconnecting", hostConnected
      ? "The host is reconnecting."
      : "Speaker reconnecting. Captions are paused.");
    setSpeakerState("paused", "Speaker reconnecting", "Captions will resume when the host returns.");
    renderLevel(speakerLevel, speakerBars, 0);
    return;
  }
  listenerLiveMarker.textContent = "Live";
  listenerLiveMarker.dataset.state = "live";
  setListenerStatus("live", "French captions are live.");
}

function applySnapshot(event) {
  const session = event.session || {};
  if (event.role === "host") {
    showHostWorkspace();
    selectedHostSessionCode = session.session_code || event.join_code || selectedHostSessionCode;
    updateHostListenerCount(session.listener_count || 0);
    englishSegments = Array.isArray(event.english_segments) ? event.english_segments : [];
    partialTranscript = event.partial_english || "";
    transcriptParagraph = null;
    transcriptList.replaceChildren(emptyTranscript);
    if (englishSegments.length || partialTranscript) renderTranscript();
    ownsHostLease = true;
    resumePending = false;
    hostSessionState = session.state || "live";
    persistHostState(hostSessionState);
    if (hostSessionState === "prepared") {
      setHostStatus("prepared", "Waiting room ready. Start when the listener is connected.");
      setMicrophoneState("idle", "Microphone off", "Recording starts only when you select Start live session.");
    } else {
      setHostStatus(hostSessionState, hostSessionState === "live"
        ? "Live session in progress."
        : "Connecting to OCI Speech Realtime...");
    }
    updateHostControls();
    if (session.state === "live" && mediaStream && !workletNode) {
      beginAudioCapture(16000).catch(handleHostError);
    }
  } else {
    showListenerWorkspace();
    listenerSessionTitle.textContent = session.title || "Live session";
    listenerSessionCode.textContent = session.session_code || listenerSessionCode.textContent;
    frenchSegments = Array.isArray(event.french_segments) ? event.french_segments : [];
    translationParagraph = null;
    translationList.replaceChildren(emptyTranslation);
    if (frenchSegments.length) renderTranslation();
    renderLevel(speakerLevel, speakerBars, event.latest_audio_level || 0);
    showListenerSessionState(
      session.state || "live",
      session.host_connected,
      session.resume_state || "",
    );
  }
}

async function beginAudioCapture(sampleRate) {
  if (!audioContext || !mediaStream || workletNode) return;
  await audioContext.audioWorklet.addModule(applicationPath("/audio-worklet.js"));
  await audioContext.resume();
  mediaSource = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm16-resampler");
  silentGain = audioContext.createGain();
  analyserNode = audioContext.createAnalyser();
  analyserNode.fftSize = 256;
  analyserNode.smoothingTimeConstant = 0.72;
  silentGain.gain.value = 0;
  workletNode.port.onmessage = (event) => {
    if (socket?.readyState === WebSocket.OPEN && ownsHostLease) socket.send(event.data);
  };
  mediaSource.connect(workletNode);
  mediaSource.connect(analyserNode);
  workletNode.connect(silentGain);
  silentGain.connect(audioContext.destination);
  startMicrophoneMeter();
  setMicrophoneState("active", "Microphone active", "Audio is being captured from this device.");
  setHostStatus("live", `Listening at ${sampleRate / 1000} kHz PCM`);
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
  if (audioContext && audioContext.state !== "closed") await audioContext.close();
  mediaStream = null;
  audioContext = null;
  mediaSource = null;
  analyserNode = null;
  workletNode = null;
  silentGain = null;
}

async function acquireMicrophone() {
  setMicrophoneState("requesting", "Microphone permission", "Allow microphone access when Chrome asks.");
  audioContext = new AudioContext();
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  setMicrophoneState("connecting", "Microphone connected", "Connecting securely to the live session...");
}

function persistHostSession(event) {
  writeSessionValue(HOST_SESSION_KEY, {
    session_id: event.session_id,
    resume_token: event.resume_token,
    join_code: event.join_code,
    session_code: event.session_code || event.join_code,
    session_label: event.session_label,
    title: event.title,
    state: event.state || "prepared",
  });
}

function persistHostState(state) {
  const stored = readSessionValue(HOST_SESSION_KEY);
  if (!stored) return;
  writeSessionValue(HOST_SESSION_KEY, { ...stored, state });
}

function handleServerEvent(event) {
  switch (event.type) {
    case "session_prepared":
      persistHostSession({ ...event, state: "prepared" });
      selectedHostSessionCode = event.session_code || event.join_code;
      ownsHostLease = true;
      hostSessionState = "prepared";
      sessionStarting = false;
      updateHostListenerCount(0);
      broadcastLiveSessionState("reserved");
      setHostStatus("prepared", "Waiting room ready. Start when the listener is connected.");
      setMicrophoneState("idle", "Microphone off", "Recording starts only when you select Start live session.");
      updateHostControls();
      loadSessionSchedule();
      break;
    case "session_snapshot": applySnapshot(event); break;
    case "session_status":
      updateActiveScheduleStatus(event.state);
      if (currentRole === "host") {
        hostSessionState = event.state;
        persistHostState(hostSessionState);
        setHostStatus(event.state, event.message);
        updateHostControls();
      }
      else {
        showListenerSessionState(
          event.state,
          event.state !== "host_reconnecting",
          event.resume_state || "",
        );
      }
      break;
    case "session_ready":
      updateActiveScheduleStatus("live");
      if (currentRole === "host" && !stopping) {
        hostSessionState = "live";
        sessionStarting = false;
        persistHostState("live");
        beginAudioCapture(event.sample_rate).catch(handleHostError);
        updateHostControls();
      } else if (currentRole === "listener") showListenerSessionState("live");
      break;
    case "transcript": if (currentRole === "host") appendTranscript(event.text, event.is_final); break;
    case "translation": if (currentRole === "listener") appendTranslation(event); break;
    case "audio_level":
      if (currentRole === "listener") {
        renderLevel(speakerLevel, speakerBars, event.level);
        setSpeakerState("active", "Speaker microphone active", "Audio is being captured on the host device.");
      }
      break;
    case "listener_count":
      updateHostListenerCount(event.count || 0);
      if (currentRole === "host" && hostSessionState === "prepared") {
        setHostStatus("prepared", hostListenerTotal
          ? "Listener ready. Start when the speaker is ready."
          : "Waiting for the listener to join this event session.");
      }
      break;
    case "queue_status": if (currentRole === "host") setHostStatus("delayed", event.message); break;
    case "error":
      appendNotice(event);
      if (currentRole === "host" && ["speech", "session", "audio"].includes(event.stage)) {
        setHostStatus("error", event.message);
        setMicrophoneState("error", "Microphone stream interrupted", event.message);
      }
      break;
    case "session_saved": loadSessions(event.session?.session_id || null); break;
    case "session_ended":
      sessionEnded = true;
      stopping = false;
      ownsHostLease = false;
      hostSessionState = "ended";
      updateHostListenerCount(0);
      removeSessionValue(HOST_SESSION_KEY);
      broadcastLiveSessionState("inactive");
      if (currentRole === "host") {
        if (event.archived) {
          const slots = scheduleState?.slots || [];
          const currentIndex = slots.findIndex(
            (slot) => slot.code === selectedHostSessionCode,
          );
          if (currentIndex >= 0 && currentIndex < slots.length - 1) {
            selectedHostSessionCode = slots[currentIndex + 1].code;
          }
        }
        releaseAudio().catch(() => {});
        setHostStatus("ended", event.message);
        setMicrophoneState("idle", "Microphone off", event.archived
          ? "The recording has ended."
          : "No recording was created.");
      } else {
        removeSessionValue(LISTENER_SESSION_KEY);
        listenerLiveMarker.textContent = "Ended";
        listenerLiveMarker.dataset.state = "ended";
        setListenerStatus("ended", event.archived
          ? "The session has ended. The archive is now available."
          : event.message);
        setSpeakerState("idle", "Speaker microphone off", event.archived
          ? "No more live audio is being captured."
          : "Recording did not start.");
      }
      updateHostControls();
      loadSessionSchedule();
      break;
    case "session_rejected":
      sessionRejected = true;
      if (currentRole === "listener") {
        removeSessionValue(LISTENER_SESSION_KEY);
        showListenerJoin();
        setListenerCodeError(event.message);
        joinListenerButton.disabled = false;
      } else {
        setHostStatus("unavailable", event.message);
        ownsHostLease = false;
        hostSessionState = "idle";
        sessionStarting = false;
        resumePending = false;
        if (event.code !== "LIVE_SESSION_ACTIVE") removeSessionValue(HOST_SESSION_KEY);
        releaseAudio().catch(() => {});
        updateHostControls();
      }
      break;
    default: break;
  }
}

function connectLiveSocket(command) {
  intentionalDisconnect = false;
  sessionRejected = false;
  sessionEnded = false;
  socket = new WebSocket(websocketUrl("/ws/live"));
  socket.binaryType = "arraybuffer";
  socket.onopen = () => socket.send(JSON.stringify(command));
  socket.onmessage = (message) => handleServerEvent(JSON.parse(message.data));
  socket.onerror = () => {
    if (sessionRejected) return;
    const message = "The browser couldn't reach the OraTranslate server.";
    if (currentRole === "host") handleHostError(new Error(message));
    else setListenerStatus("error", message);
  };
  socket.onclose = async () => {
    socket = null;
    ownsHostLease = false;
    sessionStarting = false;
    stopping = false;
    updateHostControls();
    if (intentionalDisconnect || sessionRejected || sessionEnded) return;
    if (currentRole === "host") {
      await releaseAudio();
      const storedHost = readSessionValue(HOST_SESSION_KEY);
      if (storedHost?.state === "prepared") {
        hostSessionState = "prepared";
        setHostStatus("host_reconnecting", "Rejoining the waiting room...");
        setMicrophoneState("idle", "Microphone off", "Recording has not started.");
        schedulePreparedHostReconnect();
        updateHostControls();
      } else if (storedHost) {
        resumePending = true;
        hostSessionState = "host_reconnecting";
        setHostStatus("host_reconnecting", "Reconnect the microphone to continue this session.");
        setMicrophoneState("paused", "Microphone disconnected", "Select Reconnect microphone within the recovery window.");
        updateHostControls();
      }
    } else if (currentRole === "listener") {
      setListenerStatus("reconnecting", "Connection lost. Rejoining the session...");
      scheduleListenerReconnect();
    }
  };
}

function schedulePreparedHostReconnect() {
  clearTimeout(hostPreparedReconnectTimer);
  const stored = readSessionValue(HOST_SESSION_KEY);
  if (!stored?.session_id || !stored?.resume_token || intentionalDisconnect) return;
  hostPreparedReconnectTimer = setTimeout(() => {
    if (!socket && currentRole === "host") {
      connectLiveSocket({
        type: "resume",
        session_id: stored.session_id,
        resume_token: stored.resume_token,
      });
    }
  }, 2000);
}

function handleHostError(error) {
  const message = error?.message || String(error);
  appendNotice({ type: "error", stage: "browser", message });
  setHostStatus("error", message);
  setMicrophoneState("error", "Microphone unavailable", message);
  sessionStarting = false;
  ownsHostLease = false;
  releaseAudio().catch(() => {});
  socket?.close();
  updateHostControls();
}

function handleActivationError(error) {
  const message = error?.message || String(error);
  appendNotice({ type: "error", stage: "browser", message });
  setHostStatus("prepared", "The microphone is unavailable. Fix it, then try again.");
  setMicrophoneState("error", "Microphone unavailable", message);
  hostSessionState = "prepared";
  sessionStarting = false;
  releaseAudio().catch(() => {});
  updateHostControls();
}

async function startHostSession() {
  if (sessionStarting || stopping) return;
  if (ownsHostLease && hostSessionState === "prepared") {
    if (hostListenerTotal === 0) {
      startWithoutListenerDialog.showModal();
      cancelStartWithoutListenerButton.focus();
      return;
    }
    await activatePreparedSession();
    return;
  }
  const storedHost = readSessionValue(HOST_SESSION_KEY);
  if (resumePending && storedHost) {
    sessionStarting = true;
    updateHostControls();
    try {
      if (storedHost.state !== "prepared") await acquireMicrophone();
      connectLiveSocket({ type: "resume", session_id: storedHost.session_id, resume_token: storedHost.resume_token });
    } catch (error) {
      handleHostError(error);
      resumePending = true;
      updateHostControls();
    }
    return;
  }
  if (ownsHostLease || socket) return;
  if (!selectedHostSessionCode) {
    setHostStatus("unavailable", "Choose an event session first.");
    return;
  }
  sessionStarting = true;
  updateHostControls();
  try {
    const response = await fetch(applicationPath("/api/live-session"), { cache: "no-store" });
    const status = response.ok ? await response.json() : { active: false };
    if (status.active) throw new Error("Another OraTranslate session is already active. Join it as a listener or wait for it to end.");
    resetHostTranscript();
    setHostStatus("preparing", "Preparing the waiting room...");
    setMicrophoneState("idle", "Microphone off", "Recording starts only when you select Start live session.");
    connectLiveSocket({ type: "prepare", session_code: selectedHostSessionCode });
  } catch (error) { handleHostError(error); }
}

async function activatePreparedSession() {
  if (
    sessionStarting
    || stopping
    || !ownsHostLease
    || hostSessionState !== "prepared"
    || socket?.readyState !== WebSocket.OPEN
  ) return;
  sessionStarting = true;
  updateHostControls();
  setHostStatus("microphone", "Waiting for microphone permission...");
  try {
    await acquireMicrophone();
    pauseArchivePlaybackAcrossTabs();
    broadcastLiveSessionState("active");
    hostSessionState = "connecting";
    persistHostState("connecting");
    socket.send(JSON.stringify({ type: "activate" }));
    setHostStatus("connecting", "Connecting to OCI Speech Realtime...");
  } catch (error) {
    handleActivationError(error);
  }
}

async function stopHostSession() {
  if (stopping || !ownsHostLease) return;
  stopping = true;
  updateHostControls();
  if (hostSessionState === "prepared") {
    intentionalDisconnect = true;
    setHostStatus("closing", "Closing the waiting room...");
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "cancel" }));
    }
    return;
  }
  endSessionDialogDescription.textContent =
    `Ending ${selectedHostSessionCode} stops recording and saves this run. `
    + "The event code remains available if another run is needed.";
  endSessionDialog.showModal();
  cancelEndSessionButton.focus();
}

async function confirmEndHostSession() {
  endSessionDialog.close();
  setHostStatus("finalizing", "Sending the final audio and translations...");
  setMicrophoneState("connecting", "Microphone stopped", "Finalizing the recording...");
  await releaseAudio();
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
}

function joinListener() {
  const joinCode = normalizeJoinCode(listenerCodeValue());
  setListenerCodeValue(joinCode);
  setListenerCodeError();
  if (!joinCode) {
    setListenerCodeError("Enter a session code.");
    focusListenerCodeInput();
    return;
  }
  if (!/^DAY[12]-(AM|PM)$/.test(joinCode)) {
    setListenerCodeError("Enter one of the event codes shown above.");
    focusListenerCodeInput();
    return;
  }
  joinListenerButton.disabled = true;
  writeSessionValue(LISTENER_SESSION_KEY, { join_code: joinCode });
  listenerSessionCode.textContent = joinCode;
  showListenerWorkspace();
  resetListenerTranscript();
  setListenerStatus("connecting", "Joining the live session...");
  listenerLiveMarker.textContent = "Waiting";
  listenerLiveMarker.dataset.state = "waiting";
  setSpeakerState("idle", "Speaker microphone off", "Waiting for the host to start.");
  pauseArchivePlaybackAcrossTabs();
  connectLiveSocket({ type: "join", join_code: joinCode });
}

function scheduleListenerReconnect() {
  clearTimeout(listenerReconnectTimer);
  const stored = readSessionValue(LISTENER_SESSION_KEY);
  if (!stored?.join_code || intentionalDisconnect) return;
  listenerReconnectTimer = setTimeout(() => {
    if (!socket && currentRole === "listener") connectLiveSocket({ type: "join", join_code: stored.join_code });
  }, 2000);
}

function leaveListenerSession() {
  intentionalDisconnect = true;
  clearTimeout(listenerReconnectTimer);
  removeSessionValue(LISTENER_SESSION_KEY);
  socket?.close();
  socket = null;
  resetListenerTranscript();
  showListenerJoin();
}

function liveSessionInProgress() {
  return Boolean(socket || ownsHostLease || sessionStarting || readSessionValue(HOST_SESSION_KEY) || readSessionValue(LISTENER_SESSION_KEY));
}

function pauseSavedAudioForCoordination() {
  archivePlaybackClaim = null;
  if (!savedAudio.paused) savedAudio.pause();
}

function pauseArchivePlaybackAcrossTabs() {
  pauseSavedAudioForCoordination();
  liveSessionChannel?.postMessage({ type: "archive_playback_pause", reason: "live_session", tabId });
}

function broadcastLiveSessionState(state) {
  liveSessionChannel?.postMessage({ type: "live_session_state", state, tabId });
}

function claimArchivePlayback() {
  const claim = { type: "archive_playback_started", tabId, sessionId: selectedSessionId, startedAt: Date.now() };
  archivePlaybackClaim = claim;
  liveSessionChannel?.postMessage(claim);
}

function claimComesAfter(candidate, current) {
  if (!current) return true;
  if (candidate.startedAt !== current.startedAt) return candidate.startedAt > current.startedAt;
  return String(candidate.tabId) > String(current.tabId);
}

liveSessionChannel?.addEventListener("message", (event) => {
  if (event.data?.tabId === tabId) return;
  if (event.data?.type === "archive_playback_started" && !savedAudio.paused && claimComesAfter(event.data, archivePlaybackClaim)) pauseSavedAudioForCoordination();
  if (event.data?.type === "archive_playback_pause") pauseSavedAudioForCoordination();
  if (event.data?.type === "live_session_state" && event.data.state === "active") pauseSavedAudioForCoordination();
});

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  return [hours, minutes, remaining]
    .filter((_, index) => hours > 0 || index > 0)
    .map((value) => String(value).padStart(2, "0"))
    .join(":");
}

function formatSessionDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown date" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function formatSessionStatus(value) {
  return String(value || "saved")
    .trim()
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (character) => character.toLocaleUpperCase());
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
    card.setAttribute("aria-current", String(card.dataset.sessionId === sessionId));
  });
  emptyViewer.hidden = false;
  emptyViewer.textContent = "Loading saved session...";
  sessionDetail.hidden = true;
  try {
    const response = await fetch(applicationPath(`/api/sessions/${encodeURIComponent(sessionId)}`), { cache: "no-store" });
    if (!response.ok) throw new Error("The saved session could not be loaded.");
    const session = await response.json();
    if (selectedSessionId !== sessionId) return;
    const sessionStatus = String(session.status || "saved").toLocaleLowerCase();
    savedSessionStatus.textContent = formatSessionStatus(sessionStatus);
    savedSessionStatus.dataset.status = sessionStatus;
    savedSessionTitle.textContent = session.title || "Live session";
    savedSessionMeta.textContent = `${formatSessionDate(session.started_at)} · ${formatDuration(session.duration_seconds)}`;
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
    savedEnglish.textContent = session.english_text || "No English transcript was captured.";
    savedFrench.textContent = session.french_text || "No French translation was captured.";
    emptyViewer.hidden = true;
    sessionDetail.hidden = false;
  } catch (error) { clearSessionViewer(error?.message || "The saved session could not be loaded."); }
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
    details.textContent = `${formatSessionDate(session.started_at)} · ${formatDuration(session.duration_seconds)} · ${formatSessionStatus(session.status)}`;
    card.append(title, details);
    card.addEventListener("click", () => selectSession(session.session_id));
    return card;
  });
  sessionList.replaceChildren(...cards);
  const ids = new Set(sessions.map((session) => session.session_id));
  const next = ids.has(preferredSessionId) ? preferredSessionId : ids.has(selectedSessionId) ? selectedSessionId : sessions[0].session_id;
  selectSession(next);
}

async function loadSessions(preferredSessionId = null) {
  refreshSessionsButton.disabled = true;
  try {
    const response = await fetch(applicationPath("/api/sessions"), { cache: "no-store" });
    if (!response.ok) throw new Error("Saved sessions could not be loaded.");
    const payload = await response.json();
    renderSessionList(Array.isArray(payload.sessions) ? payload.sessions : [], preferredSessionId);
  } catch (error) {
    showSessionListMessage(error?.message || "Saved sessions could not be loaded.");
    clearSessionViewer();
  } finally { refreshSessionsButton.disabled = false; }
}

function hideArchiveMessage() { archiveMessage.replaceChildren(); }

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
  if (!selectedSessionId || deleteSessionDialog.open) return;
  hideArchiveMessage();
  deleteDialogSessionName.textContent = savedSessionTitle.textContent || "This saved session";
  deleteSessionDialog.showModal();
  cancelDeleteSessionButton.focus();
}

async function deleteSelectedSession() {
  const sessionId = selectedSessionId;
  if (!sessionId) return;
  deleteSessionButton.disabled = true;
  cancelDeleteSessionButton.disabled = true;
  confirmDeleteSessionButton.disabled = true;
  try {
    const response = await fetch(applicationPath(`/api/sessions/${encodeURIComponent(sessionId)}`), { method: "DELETE" });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || "The saved session could not be deleted.");
    }
    deleteSessionDialog.close();
    clearSessionViewer();
    await loadSessions();
    showArchiveMessage("Session deleted", "The saved recording, transcripts, report, metadata, and diagnostics were deleted.", "success");
  } catch (error) {
    deleteSessionDialog.close();
    showArchiveMessage("Session not deleted", error?.message || "The saved session could not be deleted.");
  } finally {
    deleteSessionButton.disabled = false;
    cancelDeleteSessionButton.disabled = false;
    confirmDeleteSessionButton.disabled = false;
  }
}

async function fetchLiveSessionStatus() {
  const response = await fetch(applicationPath("/api/live-session"), { cache: "no-store" });
  if (!response.ok) throw new Error("The active session status is unavailable.");
  return response.json();
}

function showStoredHostRecovery(host, autoResumePrepared = false) {
  showHostWorkspace();
  selectedHostSessionCode = host.session_code || host.join_code || selectedHostSessionCode;
  if (host.state === "prepared") {
    hostSessionState = "prepared";
    setHostStatus("host_reconnecting", autoResumePrepared
      ? "Rejoining the waiting room..."
      : "Reconnect to continue preparing this session.");
    setMicrophoneState("idle", "Microphone off", "Recording has not started.");
    if (autoResumePrepared) {
      connectLiveSocket({ type: "resume", session_id: host.session_id, resume_token: host.resume_token });
    } else {
      resumePending = true;
    }
  } else {
    resumePending = true;
    hostSessionState = "host_reconnecting";
    setHostStatus("host_reconnecting", "Reconnect the microphone to continue this session.");
    setMicrophoneState("paused", "Microphone disconnected", "Select Reconnect microphone within the recovery window.");
  }
  updateHostControls();
}

async function restoreRole() {
  const listener = readSessionValue(LISTENER_SESSION_KEY);
  if (listener?.join_code) {
    showListenerWorkspace();
    listenerSessionCode.textContent = listener.join_code;
    setListenerStatus("reconnecting", "Rejoining the live session...");
    connectLiveSocket({ type: "join", join_code: listener.join_code });
    return;
  }

  const host = readSessionValue(HOST_SESSION_KEY);
  if (host?.session_id && host?.resume_token) {
    try {
      let status = await fetchLiveSessionStatus();
      if (
        status.active
        && status.session_id === host.session_id
        && status.host_connected
      ) {
        await new Promise((resolve) => setTimeout(resolve, HOST_RESTORE_SETTLE_MILLISECONDS));
        status = await fetchLiveSessionStatus();
      }
      if (status.active && status.session_id === host.session_id) {
        if (!status.host_connected) {
          showStoredHostRecovery(host, host.state === "prepared");
          return;
        }
        showLiveSurface(roleEntry);
        return;
      }
      removeSessionValue(HOST_SESSION_KEY);
    } catch {
      showStoredHostRecovery(host);
      return;
    }
  }
  showLiveSurface(roleEntry);
}

hostModeButton.addEventListener("click", () => { showHostWorkspace(); updateHostControls(); startButton.focus(); });
listenerModeButton.addEventListener("click", showListenerJoin);
hostBackButton.addEventListener("click", showRoleEntry);
listenerBackButton.addEventListener("click", showRoleEntry);
leaveListenerButton.addEventListener("click", leaveListenerSession);
startButton.addEventListener("click", startHostSession);
stopButton.addEventListener("click", stopHostSession);
joinListenerButton.addEventListener("click", joinListener);
listenerCodeInputs.forEach((input, inputIndex) => {
  input.addEventListener("input", () => {
    const characters = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (characters.length > 1) {
      const current = listenerCodeInputs.map((candidate) => candidate.value);
      characters.split("").forEach((character, offset) => {
        if (listenerCodeInputs[inputIndex + offset]) current[inputIndex + offset] = character;
      });
      setListenerCodeValue(current.join(""));
    } else {
      input.value = characters;
    }
    setListenerCodeError();
    if (input.value && listenerCodeInputs[inputIndex + 1]) {
      listenerCodeInputs[inputIndex + 1].focus();
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !joinListenerButton.disabled) {
      joinListener();
      return;
    }
    if (event.key === "Backspace" && !input.value && listenerCodeInputs[inputIndex - 1]) {
      listenerCodeInputs[inputIndex - 1].focus();
      listenerCodeInputs[inputIndex - 1].value = "";
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowLeft" && listenerCodeInputs[inputIndex - 1]) {
      listenerCodeInputs[inputIndex - 1].focus();
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowRight" && listenerCodeInputs[inputIndex + 1]) {
      listenerCodeInputs[inputIndex + 1].focus();
      event.preventDefault();
    }
  });
  input.addEventListener("paste", (event) => {
    const pastedCode = event.clipboardData?.getData("text") || "";
    const characters = pastedCode.toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!characters) return;
    event.preventDefault();
    setListenerCodeValue(characters);
    setListenerCodeError();
    focusListenerCodeInput();
  });
});
hostSessionCodeInput.addEventListener("change", () => {
  selectedHostSessionCode = normalizeJoinCode(hostSessionCodeInput.value);
  setHostStatus("idle", `Ready to prepare ${selectedHostSessionCode}.`);
  updateHostControls();
});
cancelStartWithoutListenerButton.addEventListener("click", () => startWithoutListenerDialog.close());
confirmStartWithoutListenerButton.addEventListener("click", () => {
  startWithoutListenerDialog.close();
  activatePreparedSession();
});
startWithoutListenerDialog.addEventListener("cancel", () => cancelStartWithoutListenerButton.focus());
cancelEndSessionButton.addEventListener("click", () => {
  endSessionDialog.close();
  stopping = false;
  updateHostControls();
});
confirmEndSessionButton.addEventListener("click", confirmEndHostSession);
endSessionDialog.addEventListener("cancel", () => {
  stopping = false;
  updateHostControls();
});
refreshSessionsButton.addEventListener("click", () => loadSessions());
deleteSessionButton.addEventListener("click", openDeleteSessionDialog);
cancelDeleteSessionButton.addEventListener("click", () => deleteSessionDialog.close());
confirmDeleteSessionButton.addEventListener("click", deleteSelectedSession);
deleteSessionDialog.addEventListener("cancel", (event) => { if (confirmDeleteSessionButton.disabled) event.preventDefault(); });
savedAudio.addEventListener("play", () => { if (liveSessionInProgress()) pauseSavedAudioForCoordination(); else claimArchivePlayback(); });
savedAudio.addEventListener("pause", () => { archivePlaybackClaim = null; });
savedAudio.addEventListener("ended", () => { archivePlaybackClaim = null; });

resetHostTranscript();
resetListenerTranscript();
restoreActiveView();
updateHostControls();
loadSessionSchedule()
  .catch(() => {})
  .finally(() => restoreRole().catch(showRoleEntry));
