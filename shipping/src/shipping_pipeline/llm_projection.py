from __future__ import annotations

from typing import Any, Iterable


MODEL_CARD_FIELDS = (
    "material_id",
    "content_kind",
    "heading_path",
    "title",
    "extract",
    "parse_flags",
)


class ProjectionError(ValueError):
    pass


def project_card(material: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(material, dict):
        raise ProjectionError("Card 必须是对象。")
    material_id = _required_text(material, "material_id")
    content_kind = _required_text(material, "content_kind")
    extract = _required_text(material, "extract")
    heading_path = _string_list(material.get("heading_path", []), "heading_path")
    confidence_flags = _string_list(material.get("confidence_flags", []), "confidence_flags")
    quality_flags = _string_list(material.get("quality_flags", []), "quality_flags")
    title = _first_nonempty_text(
        material.get("clean_title"),
        material.get("raw_title"),
        material.get("paper_title"),
    )
    if title is None:
        raise ProjectionError(f"Card 缺少可用标题：material_id={material_id!r}。")
    projected = {
        "material_id": material_id,
        "content_kind": content_kind,
        "heading_path": heading_path,
        "title": title,
        "extract": extract,
        "parse_flags": _unique([*confidence_flags, *quality_flags]),
    }
    if tuple(projected) != MODEL_CARD_FIELDS:
        raise AssertionError("模型 Card 投影字段顺序发生内部漂移。")
    return projected


def project_cards(materials: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for material in materials:
        card = project_card(material)
        material_id = card["material_id"]
        if material_id in seen:
            raise ProjectionError(f"模型 Card 投影存在重复 material_id：{material_id!r}。")
        seen.add(material_id)
        projected.append(card)
    if not projected:
        raise ProjectionError("模型 Card 投影不能为空。")
    return projected


def _required_text(material: dict[str, Any], field: str) -> str:
    value = material.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"Card 字段 {field} 必须是非空字符串。")
    return value.strip()


def _first_nonempty_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ProjectionError(f"Card 字段 {field} 必须是字符串数组。")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ProjectionError(f"Card 字段 {field}[{index}] 必须是非空字符串。")
        result.append(item.strip())
    return result


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
