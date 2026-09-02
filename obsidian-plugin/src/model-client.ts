import { requestUrl } from "obsidian";

import {
  validateApiCredentials,
  type CredentialValidationOptions,
} from "./credential-validation";

export interface ChatJsonOptions {
  apiBase: string;
  apiKey: string;
  model: string;
  system: string;
  user: string;
  signal: AbortSignal;
  temperature?: number;
  timeoutMs?: number;
}

export async function requestChatJson<T>(options: ChatJsonOptions): Promise<T> {
  const base = options.apiBase.replace(/\/+$/, "");
  const url = base.endsWith("/chat/completions") ? base : `${base}/chat/completions`;
  const request = requestUrl({
    url,
    method: "POST",
    contentType: "application/json",
    headers: {
      Authorization: `Bearer ${options.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: options.model,
      temperature: options.temperature ?? 0.1,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: options.system },
        { role: "user", content: options.user },
      ],
    }),
  });
  const response = await raceAbort(request, options.signal, options.timeoutMs ?? 180_000);
  const content = response.json?.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) {
    throw new Error("MODEL_RESPONSE_INVALID: 模型没有返回可解析的 JSON 内容。");
  }
  return parseJsonObject<T>(content);
}

export function probeApiCredentials(options: CredentialValidationOptions): Promise<string> {
  return validateApiCredentials(options, async (request) => {
    const response = await requestUrl(request);
    return { status: response.status, json: response.json, text: response.text };
  });
}

export function parseJsonObject<T>(content: string): T {
  const unfenced = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  const start = unfenced.indexOf("{");
  const end = unfenced.lastIndexOf("}");
  if (start < 0 || end < start) throw new Error("MODEL_JSON_INVALID: 模型输出不包含 JSON 对象。");
  try {
    return JSON.parse(unfenced.slice(start, end + 1)) as T;
  } catch (error) {
    throw new Error(`MODEL_JSON_INVALID: ${(error as Error).message}`);
  }
}

async function raceAbort<T>(promise: Promise<T>, signal: AbortSignal, timeoutMs: number): Promise<T> {
  if (signal.aborted) throw new Error("CANCELLED: 任务已停止。");
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      callback();
    };
    const abort = () => finish(() => reject(new Error("CANCELLED: 任务已停止。")));
    const timeout = setTimeout(() => finish(
      () => reject(new Error("MODEL_TIMEOUT: 模型在阶段时限内没有返回，任务已停止。")),
    ), timeoutMs);
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => finish(() => resolve(value)),
      (error) => finish(() => reject(error)),
    );
  });
}
