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
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from extract_transcript import (
    YtDlp,
    image_quality_score,
    platform_cookie_path,
    sanitize_filename,
    video_platform,
)
from video_common import Deadline, PipelineError, VERSION, emit_progress, emit_result, version_text


MAX_FRAMES = 24
FRAME_DEADLINE_SECONDS = 120
FRAME_TIMEOUT_SECONDS = 15
MIN_FRAME_BYTES = 2_000
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024
MAX_SEGMENT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class FrameRequest:
    section_id: str
    timestamp: float
    purpose: str
    required: bool


@dataclass(frozen=True)
class FrameSource:
    url: str
    headers: dict[str, str]
    protocol: str = ""


@dataclass(frozen=True)
class HlsSegment:
    index: int
    start: float
    duration: float
    url: str


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


def load_plan_document(path: Path) -> tuple[list[FrameRequest], list[dict]]:
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
    outline = raw.get("article_outline", []) if isinstance(raw, dict) else []
    if outline and not isinstance(outline, list):
        raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", "article_outline 必须是数组。")
    normalized_outline: list[dict] = []
    for index, item in enumerate(outline, start=1):
        if not isinstance(item, dict):
            raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", f"article_outline 第 {index} 项不是对象。")
        section_id = str(item.get("section_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not section_id or not title:
            raise PipelineError("FRAME_PLAN_INVALID", "frame_plan", f"article_outline 第 {index} 项缺少 section_id 或 title。")
        normalized_outline.append(dict(item, section_id=section_id, title=title))
    return requests, normalized_outline


def load_plan(path: Path) -> list[FrameRequest]:
    return load_plan_document(path)[0]


def parse_hls_media_playlist(text: str, playlist_url: str) -> list[HlsSegment]:
    segments: list[HlsSegment] = []
    pending_duration: float | None = None
    elapsed = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            try:
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                pending_duration = None
            continue
        if not line or line.startswith("#") or pending_duration is None:
            continue
        segments.append(
            HlsSegment(
                index=len(segments),
                start=elapsed,
                duration=pending_duration,
                url=urllib.parse.urljoin(playlist_url, line),
            )
        )
        elapsed += pending_duration
        pending_duration = None
    if not segments:
        raise PipelineError("FRAME_SOURCE_UNAVAILABLE", "frame_source", "720p HLS 播放列表没有可读取的媒体分片。")
    return segments


def select_hls_segment(segments: list[HlsSegment], seconds: float) -> tuple[HlsSegment, float]:
    target = max(0.0, seconds)
    for segment in segments:
        if target < segment.start + segment.duration:
            return segment, max(0.0, target - segment.start)
    final = segments[-1]
    return final, max(0.0, min(final.duration - 0.05, target - final.start))


def classify_frame_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "saved youtube cookies were rejected" in text or "sign in to confirm" in text or "cookie" in text and "rejected" in text:
        return "COOKIE_REJECTED"
    if "timed out" in text or "timeout" in text:
        return "NETWORK_TIMEOUT"
    return "FRAME_EXTRACTION_FAILED"


def read_remote_bytes(url: str, headers: dict[str, str], timeout: int, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={key: value for key, value in headers.items() if value})
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if hostname in {"127.0.0.1", "localhost"} else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:
        announced = int(response.headers.get("Content-Length") or 0)
        if announced > limit:
            raise PipelineError("FRAME_SEGMENT_TOO_LARGE", "frames", f"目标视频分片超过 {limit // 1024 // 1024} MB 安全上限。")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise PipelineError("FRAME_SEGMENT_TOO_LARGE", "frames", f"目标视频分片超过 {limit // 1024 // 1024} MB 安全上限。")
    return payload


def load_hls_segments(source: FrameSource, timeout: int) -> list[HlsSegment]:
    payload = read_remote_bytes(source.url, source.headers, timeout, MAX_PLAYLIST_BYTES)
    return parse_hls_media_playlist(payload.decode("utf-8", errors="replace"), source.url)


def cache_hls_segment(segment: HlsSegment, directory: Path, headers: dict[str, str], timeout: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"hls_{segment.index:04d}.segment"
    if target.exists() and target.stat().st_size > MIN_FRAME_BYTES:
        return target
    payload = read_remote_bytes(segment.url, headers, timeout, MAX_SEGMENT_BYTES)
    target.write_bytes(payload)
    return target


def extract_hls_frame(
    ffmpeg: str,
    source: FrameSource,
    segments: list[HlsSegment],
    request: FrameRequest,
    target: Path,
    directory: Path,
    deadline: Deadline,
) -> tuple[bool, str]:
    target_deadline = Deadline(FRAME_TIMEOUT_SECONDS)
    for candidate_time in (request.timestamp, request.timestamp - 2.0, request.timestamp + 2.0):
        target.unlink(missing_ok=True)
        try:
            segment, local_seconds = select_hls_segment(segments, candidate_time)
            segment_path = cache_hls_segment(
                segment,
                directory,
                source.headers,
                min(deadline.timeout_for(8), target_deadline.timeout_for(8)),
            )
            timeout = min(deadline.timeout_for(6), target_deadline.timeout_for(6))
        except (OSError, PipelineError, TimeoutError, urllib.error.URLError):
            continue
        if extract_candidate(ffmpeg, str(segment_path), local_seconds, target, timeout):
            return True, ""
    target.unlink(missing_ok=True)
    return False, "FRAME_HLS_SEGMENT_FAILED"


def extract_hls_frames_parallel(
    ffmpeg: str,
    source: FrameSource,
    segments: list[HlsSegment],
    jobs: list[tuple[int, FrameRequest, Path]],
    directory: Path,
    deadline: Deadline,
    *,
    workers: int = 4,
    on_complete: Callable[[int, int, int], None] | None = None,
) -> dict[int, tuple[bool, str]]:
    results: dict[int, tuple[bool, str]] = {}
    if not jobs:
        return results

    def run_one(index: int, request: FrameRequest, target: Path) -> tuple[int, tuple[bool, str]]:
        try:
            outcome = extract_hls_frame(
                ffmpeg,
                source,
                segments,
                request,
                target,
                directory / f"request_{index:02d}",
                deadline,
            )
        except PipelineError as exc:
            outcome = (False, exc.code)
        return index, outcome

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs))), thread_name_prefix="youtube-frame") as executor:
        futures = [executor.submit(run_one, index, request, target) for index, request, target in jobs]
        completed = 0
        for future in as_completed(futures):
            index, outcome = future.result()
            results[index] = outcome
            completed += 1
            if on_complete:
                on_complete(completed, len(jobs), index)
    return results


def select_source(metadata: dict) -> FrameSource:
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
            return FrameSource(
                str(direct_url),
                {str(k): str(v) for k, v in (metadata.get("http_headers") or {}).items()},
                str(metadata.get("protocol") or ""),
            )
        raise PipelineError("FRAME_SOURCE_UNAVAILABLE", "frame_source", "平台未提供可直接读取的视频流。")
    selected = candidates[0]
    headers = {str(k): str(v) for k, v in (metadata.get("http_headers") or {}).items()}
    headers.update({str(k): str(v) for k, v in (selected.get("http_headers") or {}).items()})
    return FrameSource(str(selected["url"]), headers, str(selected.get("protocol") or ""))


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


def extract_candidate(
    ffmpeg: str,
    source: str,
    seconds: float,
    target: Path,
    timeout: int,
    headers: dict[str, str] | None = None,
) -> bool:
    remote = urllib.parse.urlsplit(source).scheme in {"http", "https"}
    command = [ffmpeg, "-y"]
    if remote:
        command.extend(["-ss", f"{max(0, seconds):.3f}"])
    if headers:
        serialized = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if value)
        if serialized:
            command.extend(["-headers", serialized])
    command.extend(["-i", source])
    if not remote:
        command.extend(["-ss", f"{max(0, seconds):.3f}"])
    command.extend(
        [
        "-frames:v",
        "1",
        "-vf",
        "scale='min(1280,iw)':-2",
        "-q:v",
        "2",
        str(target),
        ]
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > MIN_FRAME_BYTES and image_quality_score(target) >= 0


def extract_frame(ffmpeg: str, source: FrameSource, request: FrameRequest, target: Path, deadline: Deadline) -> tuple[bool, str]:
    target_deadline = Deadline(FRAME_TIMEOUT_SECONDS)
    for offset in (0.0, -2.0, 2.0):
        target.unlink(missing_ok=True)
        try:
            timeout = min(deadline.timeout_for(8), target_deadline.timeout_for(8))
        except PipelineError:
            break
        if extract_candidate(ffmpeg, source.url, request.timestamp + offset, target, timeout, source.headers):
            return True, ""
    target.unlink(missing_ok=True)
    return False, "FRAME_TIMEOUT_OR_LOW_QUALITY"


def download_short_segment(
    runner: YtDlp,
    url: str,
    request: FrameRequest,
    directory: Path,
    ffmpeg: str,
    timeout: int,
) -> Path | None:
    start = max(0.0, request.timestamp - 4.0)
    end = request.timestamp + 4.0
    output_template = str(directory / f"segment_{timestamp_label(request.timestamp)}.%(ext)s")
    result = runner.run(
        [
            "--ffmpeg-location",
            ffmpeg,
            "--download-sections",
            f"*{start:.3f}-{end:.3f}",
            "--force-keyframes-at-cuts",
            "-f",
            "bestvideo[height<=720]/best[height<=720]/bestvideo[height<=480]/best[height<=480]",
            "--no-playlist",
            "--no-part",
            "-o",
            output_template,
            url,
        ],
        purpose=f"download bounded screenshot segment at {request.timestamp:.1f}s",
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        return None
    candidates = sorted(
        (path for path in directory.glob(f"segment_{timestamp_label(request.timestamp)}.*") if path.is_file()),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    return candidates[0] if candidates else None


def extract_best_segment_frame(ffmpeg: str, segment: Path, target: Path, timeout: int) -> bool:
    with tempfile.TemporaryDirectory(prefix="youtube-segment-candidates-") as raw:
        directory = Path(raw)
        pattern = directory / "candidate_%02d.jpg"
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(segment),
                    "-vf",
                    "fps=1,scale='min(1280,iw)':-2",
                    "-q:v",
                    "2",
                    str(pattern),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False
        candidates = [path for path in directory.glob("candidate_*.jpg") if path.stat().st_size > MIN_FRAME_BYTES]
        if result.returncode != 0 or not candidates:
            return False
        usable = [path for path in candidates if image_quality_score(path) >= 0]
        if not usable:
            return False
        shutil.copy2(max(usable, key=image_quality_score), target)
    return target.exists() and target.stat().st_size > MIN_FRAME_BYTES


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
    plan, article_outline = load_plan_document(args.plan)
    auto_cookie = platform_cookie_path(args.url)
    runner = YtDlp(args.cookies, args.no_cookies, args.proxy, auto_cookie, deadline)
    if auto_cookie and auto_cookie.exists() and not args.cookies and not args.no_cookies:
        print(f"Using saved {video_platform(args.url)} cookies: {auto_cookie}", flush=True)
    emit_progress("frames", "正在解析最高 720p 视频来源。", percent=35, current=0, total=len(plan))
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
    prefer_segments = False
    segment_seconds_used = 0
    segment_bytes_used = 0
    with tempfile.TemporaryDirectory(prefix="youtube-frame-segments-") as segment_directory_raw:
        segment_directory = Path(segment_directory_raw)
        hls_segments: list[HlsSegment] = []
        hls_outcomes: dict[int, tuple[bool, str]] = {}
        if source.protocol in {"m3u8", "m3u8_native"}:
            print("Reading bounded 720p HLS media segments...", flush=True)
            hls_segments = load_hls_segments(source, deadline.timeout_for(15))
            hls_jobs = [
                (
                    index,
                    request,
                    assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg",
                )
                for index, request in enumerate(plan, start=1)
                if not (
                    (assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg").exists()
                    and (assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg").stat().st_size > MIN_FRAME_BYTES
                    and image_quality_score(assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg") >= 0
                )
            ]
            if hls_jobs:
                print(f"Extracting {len(hls_jobs)} bounded HLS frames with 4 workers...", flush=True)

                def report_hls_progress(completed: int, total: int, _index: int) -> None:
                    print(f"HLS frame progress: {completed}/{total}", flush=True)
                    emit_progress(
                        "frames",
                        f"正在定点抽帧：{completed}/{total}",
                        percent=38 + round(34 * completed / max(1, total)),
                        current=completed,
                        total=total,
                    )

                hls_outcomes = extract_hls_frames_parallel(
                    ffmpeg,
                    source,
                    hls_segments,
                    hls_jobs,
                    segment_directory,
                    deadline,
                    on_complete=report_hls_progress,
                )
        for index, request in enumerate(plan, start=1):
            try:
                deadline.timeout_for(FRAME_TIMEOUT_SECONDS)
            except PipelineError:
                failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": "FRAME_TIMEOUT"})
                break
            target = assets_dir / f"frame_{index:02d}_{timestamp_label(request.timestamp)}.jpg"
            print(f"[{index}/{len(plan)}] Extracting {request.section_id} at {timestamp_label(request.timestamp)}", flush=True)
            if target.exists() and target.stat().st_size > MIN_FRAME_BYTES and image_quality_score(target) >= 0:
                successful.append({"section_id": request.section_id, "timestamp": request.timestamp, "purpose": request.purpose, "required": request.required, "path": str(target), "method": "cache"})
                continue
            ok = False
            reason = "FRAME_TIMEOUT_OR_LOW_QUALITY"
            if index in hls_outcomes:
                ok, reason = hls_outcomes[index]
                method = "hls-segment"
            elif not prefer_segments:
                if hls_segments:
                    ok, reason = extract_hls_frame(ffmpeg, source, hls_segments, request, target, segment_directory, deadline)
                    method = "hls-segment"
                else:
                    ok, reason = extract_frame(ffmpeg, source, request, target, deadline)
                    method = "remote"
            if not ok and not refreshed and not prefer_segments:
                refreshed = True
                print("Refreshing stream URL once...", flush=True)
                metadata = json.loads(runner.run(["--dump-single-json", "--no-playlist", args.url], purpose="refresh frame source", timeout=45).stdout)
                source = select_source(metadata)
                try:
                    if source.protocol in {"m3u8", "m3u8_native"}:
                        hls_segments = load_hls_segments(source, deadline.timeout_for(15))
                        ok, reason = extract_hls_frame(ffmpeg, source, hls_segments, request, target, segment_directory, deadline)
                        method = "hls-segment"
                    else:
                        hls_segments = []
                        ok, reason = extract_frame(ffmpeg, source, request, target, deadline)
                        method = "remote"
                except PipelineError:
                    ok = False
            if not ok:
                prefer_segments = True
                if segment_seconds_used + 8 > 120:
                    reason = "FRAME_SEGMENT_BUDGET_EXCEEDED"
                else:
                    print("Remote seek failed; downloading one bounded 8-second segment...", flush=True)
                    try:
                        segment = download_short_segment(
                            runner,
                            args.url,
                            request,
                            segment_directory,
                            ffmpeg,
                            deadline.timeout_for(45),
                        )
                    except PipelineError:
                        segment = None
                    if segment:
                        segment_seconds_used += 8
                        segment_bytes_used += segment.stat().st_size
                        if segment_bytes_used > 150 * 1024 * 1024:
                            reason = "FRAME_SEGMENT_BUDGET_EXCEEDED"
                        else:
                            try:
                                ok = extract_best_segment_frame(ffmpeg, segment, target, deadline.timeout_for(20))
                            except PipelineError:
                                ok = False
                            method = "segment"
                            reason = "" if ok else "FRAME_SEGMENT_UNUSABLE"
                    else:
                        reason = "FRAME_SEGMENT_DOWNLOAD_FAILED"
            if ok:
                successful.append({"section_id": request.section_id, "timestamp": request.timestamp, "purpose": request.purpose, "required": request.required, "path": str(target), "method": method})
            else:
                failures.append({"section_id": request.section_id, "timestamp": request.timestamp, "code": reason, "required": request.required})
                if len(failures) >= 2 and not successful:
                    break
            if not hls_segments:
                emit_progress(
                    "frames",
                    f"正在定点抽帧：{index}/{len(plan)}",
                    percent=38 + round(34 * index / max(1, len(plan))),
                    current=index,
                    total=len(plan),
                )
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
        "article_outline": article_outline,
        "frames": successful,
        "failures": failures,
    }
    try:
        manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise PipelineError("FILE_WRITE_FAILED", "frames", f"无法写入抽帧清单：{exc}") from exc
    attach_manifest_to_note(args.note, manifest, Path(args.vault))
    emit_progress("writing", f"已获得 {len(successful)} 张关键画面，正在撰写中文学习笔记。", percent=76, current=len(successful), total=len(plan))
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
        emit_result("error", stage="frames", code=classify_frame_exception(exc), message=str(exc))
        raise SystemExit(1)
