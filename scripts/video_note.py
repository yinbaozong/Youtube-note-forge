#!/usr/bin/env python3
"""Entry-point helpers for the constrained /video-note OpenCode command."""

from __future__ import annotations

import argparse
import hashlib
import json

from video_common import SKILL_ROOT, VERSION


CORE_FILES = (
    "VERSION",
    "SKILL.md",
    "scripts/extract_transcript.py",
    "scripts/extract_frames.py",
    "scripts/validate_note.py",
    "scripts/video_common.py",
    "references/note-contract.md",
)


def version_report() -> dict[str, object]:
    return {
        "skill": "youtube-transcript",
        "skill_version": VERSION,
        "source_path": str(SKILL_ROOT),
        "core_sha256": {
            relative: hashlib.sha256((SKILL_ROOT / relative).read_bytes()).hexdigest()
            for relative in CORE_FILES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Helpers for /video-note.")
    parser.add_argument("--version", action="store_true", help="Show the installed skill version and core hashes.")
    args = parser.parse_args()
    if not args.version:
        parser.error("Only --version is supported. Use /video-note for the fixed workflow.")
    print(json.dumps(version_report(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
