const startButton = document.querySelector("#start-button");
const stopButton = document.querySelector("#stop-button");
const sessionTitleInput = document.querySelector("#session-title");
const statusBadge = document.querySelector("#status-badge");
const statusMessage = document.querySelector("#status-message");
const transcriptList = document.querySelector("#transcript-list");
const translationList = document.querySelector("#translation-list");
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
const savedEnglish = document.querySelector("#saved-english");
const savedFrench = document.querySelector("#saved-french");
const SESSION_RESULTS_KEY = "oci-speech-results-v1";
const FRENCH_CAPTION_PLACEHOLDER = "La traduction française apparaîtra ici.";
const { applicationPath, websocketUrl } = window.oraTranslateUrls;

let socket = null;
let mediaStream = null;
let audioContext = null;
let mediaSource = null;
let workletNode = null;
let silentGain = null;
let englishSegments = [];
let frenchSegments = [];
let partialTranscript = "";
let transcriptParagraph = null;
let translationParagraph = null;
let diagnosticEvents = [];
let stopping = false;
let selectedSessionId = null;

function setStatus(state, message) {
  statusBadge.dataset.state = state;
  statusBadge.textContent = state.replaceAll("_", " ");
  statusMessage.textContent = message;
}

function saveResults() {
  try {
    sessionStorage.setItem(
      SESSION_RESULTS_KEY,
      JSON.stringify({
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
  emptyTranslation.hidden = true;
  const card = document.createElement("article");
  card.className = "error-card";
  const title = document.createElement("strong");
  title.textContent = `${event.stage || "OCI"} error`;
  const message = document.createElement("p");
  message.textContent = event.message;
  card.append(title, message);

  if (event.opc_request_id) {
    const requestId = document.createElement("code");
    requestId.textContent = `OPC request ID: ${event.opc_request_id}`;
    card.append(requestId);
  }

  if (event.status || event.code) {
    title.textContent += ` (${[event.status, event.code]
      .filter(Boolean)
      .join(" ")})`;
  }

  translationList.append(card);
  translationList.scrollTop = translationList.scrollHeight;

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
    setStatus("restored", "Restored results from before the refresh.");
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

async function deleteSelectedSession() {
  const sessionId = selectedSessionId;
  if (!sessionId) {
    return;
  }

  const title = savedSessionTitle.textContent || "this saved session";
  const confirmed = window.confirm(
    `Permanently delete “${title}”?\n\nThe MP3, English text, French text, session report, metadata, and diagnostics cannot be recovered.`,
  );
  if (!confirmed) {
    return;
  }

  deleteSessionButton.disabled = true;
  savedAudio.pause();
  savedAudio.removeAttribute("src");
  savedAudio.load();

  try {
    const response = await fetch(
      applicationPath(`/api/sessions/${encodeURIComponent(sessionId)}`),
      { method: "DELETE" },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || "The saved session could not be deleted.");
    }

    selectedSessionId = null;
    await loadSessions();
  } catch (error) {
    window.alert(error?.message || "The saved session could not be deleted.");
  } finally {
    deleteSessionButton.disabled = false;
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
  silentGain.gain.value = 0;

  workletNode.port.onmessage = (event) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(event.data);
    }
  };

  mediaSource.connect(workletNode);
  workletNode.connect(silentGain);
  silentGain.connect(audioContext.destination);

  setStatus("listening", `Listening at ${sampleRate / 1000} kHz PCM`);
}

async function releaseAudio() {
  if (workletNode) {
    workletNode.port.postMessage({ type: "flush" });
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  mediaSource?.disconnect();
  workletNode?.disconnect();
  silentGain?.disconnect();
  mediaStream?.getTracks().forEach((track) => track.stop());

  if (audioContext && audioContext.state !== "closed") {
    await audioContext.close();
  }

  mediaStream = null;
  audioContext = null;
  mediaSource = null;
  workletNode = null;
  silentGain = null;
}

function handleServerEvent(event) {
  switch (event.type) {
    case "session_status":
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
      break;
    case "session_stopped":
      setStatus("stopped", event.message);
      break;
    case "session_saved":
      loadSessions(event.session?.session_id || null);
      break;
    default:
      break;
  }
}

function handleClientError(error) {
  const message = error?.message || String(error);
  appendError({ stage: "browser", message });
  setStatus("error", message);
  releaseAudio().catch(() => {});
  socket?.close();
  startButton.disabled = false;
  stopButton.disabled = true;
  sessionTitleInput.disabled = false;
}

async function startSession() {
  resetResults();
  stopping = false;
  startButton.disabled = true;
  stopButton.disabled = false;
  sessionTitleInput.disabled = true;
  setStatus("microphone", "Waiting for microphone permission...");

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

    socket = new WebSocket(websocketUrl("/ws/live"));
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: "start",
          title: sessionTitleInput.value,
        }),
      );
    };

    socket.onmessage = (message) => {
      handleServerEvent(JSON.parse(message.data));
    };
    socket.onerror = () => {
      handleClientError(new Error("The browser couldn't reach the server."));
    };
    socket.onclose = async () => {
      await releaseAudio();
      startButton.disabled = false;
      stopButton.disabled = true;
      sessionTitleInput.disabled = false;
      if (!stopping && statusBadge.dataset.state !== "error") {
        setStatus("disconnected", "The server connection closed.");
      }
      socket = null;
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
  stopButton.disabled = true;
  setStatus("finalizing", "Sending the final audio and translating it...");
  await releaseAudio();

  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop" }));
  } else {
    socket?.close();
    startButton.disabled = false;
  }
}

startButton.addEventListener("click", startSession);
stopButton.addEventListener("click", stopSession);
refreshSessionsButton.addEventListener("click", () => loadSessions());
deleteSessionButton.addEventListener("click", deleteSelectedSession);
restoreResults();
loadSessions();
