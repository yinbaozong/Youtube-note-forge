#!/usr/bin/env python3
"""
Extract video learning material for Obsidian.

The pipeline is intentionally layered:
1. Fetch metadata with yt-dlp.
2. Prefer platform subtitles/captions.
3. Optionally run local ASR only when explicitly requested.
4. Save a cover and write an Obsidian Markdown source note.

Targeted article screenshots are handled by extract_frames.py after an agent
has mapped the transcript to the note outline. This script never opens a
browser and never downloads video merely to create screenshots.
"""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from video_common import Deadline, PipelineError, VERSION, emit_progress, emit_result, version_text


DEFAULT_LANGS = "zh-Hans,zh-CN,zh,zh-TW,en.*,en,ja.*,all"
OBSIDIAN_LINK_SPECIAL_CHARS = r"[#^[\]]"
SUBTITLE_ATTEMPTS = 3
# YouTube throttles the caption endpoint for far longer than a network blip, so a
# rate-limited retry has to wait long enough to leave the throttle window.
SUBTITLE_RETRY_BACKOFF_SECONDS = (2.0, 4.0)
SUBTITLE_RATE_LIMIT_BACKOFF_SECONDS = (15.0, 30.0)
RATE_LIMIT_MARKERS = ("429", "too many requests")
RATE_LIMIT_ACTION = (
    "YouTube 正在限流该视频的字幕接口，任务已自动等待并重试仍未通过。"
    "请等待几分钟后再重试；短时间内连续重试会延长限流，改用 ASR 也会被同一限流拦住。"
)
# YouTube only returns caption tracks for some player clients. When the default
# client set is throttled, yt-dlp downgrades to a client that reports zero
# captions, which used to look identical to a video that genuinely has none.
CAPTION_REPROBE_ARGS = ["--extractor-args", "youtube:player_client=web_safari,web,default"]
CREDENTIAL_LABEL_RE = re.compile(r"^\[(?:cookies file|no cookies)\b[^\]]*\]$")
IMPERSONATE_UNAVAILABLE_MARKER = "no impersonate target is available"
IMPERSONATE_ACTION = (
    "当前 yt-dlp 缺少浏览器指纹依赖，YouTube 字幕接口会间歇性拒绝请求。"
    '请运行 pip install "yt-dlp[default,curl-cffi]" 后重试。'
)
TRANSIENT_SUBTITLE_MARKERS = (
    "429",
    "too many requests",
    "403",
    "forbidden",
    "timed out",
    "timeout",
    "temporarily",
    "connection reset",
    "connection aborted",
    "remote end closed",
)
SKILL_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = Path.home() / ".config" / "opencode" / "credentials" / "youtube-transcript"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_COOKIES_PATH = CREDENTIALS_DIR / "cookies.youtube.txt"
BILIBILI_COOKIES_PATH = CREDENTIALS_DIR / "cookies.bilibili.txt"


@dataclass
class TranscriptEntry:
    start: float
    end: float | None
    text: str


@dataclass
class SubtitleChoice:
    lang: str
    source: str


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    credential_label: str


def sanitize_filename(title: str, max_len: int = 100) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip()
    clean = re.sub(OBSIDIAN_LINK_SPECIAL_CHARS, "", clean)
    clean = re.sub(r"\s+", " ", clean)
    clean = clean.strip(". ")
    return (clean or "video")[:max_len]


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def suggested_note_filename(title: str, max_len: int = 120) -> str:
    if contains_cjk(title):
        return sanitize_filename(title, max_len=max_len)
    english = sanitize_filename(title, max_len=max_len - 6)
    return sanitize_filename(f"待命名 - {english}", max_len=max_len)


def is_youtube_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "bilibili.com" in host or "b23.tv" in host:
        return "bilibili"
    return "generic"


def platform_cookie_path(url: str) -> Path | None:
    platform = video_platform(url)
    if platform == "youtube":
        return YOUTUBE_COOKIES_PATH
    if platform == "bilibili":
        return BILIBILI_COOKIES_PATH
    return None


def cookie_backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".lastgood")


def cookie_arg_path(args: list[str]) -> Path | None:
    for idx, value in enumerate(args):
        if value == "--cookies" and idx + 1 < len(args):
            return Path(args[idx + 1])
    return None


def remember_working_cookie(args: list[str]) -> None:
    cookie_path = cookie_arg_path(args)
    if cookie_path and cookie_path.exists() and cookie_path.stat().st_size > 100:
        try:
            shutil.copy2(cookie_path, cookie_backup_path(cookie_path))
        except Exception:
            pass


def snapshot_cookie(args: list[str]) -> tuple[Path, bytes] | None:
    cookie_path = cookie_arg_path(args)
    if cookie_path and cookie_path.exists():
        try:
            return cookie_path, cookie_path.read_bytes()
        except Exception:
            return None
    return None


def restore_cookie(snapshot: tuple[Path, bytes] | None) -> None:
    if not snapshot:
        return
    path, data = snapshot
    try:
        path.write_bytes(data)
    except Exception:
        pass


def fallback_cookie_args(args: list[str]) -> list[str] | None:
    cookie_path = cookie_arg_path(args)
    if not cookie_path:
        return None
    backup = cookie_backup_path(cookie_path)
    if not backup.exists() or backup.stat().st_size <= 100:
        return None
    try:
        if cookie_path.exists() and cookie_path.read_bytes() == backup.read_bytes():
            return None
    except Exception:
        pass
    fallback = list(args)
    for idx, value in enumerate(fallback):
        if value == "--cookies" and idx + 1 < len(fallback):
            fallback[idx + 1] = str(backup)
            return fallback
    return None


def seconds_to_timestamp(seconds: float | int | None) -> str:
    if seconds is None:
        return "00:00:00"
    seconds = max(float(seconds), 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def seconds_to_srt_timestamp(seconds: float | int | None) -> str:
    if seconds is None:
        seconds = 0
    seconds = max(float(seconds), 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        s += 1
        ms -= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(value)


def yaml_scalar(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value), ensure_ascii=False)


def run_subprocess(args: list[str], *, check: bool = False, timeout: int = 90) -> RunResult:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        result = RunResult(
            args=args,
            returncode=124,
            stdout=stdout,
            stderr=(stderr + f"\nTimed out after {timeout} seconds.").strip(),
            credential_label="",
        )
        if check:
            raise RuntimeError(command_failure_message(result))
        return result
    result = RunResult(args=args, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr, credential_label="")
    if check and completed.returncode != 0:
        raise RuntimeError(command_failure_message(result))
    return result


def command_failure_message(result: RunResult) -> str:
    cmd = " ".join(result.args)
    stderr = result.stderr.strip()
    stdout = result.stdout.strip()
    detail = stderr or stdout or "(no output)"
    return f"Command failed ({result.returncode}): {cmd}\n{detail}"


class YtDlp:
    def __init__(
        self,
        cookies: str | None,
        no_cookies: bool,
        proxy: str | None,
        auto_cookies_path: Path | None = None,
        deadline: Deadline | None = None,
    ):
        self.proxy = proxy
        self.auto_cookies_path = auto_cookies_path
        self.deadline = deadline
        self.modes: list[tuple[str, list[str]]] = []
        if no_cookies:
            self.modes.append(("no cookies", []))
        elif cookies:
            self.modes.append((f"cookies file {cookies}", ["--cookies", cookies]))
        else:
            if auto_cookies_path and auto_cookies_path.exists():
                self.modes.append((f"cookies file {auto_cookies_path}", ["--cookies", str(auto_cookies_path)]))
            else:
                self.modes.append(("no cookies", []))

    def run(self, args: list[str], *, purpose: str, check: bool = True, timeout: int = 90) -> RunResult:
        failures: list[str] = []
        for label, credential_args in self.modes:
            cookie_snapshot = snapshot_cookie(credential_args)
            proxy_args = ["--proxy", self.proxy] if self.proxy else []
            full_args = [
                "yt-dlp",
                "--windows-filenames",
                "--js-runtimes",
                "node",
                "--socket-timeout",
                "20",
                "--retries",
                "1",
                "--fragment-retries",
                "1",
                *proxy_args,
                *credential_args,
                *args,
            ]
            effective_timeout = self.deadline.timeout_for(timeout) if self.deadline else timeout
            result = run_subprocess(full_args, timeout=effective_timeout)
            result.credential_label = label
            if result.returncode == 0:
                remember_working_cookie(credential_args)
                return result
            restore_cookie(cookie_snapshot)
            failures.append(f"[{label}]\n{result.stderr.strip() or result.stdout.strip() or '(no output)'}")

            if is_cookie_auth_failure(result.stderr + result.stdout):
                backup_args = fallback_cookie_args(credential_args)
                if backup_args:
                    backup_snapshot = snapshot_cookie(backup_args)
                    backup_full_args = [
                        "yt-dlp",
                        "--windows-filenames",
                        "--js-runtimes",
                        "node",
                        "--socket-timeout",
                        "20",
                        "--retries",
                        "2",
                        "--fragment-retries",
                        "2",
                        *proxy_args,
                        *backup_args,
                        *args,
                    ]
                    backup_timeout = self.deadline.timeout_for(timeout) if self.deadline else timeout
                    backup_result = run_subprocess(backup_full_args, timeout=backup_timeout)
                    backup_result.credential_label = label + " lastgood"
                    if backup_result.returncode == 0:
                        original_cookie = cookie_arg_path(credential_args)
                        backup_cookie = cookie_arg_path(backup_args)
                        if original_cookie and backup_cookie and backup_cookie.exists():
                            try:
                                shutil.copy2(backup_cookie, original_cookie)
                            except Exception:
                                pass
                        remember_working_cookie(backup_args)
                        return backup_result
                    restore_cookie(backup_snapshot)
                    failures.append(
                        f"[{label} lastgood]\n"
                        f"{backup_result.stderr.strip() or backup_result.stdout.strip() or '(no output)'}"
                    )

            # Avoid slow browser-cookie retries for normal public-video misses.
            if label == "no cookies" and not should_retry_with_cookies(result.stderr + result.stdout):
                break

        message = f"yt-dlp failed while trying to {purpose}.\n\n" + "\n\n".join(failures)
        combined = "\n".join(failures).lower()
        if self.auto_cookies_path == YOUTUBE_COOKIES_PATH:
            if YOUTUBE_COOKIES_PATH.exists() and is_cookie_auth_failure(combined):
                message += (
                    f"\n\nSaved YouTube cookies were rejected. Request a fresh YouTube cookie once and overwrite: "
                    f"{YOUTUBE_COOKIES_PATH}"
                )
            elif not YOUTUBE_COOKIES_PATH.exists() and is_cookie_auth_failure(combined):
                message += (
                    f"\n\nYouTube authentication may be required. If the user provides cookies, save them permanently to: "
                    f"{YOUTUBE_COOKIES_PATH}"
                )
        elif self.auto_cookies_path == BILIBILI_COOKIES_PATH:
            if BILIBILI_COOKIES_PATH.exists():
                if "412" in combined:
                    message += (
                        "\n\nA saved Bilibili cookie already exists. HTTP 412 is an anti-crawling/network response; "
                        "do not ask for the same cookie again solely because of 412."
                    )
                elif is_cookie_auth_failure(combined):
                    message += (
                        f"\n\nSaved Bilibili cookies were rejected. Request a fresh Bilibili cookie once and overwrite: "
                        f"{BILIBILI_COOKIES_PATH}"
                    )
            elif "412" in combined:
                message += (
                    f"\n\nBilibili public access returned HTTP 412. A one-time Bilibili cookie may help; "
                    f"if the user provides it, save it permanently to: {BILIBILI_COOKIES_PATH}"
                )
        if check:
            raise RuntimeError(message)
        return RunResult(args=["yt-dlp", *args], returncode=1, stdout="", stderr=message, credential_label="failed")


def should_retry_with_cookies(output: str) -> bool:
    text = output.lower()
    retry_markers = (
        "sign in",
        "login",
        "cookies",
        "confirm",
        "bot",
        "private",
        "members-only",
        "age-restricted",
        "not available",
        "forbidden",
        "403",
    )
    return any(marker in text for marker in retry_markers)


def is_cookie_auth_failure(output: str) -> bool:
    text = output.lower()
    markers = (
        "provided youtube account cookies are no longer valid",
        "sign in to confirm",
        "login required",
        "authentication required",
        "cookies are no longer valid",
        "cookie is no longer valid",
        "account cookies",
    )
    return any(marker in text for marker in markers)


def is_transient_subtitle_failure(output: str) -> bool:
    text = output.lower()
    return any(marker in text for marker in TRANSIENT_SUBTITLE_MARKERS)


def is_rate_limited(output: str) -> bool:
    text = output.lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def subtitle_backoff_seconds(output: str, attempt: int) -> float:
    """Wait long enough to leave a throttle window without stalling the pipeline."""
    schedule = SUBTITLE_RATE_LIMIT_BACKOFF_SECONDS if is_rate_limited(output) else SUBTITLE_RETRY_BACKOFF_SECONDS
    return schedule[min(attempt, len(schedule)) - 1]


def summarize_ytdlp_failure(raw: str, *, limit: int = 300) -> str:
    """Keep the actionable yt-dlp diagnosis instead of credential labels and warnings.

    yt-dlp prints benign warnings before the fatal line, and the credential label
    can be a long absolute path. Truncating the raw blob therefore used to drop the
    only line that explains the failure.
    """
    errors: list[str] = []
    warnings: list[str] = []
    others: list[str] = []
    for raw_line in (raw or "").splitlines():
        line = raw_line.strip()
        if not line or CREDENTIAL_LABEL_RE.match(line):
            continue
        lowered = line.lower()
        if lowered.startswith("error:") or "http error" in lowered:
            errors.append(line)
        elif lowered.startswith("warning:"):
            warnings.append(line)
        else:
            others.append(line)
    detail = " ".join(errors or others or warnings)
    detail = re.sub(r"https?://\S+", "[URL]", detail)
    return re.sub(r"\s+", " ", detail).strip()[:limit]


def extract_json(stdout: str) -> dict:
    stdout = stdout.strip()
    if not stdout:
        raise RuntimeError("yt-dlp returned empty JSON output")
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    return json.loads(stdout)


def extract_metadata(runner: YtDlp, url: str) -> dict:
    result = runner.run(["--dump-single-json", "--no-download", "--no-playlist", url], purpose="extract metadata")
    return extract_json(result.stdout)


def lang_patterns(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def pattern_matches(pattern: str, lang: str) -> bool:
    if pattern == "all":
        return True
    if pattern == lang:
        return True
    if "*" in pattern:
        regex = "^" + re.escape(pattern).replace("\\*", ".*") + "$"
        return re.match(regex, lang, flags=re.IGNORECASE) is not None
    return False


def choose_subtitle(metadata: dict, preferred_langs: str) -> SubtitleChoice | None:
    manual = metadata.get("subtitles") or {}
    automatic = metadata.get("automatic_captions") or {}
    patterns = lang_patterns(preferred_langs)

    for pattern in patterns:
        for source, subtitles in (("manual", manual), ("automatic", automatic)):
            for lang in subtitles.keys():
                if pattern_matches(pattern, lang):
                    return SubtitleChoice(lang=lang, source=source)

    for source, subtitles in (("manual", manual), ("automatic", automatic)):
        if subtitles:
            return SubtitleChoice(lang=next(iter(subtitles.keys())), source=source)
    return None


def find_vtt_files(directory: Path, video_id: str) -> list[Path]:
    files = list(directory.glob(f"{video_id}*.vtt"))
    if not files:
        files = list(directory.glob("*.vtt"))
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


def download_subtitle(runner: YtDlp, url: str, metadata: dict, choice: SubtitleChoice, tmp_dir: Path) -> tuple[Path | None, str]:
    output_template = str(tmp_dir / "%(id)s.%(ext)s")
    args = [
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        choice.lang,
        "--sub-format",
        "vtt/best",
        "--skip-download",
        "--no-playlist",
        "-o",
        output_template,
        url,
    ]
    last_error = ""
    for attempt in range(1, SUBTITLE_ATTEMPTS + 1):
        result = runner.run(args, purpose=f"download {choice.lang} subtitles", check=False, timeout=45)
        if result.returncode == 0:
            files = find_vtt_files(tmp_dir, metadata.get("id", ""))
            if files:
                return files[0], ""
            last_error = "yt-dlp reported success but no .vtt subtitle file was created"
        else:
            last_error = result.stderr
        # Throttling and TLS resets are the common cause here, so retry those
        # instead of reporting a video with captions as having none.
        if attempt >= SUBTITLE_ATTEMPTS or not is_transient_subtitle_failure(last_error):
            break
        backoff = subtitle_backoff_seconds(last_error, attempt)
        # Sleeping past the deadline would only turn a throttle into a timeout.
        if runner.deadline and runner.deadline.remaining() <= backoff + 15:
            break
        emit_progress(
            "materials",
            f"YouTube 暂时限流字幕接口，等待 {int(backoff)} 秒后重试。",
            percent=18,
        )
        print(f"Subtitle download throttled; retrying in {int(backoff)}s", flush=True)
        time.sleep(backoff)
    return None, last_error


def reprobe_subtitle_choice(runner: YtDlp, url: str, preferred_langs: str) -> SubtitleChoice | None:
    """Ask a caption-capable player client before declaring a video subtitle-free."""
    result = runner.run(
        [*CAPTION_REPROBE_ARGS, "--dump-single-json", "--no-download", "--no-playlist", url],
        purpose="re-probe subtitle tracks",
        check=False,
        timeout=45,
    )
    if result.returncode != 0:
        return None
    try:
        metadata = extract_json(result.stdout)
    except (json.JSONDecodeError, RuntimeError):
        return None
    return choose_subtitle(metadata, preferred_langs)


def transcript_unavailable_error(choice: SubtitleChoice | None, subtitle_error: str) -> PipelineError:
    """Preserve the difference between absent subtitles and a failed download/parse."""
    if choice and subtitle_error.strip():
        detail = summarize_ytdlp_failure(subtitle_error)
        if is_rate_limited(subtitle_error):
            # ASR would download audio from the same throttled host, so waiting is
            # the only remedy that actually helps here.
            action = RATE_LIMIT_ACTION
        else:
            action = "请稍后重试；如接受下载音频和更长耗时，可在本次任务中明确允许 ASR。"
        if IMPERSONATE_UNAVAILABLE_MARKER in subtitle_error.lower():
            action = f"{IMPERSONATE_ACTION} {action}"
        return PipelineError(
            "SUBTITLE_DOWNLOAD_FAILED",
            "transcript",
            f"检测到 {choice.lang} {choice.source} 字幕，但字幕下载失败：{detail}",
            action=action,
        )
    if choice:
        return PipelineError(
            "SUBTITLE_PARSE_FAILED",
            "transcript",
            f"已下载 {choice.lang} {choice.source} 字幕，但没有解析出可用文本。",
            action="请稍后重试；如接受下载音频和更长耗时，可在本次任务中明确允许 ASR。",
        )
    return PipelineError(
        "SUBTITLE_UNAVAILABLE",
        "transcript",
        "该视频没有可用字幕，且未明确允许 ASR。",
        action="如接受下载音频和更长耗时，请在本次任务中明确允许 ASR。",
    )


def clean_vtt_text(line: str) -> str:
    line = html.unescape(line)
    line = re.sub(r"<\d\d:\d\d:\d\d\.\d+>", "", line)
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def parse_vtt(vtt_path: Path) -> list[TranscriptEntry]:
    content = vtt_path.read_text(encoding="utf-8", errors="replace")
    entries: list[TranscriptEntry] = []
    current_start: float | None = None
    current_end: float | None = None
    current_text: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, current_text
        text = clean_vtt_text(" ".join(current_text))
        if current_start is not None and text:
            entries.append(TranscriptEntry(start=current_start, end=current_end, text=text))
        current_start = None
        current_end = None
        current_text = []

    for raw_line in content.splitlines():
        line = raw_line.strip("\ufeff").strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE", "STYLE", "REGION")):
            continue
        if "-->" in line:
            flush()
            left, right = line.split("-->", 1)
            current_start = parse_timestamp(left)
            current_end = parse_timestamp(right.split()[0])
            continue
        if current_start is None:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        cleaned = clean_vtt_text(line)
        if cleaned:
            current_text.append(cleaned)
    flush()
    return deduplicate_entries(entries)


def deduplicate_entries(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    if not entries:
        return []
    result: list[TranscriptEntry] = []
    previous_text = ""
    for idx, entry in enumerate(entries):
        text = re.sub(r"\s+", " ", entry.text).strip()
        if not text:
            continue
        is_prefix = False
        for next_entry in entries[idx + 1 : idx + 5]:
            next_text = re.sub(r"\s+", " ", next_entry.text).strip()
            if next_text.startswith(text) and len(next_text) > len(text):
                is_prefix = True
                break
        if is_prefix or text == previous_text:
            continue
        result.append(TranscriptEntry(start=entry.start, end=entry.end, text=text))
        previous_text = text
    return result


def download_audio(runner: YtDlp, url: str, tmp_dir: Path) -> Path:
    output_template = str(tmp_dir / "audio.%(ext)s")
    result = runner.run(
        ["-f", "bestaudio/best", "--no-playlist", "-o", output_template, url],
        purpose="download audio for ASR",
    )
    candidates = [p for p in tmp_dir.glob("audio.*") if p.is_file()]
    if not candidates:
        raise RuntimeError("Audio download succeeded but no audio file was created.\n" + result.stderr)
    return max(candidates, key=lambda p: p.stat().st_size)


def transcribe_audio(audio_path: Path, model_name: str) -> tuple[list[TranscriptEntry], str]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "No subtitles were available and faster-whisper is not installed.\n"
            "Install the ASR fallback with:\n"
            "  python -m pip install faster-whisper imageio-ffmpeg\n"
            f"Import error: {exc}"
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio_path), vad_filter=True, beam_size=5)
    entries = [
        TranscriptEntry(start=float(seg.start), end=float(seg.end), text=re.sub(r"\s+", " ", seg.text).strip())
        for seg in segments
        if seg.text and seg.text.strip()
    ]
    language = getattr(info, "language", "") or "unknown"
    return entries, f"asr:faster-whisper:{model_name}:{language}"


def best_thumbnail(metadata: dict) -> str | None:
    thumbnails = metadata.get("thumbnails") or []
    if thumbnails:
        sorted_thumbs = sorted(thumbnails, key=lambda item: (item.get("width") or 0) * (item.get("height") or 0), reverse=True)
        for thumb in sorted_thumbs:
            if thumb.get("url"):
                return thumb["url"]
    return metadata.get("thumbnail")


def download_url(url: str, target: Path) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            target.write_bytes(response.read())
        return target.exists() and target.stat().st_size > 0
    except Exception:
        return False


def extension_from_url(url: str, default: str = ".jpg") -> str:
    path = urllib.parse.urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return default


def resolve_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def frame_times(metadata: dict, max_frames: int, transcript_entries: list[TranscriptEntry] | None = None) -> list[float]:
    duration = float(metadata.get("duration") or 0)
    chapters = metadata.get("chapters") or []
    times: list[float] = []
    for chapter in chapters:
        start = chapter.get("start_time")
        end = chapter.get("end_time")
        if start is not None and float(start) > 2:
            start_value = float(start)
            end_value = float(end) if end is not None else start_value + 8
            times.append(min(start_value + 4, max(start_value, end_value - 1)))
    if len(times) < max_frames and transcript_entries:
        usable_entries = [entry for entry in transcript_entries if entry.start > 2]
        if usable_entries:
            needed = max_frames - len(times)
            step = len(usable_entries) / (needed + 1)
            times.extend(usable_entries[min(int(step * (idx + 1)), len(usable_entries) - 1)].start for idx in range(needed))
    if not times and duration > 0:
        step = duration / (max_frames + 1)
        times = [step * (idx + 1) for idx in range(max_frames)]

    unique: list[float] = []
    for value in sorted(times):
        if not unique or value - unique[-1] >= 12:
            unique.append(value)
    if len(unique) <= max_frames:
        return unique
    step = len(unique) / max_frames
    return [unique[min(int(step * idx), len(unique) - 1)] for idx in range(max_frames)]


def automatic_frame_limit(metadata: dict, requested_max: int) -> int:
    duration = float(metadata.get("duration") or 0)
    if duration <= 10 * 60:
        suggested = 5
    elif duration <= 30 * 60:
        suggested = 8
    elif duration <= 60 * 60:
        suggested = 10
    else:
        suggested = 12
    chapters = len(metadata.get("chapters") or [])
    if chapters:
        suggested = max(suggested, min(chapters, 12))
    return max(1, min(requested_max, suggested))


def select_remote_frame_source(metadata: dict) -> str | None:
    formats = [
        item
        for item in (metadata.get("formats") or [])
        if item.get("url")
        and item.get("vcodec") != "none"
        and item.get("height")
        and int(item["height"]) <= 720
    ]
    if not formats:
        return None
    formats.sort(
        key=lambda item: (
            str(item.get("protocol") or "").startswith("http"),
            item.get("ext") == "mp4",
            int(item.get("height") or 0),
            float(item.get("tbr") or 0),
        ),
        reverse=True,
    )
    return str(formats[0]["url"])


def video_cache_dir(metadata: dict) -> Path:
    video_id = sanitize_filename(str(metadata.get("id") or "video"), max_len=80)
    cache = CACHE_DIR / video_id
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def download_frame_segment(
    runner: YtDlp,
    url: str,
    seconds: float,
    cache_dir: Path,
    ffmpeg: str,
) -> tuple[Path | None, bool, str]:
    key = hashlib.sha1(f"{seconds:.1f}".encode("ascii")).hexdigest()[:10]
    output_template = str(cache_dir / f"segment_{key}.%(ext)s")
    existing = sorted(
        (path for path in cache_dir.glob(f"segment_{key}.*") if path.is_file() and path.stat().st_size > 10_000),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if existing:
        return existing[0], True, ""

    start = max(0, seconds - 3)
    end = seconds + 3
    result = runner.run(
        [
            "--ffmpeg-location",
            ffmpeg,
            "--download-sections",
            f"*{start:.2f}-{end:.2f}",
            "-f",
            "bestvideo[height<=720]/best[height<=720]/bestvideo[height<=480]/best[height<=480]/bestvideo/best",
            "--no-playlist",
            "--no-part",
            "-o",
            output_template,
            url,
        ],
        purpose=f"download screenshot segment at {seconds_to_timestamp(seconds)}",
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip() or "unknown yt-dlp error").splitlines()[-1]
        return None, False, detail
    candidates = sorted(
        (path for path in cache_dir.glob(f"segment_{key}.*") if path.is_file() and path.stat().st_size > 10_000),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    return (candidates[0], False, "") if candidates else (None, False, "yt-dlp created no usable segment file")


def image_quality_score(path: Path) -> float:
    try:
        import numpy as np  # type: ignore
        from PIL import Image, ImageStat  # type: ignore

        with Image.open(path) as image:
            gray = image.convert("L")
            stat = ImageStat.Stat(gray)
            brightness = float(stat.mean[0])
            contrast = float(stat.stddev[0])
            pixels = np.asarray(gray.resize((320, 180)), dtype=np.float32)
            sharpness = float(np.var(np.diff(pixels, axis=0)) + np.var(np.diff(pixels, axis=1)))
        # Keep terminal, slide, and code frames even when their layouts are
        # visually similar. Reject only effectively blank black/white frames.
        if (brightness < 5 or brightness > 250) and contrast < 3:
            return -1
        exposure = max(0.0, 1.0 - abs(brightness - 128) / 128)
        return contrast * 1.5 + min(sharpness, 5000) / 50 + exposure * 20
    except Exception:
        return float(path.stat().st_size)


def extract_representative_frame(ffmpeg: str, segment: Path, target: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="frame-candidates-") as candidate_dir_raw:
        candidate_dir = Path(candidate_dir_raw)
        pattern = candidate_dir / "candidate_%02d.jpg"
        result = run_subprocess(
            [
                ffmpeg,
                "-y",
                "-i",
                str(segment),
                "-vf",
                "fps=1,scale='min(1280,iw)':-2",
                "-q:v",
                "3",
                str(pattern),
            ],
            timeout=60,
        )
        candidates = [path for path in candidate_dir.glob("candidate_*.jpg") if path.stat().st_size > 10_000]
        if result.returncode != 0 or not candidates:
            return False
        best = max(candidates, key=image_quality_score)
        if image_quality_score(best) < 0:
            return False
        shutil.copy2(best, target)
    return target.exists() and target.stat().st_size > 10_000


def extract_remote_representative_frame(ffmpeg: str, source_url: str, seconds: float, target: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="remote-frame-candidates-") as candidate_dir_raw:
        candidate_dir = Path(candidate_dir_raw)
        pattern = candidate_dir / "candidate_%02d.jpg"
        result = run_subprocess(
            [
                ffmpeg,
                "-y",
                "-ss",
                str(max(0, seconds - 2)),
                "-i",
                source_url,
                "-t",
                "4",
                "-vf",
                "fps=1,scale='min(1280,iw)':-2",
                "-q:v",
                "3",
                str(pattern),
            ],
            timeout=90,
        )
        candidates = [path for path in candidate_dir.glob("candidate_*.jpg") if path.stat().st_size > 10_000]
        if result.returncode != 0 or not candidates:
            return False
        best = max(candidates, key=image_quality_score)
        if image_quality_score(best) < 0:
            return False
        shutil.copy2(best, target)
    return target.exists() and target.stat().st_size > 10_000


def extract_partial_frames(
    runner: YtDlp,
    url: str,
    ffmpeg: str,
    assets_dir: Path,
    metadata: dict,
    max_frames: int,
    transcript_entries: list[TranscriptEntry],
) -> tuple[list[Path], int, int, int]:
    frames: list[Path] = []
    cache_hits = 0
    downloaded_bytes = 0
    direct_frames = 0
    times = frame_times(metadata, automatic_frame_limit(metadata, max_frames), transcript_entries)
    cache_dir = video_cache_dir(metadata)
    remote_source = select_remote_frame_source(metadata)

    for idx, seconds in enumerate(times, start=1):
        target = assets_dir / f"frame_{idx:03d}_{seconds_to_timestamp(seconds).replace(':', '-')}.jpg"
        if target.exists() and target.stat().st_size > 10_000:
            frames.append(target)
            cache_hits += 1
            print(f"  [{idx}/{len(times)}] Reused screenshot {seconds_to_timestamp(seconds)}")
            continue

        if remote_source:
            print(f"  [{idx}/{len(times)}] Seeking remote 720p source near {seconds_to_timestamp(seconds)}...")
            if extract_remote_representative_frame(ffmpeg, remote_source, seconds, target):
                direct_frames += 1
                frames.append(target)
                continue
            target.unlink(missing_ok=True)
            print(f"  [{idx}/{len(times)}] Direct seek failed; trying a short downloaded segment.")

        print(f"  [{idx}/{len(times)}] Fetching six-second fallback segment near {seconds_to_timestamp(seconds)}...")
        segment, reused, error = download_frame_segment(runner, url, seconds, cache_dir, ffmpeg)
        if not segment:
            print(f"  [{idx}/{len(times)}] Segment unavailable; continuing. {error[:240]}")
            continue
        if reused:
            cache_hits += 1
        else:
            downloaded_bytes += segment.stat().st_size
        if not extract_representative_frame(ffmpeg, segment, target):
            target.unlink(missing_ok=True)
            continue
        frames.append(target)
    return frames, cache_hits, downloaded_bytes, direct_frames


def save_visual_assets(
    runner: YtDlp,
    url: str,
    metadata: dict,
    transcript_entries: list[TranscriptEntry],
    output_file: Path,
    tmp_dir: Path,
    *,
    max_keyframes: int,
    skip_keyframes: bool,
) -> tuple[Path | None, list[Path], list[str]]:
    warnings: list[str] = []
    slug = sanitize_filename(metadata.get("title") or metadata.get("id") or "video", max_len=80)
    assets_dir = output_file.parent / "assets" / slug
    assets_dir.mkdir(parents=True, exist_ok=True)

    cover_path: Path | None = None
    thumb_url = best_thumbnail(metadata)
    if thumb_url:
        cover_path = assets_dir / ("cover" + extension_from_url(thumb_url))
        if not download_url(thumb_url, cover_path):
            warnings.append("Failed to download cover thumbnail.")
            cover_path = None

    frames: list[Path] = []
    if not skip_keyframes and max_keyframes > 0:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            warnings.append("ffmpeg was not found; skipped keyframe extraction. Install imageio-ffmpeg or add ffmpeg to PATH.")
        else:
            frames, cache_hits, downloaded_bytes, direct_frames = extract_partial_frames(
                runner, url, ffmpeg, assets_dir, metadata, max_keyframes, transcript_entries
            )
            print(
                f"  Screenshot phase: {len(frames)} kept, {direct_frames} remote seek(s), "
                f"{cache_hits} cache hit(s), {downloaded_bytes / 1024 / 1024:.1f} MiB fallback segments downloaded."
            )
            if not frames:
                warnings.append("No useful article screenshots were produced from the selected video segments.")
    return cover_path, frames, warnings


def group_by_chapters(entries: list[TranscriptEntry], chapters: list[dict]) -> list[tuple[str, list[TranscriptEntry]]]:
    if not chapters:
        return [("Transcript", entries)]
    chapter_points = [(float(ch.get("start_time") or 0), ch.get("title") or "Chapter") for ch in chapters]
    chapter_points.append((float("inf"), ""))
    grouped: list[tuple[str, list[TranscriptEntry]]] = []
    for idx, (start, title) in enumerate(chapter_points[:-1]):
        end = chapter_points[idx + 1][0]
        chapter_entries = [entry for entry in entries if start <= entry.start < end]
        if chapter_entries:
            grouped.append((title, chapter_entries))
    return grouped or [("Transcript", entries)]


def write_srt(entries: list[TranscriptEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for idx, entry in enumerate(entries, start=1):
        next_start = entries[idx].start if idx < len(entries) else None
        end = entry.end if entry.end is not None else next_start
        if end is None or end <= entry.start:
            end = entry.start + 4
        text = re.sub(r"\s+", " ", entry.text).strip()
        lines.extend(
            [
                str(idx),
                f"{seconds_to_srt_timestamp(entry.start)} --> {seconds_to_srt_timestamp(end)}",
                text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def vault_link(path: Path, vault_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
        return str(rel).replace("\\", "/")
    except Exception:
        return str(path)


def frame_timestamp_from_path(path: Path) -> float | None:
    match = re.search(r"frame_\d+_(\d{2})-(\d{2})-(\d{2})", path.name)
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def nearest_transcript_snippet(entries: list[TranscriptEntry], seconds: float | None, *, max_chars: int = 180) -> str:
    if seconds is None or not entries:
        return "待确认：该截图附近没有匹配到字幕。"
    nearest = min(entries, key=lambda entry: abs(entry.start - seconds))
    timestamp = seconds_to_timestamp(nearest.start)
    text = re.sub(r"\s+", " ", nearest.text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return f"{timestamp} {text}"


def frame_evidence(frame_paths: list[Path], transcript_entries: list[TranscriptEntry]) -> list[dict]:
    evidence: list[dict] = []
    for idx, frame in enumerate(frame_paths, start=1):
        seconds = frame_timestamp_from_path(frame)
        evidence.append(
            {
                "index": idx,
                "path": frame,
                "seconds": seconds,
                "timestamp": seconds_to_timestamp(seconds),
                "snippet": nearest_transcript_snippet(transcript_entries, seconds),
            }
        )
    return evidence


def canvas_id() -> str:
    return secrets.token_hex(8)


def create_canvas(
    *,
    output_file: Path,
    vault_root: Path,
    metadata: dict,
    transcript_source: str,
    transcript_language: str,
    cover_path: Path | None,
    frame_paths: list[Path],
    transcript_entries: list[TranscriptEntry],
    transcript_path: Path | None,
) -> dict:
    title = metadata.get("title") or "Video"
    note_file = vault_link(output_file, vault_root)
    url = metadata.get("webpage_url") or metadata.get("original_url") or ""
    nodes: list[dict] = []
    edges: list[dict] = []
    frames = frame_evidence(frame_paths, transcript_entries)
    visual_height = max(980, 120 + len(frames) * 270)

    source_id = canvas_id()
    transcript_id = canvas_id()
    note_id = canvas_id()
    status_id = canvas_id()
    cover_id = canvas_id()
    summary_id = canvas_id()
    details_id = canvas_id()
    hard_id = canvas_id()
    map_id = canvas_id()
    visual_note_id = canvas_id()
    transfer_id = canvas_id()
    action_id = canvas_id()

    nodes.extend(
        [
            {"id": canvas_id(), "type": "group", "x": 0, "y": 0, "width": 520, "height": 980, "label": "1. Source & Evidence", "color": "5"},
            {"id": canvas_id(), "type": "group", "x": 620, "y": 0, "width": 760, "height": 980, "label": "2. Learning Note", "color": "4"},
            {"id": canvas_id(), "type": "group", "x": 1480, "y": 0, "width": 960, "height": visual_height, "label": "3. Visual Evidence Timeline", "color": "3"},
            {"id": canvas_id(), "type": "group", "x": 620, "y": 1060, "width": 760, "height": 360, "label": "4. Review & Transfer", "color": "6"},
            {"id": source_id, "type": "link", "x": 40, "y": 70, "width": 440, "height": 230, "url": url},
            {
                "id": status_id,
                "type": "text",
                "x": 40,
                "y": 330,
                "width": 440,
                "height": 210,
                "color": "5",
                "text": f"# 来源状态\n\n- 字幕来源：{transcript_source}\n- 字幕语言：{transcript_language}\n- 视频标题：{title}\n- 使用方式：笔记负责理解，SRT 负责回查原文",
            },
            {"id": note_id, "type": "file", "x": 680, "y": 70, "width": 640, "height": 280, "file": note_file},
            {
                "id": summary_id,
                "type": "text",
                "x": 680,
                "y": 400,
                "width": 300,
                "height": 160,
                "color": "4",
                "text": "# 一句话摘要 / 速览\n\n- 视频解决什么问题？\n- 最核心的结论是什么？\n- 哪些知识点值得复习？",
            },
            {
                "id": details_id,
                "type": "text",
                "x": 1020,
                "y": 400,
                "width": 300,
                "height": 160,
                "color": "4",
                "text": "# 详细内容总结\n\n- 按视频逻辑分段\n- 方法、案例、因果关系分开写\n- 把关键截图插入对应段落",
            },
            {
                "id": hard_id,
                "type": "text",
                "x": 680,
                "y": 610,
                "width": 300,
                "height": 160,
                "color": "4",
                "text": "# 重点难点解析\n\n- 最容易误解的概念\n- 需要反复看的步骤\n- 复习优先级",
            },
            {
                "id": map_id,
                "type": "text",
                "x": 1020,
                "y": 610,
                "width": 300,
                "height": 160,
                "color": "4",
                "text": "# 学习图谱\n\n- 前置知识\n- 相关概念\n- 延伸主题\n- 复杂内容再画图",
            },
            {
                "id": visual_note_id,
                "type": "text",
                "x": 1520,
                "y": 70,
                "width": 880,
                "height": 110,
                "color": "3",
                "text": "# 关键画面索引\n\n右侧每一行是一个时间点截图和附近字幕。只把能解释概念、步骤、案例或证据的截图插入详细总结。",
            },
            {
                "id": transfer_id,
                "type": "text",
                "x": 680,
                "y": 1130,
                "width": 300,
                "height": 190,
                "color": "6",
                "text": "# 举一反三\n\n- 这个方法还能用在哪些场景？\n- 哪些前提一变，结论就不成立？\n- 用自己的项目/学习对象做一个小练习。",
            },
            {
                "id": action_id,
                "type": "text",
                "x": 1020,
                "y": 1130,
                "width": 300,
                "height": 190,
                "color": "6",
                "text": "# 复习动作\n\n1. 复述核心问题\n2. 对照截图解释关键步骤\n3. 画出流程图\n4. 做一个迁移案例\n5. 一周后回看难点",
            },
        ]
    )

    if transcript_path:
        nodes.append(
            {
                "id": transcript_id,
                "type": "file",
                "x": 40,
                "y": 580,
                "width": 440,
                "height": 120,
                "file": vault_link(transcript_path, vault_root),
            }
        )

    if cover_path:
        nodes.append(
            {
                "id": cover_id,
                "type": "file",
                "x": 40,
                "y": 740,
                "width": 440,
                "height": 220,
                "file": vault_link(cover_path, vault_root),
            }
        )
    else:
        nodes.append(
            {
                "id": cover_id,
                "type": "text",
                "x": 40,
                "y": 740,
                "width": 440,
                "height": 160,
                "text": "# 封面\n\n未保存封面图。",
            }
        )

    first_frame_id = None
    for idx, evidence in enumerate(frames, start=1):
        frame_id = canvas_id()
        caption_id = canvas_id()
        y = 220 + (idx - 1) * 270
        if first_frame_id is None:
            first_frame_id = frame_id
        nodes.append(
            {
                "id": frame_id,
                "type": "file",
                "x": 1520,
                "y": y,
                "width": 380,
                "height": 220,
                "file": vault_link(evidence["path"], vault_root),
            }
        )
        nodes.append(
            {
                "id": caption_id,
                "type": "text",
                "x": 1940,
                "y": y,
                "width": 460,
                "height": 180,
                "text": f"# {evidence['timestamp']}\n\n附近字幕：{evidence['snippet']}\n\n用途：判断是否插入详细总结。",
            }
        )

    for from_id, to_id, label in (
        (source_id, note_id, "来源"),
        (status_id, note_id, "提取状态"),
        (transcript_id, note_id, "字幕") if transcript_path else (status_id, note_id, "字幕"),
        (cover_id, details_id, "视觉参考"),
        (note_id, summary_id, "提炼"),
        (note_id, details_id, "展开"),
        (details_id, hard_id, "辨析"),
        (details_id, map_id, "结构化"),
        (details_id, transfer_id, "迁移"),
        (transfer_id, action_id, "行动"),
    ):
        edges.append(
            {
                "id": canvas_id(),
                "fromNode": from_id,
                "toNode": to_id,
                "toEnd": "arrow",
                "label": label,
            }
        )
    if first_frame_id:
        edges.append(
            {
                "id": canvas_id(),
                "fromNode": visual_note_id,
                "toNode": details_id,
                "toEnd": "arrow",
                "label": "截图辅助详细总结",
            }
        )
    return {"nodes": nodes, "edges": edges}


def create_markdown(
    metadata: dict,
    transcript_entries: list[TranscriptEntry],
    *,
    transcript_source: str,
    transcript_language: str,
    output_file: Path,
    vault_root: Path,
    cover_path: Path | None,
    frame_paths: list[Path],
    transcript_path: Path | None,
    warnings: list[str],
) -> str:
    title = metadata.get("title") or "Unknown"
    channel = metadata.get("channel") or metadata.get("uploader") or "Unknown"
    webpage_url = metadata.get("webpage_url") or metadata.get("original_url") or ""
    upload_date = metadata.get("upload_date") or ""
    if re.fullmatch(r"\d{8}", str(upload_date)):
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = seconds_to_timestamp(metadata.get("duration"))
    chapters = metadata.get("chapters") or []

    lines: list[str] = [
        "---",
        f"title: {yaml_scalar(title)}",
        f"filename_title_rule: {yaml_scalar('中文标题 - English Title')}",
        f"english_title_suggestion: {yaml_scalar(title)}",
        f"channel: {yaml_scalar(channel)}",
        f"url: {yaml_scalar(webpage_url)}",
        f"upload_date: {yaml_scalar(upload_date)}",
        f"duration: {yaml_scalar(duration)}",
        f"transcript_source: {yaml_scalar(transcript_source)}",
        f"transcript_language: {yaml_scalar(transcript_language)}",
        f"transcript_file: {yaml_scalar(vault_link(transcript_path, vault_root) if transcript_path else '')}",
        f"cover: {yaml_scalar(vault_link(cover_path, vault_root) if cover_path else '')}",
        f"view_count: {yaml_scalar(metadata.get('view_count'))}",
        f"like_count: {yaml_scalar(metadata.get('like_count'))}",
        "tags:",
        "  - video-learning",
        "---",
        "",
        "## Source Status",
        "",
        f"- URL: {webpage_url}",
        f"- Channel: {channel}",
        f"- Duration: {duration}",
        f"- Transcript source: {transcript_source}",
        f"- Transcript language: {transcript_language}",
        f"- Transcript file: [[{vault_link(transcript_path, vault_root)}]]" if transcript_path else "- Transcript file: 待确认",
    ]
    if warnings:
        lines.append("- Warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")

    description = (metadata.get("description") or "").strip()
    if description:
        lines.extend(["", "## Description", "", description[:2000]])

    frames = frame_evidence(frame_paths, transcript_entries)
    if cover_path or frames:
        lines.extend(["", "## 视觉素材", ""])
        if cover_path:
            lines.append(f"![[{vault_link(cover_path, vault_root)}|cover]]")
            lines.append("")
        if frames:
            lines.extend(
                [
                    "## 关键画面索引",
                    "",
                    "> 这些截图不是随机抽取：如果视频有章节，优先使用章节起点；否则按视频时长均匀取样。整理“详细内容总结”时，请把相关截图移动或引用到对应段落，而不是全部堆在开头。",
                    "",
                ]
            )
            for evidence in frames:
                lines.extend(
                    [
                        f"### {evidence['timestamp']}",
                        "",
                        f"![[{vault_link(evidence['path'], vault_root)}|520]]",
                        "",
                        f"- 附近字幕：{evidence['snippet']}",
                        "- 建议用途：如果这一帧能说明某个步骤、案例、结果或对比，请插入到对应的“详细内容总结”小节下。",
                        "",
                    ]
                )

    lines.extend(
        [
            "## 学习笔记整理任务",
            "",
            "> 请基于本文件中的视频信息、描述、可用的关键画面和单独保存的 SRT 字幕文件，把当前文档整理成一份适合长期复习的中文 Obsidian 学习笔记。不要只做摘要，要帮助真正理解、复述、应用和举一反三。若信息不足，请标注“待确认”。",
            "",
            "整理要求：",
            "",
            "1. 文件名必须使用 `中文标题 - English Title` 格式。不要保留纯英文文件名，也不要用下划线代替空格；如果当前文件名以 `待命名 -` 开头，必须重命名当前文件本身：根据内容改成准确中文标题，并保留英文标题在连字符后。",
            "2. 如果重命名或移动笔记，必须同步更新 SRT 字幕链接和相关图片链接。",
            "3. 正文不要写顶层 `# 标题`，也不要重复写中英双语大标题；Obsidian 文件名就是文章标题，正文直接从 `## 一句话摘要` 开始。",
            "4. 内容以中文为主，关键 English terms 保留原词并解释。",
            "5. 每一节都要围绕“我学到了什么、为什么重要、怎么应用”展开。",
            "6. 详细内容总结中按内容需要插入相关截图，不限制数量；只有能辅助理解的截图才插入，不要为了插图而插图。",
            "7. 原始字幕不要粘贴到正文；文末只保留 SRT 字幕文件链接。",
            "",
            "请使用以下结构重写当前文档：",
            "",
            "## 一句话总结",
            "",
            "用 2-4 句话说明视频主题、核心价值、适合学习的原因。",
            "",
            "## 核心知识点速览",
            "",
            "列出 5-10 条最重要知识点，每条都要有一句解释。",
            "",
            "## 详细内容总结",
            "",
            "按视频逻辑分段讲解。不要流水账，要把观点、方法、案例、因果关系和关键证据整理清楚。能用截图说明的段落，请插入来自“关键画面索引”的对应截图，并说明它证明了什么。",
            "",
            "## 重点难点解析",
            "",
            "- 最值得记住的重点",
            "- 最容易误解或忽略的难点",
            "- 复习时应该优先看的部分",
            "",
            "## 可视化总结",
            "",
            "不要强制生成思维导图。简单视频可以只用要点表或流程图；复杂视频再生成 Mermaid 图。优先使用层级清晰的 flowchart、timeline、quadrant 或对比表，只有概念关系确实复杂时才用 mindmap。图要少而清楚，节点不宜过多。",
            "",
            "## 学习图谱",
            "",
            "说明本视频涉及哪些前置知识、相关概念、可延伸主题，以及它们之间的关系。",
            "",
            "## 行动建议-举一反三",
            "",
            "告诉我学完后可以马上做什么、想学好还需要补什么知识、后续可以怎么应用、继续深入应该关注什么，并给出 3-5 个迁移应用场景、反例或练习题。",
            "",
            "## 专业术语表",
            "",
            "如果视频包含专业概念、缩写、技术名词或行业术语，请生成术语表；如果术语很少，可以保留 3-5 个最关键的。表格列为：术语｜英文全称｜中文说明。中文说明要解释它在本视频语境中的含义和作用，不要只翻译字面意思。",
            "",
            "## 原始字幕 Transcript",
            "",
            f"- SRT 字幕文件：[[{vault_link(transcript_path, vault_root)}]]" if transcript_path else "- SRT 字幕文件：待确认",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def suggested_note_filename(title: str, max_len: int = 120) -> str:
    if contains_cjk(title):
        return sanitize_filename(title, max_len=max_len)
    english = sanitize_filename(title, max_len=max_len - 6)
    return sanitize_filename(f"待命名 - {english}", max_len=max_len)


def create_markdown(
    metadata: dict,
    transcript_entries: list[TranscriptEntry],
    *,
    transcript_source: str,
    transcript_language: str,
    output_file: Path,
    vault_root: Path,
    cover_path: Path | None,
    frame_paths: list[Path],
    transcript_path: Path | None,
    warnings: list[str],
) -> str:
    title = metadata.get("title") or "Unknown"
    channel = metadata.get("channel") or metadata.get("uploader") or "Unknown"
    webpage_url = metadata.get("webpage_url") or metadata.get("original_url") or ""
    upload_date = metadata.get("upload_date") or ""
    if re.fullmatch(r"\d{8}", str(upload_date)):
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = seconds_to_timestamp(metadata.get("duration"))

    lines: list[str] = [
        "---",
        f"title: {yaml_scalar(title)}",
        f"skill_version: {yaml_scalar(VERSION)}",
        "quality_profile_version: 1",
        f"filename_title_rule: {yaml_scalar('中文标题 - English Title')}",
        f"english_title_suggestion: {yaml_scalar(title)}",
        f"channel: {yaml_scalar(channel)}",
        f"url: {yaml_scalar(webpage_url)}",
        f"upload_date: {yaml_scalar(upload_date)}",
        f"duration: {yaml_scalar(duration)}",
        f"transcript_source: {yaml_scalar(transcript_source)}",
        f"transcript_language: {yaml_scalar(transcript_language)}",
        f"transcript_file: {yaml_scalar(vault_link(transcript_path, vault_root) if transcript_path else '')}",
        f"view_count: {yaml_scalar(metadata.get('view_count'))}",
        f"like_count: {yaml_scalar(metadata.get('like_count'))}",
        "tags:",
        "  - video-learning",
        "---",
    ]
    if warnings:
        lines.extend(["", "## 提取警告", ""])
        for warning in warnings:
            lines.append(f"- {warning}")

    description = (metadata.get("description") or "").strip()
    if description:
        lines.extend(["", "## 视频描述", "", description[:2000]])

    lines.extend(
        [
            "## 学习笔记整理任务",
            "",
            "> 先依据 SRT 为重要正文小节制定画面计划，再运行 `extract_frames.py`。抽帧成功后，基于视频信息、描述、SRT 和关键画面整理中文学习笔记。文章内容由字幕决定，图片只作辅助证据，不能为了配图而删减知识点；信息不足时标注“待确认”。",
            "",
            "### 必须遵守",
            "",
            "1. 文件名必须使用 `中文标题 - English Title` 格式；如果当前文件名以 `待命名 -` 开头，必须把当前文件重命名为准确中文标题加英文标题。",
            "2. 正文不要写顶层 `# 标题`，正文直接从 `## 一句话摘要` 开始。",
            "3. 正文使用中文表达。除必要专业术语、产品名、代码命令、模型名、论文名、链接和 YAML 外，不要夹杂英文句子或英文小标题。",
            "4. 关键 English terms 可以保留英文原词，但第一次出现时必须用中文解释它在本视频语境中的含义。",
            "5. 先完成画面计划和定点抽帧。保留计划中所有非纯黑、非纯白的有效画面，不做近重复去重，也不使用 OCR 或 AI 图像识别。`## 详细内容总结` 插入对应截图并写中文说明。",
            "6. 抽帧失败时立即停止并报告，不要改用浏览器、下载完整视频或临时凑图。",
            "7. 原始字幕不要粘贴到正文，文末只保留 SRT 字幕链接。",
            "8. 最终回答用户前运行 `validate_note.py`。终稿必须删除视频描述、提取警告、整理任务和输出自检清单等脚手架章节。",
            "",
            "## 一句话摘要",
            "",
            "用 2-4 句话说明视频主题、核心价值和适合学习的点。",
            "",
            "## 核心知识点速览",
            "",
            "列出 5-10 条最重要知识点。每条包含：是什么、为什么重要、可以怎么用。",
            "",
            "## 详细内容总结",
            "",
            "按视频逻辑完整分段讲解。不要流水账，也不要为了缩短文章或适配图片而遗漏重要知识。区分事实、视频观点、推断、案例、限制条件与不确定性；把方法、因果关系和关键证据讲清楚。截图放在对应描述附近并说明它展示了什么。",
            "",
            "## 重点难点解析",
            "",
            "- 最值得记住的重点",
            "- 最容易误解或忽略的难点",
            "- 复习时应该优先看的部分",
            "",
            "## 可视化总结",
            "",
            "不要强制生成思维导图。简单视频可以只用要点表或流程图；复杂视频再生成 Mermaid 图。优先使用层级清晰的 flowchart、timeline、quadrant 或对比表。图要少而清楚，节点不宜过多。",
            "",
            "## 学习图谱",
            "",
            "说明本视频涉及哪些前置知识、相关概念、可延伸主题，以及它们之间的关系。",
            "",
            "## 行动建议-举一反三",
            "",
            "告诉我学完后可以马上做什么、想学好还需要补什么、后续可以怎么应用、继续深入应该关注什么，并给出 3-5 个迁移应用场景、反例或练习题。",
            "",
            "## 专业术语表",
            "",
            "如果视频包含专业概念、缩写、技术名词或行业术语，请生成术语表；如果术语很少，可以保留 3-5 个最关键的。表格列为：术语｜英文全称｜中文说明。中文说明要解释它在本视频语境中的含义和作用，不要只翻译字面意思。",
            "",
            "## 原始字幕 Transcript",
            "",
            f"- SRT 字幕文件：[[{vault_link(transcript_path, vault_root)}]]" if transcript_path else "- SRT 字幕文件：待确认",
            "",
            "## 输出自检清单",
            "",
            "- [ ] 文件名是 `中文标题 - English Title`，不是纯英文，也不是 `待命名 - ...`。",
            "- [ ] 正文从 `## 一句话摘要` 开始，没有重复大标题。",
            "- [ ] `## 详细内容总结` 已插入抽帧清单中的真实截图。",
            "- [ ] 每张正文截图下面都有中文说明。",
            "- [ ] 正文没有不必要的英文句子或英文小标题；必要英文术语均有中文解释。",
            "- [ ] 文末只链接 SRT 字幕，没有粘贴完整原始字幕。",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract video transcript and source material for an Obsidian note.")
    parser.add_argument("url", nargs="?", help="YouTube, Bilibili, or any yt-dlp supported video URL")
    parser.add_argument("output_filename", nargs="?", help="Optional output Markdown filename")
    parser.add_argument("--vault", default=str(Path.home() / "Documents" / "Obsidian Vault"), help="Obsidian vault root")
    parser.add_argument("--output-dir", default="YouTube video", help="Output folder relative to vault root")
    parser.add_argument("--cookies", help="Explicit cookies.txt path for this run")
    parser.add_argument("--no-cookies", action="store_true", help="Do not use saved cookies")
    parser.add_argument("--proxy", help="Proxy URL passed to yt-dlp")
    parser.add_argument("--langs", default=DEFAULT_LANGS, help="Comma-separated subtitle language priority")
    parser.add_argument("--allow-asr", action="store_true", help="Explicitly allow slower audio download and local ASR")
    parser.add_argument("--force-asr", action="store_true", help="Skip platform subtitles and run ASR; requires --allow-asr")
    parser.add_argument("--asr-model", default="base", help="faster-whisper model name")
    parser.add_argument("--deadline", type=int, default=300, help="Total material extraction deadline in seconds")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary downloaded audio only when ASR was requested")
    parser.add_argument("--self-test", action="store_true", help="Create a local sample note and SRT without platform access")
    parser.add_argument("--version", action="store_true", help="Print installed skill version")
    args = parser.parse_args(argv)
    if not args.version and not args.self_test and not args.url:
        parser.error("url is required unless --self-test or --version is used")
    if args.force_asr and not args.allow_asr:
        parser.error("--force-asr requires explicit --allow-asr")
    if args.deadline < 30:
        parser.error("--deadline must be at least 30 seconds")
    return args


def run_self_test(output_dir: Path, vault_root: Path) -> int:
    output_file = output_dir / "自检通过 - YouTube Note Forge.md"
    transcript_file = output_dir / "transcripts" / "自检通过 - YouTube Note Forge.srt"
    transcript_entries = [
        TranscriptEntry(0.0, 3.2, "欢迎使用 YouTube Note Forge。"),
        TranscriptEntry(3.2, 7.8, "这个自检不会访问视频平台，只验证本地 Markdown 和 SRT 生成链路。"),
        TranscriptEntry(7.8, 13.0, "真实视频提取仍然需要 yt-dlp、Node.js，以及在必要时提供有效 cookies。"),
    ]
    write_srt(transcript_entries, transcript_file)
    markdown = create_markdown(
        {
            "id": "youtube-note-forge-self-test",
            "title": "YouTube Note Forge Self Test",
            "channel": "Local Verification",
            "webpage_url": "self-test://youtube-note-forge",
            "original_url": "self-test://youtube-note-forge",
            "duration": 13,
            "description": "Local verification note generated without network access.",
        },
        transcript_entries,
        transcript_source="self-test",
        transcript_language="zh",
        output_file=output_file,
        vault_root=vault_root,
        cover_path=None,
        frame_paths=[],
        transcript_path=transcript_file,
        warnings=["Self-test mode does not contact YouTube, Bilibili, or any remote video platform."],
    )
    output_file.write_text(markdown, encoding="utf-8")
    print(f"[OK] Self-test note: {output_file}")
    print(f"[OK] Self-test transcript: {transcript_file}")
    emit_result("ok", stage="self_test", note=str(output_file), transcript=str(transcript_file), screenshots=[])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.version:
        print(version_text())
        return 0
    vault_root = Path(args.vault).expanduser()
    output_dir = vault_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.self_test:
        return run_self_test(output_dir, vault_root)

    automatic_cookie = platform_cookie_path(args.url)
    deadline = Deadline(args.deadline)
    runner = YtDlp(
        cookies=args.cookies,
        no_cookies=args.no_cookies,
        proxy=args.proxy,
        auto_cookies_path=None if args.no_cookies else automatic_cookie,
        deadline=deadline,
    )
    if not args.cookies and not args.no_cookies:
        if automatic_cookie and automatic_cookie.exists():
            print(f"Using saved {video_platform(args.url)} cookies: {automatic_cookie}")
        else:
            print(f"No saved cookies for {video_platform(args.url)}; trying public access without cookies.")

    emit_progress("materials", "正在读取视频元数据。", percent=8)
    print(f"Extracting metadata: {args.url}")
    metadata = extract_metadata(runner, args.url)
    emit_progress("materials", "元数据已读取，正在获取字幕。", percent=13)
    title = metadata.get("title") or metadata.get("id") or "video"
    output_file = output_dir / (args.output_filename or (suggested_note_filename(title) + ".md"))

    temp_context = tempfile.TemporaryDirectory(prefix="video-learning-")
    tmp_dir = Path(temp_context.name)
    try:
        transcript_entries: list[TranscriptEntry] = []
        transcript_source = ""
        transcript_language = ""
        warnings: list[str] = []
        subtitle_error = ""

        choice = None if args.force_asr else choose_subtitle(metadata, args.langs)
        if choice is None and not args.force_asr:
            choice = reprobe_subtitle_choice(runner, args.url, args.langs)
            if choice:
                print(f"Recovered subtitle track after re-probe: {choice.lang} ({choice.source})")
        if choice:
            emit_progress("materials", f"正在下载 {choice.lang} 字幕。", percent=18)
            print(f"Downloading subtitles: {choice.lang} ({choice.source})")
            subtitle_file, subtitle_error = download_subtitle(runner, args.url, metadata, choice, tmp_dir)
            if subtitle_file:
                transcript_entries = parse_vtt(subtitle_file)
                transcript_source = f"subtitle:{choice.source}"
                transcript_language = choice.lang
            elif subtitle_error:
                warnings.append(f"Subtitle download failed: {subtitle_error.strip()[:500]}")

        if not transcript_entries:
            if not args.allow_asr:
                raise transcript_unavailable_error(choice, subtitle_error)
            print("No usable subtitles found; downloading audio for ASR fallback...")
            audio_path = download_audio(runner, args.url, tmp_dir)
            transcript_entries, transcript_source = transcribe_audio(audio_path, args.asr_model)
            transcript_language = transcript_source.split(":")[-1]

        if not transcript_entries:
            raise RuntimeError("Transcript extraction produced no text.")

        transcript_file = output_dir / "transcripts" / f"{output_file.stem}.srt"
        write_srt(transcript_entries, transcript_file)

        print("Saving cover image...", flush=True)
        cover_path, frame_paths, visual_warnings = save_visual_assets(
            runner,
            args.url,
            metadata,
            transcript_entries,
            output_file,
            tmp_dir,
            max_keyframes=0,
            skip_keyframes=True,
        )
        warnings.extend(visual_warnings)

        markdown = create_markdown(
            metadata,
            transcript_entries,
            transcript_source=transcript_source,
            transcript_language=transcript_language,
            output_file=output_file,
            vault_root=vault_root,
            cover_path=cover_path,
            frame_paths=frame_paths,
            transcript_path=transcript_file,
            warnings=warnings,
        )
        output_file.write_text(markdown, encoding="utf-8")
        emit_progress("planning", "字幕、SRT 和封面已就绪，正在规划文章与截图。", percent=28)

        print(f"[OK] Saved note: {output_file}")
        print(f"[OK] Saved transcript: {transcript_file}")
        if cover_path:
            print(f"[OK] Saved cover: {cover_path}")
        if warnings:
            print("[WARN] " + " | ".join(warnings))
        emit_result(
            "ok",
            stage="materials",
            url=args.url,
            title=title,
            note=str(output_file),
            transcript=str(transcript_file),
            cover=str(cover_path) if cover_path else None,
            screenshots=[],
            elapsed_seconds=args.deadline - deadline.remaining(),
        )
        return 0
    finally:
        if args.keep_temp:
            print(f"[INFO] Temporary files kept at: {tmp_dir}")
        else:
            temp_context.cleanup()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        emit_result("error", stage="cancelled", code="CANCELLED", message="用户取消了任务。")
        raise
    except PipelineError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        emit_result("error", stage=exc.stage, code=exc.code, message=str(exc), action=exc.action)
        raise SystemExit(2)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        text = str(exc).lower()
        if "saved youtube cookies were rejected" in text or "cookies were rejected" in text:
            code = "COOKIE_REJECTED"
        elif "sign in to confirm" in text or "authentication may be required" in text:
            code = "AUTH_REQUIRED"
        else:
            code = "EXTRACTION_FAILED"
        emit_result("error", stage="materials", code=code, message=str(exc))
        raise SystemExit(1)
