from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm_contracts import (
    build_non_used_evidence_disposition_schema,
    build_paper_analysis_draft_schema,
    build_section_summary_schema,
)
from .llm_sections import AnalysisSection, SectionMap


PAPER_ROOT_SECTION_ID = "paper:root"

SECTION_SUMMARY_SYSTEM_PROMPT = """你是单篇论文的章节证据综合器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
只能使用输入中的原始 evidence_unit_id，不得创造、改写或遗漏证据身份。
每个输入 evidence_unit_id 必须且只能有一个处置；used 必须被 claim 引用，其他处置不得被引用。
处置与原因只能使用以下组合：used/supports_claim，redundant/duplicate_support，peripheral/background_only、supporting_detail 或 low_confidence，excluded/low_confidence 或 off_topic。"""

PAPER_SYNTHESIS_SYSTEM_PROMPT = """你是单篇论文证据综合器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
只能使用输入中可追溯的 evidence_unit_id，不能新增事实或证据。
输出形状必须直接服从 Schema：research_focus 是单个对象；methods、core_findings、limitations、review_uses、unresolved_questions 必须直接输出 JSON 数组 `[...]`。严禁把任何数组包装成 `{"items": [...]}` 或其他对象。
research_focus、methods、core_findings、limitations、review_uses 和 unresolved_questions 中的每条自由文本都必须引用输入中存在且直接相关的 evidence_unit_id。
除 review_uses 外，所有 statement 都是论文内容记录层，只能写所列证据直接支持的内容，不得加入分析者解释、后果判断、价值判断或常识推断；review_uses 才允许在完整事实前提上形成明确的综述用途推论。
每个 statement 必须保持原子化，一条只表达一个可核验命题；不同方法步骤、比较关系、因果解释或限制后果必须拆成不同条目，不能用长句把多条证据拼成一个新判断。
优先沿用证据中的术语、对象范围和限定语。不得用更宽或更具体的近义表达替换原证据，例如不能把“运价”扩成“运输成本”，不能把“小船”具体化为证据未列出的吨位。
其中每个数值、比例、样本规模、比较关系、因果关系和程度词都必须由该 statement 所列的 evidence_unit_id 直接支持，不得读取其他未列证据后把事实写入 statement。
research_focus 只概括研究对象、目标和明确建模范围；methods 每条只记录一个方法组件或步骤；core_findings 不得追加证据未明说的原因或解释。对于政策分析、SWOT分析和案例研究，作者明确纳入分析并用于构成优势、劣势、机会、威胁、现状判断或对策依据的政策条件、市场预测和外部约束，可以进入 core_findings；与论文分析没有明确关系的一般背景不得进入。
limitations 只能记录证据明确陈述的假设、范围限制或作者局限，不得根据“只考虑 X”继续推断“未考虑 Y”的具体后果，也不得追加“影响全面性”“精确度受限”等评价。
不得仅根据某段文字没有提及某项内容，就推断整篇论文缺少该内容；只有输入证据明确陈述局限、未来研究方向或未解决问题时，才能形成相应 limitation 或 unresolved_question。
没有证据明确陈述开放问题时，unresolved_questions 必须输出空数组；严禁生成 evidence_unit_ids 为空的条目。"""

NON_USED_EVIDENCE_DISPOSITION_SYSTEM_PROMPT = """你是单篇论文综合后的未采用证据分类器。
只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
输入证据均未被论文分析草稿引用，禁止把任何证据标为 used。
每个输入 evidence_unit_id 必须且只能出现一次，不得创造、改写或遗漏证据身份。
处置与原因只能使用以下组合：redundant/duplicate_support，peripheral/background_only、supporting_detail 或 low_confidence，excluded/low_confidence 或 off_topic。
只有内容已被草稿中的其他证据实质覆盖时才能标为 redundant；仅作背景、置信度较低或偏离主题时按对应原因分类。
与主题相关、可作为方法或数据支持细节，但因综合结果保持精简而未被引用时，必须标为 peripheral/supporting_detail，不得伪装成背景或重复证据。"""


class SynthesisStructureError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceSectionAssignment:
    evidence_to_section: dict[str, str]
    cross_section_evidence_ids: tuple[str, ...]


def build_direct_paper_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    quality_flags: list[str],
    disposition_counts: dict[str, int],
    evidence_units: list[dict[str, Any]],
    adjudication_constraints: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    evidence_ids = [str(unit["evidence_unit_id"]) for unit in evidence_units]
    prompt_input = {
        "任务": "仅根据已验证证据生成单篇论文分析草稿；证据处置由后续步骤处理",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "Card质量标记": quality_flags,
        "材料判定统计": disposition_counts,
        "输出JSONSchema": build_paper_analysis_draft_schema(evidence_ids),
        "已验证证据单元": evidence_units,
    }
    system_prompt = PAPER_SYNTHESIS_SYSTEM_PROMPT
    if adjudication_constraints:
        prompt_input["人工确认的字段职责缺陷"] = adjudication_constraints
        system_prompt += (
            "\n输入中的人工确认字段职责缺陷是上一候选的禁止复现模式；必须重新生成对应字段，"
            "不得原样或同义复述被确认错位的陈述。"
        )
    return system_prompt, _compact_json(prompt_input)


def build_section_summary_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    section: AnalysisSection,
    direct_evidence_units: list[dict[str, Any]],
    child_section_summaries: list[dict[str, Any]],
) -> tuple[str, str]:
    evidence_ids = [
        str(unit["evidence_unit_id"])
        for unit in direct_evidence_units
    ]
    for summary in child_section_summaries:
        evidence_ids.extend(
            str(row["evidence_unit_id"])
            for row in summary["evidence_dispositions"]
        )
    prompt_input = {
        "任务": "综合当前章节的直接证据和已经验证的子章节摘要",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "章节": {
            "section_id": section.section_id,
            "title": section.title,
            "level": section.level,
        },
        "输出JSONSchema": build_section_summary_schema(section.section_id, evidence_ids),
        "当前章节直接证据": direct_evidence_units,
        "子章节摘要": child_section_summaries,
    }
    return SECTION_SUMMARY_SYSTEM_PROMPT, _compact_json(prompt_input)


def build_hierarchical_paper_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    root_section_summaries: list[dict[str, Any]],
    paper_root_evidence_units: list[dict[str, Any]],
    cross_section_evidence_ids: tuple[str, ...],
    adjudication_constraints: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    evidence_ids = []
    for summary in root_section_summaries:
        evidence_ids.extend(
            str(row["evidence_unit_id"])
            for row in summary["evidence_dispositions"]
        )
    evidence_ids.extend(str(unit["evidence_unit_id"]) for unit in paper_root_evidence_units)
    prompt_input = {
        "任务": "根据可追溯的章节摘要和跨章节证据生成单篇论文分析草稿；证据处置由后续步骤处理",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "输出JSONSchema": build_paper_analysis_draft_schema(evidence_ids),
        "章节摘要": root_section_summaries,
        "论文根节点证据": paper_root_evidence_units,
        "跨章节证据ID": list(cross_section_evidence_ids),
    }
    system_prompt = PAPER_SYNTHESIS_SYSTEM_PROMPT
    if adjudication_constraints:
        prompt_input["人工确认的字段职责缺陷"] = adjudication_constraints
        system_prompt += (
            "\n输入中的人工确认字段职责缺陷是上一候选的禁止复现模式；必须重新生成对应字段，"
            "不得原样或同义复述被确认错位的陈述。"
        )
    return system_prompt, _compact_json(prompt_input)


def build_non_used_evidence_disposition_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    paper_analysis_draft: dict[str, Any],
    non_used_evidence_units: list[dict[str, Any]],
) -> tuple[str, str]:
    evidence_ids = [str(unit["evidence_unit_id"]) for unit in non_used_evidence_units]
    projected_units = [
        {
            "evidence_unit_id": str(unit["evidence_unit_id"]),
            "evidence_type": unit.get("evidence_type"),
            "claim": unit.get("claim"),
            "relevance": unit.get("relevance"),
            "confidence": unit.get("confidence"),
            "caveats": unit.get("caveats", []),
        }
        for unit in non_used_evidence_units
    ]
    prompt_input = {
        "任务": "对论文分析草稿未引用的证据逐条分类",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "论文分析草稿": paper_analysis_draft,
        "输出JSONSchema": build_non_used_evidence_disposition_schema(evidence_ids),
        "未引用证据单元": projected_units,
    }
    return NON_USED_EVIDENCE_DISPOSITION_SYSTEM_PROMPT, _compact_json(prompt_input)


def assign_evidence_to_sections(
    evidence_units: list[dict[str, Any]],
    section_map: SectionMap,
) -> EvidenceSectionAssignment:
    sections = {section.section_id: section for section in section_map.sections}
    evidence_to_section: dict[str, str] = {}
    cross: list[str] = []
    for unit in evidence_units:
        evidence_id = str(unit["evidence_unit_id"])
        citation_sections: set[str] = set()
        for citation in unit.get("citations", []):
            material_id = str(citation.get("material_id", ""))
            section_id = section_map.material_to_section.get(material_id)
            if section_id is None:
                raise SynthesisStructureError(
                    f"证据引用的 material_id 不在章节映射中：{material_id!r}。"
                )
            citation_sections.add(section_id)
        if not citation_sections:
            raise SynthesisStructureError(f"证据没有可用于章节定位的 citations：{evidence_id!r}。")
        common = _lowest_common_ancestor(citation_sections, sections)
        if common is None:
            evidence_to_section[evidence_id] = PAPER_ROOT_SECTION_ID
            cross.append(evidence_id)
        else:
            evidence_to_section[evidence_id] = common
            if len(citation_sections) > 1:
                cross.append(evidence_id)
    return EvidenceSectionAssignment(evidence_to_section, tuple(cross))


def build_final_evidence_coverage(
    paper_analysis: dict[str, Any],
    all_evidence_ids: set[str],
) -> dict[str, list[str]]:
    used = {
        str(row["evidence_unit_id"])
        for row in paper_analysis["evidence_dispositions"]
        if row["disposition"] == "used"
    }
    not_used = all_evidence_ids - used
    unresolved = all_evidence_ids - (used | not_used)
    if used & not_used or unresolved:
        raise SynthesisStructureError("最终证据采用集合无法形成互斥全覆盖。")
    return {
        "all_evidence_unit_ids": sorted(all_evidence_ids),
        "used_in_final_analysis_ids": sorted(used),
        "not_used_in_final_analysis_ids": sorted(not_used),
        "unresolved_evidence_unit_ids": sorted(unresolved),
    }


def summary_evidence_ids(summary: dict[str, Any]) -> set[str]:
    return {str(row["evidence_unit_id"]) for row in summary["evidence_dispositions"]}


def _lowest_common_ancestor(
    section_ids: set[str],
    sections: dict[str, AnalysisSection],
) -> str | None:
    ancestor_paths = [_ancestor_path(section_id, sections) for section_id in section_ids]
    common = set(ancestor_paths[0])
    for path in ancestor_paths[1:]:
        common &= set(path)
    if not common:
        return None
    return max(common, key=lambda section_id: sections[section_id].level)


def _ancestor_path(section_id: str, sections: dict[str, AnalysisSection]) -> list[str]:
    result: list[str] = []
    current: str | None = section_id
    while current is not None:
        if current not in sections:
            raise SynthesisStructureError(f"章节父链引用未知 section_id：{current!r}。")
        result.append(current)
        current = sections[current].parent_section_id
    return result


def _compact_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
