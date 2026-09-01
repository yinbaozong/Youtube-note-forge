# v4 架构：Chrome + Obsidian

## 目标

完整安装不再依赖 OpenCode，也不再启动独立常驻桌面伴侣。Chrome 扩展负责读取当前视频 URL 与 YouTube Cookie；Obsidian 桌面插件负责固定任务状态机、模型调用、现有 Skill 脚本、进度持久化和 Vault 文件管理。

```text
Chrome 当前视频页
  -> YouTube 阅读器扩展
  -> 127.0.0.1:32191（仅 Obsidian 打开时存在）
  -> YouTube Note Reader Obsidian 插件
  -> youtube-transcript 现有脚本
  -> OpenAI-compatible 模型 API
  -> 当前 Vault/YouTube video
```

## 组件职责

### Chrome 扩展

- 读取当前 YouTube URL、标题和 `.youtube.com` Cookie。
- 启动、恢复、停止任务并展示进度与最终路径。
- 字幕不可用、下载失败或解析失败时，只向用户提供显式的“允许 ASR 并重试”。
- 不保存 Cookie 值，不运行模型，不读取或写入 Vault。

### Obsidian 插件

- 只监听 `127.0.0.1:32191`，并拒绝普通网页 Origin。
- 在 Obsidian 设置中保存模型地址、模型名、Python 与 Skill 路径；API Key 使用 Obsidian Secret Storage。
- 把 Cookie 写到现有兼容路径，然后依次执行 `extract_transcript.py`、模型画面计划、`extract_frames.py`、模型写作和 `validate_note.py`。
- 第一次笔记校验失败只修正文档一次；第二次失败立即停止。
- 持久化最新任务；弹窗关闭不影响任务，Obsidian 重启后把未完成任务标记为可恢复。

### youtube-transcript Skill

- 保持唯一的视频素材、字幕、抽帧和质量校验实现。
- 完整安装由 Obsidian 插件调用；便携安装仍可通过 OpenCode `/video-note` 调用。
- 不启动浏览器，不关闭用户 Chrome，不下载完整视频用于抽帧。

## 为什么仍需要打开 Obsidian

视频任务需要执行 Python、`yt-dlp`、FFmpeg，持续数分钟并写入本地 Vault。Chrome Manifest V3 后台不适合承担本地进程和长任务。把执行环境放进用户本来就要阅读笔记的 Obsidian，可以取消单独常驻程序，同时保留本地文件、Cookie 隐私和可恢复任务。

## 故障边界

- Obsidian 未打开或插件未启用：Chrome 立即提示打开 Obsidian，不创建假任务。
- 模型未配置：插件在启动前停止并打开设置入口。
- `SUBTITLE_UNAVAILABLE`：视频确实没有可用字幕。
- `SUBTITLE_DOWNLOAD_FAILED`：检测到字幕，但平台请求超时或被拒绝。
- `SUBTITLE_PARSE_FAILED`：字幕文件存在，但没有解析出文本。
- 上述三类错误都不自动下载音频；只有用户点击“允许 ASR 并重试”才启用。
- 抽帧、写作或校验失败：保留已成功的 SRT、截图、清单和草稿，供同 URL 恢复。

## 分发

- Skill-only：适合已经使用 OpenCode 的用户，不安装浏览器和 Obsidian 插件。
- Full reader：安装 Chrome 扩展、Obsidian 插件和 Skill；OpenCode 不再是依赖。
