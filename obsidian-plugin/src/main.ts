import { FileSystemAdapter, Notice, Plugin, TFile } from "obsidian";

import { PluginHttpServer } from "./http-server";
import { JobManager, type JobState } from "./job-manager";
import { TASK_CENTER_VIEW, TaskCenterView } from "./task-center";
import { updateHistory, type TaskHistoryEntry } from "./task-history";
import { probeApiCredentials } from "./model-client";
import {
  YouTubeReaderSettingTab,
  defaultSettings,
  getApiKey,
  type YouTubeReaderSettings,
} from "./settings";

interface PluginData {
  settings?: Partial<YouTubeReaderSettings>;
  latestJob?: JobState;
  taskHistory?: TaskHistoryEntry[];
  historyImported?: boolean;
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
    this.registerView(TASK_CENTER_VIEW, (leaf) => new TaskCenterView(leaf, {
      history: () => this.data.taskHistory || [],
      current: () => this.jobs?.latest || this.data.latestJob || ({ status: "idle", progress_percent: 0 } as JobState),
      open: (note) => this.openNote(note),
    }));
    this.addRibbonIcon("video", "视频笔记", () => void this.openTaskCenter());
    this.addCommand({ id: "open-task-center", name: "打开视频笔记任务中心", callback: () => void this.openTaskCenter() });
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
        this.data.taskHistory = updateHistory(this.data.taskHistory || [], state);
        await this.persistData();
        this.refreshTaskCenter();
      },
      openNote: (note) => this.openNote(note),
      openSettings: () => this.openSettings(),
      validateApiKey: () => this.validateApiKey(),
      initialState: this.data.latestJob,
    });
    await this.jobs.markInterruptedOnLoad();
    await this.importHistoryOnce();
    this.registerEvent(this.app.vault.on("rename", (file, oldPath) => {
      if (!(file instanceof TFile)) return;
      const oldAbsolute = `${this.vaultBasePath}\\${oldPath.replaceAll("/", "\\")}`;
      const item = this.data.taskHistory?.find((entry) => entry.notePath === oldAbsolute);
      if (!item) return;
      item.notePath = `${this.vaultBasePath}\\${file.path.replaceAll("/", "\\")}`;
      item.title = file.basename;
      item.updatedAt = Date.now();
      void this.persistData().then(() => this.refreshTaskCenter());
    }));
    this.registerEvent(this.app.vault.on("delete", (file) => {
      const absolute = `${this.vaultBasePath}\\${file.path.replaceAll("/", "\\")}`;
      const next = (this.data.taskHistory || []).filter((entry) => entry.notePath !== absolute);
      if (next.length === (this.data.taskHistory || []).length) return;
      this.data.taskHistory = next;
      void this.persistData().then(() => this.refreshTaskCenter());
    }));
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

  async validateApiKey(): Promise<string> {
    return probeApiCredentials({
      apiBase: this.settings.apiBase,
      apiKey: getApiKey(this.app),
      model: this.settings.model,
      timeoutMs: 20_000,
    });
  }

  private async persistData(): Promise<void> {
    await this.saveData({ settings: this.settings, latestJob: this.data.latestJob, taskHistory: this.data.taskHistory, historyImported: this.data.historyImported });
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

  private async openTaskCenter(): Promise<void> {
    const existing = this.app.workspace.getLeavesOfType(TASK_CENTER_VIEW)[0];
    const leaf = existing || this.app.workspace.getRightLeaf(false);
    if (!leaf) return;
    if (!existing) await leaf.setViewState({ type: TASK_CENTER_VIEW, active: true });
    this.app.workspace.revealLeaf(leaf);
  }

  private refreshTaskCenter(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(TASK_CENTER_VIEW)) (leaf.view as TaskCenterView).refresh();
  }

  private async importHistoryOnce(): Promise<void> {
    if (this.data.historyImported) return;
    const existing = this.data.taskHistory || [];
    for (const file of this.app.vault.getMarkdownFiles()) {
      if (!file.path.startsWith(`${this.settings.outputFolder}/`) || file.path.includes("/.reader-drafts/") || file.path.includes("/transcripts/")) continue;
      const frontmatter = this.app.metadataCache.getFileCache(file)?.frontmatter;
      const url = String(frontmatter?.url || "");
      if (!/youtu(?:\.be|be\.com)|bilibili\.com/i.test(url)) continue;
      const tags = Array.isArray(frontmatter?.tags) ? frontmatter.tags.map(String) : [];
      const notePath = `${this.vaultBasePath}\\${file.path.replaceAll("/", "\\")}`;
      if (!existing.some((entry) => entry.notePath === notePath)) {
        existing.push({ id: `import:${file.path}`, title: String(frontmatter?.title || file.basename), topic: String(frontmatter?.topic || tags.find((tag) => !/video-learning|source\/video/.test(tag)) || "未分类"), videoUrl: url,
          notePath, status: "ok", stage: "complete", progress: 100, createdAt: file.stat.ctime, updatedAt: file.stat.mtime, imported: true });
      }
    }
    this.data.taskHistory = existing.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 500);
    this.data.historyImported = true;
    await this.persistData();
  }
}
