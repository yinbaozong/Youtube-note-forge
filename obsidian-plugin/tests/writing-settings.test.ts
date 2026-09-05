import assert from "node:assert/strict";
import test from "node:test";
import { writingPrompt } from "../src/prompts";

test("custom writing and separately labelled AI extension reach the writer", () => {
  const prompt = writingPrompt("字幕", "契约", { status: "ok", skill_version: "x", video_id: "v", article_outline: [], frames: [] }, [], {
    writingStyle: "detailed", customWritingInstructions: "补充材料选择建议", allowAiExtensions: true,
  });
  assert.match(prompt.user, /详细教程/);
  assert.match(prompt.user, /延伸解读（AI补充）/);
  assert.match(prompt.user, /补充材料选择建议/);
});
