(function exposeProtocol(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.YouTubeReaderProtocol = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createProtocol() {
  const CONNECTION_ERROR_CODE = "OBSIDIAN_PLUGIN_UNAVAILABLE";
  const CONNECTION_ERROR_MESSAGE = "请先打开 Obsidian，并启用 YouTube Note Reader 插件。";
  const ASR_RETRYABLE_CODES = Object.freeze([
    "SUBTITLE_UNAVAILABLE",
    "SUBTITLE_DOWNLOAD_FAILED",
    "SUBTITLE_PARSE_FAILED"
  ]);

  const INITIAL_STATE = Object.freeze({
    status: "idle",
    stage: "idle",
    message: "准备就绪",
    elapsed_seconds: 0,
    progress_percent: 0,
    note_path: "",
    screenshot_count: 0,
    screenshot_dir: "",
    video_title: "",
    video_url: "",
    output_dir: "",
    request_id: "",
    dismissed_request_id: "",
    code: "",
    can_retry_asr: false,
    can_resume: false,
    allow_asr: false
  });

  function createInitialState(overrides = {}) {
    return { ...INITIAL_STATE, ...overrides };
  }

  function isAsrRetryableCode(code) {
    return ASR_RETRYABLE_CODES.includes(code || "");
  }

  function buildStartPayload({ requestId, url, title, cookies, resume = false, allowAsr = false }) {
    return {
      type: "start_job",
      request_id: requestId,
      url,
      video_title: title,
      cookies,
      resume: Boolean(resume),
      allow_asr: Boolean(allowAsr)
    };
  }

  function shouldOfferAsr(state) {
    return Boolean(
      state
      && state.status === "error"
      && (state.can_retry_asr === true || isAsrRetryableCode(state.code))
    );
  }

  function statusPatch(current, message) {
    const patch = {
      status: message.status || current.status,
      stage: message.stage || current.stage,
      message: message.message || current.message,
      elapsed_seconds: message.elapsed_seconds ?? current.elapsed_seconds,
      progress_percent: message.progress_percent ?? current.progress_percent ?? 0,
      current: message.current ?? current.current,
      total: message.total ?? current.total
    };
    if (message.code !== undefined) patch.code = message.code || "";
    if (message.allow_asr !== undefined) patch.allow_asr = Boolean(message.allow_asr);
    if (message.can_resume !== undefined) patch.can_resume = Boolean(message.can_resume);
    for (const key of ["video_title", "video_url", "output_dir"]) {
      if (message[key]) patch[key] = message[key];
    }
    if (message.title && !patch.video_title) patch.video_title = message.title;
    if (message.url && !patch.video_url) patch.video_url = message.url;
    if (message.screenshot_dir) patch.screenshot_dir = message.screenshot_dir;
    if (message.type === "attached" && message.active_request_id) {
      patch.request_id = message.active_request_id;
      patch.status = "running";
    }
    if (message.type === "complete") {
      patch.status = "ok";
      patch.note_path = message.note_path || "";
      patch.screenshot_count = message.screenshot_count || 0;
      patch.screenshot_dir = message.screenshot_dir || current.screenshot_dir || "";
      patch.message = message.note_opened
        ? "学习笔记已生成，已在 Obsidian 打开"
        : "学习笔记已生成并通过校验";
      patch.progress_percent = 100;
      patch.auto_opened = Boolean(message.note_opened);
      patch.can_retry_asr = false;
      patch.can_resume = false;
      patch.code = "";
    } else if (message.type === "error") {
      patch.status = "error";
      patch.current = message.current || 0;
      patch.total = message.total || 0;
      patch.can_retry_asr = message.can_retry_asr === true || isAsrRetryableCode(message.code);
    } else if (message.type === "cancelled") {
      patch.status = "cancelled";
      patch.can_retry_asr = false;
    } else if (message.can_retry_asr !== undefined) {
      patch.can_retry_asr = Boolean(message.can_retry_asr);
    }
    return patch;
  }

  function normalizePluginSettings(health = {}, settings = {}) {
    const rpc = settings.settings && typeof settings.settings === "object"
      ? settings.settings
      : settings;
    return {
      connected: true,
      model: rpc.current_model || rpc.model || health.current_model || health.model || "未配置",
      vault: rpc.current_vault || rpc.vault || rpc.default_vault
        || health.current_vault || health.vault || health.default_vault || "未配置",
      plugin_version: rpc.plugin_version || rpc.version || health.plugin_version || health.version || "未知",
      api_key_configured: rpc.api_key_configured === true
    };
  }

  return {
    ASR_RETRYABLE_CODES,
    CONNECTION_ERROR_CODE,
    CONNECTION_ERROR_MESSAGE,
    INITIAL_STATE,
    buildStartPayload,
    createInitialState,
    isAsrRetryableCode,
    normalizePluginSettings,
    shouldOfferAsr,
    statusPatch
  };
});
