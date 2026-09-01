import { promises as fs } from "node:fs";
import path from "node:path";

import { frontmatterValue, splitFrontmatter } from "./note-utils";

export interface ArticleOutlineItem {
  section_id: string;
  title: string;
  start: number;
  end: number;
  core_claims: string[];
  learning_goal: string;
}

export interface FramePlanItem {
  section_id: string;
  timestamp: number;
  purpose: string;
  required: boolean;
}

export interface ArticlePlan {
  article_outline: ArticleOutlineItem[];
  frames: FramePlanItem[];
}

export interface FrameManifest {
  status: string;
  skill_version: string;
  video_id: string;
  article_outline: ArticleOutlineItem[];
  frames: Array<FramePlanItem & { path: string; method?: string }>;
  [key: string]: unknown;
}

export interface WritingResult {
  filename: string;
  body: string;
}

export async function readSkillVersion(skillDirectory: string): Promise<string> {
  return (await fs.readFile(path.join(skillDirectory, "VERSION"), "utf8")).trim();
}

export async function readNoteContract(skillDirectory: string): Promise<string> {
  return fs.readFile(path.join(skillDirectory, "references", "note-contract.md"), "utf8");
}

export function validateArticlePlan(value: unknown): ArticlePlan {
  if (!value || typeof value !== "object") throw new Error("FRAME_PLAN_INVALID: 画面计划不是 JSON 对象。");
  const plan = value as Partial<ArticlePlan>;
  if (!Array.isArray(plan.article_outline) || plan.article_outline.length < 3 || plan.article_outline.length > 8) {
    throw new Error("FRAME_PLAN_INVALID: article_outline 必须包含 3-8 个章节。");
  }
  if (!Array.isArray(plan.frames) || !plan.frames.length || plan.frames.length > 24) {
    throw new Error("FRAME_PLAN_INVALID: frames 必须包含 1-24 个画面点。");
  }
  const sectionIds = new Set<string>();
  for (const item of plan.article_outline) {
    if (!item || typeof item !== "object") throw new Error("FRAME_PLAN_INVALID: 章节格式错误。");
    if (!String(item.section_id || "").trim() || !String(item.title || "").trim()) {
      throw new Error("FRAME_PLAN_INVALID: 章节缺少 section_id 或 title。");
    }
    sectionIds.add(String(item.section_id));
  }
  for (const frame of plan.frames) {
    if (!frame || typeof frame !== "object"
      || !sectionIds.has(String(frame.section_id))
      || !Number.isFinite(Number(frame.timestamp))
      || Number(frame.timestamp) < 0
      || !String(frame.purpose || "").trim()) {
      throw new Error("FRAME_PLAN_INVALID: 截图必须关联已有章节，并包含秒数时间点和中文用途。");
    }
    frame.timestamp = Number(frame.timestamp);
    frame.required = Boolean(frame.required);
  }
  return plan as ArticlePlan;
}

export function validateWritingResult(value: unknown): WritingResult {
  if (!value || typeof value !== "object") throw new Error("NOTE_RESPONSE_INVALID: 写作结果不是 JSON 对象。");
  const result = value as Partial<WritingResult>;
  if (!String(result.filename || "").trim() || !String(result.body || "").trim()) {
    throw new Error("NOTE_RESPONSE_INVALID: 写作结果必须包含 filename 和 body。");
  }
  const body = String(result.body).trim();
  if (!body.startsWith("## 一句话摘要")) {
    throw new Error("NOTE_RESPONSE_INVALID: 正文必须从“## 一句话摘要”开始。");
  }
  return { filename: String(result.filename).trim(), body };
}

export async function findReusableArtifacts(
  vault: string,
  outputFolder: string,
  url: string,
): Promise<{ note: string; transcript: string; manifest?: string } | null> {
  const output = path.resolve(vault, outputFolder);
  let entries: Array<{ name: string; isFile(): boolean }>;
  try {
    entries = await fs.readdir(output, { withFileTypes: true });
  } catch {
    return null;
  }
  const candidates: Array<{ note: string; transcript: string; manifest?: string; modified: number }> = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.toLowerCase().endsWith(".md")) continue;
    const note = path.join(output, entry.name);
    try {
      const source = await fs.readFile(note, "utf8");
      if (frontmatterValue(source, "url") !== url) continue;
      const transcript = resolveVaultLink(vault, frontmatterValue(source, "transcript_file"));
      if (!transcript || !(await exists(transcript))) continue;
      const manifest = resolveVaultLink(vault, frontmatterValue(source, "frame_manifest"));
      const stat = await fs.stat(note);
      candidates.push({
        note,
        transcript,
        manifest: manifest && await exists(manifest) ? manifest : undefined,
        modified: stat.mtimeMs,
      });
    } catch {
      continue;
    }
  }
  candidates.sort((a, b) => b.modified - a.modified);
  return candidates[0] || null;
}

export async function readFrameManifest(manifestPath: string): Promise<FrameManifest> {
  const value = JSON.parse(await fs.readFile(manifestPath, "utf8")) as FrameManifest;
  if (value.status !== "ok" || !Array.isArray(value.frames) || !value.frames.length) {
    throw new Error("FRAME_MANIFEST_INVALID: 现有抽帧清单不可复用。");
  }
  return value;
}

export function resolveVaultLink(vault: string, raw: string): string {
  const clean = raw.trim().replace(/^\[\[|\]\]$/g, "").split("|")[0];
  if (!clean) return "";
  const resolved = path.resolve(vault, clean.replaceAll("/", path.sep));
  const relative = path.relative(path.resolve(vault), resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return "";
  return resolved;
}

export function vaultRelative(vault: string, absolute: string): string {
  const relative = path.relative(path.resolve(vault), path.resolve(absolute));
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("PATH_OUTSIDE_VAULT: 素材路径不在当前 Vault 内。");
  }
  return relative.split(path.sep).join("/");
}

export function bodyOnly(source: string): string {
  return splitFrontmatter(source).body.trim();
}

async function exists(target: string): Promise<boolean> {
  try {
    await fs.access(target);
    return true;
  } catch {
    return false;
  }
}
