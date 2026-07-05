# YouTube Note Forge

Turn long videos into Obsidian-ready learning notes, with transcripts, SRT files, cover images, and useful screenshots.

YouTube Note Forge is an agent skill for people who do serious learning from video. Paste a YouTube or Bilibili URL, let `yt-dlp` collect the reliable source material, then ask your coding agent to forge it into a structured Chinese note that is actually useful for review, writing, and reuse.

## Why Star This

- Built for video learners, not just transcript dumping.
- Produces Markdown notes, SRT subtitles, covers, and keyframes in one run.
- Keeps cookies in a shared credentials folder and backs up last-known-good cookies.
- Fails fast instead of quietly opening random browser automation fallbacks.
- Designed for Obsidian workflows: clean filenames, local assets, and review-ready note sections.

## What It Does

1. Reads metadata with `yt-dlp`.
2. Prefers platform subtitles and saves them as `.srt`.
3. Optionally falls back to local ASR with `faster-whisper`.
4. Extracts cover images and useful keyframes without downloading the whole video just for screenshots.
5. Creates a Markdown source note that guides an agent to write a polished Chinese learning note.

## Install

Clone this repo into your skill directory:

```bash
cd ~/.codex/skills
git clone https://github.com/yinbaozong/youtube-note-forge.git
```

For OpenCode-style skill locations, clone it to:

```bash
mkdir -p ~/.config/opencode/skills
cd ~/.config/opencode/skills
git clone https://github.com/yinbaozong/youtube-note-forge.git
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

## First Run

Run from your Obsidian vault root:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --output-dir "YouTube video"
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

Then ask your agent:

```text
Use youtube-note-forge to analyze this video:
<video url>

Rewrite the generated Markdown into a Chinese Obsidian learning note.
The filename must be: 中文标题 - English Title.
```

## Cookies

Public videos usually work without cookies. If a platform asks for login, export cookies manually and save them here:

```text
~/.config/opencode/credentials/youtube-note-forge/cookies.youtube.txt
~/.config/opencode/credentials/youtube-note-forge/cookies.bilibili.txt
```

Rules baked into the skill:

- YouTube cookies are never reused for Bilibili.
- Bilibili cookies are never reused for YouTube.
- A working cookie is copied to `*.lastgood`.
- If a command fails after mutating a cookie file, the script restores the pre-run cookie.
- The skill asks for fresh cookies only after the saved cookie is actually rejected.

## Common Commands

Extract more screenshots:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --output-dir "YouTube video" --max-keyframes 16
```

Skip screenshots:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --output-dir "YouTube video" --no-keyframes
```

Allow local ASR when subtitles are missing:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --output-dir "YouTube video" --allow-asr --asr-model base
```

Use a proxy:

```bash
python .obsidian/skills/youtube-note-forge/scripts/extract_transcript.py <video_url> --proxy http://127.0.0.1:7897
```

## Troubleshooting

- `yt-dlp` errors: run `python -m pip install --upgrade "yt-dlp[default]"`.
- No subtitles: retry with `--allow-asr`, or choose another video with captions.
- No screenshots: install dependencies with `python -m pip install -r requirements.txt`.
- Bilibili `HTTP 412`: add a Bilibili cookie once; if it continues, report the 412 instead of repeatedly replacing cookies.
- YouTube login challenge: export a fresh YouTube cookie and save it as `cookies.youtube.txt`.

## Philosophy

This skill is intentionally boring where reliability matters. It does not silently launch Puppeteer, Chrome for Testing, or browser audio recording. If extraction fails, you get the real error, fix the real cause, and keep your browser profiles and cookies under your control.
