import http, { type IncomingMessage, type ServerResponse } from "node:http";

import type { JobManager } from "./job-manager";
import { isAllowedExtensionOrigin } from "./origin";

const HOST = "127.0.0.1";
const PORT = 32191;
const MAX_BODY_BYTES = 8 * 1024 * 1024;

export class PluginHttpServer {
  private server: http.Server | null = null;

  constructor(private readonly jobs: JobManager) {}

  async start(): Promise<void> {
    if (this.server) return;
    this.server = http.createServer((request, response) => void this.handle(request, response));
    await new Promise<void>((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(PORT, HOST, () => resolve());
    });
  }

  async stop(): Promise<void> {
    const server = this.server;
    this.server = null;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const url = new URL(request.url || "/", `http://${HOST}:${PORT}`);
    const origin = String(request.headers.origin || "");
    const chromeOrigin = isAllowedExtensionOrigin(origin);
    const emptyHealthProbe = !origin && request.method === "GET" && url.pathname === "/health";
    if (!chromeOrigin && !emptyHealthProbe) {
      this.send(response, 403, { type: "error", status: "error", message: "Extension origin is not allowed." }, origin);
      return;
    }
    if (request.method === "OPTIONS") {
      response.writeHead(204, corsHeaders(origin));
      response.end();
      return;
    }
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        this.send(response, 200, { status: "ok", host: "youtube-note-reader", version: "4.0.1" }, origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/active") {
        this.send(response, 200, this.jobs.active() || { type: "status", status: "idle" }, origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/latest") {
        this.send(response, 200, this.jobs.latest, origin);
        return;
      }
      if (request.method === "GET" && url.pathname === "/status") {
        const requestId = url.searchParams.get("request_id") || "";
        this.send(response, 200, this.jobs.statusFor(requestId) || { type: "status", status: "idle", request_id: requestId }, origin);
        return;
      }
      if (request.method === "POST" && url.pathname === "/rpc") {
        const payload = await readJson(request);
        this.send(response, 200, await this.jobs.handleRpc(payload), origin);
        return;
      }
      this.send(response, 404, { type: "error", status: "error", message: "Not found." }, origin);
    } catch (error) {
      this.send(response, 400, {
        type: "error",
        status: "error",
        message: String((error as Error).message || error),
      }, origin);
    }
  }

  private send(response: ServerResponse, status: number, payload: object, origin: string): void {
    const body = JSON.stringify(payload);
    response.writeHead(status, {
      ...corsHeaders(origin),
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": Buffer.byteLength(body),
    });
    response.end(body);
  }
}

function corsHeaders(origin: string): Record<string, string> {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
    Vary: "Origin",
  };
  if (isAllowedExtensionOrigin(origin)) headers["Access-Control-Allow-Origin"] = origin;
  return headers;
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
