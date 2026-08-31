const modelNode = document.getElementById("model");
const modelOptionsNode = document.getElementById("modelOptions");
const apiKeyNode = document.getElementById("apiKey");
const vaultNode = document.getElementById("vault");
const autoOpenNoteNode = document.getElementById("autoOpenNote");
const statusNode = document.getElementById("status");

const RECOMMENDED_MODEL = "deepseek/deepseek-v4-pro";

async function loadModels(savedModel = "") {
  statusNode.textContent = "正在读取 OpenCode 模型…";
  modelOptionsNode.replaceChildren();
  const response = await chrome.runtime.sendMessage({ type: "list_models" });
  if (response?.type === "error") {
    statusNode.textContent = "本机组件未连接：" + response.message;
    modelNode.placeholder = "可手动输入 provider/model，修复连接后再验证";
    return false;
  }
  const models = response.models || [];
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model;
    modelOptionsNode.appendChild(option);
  }
  if (!modelNode.value) {
    modelNode.value = savedModel || (models.includes(RECOMMENDED_MODEL) ? RECOMMENDED_MODEL : models[0] || "");
  }
  statusNode.textContent = models.length
    ? `已读取 ${models.length} 个模型，可搜索或直接输入`
    : "OpenCode 没有返回可用模型，可手动输入 provider/model";
  return true;
}

async function load() {
  const settings = await chrome.storage.local.get({
    model: "",
    vault: "",
    auto_open_note: true
  });
  let vault = settings.vault;
  if (!vault) {
    const environment = await chrome.runtime.sendMessage({ type: "get_environment" });
    if (environment?.default_vault) {
      vault = environment.default_vault;
      await chrome.storage.local.set({ vault });
    }
  }
  vaultNode.value = vault;
  autoOpenNoteNode.checked = settings.auto_open_note !== false;
  modelNode.value = settings.model;
  await loadModels(settings.model);
}

document.getElementById("save").addEventListener("click", async () => {
  const model = modelNode.value;
  if (!model) {
    statusNode.textContent = "请选择模型";
    return;
  }
  statusNode.textContent = "正在保存…";
  const response = await chrome.runtime.sendMessage({
    type: "configure",
    model,
    api_key: apiKeyNode.value
  });
  if (response?.type === "error") {
    statusNode.textContent = response.message;
    return;
  }
  await chrome.storage.local.set({
    model,
    vault: vaultNode.value.trim(),
    auto_open_note: autoOpenNoteNode.checked
  });
  apiKeyNode.value = "";
  statusNode.textContent = "已固定使用 " + model;
});

document.getElementById("retry").addEventListener("click", async () => {
  await loadModels(modelNode.value.trim());
});

load();
