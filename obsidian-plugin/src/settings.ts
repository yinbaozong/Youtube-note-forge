import {
  App,
  Notice,
  PluginSettingTab,
  Setting,
  requireApiVersion,
} from "obsidian";

export const API_KEY_SECRET_ID = "youtube-note-reader-api-key";

export interface YouTubeReaderSettings {
  apiBase: string;
  model: string;
  pythonExecutable: string;
  skillDirectory: string;
  outputFolder: string;
  autoOpenNote: boolean;
  writingStyle: "standard" | "detailed" | "plain";
  customWritingInstructions: string;
  allowAiExtensions: boolean;
}

export function defaultSettings(vaultBasePath: string): YouTubeReaderSettings {
  return {
    apiBase: "https://api.openai.com/v1",
    model: "gpt-5-mini",
    pythonExecutable: "python",
    skillDirectory: `${vaultBasePath}\\.obsidian\\skills\\youtube-transcript`,
    outputFolder: "YouTube video",
    autoOpenNote: true,
    writingStyle: "standard",
    customWritingInstructions: "",
    allowAiExtensions: true,
  };
}

export function secretStorageSupported(app: App): boolean {
  return requireApiVersion("1.11.4")
    && Boolean(app.secretStorage)
    && typeof app.secretStorage.getSecret === "function"
    && typeof app.secretStorage.setSecret === "function";
}

export function getApiKey(app: App): string {
  if (!secretStorageSupported(app)) {
    throw new Error("SECRET_STORAGE_UNSUPPORTED: 当前 Obsidian 不支持 SecretStorage，请升级到 1.11.4 或更高版本。");
  }
  const key = app.secretStorage.getSecret(API_KEY_SECRET_ID);
  if (!key) throw new Error("API_KEY_MISSING: 请先在 YouTube Note Reader 设置中保存模型 API Key。");
  return key;
}

export interface SettingsController {
  app: App;
  settings: YouTubeReaderSettings;
  saveSettings(): Promise<void>;
  validateApiKey(): Promise<string>;
}

export class YouTubeReaderSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly controller: SettingsController) {
    super(app, controller as never);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl).setName("YouTube Note Reader").setHeading();
    containerEl.createEl("p", {
      text: "Chrome 扩展负责发送视频和 Cookie；Obsidian 插件直接调用现有 Skill、模型 API 并写入当前 Vault。",
      cls: "youtube-note-reader-status",
    });

    new Setting(containerEl)
      .setName("OpenAI-compatible API base")
      .setDesc("例如 https://api.openai.com/v1，插件会调用 /chat/completions。")
      .addText((text) => text
        .setPlaceholder("https://api.openai.com/v1")
        .setValue(this.controller.settings.apiBase)
        .onChange(async (value) => {
          this.controller.settings.apiBase = value.trim();
          await this.controller.saveSettings();
        }));

    new Setting(containerEl)
      .setName("模型")
      .setDesc("使用服务商要求的完整模型名称，任务期间不会自动换模型。")
      .addText((text) => text
        .setPlaceholder("gpt-5-mini")
        .setValue(this.controller.settings.model)
        .onChange(async (value) => {
          this.controller.settings.model = value.trim();
          await this.controller.saveSettings();
        }));

    const supported = secretStorageSupported(this.app);
    const configured = supported && Boolean(this.app.secretStorage.getSecret(API_KEY_SECRET_ID));
    let pendingApiKey = "";
    new Setting(containerEl)
      .setName("API Key")
      .setDesc(supported
        ? (configured ? "已安全保存在 Obsidian SecretStorage。输入新值可替换。" : "仅保存到 Obsidian SecretStorage，不写入插件 data.json。")
        : "当前 Obsidian 不支持 SecretStorage。为避免明文保存，插件不会接受 API Key。")
      .addText((text) => {
        text.inputEl.type = "password";
        text.setPlaceholder(configured ? "已配置，留空保持不变" : "sk-...");
        text.setDisabled(!supported);
        text.onChange((value) => {
          pendingApiKey = value.trim();
        });
      })
      .addButton((button) => button
        .setButtonText("保存 API Key")
        .setCta()
        .setDisabled(!supported)
        .onClick(() => {
          if (!pendingApiKey || !supported) {
            new Notice("请先输入完整的 API Key。");
            return;
          }
          this.app.secretStorage.setSecret(API_KEY_SECRET_ID, pendingApiKey);
          pendingApiKey = "";
          new Notice("YouTube Note Reader API Key 已保存。");
          this.display();
        }));

    new Setting(containerEl)
      .setName("验证模型连接")
      .setDesc("使用已保存的 API Key 和当前模型发送一次最小请求；Key 不会显示或写入日志。")
      .addButton((button) => button
        .setButtonText("验证 API Key")
        .setDisabled(!supported || !configured)
        .onClick(async () => {
          button.setDisabled(true).setButtonText("验证中…");
          try {
            const message = await this.controller.validateApiKey();
            new Notice(message, 6_000);
          } catch (error) {
            new Notice(String((error as Error).message || error), 10_000);
          } finally {
            button.setDisabled(false).setButtonText("验证 API Key");
          }
        }));

    new Setting(containerEl)
      .setName("Python 可执行文件")
      .setDesc("可以填写 python、py，或 python.exe 的绝对路径。")
      .addText((text) => text
        .setValue(this.controller.settings.pythonExecutable)
        .onChange(async (value) => {
          this.controller.settings.pythonExecutable = value.trim() || "python";
          await this.controller.saveSettings();
        }));

    new Setting(containerEl)
      .setName("Skill 目录")
      .setDesc("必须包含 VERSION、scripts/extract_transcript.py、extract_frames.py 和 validate_note.py。")
      .addText((text) => text
        .setValue(this.controller.settings.skillDirectory)
        .onChange(async (value) => {
          this.controller.settings.skillDirectory = value.trim();
          await this.controller.saveSettings();
        }));

    new Setting(containerEl)
      .setName("输出文件夹")
      .setDesc("相对于当前 Vault，例如 YouTube video。")
      .addText((text) => text
        .setValue(this.controller.settings.outputFolder)
        .onChange(async (value) => {
          this.controller.settings.outputFolder = value.trim() || "YouTube video";
          await this.controller.saveSettings();
        }));

    new Setting(containerEl)
      .setName("完成后打开笔记")
      .setDesc("校验通过后在 Obsidian 中打开最终笔记。")
      .addToggle((toggle) => toggle
        .setValue(this.controller.settings.autoOpenNote)
        .onChange(async (value) => {
          this.controller.settings.autoOpenNote = value;
          await this.controller.saveSettings();
        }));

    new Setting(containerEl).setName("写作设置").setHeading();
    let pendingStyle = this.controller.settings.writingStyle;
    let pendingInstructions = this.controller.settings.customWritingInstructions;
    let pendingExtensions = this.controller.settings.allowAiExtensions;
    new Setting(containerEl)
      .setName("写作风格")
      .setDesc("详细教程最长 12 分钟；其他模式最长 8 分钟。")
      .addDropdown((dropdown) => dropdown
        .addOption("standard", "标准学习笔记")
        .addOption("detailed", "详细教程")
        .addOption("plain", "通俗讲解")
        .setValue(pendingStyle)
        .onChange((value) => { pendingStyle = value as YouTubeReaderSettings["writingStyle"]; }));
    new Setting(containerEl)
      .setName("自定义要求")
      .setDesc("例如：增加参数表、重点讲清原理。留空则不额外扩写。")
      .addTextArea((text) => text.setValue(pendingInstructions).onChange((value) => { pendingInstructions = value.trim(); }));
    new Setting(containerEl)
      .setName("允许 AI 延伸解读")
      .setDesc("仅当自定义要求明确请求时生成，并单列“延伸解读（AI补充）”。")
      .addToggle((toggle) => toggle.setValue(pendingExtensions).onChange((value) => { pendingExtensions = value; }));
    new Setting(containerEl).addButton((button) => button.setButtonText("保存写作设置").setCta().onClick(async () => {
      this.controller.settings.writingStyle = pendingStyle;
      this.controller.settings.customWritingInstructions = pendingInstructions;
      this.controller.settings.allowAiExtensions = pendingExtensions;
      await this.controller.saveSettings();
      new Notice("写作设置已保存，将从下一个任务开始生效。", 4_000);
    }));
  }
}
