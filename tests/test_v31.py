from __future__ import annotations

import json
import functools
import http.server
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "native_host"))

from extract_frames import (  # noqa: E402
    FrameRequest,
    FrameSource,
    classify_frame_exception,
    extract_candidate,
    extract_hls_frame,
    extract_hls_frames_parallel,
    load_hls_segments,
    manifest_name_for_plan,
    parse_hls_media_playlist,
    resolve_ffmpeg,
    select_hls_segment,
    load_plan_document,
    select_source,
)
from validate_note import validate  # noqa: E402
from video_common import Deadline  # noqa: E402
from youtube_reader_host import (  # noqa: E402
    build_opencode_command,
    cookies_to_netscape,
    create_http_server,
    format_user_error,
    NativeHost,
    screenshot_directory,
    stage_from_output,
    validation_error_action,
    validate_youtube_url,
)


class NativeHostContractTests(unittest.TestCase):
    def test_only_youtube_video_urls_are_accepted(self) -> None:
        self.assertEqual(validate_youtube_url("https://www.youtube.com/watch?v=XWlz2zfBL7E"), "XWlz2zfBL7E")
        self.assertEqual(validate_youtube_url("https://youtu.be/XWlz2zfBL7E"), "XWlz2zfBL7E")
        with self.assertRaises(ValueError):
            validate_youtube_url("https://example.com/watch?v=XWlz2zfBL7E")

    def test_cookie_snapshot_is_rendered_as_netscape_without_logging_secrets(self) -> None:
        text = cookies_to_netscape(
            [
                {
                    "domain": ".youtube.com",
                    "hostOnly": False,
                    "path": "/",
                    "secure": True,
                    "expirationDate": 1_900_000_000,
                    "name": "SID",
                    "value": "top-secret",
                }
            ]
        )
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\ttop-secret", text)
        self.assertTrue(text.startswith("# Netscape HTTP Cookie File"))

    def test_host_invokes_existing_video_note_command_only(self) -> None:
        command = build_opencode_command(
            opencode="opencode",
            vault=Path(r"C:\Users\win11\Documents\Obsidian Vault"),
            model="deepseek/deepseek-v4-pro",
            url="https://www.youtube.com/watch?v=XWlz2zfBL7E",
        )
        self.assertEqual(command[:5], ["opencode", "run", "--command", "video-note", "--format"])
        self.assertIn("deepseek/deepseek-v4-pro", command)
        self.assertNotIn("extract_transcript.py", " ".join(command))
        self.assertNotIn("extract_frames.py", " ".join(command))

    def test_long_cookie_failure_is_reduced_to_one_clear_action(self) -> None:
        code, message = format_user_error(
            RuntimeError("COOKIE_REJECTED: yt-dlp failed\nWARNING: cookies invalid\nERROR: Sign in to confirm")
        )
        self.assertEqual(code, "COOKIE_REJECTED")
        self.assertEqual(message, "当前 YouTube Cookie 已失效。请保持 YouTube 登录状态并重新点击插件，插件会自动更新 Cookie。")
        self.assertNotIn("yt-dlp", message)

    def test_first_note_validation_failure_gets_one_repair_only(self) -> None:
        result = {"status": "error", "code": "NOTE_VALIDATION_FAILED"}
        self.assertEqual(validation_error_action(result, failures_seen=0), "repair")
        self.assertEqual(validation_error_action(result, failures_seen=1), "fail")
        self.assertEqual(validation_error_action({"status": "error", "code": "COOKIE_REJECTED"}, failures_seen=0), "fail")

    def test_screenshot_directory_is_derived_from_frame_results(self) -> None:
        result = {
            "manifest": r"C:\Vault\YouTube video\assets\abc\frame-manifest.json",
            "screenshots": [{"path": r"C:\Vault\YouTube video\assets\abc\frame_01.jpg"}],
        }
        self.assertEqual(screenshot_directory(result), r"C:\Vault\YouTube video\assets\abc")


class FrameV31Tests(unittest.TestCase):
    def test_each_distinct_frame_plan_gets_an_immutable_manifest_name(self) -> None:
        first = [FrameRequest("section", 10.0, "展示结构", True)]
        second = [FrameRequest("section", 20.0, "展示结果", True)]
        first_name = manifest_name_for_plan(first, [{"title": "原理"}])
        self.assertEqual(first_name, manifest_name_for_plan(first, [{"title": "原理"}]))
        self.assertNotEqual(first_name, manifest_name_for_plan(second, [{"title": "原理"}]))
        self.assertRegex(first_name, r"^frame-manifest-[0-9a-f]{12}\.json$")

    def test_hls_playlist_maps_timestamp_to_one_bounded_segment(self) -> None:
        playlist = """#EXTM3U
#EXT-X-TARGETDURATION:8
#EXTINF:3.5,
segment-0.ts
#EXTINF:6.0,
segment-1.ts
#EXTINF:4.0,
segment-2.ts
#EXT-X-ENDLIST
"""
        segments = parse_hls_media_playlist(playlist, "https://video.example/path/index.m3u8")
        self.assertEqual(len(segments), 3)
        selected, offset = select_hls_segment(segments, 7.0)
        self.assertEqual(selected.url, "https://video.example/path/segment-1.ts")
        self.assertAlmostEqual(offset, 3.5)

    def test_cookie_rejection_keeps_a_specific_error_code(self) -> None:
        error = RuntimeError("Saved YouTube cookies were rejected. Sign in to confirm you're not a bot.")
        self.assertEqual(classify_frame_exception(error), "COOKIE_REJECTED")

    def test_hls_segment_path_extracts_a_real_720p_frame(self) -> None:
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            playlist = root / "playlist.m3u8"
            segment_pattern = root / "segment_%02d.ts"
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=1280x720:rate=2",
                    "-t",
                    "4",
                    "-c:v",
                    "libx264",
                    "-g",
                    "2",
                    "-pix_fmt",
                    "yuv420p",
                    "-f",
                    "hls",
                    "-hls_time",
                    "1",
                    "-hls_list_size",
                    "0",
                    "-hls_segment_filename",
                    str(segment_pattern),
                    str(playlist),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=raw)
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}/playlist.m3u8"
                source = FrameSource(base, {}, "m3u8_native")
                segments = load_hls_segments(source, 5)
                target = root / "frame.jpg"
                ok, reason = extract_hls_frame(
                    ffmpeg,
                    source,
                    segments,
                    FrameRequest("demo", 2.5, "测试画面", True),
                    target,
                    root / "cache",
                    Deadline(15),
                )
                self.assertTrue(ok, reason)
                self.assertGreater(target.stat().st_size, 2_000)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    @mock.patch("extract_frames.extract_hls_frame")
    def test_hls_frame_requests_run_in_a_bounded_parallel_batch(self, extract: mock.Mock) -> None:
        extract.side_effect = lambda *_args, **_kwargs: (time.sleep(0.05) or (True, ""))
        requests = [FrameRequest(f"s{i}", float(i), f"画面 {i}", True) for i in range(8)]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = [(i, request, root / f"frame-{i}.jpg") for i, request in enumerate(requests)]
            started = time.monotonic()
            results = extract_hls_frames_parallel(
                "ffmpeg",
                FrameSource("https://video.example/playlist.m3u8", {}, "m3u8_native"),
                [],
                jobs,
                root,
                Deadline(10),
                workers=4,
            )
            elapsed = time.monotonic() - started
        self.assertEqual(len(results), 8)
        self.assertLess(elapsed, 0.25)

    def test_frame_plan_carries_article_outline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            plan = Path(raw) / "plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "article_outline": [
                            {
                                "section_id": "engine-cycle",
                                "title": "涡喷发动机的基本循环",
                                "start": "00:00:30",
                                "end": "00:03:00",
                                "core_claims": ["压缩、燃烧和膨胀构成基本循环"],
                                "learning_goal": "理解能量如何转换为推力",
                            }
                        ],
                        "frames": [
                            {
                                "section_id": "engine-cycle",
                                "timestamp": "00:01:20",
                                "purpose": "展示压气机结构",
                                "required": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            frames, outline = load_plan_document(plan)
        self.assertEqual(frames[0], FrameRequest("engine-cycle", 80.0, "展示压气机结构", True))
        self.assertEqual(outline[0]["title"], "涡喷发动机的基本循环")

    def test_source_selection_preserves_http_headers(self) -> None:
        source = select_source(
            {
                "http_headers": {"User-Agent": "metadata-agent", "Referer": "https://www.youtube.com/"},
                "formats": [
                    {
                        "url": "https://video.example/stream",
                        "vcodec": "avc1",
                        "height": 720,
                        "ext": "mp4",
                        "protocol": "https",
                        "http_headers": {"User-Agent": "format-agent", "Cookie": "SID=abc"},
                    }
                ],
            }
        )
        self.assertEqual(source.url, "https://video.example/stream")
        self.assertEqual(source.headers["User-Agent"], "format-agent")
        self.assertEqual(source.headers["Cookie"], "SID=abc")
        self.assertEqual(source.headers["Referer"], "https://www.youtube.com/")

    @mock.patch("extract_frames.subprocess.run")
    def test_ffmpeg_receives_remote_headers(self, run: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "frame.jpg"

            def fake_run(command: list[str], **_: object) -> mock.Mock:
                target.write_bytes(b"x" * 3_000)
                return mock.Mock(returncode=0)

            run.side_effect = fake_run
            with mock.patch("extract_frames.image_quality_score", return_value=1):
                self.assertTrue(
                    extract_candidate(
                        "ffmpeg",
                        "https://video.example/stream",
                        12.0,
                        target,
                        10,
                        {"User-Agent": "Chrome", "Referer": "https://www.youtube.com/"},
                    )
                )
        command = run.call_args.args[0]
        self.assertIn("-headers", command)
        self.assertIn("User-Agent: Chrome", command[command.index("-headers") + 1])


class QualityGateV31Tests(unittest.TestCase):
    def test_shallow_detailed_summary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "中文标题 - English Title.md"
            note.write_text(
                "---\nskill_version: 3.3.1\nquality_profile_version: 1\nduration: 00:20:00\n---\n\n"
                "## 一句话摘要\n\n这是摘要。\n\n"
                "## 核心知识点速览\n\n- 知识点。\n\n"
                "## 详细内容总结\n\n### 第一部分\n\n内容很少。\n\n"
                "## 重点难点解析\n\n重点。\n\n"
                "## 可视化总结\n\n流程。\n\n"
                "## 学习图谱\n\n学习。\n\n"
                "## 行动建议-举一反三\n\n行动。\n\n"
                "## 专业术语表\n\n术语。\n\n"
                "## 原始字幕 Transcript\n\n字幕。\n",
                encoding="utf-8",
            )
            codes = {item["code"] for item in validate(note, vault)}
        self.assertIn("DETAIL_CONTENT_SHALLOW", codes)
        self.assertIn("DETAIL_CHAPTERS_INSUFFICIENT", codes)


class ExtensionStaticContractTests(unittest.TestCase):
    def test_extension_uses_local_companion_and_never_stores_cookie_values(self) -> None:
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        self.assertNotIn("nativeMessaging", manifest["permissions"])
        self.assertIn("cookies", manifest["permissions"])
        self.assertIn("http://127.0.0.1:32191/*", manifest["host_permissions"])
        self.assertIn("chrome.cookies.getAll", worker)
        self.assertIn("http://127.0.0.1:32191", worker)
        self.assertNotIn("connectNative", worker)
        self.assertIn('model: "deepseek/deepseek-v4-pro"', worker)
        self.assertIn("auto_open_note", worker)
        self.assertIn("auto_open_note: settings.auto_open_note", worker)
        self.assertIn("companionActive", worker)
        self.assertNotIn("cookies:", worker.split("chrome.storage.local.set", 1)[-1] if "chrome.storage.local.set" in worker else "")

    def test_popup_has_determinate_progress_and_stage_timeline(self) -> None:
        popup = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
        script = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        self.assertIn('id="progressText"', popup)
        self.assertIn('id="timeline"', popup)
        self.assertIn('id="copyPath"', popup)
        self.assertIn('id="copyPhotosPath"', popup)
        self.assertIn('id="retryConnection"', popup)
        self.assertIn("强制停止", popup)
        self.assertIn("文件位置", script)
        self.assertIn("照片位置", script)
        self.assertIn("正在处理", script)
        self.assertIn("预计保存位置", script)
        self.assertIn("清除任务", script)
        self.assertIn("总耗时", script)
        self.assertIn("progress_percent", script)
        self.assertIn("STAGE_ORDER", script)

    def test_extension_has_a_visible_brand_logo_and_icons(self) -> None:
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        popup = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
        self.assertIn('class="brand-logo"', popup)
        self.assertEqual(manifest["action"]["default_icon"]["32"], "icons/icon32.png")
        for size in (16, 32, 48, 128):
            self.assertTrue((ROOT / "extension" / "icons" / f"icon{size}.png").is_file())

    def test_background_state_uses_action_badges_and_survives_popup_closure(self) -> None:
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        host = (ROOT / "native_host" / "youtube_reader_host.py").read_text(encoding="utf-8")
        self.assertIn("chrome.storage.session.set", worker)
        self.assertIn("chrome.action.setBadgeText", worker)
        self.assertIn("companionActive", worker)
        self.assertIn("companionLatest", worker)
        self.assertIn("active_request_id", worker)
        self.assertIn('message.get("auto_open_note", True)', host)
        self.assertIn('"note_opened": note_opened', host)
        self.assertIn("video_title", worker)
        self.assertIn("output_dir", worker)
        self.assertIn('"video_title": video_title', host)

    def test_terminal_error_can_be_cleared_without_stale_task_context(self) -> None:
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        self.assertIn('status: "idle"', worker)
        self.assertIn('message: "准备就绪，可以开始新任务"', worker)
        self.assertIn('video_title: ""', worker)
        self.assertIn('request_id: ""', worker)

    def test_model_picker_is_searchable_and_has_manual_fallback(self) -> None:
        options_html = (ROOT / "extension" / "options.html").read_text(encoding="utf-8")
        options_js = (ROOT / "extension" / "options.js").read_text(encoding="utf-8")
        self.assertIn('list="modelOptions"', options_html)
        self.assertIn('id="modelOptions"', options_html)
        self.assertIn("deepseek/deepseek-v4-pro", options_js)

    def test_installer_runs_and_verifies_local_companion(self) -> None:
        installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_install.ps1").read_text(encoding="utf-8")
        self.assertNotIn("PyInstaller", installer)
        self.assertIn("pythonw.exe", installer)
        self.assertIn("youtube_reader_host.py", installer)
        self.assertIn("runtime.json", installer)
        self.assertIn("CurrentVersion\\Run", installer)
        self.assertIn("--serve", installer)
        self.assertIn("Wait-Process", installer)
        self.assertIn("runtime.json", verifier)
        self.assertIn("/health", verifier)
        self.assertTrue((ROOT / "scripts" / "restart_companion.ps1").is_file())

    def test_video_note_agent_runs_directly_and_reports_machine_result(self) -> None:
        agent = (ROOT / "opencode" / "agent" / "video-note.md").read_text(encoding="utf-8")
        command = (ROOT / "opencode" / "command" / "video-note.md").read_text(encoding="utf-8")
        self.assertIn("mode: primary", agent)
        self.assertIn("PIPELINE_RESULT=", agent)
        self.assertIn("PIPELINE_RESULT=", command)
        self.assertIn("第一次 NOTE_VALIDATION_FAILED", agent)
        self.assertIn("第一次 NOTE_VALIDATION_FAILED", command)

    def test_stage_detection_exposes_real_pipeline_phases(self) -> None:
        self.assertEqual(stage_from_output("extract_transcript.py"), "materials")
        self.assertEqual(stage_from_output("plan_WMgny-yDjvs.json"), "planning")
        self.assertEqual(stage_from_output("extract_frames.py"), "frames")
        self.assertEqual(stage_from_output("validate_note.py"), "validation")

    def test_native_host_accepts_chrome_launch_arguments(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "native_host" / "youtube_reader_host.py"),
                "--self-test",
                "chrome-extension://obcfabljhffpdbcaebficbfpdpinnhgh/",
                "--parent-window=0",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "ok"', result.stdout)


class CompanionHttpTests(unittest.TestCase):
    @mock.patch("youtube_reader_host.subprocess.run")
    def test_force_stop_reports_cancelled_instead_of_failed(self, run: mock.Mock) -> None:
        host = NativeHost(native_output=False, persist_log=False)
        host.process = mock.Mock()
        host.process.poll.return_value = None
        host.handle({"type": "cancel_job", "request_id": "request-1"})
        payload = host.status_for("request-1")
        self.assertEqual(payload["type"], "cancelled")
        self.assertEqual(payload["status"], "cancelled")
        run.assert_called_once()

    @mock.patch("youtube_reader_host.subprocess.run")
    def test_force_stop_waits_for_old_job_before_allowing_a_new_one(self, _run: mock.Mock) -> None:
        host = NativeHost(native_output=False, persist_log=False)
        host.active_request_id = "request-1"
        host.process = mock.Mock()
        host.process.poll.return_value = None
        host.job_thread = mock.Mock()
        host.job_thread.is_alive.return_value = False
        host.handle({"type": "cancel_job", "request_id": "request-1"})
        host.job_thread.join.assert_called_once_with(timeout=5)
        self.assertEqual(host.active_request_id, "")

    def test_second_start_attaches_to_the_existing_job(self) -> None:
        host = NativeHost(native_output=False, persist_log=False)
        host.active_request_id = "active-request"
        host.latest_by_request["active-request"] = {
            "type": "progress",
            "request_id": "active-request",
            "status": "running",
            "stage": "frames",
            "message": "正在抽帧",
            "elapsed_seconds": 42,
        }
        host.job_thread = mock.Mock()
        host.job_thread.is_alive.return_value = True
        host.handle({"type": "start_job", "request_id": "new-request"})
        attached = host.status_for("new-request")
        self.assertEqual(attached["type"], "attached")
        self.assertEqual(attached["active_request_id"], "active-request")
        self.assertEqual(attached["status"], "running")

    def test_latest_completed_job_can_be_restored_after_browser_state_is_lost(self) -> None:
        host = NativeHost(native_output=False, persist_log=False)
        host.send(
            {
                "type": "complete",
                "request_id": "finished-request",
                "status": "ok",
                "stage": "complete",
                "note_path": r"C:\Vault\YouTube video\完成笔记 - Finished Note.md",
                "elapsed_seconds": 187,
                "progress_percent": 100,
            }
        )
        latest = host.latest_status()
        self.assertEqual(latest["request_id"], "finished-request")
        self.assertEqual(latest["note_path"], r"C:\Vault\YouTube video\完成笔记 - Finished Note.md")

    def test_restored_legacy_error_is_sanitized_for_the_popup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            log_path = Path(raw) / "last-job.jsonl"
            log_path.write_text(
                json.dumps(
                    {
                        "type": "error",
                        "request_id": "failed-request",
                        "status": "error",
                        "stage": "failed",
                        "message": "COOKIE_REJECTED: yt-dlp failed\nWARNING: cookies invalid\nERROR: Sign in",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            host = NativeHost(native_output=False, persist_log=True)
            host.log_path = log_path
            latest = host.latest_status()
        self.assertEqual(latest["code"], "COOKIE_REJECTED")
        self.assertEqual(latest["message"], "当前 YouTube Cookie 已失效。请保持 YouTube 登录状态并重新点击插件，插件会自动更新 Cookie。")

    def test_health_endpoint_is_local_and_reports_version(self) -> None:
        server = create_http_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            self.assertEqual(host, "127.0.0.1")
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["host"], "com.youtube_note_reader.host")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_latest_endpoint_restores_a_finished_note(self) -> None:
        server = create_http_server(port=0)
        server.bridge.persist_log = False
        server.bridge.send(
            {
                "type": "complete",
                "request_id": "finished-request",
                "status": "ok",
                "stage": "complete",
                "note_path": r"C:\Vault\YouTube video\完成笔记 - Finished Note.md",
                "elapsed_seconds": 187,
            }
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _host, port = server.server_address
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/latest", timeout=3) as response:
                payload = json.loads(response.read())
            self.assertEqual(payload["request_id"], "finished-request")
            self.assertEqual(payload["elapsed_seconds"], 187)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
