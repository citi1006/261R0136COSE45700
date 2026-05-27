const state = {
  stream: null,
  configs: [],
  selectedConfig: null,
  uploading: false,
  uploadCount: 0,
  uploadSequence: 0,
  uploadTimer: null,
  progressTimer: null,
  activeTab: "capture",
  isRoiEditing: false,
  roiDraft: null,
  roiDragStart: null,
  authToken: window.localStorage.getItem("mobile-cleanliness-auth-token") || "",
  authMode: "login",
};

const elements = {
  authPanel: document.getElementById("auth-panel"),
  appShell: document.getElementById("app-shell"),
  authForm: document.getElementById("auth-form"),
  authTitle: document.getElementById("auth-title"),
  authUserIdInput: document.getElementById("auth-user-id-input"),
  authPasswordInput: document.getElementById("auth-password-input"),
  authSubmitButton: document.getElementById("auth-submit-button"),
  authMessage: document.getElementById("auth-message"),
  logoutButton: document.getElementById("logout-button"),
  secureStatus: document.getElementById("secure-status"),
  videoFrame: document.getElementById("video-frame"),
  cameraPreview: document.getElementById("camera-preview"),
  roiLayer: document.getElementById("roi-layer"),
  emptyPreview: document.getElementById("empty-preview"),
  cameraMessage: document.getElementById("camera-message"),
  recordingBadge: document.getElementById("recording-badge"),
  backendBaseInput: document.getElementById("backend-base-input"),
  loadPolicyButton: document.getElementById("load-policy-button"),
  configSelect: document.getElementById("config-select"),
  roiSelect: document.getElementById("roi-select"),
  cameraFacingSelect: document.getElementById("camera-facing-select"),
  deviceIdInput: document.getElementById("device-id-input"),
  uploadIntervalInput: document.getElementById("upload-interval-input"),
  clipLengthInput: document.getElementById("clip-length-input"),
  promptProfileSelect: document.getElementById("prompt-profile-select"),
  captureTabButton: document.getElementById("capture-tab-button"),
  roiTabButton: document.getElementById("roi-tab-button"),
  captureForm: document.getElementById("capture-form"),
  roiForm: document.getElementById("roi-form"),
  roiStoreInput: document.getElementById("roi-store-input"),
  roiCctvInput: document.getElementById("roi-cctv-input"),
  roiNameInput: document.getElementById("roi-name-input"),
  roiModeButton: document.getElementById("roi-mode-button"),
  clearRoiButton: document.getElementById("clear-roi-button"),
  saveRoiButton: document.getElementById("save-roi-button"),
  roiSizeText: document.getElementById("roi-size-text"),
  openCameraButton: document.getElementById("open-camera-button"),
  startUploadButton: document.getElementById("start-upload-button"),
  stopButton: document.getElementById("stop-button"),
  uploadCount: document.getElementById("upload-count"),
  statusText: document.getElementById("status-text"),
  progressBar: document.getElementById("progress-bar"),
  currentUploadStatus: document.getElementById("current-upload-status"),
  lastUploadTime: document.getElementById("last-upload-time"),
  resultDecision: document.getElementById("result-decision"),
  resultScore: document.getElementById("result-score"),
  resultConfidence: document.getElementById("result-confidence"),
  resultSummary: document.getElementById("result-summary"),
  uploadEventList: document.getElementById("upload-event-list"),
  logList: document.getElementById("log-list"),
  clearLogButton: document.getElementById("clear-log-button"),
};

function defaultBackendBase() {
  if (window.location.protocol === "file:") {
    return "http://127.0.0.1:8000";
  }
  if (window.location.port === "8080") {
    const host = window.location.hostname || "127.0.0.1";
    return `http://${host}:8000`;
  }
  return window.location.origin;
}

function buildApiUrl(path) {
  const baseValue = elements.backendBaseInput.value.trim() || defaultBackendBase();
  const baseUrl = baseValue.endsWith("/") ? baseValue : `${baseValue}/`;
  return new URL(path.replace(/^\//, ""), baseUrl).toString();
}

function authHeaders() {
  if (!state.authToken) {
    return {};
  }
  return { Authorization: `Bearer ${state.authToken}` };
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  Object.entries(authHeaders()).forEach(([key, value]) => headers.set(key, value));
  const response = await fetch(buildApiUrl(path), { ...options, headers });
  if (response.status === 401 && state.authToken) {
    handleLogout("로그인이 만료되었습니다.");
  }
  return response;
}

function setAuthMessage(message, level = "info") {
  elements.authMessage.textContent = message || "";
  elements.authMessage.className = `auth-message ${level}`;
}

function setAuthenticated(token) {
  state.authToken = token;
  window.localStorage.setItem("mobile-cleanliness-auth-token", token);
  elements.authPanel.classList.add("hidden");
  elements.appShell.classList.remove("hidden");
}

function showAuthPanel(mode = "login") {
  state.authMode = mode;
  const isBootstrap = mode === "bootstrap";
  elements.authTitle.textContent = isBootstrap ? "점주 계정 생성" : "점주 로그인";
  elements.authSubmitButton.textContent = isBootstrap ? "계정 생성" : "로그인";
  elements.authPasswordInput.autocomplete = isBootstrap ? "new-password" : "current-password";
  elements.authPanel.classList.remove("hidden");
  elements.appShell.classList.add("hidden");
}

function clearAuthToken() {
  state.authToken = "";
  window.localStorage.removeItem("mobile-cleanliness-auth-token");
}

function handleLogout(message = "로그아웃되었습니다.") {
  stopUploadLoop();
  clearAuthToken();
  showAuthPanel("login");
  setAuthMessage(message, "info");
}

function getOrCreateDeviceId() {
  const stored = window.localStorage.getItem("mobile-cleanliness-device-id");
  if (stored) {
    return stored;
  }
  const generated = `web-${Math.random().toString(36).slice(2, 8)}`;
  window.localStorage.setItem("mobile-cleanliness-device-id", generated);
  return generated;
}

function setStatus(message, level = "idle") {
  elements.statusText.textContent = message;
  const labels = {
    idle: "대기",
    ready: "준비",
    recording: "촬영",
    uploading: "업로드",
    success: "완료",
    error: "오류",
  };
  elements.secureStatus.textContent = labels[level] || labels.idle;
}

function addLog(message, level = "info") {
  const item = document.createElement("li");
  item.className = level;
  item.textContent = `${new Date().toLocaleTimeString()} ${message}`;
  elements.logList.prepend(item);
  while (elements.logList.children.length > 30) {
    elements.logList.lastElementChild.remove();
  }
}

function setProgress(percent) {
  elements.progressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 KB";
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatElapsed(startedAt) {
  return `${Math.max(0, Math.round((Date.now() - startedAt) / 1000))}초`;
}

function createUploadEvent(values) {
  const empty = elements.uploadEventList.querySelector(".upload-empty");
  if (empty) {
    empty.remove();
  }

  state.uploadSequence += 1;
  elements.currentUploadStatus.textContent = `#${state.uploadSequence} 촬영 중`;

  const item = document.createElement("li");
  item.className = "upload-event recording";

  const top = document.createElement("div");
  top.className = "upload-event-top";
  const title = document.createElement("strong");
  const time = document.createElement("span");
  time.className = "upload-event-time";
  time.textContent = new Date().toLocaleTimeString();
  top.append(title, time);

  const detail = document.createElement("div");
  detail.className = "upload-event-detail";

  const meta = document.createElement("div");
  meta.className = "upload-event-meta";
  const chip = document.createElement("span");
  chip.className = "upload-status-chip";
  const extra = document.createElement("span");
  meta.append(chip, extra);

  item.append(top, detail, meta);
  elements.uploadEventList.prepend(item);
  while (elements.uploadEventList.children.length > 20) {
    elements.uploadEventList.lastElementChild.remove();
  }

  const entry = {
    id: state.uploadSequence,
    startedAt: Date.now(),
    item,
    title,
    detail,
    chip,
    extra,
    values,
  };
  updateUploadEvent(entry, "recording", `${values.configId} / ${values.roiName} · ${values.clipLengthSeconds}초 촬영`);
  return entry;
}

function updateUploadEvent(entry, status, detailText, extraText = "") {
  const labels = {
    recording: "촬영 중",
    uploading: "업로드 중",
    accepted: "접수됨",
    success: "완료",
    error: "실패",
  };
  entry.item.className = `upload-event ${status}`;
  entry.title.textContent = `#${entry.id} ${labels[status] || status}`;
  entry.chip.textContent = labels[status] || status;
  entry.detail.textContent = detailText;
  entry.extra.textContent = extraText || formatElapsed(entry.startedAt);
  elements.currentUploadStatus.textContent = `#${entry.id} ${labels[status] || status}`;
}

function showTab(tabName) {
  state.activeTab = tabName;
  elements.captureTabButton.classList.toggle("active", tabName === "capture");
  elements.roiTabButton.classList.toggle("active", tabName === "roi");
  elements.captureForm.classList.toggle("active", tabName === "capture");
  elements.roiForm.classList.toggle("active", tabName === "roi");
  if (tabName !== "roi") {
    state.isRoiEditing = false;
  } else {
    syncConfigSelectionFromRoiInputs();
  }
  updateRoiUi();
}

function updateCameraUi() {
  const hasStream = Boolean(state.stream);
  elements.emptyPreview.style.display = hasStream ? "none" : "grid";
  elements.openCameraButton.disabled = hasStream;
  elements.startUploadButton.disabled = !hasStream || state.uploading;
  elements.stopButton.disabled = !hasStream && !state.uploading;
  elements.recordingBadge.classList.toggle("active", state.uploading);
  updateRoiUi();
}

function stopStream() {
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  elements.cameraPreview.srcObject = null;
  elements.cameraMessage.textContent = "스트림 없음";
  updateCameraUi();
}

function stopUploadLoop() {
  state.uploading = false;
  if (state.uploadTimer) {
    window.clearTimeout(state.uploadTimer);
    state.uploadTimer = null;
  }
  if (state.progressTimer) {
    window.clearInterval(state.progressTimer);
    state.progressTimer = null;
  }
  setProgress(0);
  updateCameraUi();
}

function validateSecureContext() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("브라우저가 카메라 스트림을 지원하지 않습니다.", "error");
    elements.openCameraButton.disabled = true;
    return false;
  }

  if (!window.isSecureContext) {
    setStatus("카메라는 HTTPS 또는 localhost에서만 열 수 있습니다.", "error");
    addLog("보안 컨텍스트가 아니어서 카메라 접근이 차단됩니다.", "warning");
    return false;
  }

  setStatus("카메라 준비 가능", "ready");
  return true;
}

async function loadPolicy(selection = null) {
  const response = await apiFetch("/api/mobile/upload-policy");
  if (!response.ok) {
    throw new Error(`설정 요청 실패 ${response.status}`);
  }
  const policy = await response.json();
  state.configs = Array.isArray(policy.configs) ? policy.configs : [];
  elements.uploadIntervalInput.value = String(policy.upload_interval_seconds || 60);
  renderConfigOptions(selection);
  addLog("서버 설정을 불러왔습니다.", "success");
  if (!policy.openai_configured) {
    addLog("Server OPENAI_API_KEY is not configured.", "warning");
  }
}

function configExists(configId) {
  return state.configs.some((config) => config.config_id === configId);
}

function findConfigFromRoiInputs() {
  const storeName = elements.roiStoreInput.value.trim();
  const cctvNickname = elements.roiCctvInput.value.trim();
  if (!storeName || !cctvNickname) {
    return null;
  }
  return (
    state.configs.find(
      (config) => config.store_name === storeName && config.cctv_nickname === cctvNickname,
    ) || null
  );
}

function resolveTargetConfigId(selection = null) {
  const requestedConfigId = selection?.configId || elements.configSelect.value;
  if (requestedConfigId && configExists(requestedConfigId)) {
    return requestedConfigId;
  }

  const roiConfig = findConfigFromRoiInputs();
  return roiConfig?.config_id || "";
}

function syncRoiInputsFromSelectedConfig() {
  if (!state.selectedConfig) {
    return;
  }
  elements.roiStoreInput.value = state.selectedConfig.store_name || "";
  elements.roiCctvInput.value = state.selectedConfig.cctv_nickname || "";
  if (state.activeTab === "roi" && !elements.roiNameInput.value.trim()) {
    elements.roiNameInput.value = nextRoiName();
  }
}

function syncConfigSelectionFromRoiInputs() {
  const roiConfig = findConfigFromRoiInputs();
  if (roiConfig) {
    if (elements.configSelect.value !== roiConfig.config_id) {
      elements.configSelect.value = roiConfig.config_id;
      renderRoiOptions();
    }
    return;
  }

  if (state.activeTab === "roi") {
    elements.configSelect.value = "";
    state.selectedConfig = null;
    elements.roiSelect.innerHTML = '<option value="">선택</option>';
    updateRoiUi();
  }
}

function renderConfigOptions(selection = null) {
  const targetConfigId = resolveTargetConfigId(selection);
  elements.configSelect.innerHTML = '<option value="">선택</option>';
  state.configs.forEach((config) => {
    const option = document.createElement("option");
    option.value = config.config_id;
    option.textContent = `${config.store_name} / ${config.cctv_nickname}`;
    elements.configSelect.appendChild(option);
  });
  if (targetConfigId) {
    elements.configSelect.value = targetConfigId;
  }
  renderRoiOptions(selection?.roiName || "");
}

function renderRoiOptions(targetRoiName = "") {
  const configId = elements.configSelect.value;
  state.selectedConfig = state.configs.find((config) => config.config_id === configId) || null;
  const currentRoiName = targetRoiName || elements.roiSelect.value;
  elements.roiSelect.innerHTML = '<option value="">선택</option>';
  if (!state.selectedConfig) {
    updateRoiUi();
    return;
  }
  syncRoiInputsFromSelectedConfig();
  state.selectedConfig.areas.forEach((roi) => {
    const option = document.createElement("option");
    option.value = roi.name;
    option.textContent = roi.name;
    elements.roiSelect.appendChild(option);
  });
  if (currentRoiName) {
    elements.roiSelect.value = currentRoiName;
  }
  if (state.activeTab === "roi" && !elements.roiNameInput.value.trim()) {
    elements.roiNameInput.value = nextRoiName();
  }
  updateRoiUi();
}

async function openCamera() {
  if (!validateSecureContext()) {
    return;
  }
  stopStream();
  const facingMode = elements.cameraFacingSelect.value || "environment";
  const constraints = {
    audio: false,
    video: {
      facingMode: { ideal: facingMode },
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
  };

  try {
    state.stream = await navigator.mediaDevices.getUserMedia(constraints);
    elements.cameraPreview.srcObject = state.stream;
    await elements.cameraPreview.play();
    setStatus("카메라 스트림 연결됨", "ready");
    addLog("카메라 스트림을 열었습니다.", "success");
  } catch (error) {
    setStatus(error.message || "카메라 접근 실패", "error");
    addLog(error.message || "카메라 접근 실패", "error");
    stopStream();
  }
  updateCameraUi();
}

function pickRecorderMimeType() {
  if (!window.MediaRecorder) {
    return "";
  }
  const candidates = [
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
    "video/mp4",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function extensionForMimeType(mimeType) {
  if (mimeType.includes("mp4")) {
    return "mp4";
  }
  return "webm";
}

function recordClip(durationMs) {
  return new Promise((resolve, reject) => {
    const mimeType = pickRecorderMimeType();
    if (!window.MediaRecorder || !mimeType) {
      reject(new Error("브라우저가 영상 녹화를 지원하지 않습니다."));
      return;
    }

    const chunks = [];
    const recorder = new MediaRecorder(state.stream, { mimeType });
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    });
    recorder.addEventListener("error", () => reject(new Error("영상 녹화 중 오류가 발생했습니다.")));
    recorder.addEventListener("stop", () => {
      const blob = new Blob(chunks, { type: mimeType });
      resolve({ blob, mimeType });
    });
    recorder.start(1000);
    window.setTimeout(() => {
      if (recorder.state !== "inactive") {
        recorder.stop();
      }
    }, durationMs);
  });
}

function selectedFormValues() {
  const configId = elements.configSelect.value;
  const roiName = elements.roiSelect.value;
  const uploadIntervalSeconds = Number(elements.uploadIntervalInput.value);
  const clipLengthSeconds = Number(elements.clipLengthInput.value);
  if (!configId || !roiName) {
    throw new Error("CCTV 설정과 ROI를 선택해야 합니다.");
  }
  if (!Number.isFinite(uploadIntervalSeconds) || uploadIntervalSeconds < 5) {
    throw new Error("업로드 간격은 5초 이상이어야 합니다.");
  }
  if (!Number.isFinite(clipLengthSeconds) || clipLengthSeconds < 3) {
    throw new Error("영상 길이는 3초 이상이어야 합니다.");
  }
  return {
    configId,
    roiName,
    uploadIntervalSeconds,
    clipLengthSeconds: Math.min(clipLengthSeconds, uploadIntervalSeconds),
    promptProfile: elements.promptProfileSelect.value,
    deviceId: elements.deviceIdInput.value.trim() || getOrCreateDeviceId(),
  };
}

async function uploadClip({ blob, mimeType }, values) {
  const formData = new FormData();
  const capturedAt = new Date().toISOString();
  const extension = extensionForMimeType(mimeType);
  const filename = `${values.deviceId}_${Date.now()}.${extension}`;

  formData.append("config_id", values.configId);
  formData.append("roi_name", values.roiName);
  formData.append("prompt_profile", values.promptProfile);
  formData.append("device_id", values.deviceId);
  formData.append("captured_at", capturedAt);
  formData.append("upload_period_seconds", String(values.uploadIntervalSeconds));
  formData.append("video_file", blob, filename);

  const response = await apiFetch("/api/mobile/cleanliness-video", {
    method: "POST",
    body: formData,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `업로드 실패 ${response.status}`);
  }
  return payload;
}

function renderResult(payload) {
  elements.resultDecision.textContent = payload.decision || "-";
  elements.resultScore.textContent = payload.score ? `${payload.score} / 5` : "-";
  elements.resultConfidence.textContent =
    typeof payload.confidence === "number" ? `${Math.round(payload.confidence * 100)}%` : "-";
  elements.resultSummary.textContent = payload.summary || "-";
  elements.lastUploadTime.textContent = new Date().toLocaleTimeString();
}

function renderAcceptedJob(payload) {
  elements.resultDecision.textContent = "접수됨";
  elements.resultScore.textContent = "-";
  elements.resultConfidence.textContent = "-";
  elements.resultSummary.textContent = `job_id ${payload.job_id || "-"} 서버 분석 작업으로 접수됨`;
  elements.lastUploadTime.textContent = new Date().toLocaleTimeString();
}

function startProgress(durationMs) {
  const startedAt = Date.now();
  setProgress(0);
  if (state.progressTimer) {
    window.clearInterval(state.progressTimer);
  }
  state.progressTimer = window.setInterval(() => {
    const elapsed = Date.now() - startedAt;
    setProgress((elapsed / durationMs) * 100);
    if (elapsed >= durationMs) {
      window.clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
  }, 200);
}

async function runUploadCycle() {
  if (!state.uploading) {
    return;
  }

  let values;
  try {
    values = selectedFormValues();
  } catch (error) {
    stopUploadLoop();
    setStatus(error.message, "error");
    addLog(error.message, "error");
    return;
  }

  const clipMs = values.clipLengthSeconds * 1000;
  const cycleStartedAt = Date.now();
  const uploadEntry = createUploadEvent(values);
  try {
    setStatus("영상 촬영 중", "recording");
    updateUploadEvent(
      uploadEntry,
      "recording",
      `${values.configId} / ${values.roiName} · ${values.clipLengthSeconds}초 촬영 중`,
    );
    startProgress(clipMs);
    const clip = await recordClip(clipMs);

    setStatus("서버 업로드 중", "uploading");
    updateUploadEvent(
      uploadEntry,
      "uploading",
      `${values.configId} / ${values.roiName} · ${formatBytes(clip.blob.size)} 업로드 중`,
      formatElapsed(uploadEntry.startedAt),
    );
    const payload = await uploadClip(clip, values);
    state.uploadCount += 1;
    elements.uploadCount.textContent = `${state.uploadCount}회`;
    renderAcceptedJob(payload);
    setStatus("분석 작업 접수", "success");
    updateUploadEvent(
      uploadEntry,
      "accepted",
      `job_id ${payload.job_id || "-"} · 서버 내부 분석 작업으로 접수됨`,
      formatElapsed(uploadEntry.startedAt),
    );
    addLog(`업로드 접수: job_id ${payload.job_id || "-"}`, "success");
  } catch (error) {
    setStatus(error.message || "업로드 실패", "error");
    updateUploadEvent(
      uploadEntry,
      "error",
      error.message || "업로드 실패",
      formatElapsed(uploadEntry.startedAt),
    );
    addLog(error.message || "업로드 실패", "error");
  }

  if (state.uploading) {
    const elapsed = Date.now() - cycleStartedAt;
    const delay = Math.max(0, values.uploadIntervalSeconds * 1000 - elapsed);
    state.uploadTimer = window.setTimeout(runUploadCycle, delay);
  }
}

function videoMetrics() {
  const videoWidth = elements.cameraPreview.videoWidth;
  const videoHeight = elements.cameraPreview.videoHeight;
  if (!videoWidth || !videoHeight) {
    return null;
  }

  const frameRect = elements.videoFrame.getBoundingClientRect();
  const scale = Math.max(frameRect.width / videoWidth, frameRect.height / videoHeight);
  const displayWidth = videoWidth * scale;
  const displayHeight = videoHeight * scale;
  return {
    frameRect,
    videoWidth,
    videoHeight,
    scale,
    offsetX: (frameRect.width - displayWidth) / 2,
    offsetY: (frameRect.height - displayHeight) / 2,
  };
}

function screenToVideoPoint(event) {
  const metrics = videoMetrics();
  if (!metrics) {
    return null;
  }
  const rawX = (event.clientX - metrics.frameRect.left - metrics.offsetX) / metrics.scale;
  const rawY = (event.clientY - metrics.frameRect.top - metrics.offsetY) / metrics.scale;
  return {
    x: Math.max(0, Math.min(metrics.videoWidth, rawX)),
    y: Math.max(0, Math.min(metrics.videoHeight, rawY)),
  };
}

function normalizeVideoRect(start, end) {
  const metrics = videoMetrics();
  if (!metrics) {
    return null;
  }
  const left = Math.max(0, Math.min(start.x, end.x));
  const top = Math.max(0, Math.min(start.y, end.y));
  const right = Math.min(metrics.videoWidth, Math.max(start.x, end.x));
  const bottom = Math.min(metrics.videoHeight, Math.max(start.y, end.y));
  return {
    name: elements.roiNameInput.value.trim() || "ROI",
    x: Math.round(left),
    y: Math.round(top),
    width: Math.max(0, Math.round(right - left)),
    height: Math.max(0, Math.round(bottom - top)),
  };
}

function getSelectedRoiBounds() {
  if (!state.selectedConfig || !elements.roiSelect.value) {
    return null;
  }
  const roi = state.selectedConfig.areas.find((item) => item.name === elements.roiSelect.value);
  if (!roi || !roi.bounds) {
    return null;
  }
  return {
    name: roi.name,
    x: Number(roi.bounds.x),
    y: Number(roi.bounds.y),
    width: Number(roi.bounds.width),
    height: Number(roi.bounds.height),
  };
}

function nextRoiName() {
  const existingNames = new Set((state.selectedConfig?.areas || []).map((roi) => String(roi.name)));
  let index = 1;
  while (existingNames.has(`TABLE_${index}`)) {
    index += 1;
  }
  return `TABLE_${index}`;
}

function roiToLayerRect(roi) {
  const metrics = videoMetrics();
  if (!metrics || !roi) {
    return null;
  }
  return {
    left: roi.x * metrics.scale + metrics.offsetX,
    top: roi.y * metrics.scale + metrics.offsetY,
    width: roi.width * metrics.scale,
    height: roi.height * metrics.scale,
  };
}

function addRoiOverlayBox(roi) {
  const rect = roiToLayerRect(roi);
  if (!roi || !rect || rect.width <= 0 || rect.height <= 0) {
    return;
  }

  const box = document.createElement("div");
  box.className = "roi-box";
  box.dataset.label = roi.name;
  box.style.left = `${rect.left}px`;
  box.style.top = `${rect.top}px`;
  box.style.width = `${rect.width}px`;
  box.style.height = `${rect.height}px`;
  elements.roiLayer.appendChild(box);
}

function renderRoiOverlay() {
  elements.roiLayer.innerHTML = "";
  if (state.activeTab === "roi" && state.selectedConfig) {
    state.selectedConfig.areas.forEach((roi) => addRoiOverlayBox({ name: roi.name, ...roi.bounds }));
  }

  const roi = state.roiDraft || (state.activeTab === "capture" ? getSelectedRoiBounds() : null);
  addRoiOverlayBox(roi);
}

function updateRoiUi() {
  const canEdit = state.activeTab === "roi" && state.isRoiEditing && Boolean(state.stream);
  elements.roiLayer.classList.toggle("editing", canEdit);
  elements.roiModeButton.classList.toggle("active", state.isRoiEditing);
  elements.roiModeButton.textContent = state.isRoiEditing ? "그리기 종료" : "ROI 그리기";
  elements.saveRoiButton.disabled = !state.roiDraft || state.roiDraft.width < 12 || state.roiDraft.height < 12;

  if (state.roiDraft) {
    elements.roiSizeText.textContent = `${state.roiDraft.name}: ${state.roiDraft.width} x ${state.roiDraft.height}`;
  } else {
    const selectedRoi = getSelectedRoiBounds();
    elements.roiSizeText.textContent = selectedRoi
      ? `${selectedRoi.name}: ${selectedRoi.width} x ${selectedRoi.height}`
      : "ROI 없음";
  }
  renderRoiOverlay();
}

function startRoiDraw(event) {
  if (!state.isRoiEditing || !state.stream) {
    return;
  }
  const point = screenToVideoPoint(event);
  if (!point) {
    return;
  }
  event.preventDefault();
  elements.roiLayer.setPointerCapture(event.pointerId);
  state.roiDragStart = point;
  state.roiDraft = normalizeVideoRect(point, point);
  updateRoiUi();
}

function moveRoiDraw(event) {
  if (!state.roiDragStart) {
    return;
  }
  const point = screenToVideoPoint(event);
  if (!point) {
    return;
  }
  event.preventDefault();
  state.roiDraft = normalizeVideoRect(state.roiDragStart, point);
  updateRoiUi();
}

function endRoiDraw(event) {
  if (!state.roiDragStart) {
    return;
  }
  event.preventDefault();
  state.roiDragStart = null;
  if (elements.roiLayer.hasPointerCapture(event.pointerId)) {
    elements.roiLayer.releasePointerCapture(event.pointerId);
  }
  updateRoiUi();
}

function clearRoi() {
  state.roiDraft = null;
  state.roiDragStart = null;
  updateRoiUi();
}

function captureReferenceFrame() {
  return new Promise((resolve, reject) => {
    const width = elements.cameraPreview.videoWidth;
    const height = elements.cameraPreview.videoHeight;
    if (!width || !height) {
      reject(new Error("카메라 프레임을 캡처할 수 없습니다."));
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    context.drawImage(elements.cameraPreview, 0, 0, width, height);
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("기준 이미지 생성에 실패했습니다."));
        return;
      }
      resolve(blob);
    }, "image/png");
  });
}

function roiPayload() {
  if (!state.roiDraft || state.roiDraft.width < 12 || state.roiDraft.height < 12) {
    throw new Error("저장할 ROI를 먼저 지정해야 합니다.");
  }
  return [
    {
      name: elements.roiNameInput.value.trim() || state.roiDraft.name || "ROI",
      x: state.roiDraft.x,
      y: state.roiDraft.y,
      width: state.roiDraft.width,
      height: state.roiDraft.height,
    },
  ];
}

async function saveRoiConfig() {
  if (!state.stream) {
    setStatus("카메라 스트림이 필요합니다.", "error");
    return;
  }

  let rois;
  try {
    rois = roiPayload();
  } catch (error) {
    setStatus(error.message, "error");
    addLog(error.message, "error");
    return;
  }

  const storeName = elements.roiStoreInput.value.trim();
  const cctvNickname = elements.roiCctvInput.value.trim();
  if (!storeName || !cctvNickname) {
    setStatus("매장명과 CCTV 이름이 필요합니다.", "error");
    return;
  }

  try {
    setStatus("ROI 저장 중", "uploading");
    const referenceBlob = await captureReferenceFrame();
    const formData = new FormData();
    formData.append("store_name", storeName);
    formData.append("cctv_nickname", cctvNickname);
    formData.append("rois_json", JSON.stringify(rois));
    formData.append("reference_image", referenceBlob, `${storeName}_${cctvNickname}.png`);

    const response = await apiFetch("/api/mobile/roi-configs", {
      method: "POST",
      body: formData,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `ROI 저장 실패 ${response.status}`);
    }

    await loadPolicy({ configId: payload.config_id, roiName: rois[0].name });
    state.roiDraft = null;
    state.isRoiEditing = false;
    elements.roiNameInput.value = nextRoiName();
    showTab("roi");
    setStatus("ROI 저장 완료", "success");
    addLog(`${payload.store_name} / ${payload.cctv_nickname} ${rois[0].name} 저장 완료`, "success");
  } catch (error) {
    setStatus(error.message || "ROI 저장 실패", "error");
    addLog(error.message || "ROI 저장 실패", "error");
  } finally {
    updateRoiUi();
  }
}

function startUploadLoop() {
  if (!state.stream) {
    setStatus("카메라 스트림이 필요합니다.", "error");
    return;
  }
  try {
    selectedFormValues();
  } catch (error) {
    setStatus(error.message, "error");
    addLog(error.message, "error");
    return;
  }
  state.uploading = true;
  updateCameraUi();
  runUploadCycle();
}

function stopAll() {
  stopUploadLoop();
  stopStream();
  setStatus("중지됨", "idle");
  addLog("촬영을 중지했습니다.", "info");
}

async function loadAuthStatus() {
  const response = await fetch(buildApiUrl("/api/auth/status"));
  if (!response.ok) {
    throw new Error(`인증 상태 확인 실패 ${response.status}`);
  }
  return response.json();
}

async function verifyStoredToken() {
  if (!state.authToken) {
    return false;
  }
  const response = await apiFetch("/api/auth/me");
  return response.ok;
}

async function unlockApplication() {
  elements.authPanel.classList.add("hidden");
  elements.appShell.classList.remove("hidden");
  validateSecureContext();
  updateCameraUi();
  await loadPolicy();
}

async function initializeAuth() {
  try {
    const status = await loadAuthStatus();
    if (!status.has_owner) {
      clearAuthToken();
      showAuthPanel("bootstrap");
      setAuthMessage("처음 사용할 점주 계정을 생성하세요.", "info");
      return;
    }

    if (await verifyStoredToken()) {
      await unlockApplication();
      return;
    }

    clearAuthToken();
    showAuthPanel("login");
    setAuthMessage("점주 아이디와 비밀번호를 입력하세요.", "info");
  } catch (error) {
    showAuthPanel("login");
    setAuthMessage(error.message || "인증 서버에 연결할 수 없습니다.", "error");
  }
}

async function submitAuth(event) {
  event.preventDefault();
  const userId = elements.authUserIdInput.value.trim();
  const password = elements.authPasswordInput.value;
  if (!userId || !password) {
    setAuthMessage("아이디와 비밀번호를 입력하세요.", "error");
    return;
  }

  const formData = new FormData();
  formData.append("user_id", userId);
  formData.append("password", password);
  const endpoint = state.authMode === "bootstrap" ? "/api/auth/bootstrap" : "/api/auth/login";

  elements.authSubmitButton.disabled = true;
  setAuthMessage(state.authMode === "bootstrap" ? "계정 생성 중..." : "로그인 중...", "info");
  try {
    const response = await fetch(buildApiUrl(endpoint), {
      method: "POST",
      body: formData,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `인증 실패 ${response.status}`);
    }
    setAuthenticated(payload.access_token);
    elements.authPasswordInput.value = "";
    await unlockApplication();
    addLog(`${payload.user_id || userId} 로그인`, "success");
  } catch (error) {
    clearAuthToken();
    showAuthPanel(state.authMode);
    setAuthMessage(error.message || "로그인에 실패했습니다.", "error");
  } finally {
    elements.authSubmitButton.disabled = false;
  }
}

function bindEvents() {
  elements.authForm.addEventListener("submit", submitAuth);
  elements.logoutButton.addEventListener("click", () => handleLogout());
  elements.loadPolicyButton.addEventListener("click", async () => {
    try {
      await loadPolicy();
    } catch (error) {
      setStatus(error.message || "설정 요청 실패", "error");
      addLog(error.message || "설정 요청 실패", "error");
    }
  });
  elements.configSelect.addEventListener("change", () => renderRoiOptions());
  elements.roiSelect.addEventListener("change", renderRoiOverlay);
  elements.roiStoreInput.addEventListener("change", syncConfigSelectionFromRoiInputs);
  elements.roiCctvInput.addEventListener("change", syncConfigSelectionFromRoiInputs);
  elements.captureTabButton.addEventListener("click", () => showTab("capture"));
  elements.roiTabButton.addEventListener("click", () => showTab("roi"));
  elements.roiModeButton.addEventListener("click", () => {
    state.isRoiEditing = !state.isRoiEditing;
    showTab("roi");
  });
  elements.clearRoiButton.addEventListener("click", clearRoi);
  elements.saveRoiButton.addEventListener("click", saveRoiConfig);
  elements.roiLayer.addEventListener("pointerdown", startRoiDraw);
  elements.roiLayer.addEventListener("pointermove", moveRoiDraw);
  elements.roiLayer.addEventListener("pointerup", endRoiDraw);
  elements.roiLayer.addEventListener("pointercancel", endRoiDraw);
  elements.openCameraButton.addEventListener("click", openCamera);
  elements.startUploadButton.addEventListener("click", startUploadLoop);
  elements.stopButton.addEventListener("click", stopAll);
  elements.clearLogButton.addEventListener("click", () => {
    elements.logList.innerHTML = "";
  });
  elements.cameraPreview.addEventListener("loadedmetadata", renderRoiOverlay);
  window.addEventListener("resize", renderRoiOverlay);
  window.addEventListener("beforeunload", stopAll);
}

function init() {
  elements.backendBaseInput.value = defaultBackendBase();
  elements.deviceIdInput.value = getOrCreateDeviceId();
  bindEvents();
  showAuthPanel("login");
  initializeAuth();
}

init();
