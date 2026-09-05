import assert from "node:assert/strict";
import test from "node:test";
import { bindTranscript } from "../src/note-utils";
import { mapPipelineFailure } from "../src/errors";

test("SRT binding replaces invented paths and preserves other content", () => {
  const body = "## 一句话摘要\n解释\n\n## 原始字幕 Transcript\n[[wrong.srt]]\n\n## 附录\n保留\n";
  const next = bindTranscript(body, "YouTube video/transcripts/真实字幕.srt");
  assert.ok(next.includes("[[YouTube video/transcripts/真实字幕.srt]]"));
  assert.ok(!next.includes("wrong.srt"));
  assert.ok(next.includes("## 附录\n保留"));
  assert.equal(bindTranscript(next, "YouTube video/transcripts/真实字幕.srt"), next);
});

test("validation diagnostics reach the popup instead of a bare error code", () => {
  const mapped = mapPipelineFailure({ code: "NOTE_VALIDATION_FAILED", errors: [
    { code: "DETAIL_CONTENT_SHALLOW", message: "需要 900 字" },
  ] });
  assert.match(mapped.message, /需要 900 字/);
  assert.equal(mapped.can_retry_asr, false);
});
