import http, { type IncomingMessage, type ServerResponse } from "node:http";

import type { JobManager } from "./job-manager";
import { isAllowedExtensionOrigin } from "./origin";

const HOST = "127.0.0.1";
const PORT = 32191;
const MAX_BODY_BYTES = 8 * 1024 * 1024;
const CLIENT_HEADER = "x-youtube-reader-client";
const PLUGIN_VERSION = "4.0.4";

export interface PluginHttpServerOptions {
  host?: string;
  port?: number;
}

export class PluginHttpServer {
  private server: http.Server | null = null;
  private readonly host: string;
  private readonly port: number;

  constructor(private readonly jobs: JobManager, options: PluginHttpServerOptions = {}) {
    this.host = options.host || HOST;
    this.port = options.port ?? PORT;
  }

  get baseUrl(): string {
    const address = this.server?.address();
    if (!address || typeof address === "string") throw new Error("HTTP_SERVER_NOT_STARTED");
    return `http://${this.host}:${address.port}`;
  }

  async start(): Promise<void> {
    if (this.server) return;
    this.server = http.createServer((request, response) => void this.handle(request, response));
    await new Promise<void>((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(this.port, this.host, () => resolve());
    });
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const url = new URL(request.url || "/", `http://${this.host}:${this.port}`);
    const origin = String(request.headers.origin || "");
    if (request.method === "OPTIONS") {
      if (!isAllowedExtensionOrigin(origin)) {
        this.rejectClient(response, request, url, origin);
        return;
      }
      response.writeHead(204, corsHeaders(origin));
      response.end();
      return;
    }
    const clientIdentity = String(request.headers[CLIENT_HEADER] || "");
    const emptyHealthProbe = !origin && !clientIdentity && request.method === "GET" && url.pathname === "/health";
    const extensionRequest = isAllowedClientIdentity(clientIdentity)
      && (isAllowedExtensionOrigin(origin) || !origin || origin === "null");
    if (!extensionRequest && !emptyHealthProbe) {
      this.rejectClient(response, request, url, origin);
      return;
    }
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        this.send(response, 200, { status: "ok", host: "youtube-note-reader", version: PLUGIN_VERSION }, origin, extensionRequest);
        return;
      }
      if (request.method === "GET" && url.pathname === "/active") {
        this.send(response, 200, this.jobs.active() || { type: "status", status: "idle" }, origin, extensionRequest);
        return;
      }
      if (request.method === "GET" && url.pathname === "/latest") {
        this.send(response, 200, this.jobs.latest, origin, extensionRequest);
        return;
      }
      if (request.method === "GET" && url.pathname === "/status") {
        const requestId = url.searchParams.get("request_id") || "";
        this.send(response, 200, this.jobs.statusFor(requestId) || { type: "status", status: "idle", request_id: requestId }, origin, extensionRequest);
        return;
      }
      if (request.method === "POST" && url.pathname === "/rpc") {
        const payload = await readJson(request);
        this.send(response, 200, await this.jobs.handleRpc(payload), origin, extensionRequest);
        return;
      }
      this.send(response, 404, { type: "error", status: "error", message: "Not found." }, origin, extensionRequest);
    } catch (error) {
      this.send(response, 400, {
        type: "error",
        status: "error",
        message: String((error as Error).message || error),
      }, origin, extensionRequest);
    }
  }

  private rejectClient(response: ServerResponse, request: IncomingMessage, url: URL, origin: string): void {
    this.send(response, 403, {
      type: "error",
      status: "error",
      code: "EXTENSION_CLIENT_REJECTED",
      message: "Chrome 扩展请求未通过本地连接校验。",
      endpoint: `${request.method || "UNKNOWN"} ${url.pathname}`,
      plugin_version: PLUGIN_VERSION,
    }, origin, false);
  }

  private send(response: ServerResponse, status: number, payload: object, origin: string, extensionRequest = false): void {
    const body = JSON.stringify(payload);
    response.writeHead(status, {
      ...corsHeaders(origin, extensionRequest),
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
    });
    response.end(body);
  }
}

function corsHeaders(origin: string, extensionRequest = false): Record<string, string> {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-YouTube-Reader-Client",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
  if (isAllowedExtensionOrigin(origin) || (extensionRequest && origin === "null")) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function isAllowedClientIdentity(value: string): boolean {
  return /^[a-z0-9-]{1,128}@\d+\.\d+\.\d+(?:[-+][a-z0-9.-]+)?$/i.test(value);
}

async function readJson(request: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_BODY_BYTES) throw new Error("REQUEST_TOO_LARGE: 请求超过 8 MB。\n");
    chunks.push(buffer);
  }
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("RPC 请求必须是 JSON 对象。");
  return parsed as Record<string, unknown>;
}
