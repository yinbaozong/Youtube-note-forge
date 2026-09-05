import assert from "node:assert/strict";
import test from "node:test";
import { filterHistory, updateHistory } from "../src/task-history";
import type { JobState } from "../src/job-manager";

const state = (patch: Partial<JobState>): JobState => ({ type: "progress", request_id: "a", status: "running", stage: "writing", message: "", elapsed_seconds: 1, progress_percent: 76, ...patch });

test("history updates one task id instead of duplicating it", () => {
  const first = updateHistory([], state({ video_title: "A" }), 100);
  const done = updateHistory(first, state({ status: "ok", stage: "complete", progress_percent: 100, topic: "3D打印" }), 200);
  assert.equal(done.length, 1);
  assert.equal(done[0].topic, "3D打印");
  assert.equal(done[0].createdAt, 100);
});

test("history filters by query topic and recent period", () => {
  const history = [{ id: "a", title: "打印机升级", topic: "3D打印", videoUrl: "", notePath: "", status: "ok" as const, stage: "complete" as const, progress: 100, createdAt: 1, updatedAt: 900 }];
  assert.equal(filterHistory(history, "升级", "week", "3D打印", 1000).length, 1);
  assert.equal(filterHistory(history, "机器人", "all", "", 1000).length, 0);
});
