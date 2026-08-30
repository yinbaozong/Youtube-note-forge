const statusNode = document.getElementById("status");
const stageNode = document.getElementById("stage");
const resultNode = document.getElementById("result");
const indicator = document.getElementById("indicator");
const progress = document.getElementById("progress");
const progressText = document.getElementById("progressText");
const timeline = document.getElementById("timeline");
const start = document.getElementById("start");
const cancel = document.getElementById("cancel");
const openNote = document.getElementById("openNote");
const copyPath = document.getElementById("copyPath");
let currentNotePath = "";

const STAGE_LABELS = {
  idle: "准备",
  queued: "任务排队",
  credentials: "同步 Cookie",
  starting: "启动 OpenCode",
  materials: "提取字幕与素材",
  planning: "规划文章与截图",
  frames: "定点抽帧",
  writing: "撰写学习笔记",
  validation: "质量校验",
  complete: "已完成",
  failed: "失败",
  cancelled: "已停止"
};

const STAGE_ORDER = [
  "queued",
  "credentials",
  "starting",
  "materials",
  "planning",
  "frames",
  "writing",
  "validation",
  "complete"
];

function stageRank(stage) {
  const index = STAGE_ORDER.indexOf(stage);
  return index < 0 ? 0 : index;
}

function renderTimeline(stage, status) {
  const rank = stageRank(stage);
  for (const item of timeline.querySelectorAll("li")) {
    const itemRank = stageRank(item.dataset.stage);
    item.className = itemRank < rank ? "done" : itemRank === rank ? "current" : "pending";
    if (status === "error" && itemRank === rank) item.className = "failed";
  }
}

function formatDuration(rawSeconds) {
  const seconds = Math.max(0, Number(rawSeconds) || 0);
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function render(state) {
  const running = state.status === "running";
  statusNode.textContent = state.message || "准备就绪";
  const stage = state.stage || "idle";
  const countText = state.total ? ` · ${state.current || 0}/${state.total} 张` : "";
  stageNode.textContent = (STAGE_LABELS[stage] || stage) + countText + " · " + (state.elapsed_seconds || 0) + "s";
  indicator.className = state.status || "idle";
  const percent = Math.max(0, Math.min(100, Number(state.progress_percent) || (state.status === "ok" ? 100 : 0)));
  progress.value = percent;
  progressText.textContent = percent + "%";
  renderTimeline(stage, state.status);
  start.disabled = running;
  cancel.hidden = !running;
  resultNode.hidden = !state.note_path;
  openNote.hidden = !state.note_path;
  copyPath.hidden = !state.note_path;
  currentNotePath = state.note_path || "";
  resultNode.textContent = state.note_path
    ? "文件位置：\n" + state.note_path
      + "\n\n截图：" + (state.screenshot_count || 0) + " 张"
      + " · 总耗时：" + formatDuration(state.elapsed_seconds)
    : "";
}

async function refresh() {
  render(await send({ type: "get_status" }));
}

start.addEventListener("click", async () => {
  const response = await send({ type: "start_job" });
  if (response?.type === "error") {
    render({ status: "error", message: response.message, stage: "credentials", elapsed_seconds: 0, progress_percent: 0 });
  } else {
    render(response);
  }
});

cancel.addEventListener("click", async () => render(await send({ type: "cancel_job" })));
openNote.addEventListener("click", async () => {
  if (currentNotePath) await send({ type: "open_note", note_path: currentNotePath });
});
copyPath.addEventListener("click", async () => {
  if (!currentNotePath) return;
  await navigator.clipboard.writeText(currentNotePath);
  copyPath.textContent = "已复制";
  setTimeout(() => { copyPath.textContent = "复制路径"; }, 1500);
});
document.getElementById("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("settingsFooter").addEventListener("click", () => chrome.runtime.openOptionsPage());
setInterval(refresh, 1000);
refresh();
