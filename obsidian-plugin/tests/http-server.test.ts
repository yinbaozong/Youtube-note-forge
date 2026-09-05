import assert from "node:assert/strict";
import test from "node:test";

import type { JobManager } from "../src/job-manager";
import { PluginHttpServer } from "../src/http-server";

const EXTENSION_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop";
const CLIENT_IDENTITY = "abcdefghijklmnopabcdefghijklmnop@4.1.1";

function createJobs(): JobManager {
  return {
    active: () => null,
    latest: { type: "status", status: "idle" },
    statusFor: () => null,
    handleRpc: async (payload: Record<string, unknown>) => ({
      type: payload.type === "get_settings" ? "settings" : "ok",
      settings: payload.type === "get_settings" ? { plugin_version: "4.1.1" } : undefined,
    }),
  } as unknown as JobManager;
}

async function withServer(run: (baseUrl: string) => Promise<void>): Promise<void> {
  const server = new PluginHttpServer(createJobs(), { port: 0 });
  await server.start();
  try {
    await run(server.baseUrl);
  } finally {
    await server.stop();
  }
}

test("keeps the originless health probe available without authorizing job endpoints", async () => {
  await withServer(async (baseUrl) => {
    const health = await fetch(`${baseUrl}/health`);
    assert.equal(health.status, 200);

    const active = await fetch(`${baseUrl}/active`);
    assert.equal(active.status, 403);
    assert.deepEqual(await active.json(), {
      type: "error",
      status: "error",
      code: "EXTENSION_CLIENT_REJECTED",
      message: "Chrome 扩展请求未通过本地连接校验。",
      endpoint: "GET /active",
      plugin_version: "4.1.1",
    });
  });
});

test("accepts marked extension requests when Chrome omits or nulls the actual Origin", async () => {
  await withServer(async (baseUrl) => {
    for (const origin of [undefined, "null", EXTENSION_ORIGIN]) {
      const headers = new Headers({ "X-YouTube-Reader-Client": CLIENT_IDENTITY });
      if (origin !== undefined) headers.set("Origin", origin);
      const response = await fetch(`${baseUrl}/active`, { headers });
      assert.equal(response.status, 200, `origin=${String(origin)}`);
      assert.equal((await response.json() as { status: string }).status, "idle");
    }
  });
});

test("allows the extension preflight and rejects ordinary web origins", async () => {
  await withServer(async (baseUrl) => {
    const preflight = await fetch(`${baseUrl}/rpc`, {
      method: "OPTIONS",
      headers: {
        Origin: EXTENSION_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-youtube-reader-client",
      },
    });
    assert.equal(preflight.status, 204);
    assert.equal(preflight.headers.get("access-control-allow-origin"), EXTENSION_ORIGIN);
    assert.match(preflight.headers.get("access-control-allow-headers") || "", /X-YouTube-Reader-Client/i);

    const web = await fetch(`${baseUrl}/active`, {
      headers: {
        Origin: "https://www.youtube.com",
        "X-YouTube-Reader-Client": CLIENT_IDENTITY,
      },
    });
    assert.equal(web.status, 403);
  });
});

test("accepts authenticated RPC requests from the extension request path", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(`${baseUrl}/rpc`, {
      method: "POST",
      headers: {
        Origin: EXTENSION_ORIGIN,
        "Content-Type": "application/json",
        "X-YouTube-Reader-Client": CLIENT_IDENTITY,
      },
      body: JSON.stringify({ type: "get_settings", request_id: "test-request" }),
    });
    assert.equal(response.status, 200);
    assert.equal((await response.json() as { type: string }).type, "settings");
  });
});
