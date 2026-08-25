const DEFAULT_SENTENCES = [
  "Welcome to this session about Oracle Cloud Infrastructure.",
  "Today we will explore several practical cloud services.",
  "OCI provides computing, networking, storage, and database capabilities.",
  "The speaker will demonstrate a live translation proof of concept.",
  "A browser can capture microphone audio after the user grants permission.",
  "The local server sends raw audio to OCI Speech Realtime.",
  "Whisper converts the English speech into final transcript segments.",
  "OCI Language translates the buffered English text into French.",
  "Each request in this test contains exactly one English sentence.",
  "The application records the response time for every translation call.",
  "Successful responses include an OPC request identifier.",
  "Failed responses can also include an OPC request identifier for support.",
  "An authorization failure should not be retried without investigation.",
  "A rate limit normally produces an HTTP 429 response.",
  "The sequential test establishes a low-concurrency baseline.",
  "A second run can use two concurrent translation requests.",
  "A controlled burst can reveal whether throttling occurs.",
  "The test results can be downloaded as a JSON diagnostic report.",
  "MongoDB can be used alongside services in Oracle Cloud Infrastructure.",
  "Artificial intelligence is changing how applications process language.",
  "Clear punctuation helps the translation service understand each sentence.",
  "Technical product names should be spoken slowly and clearly.",
  "Headphones can reduce echo during a live transcription session.",
  "Background noise affects transcription but does not cause an OCI 404.",
  "The configured compartment must authorize the calling OCI identity.",
  "Service availability and IAM policies can vary between OCI regions.",
  "Request identifiers help Oracle Support trace failures in service logs.",
  "This sentence checks whether translation remains stable near the end.",
  "The twenty-ninth request provides another repeatability checkpoint.",
  "Thank you for completing the OCI Language reliability test.",
];

const { websocketUrl } = window.oraTranslateUrls;

const sentencesInput = document.querySelector("#sentences");
const concurrencyInput = document.querySelector("#concurrency");
const delayInput = document.querySelector("#delay-ms");
const startButton = document.querySelector("#start-test");
const stopButton = document.querySelector("#stop-test");
const downloadButton = document.querySelector("#download-results");
const statusBadge = document.querySelector("#status-badge");
const statusMessage = document.querySelector("#status-message");
const resultsBody = document.querySelector("#results-body");
const totalCount = document.querySelector("#total-count");
const successCount = document.querySelector("#success-count");
const error404Count = document.querySelector("#error-404-count");
const error429Count = document.querySelector("#error-429-count");
const otherErrorCount = document.querySelector("#other-error-count");
const runMetadata = document.querySelector("#run-metadata");

let socket = null;
let running = false;
let currentSentences = [];
let currentSettings = null;
let results = [];
let startedAt = null;

function setStatus(state, message) {
  statusBadge.dataset.state = state;
  statusBadge.textContent = state.replaceAll("_", " ");
  statusMessage.textContent = message;
}

function parseSentences() {
  return sentencesInput.value
    .split(/\r?\n/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

function resetSummary(total) {
  totalCount.textContent = String(total);
  successCount.textContent = "0";
  error404Count.textContent = "0";
  error429Count.textContent = "0";
  otherErrorCount.textContent = "0";
}

function renderPendingRows(sentences) {
  resultsBody.replaceChildren();
  sentences.forEach((sentence, offset) => {
    const index = offset + 1;
    const row = document.createElement("tr");
    row.dataset.index = String(index);

    const numberCell = document.createElement("th");
    numberCell.scope = "row";
    numberCell.textContent = String(index);

    const englishCell = document.createElement("td");
    englishCell.textContent = sentence;

    const outputCell = document.createElement("td");
    outputCell.dataset.field = "output";
    outputCell.textContent = "Waiting";

    const resultCell = document.createElement("td");
    resultCell.dataset.field = "result";
    resultCell.innerHTML = '<span class="result-pill pending">pending</span>';

    const latencyCell = document.createElement("td");
    latencyCell.dataset.field = "latency";
    latencyCell.textContent = "—";

    const requestCell = document.createElement("td");
    requestCell.dataset.field = "request";
    requestCell.textContent = "—";

    row.append(
      numberCell,
      englishCell,
      outputCell,
      resultCell,
      latencyCell,
      requestCell,
    );
    resultsBody.append(row);
  });
}

function updateSummary() {
  let successes = 0;
  let errors404 = 0;
  let errors429 = 0;
  let otherErrors = 0;

  results.forEach((result) => {
    if (result.status >= 200 && result.status < 300) {
      successes += 1;
    } else if (result.status === 404) {
      errors404 += 1;
    } else if (result.status === 429) {
      errors429 += 1;
    } else {
      otherErrors += 1;
    }
  });

  successCount.textContent = String(successes);
  error404Count.textContent = String(errors404);
  error429Count.textContent = String(errors429);
  otherErrorCount.textContent = String(otherErrors);
}

function renderResult(event) {
  results.push(event);
  results.sort((left, right) => left.index - right.index);
  const row = resultsBody.querySelector(`[data-index="${event.index}"]`);
  if (!row) {
    return;
  }

  const successful = event.status >= 200 && event.status < 300;
  const outputCell = row.querySelector('[data-field="output"]');
  outputCell.textContent = successful
    ? event.french || "Translated without returned text"
    : event.message || "Translation failed";
  if (event.retry_after) {
    outputCell.textContent += ` Retry-After: ${event.retry_after}`;
  }

  const resultCell = row.querySelector('[data-field="result"]');
  const resultLabel = [event.status, event.code].filter(Boolean).join(" ");
  const resultClass = successful ? "success" : "failure";
  resultCell.replaceChildren();
  const pill = document.createElement("span");
  pill.className = `result-pill ${resultClass}`;
  pill.textContent = resultLabel || "error";
  resultCell.append(pill);

  row.querySelector('[data-field="latency"]').textContent =
    `${event.latency_ms} ms`;

  const requestCell = row.querySelector('[data-field="request"]');
  requestCell.textContent = event.opc_request_id || "Not returned";
  requestCell.className = "request-id";
  row.dataset.result = successful ? "success" : "failure";

  updateSummary();
  downloadButton.disabled = false;
}

function finishRun(state, message) {
  running = false;
  startButton.disabled = false;
  stopButton.disabled = true;
  setStatus(state, message);
}

function handleServerEvent(event) {
  switch (event.type) {
    case "translation_test_started":
      setStatus(
        "running",
        `Running ${event.total} requests with concurrency ${event.concurrency}.`,
      );
      break;
    case "translation_test_result":
      renderResult(event);
      break;
    case "translation_test_complete": {
      const seconds = (event.elapsed_ms / 1000).toFixed(1);
      runMetadata.textContent =
        `${event.total} requests completed in ${seconds} seconds.`;
      finishRun("complete", "Translation reliability test completed.");
      break;
    }
    case "translation_test_stopped":
      finishRun("stopped", event.message);
      break;
    case "error":
      runMetadata.textContent = event.opc_request_id
        ? `${event.message} OPC request ID: ${event.opc_request_id}`
        : event.message;
      finishRun("error", `${event.stage || "OCI"} error`);
      break;
    default:
      break;
  }
}

function ensureSocket() {
  if (socket?.readyState === WebSocket.OPEN) {
    return Promise.resolve(socket);
  }

  return new Promise((resolve, reject) => {
    socket = new WebSocket(websocketUrl("/ws/translation-test"));
    socket.onopen = () => resolve(socket);
    socket.onmessage = (message) => {
      handleServerEvent(JSON.parse(message.data));
    };
    socket.onerror = () => {
      reject(new Error("The browser could not reach the test server."));
    };
    socket.onclose = () => {
      if (running) {
        finishRun("disconnected", "The test server connection closed.");
      }
      socket = null;
    };
  });
}

async function startTest() {
  if (running) {
    return;
  }

  currentSentences = parseSentences();
  if (!currentSentences.length || currentSentences.length > 50) {
    setStatus("error", "Provide between 1 and 50 sentences.");
    return;
  }

  currentSettings = {
    concurrency: Number.parseInt(concurrencyInput.value, 10),
    delay_ms: Number.parseInt(delayInput.value, 10),
  };
  results = [];
  startedAt = new Date().toISOString();
  running = true;
  startButton.disabled = true;
  stopButton.disabled = false;
  downloadButton.disabled = true;
  resetSummary(currentSentences.length);
  renderPendingRows(currentSentences);
  runMetadata.textContent =
    `Concurrency ${currentSettings.concurrency}; ` +
    `${currentSettings.delay_ms} ms pause per worker.`;
  setStatus("connecting", "Connecting to the local test server...");

  try {
    const activeSocket = await ensureSocket();
    activeSocket.send(
      JSON.stringify({
        type: "start",
        sentences: currentSentences,
        ...currentSettings,
      }),
    );
  } catch (error) {
    finishRun("error", error.message || String(error));
  }
}

function stopTest() {
  if (running && socket?.readyState === WebSocket.OPEN) {
    stopButton.disabled = true;
    setStatus("stopping", "Stopping the translation test...");
    socket.send(JSON.stringify({ type: "stop" }));
  }
}

function downloadResults() {
  const report = {
    started_at: startedAt,
    settings: currentSettings,
    total_sentences: currentSentences.length,
    results,
  };
  const blob = new Blob([JSON.stringify(report, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `oci-language-test-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

sentencesInput.value = DEFAULT_SENTENCES.join("\n");
renderPendingRows(DEFAULT_SENTENCES);
startButton.addEventListener("click", startTest);
stopButton.addEventListener("click", stopTest);
downloadButton.addEventListener("click", downloadResults);
