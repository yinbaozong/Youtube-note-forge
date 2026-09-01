import { type ChildProcessByStdio, execFile, spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Readable } from "node:stream";

import { parsePipelineResults } from "./pipeline-result";
import type { PipelineResult } from "./types";

export interface ScriptProgress {
  stage?: string;
  message?: string;
  percent?: number;
  current?: number;
  total?: number;
  code?: string;
}

export interface ScriptRunResult {
  exitCode: number;
  output: string;
  results: PipelineResult[];
  timedOut: boolean;
}

export interface RunScriptOptions {
  cwd: string;
  signal: AbortSignal;
  timeoutMs: number;
  onProgress?: (progress: ScriptProgress) => void | Promise<void>;
}

export class PythonScriptRunner {
  private child: ChildProcessByStdio<null, Readable, Readable> | null = null;

  async run(python: string, script: string, args: string[], options: RunScriptOptions): Promise<ScriptRunResult> {
    if (this.child) throw new Error("PROCESS_BUSY: 已有 Skill 脚本正在运行。");
    const progressPath = path.join(os.tmpdir(), `youtube-note-reader-${crypto.randomUUID()}.jsonl`);
    const environment = { ...process.env, YOUTUBE_NOTE_PROGRESS_FILE: progressPath };
    const child = spawn(python, [script, ...args], {
      cwd: options.cwd,
      env: environment,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;
    let output = "";
    let progressLinesSeen = 0;
    const collect = (chunk: Buffer | string) => {
      output += chunk.toString();
      if (output.length > 4 * 1024 * 1024) output = output.slice(-4 * 1024 * 1024);
    };
    child.stdout.on("data", collect);
    child.stderr.on("data", collect);

    const readProgress = async () => {
      try {
        const content = await fs.readFile(progressPath, "utf8");
        const lines = content.split(/\r?\n/).filter(Boolean);
        for (const line of lines.slice(progressLinesSeen)) {
          try {
            const payload = JSON.parse(line) as ScriptProgress;
            await options.onProgress?.(payload);
          } catch {
            // A partially-written heartbeat is retried on the next poll.
          }
        }
        progressLinesSeen = lines.length;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
    };
    const progressTimer = setInterval(() => void readProgress(), 500);
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      void this.stop();
    }, options.timeoutMs);
    const abort = () => void this.stop();
    options.signal.addEventListener("abort", abort, { once: true });

    try {
      const exitCode = await new Promise<number>((resolve, reject) => {
        child.once("error", reject);
        child.once("close", (code) => resolve(code ?? 1));
      });
      await readProgress();
      if (options.signal.aborted) throw new Error("CANCELLED: 任务已停止。");
      return { exitCode, output, results: parsePipelineResults(output), timedOut };
    } finally {
      clearInterval(progressTimer);
      clearTimeout(timeout);
      options.signal.removeEventListener("abort", abort);
      this.child = null;
      await fs.rm(progressPath, { force: true }).catch(() => undefined);
    }
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child || child.exitCode !== null || !child.pid) return;
    if (process.platform === "win32") {
      await new Promise<void>((resolve) => {
        execFile("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true }, () => resolve());
      });
    } else {
      child.kill("SIGTERM");
    }
  }
}

export function timeoutErrorForScript(filename: string): string {
  const code = filename === "extract_transcript.py"
    ? "MATERIALS_TIMEOUT"
    : filename === "extract_frames.py"
      ? "FRAME_TIMEOUT"
      : filename === "validate_note.py"
        ? "NOTE_VALIDATION_TIMEOUT"
        : "SCRIPT_TIMEOUT";
  return `${code}: ${filename} 超过阶段时限，任务已停止。`;
}
