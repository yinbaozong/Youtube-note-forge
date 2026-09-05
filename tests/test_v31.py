from __future__ import annotations

import functools
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_frames import (  # noqa: E402
    FrameRequest,
    FrameSource,
    classify_frame_exception,
    extract_candidate,
    extract_hls_frame,
    extract_hls_frames_parallel,
    load_hls_segments,
    load_plan_document,
    manifest_name_for_plan,
    parse_hls_media_playlist,
    resolve_ffmpeg,
    select_hls_segment,
    select_source,
)
from validate_note import validate  # noqa: E402
from video_common import Deadline  # noqa: E402


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
                source = FrameSource(
                    f"http://127.0.0.1:{server.server_address[1]}/playlist.m3u8",
                    {},
                    "m3u8_native",
                )
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
                "---\nskill_version: 4.1.0\nquality_profile_version: 1\nduration: 00:20:00\n---\n\n"
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


class ChromeObsidianContractTests(unittest.TestCase):
    def test_extension_targets_obsidian_plugin_without_native_messaging(self) -> None:
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        self.assertEqual(manifest["version"], "4.1.0")
        self.assertNotIn("nativeMessaging", manifest["permissions"])
        self.assertIn("cookies", manifest["permissions"])
        self.assertIn("http://127.0.0.1:32191/*", manifest["host_permissions"])
        self.assertIn("chrome.cookies.getAll", worker)
        self.assertIn("http://127.0.0.1:32191", worker)
        self.assertNotIn("connectNative", worker)
        self.assertNotIn("opencode run", worker.lower())

    def test_chrome_does_not_own_model_key_or_vault_settings(self) -> None:
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        options = (ROOT / "extension" / "options.html").read_text(encoding="utf-8")
        self.assertNotIn('id="apiKey"', options)
        self.assertNotIn('name="apiKey"', options)
        self.assertNotIn('<input', options)
        self.assertNotIn("api_key:", worker)
        self.assertNotIn("model:", worker)
        self.assertNotIn("vault:", worker)
        self.assertIn("open_obsidian_settings", worker)

    def test_popup_supports_progress_resume_stop_paths_and_asr_consent(self) -> None:
        popup = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
        script = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        for element_id in (
            "progressText",
            "timeline",
            "copyPath",
            "copyPhotosPath",
            "retryConnection",
            "resume",
            "cancel",
            "retryAsr",
        ):
            self.assertIn(f'id="{element_id}"', popup)
        self.assertIn("强制停止", popup)
        self.assertIn("文件位置", script)
        self.assertIn("照片位置", script)
        self.assertIn("总耗时", script)
        self.assertIn("allow_asr", script)

    def test_background_state_survives_popup_closure(self) -> None:
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        jobs = (ROOT / "obsidian-plugin" / "src" / "job-manager.ts").read_text(encoding="utf-8")
        self.assertIn("chrome.storage.session.set", worker)
        self.assertIn("chrome.action.setBadgeText", worker)
        self.assertIn("pluginActive", worker)
        self.assertIn("pluginLatest", worker)
        self.assertIn("active_request_id", worker)
        self.assertIn("TASK_INTERRUPTED", worker)
        self.assertIn('type: "clear_job"', worker)
        self.assertIn('type === "clear_job"', jobs)
        self.assertIn("isConnectionError(state)", worker)
        self.assertIn("连接已恢复", worker)

    def test_full_installer_uses_obsidian_and_removes_legacy_runtime(self) -> None:
        installer = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_install.ps1").read_text(encoding="utf-8")
        packager = (ROOT / "scripts" / "package_release.ps1").read_text(encoding="utf-8")
        self.assertIn("obsidian-plugin", installer)
        self.assertIn("$pluginBuildReady", installer)
        self.assertIn("if (-not $pluginBuildReady)", installer)
        self.assertIn("community-plugins.json", installer)
        self.assertIn("YouTubeNoteReader", installer)
        self.assertIn("$parsedPlugins", verifier)
        self.assertIn("ForEach-Object { [string]$_ }", verifier)
        self.assertNotIn("opencode run", installer.lower())
        self.assertIn("Legacy desktop companion: absent", verifier)
        self.assertIn("http://127.0.0.1:32191/*", verifier)
        self.assertIn("npm run build", packager)
        self.assertIn("main.js", packager)

    def test_plugin_source_is_present_and_does_not_import_old_host(self) -> None:
        manifest = json.loads((ROOT / "obsidian-plugin" / "manifest.json").read_text(encoding="utf-8"))
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "obsidian-plugin" / "src").glob("*.ts")
        )
        self.assertEqual(manifest["id"], "youtube-note-reader")
        self.assertEqual(manifest["version"], "4.1.0")
        self.assertNotIn("youtube_reader_host", sources)
        self.assertNotIn("opencode run", sources.lower())
        http_server = (ROOT / "obsidian-plugin" / "src" / "http-server.ts").read_text(encoding="utf-8")
        origin = (ROOT / "obsidian-plugin" / "src" / "origin.ts").read_text(encoding="utf-8")
        self.assertIn("isAllowedExtensionOrigin", http_server)
        self.assertIn('parsed.protocol === "chrome-extension:"', origin)
        self.assertIn("^[a-z0-9-]+$", origin)
        self.assertNotIn("obcfabljhffpdbcaebficbfpdpinnhgh", http_server)

    def test_api_key_is_saved_by_an_explicit_button(self) -> None:
        settings = (ROOT / "obsidian-plugin" / "src" / "settings.ts").read_text(encoding="utf-8")
        self.assertIn('.setButtonText("保存 API Key")', settings)
        self.assertIn('.setButtonText("验证 API Key")', settings)
        self.assertIn("validateApiKey", settings)
        self.assertIn("let pendingApiKey", settings)
        self.assertNotIn('text.setValue("");\n          });', settings)

    def test_browser_connection_page_has_explicit_connection_and_key_status(self) -> None:
        options = (ROOT / "extension" / "options.html").read_text(encoding="utf-8")
        script = (ROOT / "extension" / "options.js").read_text(encoding="utf-8")
        worker = (ROOT / "extension" / "service_worker.js").read_text(encoding="utf-8")
        jobs = (ROOT / "obsidian-plugin" / "src" / "job-manager.ts").read_text(encoding="utf-8")
        styles = (ROOT / "extension" / "options.css").read_text(encoding="utf-8")
        self.assertIn('id="apiKeyStatus"', options)
        self.assertIn('id="validateApiKey"', options)
        self.assertIn("connection-card connected", script)
        self.assertIn("connection-card disconnected", script)
        self.assertIn("api_key_configured", script)
        self.assertIn('type: "validate_api_key"', script)
        self.assertIn('message.type === "validate_api_key"', worker)
        self.assertIn('type === "validate_api_key"', jobs)
        self.assertIn(".connection-card.connected", styles)
        self.assertIn(".connection-card.disconnected", styles)


if __name__ == "__main__":
    unittest.main()
