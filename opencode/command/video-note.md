---
description: Convert one or more video URLs into verified Chinese Obsidian learning notes with transcript-guided screenshots.
agent: video-note
---

Use the `youtube-transcript` v3.3.1 fixed workflow for: $ARGUMENTS

If the argument is `--version`, run `python .obsidian/skills/youtube-transcript/scripts/video_note.py --version` and report its real output only. Otherwise extract each URL in order. Frame extraction is mandatory: never write or validate the final note until `extract_frames.py` returns a successful non-empty manifest. Extraction and frame errors stop immediately. 第一次 NOTE_VALIDATION_FAILED must trigger exactly one note-only repair and one final validation; it must not rerun materials or frames. A second validation failure stops immediately.

The final response must end with the exact final script line `PIPELINE_RESULT=<JSON>`, copied verbatim without translation or reformatting.
