---
name: youtube-note-forge
description: "Turn YouTube, Bilibili, and other yt-dlp supported videos into Obsidian-ready learning notes. Use when the user gives a video URL and wants metadata, subtitles, SRT files, cover/keyframe images, or a polished Chinese learning note. The skill uses yt-dlp-first extraction, fails fast on real errors, and does not use Puppeteer, Chrome for Testing, browser recording, or Canvas generation by default."
---

# YouTube Note Forge

## Quick Start

Run from the Obsidian vault root:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --output-dir "YouTube video"
```

Before contacting a real platform, verify the local install:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py --self-test --output-dir "YouTube video"
```

Default behavior:

- Uses `yt-dlp` only.
- Uses shared persistent credentials under `~/.config/opencode/credentials/youtube-note-forge/`.
- After a cookie works, the script saves a `*.lastgood` backup. If the current cookie is rejected later, the script verifies the last-known-good backup once before asking the user for a new cookie.
- If `yt-dlp` mutates a cookie file during a failed command, the script restores the pre-run cookie file. Only successful commands are allowed to update saved cookies.
- YouTube cookies are never sent to Bilibili, and Bilibili cookies are never sent to YouTube.
- Never opens or reads the running browser profile by default.
- Never opens Chrome for Testing, Puppeteer, or browser audio recording.
- Extracts article screenshots by seeking directly into a 720p-or-lower remote stream. It does not download the complete video only for screenshots.
- Scores candidate frames, rejects blank/low-quality/near-duplicate images, and reuses existing screenshots.
- Does not create Canvas. The Markdown article is the learning output.
- Does not download audio for ASR unless the user explicitly asks for ASR and passes `--allow-asr`.
- Uses hard timeouts so failures are reported instead of hanging forever.

Screenshot behavior:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --output-dir "YouTube video" --max-keyframes 16
```

`--max-keyframes` is a ceiling, not a target. The script automatically keeps only useful screenshots. Use `--max-keyframes 0` or `--no-keyframes` only if the user explicitly says screenshots are not needed.

## Cookie Rules

Do not ask the user for cookies unless the saved cookie has actually been tested and rejected.

Required flow:

1. Run `extract_transcript.py` first.
2. If it prints `Using saved youtube cookies` or `Using saved bilibili cookies`, assume the cookie is remembered.
3. If extraction succeeds, never ask for cookies.
4. If extraction fails for subtitles, frames, network, HTTP 412, missing ffmpeg, old yt-dlp, or no captions, do not ask for cookies unless the output explicitly says the saved cookie was rejected.
5. Ask for a new YouTube cookie only when the script output includes `Saved YouTube cookies were rejected`, `cookies are no longer valid`, or `Sign in to confirm`. At that point the script has already tried the saved cookie and any `lastgood` backup.
6. Ask for a new Bilibili cookie only when the script output says the saved Bilibili cookie was rejected, or when no Bilibili cookie exists and public access returns HTTP 412. At that point the script has already tried the saved cookie and any `lastgood` backup.
7. When the user provides cookies, save them permanently to the matching shared credentials file, then rerun the same command immediately.

Forbidden:

- Do not store cookies only in the current chat.
- Do not store cookies inside the note.
- Do not paste cookies into the final answer.
- Do not use `--cookies-from-browser chrome`, Edge, Firefox, Puppeteer, or Chrome for Testing.
- Do not retry random browser or API fallback paths.
- Do not delete or overwrite another platform's cookie.

Bilibili note:

- Public Bilibili videos may return `HTTP 412 Precondition Failed`. This is an anti-crawling or network response, not proof that a saved cookie expired.
- If no Bilibili cookie exists, ask once and save it to `cookies.bilibili.txt`.
- If a saved Bilibili cookie already exists and HTTP 412 continues, report the 412 error. Do not repeatedly ask for the same cookie.

## Agent Workflow

Use this loop every time:

1. Run the extraction script from the vault root.
2. If the script fails, report the real script error. Only ask for cookies under the Cookie Rules above.
3. Open the generated Markdown note.
4. Rewrite the current note in place. Do not create a second note unless the user explicitly asks.
5. Rename the file to `中文标题 - English Title` if needed, and update SRT/image links after any rename or move.
6. Insert useful screenshots into `## 详细内容总结`. Do not leave all images only in `## 关键画面索引`.
7. Make the article Chinese-first and learning-focused.
8. Before finishing, inspect the final note and complete the quality gates below. If any gate fails, keep editing.

## Quality Gates

The final note is not done until all are true:

- Filename is `中文标题 - English Title`, not pure English and not `待命名 - ...`.
- Body starts at `## 一句话摘要`; no repeated top-level title.
- `## 详细内容总结` contains relevant image embeds if screenshots were extracted.
- Each inserted image has a Chinese sentence explaining what it proves, shows, compares, or clarifies.
- The article is mostly Chinese. English is allowed only for necessary terms, product names, commands, models, paper titles, links, YAML keys, and transcript metadata.
- Necessary English terms have Chinese explanations on first use.
- Original transcript is not pasted into the article. The end only links to the SRT file.
- The `## 输出自检清单` is either completed or removed after the checks are satisfied.

## Chinese Learning Note Prompt

Use this instruction after extraction:

```text
分析这个视频：<视频链接>
请使用 youtube-note-forge skill 处理这个视频，并输出到 YouTube video 文件夹。

目标：
把当前文档整理成一份适合学习、理解、复习和迁移应用的高质量中文 Obsidian 笔记。直接修改当前文档，不要新建文件。

要求：
1. 文件名必须是：中文标题 - English Title。
2. 正文以中文为主，必要 English terms 保留英文并用中文解释。
3. 内容要详细、结构化，不能只做简单摘要。
4. 区分事实、观点、方法、案例、行动建议。
5. 结论尽量来自视频信息、SRT 字幕、描述和关键画面；不确定就标注“待确认”。
6. 如果有关键截图，必须把有价值截图插入“详细内容总结”的对应段落，并说明图片作用。
7. 原始字幕不要粘贴到正文；文末只保留 SRT 字幕文件链接。
8. 完成后检查：插图是否进入正文、是否有英文残留、文件名是否正确、SRT 链接是否仍有效。

结构：

## 一句话摘要

## 核心知识点速览

## 详细内容总结

## 重点难点解析

## 可视化总结

## 学习图谱

## 行动建议-举一反三

## 专业术语表

## 原始字幕 Transcript
```

## Troubleshooting

- Cookie failure: follow Cookie Rules. Do not improvise browser fallbacks.
- No subtitles: report the real error. Do not download audio unless the user passed `--allow-asr`.
- YouTube bot or n-challenge errors: first update yt-dlp and install default dependencies:

```bash
python -m pip install --upgrade "yt-dlp[default]"
node --version
```

- Screenshots not needed: pass `--max-keyframes 0` or `--no-keyframes`.
- Canvas not needed: do not pass `--write-canvas`.
