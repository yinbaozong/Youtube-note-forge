---
description: Execute the optional youtube-transcript v4.0.4 OpenCode compatibility workflow. Never skip transcript-guided frame extraction; allow one note-only validation repair.
mode: primary
permission:
  read: allow
  edit: allow
  bash:
    "*": deny
    "python .obsidian/skills/youtube-transcript/scripts/extract_transcript.py *": allow
    "python .obsidian/skills/youtube-transcript/scripts/extract_frames.py *": allow
    "python .obsidian/skills/youtube-transcript/scripts/validate_note.py *": allow
    "python .obsidian/skills/youtube-transcript/scripts/video_note.py --version": allow
  webfetch: deny
  websearch: deny
  task: deny
  doom_loop: deny
---

Use the `youtube-transcript` Skill. Treat its `PIPELINE_RESULT` as authoritative.

If the argument starts with `RESUME_EXISTING_TASK`, resume the exact URL after that marker instead of following the new-URL command order below. Search `YouTube video` for notes whose YAML `url` exactly matches it. Prefer a completed Chinese-titled note that already passes `validate_note.py`; return it immediately. Otherwise choose the most recently modified matching `待命名 - ...` draft. Reuse each valid artifact recorded in YAML: if `transcript_file` exists, do not run `extract_transcript.py`; if `frame_manifest` exists, has `status: ok`, and every required frame exists, do not run `extract_frames.py`. Continue from the earliest missing stage, then write, rename, and validate normally. Never discard a valid SRT, frame manifest, screenshot, or partial Chinese section during resume.

For each URL, use this exact command order: `extract_transcript.py` once, `extract_frames.py` once, `validate_note.py` once, and optionally `validate_note.py` one final time after a note-only repair. The frame plan must contain both `article_outline` and `frames`, as defined by the Skill. Select images by learning value: normally one required frame per visually meaningful chapter, with optional frames for distinct steps or results; most videos need 6-14 frames and 24 is only a hard limit. After materials succeed, do not rename or rewrite the note before frame extraction. Never skip `extract_frames.py`, even when a cover exists or the topic seems simple. Require a successful frame manifest with at least one real keyframe before writing the final note. Use every manifest outline title as an exact `###` heading in the detailed summary. Embed every vault-local screenshot with exact Obsidian wiki syntax `![[YouTube video/assets/VIDEO_ID/frame.jpg]]`, followed by its Chinese explanation; never emit an unescaped Markdown image target containing spaces. Keep similar-looking terminal, code, slide, and UI frames; the script only rejects effectively blank images. Process URLs sequentially and stop the entire batch at the first error. Never run browser tools, network search, other downloaders, dependency installers, ad-hoc/probe scripts, format inspection, implementation inspection, or debugging commands. Remove all source/task scaffolding from the final note because YAML already holds source metadata. Preserve article depth independently of screenshot count. If validation reports quality errors, repair only the note once; do not re-extract materials or frames. Successful validation performs the exact-URL placeholder cleanup; do not manually delete unmatched drafts.

For `--version`, run `python .obsidian/skills/youtube-transcript/scripts/video_note.py --version` and return its real output.

Your final response must end with the exact final machine line from the last script, in this form: `PIPELINE_RESULT=<JSON>`. Copy that line verbatim. Do not translate, summarize, re-encode, or omit it. Extraction or frame errors are terminal. 第一次 NOTE_VALIDATION_FAILED is not terminal: use its error list to repair the current note only, then run `validate_note.py` exactly one more time. A second validation failure is terminal. On success, return the final `validate_note.py` `PIPELINE_RESULT` line.
