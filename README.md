# YouTube Note Forge

Turn long videos into Obsidian-ready learning notes, with transcripts, SRT files, cover images, and useful screenshots.

YouTube Note Forge v3.3.0 is an agent skill for people who do serious learning from video. Paste a YouTube or Bilibili URL, let `yt-dlp` collect the reliable source material, then use the fixed workflow to forge it into a structured Chinese note with transcript-guided visual evidence.

![YouTube Note Forge workflow](docs/workflow.svg)

## Why Star This

- Built for video learners, not just transcript dumping.
- Produces Markdown notes, SRT subtitles, covers, and transcript-guided keyframes in a bounded workflow.
- Keeps cookies in a shared credentials folder and backs up last-known-good cookies.
- Fails fast instead of quietly opening random browser automation fallbacks.
- Designed for Obsidian workflows: clean filenames, local assets, and review-ready note sections.

## What It Does

1. Reads metadata with `yt-dlp`.
2. Prefers platform subtitles and saves them as `.srt`.
3. Optionally falls back to local ASR with `faster-whisper`.
4. Extracts a cover first; then maps planned timestamps directly to bounded 720p HLS media segments. If that path is unavailable, it downloads only bounded 8-second temporary segments, never the whole video.
5. Validates the finished Chinese learning note and stops after one bounded repair attempt.

![Generated output structure](docs/output-structure.svg)

## Install

Clone this repo into your skill directory:

```bash
cd ~/.codex/skills
git clone https://github.com/yinbaozong/Youtube-note-forge.git
```

For OpenCode-style skill locations, clone it to:

```bash
mkdir -p ~/.config/opencode/skills
cd ~/.config/opencode/skills
git clone https://github.com/yinbaozong/Youtube-note-forge.git youtube-transcript
```

Install runtime dependencies:

```bash
python -m pip install -r requirements.txt
node --version
```

If `node --version` fails, install Node.js first. `yt-dlp` uses Node for some site JavaScript challenges.

Optional ASR fallback:

```bash
python -m pip install -r requirements-asr.txt
```

Verify the local installation before touching YouTube or Bilibili:

```bash
python scripts/extract_transcript.py --self-test --vault ./_verify --output-dir notes
```

If this succeeds, your Python dependencies and Markdown/SRT generation path are working. Real video extraction can still require platform access or valid cookies.

## Chrome Extension

The local YouTube Reader extension reads the current YouTube URL and its `.youtube.com` cookies, then talks to a desktop companion bound only to `127.0.0.1:32191`. The companion starts the existing `/video-note` OpenCode command. Neither component analyzes video or writes notes, and cookie values are never stored in Chrome extension storage.

Install the Skill and desktop companion:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Chrome requires one manual step for a local unpacked extension:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select the repository's `extension` directory.
5. Open the extension settings, select an OpenCode model, optionally enter that provider's API key, and save.

The API key is passed directly to the local companion and stored with OpenCode's existing credentials. Cookie values are sent only over the local loopback connection and saved at the existing `cookies.youtube.txt` path. Obsidian does not need to be open while a job runs. Closing the popup never stops the desktop job; reopening the extension from any YouTube tab reconnects to the same request. The extension badge shows progress or completion, and the finished popup shows the full note path, screenshot count, total runtime, a copy-path action, and an **Open in Obsidian** button. Auto-open is enabled by default and is executed by the desktop companion, so it still works when the popup is closed. A visible **Force stop** button terminates the OpenCode process tree without deleting completed files.

The visible stages are: Cookie sync, OpenCode startup, source material extraction, article and screenshot planning, bounded frame extraction, Chinese note writing, quality validation, and completion. A failed stage returns its machine error code and stops; it does not switch browsers or retry with a different workflow.

Verify or remove the local bridge:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_install.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall.ps1
```

## Need Help?

If you want to reproduce this project but are not sure how to start, feel free to contact me anytime: yinbaozong@163.com

## First Run

Run from your Obsidian vault root:

```bash
python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --output-dir "YouTube video" --deadline 300
```

Typical output:

```text
YouTube video/
├── 待命名 - Example Video.md
├── transcripts/
│   └── 待命名 - Example Video.srt
└── assets/
    └── 待命名 - Example Video/
        ├── cover.jpg
        └── frame_00-03-21.jpg
```

Then use the fixed OpenCode command:

```text
/video-note <video url>
```

## Cookies

Public videos usually work without cookies. If a platform asks for login, export cookies manually and save them here:

```text
~/.config/opencode/credentials/youtube-transcript/cookies.youtube.txt
~/.config/opencode/credentials/youtube-transcript/cookies.bilibili.txt
```

Rules baked into the skill:

- YouTube cookies are never reused for Bilibili.
- Bilibili cookies are never reused for YouTube.
- A working cookie is copied to `*.lastgood`.
- If a command fails after mutating a cookie file, the script restores the pre-run cookie.
- The skill asks for fresh cookies only after the saved cookie is actually rejected.

## Common Commands

Check the installed version:

```bash
python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py --version
```

Extract screenshots from a transcript-guided plan:

```bash
python .obsidian/skills/youtube-transcript/scripts/extract_frames.py <video_url> --plan frame-plan.json --note "YouTube video/待命名 - Video.md" --deadline 120
```

Allow local ASR when subtitles are missing:

```bash
python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py <video_url> --output-dir "YouTube video" --allow-asr --asr-model base
```

Use a proxy:

```bash
python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py <video_url> --proxy http://127.0.0.1:7897
```

## Troubleshooting

- `yt-dlp` errors: run `python -m pip install --upgrade "yt-dlp[default]"`.
- No subtitles: retry with `--allow-asr`, or choose another video with captions.
- No screenshots: read the `PIPELINE_RESULT` from `extract_frames.py`; it stops on missing required evidence or insufficient coverage.
- Bilibili `HTTP 412`: add a Bilibili cookie once; if it continues, report the 412 instead of repeatedly replacing cookies.
- YouTube login challenge: export a fresh YouTube cookie and save it as `cookies.youtube.txt`.

## Philosophy

This skill is intentionally boring where reliability matters. It does not silently launch Puppeteer, Chrome for Testing, browser audio recording, or a second downloader. If extraction fails, it emits a fixed error result and stops.
