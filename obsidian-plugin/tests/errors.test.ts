import assert from "node:assert/strict";
import test from "node:test";

import { mapPipelineFailure } from "../src/errors";

for (const code of [
  "SUBTITLE_UNAVAILABLE",
  "SUBTITLE_DOWNLOAD_FAILED",
  "SUBTITLE_PARSE_FAILED",
]) {
  test(`${code} preserves the extractor message and offers explicit ASR retry`, () => {
    const failure = mapPipelineFailure({
      status: "error",
      code,
      message: `${code} 的原始中文消息`,
      stage: "transcript",
    });

    assert.equal(failure.code, code);
    assert.equal(failure.message, `${code} 的原始中文消息`);
    assert.equal(failure.can_retry_asr, true);
    assert.equal(failure.auto_retry, false);
  });
}

test("other failures do not advertise an ASR retry", () => {
  const failure = mapPipelineFailure({
    status: "error",
    code: "COOKIE_REJECTED",
    message: "Cookie 已失效",
  });

  assert.equal(failure.can_retry_asr, false);
  assert.equal(failure.auto_retry, false);
});
