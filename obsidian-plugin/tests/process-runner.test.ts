import assert from "node:assert/strict";
import test from "node:test";

import { timeoutErrorForScript } from "../src/process-runner";

test("script timeouts keep a stage-specific error code", () => {
  assert.match(timeoutErrorForScript("extract_transcript.py"), /^MATERIALS_TIMEOUT:/);
  assert.match(timeoutErrorForScript("extract_frames.py"), /^FRAME_TIMEOUT:/);
  assert.match(timeoutErrorForScript("validate_note.py"), /^NOTE_VALIDATION_TIMEOUT:/);
});
