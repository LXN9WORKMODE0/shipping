from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from shipping_pipeline.content_blocks import ContentBlock
from shipping_pipeline.markdown_structure import SourceSpan


MATERIAL_SCHEMA_VERSION = "material.v2"
MATERIAL_TARGET_CHARS = 1100
MATERIAL_MAX_CHARS = 1400
MATERIAL_MIN_TAIL_CHARS = 200
LEAD_EXCERPT_MAX_CHARS = 280
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;])\s*")


@dataclass
class _BlockGroup:
    heading_path: tuple[str, ...]
    content_kind: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass
class _PackedBlocks:
    blocks: list[ContentBlock]
    quality_flags: list[str] = field(default_factory=list)

    @property
    def extract(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


def build_material_cards(
    paper_id: str,
    paper_title: str,
    blocks: list[ContentBlock],
    confidence_flags: list[str] | None = None,
) -> list[dict[str, Any]]:
    _validate_blocks(blocks)
    groups = _group_adjacent_blocks(blocks)
    cards: list[dict[str, Any]] = []
    id_counts: dict[str, int] = {}

    for group in groups:
        packed_items = _pack_group(group)
        for group_index, packed in enumerate(packed_items, start=1):
            order = len(cards) + 1
            cards.append(
                _material_card(
                    paper_id=paper_id,
                    paper_title=paper_title,
                    packed=packed,
                    order=order,
                    group_index=group_index,
                    group_count=len(packed_items),
                    confidence_flags=confidence_flags or [],
                    id_counts=id_counts,
                )
            )
    return cards


def _validate_blocks(blocks: list[ContentBlock]) -> None:
    block_ids = [block.block_id for block in blocks]
    if len(block_ids) != len(set(block_ids)):
        raise ValueError("原子内容块 block_id 重复，无法证明材料覆盖率。")
    for block in blocks:
        if not block.text:
            raise ValueError(f"原子内容块为空：{block.block_id}")
        if len(block.text) <= MATERIAL_MAX_CHARS:
            continue
        allowed_long_table = block.content_kind == "table" and "table.row_too_long" in block.quality_flags
        if not allowed_long_table:
            raise ValueError(
                f"原子内容块超过 {MATERIAL_MAX_CHARS} 字符且未显式暴露：{block.block_id}"
            )


def _group_adjacent_blocks(blocks: list[ContentBlock]) -> list[_BlockGroup]:
    groups: list[_BlockGroup] = []
    for block in blocks:
        key = (block.heading_path, block.content_kind)
        if not groups or (groups[-1].heading_path, groups[-1].content_kind) != key:
            groups.append(_BlockGroup(block.heading_path, block.content_kind))
        groups[-1].blocks.append(block)
    return groups


def _pack_group(group: _BlockGroup) -> list[_PackedBlocks]:
    if group.content_kind in {"table", "image"}:
        return [_PackedBlocks([block]) for block in group.blocks]

    packed: list[_PackedBlocks] = []
    current: list[ContentBlock] = []
    for block in group.blocks:
        candidate = "\n\n".join(item.text for item in [*current, block])
        if current and len(candidate) > MATERIAL_TARGET_CHARS:
            packed.append(_PackedBlocks(current))
            current = [block]
        else:
            current.append(block)
    if current:
        packed.append(_PackedBlocks(current))

    if len(packed) > 1 and len(packed[-1].extract) < MATERIAL_MIN_TAIL_CHARS:
        merged = "\n\n".join([packed[-2].extract, packed[-1].extract])
        if len(merged) <= MATERIAL_MAX_CHARS:
            packed[-2].blocks.extend(packed[-1].blocks)
            packed.pop()
        else:
            packed[-1].quality_flags.append("card.short_tail")
    return packed


def _material_card(
    paper_id: str,
    paper_title: str,
    packed: _PackedBlocks,
    order: int,
    group_index: int,
    group_count: int,
    confidence_flags: list[str],
    id_counts: dict[str, int],
) -> dict[str, Any]:
    blocks = packed.blocks
    extract = packed.extract
    source_spans = [span for block in blocks for span in block.source_spans]
    source_span = _bounding_span(source_spans)
    heading_path = blocks[0].heading_path
    content_kind = blocks[0].content_kind
    raw_title = heading_path[-1] if heading_path else ""
    clean_title = _card_title(
        paper_title=paper_title,
        heading_path=heading_path,
        content_kind=content_kind,
        order=order,
        source_span=source_span,
        group_index=group_index,
        group_count=group_count,
    )
    id_base = (
        f"{paper_id}:{content_kind}:"
        f"L{source_span.start_line:05d}-L{source_span.end_line:05d}"
    )
    id_counts[id_base] = id_counts.get(id_base, 0) + 1
    material_id = f"{id_base}:{id_counts[id_base]:02d}"
    lead_excerpt = _lead_excerpt(extract)
    quality_flags = _unique_flags(
        [
            *(flag for block in blocks for flag in block.quality_flags),
            *packed.quality_flags,
        ]
    )
    parent_title = " > ".join(heading_path)
    parent_id = (
        "heading_" + hashlib.sha256(parent_title.encode("utf-8")).hexdigest()[:12]
        if parent_title
        else "unstructured"
    )
    source_payload = source_span.to_dict()
    return {
        "schema_version": MATERIAL_SCHEMA_VERSION,
        "material_id": material_id,
        "material_type": "evidence_card",
        "paper_id": paper_id,
        "paper_title": paper_title,
        "source_path": source_span.path,
        "raw_title": raw_title,
        "clean_title": clean_title,
        "parent_ref": {
            "node_id": parent_id,
            "title": parent_title,
            "raw_title": raw_title,
        },
        "order": order,
        "summary": lead_excerpt,
        "lead_excerpt": lead_excerpt,
        "extract": extract,
        "source_span": source_payload,
        "content_ref": dict(source_payload),
        "confidence_flags": _unique_flags(confidence_flags),
        "quality_flags": quality_flags,
        "content_kind": content_kind,
        "heading_path": list(heading_path),
        "source_spans": [span.to_dict() for span in source_spans],
        "source_block_ids": [block.block_id for block in blocks],
        "source_fingerprint": _fingerprint(extract),
    }


def _bounding_span(spans: list[SourceSpan]) -> SourceSpan:
    if not spans:
        raise ValueError("材料卡没有来源范围。")
    paths = {span.path for span in spans}
    if len(paths) != 1:
        raise ValueError("一张材料卡不能跨越多个来源文件。")
    if len(spans) == 1:
        return spans[0]
    return SourceSpan(
        path=spans[0].path,
        start_line=min(span.start_line for span in spans),
        end_line=max(span.end_line for span in spans),
    )


def _card_title(
    paper_title: str,
    heading_path: tuple[str, ...],
    content_kind: str,
    order: int,
    source_span: SourceSpan,
    group_index: int,
    group_count: int,
) -> str:
    if content_kind == "abstract":
        base = f"{paper_title} > 摘要"
    elif content_kind == "table":
        base = f"{' > '.join(heading_path) or paper_title} > 表格"
    elif content_kind == "image":
        base = f"{' > '.join(heading_path) or paper_title} > 图片"
    elif content_kind == "formula":
        base = f"{' > '.join(heading_path) or paper_title} > 公式"
    elif heading_path:
        base = " > ".join(heading_path)
    else:
        return f"正文片段 {order:03d}（L{source_span.start_line}-L{source_span.end_line}）"
    if group_count > 1:
        return f"{base}（片段 {group_index}/{group_count}）"
    return base


def _lead_excerpt(text: str) -> str:
    sentences = [sentence.strip() for sentence in SENTENCE_SPLIT_PATTERN.split(text) if sentence.strip()]
    selected: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*selected, sentence]).strip()
        if len(candidate) > LEAD_EXCERPT_MAX_CHARS:
            break
        selected.append(sentence)
        if len(selected) >= 2:
            break
    if selected:
        return " ".join(selected)
    return text[:LEAD_EXCERPT_MAX_CHARS].strip()


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _unique_flags(flags: list[str]) -> list[str]:
    return list(dict.fromkeys(flag for flag in flags if flag))
