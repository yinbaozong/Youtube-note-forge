const connectionNode = document.getElementById("connection");
const indicatorNode = document.getElementById("indicator");
const modelNode = document.getElementById("model");
const vaultNode = document.getElementById("vault");
const pluginVersionNode = document.getElementById("pluginVersion");
const statusNode = document.getElementById("status");
const openSettings = document.getElementById("openSettings");
const retry = document.getElementById("retry");

function showDisconnected(message) {
  indicatorNode.className = "error";
  connectionNode.textContent = "未连接";
  modelNode.textContent = "不可用";
  vaultNode.textContent = "不可用";
  pluginVersionNode.textContent = "不可用";
  statusNode.textContent = message || "请先打开 Obsidian，并启用 YouTube Note Reader 插件。";
  openSettings.disabled = true;
}

async function load() {
  retry.disabled = true;
  statusNode.textContent = "正在检测 Obsidian 插件…";
  const response = await chrome.runtime.sendMessage({ type: "get_settings" });
  retry.disabled = false;
  if (response?.type === "error" || !response?.connected) {
    showDisconnected(response?.message);
    return;
  }
  indicatorNode.className = "ok";
  connectionNode.textContent = "已连接 Obsidian 插件";
  modelNode.textContent = response.model || "未配置";
  vaultNode.textContent = response.vault || "未配置";
  pluginVersionNode.textContent = response.plugin_version || "未知";
  statusNode.textContent = "配置只在 Obsidian 中修改，Chrome 不保存模型、API Key 或 Vault。";
  openSettings.disabled = false;
}

openSettings.addEventListener("click", async () => {
  openSettings.disabled = true;
  statusNode.textContent = "正在打开 Obsidian 插件设置…";
  const response = await chrome.runtime.sendMessage({ type: "open_obsidian_settings" });
  openSettings.disabled = false;
  statusNode.textContent = response?.type === "error"
    ? response.message
    : "已请求 Obsidian 打开 YouTube Note Reader 设置。";
});

retry.addEventListener("click", load);
load();
