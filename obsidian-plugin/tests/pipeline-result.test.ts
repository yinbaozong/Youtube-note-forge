import assert from "node:assert/strict";
import test from "node:test";

import { parsePipelineResults } from "../src/pipeline-result";

test("parses every valid PIPELINE_RESULT and ignores noise", () => {
  const output = [
    "Downloading subtitles...",
    'PIPELINE_RESULT={"status":"ok","stage":"materials","note":"C:\\\\Vault\\\\draft.md"}',
    "PIPELINE_RESULT=not-json",
    'prefix PIPELINE_RESULT={"status":"error","code":"SUBTITLE_UNAVAILABLE","message":"没有可用字幕"}',
  ].join("\n");

  assert.deepEqual(parsePipelineResults(output), [
    { status: "ok", stage: "materials", note: "C:\\Vault\\draft.md" },
    { status: "error", code: "SUBTITLE_UNAVAILABLE", message: "没有可用字幕" },
  ]);
});
