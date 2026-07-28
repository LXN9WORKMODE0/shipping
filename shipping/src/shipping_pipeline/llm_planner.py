from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .llm_contracts import build_evidence_batch_schema
from .llm_model_profile import ModelProfile
from .llm_projection import project_cards
from .llm_quotes import build_quote_candidates, project_quote_candidates
from .llm_sections import AnalysisSection, SectionMap, SectionStructureError, build_section_map
from .llm_table_evidence import build_table_numeric_scopes


PLAN_SCHEMA_VERSION = "llm.paper_analysis_plan.v1"

EVIDENCE_SYSTEM_PROMPT = """你是论文证据抽取器。你的输入只来自同一篇论文。
只输出一个符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown 或解释。
必须在 material_results 中对每张材料卡输出一次且仅一次结果。材料没有明确证据时可以标记为 context、not_relevant 或 unusable，不得强迫生成观点。
disposition 为 evidence 时，证据单元必须嵌套在当前 Card 结果的 evidence_units 中且至少一条、最多12条；其他 disposition 的 evidence_units 必须为空数组。
每条 citation 只输出 quote_id，不得输出 quote 文本或 material_id，不得自行生成 quote_id。
quote_id 必须来自当前 Card 的引文候选，不得引用相邻 Card 的候选。
优先选择最短且足以支撑 claim 的候选；没有合适候选时不得生成近似引文，应将该 Card 判为 context、not_relevant 或 unusable。
claim 中出现的每一个阿拉伯数字、百分比和小数都必须逐一得到已选择 citation 支持；不能仅因数字出现在 Card 其他位置就写入 claim。通常百分号必须与数值出现在同一条 quote 中。对于 content_kind=table，若表头列名明确含“%”且数据行列数、列位置、行标签均清楚，可用“完整表头 quote + 对应数据行 quote”共同支持一个百分比；claim 必须明确写出该行标签和列标签。每条 Evidence 最多使用一个这种表头补单位的百分比，多个单元格必须拆成多个原子 Evidence。不得组合不同行列数、缺失表头、模糊标签或截断的表格行。
title、heading_path 和未选中的引文候选不是 citation。若 claim 使用的年份或其他数字只出现在当前 Card 的另一条引文候选中，必须同时选择那条候选的 quote_id；否则必须从 claim 删除该数字。不得从标题取得年份后只引用不含年份的结果句。
不得把原文中的连续数字自行解释为科学计数法；只有逐字引文本身明确包含指数符号时，claim 才能写 10 的幂或上标指数。
所有数值型表格观点都必须引用完整表头 quote 和对应数据行 quote；数据行已经直接带百分号时也不能省略表头。材料卡可能包含“表格数值引用范围”：使用其中任一 eligible_data_row_quote_ids 生成数值观点时，必须同时引用该范围的全部 required_header_quote_ids，多级表头缺一不可。若无法满足，应输出 context 或 unusable，不得生成该数值观点。相同表头、数据行、列和值定义的是同一个表格事实，不得换一种 claim 措辞重复输出。
表格观点跨多行时应选择支撑全部数值所需的所有行级 quote_id，单个 Evidence 最多8条 citation；超过上限时拆成多个原子 Evidence，不得漏选关键行。最高、最低、增减或排序结论必须引用覆盖比较范围的全部相关数据行，否则只能陈述单元格值。
claim 必须保留引文中的关键条件和模态词，例如“建议”“考虑”“可能”“预计”“计划”“约”“至少”“至多”“仅”“尚未”；不得为了简化句子而提高确定性或扩大范围。
confidence 表示当前引文对 claim 的支持清晰度，不表示作者结论必然真实；只有直接、明确且无解析歧义时才可用 high，存在上下文或解析不确定性时使用 medium 或 low，不得默认全部为 high。
不得输出 schema 之外的字段，不得生成系统 ID、论文 ID 或原文位置。"""


class PlanningError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    stage: str
    section_ids: tuple[str, ...]
    material_ids: tuple[str, ...]
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    split_reason: str
    prompt_sha256: str


@dataclass(frozen=True)
class PlannedSynthesisRequest:
    request_id: str
    stage: str
    section_id: str | None
    evidence_unit_ids: tuple[str, ...]
    input_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    prompt_sha256: str
    statement_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaperAnalysisPlan:
    schema_version: str
    paper_id: str
    generation_id: str
    scope: str
    model_profile_id: str
    model_profile_sha256: str
    tokenizer_revision: str
    strategy: str
    evidence_batches: tuple[PlannedRequest, ...]
    synthesis_strategy: str
    input_material_ids: tuple[str, ...]
    plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def build_evidence_prompts(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    projected_cards: list[dict[str, Any]],
    all_projected_cards: list[dict[str, Any]],
) -> tuple[str, str]:
    if not projected_cards:
        raise PlanningError("planning.empty_batch", "证据批次不能为空。")
    quote_candidates = build_quote_candidates(projected_cards)
    material_ids = [str(card["material_id"]) for card in projected_cards]
    projected_quote_candidates = project_quote_candidates(quote_candidates)
    quote_ids_by_material = {
        material_id: [
            str(row["quote_id"])
            for row in projected_quote_candidates
            if str(row["material_id"]) == material_id
        ]
        for material_id in material_ids
    }
    model_cards = []
    for card in projected_cards:
        material_id = str(card["material_id"])
        scoped_candidates = [
            row
            for row in quote_candidates
            if str(row["material_id"]) == material_id
        ]
        model_card = {
            **card,
            "引文候选": [
                {"quote_id": str(row["quote_id"]), "text": str(row["text"])}
                for row in projected_quote_candidates
                if str(row["material_id"]) == material_id
            ],
        }
        table_scopes = build_table_numeric_scopes(card, scoped_candidates)
        if table_scopes:
            model_card["表格数值引用范围"] = [
                {
                    "required_header_quote_ids": list(scope.header_quote_ids),
                    "eligible_data_row_quote_ids": list(scope.data_row_quote_ids),
                }
                for scope in table_scopes
            ]
        model_cards.append(model_card)
    outline: list[str] = []
    for card in all_projected_cards:
        heading_path = card["heading_path"]
        label = " > ".join(heading_path) if heading_path else card["title"]
        if label not in outline:
            outline.append(label)
    prompt_input = {
        "任务": "判定每张材料卡，并抽取有逐字引文支撑的证据单元",
        "论文": {"paper_id": paper_id, "paper_title": paper_title},
        "综述主题": topic,
        "论文结构纲要": outline,
        "输出JSONSchema": build_evidence_batch_schema(
            len(projected_cards),
            material_ids=material_ids,
            quote_candidate_ids=[str(row["quote_id"]) for row in quote_candidates],
            quote_candidate_ids_by_material=quote_ids_by_material,
        ),
        "材料卡": model_cards,
    }
    return EVIDENCE_SYSTEM_PROMPT, json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def plan_paper_analysis(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    generation_id: str,
    materials: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    structure_path: Path | None = None,
) -> PaperAnalysisPlan:
    projected = project_cards(materials)
    material_ids = tuple(str(card["material_id"]) for card in projected)
    projected_by_id = {str(card["material_id"]): card for card in projected}
    entire, entire_failure = _try_request(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        generation_id=generation_id,
        material_ids=material_ids,
        section_ids=(),
        projected_by_id=projected_by_id,
        all_projected=projected,
        profile=profile,
        token_counter=token_counter,
        split_reason="full_paper_within_budget",
        max_card_count=None,
    )
    if entire is not None:
        return _finish_plan(
            paper_id=paper_id,
            generation_id=generation_id,
            profile=profile,
            strategy="single_evidence_batch",
            evidence_batches=(entire,),
            synthesis_strategy="direct_paper_synthesis",
            input_material_ids=material_ids,
        )

    if structure_path is None:
        raise PlanningError(
            "planning.section_structure_unreliable",
            f"整篇超预算（{entire_failure}）但未提供 structure.json。",
        )
    try:
        section_map = build_section_map(
            structure_path,
            materials,
            paper_id=paper_id,
            generation_id=generation_id,
        )
    except SectionStructureError as exc:
        raise PlanningError(exc.code, str(exc)) from exc
    batches = _plan_section_batches(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        generation_id=generation_id,
        material_ids=material_ids,
        projected_by_id=projected_by_id,
        all_projected=projected,
        profile=profile,
        token_counter=token_counter,
        section_map=section_map,
        full_paper_failure=str(entire_failure),
    )
    planned_ids = tuple(material_id for batch in batches for material_id in batch.material_ids)
    if planned_ids != material_ids:
        raise PlanningError("planning.material_coverage_invalid", "章节规划未保持完整、有序的 Card 集合。")
    return _finish_plan(
        paper_id=paper_id,
        generation_id=generation_id,
        profile=profile,
        strategy="section_evidence_batches",
        evidence_batches=tuple(batches),
        synthesis_strategy="section_then_paper_synthesis",
        input_material_ids=material_ids,
    )


def plan_synthesis_request(
    *,
    generation_id: str,
    stage: str,
    section_id: str | None,
    evidence_unit_ids: tuple[str, ...],
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    profile: ModelProfile,
    token_counter: Any,
    statement_ids: tuple[str, ...] = (),
) -> PlannedSynthesisRequest:
    count = token_counter.count_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    if count.prompt_tokens + max_output_tokens + profile.safety_margin_tokens > profile.context_window_tokens:
        raise PlanningError(
            "planning.synthesis_context_window_exceeded",
            f"stage={stage!r}, section_id={section_id!r}, input_tokens={count.prompt_tokens}。",
        )
    prompt_sha256 = "sha256:" + hashlib.sha256(
        (system_prompt + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()
    identity = {
        "paper_generation": generation_id,
        "stage": stage,
        "section_id": section_id,
        "evidence_unit_ids": evidence_unit_ids,
        "statement_ids": statement_ids,
        "prompt_sha256": prompt_sha256,
    }
    return PlannedSynthesisRequest(
        request_id=f"{stage}_" + _sha256_json(identity)[:16],
        stage=stage,
        section_id=section_id,
        evidence_unit_ids=evidence_unit_ids,
        input_tokens=count.prompt_tokens,
        max_output_tokens=max_output_tokens,
        safety_margin_tokens=profile.safety_margin_tokens,
        prompt_sha256=prompt_sha256,
        statement_ids=statement_ids,
    )


def _plan_section_batches(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    generation_id: str,
    material_ids: tuple[str, ...],
    projected_by_id: dict[str, dict[str, Any]],
    all_projected: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap,
    full_paper_failure: str,
) -> list[PlannedRequest]:
    sections_by_id = {section.section_id: section for section in section_map.sections}
    children: dict[str, list[str]] = {section.section_id: [] for section in section_map.sections}
    for section in section_map.sections:
        if section.parent_section_id:
            children[section.parent_section_id].append(section.section_id)
    root_ids = [section.section_id for section in section_map.sections if section.parent_section_id is None]
    order = {material_id: index for index, material_id in enumerate(material_ids)}
    roots_with_materials: list[tuple[int, str, tuple[str, ...]]] = []
    for root_id in root_ids:
        descendants = _descendants(root_id, children)
        ids = tuple(
            material_id
            for material_id in material_ids
            if section_map.material_to_section[material_id] in descendants
        )
        if ids:
            roots_with_materials.append((order[ids[0]], root_id, ids))
    roots_with_materials.sort()
    planned: list[PlannedRequest] = []
    for _, root_id, ids in roots_with_materials:
        planned.extend(
            _plan_section_group(
                paper_id=paper_id,
                paper_title=paper_title,
                topic=topic,
                generation_id=generation_id,
                section_id=root_id,
                material_ids=ids,
                projected_by_id=projected_by_id,
                all_projected=all_projected,
                profile=profile,
                token_counter=token_counter,
                section_map=section_map,
                sections_by_id=sections_by_id,
                children=children,
                split_reason=f"full_paper.{full_paper_failure}:level1",
            )
        )
    return planned


def _plan_section_group(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    generation_id: str,
    section_id: str,
    material_ids: tuple[str, ...],
    projected_by_id: dict[str, dict[str, Any]],
    all_projected: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap,
    sections_by_id: dict[str, AnalysisSection],
    children: dict[str, list[str]],
    split_reason: str,
) -> list[PlannedRequest]:
    section_ids = _section_ids_for_materials(section_id, material_ids, section_map, sections_by_id)
    request, failure = _try_request(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        generation_id=generation_id,
        material_ids=material_ids,
        section_ids=section_ids,
        projected_by_id=projected_by_id,
        all_projected=all_projected,
        profile=profile,
        token_counter=token_counter,
        split_reason=split_reason,
        max_card_count=profile.large_paper_evidence_batch_max_cards,
    )
    if request is not None:
        return [request]
    if len(material_ids) == 1:
        raise PlanningError(
            "planning.single_material_over_budget",
            f"material_id={material_ids[0]!r}, reason={failure}。",
        )

    section = sections_by_id[section_id]
    child_ids = children[section_id]
    if (
        section.level >= 3
        or section.level == 0
        or (failure == "identity_card_limit_exceeded" and not child_ids)
    ):
        return _pack_cards(
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            generation_id=generation_id,
            section_id=section_id,
            material_ids=material_ids,
            projected_by_id=projected_by_id,
            all_projected=all_projected,
            profile=profile,
            token_counter=token_counter,
            section_map=section_map,
            sections_by_id=sections_by_id,
            split_reason=f"section.{failure}:card_boundary",
        )
    if not child_ids:
        raise PlanningError(
            "planning.section_structure_unreliable",
            f"章节 {section_id!r} 超预算但没有可验证的下一级章节。",
        )

    child_descendants = {child_id: _descendants(child_id, children) for child_id in child_ids}
    grouped: list[tuple[str, tuple[str, ...]]] = []
    direct = tuple(
        material_id
        for material_id in material_ids
        if section_map.material_to_section[material_id] == section_id
    )
    if direct:
        grouped.append((section_id, direct))
    for child_id in child_ids:
        ids = tuple(
            material_id
            for material_id in material_ids
            if section_map.material_to_section[material_id] in child_descendants[child_id]
        )
        if ids:
            grouped.append((child_id, ids))
    grouped.sort(key=lambda item: material_ids.index(item[1][0]))
    if tuple(material_id for _, ids in grouped for material_id in ids) != material_ids:
        raise PlanningError("planning.material_coverage_invalid", f"章节 {section_id!r} 子分区覆盖不完整。")

    planned: list[PlannedRequest] = []
    for child_id, ids in grouped:
        if child_id == section_id:
            direct_request, direct_failure = _try_request(
                paper_id=paper_id,
                paper_title=paper_title,
                topic=topic,
                generation_id=generation_id,
                material_ids=ids,
                section_ids=(section_id,),
                projected_by_id=projected_by_id,
                all_projected=all_projected,
                profile=profile,
                token_counter=token_counter,
                split_reason=f"section.{failure}:direct_content",
                max_card_count=profile.large_paper_evidence_batch_max_cards,
            )
            if direct_request is None:
                if direct_failure == "identity_card_limit_exceeded":
                    planned.extend(
                        _pack_cards(
                            paper_id=paper_id,
                            paper_title=paper_title,
                            topic=topic,
                            generation_id=generation_id,
                            section_id=section_id,
                            material_ids=ids,
                            projected_by_id=projected_by_id,
                            all_projected=all_projected,
                            profile=profile,
                            token_counter=token_counter,
                            section_map=section_map,
                            sections_by_id=sections_by_id,
                            split_reason=f"section.{direct_failure}:direct_content_card_boundary",
                        )
                    )
                    continue
                raise PlanningError(
                    "planning.section_structure_unreliable",
                    f"章节 {section_id!r} 的直属内容超预算且没有更细结构：{direct_failure}。",
                )
            planned.append(direct_request)
            continue
        planned.extend(
            _plan_section_group(
                paper_id=paper_id,
                paper_title=paper_title,
                topic=topic,
                generation_id=generation_id,
                section_id=child_id,
                material_ids=ids,
                projected_by_id=projected_by_id,
                all_projected=all_projected,
                profile=profile,
                token_counter=token_counter,
                section_map=section_map,
                sections_by_id=sections_by_id,
                children=children,
                split_reason=f"section.{failure}:level{sections_by_id[child_id].level}",
            )
        )
    return planned


def _pack_cards(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    generation_id: str,
    section_id: str,
    material_ids: tuple[str, ...],
    projected_by_id: dict[str, dict[str, Any]],
    all_projected: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    section_map: SectionMap,
    sections_by_id: dict[str, AnalysisSection],
    split_reason: str,
) -> list[PlannedRequest]:
    batches: list[PlannedRequest] = []
    current: list[str] = []
    for material_id in material_ids:
        trial = tuple([*current, material_id])
        section_ids = _section_ids_for_materials(section_id, trial, section_map, sections_by_id)
        request, failure = _try_request(
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            generation_id=generation_id,
            material_ids=trial,
            section_ids=section_ids,
            projected_by_id=projected_by_id,
            all_projected=all_projected,
            profile=profile,
            token_counter=token_counter,
            split_reason=split_reason,
            max_card_count=profile.large_paper_evidence_batch_max_cards,
        )
        if request is not None:
            current.append(material_id)
            continue
        if not current:
            raise PlanningError(
                "planning.single_material_over_budget",
                f"material_id={material_id!r}, reason={failure}。",
            )
        final_ids = tuple(current)
        final_request, _ = _try_request(
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            generation_id=generation_id,
            material_ids=final_ids,
            section_ids=_section_ids_for_materials(section_id, final_ids, section_map, sections_by_id),
            projected_by_id=projected_by_id,
            all_projected=all_projected,
            profile=profile,
            token_counter=token_counter,
            split_reason=split_reason,
            max_card_count=profile.large_paper_evidence_batch_max_cards,
        )
        if final_request is None:
            raise AssertionError("已接受的 Card 装箱批次不可重建。")
        batches.append(final_request)
        current = [material_id]
        single, single_failure = _try_request(
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            generation_id=generation_id,
            material_ids=(material_id,),
            section_ids=_section_ids_for_materials(section_id, (material_id,), section_map, sections_by_id),
            projected_by_id=projected_by_id,
            all_projected=all_projected,
            profile=profile,
            token_counter=token_counter,
            split_reason=split_reason,
            max_card_count=profile.large_paper_evidence_batch_max_cards,
        )
        if single is None:
            raise PlanningError(
                "planning.single_material_over_budget",
                f"material_id={material_id!r}, reason={single_failure}。",
            )
    if current:
        final_ids = tuple(current)
        final_request, _ = _try_request(
            paper_id=paper_id,
            paper_title=paper_title,
            topic=topic,
            generation_id=generation_id,
            material_ids=final_ids,
            section_ids=_section_ids_for_materials(section_id, final_ids, section_map, sections_by_id),
            projected_by_id=projected_by_id,
            all_projected=all_projected,
            profile=profile,
            token_counter=token_counter,
            split_reason=split_reason,
            max_card_count=profile.large_paper_evidence_batch_max_cards,
        )
        if final_request is None:
            raise AssertionError("Card 装箱尾批不可重建。")
        batches.append(final_request)
    return batches


def _try_request(
    *,
    paper_id: str,
    paper_title: str,
    topic: str,
    generation_id: str,
    material_ids: tuple[str, ...],
    section_ids: tuple[str, ...],
    projected_by_id: dict[str, dict[str, Any]],
    all_projected: list[dict[str, Any]],
    profile: ModelProfile,
    token_counter: Any,
    split_reason: str,
    max_card_count: int | None,
) -> tuple[PlannedRequest | None, str | None]:
    if max_card_count is not None and len(material_ids) > max_card_count:
        return None, "identity_card_limit_exceeded"
    cards = [projected_by_id[material_id] for material_id in material_ids]
    system_prompt, user_prompt = build_evidence_prompts(
        paper_id=paper_id,
        paper_title=paper_title,
        topic=topic,
        projected_cards=cards,
        all_projected_cards=all_projected,
    )
    count = token_counter.count_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    max_output_tokens = profile.evidence_output_tokens(len(cards))
    if max_output_tokens > profile.evidence_max_output_tokens:
        return None, "output_budget_exceeded"
    if count.prompt_tokens + max_output_tokens + profile.safety_margin_tokens > profile.context_window_tokens:
        return None, "context_window_exceeded"
    prompt_sha256 = "sha256:" + hashlib.sha256(
        (system_prompt + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()
    request_payload = {
        "paper_generation": generation_id,
        "stage": "evidence",
        "material_ids": material_ids,
        "prompt_sha256": prompt_sha256,
    }
    request_id = "evidence_" + _sha256_json(request_payload)[:16]
    return (
        PlannedRequest(
            request_id=request_id,
            stage="evidence",
            section_ids=section_ids,
            material_ids=material_ids,
            input_tokens=count.prompt_tokens,
            max_output_tokens=max_output_tokens,
            safety_margin_tokens=profile.safety_margin_tokens,
            split_reason=split_reason,
            prompt_sha256=prompt_sha256,
        ),
        None,
    )


def _finish_plan(
    *,
    paper_id: str,
    generation_id: str,
    profile: ModelProfile,
    strategy: str,
    evidence_batches: tuple[PlannedRequest, ...],
    synthesis_strategy: str,
    input_material_ids: tuple[str, ...],
) -> PaperAnalysisPlan:
    draft = PaperAnalysisPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        paper_id=paper_id,
        generation_id=generation_id,
        scope="full_paper",
        model_profile_id=profile.profile_id,
        model_profile_sha256=profile.sha256,
        tokenizer_revision=profile.tokenizer_revision,
        strategy=strategy,
        evidence_batches=evidence_batches,
        synthesis_strategy=synthesis_strategy,
        input_material_ids=input_material_ids,
        plan_sha256="",
    )
    payload = dataclasses.asdict(draft)
    payload.pop("plan_sha256")
    return replace(draft, plan_sha256="sha256:" + _sha256_json(payload))


def _section_ids_for_materials(
    root_id: str,
    material_ids: tuple[str, ...],
    section_map: SectionMap,
    sections_by_id: dict[str, AnalysisSection],
) -> tuple[str, ...]:
    included = {root_id}
    for material_id in material_ids:
        section_id: str | None = section_map.material_to_section[material_id]
        while section_id is not None:
            included.add(section_id)
            if section_id == root_id:
                break
            section_id = sections_by_id[section_id].parent_section_id
    return tuple(
        section.section_id for section in section_map.sections if section.section_id in included
    )


def _descendants(section_id: str, children: dict[str, list[str]]) -> set[str]:
    result = {section_id}
    pending = list(children[section_id])
    while pending:
        child = pending.pop()
        result.add(child)
        pending.extend(children[child])
    return result


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
