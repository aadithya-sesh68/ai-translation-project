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
const sessionTitleInput = $("#session-title");
const sessionTitleMessage = $("#session-title-message");
const statusBadge = $("#status-badge");
const statusMessage = $("#status-message");
const microphoneMonitor = $("#microphone-monitor");
const microphoneLabel = $("#microphone-label");
const microphoneMessage = $("#microphone-message");
const microphoneLevel = $("#microphone-level");
const microphoneBars = [...microphoneLevel.querySelectorAll("span")];
const hostSharePanel = $("#host-share-panel");
const hostJoinCode = $("#host-join-code");
const copyJoinCodeButton = $("#copy-join-code");
const hostListenerCount = $("#host-listener-count");
const transcriptList = $("#transcript-list");
const emptyTranscript = $("#empty-transcript");
const hostAlerts = $("#host-alerts");
const listenerCodeInput = $("#listener-code");
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
const downloadReport = $("#download-report");
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

const ACTIVE_VIEW_KEY = "oratranslate-active-view-v1";
const HOST_SESSION_KEY = "oratranslate-host-session-v2";
const LISTENER_SESSION_KEY = "oratranslate-listener-session-v2";
const LIVE_SESSION_CHANNEL_NAME = "oratranslate-live-session-v2";
const FRENCH_CAPTION_PLACEHOLDER = "La traduction française apparaîtra ici.";
const AUTO_SCROLL_THRESHOLD = 48;
const SESSION_TITLE_CONFLICT_MESSAGE =
  "A saved session already uses this name. Choose a different name.";
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
let listenerReconnectTimer = null;
let selectedSessionId = null;
let archivePlaybackClaim = null;

function readSessionValue(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "null");
  } catch {
    try { sessionStorage.removeItem(key); } catch { /* ignore */ }
    return null;
  }
}

function writeSessionValue(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* snapshots remain authoritative */ }
}

function removeSessionValue(key) {
  try { sessionStorage.removeItem(key); } catch { /* ignore */ }
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
  listenerCodeInput.focus();
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

function normalizeSessionTitle(value) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, 120);
}

function normalizeJoinCode(value) {
  const raw = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
  return raw.length > 3 ? `${raw.slice(0, 3)}-${raw.slice(3)}` : raw;
}

function setSessionTitleError(message = "") {
  sessionTitleInput.setCustomValidity(message);
  sessionTitleInput.toggleAttribute("aria-invalid", Boolean(message));
  sessionTitleMessage.textContent = message;
  sessionTitleMessage.hidden = !message;
}

function setListenerCodeError(message = "") {
  listenerCodeInput.setCustomValidity(message);
  listenerCodeInput.toggleAttribute("aria-invalid", Boolean(message));
  listenerCodeMessage.textContent = message;
  listenerCodeMessage.hidden = !message;
}

async function validateSessionTitleForStart() {
  const title = normalizeSessionTitle(sessionTitleInput.value);
  sessionTitleInput.value = title;
  setSessionTitleError();
  if (!title) {
    setSessionTitleError("Enter a session name.");
    sessionTitleInput.focus();
    return null;
  }
  try {
    const response = await fetch(applicationPath("/api/sessions"), { cache: "no-store" });
    if (response.ok) {
      const payload = await response.json();
      const titleKey = title.toLocaleLowerCase();
      const duplicate = (payload.sessions || []).some(
        (session) => normalizeSessionTitle(session.title).toLocaleLowerCase() === titleKey,
      );
      if (duplicate) {
        setSessionTitleError(SESSION_TITLE_CONFLICT_MESSAGE);
        sessionTitleInput.focus();
        return null;
      }
    }
  } catch {
    // The server performs the authoritative validation.
  }
  return title;
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
  const connected = ownsHostLease || sessionStarting || socket !== null;
  startButton.disabled = connected || stopping;
  stopButton.disabled = !ownsHostLease || stopping;
  sessionTitleInput.disabled = connected || resumePending;
  hostBackButton.disabled = connected || resumePending;
  startButton.textContent = resumePending ? "Reconnect microphone" : "Start session";
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

function applySnapshot(event) {
  const session = event.session || {};
  if (event.role === "host") {
    showHostWorkspace();
    sessionTitleInput.value = session.title || sessionTitleInput.value;
    hostJoinCode.textContent = event.join_code || "—";
    hostSharePanel.hidden = !event.join_code;
    hostListenerCount.textContent = String(session.listener_count || 0);
    englishSegments = Array.isArray(event.english_segments) ? event.english_segments : [];
    partialTranscript = event.partial_english || "";
    transcriptParagraph = null;
    transcriptList.replaceChildren(emptyTranscript);
    if (englishSegments.length || partialTranscript) renderTranscript();
    ownsHostLease = true;
    resumePending = false;
    setHostStatus(session.state || "live", "Live session in progress.");
    updateHostControls();
    if (session.state === "live" && mediaStream && !workletNode) {
      beginAudioCapture(16000).catch(handleHostError);
    }
  } else {
    showListenerWorkspace();
    listenerSessionTitle.textContent = session.title || "Live session";
    frenchSegments = Array.isArray(event.french_segments) ? event.french_segments : [];
    translationParagraph = null;
    translationList.replaceChildren(emptyTranslation);
    if (frenchSegments.length) renderTranslation();
    renderLevel(speakerLevel, speakerBars, event.latest_audio_level || 0);
    setListenerStatus(session.state || "live", session.host_connected
      ? "French captions are live."
      : "Speaker reconnecting. Captions are paused.");
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
    title: event.title,
  });
  hostJoinCode.textContent = event.join_code;
  hostSharePanel.hidden = false;
}

function handleServerEvent(event) {
  switch (event.type) {
    case "session_created":
      persistHostSession(event);
      ownsHostLease = true;
      sessionStarting = false;
      pauseArchivePlaybackAcrossTabs();
      broadcastLiveSessionState("active");
      updateHostControls();
      break;
    case "session_snapshot": applySnapshot(event); break;
    case "session_status":
      if (currentRole === "host") setHostStatus(event.state, event.message);
      else {
        setListenerStatus(event.state, event.message);
        if (event.state === "host_reconnecting") {
          setSpeakerState("paused", "Speaker reconnecting", "Captions will resume when the host returns.");
          renderLevel(speakerLevel, speakerBars, 0);
        }
      }
      break;
    case "session_ready":
      if (currentRole === "host" && !stopping) beginAudioCapture(event.sample_rate).catch(handleHostError);
      else if (currentRole === "listener") setListenerStatus("live", "French captions are live.");
      break;
    case "transcript": if (currentRole === "host") appendTranscript(event.text, event.is_final); break;
    case "translation": if (currentRole === "listener") appendTranslation(event); break;
    case "audio_level":
      if (currentRole === "listener") {
        renderLevel(speakerLevel, speakerBars, event.level);
        setSpeakerState("active", "Speaker microphone active", "Audio is being captured on the host device.");
      }
      break;
    case "listener_count": hostListenerCount.textContent = String(event.count || 0); break;
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
      if (currentRole === "host") {
        removeSessionValue(HOST_SESSION_KEY);
        releaseAudio().catch(() => {});
        setHostStatus("ended", event.message);
        setMicrophoneState("idle", "Microphone stopped", "The recording has ended.");
      } else {
        removeSessionValue(LISTENER_SESSION_KEY);
        setListenerStatus("ended", "The session has ended. The archive is now available.");
        setSpeakerState("idle", "Speaker microphone stopped", "No more live audio is being captured.");
      }
      updateHostControls();
      break;
    case "session_rejected":
      sessionRejected = true;
      if (currentRole === "listener") {
        removeSessionValue(LISTENER_SESSION_KEY);
        showListenerJoin();
        setListenerCodeError(event.message);
        joinListenerButton.disabled = false;
      } else {
        if (["SESSION_TITLE_REQUIRED", "SESSION_TITLE_CONFLICT"].includes(event.code)) setSessionTitleError(event.message);
        else setHostStatus("unavailable", event.message);
        ownsHostLease = false;
        sessionStarting = false;
        resumePending = false;
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
      if (readSessionValue(HOST_SESSION_KEY)) {
        resumePending = true;
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

async function startHostSession() {
  if (sessionStarting || ownsHostLease || socket) return;
  const storedHost = readSessionValue(HOST_SESSION_KEY);
  if (resumePending && storedHost) {
    sessionStarting = true;
    updateHostControls();
    try {
      await acquireMicrophone();
      connectLiveSocket({ type: "resume", session_id: storedHost.session_id, resume_token: storedHost.resume_token });
    } catch (error) {
      handleHostError(error);
      resumePending = true;
      updateHostControls();
    }
    return;
  }
  sessionStarting = true;
  updateHostControls();
  const title = await validateSessionTitleForStart();
  if (!title) { sessionStarting = false; updateHostControls(); return; }
  try {
    const response = await fetch(applicationPath("/api/live-session"), { cache: "no-store" });
    const status = response.ok ? await response.json() : { active: false };
    if (status.active) throw new Error("Another OraTranslate session is already active. Join it as a listener or wait for it to end.");
    pauseArchivePlaybackAcrossTabs();
    resetHostTranscript();
    setHostStatus("microphone", "Waiting for microphone permission...");
    await acquireMicrophone();
    connectLiveSocket({ type: "start", title });
  } catch (error) { handleHostError(error); }
}

async function stopHostSession() {
  if (stopping || !ownsHostLease) return;
  stopping = true;
  updateHostControls();
  setHostStatus("finalizing", "Sending the final audio and translations...");
  setMicrophoneState("connecting", "Microphone stopped", "Finalizing the recording...");
  await releaseAudio();
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop" }));
}

function joinListener() {
  const joinCode = normalizeJoinCode(listenerCodeInput.value);
  listenerCodeInput.value = joinCode;
  setListenerCodeError();
  if (joinCode.length !== 7) {
    setListenerCodeError("Enter the six-character session code.");
    listenerCodeInput.focus();
    return;
  }
  joinListenerButton.disabled = true;
  writeSessionValue(LISTENER_SESSION_KEY, { join_code: joinCode });
  listenerSessionCode.textContent = joinCode;
  showListenerWorkspace();
  resetListenerTranscript();
  setListenerStatus("connecting", "Joining the live session...");
  setSpeakerState("connecting", "Speaker microphone", "Waiting for the host audio level.");
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
    savedSessionStatus.textContent = session.status || "saved";
    savedSessionStatus.dataset.status = session.status || "saved";
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
    downloadReport.hidden = !session.report_url;
    if (session.report_url) downloadReport.href = applicationPath(session.report_url);
    else downloadReport.removeAttribute("href");
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
    details.textContent = `${formatSessionDate(session.started_at)} · ${formatDuration(session.duration_seconds)} · ${session.status || "saved"}`;
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

function restoreRole() {
  const host = readSessionValue(HOST_SESSION_KEY);
  if (host?.session_id && host?.resume_token) {
    showHostWorkspace();
    sessionTitleInput.value = host.title || "Live session";
    hostJoinCode.textContent = host.join_code || "—";
    hostSharePanel.hidden = !host.join_code;
    resumePending = true;
    setHostStatus("host_reconnecting", "Reconnect the microphone to continue this session.");
    setMicrophoneState("paused", "Microphone disconnected", "Select Reconnect microphone within the recovery window.");
    updateHostControls();
    return;
  }
  const listener = readSessionValue(LISTENER_SESSION_KEY);
  if (listener?.join_code) {
    showListenerWorkspace();
    listenerSessionCode.textContent = listener.join_code;
    setListenerStatus("reconnecting", "Rejoining the live session...");
    connectLiveSocket({ type: "join", join_code: listener.join_code });
    return;
  }
  showLiveSurface(roleEntry);
}

hostModeButton.addEventListener("click", () => { showHostWorkspace(); updateHostControls(); sessionTitleInput.focus(); });
listenerModeButton.addEventListener("click", showListenerJoin);
hostBackButton.addEventListener("click", showRoleEntry);
listenerBackButton.addEventListener("click", showRoleEntry);
leaveListenerButton.addEventListener("click", leaveListenerSession);
startButton.addEventListener("click", startHostSession);
stopButton.addEventListener("click", stopHostSession);
joinListenerButton.addEventListener("click", joinListener);
sessionTitleInput.addEventListener("input", () => { if (sessionTitleInput.value.trim()) setSessionTitleError(); });
listenerCodeInput.addEventListener("input", () => { listenerCodeInput.value = normalizeJoinCode(listenerCodeInput.value); setListenerCodeError(); });
listenerCodeInput.addEventListener("keydown", (event) => { if (event.key === "Enter") joinListener(); });
copyJoinCodeButton.addEventListener("click", async () => {
  const code = hostJoinCode.textContent;
  if (!code || code === "—") return;
  await navigator.clipboard.writeText(code);
  copyJoinCodeButton.textContent = "Code copied";
  setTimeout(() => { copyJoinCodeButton.textContent = "Copy code"; }, 1500);
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
restoreRole();
updateHostControls();
