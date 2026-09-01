import { FileSystemAdapter, Notice, Plugin, TFile } from "obsidian";

import { PluginHttpServer } from "./http-server";
import { JobManager, type JobState } from "./job-manager";
import {
  YouTubeReaderSettingTab,
  defaultSettings,
  type YouTubeReaderSettings,
} from "./settings";

interface PluginData {
  settings?: Partial<YouTubeReaderSettings>;
  latestJob?: JobState;
}

export default class YouTubeNoteReaderPlugin extends Plugin {
  declare settings: YouTubeReaderSettings;
  private data: PluginData = {};
  private jobs: JobManager | null = null;
  private httpServer: PluginHttpServer | null = null;
  private vaultBasePath = "";

  async onload(): Promise<void> {
    if (!(this.app.vault.adapter instanceof FileSystemAdapter)) {
      throw new Error("YouTube Note Reader 仅支持 Obsidian 桌面版文件系统 Vault。");
    }
    this.vaultBasePath = this.app.vault.adapter.getBasePath();
    this.data = (await this.loadData() as PluginData | null) || {};
    this.settings = { ...defaultSettings(this.vaultBasePath), ...(this.data.settings || {}) };
    this.addSettingTab(new YouTubeReaderSettingTab(this.app, this));
    this.addCommand({
      id: "open-settings",
      name: "打开 YouTube Note Reader 设置",
      callback: () => this.openSettings(),
    });

    this.jobs = new JobManager({
      app: this.app,
      vaultBasePath: this.vaultBasePath,
      getSettings: () => this.settings,
      persist: async (state) => {
        this.data.latestJob = state;
        await this.persistData();
      },
      openNote: (note) => this.openNote(note),
      openSettings: () => this.openSettings(),
      initialState: this.data.latestJob,
    });
    await this.jobs.markInterruptedOnLoad();
    this.httpServer = new PluginHttpServer(this.jobs);
    try {
      await this.httpServer.start();
    } catch (error) {
      const message = String((error as Error).message || error);
      new Notice(message.includes("EADDRINUSE")
        ? "YouTube Note Reader 无法启动 32191 端口：旧桌面伴侣仍在运行，请先退出旧伴侣后重载插件。"
        : `YouTube Note Reader 本地服务启动失败：${message}`,
      10_000);
      throw error;
    }
  }

  async onunload(): Promise<void> {
    await this.jobs?.shutdown();
    await this.httpServer?.stop();
  }

  async saveSettings(): Promise<void> {
    this.data.settings = this.settings;
    await this.persistData();
  }

  private async persistData(): Promise<void> {
    await this.saveData({ settings: this.settings, latestJob: this.data.latestJob });
  }

  private async openNote(absolutePath: string): Promise<void> {
    const normalizedVault = this.vaultBasePath.replace(/[\\/]+$/, "");
    const normalizedTarget = absolutePath.replace(/[\\/]+/g, "/");
    const normalizedBase = normalizedVault.replace(/[\\/]+/g, "/");
    if (!normalizedTarget.startsWith(`${normalizedBase}/`)) {
      throw new Error("PATH_OUTSIDE_VAULT: 笔记路径不在当前 Vault 内。");
    }
    const relative = normalizedTarget.slice(normalizedBase.length + 1);
    const file = this.app.vault.getAbstractFileByPath(relative);
    if (!(file instanceof TFile)) throw new Error(`NOTE_MISSING: Obsidian 找不到笔记 ${relative}`);
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  private openSettings(): void {
    const setting = (this.app as unknown as {
      setting: { open(): void; openTabById(id: string): void };
    }).setting;
    setting.open();
    setting.openTabById(this.manifest.id);
  }
}
