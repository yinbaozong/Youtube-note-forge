export interface CredentialValidationOptions {
  apiBase: string;
  apiKey: string;
  model: string;
  timeoutMs?: number;
}

export interface CredentialRequest {
  url: string;
  method: "POST";
  contentType: "application/json";
  throw: false;
  headers: Record<string, string>;
  body: string;
}

export interface CredentialResponse {
  status: number;
  json?: unknown;
  text?: string;
}

export type CredentialRequester = (request: CredentialRequest) => Promise<CredentialResponse>;

export async function validateApiCredentials(
  options: CredentialValidationOptions,
  request: CredentialRequester,
): Promise<string> {
  const apiBase = options.apiBase.trim().replace(/\/+$/, "");
  const apiKey = options.apiKey.trim();
  const model = options.model.trim();
  if (!apiBase) throw new Error("API_BASE_MISSING: 请先填写 API Base。");
  if (!apiKey) throw new Error("API_KEY_MISSING: 请先保存 API Key。");
  if (!model) throw new Error("MODEL_MISSING: 请先填写模型名称。");

  const url = apiBase.endsWith("/chat/completions") ? apiBase : `${apiBase}/chat/completions`;
  let response: CredentialResponse;
  try {
    response = await withTimeout(request({
      url,
      method: "POST",
      contentType: "application/json",
      throw: false,
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model,
        max_tokens: 4,
        messages: [{ role: "user", content: "Reply with OK." }],
      }),
    }), options.timeoutMs ?? 20_000);
  } catch (error) {
    const message = String((error as Error).message || error);
    if (message.startsWith("API_KEY_VALIDATION_TIMEOUT:")) throw error;
    throw new Error(`API_KEY_VALIDATION_FAILED: 无法连接模型服务。${message}`);
  }

  if (response.status === 401 || response.status === 403) {
    throw new Error("API_KEY_REJECTED: API Key 被服务商拒绝，请检查 Key、API Base 和账户权限。");
  }
  if (response.status < 200 || response.status >= 300) {
    const detail = String(response.text || "").replace(/\s+/g, " ").slice(0, 240);
    throw new Error(`API_KEY_VALIDATION_FAILED: 模型服务返回 HTTP ${response.status}${detail ? `：${detail}` : ""}`);
  }
  return `API Key 可用，模型 ${model} 连接成功。`;
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("API_KEY_VALIDATION_TIMEOUT: 验证超过 20 秒，请检查 API Base 或网络。")),
      timeoutMs,
    );
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}
