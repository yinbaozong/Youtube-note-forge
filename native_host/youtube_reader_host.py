#!/usr/bin/env python3
"""Local desktop companion for the existing /video-note workflow."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlparse


HOST_NAME = "com.youtube_note_reader.host"
HOST_VERSION = "3.3.2"
DEFAULT_VAULT = Path.home() / "Documents" / "Obsidian Vault"
COOKIE_PATH = Path.home() / ".config" / "opencode" / "credentials" / "youtube-transcript" / "cookies.youtube.txt"
AUTH_PATH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
LOG_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "YouTubeNoteReader" / "last-job.jsonl"
MAX_JOB_SECONDS = 480
COMPANION_PORT = 32191
EXTENSION_ORIGIN = "chrome-extension://obcfabljhffpdbcaebficbfpdpinnhgh"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$")


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    video_id = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/", 3)[2]
    elif host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise ValueError("当前页面不是有效的 YouTube 视频。")
    return video_id


def cookies_to_netscape(cookies: Iterable[dict[str, Any]]) -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated locally by YouTube Reader. Do not edit.",
        "",
    ]
    count = 0
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip()
        name = str(cookie.get("name") or "").strip()
        if not domain or not name or not domain.lstrip(".").endswith("youtube.com"):
            continue
        path = str(cookie.get("path") or "/")
        include_subdomains = "FALSE" if cookie.get("hostOnly") else "TRUE"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = int(float(cookie.get("expirationDate") or 0))
        value = str(cookie.get("value") or "")
        lines.append("\t".join((domain, include_subdomains, path, secure, str(expires), name, value)))
        count += 1
    if count == 0:
        raise ValueError("Chrome 没有返回可用的 YouTube Cookie，请先在当前标签页登录 YouTube。")
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def save_cookie_snapshot(cookies: Iterable[dict[str, Any]]) -> int:
    text = cookies_to_netscape(cookies)
    if COOKIE_PATH.exists():
        shutil.copy2(COOKIE_PATH, COOKIE_PATH.with_suffix(COOKIE_PATH.suffix + ".lastgood"))
    atomic_write(COOKIE_PATH, text)
    return sum(1 for line in text.splitlines() if line and not line.startswith("#"))


def save_provider_key(provider: str, api_key: str) -> None:
    provider = provider.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", provider):
        raise ValueError("模型提供商名称无效。")
    if not api_key.strip():
        return
    payload: dict[str, Any] = {}
    if AUTH_PATH.exists():
        payload = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        shutil.copy2(AUTH_PATH, AUTH_PATH.with_suffix(".json.lastgood"))
    payload[provider] = {"type": "api", "key": api_key.strip()}
    atomic_write(AUTH_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def build_opencode_command(*, opencode: str, vault: Path, model: str, url: str) -> list[str]:
    validate_youtube_url(url)
    if not MODEL_PATTERN.fullmatch(model):
        raise ValueError("请选择有效的 OpenCode 模型。")
    if not vault.is_dir():
        raise ValueError(f"Obsidian Vault 不存在：{vault}")
    return [
        opencode,
        "run",
        "--command",
        "video-note",
        "--format",
        "json",
        "--dir",
        str(vault),
        "-m",
        model,
        url,
    ]


def open_note_in_obsidian(note_path: str, vault: Path) -> None:
    note = Path(note_path).expanduser().resolve()
    resolved_vault = vault.expanduser().resolve()
    try:
        relative = note.relative_to(resolved_vault)
    except ValueError as exc:
        raise ValueError("只能打开当前 Obsidian Vault 内的笔记。") from exc
    if note.suffix.lower() != ".md" or not note.is_file():
        raise ValueError("生成的 Obsidian 笔记不存在。")
    uri = "obsidian://open?vault=" + quote(resolved_vault.name) + "&file=" + quote(str(relative.with_suffix("")).replace("\\", "/"))
    if os.name == "nt":
        os.startfile(uri)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", uri])


def recursive_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from recursive_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_strings(child)


def pipeline_results(line: str) -> list[dict[str, Any]]:
    strings: list[str] = [line]
    try:
        strings.extend(recursive_strings(json.loads(line)))
    except json.JSONDecodeError:
        pass
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in strings:
        for item in text.splitlines():
            marker = "PIPELINE_RESULT="
            if marker not in item:
                continue
            candidate = item.split(marker, 1)[1].strip()
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                identity = json.dumps(payload, ensure_ascii=True, sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    results.append(payload)
    return results


def stage_from_output(text: str) -> str:
    lowered = text.lower()
    if "extract_transcript.py" in lowered or '"stage":"materials"' in lowered:
        return "materials"
    if "extract_frames.py" in lowered or '"stage":"frames"' in lowered:
        return "frames"
    if "frame_plan" in lowered or "plan_" in lowered or '"stage":"frame_plan"' in lowered:
        return "planning"
    if "validate_note.py" in lowered or "note_validation" in lowered:
        return "validation"
    if ".md" in lowered and ('"tool":"edit"' in lowered or '"tool": "edit"' in lowered or "permission=edit" in lowered):
        return "writing"
    return ""


USER_ERROR_MESSAGES = {
    "COOKIE_REJECTED": "当前 YouTube Cookie 已失效。请保持 YouTube 登录状态并重新点击插件，插件会自动更新 Cookie。",
    "SUBTITLE_MISSING": "该视频没有可用字幕。若确实需要语音识别，请在 Skill 中明确启用 ASR。",
    "NETWORK_TIMEOUT": "网络请求超时，任务已停止。请检查当前网络后重新点击插件。",
    "PIPELINE_TIMEOUT": "当前阶段超过时限，任务已自动停止，没有继续空跑。",
    "FRAME_COVERAGE_INSUFFICIENT": "关键截图成功率不足，任务已停止；不会用封面冒充正文截图。",
    "RESULT_NOTE_MISSING": "OpenCode 没有返回通过校验的笔记路径，任务已停止。",
    "OPENCODE_FAILED": "OpenCode 执行失败，任务已停止。请查看本地任务日志了解技术细节。",
    "TASK_INTERRUPTED": "桌面伴侣或 OpenCode 在任务完成前退出，任务已经中断。请清除任务后重新生成。",
}


def format_user_error(exc: Exception) -> tuple[str, str]:
    technical = str(exc).strip()
    match = re.match(r"^([A-Z][A-Z0-9_]+):", technical)
    code = match.group(1) if match else "PIPELINE_FAILED"
    if code in USER_ERROR_MESSAGES:
        return code, USER_ERROR_MESSAGES[code]
    first_line = technical.splitlines()[0] if technical else "任务失败。"
    return code, first_line[:300]


def interrupted_job_from_log(path: Path) -> dict[str, Any] | None:
    """Convert an unfinished persisted job into a terminal recovery event."""
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    lifecycle_types = {"accepted", "attached", "progress", "pipeline_result", "complete", "error", "cancelled"}
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("type") not in lifecycle_types:
            continue
        if payload.get("status") != "running" or not payload.get("request_id"):
            return None
        return {
            "type": "error",
            "request_id": str(payload["request_id"]),
            "status": "error",
            "stage": "failed",
            "code": "TASK_INTERRUPTED",
            "message": USER_ERROR_MESSAGES["TASK_INTERRUPTED"],
            "technical_message": f"TASK_INTERRUPTED: last stage was {payload.get('stage') or 'unknown'}",
            "elapsed_seconds": int(payload.get("elapsed_seconds") or 0),
            "progress_percent": int(payload.get("progress_percent") or 0),
            "current": payload.get("current"),
            "total": payload.get("total"),
            "video_title": str(payload.get("video_title") or ""),
            "video_url": str(payload.get("video_url") or ""),
            "output_dir": str(payload.get("output_dir") or ""),
        }
    return None


STAGE_MESSAGES = {
    "queued": "任务已排队，准备启动 OpenCode。",
    "credentials": "已同步当前 YouTube Cookie。",
    "starting": "正在启动受限 video-note Agent。",
    "materials": "正在提取元数据、字幕、SRT 和封面。",
    "planning": "正在依据字幕规划文章章节和截图时间点。",
    "frames": "正在从 720p 视频分片定点抽取关键画面。",
    "writing": "正在撰写中文学习笔记并插入对应截图。",
    "validation": "正在校验文章结构、深度、中文表达和文件链接。",
}

STAGE_ORDER = ["queued", "credentials", "starting", "materials", "planning", "frames", "writing", "validation", "complete"]
STAGE_PROGRESS = {
    "queued": 2,
    "credentials": 4,
    "starting": 6,
    "materials": 10,
    "planning": 28,
    "frames": 35,
    "writing": 76,
    "validation": 92,
    "complete": 100,
}


def stage_message(stage: str) -> str:
    return STAGE_MESSAGES.get(stage, "现有 youtube-transcript Skill 正在运行。")


def normalize_stage(stage: str) -> str:
    return {
        "note_validation": "validation",
        "frame_plan": "planning",
        "deadline": "failed",
    }.get(stage, stage)


def validation_error_action(result: dict[str, Any], *, failures_seen: int) -> str:
    if result.get("status") != "error":
        return "continue"
    if result.get("code") == "NOTE_VALIDATION_FAILED" and failures_seen == 0:
        return "repair"
    return "fail"


def screenshot_directory(result: dict[str, Any]) -> str:
    screenshots = result.get("screenshots")
    if isinstance(screenshots, list):
        for screenshot in screenshots:
            if isinstance(screenshot, dict) and screenshot.get("path"):
                return str(Path(str(screenshot["path"])).parent)
            if isinstance(screenshot, str) and screenshot:
                return str(Path(screenshot).parent)
    manifest = str(result.get("manifest") or "")
    return str(Path(manifest).parent) if manifest else ""


def advance_stage(current: str, candidate: str) -> str:
    candidate = normalize_stage(candidate)
    if not candidate:
        return current
    if candidate in {"failed", "cancelled"}:
        return candidate
    if candidate not in STAGE_ORDER:
        return current
    if current not in STAGE_ORDER:
        return candidate
    return candidate if STAGE_ORDER.index(candidate) >= STAGE_ORDER.index(current) else current


def read_progress_events(path: Path, seen: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], seen
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], seen
    events: list[dict[str, Any]] = []
    for line in lines[seen:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events, len(lines)


class NativeHost:
    def __init__(self, *, native_output: bool = True, persist_log: bool = True) -> None:
        self.native_output = native_output
        self.persist_log = persist_log
        self.log_path = LOG_PATH
        self.write_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.latest_by_request: dict[str, dict[str, Any]] = {}
        self.cancel_event = threading.Event()
        self.process: subprocess.Popen[str] | None = None
        self.job_thread: threading.Thread | None = None
        self.active_request_id = ""
        self.latest_request_id = ""
        if self.persist_log:
            recovered = interrupted_job_from_log(self.log_path)
            if recovered:
                self.send(recovered)

    def send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.persist_log:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"time": time.time(), **payload}, ensure_ascii=False) + "\n")
        request_id = str(payload.get("request_id") or "")
        if request_id:
            with self.state_lock:
                self.latest_by_request[request_id] = dict(payload)
                if payload.get("stage") or payload.get("type") in {"accepted", "progress", "pipeline_result", "complete", "cancelled"}:
                    self.latest_request_id = request_id
        if not self.native_output:
            return
        with self.write_lock:
            sys.stdout.buffer.write(struct.pack("<I", len(data)))
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def handle(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        request_id = str(message.get("request_id") or "")
        try:
            if message_type == "get_version":
                self.send({"type": "version", "request_id": request_id, "version": HOST_VERSION})
            elif message_type == "list_models":
                executable = shutil.which("opencode") or "opencode"
                result = subprocess.run(
                    [executable, "models"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                models = [line.strip() for line in result.stdout.splitlines() if MODEL_PATTERN.fullmatch(line.strip())]
                self.send({"type": "models", "request_id": request_id, "models": models})
            elif message_type == "configure":
                model = str(message.get("model") or "")
                if not MODEL_PATTERN.fullmatch(model):
                    raise ValueError("模型名称无效。")
                save_provider_key(model.split("/", 1)[0], str(message.get("api_key") or ""))
                self.send({"type": "configured", "request_id": request_id, "model": model})
            elif message_type == "open_note":
                vault = Path(str(message.get("vault") or DEFAULT_VAULT))
                open_note_in_obsidian(str(message.get("note_path") or ""), vault)
                self.send({"type": "opened", "request_id": request_id})
            elif message_type == "start_job":
                if self.job_thread and self.job_thread.is_alive():
                    active = self.status_for(self.active_request_id)
                    self.send(
                        {
                            "type": "attached",
                            "request_id": request_id,
                            "active_request_id": self.active_request_id,
                            "status": "running",
                            "stage": active.get("stage", "starting"),
                            "message": "已重新连接到正在运行的视频任务。",
                            "elapsed_seconds": active.get("elapsed_seconds", 0),
                            "progress_percent": active.get("progress_percent", 6),
                            "current": active.get("current"),
                            "total": active.get("total"),
                            "video_title": active.get("video_title", ""),
                            "video_url": active.get("video_url", ""),
                            "output_dir": active.get("output_dir", ""),
                        }
                    )
                    return
                self.cancel_event.clear()
                if self.persist_log:
                    self.log_path.unlink(missing_ok=True)
                self.active_request_id = request_id
                self.job_thread = threading.Thread(target=self.run_job, args=(request_id, message), daemon=True)
                self.job_thread.start()
                self.send(
                    {
                        "type": "accepted",
                        "request_id": request_id,
                        "status": "running",
                        "stage": "queued",
                        "message": "任务已交给现有 /video-note Skill。",
                        "elapsed_seconds": 0,
                        "progress_percent": 2,
                        "video_title": str(message.get("video_title") or "当前 YouTube 视频"),
                        "video_url": str(message.get("url") or ""),
                        "output_dir": str(Path(str(message.get("vault") or DEFAULT_VAULT)) / "YouTube video"),
                    }
                )
            elif message_type == "cancel_job":
                self.cancel_event.set()
                self.stop_process()
                job_thread = self.job_thread
                if job_thread and job_thread is not threading.current_thread():
                    job_thread.join(timeout=5)
                    if job_thread.is_alive():
                        raise RuntimeError("CANCEL_TIMEOUT: 旧任务未能在 5 秒内退出，请重启桌面伴侣。")
                self.active_request_id = ""
                self.send({"type": "cancelled", "request_id": request_id, "status": "cancelled"})
            else:
                raise ValueError("不支持的 Native Host 消息。")
        except Exception as exc:
            self.send({"type": "error", "request_id": request_id, "status": "error", "message": str(exc)})

    def status_for(self, request_id: str) -> dict[str, Any]:
        with self.state_lock:
            return dict(self.latest_by_request.get(request_id) or {})

    def active_status(self) -> dict[str, Any]:
        if not self.active_request_id:
            return {}
        return self.status_for(self.active_request_id)

    def latest_status(self) -> dict[str, Any]:
        if self.latest_request_id:
            return self.status_for(self.latest_request_id)
        if not self.persist_log or not self.log_path.exists():
            return {}
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("type") in {"complete", "error", "cancelled"}:
                if payload.get("type") == "error":
                    technical = str(payload.get("technical_message") or payload.get("message") or "")
                    code, message = format_user_error(RuntimeError(technical))
                    payload = {**payload, "code": code, "message": message, "technical_message": technical}
                return payload
        return {}

    def stop_process(self) -> None:
        process = self.process
        if not process or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        else:
            process.terminate()

    def run_job(self, request_id: str, message: dict[str, Any]) -> None:
        started = time.monotonic()
        progress_path = self.log_path.parent / f"progress-{request_id}.jsonl"
        progress_path.unlink(missing_ok=True)
        url = str(message.get("url") or "")
        vault = Path(str(message.get("vault") or DEFAULT_VAULT)).expanduser()
        video_title = str(message.get("video_title") or "当前 YouTube 视频")
        task_context = {
            "video_title": video_title,
            "video_url": url,
            "output_dir": str(vault / "YouTube video"),
        }
        try:
            validate_youtube_url(url)
            model = str(message.get("model") or "")
            cookie_count = save_cookie_snapshot(message.get("cookies") or [])
            executable = shutil.which("opencode") or "opencode"
            command = build_opencode_command(opencode=executable, vault=vault, model=model, url=url)
            self.send(
                {
                    "type": "progress",
                    "request_id": request_id,
                    "status": "running",
                    "stage": "credentials",
                    "message": f"已保存 {cookie_count} 个 YouTube Cookie，正在启动现有 /video-note Skill。",
                    "elapsed_seconds": 0,
                    "progress_percent": 4,
                    **task_context,
                }
            )
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            process_environment = os.environ.copy()
            process_environment["YOUTUBE_NOTE_PROGRESS_FILE"] = str(progress_path)
            self.process = subprocess.Popen(
                command,
                cwd=vault,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=process_environment,
            )
            output_queue: queue.Queue[str | None] = queue.Queue()

            def read_output() -> None:
                assert self.process and self.process.stdout
                for output_line in self.process.stdout:
                    output_queue.put(output_line)
                output_queue.put(None)

            threading.Thread(target=read_output, daemon=True).start()
            last_heartbeat = 0.0
            stage = "starting"
            progress_percent = 6
            progress_message = stage_message(stage)
            progress_current: int | None = None
            progress_total: int | None = None
            progress_seen = 0
            final_result: dict[str, Any] = {}
            note_path = ""
            screenshot_count = 0
            screenshot_dir = ""
            validation_failures = 0
            stream_closed = False
            while not stream_closed or self.process.poll() is None:
                elapsed = time.monotonic() - started
                if self.cancel_event.is_set():
                    raise RuntimeError("任务已取消。")
                if elapsed > MAX_JOB_SECONDS:
                    self.stop_process()
                    raise TimeoutError(f"任务超过 {MAX_JOB_SECONDS // 60} 分钟硬时限，已停止在 {stage} 阶段。")
                progress_events, progress_seen = read_progress_events(progress_path, progress_seen)
                for event in progress_events:
                    candidate = normalize_stage(str(event.get("stage") or ""))
                    if candidate == "failed":
                        code = str(event.get("code") or "PIPELINE_FAILED")
                        if code == "NOTE_VALIDATION_FAILED" and validation_failures == 0:
                            candidate = "validation"
                            progress_message = "首次质量校验未通过，正在进行唯一一次笔记修正。"
                        else:
                            raise RuntimeError(f"{code}: {event.get('message') or '任务失败。'}")
                    stage = advance_stage(stage, candidate)
                    progress_percent = max(progress_percent, int(event.get("percent") or STAGE_PROGRESS.get(stage, 0)))
                    progress_message = str(event.get("message") or stage_message(stage))
                    progress_current = event.get("current") if isinstance(event.get("current"), int) else progress_current
                    progress_total = event.get("total") if isinstance(event.get("total"), int) else progress_total
                try:
                    line = output_queue.get(timeout=1)
                except queue.Empty:
                    line = ""
                if line is None:
                    stream_closed = True
                elif line:
                    detected_stage = stage_from_output(line)
                    advanced_stage = advance_stage(stage, detected_stage)
                    if advanced_stage != stage:
                        stage = advanced_stage
                        progress_message = stage_message(stage)
                    progress_percent = max(progress_percent, STAGE_PROGRESS.get(stage, 0))
                    for result in pipeline_results(line):
                        final_result = result
                        if result.get("title"):
                            video_title = str(result["title"])
                            task_context["video_title"] = video_title
                        result_stage = normalize_stage(str(result.get("stage") or stage))
                        if result.get("status") == "error":
                            action = validation_error_action(result, failures_seen=validation_failures)
                            if action == "repair":
                                validation_failures += 1
                                stage = advance_stage(stage, "validation")
                                progress_percent = max(progress_percent, STAGE_PROGRESS["validation"])
                                progress_message = "首次质量校验未通过，正在进行唯一一次笔记修正。"
                                self.send(
                                    {
                                        "type": "pipeline_result",
                                        "request_id": request_id,
                                        "status": "running",
                                        "stage": stage,
                                        "result": result,
                                        "message": progress_message,
                                        "elapsed_seconds": int(elapsed),
                                        "progress_percent": progress_percent,
                                        "current": progress_current,
                                        "total": progress_total,
                                        **task_context,
                                    }
                                )
                                continue
                            code = str(result.get("code") or "PIPELINE_FAILED")
                            raise RuntimeError(f"{code}: {result.get('message') or '任务失败。'}")
                        stage = advance_stage(stage, result_stage)
                        if result.get("note"):
                            note_path = str(result["note"])
                        screenshots = result.get("screenshots")
                        if isinstance(screenshots, list):
                            screenshot_count = max(screenshot_count, len(screenshots))
                        if isinstance(result.get("screenshot_count"), int):
                            screenshot_count = max(screenshot_count, int(result["screenshot_count"]))
                        screenshot_dir = screenshot_directory(result) or screenshot_dir
                        self.send(
                            {
                                "type": "pipeline_result",
                                "request_id": request_id,
                                "status": "running",
                                "stage": stage,
                                "result": result,
                                "message": progress_message,
                                "elapsed_seconds": int(elapsed),
                                "progress_percent": progress_percent,
                                "current": progress_current,
                                "total": progress_total,
                                "screenshot_dir": screenshot_dir,
                                **task_context,
                            }
                        )
                if elapsed - last_heartbeat >= 5:
                    last_heartbeat = elapsed
                    self.send(
                        {
                            "type": "progress",
                            "request_id": request_id,
                            "status": "running",
                            "stage": stage,
                            "message": progress_message,
                            "elapsed_seconds": int(elapsed),
                            "progress_percent": progress_percent,
                            "current": progress_current,
                            "total": progress_total,
                            **task_context,
                        }
                    )
            returncode = self.process.wait(timeout=5)
            if returncode != 0 or final_result.get("status") == "error":
                code = str(final_result.get("code") or "OPENCODE_FAILED")
                message_text = str(final_result.get("message") or f"OpenCode 退出码：{returncode}")
                raise RuntimeError(f"{code}: {message_text}")
            if not note_path:
                raise RuntimeError("RESULT_NOTE_MISSING: OpenCode 未返回通过校验的笔记路径。")
            note_opened = False
            open_warning = ""
            if bool(message.get("auto_open_note", True)):
                try:
                    open_note_in_obsidian(note_path, vault)
                    note_opened = True
                except Exception as open_error:
                    open_warning = str(open_error)
            self.send(
                {
                    "type": "complete",
                    "request_id": request_id,
                    "status": "ok",
                    "stage": "complete",
                    "note_path": note_path,
                    "note_opened": note_opened,
                    "open_warning": open_warning,
                    "screenshot_count": screenshot_count,
                    "screenshot_dir": screenshot_dir,
                    "elapsed_seconds": int(time.monotonic() - started),
                    "progress_percent": 100,
                    **task_context,
                }
            )
        except Exception as exc:
            self.stop_process()
            if self.cancel_event.is_set():
                self.send(
                    {
                        "type": "cancelled",
                        "request_id": request_id,
                        "status": "cancelled",
                        "stage": "cancelled",
                        "message": "任务已强制停止。",
                        "elapsed_seconds": int(time.monotonic() - started),
                        "progress_percent": progress_percent if "progress_percent" in locals() else 0,
                        "current": progress_current if "progress_current" in locals() else None,
                        "total": progress_total if "progress_total" in locals() else None,
                        **task_context,
                    }
                )
            else:
                error_code, user_message = format_user_error(exc)
                self.send(
                    {
                        "type": "error",
                        "request_id": request_id,
                        "status": "error",
                        "stage": "failed",
                        "code": error_code,
                        "message": user_message,
                        "technical_message": str(exc),
                        "elapsed_seconds": int(time.monotonic() - started),
                        "progress_percent": progress_percent if "progress_percent" in locals() else 0,
                        "current": progress_current if "progress_current" in locals() else None,
                        "total": progress_total if "progress_total" in locals() else None,
                        **task_context,
                    }
                )
        finally:
            self.process = None
            progress_path.unlink(missing_ok=True)
            if self.active_request_id == request_id:
                self.active_request_id = ""


def read_message() -> dict[str, Any] | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return None
    length = struct.unpack("<I", raw_length)[0]
    if length <= 0 or length > 8 * 1024 * 1024:
        raise ValueError("Native Messaging 消息长度无效。")
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length:
        raise EOFError("Native Messaging 消息不完整。")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Native Messaging 消息必须是对象。")
    return value


class CompanionHttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, CompanionRequestHandler)
        self.bridge = NativeHost(native_output=False)


class CompanionRequestHandler(http.server.BaseHTTPRequestHandler):
    server: CompanionHttpServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return not origin or origin == EXTENSION_ORIGIN

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin == EXTENSION_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._allowed_origin():
            self._send_json(403, {"type": "error", "message": "Extension origin is not allowed."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", EXTENSION_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok", "host": HOST_NAME, "version": HOST_VERSION})
            return
        if not self._allowed_origin():
            self._send_json(403, {"type": "error", "message": "Extension origin is not allowed."})
            return
        if parsed.path == "/status":
            request_id = parse_qs(parsed.query).get("request_id", [""])[0]
            payload = self.server.bridge.status_for(request_id)
            self._send_json(200, payload or {"type": "status", "status": "idle", "request_id": request_id})
            return
        if parsed.path == "/active":
            payload = self.server.bridge.active_status()
            self._send_json(200, payload or {"type": "status", "status": "idle"})
            return
        if parsed.path == "/latest":
            payload = self.server.bridge.latest_status()
            self._send_json(200, payload or {"type": "status", "status": "idle"})
            return
        self._send_json(404, {"type": "error", "message": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rpc":
            self._send_json(404, {"type": "error", "message": "Not found."})
            return
        if not self._allowed_origin():
            self._send_json(403, {"type": "error", "message": "Extension origin is not allowed."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024 * 1024:
                raise ValueError("Companion request size is invalid.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Companion request must be an object.")
            request_id = str(payload.get("request_id") or "")
            self.server.bridge.handle(payload)
            response = self.server.bridge.status_for(request_id)
            self._send_json(200, response or {"type": "accepted", "request_id": request_id})
        except Exception as exc:
            self._send_json(400, {"type": "error", "status": "error", "message": str(exc)})


def create_http_server(port: int = COMPANION_PORT) -> CompanionHttpServer:
    return CompanionHttpServer(("127.0.0.1", port))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=COMPANION_PORT)
    # Chrome appends the caller origin and --parent-window on Windows.
    args, _chrome_arguments = parser.parse_known_args()
    if args.version:
        print(f"youtube-reader-host {HOST_VERSION}")
        return 0
    if args.self_test:
        validate_youtube_url("https://www.youtube.com/watch?v=XWlz2zfBL7E")
        print(json.dumps({"status": "ok", "host": HOST_NAME, "version": HOST_VERSION}))
        return 0
    if args.serve:
        server = create_http_server(args.port)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    host = NativeHost()
    while True:
        message = read_message()
        if message is None:
            break
        host.handle(message)
    if host.job_thread and host.job_thread.is_alive():
        host.cancel_event.set()
        host.stop_process()
        host.job_thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
