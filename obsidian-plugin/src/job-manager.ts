import { promises as fs } from "node:fs";
import path from "node:path";

import type { App } from "obsidian";

import {
  bodyOnly,
  findReusableArtifacts,
  readFrameManifest,
  readNoteContract,
  readSkillVersion,
  validateArticlePlan,
  validateWritingResult,
  vaultRelative,
  type FrameManifest,
} from "./artifacts";
import { saveYouTubeCookies, type BrowserCookie } from "./cookies";
import { mapPipelineFailure } from "./errors";
import { requestChatJson } from "./model-client";
import { bindTranscript, frontmatterValue, resolveInsideVault, sanitizeNoteFilename, splitFrontmatter, updateFrontmatter } from "./note-utils";
import { PythonScriptRunner, timeoutErrorForScript, type ScriptRunResult } from "./process-runner";
import { planningPrompt, repairPrompt, writingPrompt } from "./prompts";
import { getApiKey, type YouTubeReaderSettings } from "./settings";
import type { PipelineResult, PipelineStage, PipelineStatus } from "./types";

const MAX_JOB_MS = 8 * 60 * 1000;
const STAGE_PERCENT: Record<string, number> = {
  credentials: 4,
  materials: 12,
  planning: 28,
  frames: 35,
  writing: 76,
  validation: 92,
  complete: 100,
};

export interface StartJobRequest {
  type: "start_job";
  request_id: string;
  url: string;
  video_title?: string;
  cookies?: BrowserCookie[];
  resume?: boolean;
  allow_asr?: boolean;
  browser_transcript?: { status?: string; [key: string]: unknown };
}

export interface JobState {
  [key: string]: unknown;
  type: string;
  request_id: string;
  status: PipelineStatus;
  stage: PipelineStage;
  message: string;
  elapsed_seconds: number;
  progress_percent: number;
  video_title?: string;
  video_url?: string;
  output_dir?: string;
  note_path?: string;
  screenshot_dir?: string;
  screenshot_count?: number;
  code?: string;
  can_retry_asr?: boolean;
  can_resume?: boolean;
  result_stage?: string;
  technical_message?: string;
}

export interface JobManagerOptions {
  app: App;
  vaultBasePath: string;
  getSettings: () => YouTubeReaderSettings;
  persist: (state: JobState) => Promise<void>;
  openNote: (absolutePath: string) => Promise<void>;
  openSettings: () => void;
  validateApiKey: () => Promise<string>;
  initialState?: JobState;
}

class PipelineFailure extends Error {
  constructor(readonly result: PipelineResult) {
    super(String(result.message || result.code || "Pipeline failed"));
  }
}

export class JobManager {
  private state: JobState;
  private controller: AbortController | null = null;
  private runner = new PythonScriptRunner();
  private running: Promise<void> | null = null;
  private startedAt = 0;
  private timedOut = false;

  constructor(private readonly options: JobManagerOptions) {
    this.state = options.initialState || idleState();
  }

  get latest(): JobState {
    return { ...this.state };
  }

  active(): JobState | null {
    return this.state.status === "running" ? this.latest : null;
  }

  statusFor(requestId: string): JobState | null {
    return this.state.request_id === requestId ? this.latest : null;
  }

  async markInterruptedOnLoad(): Promise<void> {
    if (this.state.status !== "running") return;
    await this.setState({
      type: "error",
      status: "error",
      stage: "failed",
      code: "TASK_INTERRUPTED",
      message: "Obsidian 插件曾在任务运行时退出。可使用原视频链接和 resume:true 继续任务。",
      can_resume: true,
    });
  }

  async handleRpc(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const type = String(payload.type || "");
    if (type === "start_job") return this.start(payload as unknown as StartJobRequest);
    if (type === "cancel_job") return this.cancel(String(payload.request_id || ""));
    if (type === "clear_job") {
      if (this.state.status === "running") throw new Error("ACTIVE_JOB_RUNNING: 请先停止当前任务。");
      this.state = idleState();
      await this.options.persist(this.state);
      return this.latest;
    }
    if (type === "open_note") {
      const note = String(payload.note_path || this.state.note_path || "");
      if (!note) throw new Error("NOTE_MISSING: 当前没有可打开的笔记。");
      await this.options.openNote(note);
      return { type: "opened", request_id: String(payload.request_id || ""), note_path: note };
    }
    if (type === "open_settings") {
      this.options.openSettings();
      return { type: "settings_opened", request_id: String(payload.request_id || "") };
    }
    if (type === "validate_api_key") {
      const message = await this.options.validateApiKey();
      return {
        type: "api_key_valid",
        request_id: String(payload.request_id || ""),
        message,
      };
    }
    if (type === "get_settings") {
      const settings = this.options.getSettings();
      return {
        type: "settings",
        request_id: String(payload.request_id || ""),
        settings: {
          ...settings,
          current_model: settings.model,
          current_vault: this.options.vaultBasePath,
          plugin_version: "4.0.5",
          api_key_configured: hasApiKey(this.options.app),
        },
      };
    }
    throw new Error(`RPC_UNSUPPORTED: 不支持的 RPC 类型：${type}`);
  }

  async start(request: StartJobRequest): Promise<JobState & { active_request_id?: string }> {
    if (this.running && this.state.status === "running") {
      return { ...this.latest, type: "attached", active_request_id: this.state.request_id };
    }
    if (!request.request_id || !isSupportedVideoUrl(request.url)) {
      throw new Error("VIDEO_URL_INVALID: 请提供有效的 YouTube 或 Bilibili 视频链接。");
    }
    this.startedAt = Date.now();
    this.timedOut = false;
    this.controller = new AbortController();
    const settings = this.options.getSettings();
    this.state = {
      type: "accepted",
      request_id: request.request_id,
      status: "running",
      stage: "credentials",
      message: request.resume ? "正在检查上次任务的可复用素材。" : "正在保存浏览器 Cookie。",
      elapsed_seconds: 0,
      progress_percent: 2,
      video_title: request.video_title || "当前视频",
      video_url: request.url,
      output_dir: resolveInsideVault(this.options.vaultBasePath, settings.outputFolder),
      allow_asr: Boolean(request.allow_asr),
      can_resume: false,
    };
    await this.options.persist(this.state);
    this.running = this.run(request).finally(() => {
      this.running = null;
      this.controller = null;
    });
    void this.running;
    return this.latest;
  }

  async cancel(requestId: string): Promise<JobState> {
    if (this.state.status !== "running") return this.latest;
    if (requestId && requestId !== this.state.request_id) return this.latest;
    this.controller?.abort();
    await this.runner.stop();
    await this.setState({
      type: "cancelled",
      status: "cancelled",
      stage: "cancelled",
      message: "任务已强制停止。",
      can_resume: true,
    });
    return this.latest;
  }

  async shutdown(): Promise<void> {
    if (this.state.status === "running") {
      await this.setState({
        type: "error",
        status: "error",
        stage: "failed",
        code: "TASK_INTERRUPTED",
        message: "Obsidian 已关闭，任务被中断，可在下次启动后继续。",
        can_resume: true,
      });
    }
    this.controller?.abort();
    await this.runner.stop();
  }

  private async run(request: StartJobRequest): Promise<void> {
    const signal = this.controller!.signal;
    const hardTimeout = setTimeout(() => {
      this.timedOut = true;
      this.controller?.abort();
      void this.runner.stop();
    }, MAX_JOB_MS);
    const heartbeat = setInterval(() => void this.heartbeat(), 5_000);
    let finalTarget = "";
    let priorFinal: string | null = null;
    let stagingNote = "";
    let published = false;
    try {
      const settings = this.options.getSettings();
      const apiKey = getApiKey(this.options.app);
      const skillVersion = await readSkillVersion(settings.skillDirectory);
      const contract = await readNoteContract(settings.skillDirectory);
      await assertSkillScripts(settings.skillDirectory);
      const cookie = await saveYouTubeCookies(request.cookies || []);
      await this.progress("credentials", `已保存 ${cookie.count} 个 Cookie。`, 4);

      let material = request.resume
        ? await findReusableArtifacts(this.options.vaultBasePath, settings.outputFolder, request.url)
        : null;
      if (!material) {
        await this.progress("materials", "正在提取元数据、字幕、SRT 和封面。", 12);
        const args = [
          request.url,
          "--vault", this.options.vaultBasePath,
          "--output-dir", settings.outputFolder,
          "--cookies", cookie.path,
          "--deadline", String(Math.min(300, this.remainingSeconds())),
        ];
        if (request.allow_asr) args.push("--allow-asr");
        if (request.browser_transcript?.status === "ok") {
          const inputDir = resolveInsideVault(this.options.vaultBasePath, `${settings.outputFolder}/assets/${videoId(request.url)}`);
          await fs.mkdir(inputDir, { recursive: true });
          const inputPath = path.join(inputDir, "browser-transcript.json");
          await fs.writeFile(inputPath, JSON.stringify(request.browser_transcript), "utf8");
          args.push("--browser-transcript", inputPath);
        }
        const result = await this.runScript(settings, "extract_transcript.py", args, signal, 300_000);
        const pipeline = requireSuccess(result);
        material = {
          note: requiredString(pipeline.note, "RESULT_NOTE_MISSING"),
          transcript: requiredString(pipeline.transcript, "RESULT_TRANSCRIPT_MISSING"),
        };
      } else {
        await this.progress("materials", "已复用上次任务的字幕、SRT 和草稿。", 24);
      }

      let manifestPath = material.manifest;
      let manifest: FrameManifest | null = null;
      if (manifestPath) {
        try {
          const candidate = await readFrameManifest(manifestPath);
          if (candidate.skill_version === skillVersion) manifest = candidate;
        } catch {
          manifestPath = undefined;
        }
      }
      const transcript = await fs.readFile(material.transcript, "utf8");
      if (!manifest) {
        await this.progress("planning", "正在根据字幕生成文章大纲和画面证据计划。", 28);
        const prompt = planningPrompt(transcript, contract);
        const plan = validateArticlePlan(await requestChatJson({
          ...prompt,
          apiBase: settings.apiBase,
          apiKey,
          model: settings.model,
          signal,
        }));
        const planDir = path.join(path.dirname(material.note), "assets", videoId(request.url));
        await fs.mkdir(planDir, { recursive: true });
        const planPath = path.join(planDir, `frame-plan-${request.request_id}.json`);
        await fs.writeFile(planPath, JSON.stringify(plan, null, 2), "utf8");

        await this.progress("frames", `正在定点抽取 ${plan.frames.length} 张关键画面。`, 35);
        const frameRun = await this.runScript(settings, "extract_frames.py", [
          request.url,
          "--plan", planPath,
          "--vault", this.options.vaultBasePath,
          "--output-dir", settings.outputFolder,
          "--note", material.note,
          "--cookies", cookie.path,
          "--deadline", String(Math.min(120, this.remainingSeconds())),
        ], signal, 120_000);
        const frameResult = requireSuccess(frameRun);
        manifestPath = requiredString(frameResult.manifest, "FRAME_MANIFEST_MISSING");
        manifest = await readFrameManifest(manifestPath);
      } else {
        await this.progress("frames", `已复用 ${manifest.frames.length} 张关键画面。`, 72);
      }

      await this.progress("writing", `已获得 ${manifest.frames.length} 张关键画面，正在撰写中文学习笔记。`, 76);
      const vaultFrames = manifest.frames.map((frame) => ({
        ...frame,
        path: vaultRelative(this.options.vaultBasePath, frame.path),
        obsidian_embed: `![[${vaultRelative(this.options.vaultBasePath, frame.path)}]]`,
      }));
      const draftSource = await fs.readFile(material.note, "utf8");
      const srtPath = vaultRelative(this.options.vaultBasePath, material.transcript);
      const stagingDir = resolveInsideVault(this.options.vaultBasePath, `${settings.outputFolder}/.reader-drafts/${videoId(request.url)}`);
      await fs.mkdir(stagingDir, { recursive: true });
      const checkpointPath = path.join(stagingDir, "writing.json");
      let cachedWriting;
      if (request.resume) {
        try {
          const cached = JSON.parse(await fs.readFile(checkpointPath, "utf8"));
          if (cached.manifest === manifestPath && cached.version === skillVersion && cached.model === settings.model) cachedWriting = cached.writing;
        } catch { /* No completed writing checkpoint yet. */ }
      }
      const prompt = writingPrompt(transcript, contract, manifest, vaultFrames);
      const writing = validateWritingResult(cachedWriting || await requestChatJson({
        ...prompt,
        apiBase: settings.apiBase,
        apiKey,
        model: settings.model,
        signal,
      }));
      await fs.writeFile(checkpointPath, JSON.stringify({ manifest: manifestPath, version: skillVersion, model: settings.model, writing }), "utf8");
      const safeFilename = sanitizeNoteFilename(writing.filename);
      finalTarget = resolveInsideVault(this.options.vaultBasePath, `${settings.outputFolder}/${safeFilename}`);
      priorFinal = await readIfExists(finalTarget);
      stagingNote = path.join(stagingDir, safeFilename);
      const finalSource = composeNote(draftSource, bindTranscript(writing.body, srtPath), {
        title: safeFilename.replace(/\.md$/i, ""),
        skill_version: skillVersion,
        quality_profile_version: "1",
      });
      await fs.writeFile(stagingNote, finalSource, "utf8");
      await this.setState({ draft_path: stagingNote, screenshot_dir: path.dirname(manifestPath!), screenshot_count: manifest.frames.length });

      await this.progress("validation", "正在校验结构、深度、中文比例、截图和 SRT 链接。", 92);
      let validation = await this.validate(settings, stagingNote, signal);
      if (isRepairableValidation(validation)) {
        await this.progress("validation", "首次校验未通过，正在进行唯一一次正文修正。", 94);
        const repair = repairPrompt(
          bodyOnly(await fs.readFile(stagingNote, "utf8")),
          validation.results.at(-1)?.errors,
          contract,
          manifest,
          vaultFrames,
          safeFilename,
        );
        const repaired = validateWritingResult(await requestChatJson({
          ...repair,
          apiBase: settings.apiBase,
          apiKey,
          model: settings.model,
          signal,
        }));
        const current = await fs.readFile(stagingNote, "utf8");
        await fs.writeFile(stagingNote, composeNote(current, bindTranscript(repaired.body, srtPath), {}), "utf8");
        await fs.writeFile(checkpointPath, JSON.stringify({ manifest: manifestPath, version: skillVersion, model: settings.model, writing: repaired }), "utf8");
        validation = await this.validate(settings, stagingNote, signal);
      }
      requireSuccess(validation);
      await fs.copyFile(stagingNote, finalTarget);
      published = true;
      let noteOpened = false;
      if (settings.autoOpenNote) {
        try { await this.options.openNote(finalTarget); noteOpened = true; } catch { /* The validated file is still complete. */ }
      }
      if (path.basename(material.note).startsWith("待命名 -") && material.note !== finalTarget
          && frontmatterValue(draftSource, "url") === request.url) {
        await fs.unlink(material.note).catch(() => {});
      }
      await this.setState({
        type: "complete",
        status: "ok",
        stage: "complete",
        message: "学习笔记已生成并通过校验。",
        progress_percent: 100,
        note_path: finalTarget,
        screenshot_dir: path.dirname(manifestPath!),
        screenshot_count: manifest.frames.length,
        note_opened: noteOpened,
        can_resume: false,
      });
    } catch (error) {
      if (published && finalTarget) await restoreFile(finalTarget, priorFinal);
      if (this.state.status === "cancelled") return;
      if (this.state.code === "TASK_INTERRUPTED") return;
      if (error instanceof PipelineFailure) {
        const failure = mapPipelineFailure(error.result);
        await this.setState({
          type: "error",
          status: "error",
          stage: normalizeStage(String(failure.stage || this.state.stage)),
          code: failure.code,
          message: failure.message,
          can_retry_asr: failure.can_retry_asr,
          can_resume: true,
          result_stage: failure.stage,
          validation_errors: error.result.errors,
          draft_path: stagingNote || undefined,
        });
      } else {
        const failedStage = this.state.stage;
        const message = this.timedOut
          ? "任务超过 8 分钟硬时限，已停止。"
          : String((error as Error).message || error);
        await this.setState({
          type: "error",
          status: "error",
          stage: failedStage,
          code: this.timedOut ? "PIPELINE_TIMEOUT" : errorCode(message),
          message,
          can_resume: true,
          result_stage: failedStage,
          technical_message: message,
        });
      }
    } finally {
      clearTimeout(hardTimeout);
      clearInterval(heartbeat);
    }
  }

  private async validate(settings: YouTubeReaderSettings, note: string, signal: AbortSignal): Promise<ScriptRunResult> {
    return this.runScript(settings, "validate_note.py", [
      note,
      "--vault", this.options.vaultBasePath,
    ], signal, Math.min(60_000, this.remainingMs()));
  }

  private async runScript(
    settings: YouTubeReaderSettings,
    filename: string,
    args: string[],
    signal: AbortSignal,
    maximumMs: number,
  ): Promise<ScriptRunResult> {
    const result = await this.runner.run(
      settings.pythonExecutable,
      path.join(settings.skillDirectory, "scripts", filename),
      args,
      {
        cwd: settings.skillDirectory,
        signal,
        timeoutMs: Math.max(1_000, Math.min(maximumMs, this.remainingMs())),
        onProgress: async (progress) => {
          const stage = normalizeStage(String(progress.stage || this.state.stage));
          await this.progress(
            stage,
            String(progress.message || this.state.message),
            Math.max(STAGE_PERCENT[stage] || 0, Number(progress.percent) || 0),
          );
        },
      },
    );
    if (result.timedOut) throw new Error(timeoutErrorForScript(filename));
    return result;
  }

  private async heartbeat(): Promise<void> {
    if (this.state.status !== "running") return;
    await this.setState({ elapsed_seconds: Math.floor((Date.now() - this.startedAt) / 1000) });
  }

  private async progress(stage: string, message: string, percent: number): Promise<void> {
    await this.setState({
      type: "progress",
      status: "running",
      stage: normalizeStage(stage),
      message,
      progress_percent: Math.max(this.state.progress_percent, Math.min(99, Math.round(percent))),
      elapsed_seconds: Math.floor((Date.now() - this.startedAt) / 1000),
    });
  }

  private async setState(patch: Partial<JobState>): Promise<void> {
    this.state = { ...this.state, ...patch };
    await this.options.persist(this.state);
  }

  private remainingMs(): number {
    return Math.max(1_000, MAX_JOB_MS - (Date.now() - this.startedAt));
  }

  private remainingSeconds(): number {
    return Math.max(30, Math.floor(this.remainingMs() / 1000));
  }
}

function requireSuccess(run: ScriptRunResult): PipelineResult {
  const last = run.results.at(-1);
  if (last?.status === "error") throw new PipelineFailure(last);
  if (run.exitCode !== 0 || !last || last.status !== "ok") {
    throw new Error(`SCRIPT_FAILED: Skill 脚本退出码 ${run.exitCode}，且没有成功的 PIPELINE_RESULT。`);
  }
  return last;
}

function isRepairableValidation(run: ScriptRunResult): boolean {
  const result = run.results.at(-1);
  return result?.status === "error" && result.code === "NOTE_VALIDATION_FAILED";
}

function composeNote(source: string, body: string, updates: Record<string, string>): string {
  const updated = updateFrontmatter(source, updates);
  const frontmatter = splitFrontmatter(updated).frontmatter;
  return `---\n${frontmatter}\n---\n${body.trim()}\n`;
}

async function assertSkillScripts(skill: string): Promise<void> {
  for (const relative of [
    "VERSION",
    "references/note-contract.md",
    "scripts/extract_transcript.py",
    "scripts/extract_frames.py",
    "scripts/validate_note.py",
  ]) {
    try {
      await fs.access(path.join(skill, ...relative.split("/")));
    } catch {
      throw new Error(`SKILL_FILE_MISSING: 找不到 ${relative}，请检查 Skill 目录。`);
    }
  }
}

function hasApiKey(app: App): boolean {
  try {
    return Boolean(app.secretStorage?.getSecret("youtube-note-reader-api-key"));
  } catch {
    return false;
  }
}

function normalizeStage(value: string): PipelineStage {
  if (value === "frame_plan") return "planning";
  if (value === "note_validation") return "validation";
  if (["credentials", "materials", "planning", "frames", "writing", "validation", "complete", "failed", "cancelled", "idle"].includes(value)) {
    return value as PipelineStage;
  }
  return "materials";
}

function isSupportedVideoUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "bilibili.com", "www.bilibili.com", "b23.tv"].includes(parsed.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function videoId(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "youtu.be") return parsed.pathname.slice(1).replace(/[^A-Za-z0-9_-]/g, "") || "video";
    return (parsed.searchParams.get("v") || parsed.pathname.split("/").filter(Boolean).at(-1) || "video")
      .replace(/[^A-Za-z0-9_-]/g, "") || "video";
  } catch {
    return "video";
  }
}

function requiredString(value: unknown, code: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${code}: Skill 没有返回必需文件路径。`);
  return value;
}

function errorCode(message: string): string {
  return message.match(/^([A-Z][A-Z0-9_]+):/)?.[1] || "PIPELINE_FAILED";
}

async function readIfExists(target: string): Promise<string | null> {
  try {
    return await fs.readFile(target, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
}

async function restoreFile(target: string, source: string | null): Promise<void> {
  if (source === null) await fs.rm(target, { force: true }).catch(() => undefined);
  else await fs.writeFile(target, source, "utf8");
}

function idleState(): JobState {
  return {
    type: "status",
    request_id: "",
    status: "idle",
    stage: "idle",
    message: "准备就绪。",
    elapsed_seconds: 0,
    progress_percent: 0,
  };
}
