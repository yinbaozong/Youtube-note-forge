---
name: youtube-transcript
description: Use for YouTube, Bilibili, and other video URLs when the user wants a Chinese Obsidian learning note with SRT subtitles, transcript-guided screenshots, and a fixed fail-fast workflow. Run the provided scripts only; never use browser automation, Chrome for Testing, Puppeteer, Canvas, or unbounded retries.
---

# YouTube Transcript v4.1.1

The primary full-product runtime is the `youtube-note-reader` Obsidian desktop plugin. It owns the deterministic state machine, calls the configured OpenAI-compatible model, runs these scripts, persists task state, and writes the finished note into the active Vault. The Chrome extension only supplies the current video URL and browser Cookie. OpenCode remains an optional Skill-only entry point and is not required by the full browser + Obsidian installation.

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

2. Read the generated note and its SRT. Create one JSON plan with `article_outline` and `frames`. `article_outline` contains 3-8 transcript-derived chapters; every item has `section_id`, `title`, `start`, `end`, `core_claims`, and `learning_goal`. Choose frames by learning value, normally one required frame for each visually meaningful chapter plus optional evidence for distinct steps or results. Most videos need 6-14 frames; 24 is only the hard safety limit, never a target. Do not create multiple near-identical time points merely to increase the count. Every frame has `section_id`, `timestamp`, `purpose`, and `required`. Use the same `section_id` to bind each screenshot to its detailed-summary chapter. Do not rename or rewrite the note yet. Do not write probe scripts or inspect video formats manually.
3. Extract planned frames exactly once. After a successful materials result, this is the only allowed next executable command:

```powershell
python .obsidian/skills/youtube-transcript/scripts/extract_frames.py "<url>" --plan "<plan.json>" --note "<generated-note.md>" --output-dir "YouTube video" --deadline 120
```

4. Confirm that the frame result has `status: ok`, at least one screenshot, and a real `manifest` path. The script automatically writes `frame_manifest` into the note YAML. If any of these are missing, stop; do not write the final note and do not substitute the cover.
5. Rename and rewrite the generated note in place according to [references/note-contract.md](references/note-contract.md). Under `## 详细内容总结`, use every `article_outline.title` as an exact `###` heading, explain its core claims, and insert every required successful frame into its matching chapter with a Chinese explanation. For vault-local screenshots, always use Obsidian wiki embeds such as `![[YouTube video/assets/VIDEO_ID/frame.jpg]]`; never use an unescaped Markdown target containing spaces. Only images listed in the manifest count as screenshots.
6. Validate:

```powershell
python .obsidian/skills/youtube-transcript/scripts/validate_note.py "<finished-note.md>"
```

7. If validation fails, edit only that note once and run the validator once more. Never run extraction, write temporary scripts, inspect implementation code, or diagnose platform behavior during this repair. If it still fails, stop and report the returned errors. After a successful validation, the validator removes only `待命名 - ...` Markdown drafts in the same folder whose YAML `url` exactly matches the final note; unmatched interrupted drafts remain available for resume.

## Failure policy

`PIPELINE_RESULT` is the source of truth. On `status: error`, stop immediately and report the URL, stage, code, message, action, completed URLs, and unstarted URLs.

Never respond to a failure by running another downloader, a browser, Puppeteer, Chrome/Edge cookie extraction, a web search, an audio recording, or a second attempt. The only controlled retry is inside the scripts: one last-known-good Cookie check and one video-stream URL refresh during targeted frame extraction.

ASR is disabled by default. Use `--allow-asr` only if the user explicitly accepts audio download and slower processing.

## Visual requirements

- Screenshots are evidence, not decoration.
- Use demonstrations, interfaces, diagrams, parameters, comparison states, and results.
- The frame script first uses a remote 720p-or-lower stream with yt-dlp request headers. If remote seek fails, it may download only an 8-second bounded segment around each planned time point. Temporary segments are deleted immediately and a complete video is never downloaded for screenshots.
- A required frame failure or less than 70% frame-plan coverage is a hard failure.
- A cover, thumbnail, manually copied image, or duplicate embed never counts as a keyframe.
- Keep planned frames even when their overall layouts look similar. Terminal text, code, slides, and UI state changes are useful. Reject only effectively blank black/white frames; do not use OCR, AI image analysis, or perceptual duplicate filtering.
- Never claim completion unless `frame_manifest` exists and every required manifest frame appears in `## 详细内容总结`.
- Do not create Canvas files.

## Final note requirements

- Filename: `中文标题 - English Title`.
- YAML contains `skill_version: 4.1.1`, `quality_profile_version: 1`, and a valid `frame_manifest` written by `extract_frames.py`.
- Body begins at `## 一句话摘要` without a duplicate H1 title.
- Remove source status, video description, visual-material indexes, extraction warnings, task instructions, and self-check sections from the final body; YAML already carries source metadata.
- Use Chinese first; explain necessary English terminology at first use.
- Let the transcript determine the article outline and depth. Images support the explanation; never shorten or omit important content merely because a matching frame is unavailable.
- Match article depth to video duration. Every planned chapter needs a real explanation, video evidence or example, and a clear learning implication. Avoid generic filler and repeated paragraphs.
- Link the SRT at the end. Do not paste raw transcript text.
- Remove the self-check checklist before final completion.
