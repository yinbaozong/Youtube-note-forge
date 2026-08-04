"""Shared versioning, status, and deadline helpers for youtube-transcript."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "youtube-transcript"
SKILL_ROOT = Path(__file__).resolve().parent.parent
VERSION = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()


class PipelineError(RuntimeError):
    def __init__(self, code: str, stage: str, message: str, *, action: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.action = action


class Deadline:
    def __init__(self, seconds: int) -> None:
        self.started_at = time.monotonic()
        self.seconds = seconds

    def remaining(self) -> int:
        return max(0, int(self.seconds - (time.monotonic() - self.started_at)))

    def timeout_for(self, requested: int) -> int:
        remaining = self.remaining()
        if remaining <= 0:
            raise PipelineError(
                "PIPELINE_TIMEOUT",
                "deadline",
                f"素材提取超过 {self.seconds} 秒总时限。",
                action="请检查网络或缩短视频范围后重试。",
            )
        return min(requested, remaining)


def version_text() -> str:
    return f"{SKILL_NAME} {VERSION}"


def emit_result(status: str, **fields: Any) -> None:
    payload = {"status": status, "skill": SKILL_NAME, "skill_version": VERSION, **fields}
    # Keep the machine-readable contract ASCII so Windows consoles using GBK
    # cannot corrupt an error payload that an agent must parse.
    line = "PIPELINE_RESULT=" + json.dumps(payload, ensure_ascii=True, sort_keys=True)
    try:
        sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except AttributeError:
        print(line, flush=True)
