import { ItemView, WorkspaceLeaf, setIcon } from "obsidian";
import { filterHistory, type TaskHistoryEntry } from "./task-history";
import type { JobState } from "./job-manager";

export const TASK_CENTER_VIEW = "youtube-note-reader-tasks";

export class TaskCenterView extends ItemView {
  private query = "";
  private period = "all";
  private topic = "";
  constructor(leaf: WorkspaceLeaf, private readonly source: { history(): TaskHistoryEntry[]; current(): JobState; open(path: string): Promise<void> }) { super(leaf); }
  getViewType(): string { return TASK_CENTER_VIEW; }
  getDisplayText(): string { return "视频笔记"; }
  getIcon(): string { return "video"; }
  async onOpen(): Promise<void> { this.render(); }
  refresh(): void {
    if (this.containerEl.contains(document.activeElement) && document.activeElement?.tagName === "INPUT") return;
    this.render();
  }

  private render(): void {
    const root = this.containerEl.children[1] as HTMLElement;
    root.empty(); root.addClass("youtube-reader-center");
    const history = this.source.history().filter(item => item.status === "ok" && item.notePath);
    root.createEl("h3", { text: "视频笔记" });
    const current = this.source.current();
    root.createEl("div", { cls: "youtube-reader-summary", text: `${history.length} 篇已完成` });
    if (current.status === "running") {
      const active = root.createDiv({ cls: "youtube-reader-active" });
      active.createDiv({ text: current.video_title || "当前视频" });
      active.createEl("progress", { attr: { max: "100", value: String(current.progress_percent) } });
      active.createDiv({ cls: "youtube-reader-meta", text: `${current.message} · ${current.elapsed_seconds} 秒` });
    }
    const controls = root.createDiv({ cls: "youtube-reader-filters" });
    const search = controls.createEl("input", { type: "search", placeholder: "搜索标题或主题", value: this.query });
    search.oninput = () => {
      this.query = search.value;
      this.render();
      const next = root.querySelector("input");
      next?.focus();
    };
    const period = controls.createEl("select");
    for (const [value, label] of [["all","全部"],["today","今天"],["week","近七天"],["month","本月"]]) period.createEl("option", { value, text: label });
    period.value = this.period; period.onchange = () => { this.period = period.value; this.render(); };
    const topic = controls.createEl("select"); topic.createEl("option", { value: "", text: "全部主题" });
    [...new Set(history.map((item) => item.topic))].sort().forEach((value) => topic.createEl("option", { value, text: value }));
    topic.value = this.topic; topic.onchange = () => { this.topic = topic.value; this.render(); };
    const list = root.createDiv({ cls: "youtube-reader-list" });
    for (const item of filterHistory(history, this.query, this.period, this.topic)) {
      const row = list.createDiv({ cls: "youtube-reader-row" });
      const title = row.createEl("button", { cls: "youtube-reader-title", text: item.title });
      title.disabled = !item.notePath; title.onclick = () => void this.source.open(item.notePath);
      row.createDiv({ cls: "youtube-reader-meta", text: `${item.topic} · ${new Date(item.updatedAt).toLocaleString()} · ${item.status === "ok" ? "完成" : item.status === "running" ? `${item.progress}%` : "失败"}${item.imported ? " · 导入" : ""}` });
      if (item.videoUrl) {
        const video = row.createEl("a", { cls: "youtube-reader-original", href: item.videoUrl, attr: { title: "打开原视频", "aria-label": "打开原视频", target: "_blank", rel: "noopener" } });
        setIcon(video, "external-link");
      }
    }
    if (!list.children.length) list.createDiv({ cls: "youtube-reader-empty", text: history.length ? "没有匹配的笔记" : "完成的视频笔记会出现在这里" });
  }
}
