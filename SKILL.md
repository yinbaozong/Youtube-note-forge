---
name: youtube-transcript
description: Use for YouTube, Bilibili, and other video URLs when the user wants a Chinese Obsidian learning note with SRT subtitles, transcript-guided screenshots, and a fixed fail-fast workflow. Run the provided scripts only; never use browser automation, Chrome for Testing, Puppeteer, Canvas, or unbounded retries.
---

# YouTube Transcript v3.0.1

Use this Skill as a strict state machine. Do not improvise alternate extraction paths.

## Version

When the user asks for the current version, run:

```powershell
python .obsidian/skills/youtube-transcript/scripts/video_note.py --version
```

Return the command output. Never answer from memory. It reports the installed version, source path, and SHA-256 values for the core files.

## Fixed workflow

For each URL, complete the following steps before moving to the next URL. Stop the whole batch at the first non-zero script result.

1. Extract source material:

```powershell
python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py "<url>" --output-dir "YouTube video" --deadline 300
```

2. Read the generated note and its SRT. Create a non-empty JSON frame plan with no more than 24 items. Each item has `section_id`, `timestamp`, `purpose`, and `required`. Choose time points that directly support an important detailed-summary section. Do not rename or rewrite the note yet.
3. Extract planned frames exactly once. After a successful materials result, this is the only allowed next executable command:

```powershell
python .obsidian/skills/youtube-transcript/scripts/extract_frames.py "<url>" --plan "<plan.json>" --note "<generated-note.md>" --output-dir "YouTube video" --deadline 120
```

4. Confirm that the frame result has `status: ok`, at least one screenshot, and a real `manifest` path. The script automatically writes `frame_manifest` into the note YAML. If any of these are missing, stop; do not write the final note and do not substitute the cover.
5. Rename and rewrite the generated note in place according to [references/note-contract.md](references/note-contract.md). Insert every required successful frame into its matching `## 详细内容总结` subsection and add a Chinese explanation below it. Only images listed in the manifest count as screenshots.
6. Validate:

```powershell
python .obsidian/skills/youtube-transcript/scripts/validate_note.py "<finished-note.md>"
```

7. If validation fails, edit only that note once and run the validator once more. Never run extraction during this repair. If it still fails, stop and report the returned errors.

## Failure policy

`PIPELINE_RESULT` is the source of truth. On `status: error`, stop immediately and report the URL, stage, code, message, action, completed URLs, and unstarted URLs.

Never respond to a failure by running another downloader, a browser, Puppeteer, Chrome/Edge cookie extraction, a web search, an audio recording, or a second attempt. The only controlled retry is inside the scripts: one last-known-good Cookie check and one video-stream URL refresh during targeted frame extraction.

ASR is disabled by default. Use `--allow-asr` only if the user explicitly accepts audio download and slower processing.

## Visual requirements

- Screenshots are evidence, not decoration.
- Use demonstrations, interfaces, diagrams, parameters, comparison states, and results; avoid duplicate talking-head shots.
- The frame script uses a remote 720p-or-lower stream and never downloads a complete video for screenshots.
- A required frame failure or less than 70% frame-plan coverage is a hard failure.
- A cover, thumbnail, manually copied image, or duplicate embed never counts as a keyframe.
- Never claim completion unless `frame_manifest` exists and every required manifest frame appears in `## 详细内容总结`.
- Do not create Canvas files.

## Final note requirements

- Filename: `中文标题 - English Title`.
- YAML contains `skill_version: 3.0.1` and a valid `frame_manifest` written by `extract_frames.py`.
- Body begins at `## 一句话摘要` without a duplicate H1 title.
- Use Chinese first; explain necessary English terminology at first use.
- Link the SRT at the end. Do not paste raw transcript text.
- Remove the self-check checklist before final completion.
