from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


CHAPTER_RE = re.compile(r"^\s*第\s*([0-9]+|[一二三四五六七八九十百〇零两]+)\s*章(?:\s|$)", re.IGNORECASE)
ENGLISH_CHAPTER_RE = re.compile(r"^\s*Chapter\s+([0-9]+)\b", re.IGNORECASE)
ABSTRACT_TITLES = {"摘要": "front:abstract", "abstract": "front:abstract_en"}
BACK_TITLES = {
    "参考文献": "references",
    "references": "references",
    "附录": "appendix",
    "appendix": "appendix",
}


class SectionStructureError(ValueError):
    code = "planning.section_structure_unreliable"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class AnalysisSection:
    section_id: str
    title: str
    level: int
    start_line: int
    end_line: int
    parent_section_id: str | None
    material_ids: tuple[str, ...]


@dataclass(frozen=True)
class SectionMap:
    paper_id: str
    generation_id: str
    sections: tuple[AnalysisSection, ...]
    material_to_section: dict[str, str]
    source_sha256: str


@dataclass(frozen=True)
class _Anchor:
    section_id: str
    title: str
    level: int
    start_line: int
    parent_section_id: str | None


def build_section_map(
    structure_source: Path | dict[str, Any],
    materials: list[dict[str, Any]],
    *,
    paper_id: str,
    generation_id: str,
) -> SectionMap:
    structure, source_sha256 = _load_structure(structure_source)
    if structure.get("paper_id") != paper_id:
        raise SectionStructureError("structure.json 的 paper_id 与分析论文不一致。")
    _validate_material_generation(materials, paper_id, generation_id)
    selected = structure.get("selected_segment")
    if not isinstance(selected, dict):
        raise SectionStructureError("缺少 selected_segment。")
    selected_end = _positive_line(selected.get("end_line"), "selected_segment.end_line")

    anchors = _collect_anchors(structure.get("headings"))
    if not any(anchor.section_id.startswith("chapter:") for anchor in anchors):
        raise SectionStructureError("没有显式“第…章”或“Chapter …”一级章节锚点。")
    sections = _partition_anchors(anchors, selected_end)
    material_to_section: dict[str, str] = {}
    section_materials: dict[str, list[str]] = {section.section_id: [] for section in sections}
    for material in materials:
        material_id = str(material["material_id"])
        span = material.get("source_span")
        if not isinstance(span, dict):
            raise SectionStructureError(f"Card 缺少 source_span：material_id={material_id!r}。")
        start_line = _positive_line(span.get("start_line"), f"{material_id}.start_line")
        end_line = _positive_line(span.get("end_line"), f"{material_id}.end_line")
        if end_line < start_line:
            raise SectionStructureError(f"Card 来源范围倒置：material_id={material_id!r}。")
        start_sections = [section for section in sections if section.start_line <= start_line <= section.end_line]
        end_sections = [section for section in sections if section.start_line <= end_line <= section.end_line]
        if len(start_sections) != 1 or len(end_sections) != 1:
            raise SectionStructureError(f"Card 落在章节分区之外：material_id={material_id!r}。")
        if start_sections[0].section_id != end_sections[0].section_id:
            raise SectionStructureError(f"Card 跨越章节边界：material_id={material_id!r}。")
        section_id = start_sections[0].section_id
        material_to_section[material_id] = section_id
        section_materials[section_id].append(material_id)

    material_ids = [str(material["material_id"]) for material in materials]
    if len(material_ids) != len(set(material_ids)):
        raise SectionStructureError("Card 存在重复 material_id。")
    if set(material_to_section) != set(material_ids):
        raise SectionStructureError("章节映射没有完整覆盖全部 Card。")
    populated = tuple(
        replace(section, material_ids=tuple(section_materials[section.section_id])) for section in sections
    )
    return SectionMap(
        paper_id=paper_id,
        generation_id=generation_id,
        sections=populated,
        material_to_section=material_to_section,
        source_sha256=source_sha256,
    )


def _load_structure(source: Path | dict[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(source, Path):
        try:
            raw = source.read_bytes()
            payload = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SectionStructureError(f"无法读取结构文件：{source}") from exc
        source_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    elif isinstance(source, dict):
        payload = source
        raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        source_sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    else:
        raise SectionStructureError("结构输入必须是 Path 或 JSON 对象。")
    if not isinstance(payload, dict):
        raise SectionStructureError("结构文件根节点必须是对象。")
    return payload, source_sha256


def _validate_material_generation(materials: list[dict[str, Any]], paper_id: str, generation_id: str) -> None:
    if not materials:
        raise SectionStructureError("Card 集合不能为空。")
    for material in materials:
        if material.get("paper_id") != paper_id:
            raise SectionStructureError("Card 的 paper_id 与分析论文不一致。")
        if material.get("generation_id") != generation_id:
            raise SectionStructureError("Card generation 与分析 generation 不一致。")


def _collect_anchors(headings: object) -> list[_Anchor]:
    if not isinstance(headings, list):
        raise SectionStructureError("结构文件缺少 headings 数组。")
    anchors: list[_Anchor] = []
    current_chapter: int | None = None
    current_chapter_id: str | None = None
    ordinal_ids: dict[tuple[int, ...], str] = {}
    last_sibling: dict[tuple[int, ...], int] = {}
    last_chapter = 0
    seen_ids: set[str] = set()
    for row in sorted(headings, key=lambda item: item.get("start_line", 0) if isinstance(item, dict) else 0):
        if not isinstance(row, dict):
            raise SectionStructureError("heading 条目必须是对象。")
        title = row.get("title_norm") or row.get("title_raw")
        if not isinstance(title, str) or not title.strip():
            raise SectionStructureError("heading 缺少规范标题。")
        title = title.strip()
        start_line = _positive_line(row.get("start_line"), f"heading {title!r}.start_line")
        normalized = re.sub(r"\s+", " ", title).strip()
        lowered = normalized.casefold()

        if current_chapter is None and lowered in ABSTRACT_TITLES:
            section_id = ABSTRACT_TITLES[lowered]
            _append_anchor(anchors, seen_ids, section_id, title, 0, start_line, None)
            continue

        chapter_number = _chapter_number(normalized)
        if chapter_number is not None:
            ordinal = _ordinal_path(row.get("ordinal_path"), title)
            if ordinal and ordinal != (chapter_number,):
                raise SectionStructureError(f"一级章标题与 ordinal_path 冲突：{title!r}。")
            if chapter_number <= last_chapter:
                raise SectionStructureError(f"一级章节编号未严格递增：{title!r}。")
            last_chapter = chapter_number
            current_chapter = chapter_number
            current_chapter_id = f"chapter:{chapter_number}"
            ordinal_ids = {(chapter_number,): current_chapter_id}
            last_sibling = {}
            _append_anchor(anchors, seen_ids, current_chapter_id, title, 1, start_line, None)
            continue

        back_key = _back_key(lowered)
        if back_key is not None:
            _append_anchor(anchors, seen_ids, f"back:{back_key}", title, 0, start_line, None)
            continue

        if current_chapter is None or current_chapter_id is None:
            continue
        ordinal = _ordinal_path(row.get("ordinal_path"), title)
        if len(ordinal) not in {2, 3} or ordinal[0] != current_chapter:
            continue
        depth = row.get("depth")
        if type(depth) is not int or depth != len(ordinal):
            raise SectionStructureError(f"标题深度与 ordinal_path 冲突：{title!r}。")
        parent_path = ordinal[:-1]
        parent_id = ordinal_ids.get(parent_path)
        if parent_id is None:
            # 孤立的细粒度编号不能成为拆分边界，也不能据此补造父章节。
            continue
        if ordinal[-1] <= last_sibling.get(parent_path, 0):
            raise SectionStructureError(f"同级章节编号未严格递增：{title!r}。")
        last_sibling[parent_path] = ordinal[-1]
        ordinal_text = ".".join(str(item) for item in ordinal)
        section_id = f"chapter:{current_chapter}/section:{ordinal_text}"
        ordinal_ids[ordinal] = section_id
        _append_anchor(anchors, seen_ids, section_id, title, len(ordinal), start_line, parent_id)
    return anchors


def _append_anchor(
    anchors: list[_Anchor],
    seen_ids: set[str],
    section_id: str,
    title: str,
    level: int,
    start_line: int,
    parent_section_id: str | None,
) -> None:
    if section_id in seen_ids:
        raise SectionStructureError(f"章节身份重复：{section_id!r}。")
    if anchors and start_line <= anchors[-1].start_line:
        raise SectionStructureError("章节锚点行号未严格递增。")
    seen_ids.add(section_id)
    anchors.append(_Anchor(section_id, title, level, start_line, parent_section_id))


def _partition_anchors(anchors: list[_Anchor], selected_end: int) -> tuple[AnalysisSection, ...]:
    sections: list[AnalysisSection] = []
    for index, anchor in enumerate(anchors):
        end_line = anchors[index + 1].start_line - 1 if index + 1 < len(anchors) else selected_end
        if end_line < anchor.start_line:
            raise SectionStructureError(f"章节范围重叠：{anchor.section_id!r}。")
        sections.append(
            AnalysisSection(
                section_id=anchor.section_id,
                title=anchor.title,
                level=anchor.level,
                start_line=anchor.start_line,
                end_line=end_line,
                parent_section_id=anchor.parent_section_id,
                material_ids=(),
            )
        )
    return tuple(sections)


def _chapter_number(title: str) -> int | None:
    match = CHAPTER_RE.match(title)
    if match:
        token = match.group(1)
        return int(token) if token.isdigit() else _chinese_integer(token)
    match = ENGLISH_CHAPTER_RE.match(title)
    return int(match.group(1)) if match else None


def _chinese_integer(token: str) -> int:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "百" in token:
        left, right = token.split("百", 1)
        return (digits.get(left, 1) * 100) + (_chinese_integer(right) if right else 0)
    if "十" in token:
        left, right = token.split("十", 1)
        return (digits.get(left, 1) * 10) + (digits.get(right, 0) if right else 0)
    if token in digits:
        return digits[token]
    value = 0
    for char in token:
        if char not in digits:
            raise SectionStructureError(f"无法解析中文章节编号：{token!r}。")
        value = value * 10 + digits[char]
    return value


def _ordinal_path(value: object, title: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(type(item) is not int or item < 1 for item in value):
        raise SectionStructureError(f"heading ordinal_path 非法：{title!r}。")
    return tuple(value)


def _back_key(lowered_title: str) -> str | None:
    for prefix, key in BACK_TITLES.items():
        if lowered_title == prefix or lowered_title.startswith(prefix + " "):
            return key
    return None


def _positive_line(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise SectionStructureError(f"{field} 必须是正整数。")
    return value
