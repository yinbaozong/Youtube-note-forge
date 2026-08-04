---
description: Execute the youtube-transcript v3 fixed video-to-note workflow. Stop on the first script failure and never improvise browser or downloader fallbacks.
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

For each URL, run materials extraction once, create one transcript-grounded frame plan, run targeted frame extraction once, write the note, validate it, then allow only one note-only repair. Process URLs sequentially and stop the entire batch on the first error. Never run browser tools, network search, other downloaders, dependency installers, or debugging commands.

For `--version`, run `python .obsidian/skills/youtube-transcript/scripts/video_note.py --version` and return its real output.
