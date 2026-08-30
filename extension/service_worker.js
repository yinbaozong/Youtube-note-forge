const COMPANION_BASE = "http://127.0.0.1:32191";
const DEFAULT_SETTINGS = {
  model: "deepseek/deepseek-v4-pro",
  vault: "C:\\Users\\win11\\Documents\\Obsidian Vault",
  auto_open_note: true
};

let state = {
  status: "idle",
  stage: "idle",
  message: "准备就绪",
  elapsed_seconds: 0,
  progress_percent: 0,
  note_path: "",
  screenshot_count: 0
};
let pollTimer = null;
let activePoll = null;

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

async function companionFetch(path, options = {}) {
  let response;
  try {
    response = await fetch(COMPANION_BASE + path, options);
  } catch (_error) {
    throw new Error("本地桌面伴侣未启动，请重新运行 install.ps1");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error("本地桌面伴侣返回了无效响应");
  }
  if (!response.ok || payload?.type === "error") {
    throw new Error(payload?.message || `本地桌面伴侣请求失败（${response.status}）`);
  }
  return payload;
}

function companionRequest(payload) {
  return companionFetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

function companionStatus(requestId) {
  return companionFetch("/status?request_id=" + encodeURIComponent(requestId));
}

function companionHealth() {
  return companionFetch("/health");
}

function companionActive() {
  return companionFetch("/active");
}

function companionLatest() {
  return companionFetch("/latest");
}

async function getSettings() {
  return { ...DEFAULT_SETTINGS, ...(await chrome.storage.local.get(DEFAULT_SETTINGS)) };
}

function statusPatch(message) {
  const patch = {
    status: message.status || state.status,
    stage: message.stage || state.stage,
    message: message.message || state.message,
    elapsed_seconds: message.elapsed_seconds ?? state.elapsed_seconds,
    progress_percent: message.progress_percent ?? state.progress_percent ?? 0,
    current: message.current ?? state.current,
    total: message.total ?? state.total
  };
  if (message.type === "attached" && message.active_request_id) {
    patch.request_id = message.active_request_id;
    patch.status = "running";
  }
  if (message.type === "complete") {
    patch.status = "ok";
    patch.note_path = message.note_path || "";
    patch.screenshot_count = message.screenshot_count || 0;
    patch.message = message.note_opened
      ? "学习笔记已生成，已在 Obsidian 打开"
      : "学习笔记已生成并通过校验";
    patch.progress_percent = 100;
    patch.auto_opened = Boolean(message.note_opened);
  } else if (message.type === "error") {
    patch.status = "error";
  } else if (message.type === "cancelled") {
    patch.status = "cancelled";
  }
  return patch;
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function pollJob() {
  if (activePoll || state.status !== "running" || !state.request_id) return state;
  activePoll = (async () => {
    try {
      const message = await companionStatus(state.request_id);
      await updateState(statusPatch(message));
      if (state.status !== "running") stopPolling();
    } catch (error) {
      stopPolling();
      await updateState({ status: "error", stage: "failed", message: error.message });
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

async function startJob() {
  if (state.status === "running") {
    await pollJob();
    startPolling();
    return state;
  }
  const active = await companionActive();
  if (active.status === "running" && active.request_id) {
    await updateState(statusPatch(active));
    await updateState({ request_id: active.request_id });
    startPolling();
    return state;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const youtubePattern = /^(https:\/\/(www\.|m\.)?youtube\.com\/(watch|shorts)\b|https:\/\/youtu\.be\/)/;
  if (!tab?.url || !youtubePattern.test(tab.url)) throw new Error("请先打开一个 YouTube 视频页面");
  const settings = await getSettings();
  const cookies = await chrome.cookies.getAll({ domain: "youtube.com" });
  if (!cookies.length) throw new Error("没有读取到 YouTube Cookie，请先登录 YouTube");
  const requestId = crypto.randomUUID();
  await updateState({
    status: "running",
    stage: "credentials",
    message: "正在把当前视频交给 youtube-transcript Skill",
    elapsed_seconds: 0,
    progress_percent: 1,
    current: 0,
    total: 0,
    note_path: "",
    screenshot_count: 0,
    auto_opened: false,
    request_id: requestId
  });
  try {
    const response = await companionRequest({
      type: "start_job",
      request_id: requestId,
      url: tab.url,
      model: settings.model,
      vault: settings.vault,
      auto_open_note: settings.auto_open_note,
      cookies
    });
    await updateState(statusPatch(response));
    if (response.type === "attached" && response.active_request_id) {
      await updateState({ request_id: response.active_request_id });
    }
    startPolling();
    return state;
  } catch (error) {
    await updateState({ status: "error", stage: "failed", message: error.message });
    throw error;
  }
}

async function cancelJob() {
  if (state.status === "running" && state.request_id) {
    await companionRequest({ type: "cancel_job", request_id: state.request_id });
  }
  stopPolling();
  await updateState({ status: "cancelled", stage: "cancelled", message: "任务已强制停止" });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message.type === "get_status") {
      const stored = await chrome.storage.session.get("jobState");
      if (stored.jobState) state = stored.jobState;
      if (state.status === "running") await pollJob();
      if (state.status !== "running") {
        try {
          const active = await companionActive();
          if (active.status === "running" && active.request_id) {
            await updateState(statusPatch(active));
            await updateState({ request_id: active.request_id });
            startPolling();
          } else {
            const latest = await companionLatest();
            if (
              ["ok", "error", "cancelled"].includes(latest.status)
              && (state.status === "idle" || latest.request_id !== state.request_id)
            ) {
              await updateState(statusPatch(latest));
              await updateState({ request_id: latest.request_id });
            }
          }
        } catch (_error) {
          // The normal error is rendered from the stored task state below.
        }
      }
      if (state.status === "error" && /native messaging host/i.test(state.message || "")) {
        await companionHealth();
        await updateState({ status: "idle", stage: "idle", message: "准备就绪", elapsed_seconds: 0 });
      }
      return state;
    }
    if (message.type === "start_job") return await startJob();
    if (message.type === "cancel_job") {
      await cancelJob();
      return state;
    }
    if (message.type === "list_models") {
      return await companionRequest({ type: "list_models", request_id: crypto.randomUUID() });
    }
    if (message.type === "configure") {
      return await companionRequest({
        type: "configure",
        request_id: crypto.randomUUID(),
        model: message.model,
        api_key: message.api_key || ""
      });
    }
    if (message.type === "open_note") {
      const settings = await getSettings();
      return await companionRequest({
        type: "open_note",
        request_id: crypto.randomUUID(),
        note_path: message.note_path,
        vault: settings.vault
      });
    }
    throw new Error("未知插件消息");
  })().then(sendResponse).catch(error => sendResponse({ type: "error", message: error.message }));
  return true;
});
