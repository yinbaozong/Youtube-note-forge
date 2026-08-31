#!/usr/bin/env python3
"""Deterministic quality gate for a finished Chinese video learning note."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from video_common import VERSION, emit_progress, emit_result, version_text


SECTIONS = [
    "一句话摘要",
    "核心知识点速览",
    "详细内容总结",
    "重点难点解析",
    "可视化总结",
    "学习图谱",
    "行动建议-举一反三",
    "专业术语表",
    "原始字幕 Transcript",
]

FORBIDDEN_FINAL_SECTIONS = [
    "来源状态",
    "视频描述",
    "可用视觉素材",
    "关键画面索引",
    "提取警告",
    "学习笔记整理任务",
    "输出自检清单",
]


def split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            return text[4:end], text[end + 5 :]
    return "", text


def section_body(body: str, name: str) -> str:
    match = re.search(rf"^## {re.escape(name)}\s*$", body, flags=re.MULTILINE)
    if not match:
        return ""
    next_section = re.search(r"^## ", body[match.end() :], flags=re.MULTILINE)
    end = match.end() + (next_section.start() if next_section else len(body[match.end() :]))
    return body[match.end() : end]


def resolve_obsidian_target(raw: str, note: Path, vault: Path) -> Path | None:
    target = raw.split("|")[0].strip()
    if not target or target.startswith("http"):
        return None
    candidates = [note.parent / target, vault / target]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list(vault.rglob(Path(target).name)) if vault.exists() else []
    return matches[0] if len(matches) == 1 else None


def frontmatter_value(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*$", frontmatter, flags=re.MULTILINE)
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw:
        return None
    if raw[:1] in {'"', "'"}:
        try:
            return str(json.loads(raw)) if raw.startswith('"') else raw[1:-1]
        except (json.JSONDecodeError, IndexError):
            return raw.strip("\"'")
    return raw


def parse_duration_seconds(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        parts = [int(float(part)) for part in raw.strip().split(":")]
    except ValueError:
        return 0
    if not 1 <= len(parts) <= 3:
        return 0
    total = 0
    for part in parts:
        total = total * 60 + part
    return max(0, total)


def detailed_chapters(details: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", details, flags=re.MULTILINE))
    chapters: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(details)
        chapters.append((match.group(1).strip(), details[match.end() : end]))
    return chapters


def embedded_images(details: str) -> list[tuple[str, int]]:
    """Return Obsidian wiki embeds and standard Markdown image targets."""
    found: list[tuple[int, str, int]] = []
    for match in re.finditer(r"!\[\[([^\]]+)\]\]", details):
        found.append((match.start(), match.group(1).strip(), match.end()))
    for match in re.finditer(r"!\[[^\]]*\]\((?:<([^>]+)>|([^\n)]+))\)", details):
        target = (match.group(1) or match.group(2) or "").strip()
        found.append((match.start(), target, match.end()))
    return [(target, end) for _, target, end in sorted(found)]


def unsafe_markdown_image_targets(details: str) -> list[str]:
    """Find unescaped Markdown image paths that CommonMark truncates at spaces."""
    unsafe: list[str] = []
    for match in re.finditer(r"!\[[^\]]*\]\(([^<\n][^\n)]*)\)", details):
        target = match.group(1).strip()
        if re.search(r"\s", target):
            unsafe.append(target)
    return unsafe


def cleanup_matching_drafts(note: Path) -> tuple[list[str], list[str]]:
    """Delete only same-directory placeholder drafts with the exact final-note URL."""
    removed: list[str] = []
    warnings: list[str] = []
    if note.stem.startswith("待命名 -"):
        return removed, warnings
    try:
        final_frontmatter, _ = split_frontmatter(note.read_text(encoding="utf-8"))
    except OSError as exc:
        return removed, [f"读取终稿以清理草稿时失败：{exc}"]
    final_url = frontmatter_value(final_frontmatter, "url")
    if not final_url:
        return removed, warnings
    for candidate in note.parent.glob("待命名 - *.md"):
        if candidate.resolve() == note.resolve():
            continue
        try:
            candidate_frontmatter, _ = split_frontmatter(candidate.read_text(encoding="utf-8"))
            if frontmatter_value(candidate_frontmatter, "url") != final_url:
                continue
            candidate.unlink()
            removed.append(str(candidate))
        except OSError as exc:
            warnings.append(f"无法删除同 URL 草稿 {candidate}：{exc}")
    return removed, warnings


def load_frame_manifest(frontmatter: str, note: Path, vault: Path) -> tuple[dict, Path | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    raw = frontmatter_value(frontmatter, "frame_manifest")
    if not raw:
        return {}, None, [{"code": "FRAME_MANIFEST_MISSING", "message": "笔记 YAML 缺少 frame_manifest；必须先成功运行 extract_frames.py。"}]
    path = resolve_obsidian_target(raw, note, vault)
    if not path:
        return {}, None, [{"code": "FRAME_MANIFEST_BROKEN", "message": f"抽帧清单不存在：{raw}"}]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, path, [{"code": "FRAME_MANIFEST_INVALID", "message": f"抽帧清单无法读取：{exc}"}]
    frames = payload.get("frames")
    if payload.get("status") != "ok" or payload.get("skill_version") != VERSION or not isinstance(frames, list) or not frames:
        errors.append({"code": "FRAME_MANIFEST_INVALID", "message": "抽帧清单必须是当前版本成功生成且至少包含一张真实关键帧。"})
    return payload, path, errors


def validate(note: Path, vault: Path) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not note.exists():
        return [{"code": "NOTE_MISSING", "message": f"找不到笔记：{note}"}]
    if not re.search(r"[\u3400-\u9fff].+ - [A-Za-z]", note.stem):
        errors.append({"code": "FILENAME_INVALID", "message": "文件名必须是中文标题 - English Title。"})
    text = note.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    if frontmatter_value(frontmatter, "skill_version") != VERSION:
        errors.append({"code": "VERSION_MISSING", "message": f"YAML 必须包含 skill_version: {VERSION}。"})
    if frontmatter_value(frontmatter, "quality_profile_version") != "1":
        errors.append({"code": "QUALITY_PROFILE_MISSING", "message": "YAML 必须包含 quality_profile_version: 1。"})
    if re.search(r"^#\s+", body, flags=re.MULTILINE):
        errors.append({"code": "DUPLICATE_TITLE", "message": "正文不能包含顶层 # 标题。"})
    positions: list[int] = []
    for name in SECTIONS:
        found = re.search(rf"^## {re.escape(name)}\s*$", body, flags=re.MULTILINE)
        if not found:
            errors.append({"code": "SECTION_MISSING", "message": f"缺少章节：{name}。"})
        else:
            positions.append(found.start())
    if positions and positions != sorted(positions):
        errors.append({"code": "SECTION_ORDER", "message": "章节顺序不符合学习笔记契约。"})
    for name in FORBIDDEN_FINAL_SECTIONS:
        if re.search(rf"^## {re.escape(name)}\s*$", body, flags=re.MULTILINE):
            errors.append({"code": "SCAFFOLD_SECTION_PRESENT", "message": f"终稿必须删除脚手架章节：{name}。"})
    details = section_body(body, "详细内容总结")
    for unsafe_target in unsafe_markdown_image_targets(details):
        errors.append(
            {
                "code": "MARKDOWN_IMAGE_PATH_UNSAFE",
                "message": f"带空格的 Markdown 图片路径会被 Obsidian 截断，请改用 ![[路径]]：{unsafe_target}",
            }
        )
    images = embedded_images(details)
    manifest, _, manifest_errors = load_frame_manifest(frontmatter, note, vault)
    errors.extend(manifest_errors)
    manifest_frames: dict[Path, dict] = {}
    for frame in manifest.get("frames", []) if isinstance(manifest.get("frames"), list) else []:
        if not isinstance(frame, dict) or not frame.get("path"):
            continue
        target = resolve_obsidian_target(str(frame["path"]), note, vault)
        if target:
            manifest_frames[target.resolve()] = frame
    if not images:
        errors.append({"code": "DETAIL_IMAGES_MISSING", "message": "详细内容总结必须包含对应截图。"})
    embedded: list[Path] = []
    for image_target, image_end in images:
        target = resolve_obsidian_target(image_target, note, vault)
        if not target:
            errors.append({"code": "IMAGE_LINK_BROKEN", "message": f"截图链接无效：{image_target}"})
        else:
            resolved = target.resolve()
            embedded.append(resolved)
            if target.name.lower().startswith("cover"):
                errors.append({"code": "COVER_USED_AS_FRAME", "message": f"封面不能替代正文关键帧：{image_target}"})
            elif resolved not in manifest_frames:
                errors.append({"code": "IMAGE_NOT_IN_MANIFEST", "message": f"正文图片不在成功抽帧清单中：{image_target}"})
        nearby = details[image_end : image_end + 240]
        if len(re.findall(r"[\u3400-\u9fff]", nearby)) < 8:
            errors.append({"code": "IMAGE_CAPTION_MISSING", "message": f"截图缺少中文说明：{image_target}"})
    if len(embedded) != len(set(embedded)):
        errors.append({"code": "DUPLICATE_IMAGE_EMBED", "message": "详细内容总结重复嵌入了同一张图片。"})
    embedded_set = set(embedded)
    for path, frame in manifest_frames.items():
        if frame.get("required") and path not in embedded_set:
            errors.append({"code": "REQUIRED_FRAME_NOT_EMBEDDED", "message": f"必需关键帧未插入详细内容总结：{path.name}"})
    duration = parse_duration_seconds(frontmatter_value(frontmatter, "duration"))
    if duration:
        chinese_detail = len(re.findall(r"[\u3400-\u9fff]", details))
        minimum_detail = max(900, min(4000, int(duration / 60 * 80)))
        if chinese_detail < minimum_detail:
            errors.append(
                {
                    "code": "DETAIL_CONTENT_SHALLOW",
                    "message": f"详细内容总结只有 {chinese_detail} 个中文字符，当前视频至少需要约 {minimum_detail} 个。",
                }
            )
        chapters = detailed_chapters(details)
        minutes = duration / 60
        minimum_chapters = 3 if minutes <= 10 else 4 if minutes <= 30 else 5 if minutes <= 60 else 6
        if len(chapters) < minimum_chapters:
            errors.append(
                {
                    "code": "DETAIL_CHAPTERS_INSUFFICIENT",
                    "message": f"详细内容总结只有 {len(chapters)} 个三级章节，当前视频至少需要 {minimum_chapters} 个。",
                }
            )
        for title, chapter in chapters:
            chapter_chinese = len(re.findall(r"[\u3400-\u9fff]", chapter))
            if chapter_chinese < 120:
                errors.append(
                    {
                        "code": "DETAIL_CHAPTER_SHALLOW",
                        "message": f"详细章节“{title}”解释不足，至少需要约 120 个中文字符。",
                    }
                )
        actions = section_body(body, "行动建议-举一反三")
        if len(re.findall(r"[\u3400-\u9fff]", actions)) < 120:
            errors.append({"code": "ACTION_ADVICE_GENERIC", "message": "行动建议过于简略，必须包含具体练习、迁移应用和继续深入方向。"})
    outline = manifest.get("article_outline")
    if isinstance(outline, list) and outline:
        detail_titles = {title for title, _ in detailed_chapters(details)}
        for item in outline:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if title and title not in detail_titles:
                errors.append({"code": "OUTLINE_CHAPTER_MISSING", "message": f"详细内容总结缺少画面计划章节：{title}"})
    transcript = section_body(body, "原始字幕 Transcript")
    srt_links = re.findall(r"\[\[([^\]]+\.srt[^\]]*)\]\]", transcript, flags=re.IGNORECASE)
    if not srt_links:
        errors.append({"code": "SRT_LINK_MISSING", "message": "原始字幕章节必须只保留 SRT 链接。"})
    else:
        for link in srt_links:
            if not resolve_obsidian_target(link, note, vault):
                errors.append({"code": "SRT_LINK_BROKEN", "message": f"SRT 链接无效：{link}"})
    if "<details>" in body or "点击展开原始字幕" in body:
        errors.append({"code": "RAW_TRANSCRIPT_PRESENT", "message": "正文不能粘贴原始字幕。"})
    if "- [ ]" in body:
        errors.append({"code": "CHECKLIST_UNFINISHED", "message": "输出自检清单未清除或完成。"})
    normalized_paragraphs = [
        re.sub(r"\s+", "", paragraph)
        for paragraph in re.split(r"\n\s*\n", body)
        if len(re.findall(r"[\u3400-\u9fff]", paragraph)) >= 60
    ]
    repeated = {paragraph for paragraph in normalized_paragraphs if normalized_paragraphs.count(paragraph) > 1}
    if repeated:
        errors.append({"code": "REPEATED_CONTENT", "message": "正文包含重复的长段落，必须合并或删除。"})
    chinese = len(re.findall(r"[\u3400-\u9fff]", body))
    latin = len(re.findall(r"[A-Za-z]", re.sub(r"`[^`]*`|https?://\S+", "", body)))
    if chinese < 120 or chinese / max(1, chinese + latin) < 0.45:
        errors.append({"code": "CHINESE_CONTENT_INSUFFICIENT", "message": "正文中文内容不足或夹杂过多英文。"})
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a finished youtube-transcript learning note.")
    parser.add_argument("note", nargs="?", type=Path)
    parser.add_argument("--vault", type=Path, default=Path.home() / "Documents" / "Obsidian Vault")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(version_text())
        return 0
    if not args.note:
        parser.error("note is required unless --version is used")
    emit_progress("validation", "正在校验文章结构、深度、中文比例、截图和 SRT 链接。", percent=92)
    errors = validate(args.note, args.vault)
    if errors:
        emit_result("error", stage="note_validation", code="NOTE_VALIDATION_FAILED", note=str(args.note), errors=errors)
        return 2
    emit_progress("validation", "质量校验通过，正在清理同 URL 的待命名草稿。", percent=98)
    removed_drafts, cleanup_warnings = cleanup_matching_drafts(args.note)
    emit_result(
        "ok",
        stage="note_validation",
        note=str(args.note),
        removed_drafts=removed_drafts,
        cleanup_warnings=cleanup_warnings,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
