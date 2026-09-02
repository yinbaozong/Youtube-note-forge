const connectionNode = document.getElementById("connection");
const connectionCard = document.getElementById("connectionCard");
const indicatorNode = document.getElementById("indicator");
const modelNode = document.getElementById("model");
const vaultNode = document.getElementById("vault");
const pluginVersionNode = document.getElementById("pluginVersion");
const apiKeyStatusNode = document.getElementById("apiKeyStatus");
const statusNode = document.getElementById("status");
const openSettings = document.getElementById("openSettings");
const validateApiKey = document.getElementById("validateApiKey");
const retry = document.getElementById("retry");

function showDisconnected(message) {
  connectionCard.className = "connection-card disconnected";
  indicatorNode.className = "error";
  connectionNode.textContent = "未连接";
  modelNode.textContent = "不可用";
  vaultNode.textContent = "不可用";
  pluginVersionNode.textContent = "不可用";
  apiKeyStatusNode.textContent = "无法检测";
  apiKeyStatusNode.className = "value-error";
  statusNode.className = "status-error";
  statusNode.textContent = `未连接：${message || "请先打开 Obsidian，并启用 YouTube Note Reader 插件。"}`;
  openSettings.disabled = true;
  validateApiKey.disabled = true;
}

async function load() {
  connectionCard.className = "connection-card checking";
  retry.disabled = true;
  statusNode.className = "";
  statusNode.textContent = "正在检测 Obsidian 插件…";
  const response = await chrome.runtime.sendMessage({ type: "get_settings" });
  retry.disabled = false;
  if (response?.type === "error" || !response?.connected) {
    showDisconnected(response?.message);
    return;
  }
  connectionCard.className = "connection-card connected";
  indicatorNode.className = "ok";
  connectionNode.textContent = "已连接 Obsidian 插件";
  modelNode.textContent = response.model || "未配置";
  vaultNode.textContent = response.vault || "未配置";
  pluginVersionNode.textContent = response.plugin_version || "未知";
  apiKeyStatusNode.textContent = response.api_key_configured ? "已保存" : "未配置";
  apiKeyStatusNode.className = response.api_key_configured ? "value-ok" : "value-error";
  statusNode.className = response.api_key_configured ? "status-ok" : "status-warning";
  statusNode.textContent = response.api_key_configured
    ? "连接正常。可在 Obsidian 设置中使用“验证 API Key”确认模型服务可用。"
    : "已连接 Obsidian，但尚未保存 API Key。请打开 Obsidian 设置完成配置。";
  openSettings.disabled = false;
  validateApiKey.disabled = !response.api_key_configured;
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

validateApiKey.addEventListener("click", async () => {
  validateApiKey.disabled = true;
  validateApiKey.textContent = "验证中…";
  statusNode.className = "";
  statusNode.textContent = "正在通过 Obsidian 验证 API Key 和当前模型…";
  const response = await chrome.runtime.sendMessage({ type: "validate_api_key" });
  validateApiKey.textContent = "验证 API Key";
  validateApiKey.disabled = false;
  if (response?.type === "error") {
    statusNode.className = "status-error";
    statusNode.textContent = response.message || "API Key 验证失败。";
    return;
  }
  statusNode.className = "status-ok";
  statusNode.textContent = response?.message || "API Key 与当前模型可用。";
});

retry.addEventListener("click", load);
load();
