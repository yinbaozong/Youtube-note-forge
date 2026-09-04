from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_frames import attach_manifest_to_note, extract_candidate, load_plan, resolve_ffmpeg  # noqa: E402
from extract_transcript import (  # noqa: E402
    RunResult,
    SubtitleChoice,
    download_subtitle,
    image_quality_score,
    is_transient_subtitle_failure,
    reprobe_subtitle_choice,
    summarize_ytdlp_failure,
    transcript_unavailable_error,
)
from validate_note import validate  # noqa: E402
from video_common import VERSION, version_text  # noqa: E402
from video_note import version_report  # noqa: E402


class V3ContractTests(unittest.TestCase):
    def test_subtitle_download_failure_is_not_misreported_as_no_subtitles(self) -> None:
        error = transcript_unavailable_error(
            SubtitleChoice(lang="en", source="manual"),
            "ERROR: HTTP Error 429: Too Many Requests",
        )
        self.assertEqual(error.code, "SUBTITLE_DOWNLOAD_FAILED")
        self.assertIn("en", str(error))
        self.assertIn("429", str(error))

    def test_video_without_subtitle_still_reports_subtitle_unavailable(self) -> None:
        error = transcript_unavailable_error(None, "")
        self.assertEqual(error.code, "SUBTITLE_UNAVAILABLE")

    def test_subtitle_download_has_a_bounded_timeout(self) -> None:
        class FakeRunner:
            timeout: int | None = None

            def run(self, args, *, purpose, check, timeout=None):
                self.timeout = timeout
                return RunResult(args=args, returncode=1, stdout="", stderr="timeout", credential_label="")

        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner()
            download_subtitle(
                runner,  # type: ignore[arg-type]
                "https://www.youtube.com/watch?v=J1WoNuemKOg",
                {"id": "J1WoNuemKOg"},
                SubtitleChoice(lang="en", source="manual"),
                Path(raw),
            )
        self.assertEqual(runner.timeout, 45)

    def test_version_is_single_source(self) -> None:
        self.assertEqual(VERSION, "4.0.3")
        self.assertEqual(version_text(), "youtube-transcript 4.0.3")

    def test_version_report_contains_core_hashes(self) -> None:
        report = version_report()
        self.assertEqual(report["skill_version"], "4.0.3")
        self.assertIn("scripts/extract_frames.py", report["core_sha256"])

    def test_frame_plan_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "plan.json"
            path.write_text('{"frames":[{"section_id":"步骤一","timestamp":"00:00:03","purpose":"展示界面","required":true}]}', encoding="utf-8-sig")
            plan = load_plan(path)
        self.assertEqual(plan[0].timestamp, 3.0)
        self.assertTrue(plan[0].required)

    def test_finished_note_passes_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note_dir = vault / "YouTube video"
            assets = note_dir / "assets"
            transcripts = note_dir / "transcripts"
            assets.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            (assets / "frame.jpg").write_bytes(b"image")
            (assets / "frame-manifest.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "skill_version": VERSION,
                        "frames": [
                            {
                                "section_id": "步骤一",
                                "timestamp": 3.0,
                                "purpose": "展示关键界面",
                                "required": True,
                                "path": str(assets / "frame.jpg"),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (transcripts / "test.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
            note = note_dir / "中文测试标题 - English Test Title.md"
            content = f"""---
skill_version: {VERSION}
quality_profile_version: 1
frame_manifest: "YouTube video/assets/frame-manifest.json"
---

## 一句话摘要

这是一份用于验证中文视频学习笔记结构的摘要，说明学习目标与视频价值。

## 核心知识点速览

- 理解素材、证据和行动之间的关系。

## 详细内容总结

这一段详细解释关键步骤、因果关系和实际应用，帮助学习者把抽象概念转化为可以复习的行动。

![[YouTube video/assets/frame.jpg|720]]

这张图展示了关键操作界面，用来证明正文中描述的步骤确实发生在视频里。

## 重点难点解析

重点是把视频证据放回对应论点，难点是不要把图片当成装饰。

## 可视化总结

使用简洁的流程关系帮助复习。

## 学习图谱

前置知识包括基本概念、实践经验和后续复盘能力。

## 行动建议-举一反三

先完成一个小练习，再把方法迁移到自己的真实项目中。

## 专业术语表

| 术语 | 英文全称 | 中文说明 |
| --- | --- | --- |
| 关键帧 | Keyframe | 用于支持正文证据的代表性画面。 |

## 原始字幕 Transcript

- SRT：[[YouTube video/transcripts/test.srt]]
"""
            note.write_text(content, encoding="utf-8")
            self.assertEqual(validate(note, vault), [])

            markdown_image = content.replace(
                "![[YouTube video/assets/frame.jpg|720]]",
                "![关键操作界面](YouTube video/assets/frame.jpg)",
            )
            note.write_text(markdown_image, encoding="utf-8")
            self.assertIn("MARKDOWN_IMAGE_PATH_UNSAFE", {item["code"] for item in validate(note, vault)})

            escaped_markdown_image = content.replace(
                "![[YouTube video/assets/frame.jpg|720]]",
                "![关键操作界面](<YouTube video/assets/frame.jpg>)",
            )
            note.write_text(escaped_markdown_image, encoding="utf-8")
            self.assertEqual(validate(note, vault), [])

    def test_missing_image_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "中文标题 - English Title.md"
            note.write_text(f"---\nskill_version: {VERSION}\n---\n\n## 一句话摘要\n", encoding="utf-8")
            codes = {item["code"] for item in validate(note, vault)}
        self.assertIn("DETAIL_IMAGES_MISSING", codes)
        self.assertIn("SECTION_MISSING", codes)
        self.assertIn("FRAME_MANIFEST_MISSING", codes)

    def test_cover_cannot_replace_a_manifest_frame(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "中文标题 - English Title.md"
            cover = vault / "cover.jpg"
            manifest = vault / "frame-manifest.json"
            cover.write_bytes(b"cover")
            manifest.write_text(
                json.dumps({"status": "ok", "skill_version": VERSION, "frames": [{"path": str(cover), "required": True}]}),
                encoding="utf-8",
            )
            note.write_text(
                f"---\nskill_version: \"{VERSION}\"\nframe_manifest: \"frame-manifest.json\"\n---\n\n"
                "## 详细内容总结\n\n![[cover.jpg]]\n\n这张图片只是视频封面，不能证明正文中的具体操作步骤。\n",
                encoding="utf-8",
            )
            codes = {item["code"] for item in validate(note, vault)}
        self.assertIn("COVER_USED_AS_FRAME", codes)

    def test_frame_extraction_attaches_manifest_to_note_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "note.md"
            manifest = vault / "assets" / "frame-manifest.json"
            manifest.parent.mkdir()
            manifest.write_text("{}", encoding="utf-8")
            note.write_text(f"---\nskill_version: {VERSION}\n---\n\nsource", encoding="utf-8")
            attach_manifest_to_note(note, manifest, vault)
            text = note.read_text(encoding="utf-8")
        self.assertIn('frame_manifest: "assets/frame-manifest.json"', text)

    def test_only_blank_black_or_white_frames_are_rejected(self) -> None:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            black = root / "black.jpg"
            white = root / "white.jpg"
            terminal_a = root / "terminal-a.jpg"
            terminal_b = root / "terminal-b.jpg"
            Image.new("RGB", (1280, 720), "black").save(black, quality=95)
            Image.new("RGB", (1280, 720), "white").save(white, quality=95)
            for path, text in ((terminal_a, "$ git status"), (terminal_b, "$ git commit")):
                image = Image.new("RGB", (1280, 720), "#101010")
                ImageDraw.Draw(image).text((80, 80), text, fill="white")
                image.save(path, quality=95)
            self.assertLess(image_quality_score(black), 0)
            self.assertLess(image_quality_score(white), 0)
            self.assertGreaterEqual(image_quality_score(terminal_a), 0)
            self.assertGreaterEqual(image_quality_score(terminal_b), 0)
        self.assertNotIn("DUPLICATE_FRAME", (ROOT / "scripts" / "extract_frames.py").read_text(encoding="utf-8"))

    def test_final_note_rejects_source_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "中文标题 - English Title.md"
            note.write_text(f"---\nskill_version: {VERSION}\n---\n\n## 来源状态\n\n重复来源。", encoding="utf-8")
            codes = {item["code"] for item in validate(note, vault)}
        self.assertIn("SCAFFOLD_SECTION_PRESENT", codes)

    def test_direct_frame_extraction_uses_a_local_stream_without_download(self) -> None:
        ffmpeg = resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            video = root / "sample.mp4"
            image = root / "frame.jpg"
            generated = subprocess.run(
                [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=1", "-t", "4", "-pix_fmt", "yuv420p", str(video)],
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(generated.returncode, 0)
            self.assertTrue(extract_candidate(ffmpeg, str(video), 1.0, image, 10))
            self.assertGreater(image.stat().st_size, 10_000)


class SubtitleDiagnosisTests(unittest.TestCase):
    IMPERSONATE_WARNING = (
        "WARNING: The extractor specified to use impersonation for this download, but no "
        "impersonate target is available. If you encounter errors, then see "
        "https://github.com/yt-dlp/yt-dlp#impersonation for information on installing the "
        "required dependencies"
    )

    def raw_failure(self) -> str:
        return (
            "yt-dlp failed while trying to download zh-Hans subtitles.\n\n"
            "[cookies file C:\\Users\\someone\\.config\\opencode\\credentials"
            "\\youtube-transcript\\cookies.youtube.txt]\n"
            f"{self.IMPERSONATE_WARNING}\n"
            "ERROR: unable to download video subtitles for 'zh-Hans': HTTP Error 429: Too Many Requests\n"
        )

    def test_fatal_line_survives_long_credential_paths_and_warnings(self) -> None:
        detail = summarize_ytdlp_failure(self.raw_failure())
        self.assertIn("429", detail)
        self.assertIn("Too Many Requests", detail)
        self.assertNotIn("cookies.youtube.txt", detail)
        self.assertNotIn("impersonate target", detail)

    def test_reported_failure_explains_the_real_cause_not_the_warning(self) -> None:
        error = transcript_unavailable_error(
            SubtitleChoice(lang="zh-Hans", source="automatic"),
            self.raw_failure(),
        )
        self.assertEqual(error.code, "SUBTITLE_DOWNLOAD_FAILED")
        self.assertIn("429", str(error))
        self.assertIn("curl-cffi", error.action)

    def test_warning_is_kept_when_it_is_the_only_available_detail(self) -> None:
        detail = summarize_ytdlp_failure(f"[no cookies]\n{self.IMPERSONATE_WARNING}\n")
        self.assertIn("impersonate target", detail)

    def test_urls_are_redacted_and_output_stays_bounded(self) -> None:
        detail = summarize_ytdlp_failure("ERROR: broken https://example.com/a?b=c " + "x" * 500)
        self.assertIn("[URL]", detail)
        self.assertNotIn("example.com", detail)
        self.assertLessEqual(len(detail), 300)

    def test_throttled_subtitle_download_is_retried_before_giving_up(self) -> None:
        class ThrottledRunner:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, args, *, purpose, check, timeout=None):
                self.calls += 1
                return RunResult(
                    args=args,
                    returncode=1,
                    stdout="",
                    stderr="ERROR: HTTP Error 429: Too Many Requests",
                    credential_label="",
                )

        runner = ThrottledRunner()
        with tempfile.TemporaryDirectory() as raw, mock.patch("extract_transcript.time.sleep"):
            path, error = download_subtitle(
                runner,  # type: ignore[arg-type]
                "https://www.youtube.com/watch?v=J1WoNuemKOg",
                {"id": "J1WoNuemKOg"},
                SubtitleChoice(lang="zh-Hans", source="automatic"),
                Path(raw),
            )
        self.assertEqual(runner.calls, 2)
        self.assertIsNone(path)
        self.assertIn("429", error)

    def test_permanent_subtitle_failure_is_not_retried(self) -> None:
        class MissingTrackRunner:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, args, *, purpose, check, timeout=None):
                self.calls += 1
                return RunResult(
                    args=args,
                    returncode=1,
                    stdout="",
                    stderr="ERROR: requested format is not available",
                    credential_label="",
                )

        runner = MissingTrackRunner()
        with tempfile.TemporaryDirectory() as raw:
            download_subtitle(
                runner,  # type: ignore[arg-type]
                "https://www.youtube.com/watch?v=J1WoNuemKOg",
                {"id": "J1WoNuemKOg"},
                SubtitleChoice(lang="zh-Hans", source="automatic"),
                Path(raw),
            )
        self.assertEqual(runner.calls, 1)

    def test_retry_succeeds_after_a_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_dir = Path(raw)

            class RecoveringRunner:
                def __init__(self) -> None:
                    self.calls = 0

                def run(self, args, *, purpose, check, timeout=None):
                    self.calls += 1
                    if self.calls == 1:
                        return RunResult(args=args, returncode=1, stdout="", stderr="timed out", credential_label="")
                    (tmp_dir / "J1WoNuemKOg.zh-Hans.vtt").write_text("WEBVTT\n", encoding="utf-8")
                    return RunResult(args=args, returncode=0, stdout="", stderr="", credential_label="")

            runner = RecoveringRunner()
            with mock.patch("extract_transcript.time.sleep"):
                path, error = download_subtitle(
                    runner,  # type: ignore[arg-type]
                    "https://www.youtube.com/watch?v=J1WoNuemKOg",
                    {"id": "J1WoNuemKOg"},
                    SubtitleChoice(lang="zh-Hans", source="automatic"),
                    tmp_dir,
                )
            self.assertEqual(runner.calls, 2)
            self.assertIsNotNone(path)
            self.assertEqual(error, "")

    def test_transient_marker_detection(self) -> None:
        self.assertTrue(is_transient_subtitle_failure("HTTP Error 403: Forbidden"))
        self.assertTrue(is_transient_subtitle_failure("Connection reset by peer"))
        self.assertFalse(is_transient_subtitle_failure("requested format is not available"))

    def test_downgraded_player_client_does_not_report_captions_as_missing(self) -> None:
        class ReprobeRunner:
            def __init__(self) -> None:
                self.args: list[str] = []

            def run(self, args, *, purpose, check, timeout=None):
                self.args = list(args)
                payload = {"id": "J1WoNuemKOg", "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]}}
                return RunResult(args=args, returncode=0, stdout=json.dumps(payload), stderr="", credential_label="")

        runner = ReprobeRunner()
        choice = reprobe_subtitle_choice(runner, "https://www.youtube.com/watch?v=J1WoNuemKOg", "zh-Hans")
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.lang, "zh-Hans")
        self.assertEqual(choice.source, "automatic")
        self.assertIn("youtube:player_client=web_safari,web,default", runner.args)

    def test_reprobe_stays_silent_when_the_video_really_has_no_captions(self) -> None:
        class EmptyRunner:
            def run(self, args, *, purpose, check, timeout=None):
                payload = {"id": "J1WoNuemKOg", "subtitles": {}, "automatic_captions": {}}
                return RunResult(args=args, returncode=0, stdout=json.dumps(payload), stderr="", credential_label="")

        self.assertIsNone(
            reprobe_subtitle_choice(EmptyRunner(), "https://www.youtube.com/watch?v=J1WoNuemKOg", "zh-Hans")  # type: ignore[arg-type]
        )

    def test_reprobe_tolerates_unusable_output(self) -> None:
        class BrokenRunner:
            def run(self, args, *, purpose, check, timeout=None):
                return RunResult(args=args, returncode=0, stdout="not json", stderr="", credential_label="")

        self.assertIsNone(
            reprobe_subtitle_choice(BrokenRunner(), "https://www.youtube.com/watch?v=J1WoNuemKOg", "zh-Hans")  # type: ignore[arg-type]
        )

    def test_impersonation_dependency_is_declared(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("curl-cffi", requirements)


if __name__ == "__main__":
    unittest.main()
