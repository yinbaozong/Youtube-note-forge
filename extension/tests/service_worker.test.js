const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const extensionRoot = path.resolve(__dirname, "..");

function createWorker(fetchImpl) {
  let listener;
  const stored = {};
  const context = vm.createContext({
    Headers,
    Response,
    URL,
    clearInterval() {},
    clearTimeout,
    console,
    crypto,
    fetch: fetchImpl,
    setInterval: () => 1,
    setTimeout,
  });
  context.globalThis = context;
  context.chrome = {
    action: {
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async () => {},
      setTitle: async () => {},
    },
    cookies: {
      getAll: async () => [{ domain: ".youtube.com", name: "SID", path: "/", value: "test" }],
    },
    runtime: {
      getManifest: () => ({ version: "4.0.4" }),
      id: "abcdefghijklmnopabcdefghijklmnop",
      onMessage: { addListener: (value) => { listener = value; } },
    },
    storage: {
      session: {
        get: async (key) => ({ [key]: stored[key] }),
        set: async (patch) => Object.assign(stored, patch),
      },
    },
    tabs: {
      query: async () => [{ title: "测试视频 - YouTube", url: "https://www.youtube.com/watch?v=test123" }],
    },
  };
  context.importScripts = (filename) => {
    vm.runInContext(fs.readFileSync(path.join(extensionRoot, filename), "utf8"), context, { filename });
  };
  vm.runInContext(fs.readFileSync(path.join(extensionRoot, "service_worker.js"), "utf8"), context, {
    filename: "service_worker.js",
  });

  return {
    send(message) {
      return new Promise((resolve) => {
        assert.equal(listener(message, {}, resolve), true);
      });
    },
  };
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("start job marks active, health, and RPC requests with the extension identity", async () => {
  const requests = [];
  const worker = createWorker(async (url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith("/active")) return jsonResponse({ type: "status", status: "idle" });
    if (url.endsWith("/health")) return jsonResponse({ status: "ok", version: "4.0.4", current_vault: "C:\\Vault" });
    return jsonResponse({
      type: "accepted",
      status: "running",
      stage: "credentials",
      request_id: "request-1",
      progress_percent: 2,
    });
  });

  const state = await worker.send({ type: "start_job" });
  assert.equal(state.status, "running");
  assert.equal(requests.length, 3);
  for (const request of requests) {
    assert.equal(
      new Headers(request.options.headers).get("X-YouTube-Reader-Client"),
      "abcdefghijklmnopabcdefghijklmnop@4.0.4"
    );
  }
});

test("settings never report connected when the authenticated RPC is rejected", async () => {
  const worker = createWorker(async (url) => {
    if (url.endsWith("/health")) return jsonResponse({ status: "ok", version: "4.0.4" });
    return jsonResponse({
      type: "error",
      status: "error",
      code: "EXTENSION_CLIENT_REJECTED",
      message: "Chrome 扩展请求未通过本地连接校验。",
      plugin_version: "4.0.4",
    }, 403);
  });

  const response = await worker.send({ type: "get_settings" });
  assert.equal(response.type, "error");
  assert.equal(response.code, "EXTENSION_CLIENT_REJECTED");
  assert.match(response.message, /POST \/rpc/);
  assert.notEqual(response.connected, true);
});
