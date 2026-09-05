const test = require("node:test");
const assert = require("node:assert/strict");
const {
  CONNECTION_ERROR_MESSAGE,
  buildClientIdentity,
  buildStartPayload,
  createInitialState,
  isConnectionError,
  normalizePluginSettings,
  shouldOfferAsr,
  statusPatch
} = require("../protocol.js");

test("new task state clears ASR and previous output fields", () => {
  const state = createInitialState();
  assert.equal(state.can_retry_asr, false);
  assert.equal(state.allow_asr, false);
  assert.equal(state.note_path, "");
  assert.equal(state.screenshot_dir, "");
});

test("client identity binds every local request to the running extension version", () => {
  assert.equal(
    buildClientIdentity("abcdefghijklmnopabcdefghijklmnop", "4.0.4"),
    "abcdefghijklmnopabcdefghijklmnop@4.0.4"
  );
  assert.throws(() => buildClientIdentity("", "4.0.4"), /扩展 ID/);
});

test("start payload excludes Chrome-side model and vault configuration", () => {
  const payload = buildStartPayload({
    requestId: "request-1",
    url: "https://www.youtube.com/watch?v=abc",
    title: "视频标题",
    cookies: [{ name: "SID", value: "secret" }],
    resume: true,
    allowAsr: true
  });
  assert.deepEqual(payload, {
    type: "start_job",
    request_id: "request-1",
    url: "https://www.youtube.com/watch?v=abc",
    video_title: "视频标题",
    cookies: [{ name: "SID", value: "secret" }],
    resume: true,
    allow_asr: true
  });
  assert.equal("model" in payload, false);
  assert.equal("vault" in payload, false);
  assert.equal("api_key" in payload, false);
});

for (const code of ["SUBTITLE_UNAVAILABLE", "SUBTITLE_DOWNLOAD_FAILED", "SUBTITLE_PARSE_FAILED"]) {
  test(`${code} exposes ASR retry action`, () => {
    const current = createInitialState({ status: "running" });
    const next = { ...current, ...statusPatch(current, { type: "error", status: "error", code }) };
    assert.equal(next.can_retry_asr, true);
    assert.equal(shouldOfferAsr(next), true);
  });
}

test("backend retry flag exposes ASR action for another error code", () => {
  const current = createInitialState({ status: "running" });
  const next = {
    ...current,
    ...statusPatch(current, { type: "error", status: "error", code: "TRANSCRIPT_FAILED", can_retry_asr: true })
  };
  assert.equal(shouldOfferAsr(next), true);
});

test("backend resume flag survives status updates", () => {
  const current = createInitialState({ status: "running" });
  const next = { ...current, ...statusPatch(current, {
    type: "error",
    status: "error",
    code: "TASK_INTERRUPTED",
    can_resume: true
  }) };
  assert.equal(next.can_resume, true);
});

test("plugin settings prefer RPC values and fall back to health", () => {
  assert.deepEqual(
    normalizePluginSettings(
      { status: "ok", model: "health/model", default_vault: "C:\\Vault", version: "4.0.4" },
      { type: "settings", current_model: "rpc/model", api_key_configured: true }
    ),
    {
      connected: true,
      model: "rpc/model",
      vault: "C:\\Vault",
      plugin_version: "4.0.4",
      api_key_configured: true
    }
  );
  assert.deepEqual(
    normalizePluginSettings(
      { status: "ok", version: "4.0.4" },
      { type: "settings", settings: { model: "nested/model", vault: "D:\\Notes" } }
    ),
    {
      connected: true,
      model: "nested/model",
      vault: "D:\\Notes",
      plugin_version: "4.0.4",
      api_key_configured: false
    }
  );
});

test("health alone never reports a green connection without a successful settings RPC", () => {
  assert.deepEqual(
    normalizePluginSettings({ status: "ok", version: "4.0.4" }, {}),
    {
      connected: false,
      model: "未配置",
      vault: "未配置",
      plugin_version: "4.0.4",
      api_key_configured: false
    }
  );
});

test("connection failure copy is stable and actionable", () => {
  assert.equal(CONNECTION_ERROR_MESSAGE, "请先打开 Obsidian，并启用 YouTube Note Reader 插件。");
});

test("recognizes stale origin failures without hiding pipeline errors", () => {
  assert.equal(isConnectionError({ status: "error", code: "OBSIDIAN_PLUGIN_UNAVAILABLE" }), true);
  assert.equal(isConnectionError({ status: "error", code: "EXTENSION_CLIENT_REJECTED" }), true);
  assert.equal(isConnectionError({ status: "error", message: "Extension origin is not allowed." }), true);
  assert.equal(isConnectionError({ status: "error", code: "API_KEY_REJECTED" }), false);
  assert.equal(isConnectionError({ status: "ok", message: "Extension origin is not allowed." }), false);
});
