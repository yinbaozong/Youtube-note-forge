import type { JobState } from "./job-manager";

export interface TaskHistoryEntry {
  id: string;
  title: string;
  topic: string;
  videoUrl: string;
  notePath: string;
  status: JobState["status"];
  stage: JobState["stage"];
  progress: number;
  createdAt: number;
  updatedAt: number;
  imported?: boolean;
}

export function updateHistory(history: TaskHistoryEntry[], state: JobState, now = Date.now()): TaskHistoryEntry[] {
  if (!state.request_id) return history;
  const existing = history.find((item) => item.id === state.request_id);
  const entry: TaskHistoryEntry = {
    id: state.request_id,
    title: state.video_title || existing?.title || "未命名视频",
    topic: state.topic || existing?.topic || "未分类",
    videoUrl: state.video_url || existing?.videoUrl || "",
    notePath: state.note_path || existing?.notePath || "",
    status: state.status,
    stage: state.stage,
    progress: state.progress_percent,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
    imported: existing?.imported,
  };
  return [entry, ...history.filter((item) => item.id !== entry.id)].slice(0, 500);
}

export function filterHistory(history: TaskHistoryEntry[], query: string, period: string, topic: string, now = Date.now()): TaskHistoryEntry[] {
  const q = query.trim().toLocaleLowerCase();
  const day = 24 * 60 * 60 * 1000;
  const start = period === "today" ? now - day : period === "week" ? now - 7 * day
    : period === "month" ? new Date(new Date(now).getFullYear(), new Date(now).getMonth(), 1).getTime() : 0;
  return history.filter((item) => (!q || `${item.title} ${item.topic}`.toLocaleLowerCase().includes(q))
    && (!start || item.updatedAt >= start) && (!topic || item.topic === topic));
}
