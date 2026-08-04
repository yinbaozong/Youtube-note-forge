#!/usr/bin/env python3
"""Extract transcript-guided, high-quality video frames without full video downloads."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from extract_transcript import (
    YtDlp,
    image_quality_score,
    platform_cookie_path,
    sanitize_filename,
    video_platform,
)
from video_common import Deadline, PipelineError, VERSION, emit_result, version_text


MAX_FRAMES = 24
FRAME_DEADLINE_SECONDS = 120
FRAME_TIMEOUT_SECONDS = 15
MIN_FRAME_BYTES = 2_000


@dataclass(frozen=True)
class FrameRequest:
    section_id: str
    timestamp: float
    purpose: str
    required: bool


def parse_timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    raw = str(value).strip()
    parts = raw.split(":")
    if not parts or len(parts) > 3:
        raise ValueError(f"invalid timestamp: {value}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return max(0.0, seconds)


def timestamp_label(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}-{(total % 3600) // 60:02d}-{total % 60:02d}"


def load_plan(path: Path) -> list[FrameRequest]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", f"无法读取画面计划：{exc}") from exc
    items = raw.get("frames", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", "画面计划必须包含非空 frames 数组。")
    if len(items) > MAX_FRAMES:
        raise PipelineError("FRAME_PLAN_TOO_LARGE", "frame_plan", f"画面计划包含 {len(items)} 项，最多允许 {MAX_FRAMES} 项。")
    requests: list[FrameRequest] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", f"第 {index} 项不是对象。")
        try:
            requests.append(
                FrameRequest(
                    section_id=str(item["section_id"]).strip(),
                    timestamp=parse_timestamp(item["timestamp"]),
                    purpose=str(item["purpose"]).strip(),
                    required=bool(item.get("required", False)),
                )
            )
        except (KeyError, ValueError) as exc:
            raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", f"第 {index} 项缺少有效 section_id、timestamp 或 purpose。") from exc
    if any(not item.section_id or not item.purpose for item in requests):
        raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", "section_id 和 purpose 不能为空。")
    return requests


def select_source(metadata: dict) -> str:
    formats = [
        item for item in (metadata.get("formats") or [])
        if item.get("url") and item.get("vcodec") != "none" and item.get("height")
    ]
    candidates = [item for item in formats if int(item.get("height") or 0) <= 720]
    if not candidates:
        candidates = formats
    candidates.sort(
        key=lambda item: (
            int(item.get("height") or 0) <= 720,
            int(item.get("height") or 0),
            item.get("ext") == "mp4",
            float(item.get("tbr") or 0),
        ),
        reverse=True,
    )
    if not candidates:
        direct_url = metadata.get("url")
        if direct_url:
            return str(direct_url)
        raise PipelineError("FRAME_SOURCE_UNAVAILABLE", "frame_source", "平台未提供可直接读取的视频流。")
    return str(candidates[0]["url"])


def resolve_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise PipelineError("FFMPEG_UNAVAILABLE", "frame_source", "未找到 ffmpeg 或 imageio-ffmpeg。") from exc


def vault_relative(path: Path, vault: Path) -> str:
    try:
        return str(path.resolve().relative_to(vault.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def attach_manifest_to_note(note: Path, manifest: Path, vault: Path) -> None:
    try:
        text = note.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError("note has no YAML frontmatter")
        end = text.find("\n---", 4)
        if end < 0:
            raise ValueError("note YAML frontmatter is not closed")
        frontmatter = text[4:end]
        value = vault_relative(manifest, vault)
        line = f"frame_manifest: {json.dumps(value, ensure_ascii=False)}"
        if re.search(r"^frame_manifest:\s*.*$", frontmatter, flags=re.MULTILINE):
            frontmatter = re.sub(r"^frame_manifest:\s*.*$", line, frontmatter, flags=re.MULTILINE)
        else:
            frontmatter = frontmatter.rstrip() + "\n" + line
        note.write_text("---\n" + frontmatter + text[end:], encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise PipelineError("FILE_WRITE_FAILED", "frames", f"无法把抽帧清单写入笔记 YAML：{exc}") from exc


def extract_candidate(ffmpeg: str, source: str, seconds: float, target: Path, timeout: int) -> bool:
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{max(0, seconds):.3f}",
        "-i",
        source,
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "2",
        str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > MIN_FRAME_BYTES and image_quality_score(target) >= 0


def extract_frame(ffmpeg: str, source: str, request: FrameRequest, target: Path, deadline: Deadline) -> tuple[bool, str]:
    target_deadline = Deadline(FRAME_TIMEOUT_SECONDS)
    for offset in (0.0, -2.0, 2.0):
        target.unlink(missing_ok=True)
        timeout = min(deadline.timeout_for(FRAME_TIMEOUT_SECONDS), target_deadline.timeout_for(FRAME_TIMEOUT_SECONDS))
        if extract_candidate(ffmpeg, source, request.timestamp + offset, target, timeout):
            return True, ""
    target.unlink(missing_ok=True)
    return False, "FRAME_TIMEOUT_OR_LOW_QUALITY"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract transcript-guided visual evidence without downloading a full video.")
    parser.add_argument("url", nargs="?", help="Video URL")
    parser.add_argument("--plan", type=Path, help="JSON frame plan")
    parser.add_argument("--vault", default=str(Path.home() / "Documents" / "Obsidian Vault"))
    parser.add_argument("--output-dir", default="YouTube video")
    parser.add_argument("--note", type=Path, help="Generated source note path")
    parser.add_argument("--cookies", help="Explicit cookies.txt path")
    parser.add_argument("--no-cookies", action="store_true")
    parser.add_argument("--proxy")
    parser.add_argument("--deadline", type=int, default=FRAME_DEADLINE_SECONDS)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if not args.version and (not args.url or not args.plan or not args.note):
        parser.error("url, --plan, and --note are required unless --version is used")
    if args.deadline < 30 or args.deadline > FRAME_DEADLINE_SECONDS:
        parser.error(f"--deadline must be between 30 and {FRAME_DEADLINE_SECONDS}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.version:
        print(version_text())
        return 0
    assert args.url and args.plan and args.note
    deadline = Deadline(args.deadline)
    plan = load_plan(args.plan)
    auto_cookie = platform_cookie_path(args.url)
    runner = YtDlp(args.cookies, args.no_cookies, args.proxy, auto_cookie, deadline)
    if auto_cookie and auto_cookie.exists() and not args.cookies and not args.no_cookies:
        print(f"Using saved {video_platform(args.url)} cookies: {auto_cookie}", flush=True)
    print("Resolving 720p-or-lower remote video stream...", flush=True)
    metadata = json.loads(runner.run(["--dump-single-json", "--no-playlist", args.url], purpose="resolve frame source", timeout=60).stdout)
    source = select_source(metadata)
    ffmpeg = resolve_ffmpeg()
    video_id = sanitize_filename(str(metadata.get("id") or "video"), max_len=80)
    assets_dir = args.note.parent / "assets" / video_id
    assets_dir.mkdir(parents=True, exist_ok=True)
    successful: list[dict] = []
    failures: list[dict] = []
    refreshed = False
    for index, request in enumerate(plan, start=1):
        try:
            deadline.timeout_for(FRAME_TIMEOUT_SECONDS)
        except PipelineError:
            failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": "FRAME_TIMEOUT"})
            break
        target = assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg"
        print(f"[{index}/{len(plan)}] Extracting {request.section_id} at {timestamp_label(request.timestamp)}", flush=True)
        try:
            ok, reason = extract_frame(ffmpeg, source, request, target, deadline)
        except PipelineError:
            failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": "FRAME_TIMEOUT", "required": request.required})
            break
        if not ok and not refreshed:
            refreshed = True
            print("Refreshing stream URL once...", flush=True)
            metadata = json.loads(runner.run(["--dump-single-json", "--no-playlist", args.url], purpose="refresh frame source", timeout=45).stdout)
            source = select_source(metadata)
            try:
                ok, reason = extract_frame(ffmpeg, source, request, target, deadline)
            except PipelineError:
                failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": "FRAME_TIMEOUT", "required": request.required})
                break
        if ok:
            successful.append({"section_id": request.section_id, "timestamp": request.timestamp, "purpose": request.purpose, "required": request.required, "path": str(target)})
        else:
            failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": reason, "required": request.required})
            if len(failures) >= 2 and not successful:
                break
    required_failures = [item for item in failures if item.get("required")]
    required_total = sum(1 for item in plan if item.required)
    coverage = len(successful) / len(plan)
    if required_failures or coverage < 0.70:
        code = "FRAME_COVERAGE_INSUFFICIENT"
        message = f"定点抽帧未达标：成功 {len(successful)}/{len(plan)}，必需图片失败 {len(required_failures)} 张。"
        emit_result("error", stage="frames", code=code, message=message, successful=successful, failures=failures, coverage=coverage)
        return 2
    manifest = assets_dir / "frame-manifest.json"
    manifest_payload = {
        "status": "ok",
        "skill_version": VERSION,
        "video_id": video_id,
        "note": str(args.note.resolve()),
        "plan_count": len(plan),
        "coverage": coverage,
        "frames": successful,
        "failures": failures,
    }
    try:
        manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise PipelineError("FILE_WRITE_FAILED", "frames", f"无法写入抽帧清单：{exc}") from exc
    attach_manifest_to_note(args.note, manifest, Path(args.vault))
    emit_result("ok", stage="frames", note=str(args.note), manifest=str(manifest), screenshots=successful, failures=failures, coverage=coverage, elapsed_seconds=args.deadline - deadline.remaining())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        emit_result("error", stage=exc.stage, code=exc.code, message=str(exc), action=exc.action)
        raise SystemExit(2)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        emit_result("error", stage="frames", code="FRAME_EXTRACTION_FAILED", message=str(exc))
        raise SystemExit(1)
