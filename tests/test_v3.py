from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_frames import extract_candidate, load_plan, resolve_ffmpeg  # noqa: E402
from validate_note import validate  # noqa: E402
from video_common import VERSION, version_text  # noqa: E402
from video_note import version_report  # noqa: E402


class V3ContractTests(unittest.TestCase):
    def test_version_is_single_source(self) -> None:
        self.assertEqual(VERSION, "3.0.0")
        self.assertEqual(version_text(), "youtube-transcript 3.0.0")

    def test_version_report_contains_core_hashes(self) -> None:
        report = version_report()
        self.assertEqual(report["skill_version"], "3.0.0")
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
            (transcripts / "test.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n测试\n", encoding="utf-8")
            note = note_dir / "中文测试标题 - English Test Title.md"
            content = f"""---
skill_version: {VERSION}
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

    def test_missing_image_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = Path(raw)
            note = vault / "中文标题 - English Title.md"
            note.write_text(f"---\nskill_version: {VERSION}\n---\n\n## 一句话摘要\n", encoding="utf-8")
            codes = {item["code"] for item in validate(note, vault)}
        self.assertIn("DETAIL_IMAGES_MISSING", codes)
        self.assertIn("SECTION_MISSING", codes)

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


if __name__ == "__main__":
    unittest.main()
