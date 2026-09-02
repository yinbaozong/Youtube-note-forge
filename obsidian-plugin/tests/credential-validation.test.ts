import assert from "node:assert/strict";
import test from "node:test";

import {
  validateApiCredentials,
  type CredentialRequest,
} from "../src/credential-validation";

test("validates the saved key with a minimal chat completion request", async () => {
  const requests: CredentialRequest[] = [];
  const message = await validateApiCredentials({
    apiBase: "https://api.example.test/v1/",
    apiKey: "secret-key",
    model: "example-model",
    timeoutMs: 100,
  }, async (request) => {
    requests.push(request);
    return { status: 200, json: { choices: [{ message: { content: "OK" } }] } };
  });

  const captured = requests[0];
  assert.ok(captured);
  assert.equal(captured.url, "https://api.example.test/v1/chat/completions");
  assert.equal(captured.throw, false);
  assert.equal(captured.headers.Authorization, "Bearer secret-key");
  const body = JSON.parse(captured.body);
  assert.equal(body.model, "example-model");
  assert.equal(body.max_tokens, 4);
  assert.equal(message, "API Key 可用，模型 example-model 连接成功。");
});

test("reports rejected keys with an actionable error code", async () => {
  await assert.rejects(
    validateApiCredentials({
      apiBase: "https://api.example.test/v1",
      apiKey: "bad-key",
      model: "example-model",
      timeoutMs: 100,
    }, async () => ({ status: 401, text: "invalid token" })),
    /API_KEY_REJECTED/,
  );
});
