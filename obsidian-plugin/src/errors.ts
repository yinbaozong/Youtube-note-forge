import type { PipelineResult, PublicFailure } from "./types";

const ASR_RETRY_CODES = new Set([
  "SUBTITLE_UNAVAILABLE",
  "SUBTITLE_DOWNLOAD_FAILED",
  "SUBTITLE_PARSE_FAILED",
]);

export function mapPipelineFailure(result: PipelineResult): PublicFailure {
  const code = String(result.code || "PIPELINE_FAILED");
  return {
    status: "error",
    code,
    message: [
      String(result.message || (code === "NOTE_VALIDATION_FAILED" ? "笔记校验未通过，已保留草稿。" : code)),
      ...(result.errors || []).slice(0, 8).map((item) => `${item.code}: ${item.message || ""}`),
      result.action,
    ].filter(Boolean).join("\n"),
    can_retry_asr: ASR_RETRY_CODES.has(code),
    auto_retry: false,
    stage: result.stage ? String(result.stage) : undefined,
  };
}

export function isSubtitleFailure(code: string): boolean {
  return ASR_RETRY_CODES.has(code);
}
