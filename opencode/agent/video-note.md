---
description: Execute the youtube-transcript v3.0.1 fixed video-to-note workflow. Never skip transcript-guided frame extraction; stop on the first script failure.
mode: subagent
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

For each URL, use this exact command order: `extract_transcript.py` once, `extract_frames.py` once, `validate_note.py` once, and optionally `validate_note.py` one final time after a note-only repair. After materials succeed, do not rename or rewrite the note before frame extraction. Never skip `extract_frames.py`, even when a cover exists or the topic seems simple. Require a successful frame manifest with at least one real keyframe before writing the final note. A cover or duplicate image is not a keyframe. Process URLs sequentially and stop the entire batch on the first error. Never run browser tools, network search, other downloaders, dependency installers, ad-hoc scripts, or debugging commands.

For `--version`, run `python .obsidian/skills/youtube-transcript/scripts/video_note.py --version` and return its real output.
