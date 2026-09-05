const statusNode = document.getElementById("status");
const stageNode = document.getElementById("stage");
const taskInfo = document.getElementById("taskInfo");
const resultNode = document.getElementById("result");
const asrNotice = document.getElementById("asrNotice");
const indicator = document.getElementById("indicator");
const progress = document.getElementById("progress");
const progressText = document.getElementById("progressText");
const timeline = document.getElementById("timeline");
const start = document.getElementById("start");
const resume = document.getElementById("resume");
const retryAsr = document.getElementById("retryAsr");
const cancel = document.getElementById("cancel");
const openNote = document.getElementById("openNote");
const copyPath = document.getElementById("copyPath");
const copyPhotosPath = document.getElementById("copyPhotosPath");
const retryConnection = document.getElementById("retryConnection");
const { isConnectionError, shouldOfferAsr } = YouTubeReaderProtocol;
let currentNotePath = "";
let currentPhotosPath = "";
let currentVideoUrl = "";
let currentVideoTitle = "";

const STAGE_LABELS = {
  idle: "准备",
  queued: "任务排队",
  credentials: "同步 Cookie",
  starting: "启动 Obsidian 处理",
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
  const canRetryAsr = shouldOfferAsr(state);
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
  resume.hidden = !(state.status === "error" && state.can_resume === true && state.video_url);
  retryAsr.hidden = !canRetryAsr;
  retryAsr.disabled = !state.video_url;
  asrNotice.hidden = !canRetryAsr;
  cancel.hidden = !(running || state.status === "error");
  cancel.textContent = running ? "强制停止" : "清除任务";
  const taskLines = [];
  if (state.video_title) taskLines.push("正在处理：" + state.video_title);
  if (state.video_url) taskLines.push("视频位置：" + state.video_url);
  if (state.output_dir) taskLines.push("预计保存位置：" + state.output_dir);
  if (state.draft_path && state.status === "error") taskLines.push("已保留正文草稿：" + state.draft_path);
  if (state.allow_asr && running) taskLines.push("字幕方式：ASR 语音识别（需要下载音频）");
  taskInfo.hidden = taskLines.length === 0;
  taskInfo.textContent = taskLines.join("\n");
  resultNode.hidden = !state.note_path;
  openNote.hidden = !state.note_path;
  copyPath.hidden = !state.note_path;
  copyPhotosPath.hidden = !state.screenshot_dir;
  retryConnection.hidden = !(isConnectionError(state)
    || (state.status === "error" && /Obsidian|127\.0\.0\.1:32191/.test(state.message || "")));
  currentNotePath = state.note_path || "";
  currentPhotosPath = state.screenshot_dir || "";
  currentVideoUrl = state.video_url || "";
  currentVideoTitle = state.video_title || "";
  resultNode.textContent = state.note_path
    ? "文件位置：\n" + state.note_path
      + (state.screenshot_dir ? "\n\n照片位置：\n" + state.screenshot_dir : "")
      + "\n\n截图：" + (state.screenshot_count || 0) + " 张"
      + " · 总耗时：" + formatDuration(state.elapsed_seconds)
    : "";
}

async function refresh() {
  render(await send({ type: "get_status" }));
}

start.addEventListener("click", async () => {
  start.disabled = true;
  statusNode.textContent = "正在启动当前视频…";
  render(await send({ type: "start_job", resume: false, allow_asr: false }));
});

resume.addEventListener("click", async () => {
  resume.disabled = true;
  try {
    render(await send({ type: "resume_job" }));
  } finally {
    resume.disabled = false;
  }
});

retryAsr.addEventListener("click", async () => {
  retryAsr.disabled = true;
  statusNode.textContent = "已允许 ASR，正在重新开始任务…";
  try {
    render(await send({
      type: "start_job",
      url: currentVideoUrl,
      title: currentVideoTitle,
      resume: true,
      allow_asr: true
    }));
  } finally {
    retryAsr.disabled = false;
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
copyPhotosPath.addEventListener("click", async () => {
  if (!currentPhotosPath) return;
  await navigator.clipboard.writeText(currentPhotosPath);
  copyPhotosPath.textContent = "已复制";
  setTimeout(() => { copyPhotosPath.textContent = "复制照片路径"; }, 1500);
});
retryConnection.addEventListener("click", async () => {
  retryConnection.disabled = true;
  statusNode.textContent = "正在重新连接 Obsidian 插件…";
  const nextState = await send({ type: "retry_connection" });
  retryConnection.disabled = false;
  render(nextState);
});
document.getElementById("settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.getElementById("settingsFooter").addEventListener("click", () => chrome.runtime.openOptionsPage());
setInterval(refresh, 1000);
refresh();
