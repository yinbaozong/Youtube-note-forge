importScripts("protocol.js");

const PLUGIN_BASE = "http://127.0.0.1:32191";
const {
  CONNECTION_ERROR_CODE,
  CONNECTION_ERROR_MESSAGE,
  buildStartPayload,
  createInitialState,
  isAsrRetryableCode,
  normalizePluginSettings,
  statusPatch
} = YouTubeReaderProtocol;

let state = createInitialState();
let pollTimer = null;
let activePoll = null;

function createConnectionError() {
  const error = new Error(CONNECTION_ERROR_MESSAGE);
  error.code = CONNECTION_ERROR_CODE;
  return error;
}

async function updateActionIndicator(jobState) {
  let badge = "";
  let color = "#6b7785";
  if (jobState.status === "running") {
    badge = `${Math.max(0, Math.min(100, Number(jobState.progress_percent) || 0))}%`;
    color = "#1976d2";
  } else if (jobState.status === "ok") {
    badge = "OK";
    color = "#198754";
  } else if (jobState.status === "error") {
    badge = "!";
    color = "#c73535";
  } else if (jobState.status === "cancelled") {
    badge = "STOP";
    color = "#6b7785";
  }
  const title = jobState.note_path
    ? `YouTube 阅读器\n文件位置：${jobState.note_path}`
    : `YouTube 阅读器：${jobState.message || "准备就绪"}`;
  await Promise.all([
    chrome.action.setBadgeText({ text: badge }),
    chrome.action.setBadgeBackgroundColor({ color }),
    chrome.action.setTitle({ title })
  ]);
}

async function updateState(patch) {
  state = { ...state, ...patch };
  await chrome.storage.session.set({ jobState: state });
  await updateActionIndicator(state);
}

async function pluginFetch(path, options = {}) {
  let response;
  try {
    response = await fetch(PLUGIN_BASE + path, options);
  } catch (_error) {
    throw createConnectionError();
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error("Obsidian 插件返回了无效响应");
  }
  if (!response.ok || payload?.type === "error") {
    const error = new Error(payload?.message || `Obsidian 插件请求失败（${response.status}）`);
    error.code = payload?.code || "OBSIDIAN_PLUGIN_REQUEST_FAILED";
    error.can_retry_asr = payload?.can_retry_asr === true;
    throw error;
  }
  return payload;
}

function pluginRequest(payload) {
  return pluginFetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

function pluginStatus(requestId) {
  return pluginFetch("/status?request_id=" + encodeURIComponent(requestId));
}

function pluginHealth() {
  return pluginFetch("/health");
}

function pluginActive() {
  return pluginFetch("/active");
}

function pluginLatest() {
  return pluginFetch("/latest");
}

function pluginOutputDir(environment) {
  if (environment.output_dir) return environment.output_dir;
  const vault = environment.current_vault || environment.vault || environment.default_vault || "";
  return vault ? vault.replace(/[\\/]+$/, "") + "\\YouTube video" : "由 Obsidian 插件确定";
}

async function getPluginSettings() {
  const health = await pluginHealth();
  let settings = {};
  try {
    settings = await pluginRequest({ type: "get_settings", request_id: crypto.randomUUID() });
  } catch (error) {
    if (error.code === CONNECTION_ERROR_CODE) throw error;
  }
  return normalizePluginSettings(health, settings);
}

function applyStatus(message) {
  return statusPatch(state, message);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function pollJob() {
  if (activePoll || state.status !== "running" || !state.request_id) return state;
  activePoll = (async () => {
    try {
      let message = await pluginStatus(state.request_id);
      if (message.status === "idle") {
        const latest = await pluginLatest();
        message = latest.request_id === state.request_id && ["ok", "error", "cancelled"].includes(latest.status)
          ? latest
          : {
              type: "error",
              request_id: state.request_id,
              status: "error",
              stage: "failed",
              code: "TASK_INTERRUPTED",
              message: "活动任务已经中断。可点击“继续上次任务”，复用已有素材并完成文章。"
            };
      }
      await updateState(applyStatus(message));
      if (state.status !== "running") stopPolling();
    } catch (error) {
      stopPolling();
      await updateState({
        status: "error",
        stage: "failed",
        message: error.message,
        code: error.code || "",
        can_retry_asr: error.can_retry_asr === true || isAsrRetryableCode(error.code)
      });
    } finally {
      activePoll = null;
    }
    return state;
  })();
  return activePoll;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollJob, 5000);
}

function cleanVideoTitle(title) {
  return (title || "当前 YouTube 视频")
    .replace(/^\(\d+\)\s*/, "")
    .replace(/\s+-\s+YouTube$/, "")
    .trim();
}

function isYouTubeUrl(url) {
  return /^(https:\/\/(www\.|m\.)?youtube\.com\/(watch|shorts)\b|https:\/\/youtu\.be\/)/.test(url || "");
}

async function startJob(options = {}) {
  if (state.status === "running") {
    await pollJob();
    startPolling();
    return state;
  }

  let active;
  try {
    active = await pluginActive();
  } catch (error) {
    await updateState(createInitialState({
      status: "error",
      stage: "failed",
      message: error.message,
      code: error.code || ""
    }));
    return state;
  }
  if (active.status === "running" && active.request_id) {
    await updateState(applyStatus(active));
    await updateState({ request_id: active.request_id });
    startPolling();
    return state;
  }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = options.url || tab?.url || "";
  if (!isYouTubeUrl(url)) {
    await updateState(createInitialState({
      status: "error",
      stage: "failed",
      message: "请先打开一个 YouTube 视频页面"
    }));
    return state;
  }
  const title = cleanVideoTitle(options.title || (url === tab?.url ? tab?.title : state.video_title));
  const resume = Boolean(options.resume);
  const allowAsr = Boolean(options.allow_asr);
  await updateState(createInitialState({
    status: "running",
    stage: "credentials",
    message: allowAsr ? "正在同步 Cookie，随后将使用 ASR" : "正在同步 YouTube Cookie",
    progress_percent: 1,
    video_title: title,
    video_url: url,
    output_dir: "由 Obsidian 插件确定",
    allow_asr: allowAsr
  }));
  const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
  if (!cookies.length) {
    await updateState({ status: "error", stage: "failed", message: "没有读取到 YouTube Cookie，请先登录 YouTube" });
    return state;
  }

  let environment = {};
  try {
    environment = await pluginHealth();
  } catch (error) {
    await updateState({ status: "error", stage: "failed", message: error.message, code: error.code || "" });
    return state;
  }

  const requestId = crypto.randomUUID();
  await updateState({
    ...createInitialState(),
    status: "running",
    stage: "credentials",
    message: allowAsr
      ? "已允许 ASR，正在准备下载音频并生成字幕"
      : resume
        ? "正在恢复上次任务，并检查可复用的字幕、截图和草稿"
        : "正在把当前视频交给 Obsidian 插件处理",
    progress_percent: resume ? 4 : 1,
    current: 0,
    total: 0,
    video_title: title,
    video_url: url,
    output_dir: pluginOutputDir(environment),
    request_id: requestId,
    allow_asr: allowAsr,
    can_retry_asr: false
  });

  try {
    const response = await pluginRequest(buildStartPayload({
      requestId,
      url,
      title,
      cookies,
      resume,
      allowAsr
    }));
    await updateState(applyStatus(response));
    if (response.type === "attached" && response.active_request_id) {
      await updateState({ request_id: response.active_request_id });
    }
    startPolling();
  } catch (error) {
    await updateState({
      status: "error",
      stage: "failed",
      message: error.message,
      code: error.code || "",
      can_retry_asr: error.can_retry_asr === true || isAsrRetryableCode(error.code)
    });
  }
  return state;
}

async function resumeJob() {
  if (state.status === "running") return state;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = state.video_url || (isYouTubeUrl(tab?.url) ? tab.url : "");
  if (!url) throw new Error("找不到上次任务的视频链接，请重新打开原视频页面");
  return startJob({
    url,
    title: state.video_title || cleanVideoTitle(tab?.title || "上次未完成的视频"),
    resume: true,
    allow_asr: state.allow_asr === true
  });
}

async function cancelJob() {
  const wasRunning = state.status === "running";
  if (wasRunning && state.request_id) {
    await pluginRequest({ type: "cancel_job", request_id: state.request_id });
  }
  stopPolling();
  if (wasRunning) {
    await updateState({
      status: "cancelled",
      stage: "cancelled",
      message: "任务已强制停止",
      current: 0,
      total: 0,
      can_retry_asr: false
    });
    return;
  }
  try {
    await pluginRequest({ type: "clear_job", request_id: state.request_id || crypto.randomUUID() });
  } catch (_error) {
    // Local state can still be cleared while Obsidian is temporarily offline.
  }
  await updateState(createInitialState({
    message: "准备就绪，可以开始新任务",
    dismissed_request_id: state.request_id || state.dismissed_request_id || ""
  }));
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message.type === "get_status") {
      const stored = await chrome.storage.session.get("jobState");
      if (stored.jobState) state = createInitialState(stored.jobState);
      if (state.status === "running") await pollJob();
      if (state.status !== "running") {
        try {
          const active = await pluginActive();
          if (active.status === "running" && active.request_id) {
            await updateState(applyStatus(active));
            await updateState({ request_id: active.request_id });
            startPolling();
          } else {
            const latest = await pluginLatest();
            if (
              ["ok", "error", "cancelled"].includes(latest.status)
              && (state.status === "idle" || latest.request_id !== state.request_id)
              && latest.request_id !== state.dismissed_request_id
            ) {
              await updateState(applyStatus(latest));
              await updateState({ request_id: latest.request_id });
            }
          }
        } catch (_error) {
          // Keep the persisted job visible while Obsidian is closed or reconnecting.
        }
      }
      return state;
    }
    if (message.type === "start_job") return startJob(message);
    if (message.type === "resume_job") return resumeJob();
    if (message.type === "cancel_job") {
      await cancelJob();
      return state;
    }
    if (message.type === "retry_connection") {
      try {
        await pluginHealth();
        await updateState(createInitialState({ message: "已连接 Obsidian 插件，可以开始新任务" }));
      } catch (error) {
        await updateState({ status: "error", stage: "failed", message: error.message, code: error.code || "" });
      }
      return state;
    }
    if (message.type === "get_settings") return getPluginSettings();
    if (message.type === "open_obsidian_settings") {
      return pluginRequest({ type: "open_settings", request_id: crypto.randomUUID() });
    }
    if (message.type === "open_note") {
      return pluginRequest({
        type: "open_note",
        request_id: crypto.randomUUID(),
        note_path: message.note_path
      });
    }
    throw new Error("未知插件消息");
  })().then(sendResponse).catch(error => sendResponse({
    type: "error",
    message: error.message,
    code: error.code || "",
    can_retry_asr: error.can_retry_asr === true || isAsrRetryableCode(error.code)
  }));
  return true;
});
