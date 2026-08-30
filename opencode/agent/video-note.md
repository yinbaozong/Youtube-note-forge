---
description: Execute the youtube-transcript v3.3.0 fixed video-to-note workflow. Never skip transcript-guided frame extraction; stop on the first script failure.
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

For each URL, use this exact command order: `extract_transcript.py` once, `extract_frames.py` once, `validate_note.py` once, and optionally `validate_note.py` one final time after a note-only repair. The frame plan must contain both `article_outline` and `frames`, as defined by the Skill. Select images by learning value: normally one required frame per visually meaningful chapter, with optional frames for distinct steps or results; most videos need 6-14 frames and 24 is only a hard limit. After materials succeed, do not rename or rewrite the note before frame extraction. Never skip `extract_frames.py`, even when a cover exists or the topic seems simple. Require a successful frame manifest with at least one real keyframe before writing the final note. Use every manifest outline title as an exact `###` heading in the detailed summary. Keep similar-looking terminal, code, slide, and UI frames; the script only rejects effectively blank images. Process URLs sequentially and stop the entire batch at the first error. Never run browser tools, network search, other downloaders, dependency installers, ad-hoc/probe scripts, format inspection, implementation inspection, or debugging commands. Remove all source/task scaffolding from the final note because YAML already holds source metadata. Preserve article depth independently of screenshot count. If validation reports quality errors, repair only the note once; do not re-extract materials or frames.

For `--version`, run `python .obsidian/skills/youtube-transcript/scripts/video_note.py --version` and return its real output.

Your final response must end with the exact final machine line from the last script, in this form: `PIPELINE_RESULT=<JSON>`. Copy that line verbatim. Do not translate, summarize, re-encode, or omit it. On failure, return the failing script's exact `PIPELINE_RESULT` line and stop. On success, return the final `validate_note.py` `PIPELINE_RESULT` line.
