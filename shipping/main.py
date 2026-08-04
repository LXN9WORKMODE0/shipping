from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.pipeline import LiteraturePipeline
from shipping_pipeline.audit import BatchAuditRunner
from shipping_pipeline.material_quality import MaterialQualityAuditor
from shipping_pipeline.material_review import MaterialReviewExporter
from shipping_pipeline.llm_analysis import (
    LLMAnalysisRunner,
    LLMSemanticAutoResolutionRunner,
    LLMSemanticAdjudicationRunner,
    LLMSemanticAuditRunner,
    LLMStatementRevisionRunner,
    initialize_semantic_decisions,
)
from shipping_pipeline.llm_model_profile import load_model_profile
from shipping_pipeline.llm_provider import (
    OpenAICompatibleAnalysisClient,
    ProviderCallError,
    build_chat_request,
    extract_chat_content,
)
from shipping_pipeline.llm_review import import_review_decisions
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter
from shipping_pipeline.topic_review import (
    DEFAULT_TOPIC_REVIEW_CONFIG,
    TopicReviewRunner,
)
from shipping_pipeline.research_understanding import (
    DEFAULT_UNDERSTANDING_CONFIG,
    PaperUnderstandingRunner,
)
from shipping_pipeline.paper_understanding_batch import (
    PaperUnderstandingBatchRunner,
)
from shipping_pipeline.hierarchical_landscape import (
    HierarchicalLocalLandscapeBatchRunner,
    HierarchicalLandscapePlanRunner,
)
from shipping_pipeline.research_landscape import (
    DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
    ResearchLandscapeRunner,
)
from shipping_pipeline.review_framework import ReviewFrameworkRunner
from shipping_pipeline.review_framework_b2 import ReviewFrameworkB2Builder
from shipping_pipeline.review_writing import ReviewWritingRunner
from shipping_pipeline.review_claim_audit import (
    DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
    ReviewClaimAuditRunner,
)
from shipping_pipeline.review_direct_baseline import (
    DirectReviewBaselineRunner,
)
from shipping_pipeline.review_evaluation import (
    DEFAULT_REVIEW_EVALUATION_CONFIG,
    ReviewEvaluationRunner,
)
from shipping_pipeline.chapter_knowledge_package import (
    DEFAULT_REVIEW_WRITING_CONFIG,
    ChapterKnowledgePackageBuilder,
)
from shipping_pipeline.source_window_selection import (
    SourceWindowSelectionBuilder,
)
from shipping_pipeline.source_window_contracts import MATERIAL_TIERS
from shipping_pipeline.chapter_knowledge_package_b2 import (
    B2ChapterKnowledgePackageBuilder,
)
from shipping_pipeline.chapter_knowledge_package_b2_v2 import (
    B2PackageV2Builder,
)
from shipping_pipeline.claim_ledger import ClaimLedgerRunner
from shipping_pipeline.review_writing_b2 import ReviewWritingB2Runner
from shipping_pipeline.review_writing_b2_assembly import (
    ReviewWritingB2Assembler,
)
from shipping_pipeline.review_claim_audit_b2 import (
    DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    ReviewClaimAuditB2Runner,
)
from shipping_pipeline.review_claim_audit_b2_adjudication import (
    ReviewClaimAuditB2AdjudicationRunner,
)
from shipping_pipeline.review_claim_revision_b2 import (
    ReviewClaimRevisionB2Runner,
)
from shipping_pipeline.review_writing_b2_revision import (
    ReviewWritingB2RevisionRunner,
)
from shipping_pipeline.review_writing_b2_audited_assembly import (
    ReviewWritingB2AuditedAssembler,
)
from shipping_pipeline.review_conclusion_b2 import (
    DEFAULT_REVIEW_CONCLUSION_B2_CONFIG,
    ReviewConclusionB2Runner,
)
from shipping_pipeline.review_conclusion_audit_b2 import (
    ReviewConclusionAuditB2Runner,
)
from shipping_pipeline.review_conclusion_b2_revision import (
    ReviewConclusionRevisionB2Runner,
)
from shipping_pipeline.review_b2_final_assembly import (
    ReviewB2FinalAssembler,
)
from shipping_pipeline.review_direct_a2 import (
    DEFAULT_DIRECT_A2_CONFIG,
    ReviewDirectA2Runner,
)
from shipping_pipeline.review_ab2_evaluation import (
    ReviewAB2EvaluationRunner,
    ReviewAB2StructuralStabilityRunner,
)
from shipping_pipeline.paper_pool_inventory import PaperPoolInventoryRunner
from shipping_pipeline.paper_topic_screening import PaperPoolScreeningRunner
from shipping_pipeline.paper_pool_card_preparation import (
    PaperPoolCardPreparationRunner,
    request_paper_pool_card_action,
)
from shipping_pipeline.topic_synthesis import (
    DEFAULT_TOPIC_SYNTHESIS_CONFIG,
    TopicSynthesisRunner,
)
from shipping_pipeline.end_to_end import EndToEndPipelineRunner
from shipping_pipeline.pdf_conversion import MinerUApiClient
from shipping_pipeline.env import load_env_file
from shipping_pipeline.reference_catalog import ReferenceCatalogRunner

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_MODEL_PROFILE = (
    Path(__file__).resolve().parent
    / "config"
    / "models"
    / "deepseek-v4-pro-official.json"
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行通用文献材料处理 pipeline。")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="从这个 env 文件加载 API 配置。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="处理单篇 Markdown/PDF 来源并生成综述材料卡。")
    run_parser.add_argument("source", type=Path)
    run_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    run_parser.add_argument("--paper-id", required=True)
    run_parser.add_argument(
        "--workspace-id",
        default=None,
        help="可选的隔离工作区目录 ID；论文语义身份仍使用 --paper-id。",
    )
    run_parser.add_argument("--topic", default="")
    run_parser.add_argument("--pdf-provider", choices=["none", "mineru"], default="none")
    run_parser.add_argument("--mineru-api-url", default=None)
    run_parser.add_argument("--mineru-api-key-env", default="MINERU_API_KEY")
    run_parser.add_argument("--mineru-timeout", type=int, default=120)
    run_parser.add_argument("--mineru-max-wait-seconds", type=int, default=None)
    run_parser.add_argument("--mineru-poll-interval-seconds", type=float, default=None)
    run_parser.add_argument("--mineru-model-version", default=None)
    run_parser.add_argument("--mineru-language", default=None)
    run_parser.add_argument("--mineru-page-ranges", default=None)

    inventory_parser = subparsers.add_parser(
        "paper-pool-inventory",
        help="清点论文源目录、内容去重并与当前Card运行按哈希对账。",
    )
    inventory_parser.add_argument("source_dir", type=Path)
    inventory_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    inventory_parser.add_argument("--run-id", default=None)

    pool_screen_parser = subparsers.add_parser(
        "llm-paper-pool-screen",
        help="对清点运行中Card已就绪的论文执行独立轻量主题筛选。",
    )
    pool_screen_parser.add_argument("--inventory-run-id", required=True)
    pool_screen_parser.add_argument("--topic", required=True)
    pool_screen_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    pool_screen_parser.add_argument("--run-id", default=None)
    pool_screen_parser.add_argument("--max-papers", type=int, default=None)
    pool_screen_parser.add_argument("--resume-from-run-id", default=None)
    pool_screen_parser.add_argument(
        "--provider", choices=["openai-compatible"], default="openai-compatible"
    )
    pool_screen_parser.add_argument("--api-url", default=None)
    pool_screen_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    pool_screen_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    pool_screen_parser.add_argument("--review-config", type=Path, default=DEFAULT_TOPIC_REVIEW_CONFIG)
    pool_screen_parser.add_argument("--timeout", type=int, default=1800)

    pool_card_parser = subparsers.add_parser(
        "paper-pool-prepare-cards",
        help="对清点账本中待制卡论文执行可续跑的部分成功批处理。",
    )
    pool_card_parser.add_argument("--inventory-run-id", required=True)
    pool_card_parser.add_argument("--topic", required=True)
    pool_card_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    pool_card_parser.add_argument("--run-id", default=None)
    pool_card_parser.add_argument("--max-papers", type=int, default=None)
    pool_card_parser.add_argument("--resume-from-run-id", default=None)
    pool_card_parser.add_argument("--max-workers", type=int, default=2)
    pool_card_parser.add_argument(
        "--min-start-interval-seconds", type=float, default=1.0
    )
    pool_card_parser.add_argument("--pdf-provider", choices=["none", "mineru"], default="none")
    pool_card_parser.add_argument("--mineru-api-url", default=None)
    pool_card_parser.add_argument("--mineru-api-key-env", default="MINERU_API_KEY")
    pool_card_parser.add_argument("--mineru-timeout", type=int, default=120)
    pool_card_parser.add_argument("--mineru-max-wait-seconds", type=int, default=None)
    pool_card_parser.add_argument("--mineru-poll-interval-seconds", type=float, default=None)
    pool_card_parser.add_argument("--mineru-model-version", default=None)
    pool_card_parser.add_argument("--mineru-language", default=None)
    pool_card_parser.add_argument("--mineru-page-ranges", default=None)

    pool_card_control_parser = subparsers.add_parser(
        "paper-pool-card-control",
        help="协作式暂停或取消正在运行的全论文池Card批次。",
    )
    pool_card_control_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    pool_card_control_parser.add_argument("--run-id", required=True)
    pool_card_control_parser.add_argument("--action", choices=["pause", "cancel"], required=True)

    full_pipeline_parser = subparsers.add_parser(
        "full-pipeline",
        help="按显式论文清单运行 PDF/MD、Card、单篇简报和跨论文综合。",
    )
    full_pipeline_parser.add_argument(
        "--collection",
        type=Path,
        required=True,
    )
    full_pipeline_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    full_pipeline_parser.add_argument("--run-id", default=None)
    full_pipeline_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    full_pipeline_parser.add_argument("--api-url", default=None)
    full_pipeline_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    full_pipeline_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    full_pipeline_parser.add_argument(
        "--review-config",
        type=Path,
        default=DEFAULT_TOPIC_REVIEW_CONFIG,
    )
    full_pipeline_parser.add_argument(
        "--synthesis-config",
        type=Path,
        default=DEFAULT_TOPIC_SYNTHESIS_CONFIG,
    )
    full_pipeline_parser.add_argument("--timeout", type=int, default=120)
    full_pipeline_parser.add_argument(
        "--pdf-provider",
        choices=["none", "mineru"],
        default="none",
    )
    full_pipeline_parser.add_argument("--mineru-api-url", default=None)
    full_pipeline_parser.add_argument(
        "--mineru-api-key-env",
        default="MINERU_API_KEY",
    )
    full_pipeline_parser.add_argument("--mineru-timeout", type=int, default=120)
    full_pipeline_parser.add_argument(
        "--mineru-max-wait-seconds",
        type=int,
        default=None,
    )
    full_pipeline_parser.add_argument(
        "--mineru-poll-interval-seconds",
        type=float,
        default=None,
    )
    full_pipeline_parser.add_argument("--mineru-model-version", default=None)
    full_pipeline_parser.add_argument("--mineru-language", default=None)
    full_pipeline_parser.add_argument("--mineru-page-ranges", default=None)

    audit_parser = subparsers.add_parser("audit", help="审计已有 normalized Markdown workspace。")
    audit_parser.add_argument("source_root", type=Path)
    audit_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    audit_parser.add_argument("--topic", default="")
    audit_parser.add_argument("--run-id", default=None)

    material_audit_parser = subparsers.add_parser("material-audit", help="审计已生成材料卡的质量。")
    material_audit_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    material_audit_parser.add_argument("--run-id", default=None)

    material_review_parser = subparsers.add_parser("material-review", help="把材料卡渲染成便于人工审核的 Markdown 包。")
    material_review_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    material_review_parser.add_argument("--run-id", default=None)

    llm_analysis_parser = subparsers.add_parser("llm-analysis", help="对材料卡执行受 schema 约束的 LLM 分析。")
    llm_analysis_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_analysis_parser.add_argument("--topic", default="")
    llm_analysis_parser.add_argument("--run-id", default=None)
    llm_analysis_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    llm_analysis_parser.add_argument("--api-url", default=None)
    llm_analysis_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    llm_analysis_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    llm_analysis_parser.add_argument("--timeout", type=int, default=120)
    llm_analysis_parser.add_argument("--paper-id", required=True)
    llm_analysis_parser.add_argument("--limit", type=int, default=None)
    llm_analysis_parser.add_argument("--dry-run", action="store_true")
    llm_analysis_parser.add_argument("--retry-from", default=None)
    llm_analysis_parser.add_argument("--retry-batch-id", default=None)

    topic_brief_parser = subparsers.add_parser(
        "llm-topic-brief",
        help="围绕综述主题选择少量 Card，并生成轻量论文材料简报。",
    )
    topic_brief_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    topic_brief_parser.add_argument("--topic", required=True)
    topic_brief_parser.add_argument("--run-id", default=None)
    topic_brief_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    topic_brief_parser.add_argument("--api-url", default=None)
    topic_brief_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    topic_brief_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    topic_brief_parser.add_argument(
        "--review-config",
        type=Path,
        default=DEFAULT_TOPIC_REVIEW_CONFIG,
    )
    topic_brief_parser.add_argument("--timeout", type=int, default=120)
    topic_brief_parser.add_argument("--paper-id", required=True)
    topic_brief_parser.add_argument(
        "--workspace-id",
        default=None,
        help="论文在 workspace 下的隔离目录 ID；省略时沿用 paper-id。",
    )

    paper_understanding_parser = subparsers.add_parser(
        "llm-paper-understanding",
        help="读取单篇论文完整Markdown、全部Card和可选Evidence，生成论文认知报告。",
    )
    paper_understanding_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    paper_understanding_parser.add_argument("--paper-id", required=True)
    paper_understanding_parser.add_argument(
        "--workspace-paper-id",
        required=True,
        help="论文在workspace下的隔离目录ID。",
    )
    paper_understanding_parser.add_argument("--topic", required=True)
    paper_understanding_parser.add_argument(
        "--source-topic-review-run-id",
        default=None,
        help="可选的显式Topic Review运行ID；不提供时不自动猜测Evidence来源。",
    )
    paper_understanding_parser.add_argument("--run-id", default=None)
    paper_understanding_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    paper_understanding_parser.add_argument("--api-url", default=None)
    paper_understanding_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    paper_understanding_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    paper_understanding_parser.add_argument(
        "--understanding-config",
        type=Path,
        default=DEFAULT_UNDERSTANDING_CONFIG,
    )
    paper_understanding_parser.add_argument("--timeout", type=int, default=900)

    understanding_batch_parser = subparsers.add_parser(
        "llm-paper-understanding-batch",
        help="从全论文池筛选账本选择论文，批量生成相互隔离的Paper Understanding。",
    )
    understanding_batch_parser.add_argument("--screening-run-id", required=True)
    understanding_batch_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    understanding_batch_parser.add_argument("--run-id", default=None)
    understanding_batch_parser.add_argument(
        "--relevance", action="append", choices=["core", "supporting", "peripheral"],
        default=None, help="可重复；默认只处理core。",
    )
    understanding_batch_parser.add_argument(
        "--record-id", action="append", default=None,
        help="可重复；只处理显式指定且属于纳入等级的筛选记录。",
    )
    understanding_batch_parser.add_argument("--max-papers", type=int, default=None)
    understanding_batch_parser.add_argument("--resume-from-run-id", default=None)
    understanding_batch_parser.add_argument(
        "--provider", choices=["openai-compatible"], default="openai-compatible"
    )
    understanding_batch_parser.add_argument("--api-url", default=None)
    understanding_batch_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    understanding_batch_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    understanding_batch_parser.add_argument(
        "--understanding-config", type=Path, default=DEFAULT_UNDERSTANDING_CONFIG
    )
    understanding_batch_parser.add_argument("--timeout", type=int, default=900)

    hierarchical_plan_parser = subparsers.add_parser(
        "hierarchical-landscape-plan",
        help="严格重放Paper Understanding集合并生成可审计的主题簇与局部Landscape collections。",
    )
    hierarchical_plan_parser.add_argument("--collection", type=Path, required=True)
    hierarchical_plan_parser.add_argument("--routing", type=Path, required=True)
    hierarchical_plan_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    hierarchical_plan_parser.add_argument("--run-id", default=None)
    hierarchical_plan_parser.add_argument("--max-papers-per-cluster", type=int, default=40)
    hierarchical_plan_parser.add_argument("--max-memberships-per-paper", type=int, default=2)

    hierarchical_local_parser = subparsers.add_parser(
        "hierarchical-landscape-local-run",
        help="按层级计划逐主题簇运行旧Research Landscape，并保留部分成功账本。",
    )
    hierarchical_local_parser.add_argument("--plan-run-id", required=True)
    hierarchical_local_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    hierarchical_local_parser.add_argument("--run-id", default=None)
    hierarchical_local_parser.add_argument("--max-clusters", type=int, default=None)
    hierarchical_local_parser.add_argument("--resume-from-run-id", default=None)
    hierarchical_local_parser.add_argument("--provider", choices=["openai-compatible"], default="openai-compatible")
    hierarchical_local_parser.add_argument("--api-url", default=None)
    hierarchical_local_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    hierarchical_local_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    hierarchical_local_parser.add_argument("--landscape-config", type=Path, default=DEFAULT_RESEARCH_LANDSCAPE_CONFIG)
    hierarchical_local_parser.add_argument("--timeout", type=int, default=900)

    research_landscape_parser = subparsers.add_parser(
        "llm-research-landscape",
        help="基于显式Paper Understanding集合建立跨论文研究图谱。",
    )
    research_landscape_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    research_landscape_parser.add_argument(
        "--collection",
        type=Path,
        required=True,
    )
    research_landscape_parser.add_argument("--run-id", default=None)
    research_landscape_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    research_landscape_parser.add_argument("--api-url", default=None)
    research_landscape_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    research_landscape_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    research_landscape_parser.add_argument(
        "--landscape-config",
        type=Path,
        default=DEFAULT_RESEARCH_LANDSCAPE_CONFIG,
    )
    research_landscape_parser.add_argument("--timeout", type=int, default=900)

    review_framework_parser = subparsers.add_parser(
        "llm-review-framework",
        help="基于显式Research Landscape和题录运行生成综述框架。",
    )
    review_framework_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    review_framework_parser.add_argument(
        "--landscape-run-id",
        required=True,
    )
    review_framework_parser.add_argument(
        "--reference-catalog-run-id",
        required=True,
    )
    review_framework_parser.add_argument("--review-goal", default=None)
    review_framework_parser.add_argument("--run-id", default=None)
    review_framework_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    review_framework_parser.add_argument("--api-url", default=None)
    review_framework_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    review_framework_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    review_framework_parser.add_argument("--timeout", type=int, default=900)

    chapter_package_parser = subparsers.add_parser(
        "chapter-knowledge-package",
        help="为显式Review Framework章节装配完整知识包并校验Token预算。",
    )
    chapter_package_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    chapter_package_parser.add_argument(
        "--framework-run-id",
        required=True,
    )
    section_selector = chapter_package_parser.add_mutually_exclusive_group(
        required=True
    )
    section_selector.add_argument("--section-id")
    section_selector.add_argument("--section-index", type=int)
    chapter_package_parser.add_argument("--run-id", default=None)
    chapter_package_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    chapter_package_parser.add_argument(
        "--writing-config",
        type=Path,
        default=DEFAULT_REVIEW_WRITING_CONFIG,
    )

    source_window_parser = subparsers.add_parser(
        "source-window-selection",
        help="为显式Review Framework章节选择B2原文窗口并生成认知投影。",
    )
    source_window_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    source_window_parser.add_argument(
        "--framework-run-id",
        required=True,
    )
    source_window_selector = (
        source_window_parser.add_mutually_exclusive_group(required=True)
    )
    source_window_selector.add_argument("--section-id")
    source_window_selector.add_argument("--section-index", type=int)
    source_window_parser.add_argument(
        "--material-tier",
        choices=list(MATERIAL_TIERS),
        default="tier_1",
    )
    source_window_parser.add_argument("--run-id", default=None)

    chapter_package_b2_parser = subparsers.add_parser(
        "chapter-knowledge-package-b2-preview",
        help="基于Source Window运行构建不可写作的B2 Phase 1精简知识包。",
    )
    chapter_package_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    chapter_package_b2_parser.add_argument(
        "--source-window-run-id",
        required=True,
    )
    chapter_package_b2_parser.add_argument("--run-id", default=None)
    chapter_package_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    chapter_package_b2_parser.add_argument(
        "--writing-config",
        type=Path,
        default=DEFAULT_REVIEW_WRITING_CONFIG,
    )

    framework_b2_parser = subparsers.add_parser(
        "review-framework-b2",
        help="从冻结Review Framework v1生成带Claim预算的B2规划框架。",
    )
    framework_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    framework_b2_parser.add_argument(
        "--source-framework-run-id",
        required=True,
    )
    framework_b2_parser.add_argument(
        "--target-total-chars",
        type=int,
        default=8000,
    )
    framework_b2_parser.add_argument("--run-id", default=None)

    claim_ledger_parser = subparsers.add_parser(
        "llm-claim-ledger",
        help="基于Framework B2和Source Window生成并校验章节Claim Ledger。",
    )
    claim_ledger_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    claim_ledger_parser.add_argument(
        "--framework-b2-run-id",
        required=True,
    )
    claim_ledger_parser.add_argument(
        "--source-window-run-id",
        required=True,
    )
    claim_ledger_parser.add_argument("--run-id", default=None)
    claim_ledger_parser.add_argument(
        "--provider",
        default="openai-compatible",
    )
    claim_ledger_parser.add_argument("--api-url", default=None)
    claim_ledger_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    claim_ledger_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    claim_ledger_parser.add_argument("--timeout", type=int, default=900)

    package_b2_v2_parser = subparsers.add_parser(
        "chapter-knowledge-package-b2",
        help="装配含Approved Claim Ledger的B2 Phase 2章节知识包。",
    )
    package_b2_v2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    package_b2_v2_parser.add_argument(
        "--source-window-run-id",
        required=True,
    )
    package_b2_v2_parser.add_argument(
        "--claim-ledger-run-id",
        required=True,
    )
    package_b2_v2_parser.add_argument("--run-id", default=None)
    package_b2_v2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    package_b2_v2_parser.add_argument(
        "--writing-config",
        type=Path,
        default=DEFAULT_REVIEW_WRITING_CONFIG,
    )

    writing_b2_parser = subparsers.add_parser(
        "llm-review-writing-b2",
        help="基于单章Approved Claim Ledger执行B2受约束写作。",
    )
    writing_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    writing_b2_parser.add_argument("--package-run-id", required=True)
    writing_b2_parser.add_argument("--run-id", default=None)
    writing_b2_parser.add_argument(
        "--provider",
        default="openai-compatible",
    )
    writing_b2_parser.add_argument("--api-url", default=None)
    writing_b2_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    writing_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    writing_b2_parser.add_argument(
        "--writing-config",
        type=Path,
        default=DEFAULT_REVIEW_WRITING_CONFIG,
    )
    writing_b2_parser.add_argument("--timeout", type=int, default=900)

    assemble_b2_parser = subparsers.add_parser(
        "assemble-review-b2-phase3a",
        help="装配全部成功的B2引言和正文章节，不生成结论。",
    )
    assemble_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    assemble_b2_parser.add_argument(
        "--chapter-run-id",
        action="append",
        required=True,
        dest="chapter_run_ids",
    )
    assemble_b2_parser.add_argument("--run-id", default=None)

    audit_b2_parser = subparsers.add_parser(
        "llm-review-claim-audit-b2",
        help="对单章B2正文执行逐句Claim语义审计。",
    )
    audit_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    audit_b2_parser.add_argument("--writing-run-id", required=True)
    audit_b2_parser.add_argument("--run-id", default=None)
    audit_b2_parser.add_argument(
        "--provider",
        default="openai-compatible",
    )
    audit_b2_parser.add_argument("--api-url", default=None)
    audit_b2_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    audit_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    audit_b2_parser.add_argument(
        "--audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    )
    audit_b2_parser.add_argument("--timeout", type=int, default=900)

    adjudicate_b2_parser = subparsers.add_parser(
        "llm-review-claim-audit-b2-adjudicate",
        help="对B2初审风险句执行独立二次裁决。",
    )
    adjudicate_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    adjudicate_b2_parser.add_argument("--audit-run-id", required=True)
    adjudicate_b2_parser.add_argument("--run-id", default=None)
    adjudicate_b2_parser.add_argument(
        "--provider",
        default="openai-compatible",
    )
    adjudicate_b2_parser.add_argument("--api-url", default=None)
    adjudicate_b2_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    adjudicate_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    adjudicate_b2_parser.add_argument(
        "--audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    )
    adjudicate_b2_parser.add_argument("--timeout", type=int, default=900)

    revise_b2_parser = subparsers.add_parser(
        "llm-review-claim-revision-b2",
        help="为B2裁决后风险句生成三类定向修订建议，不自动应用。",
    )
    revise_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    revise_b2_parser.add_argument("--adjudication-run-id", required=True)
    revise_b2_parser.add_argument("--run-id", default=None)
    revise_b2_parser.add_argument(
        "--provider",
        default="openai-compatible",
    )
    revise_b2_parser.add_argument("--api-url", default=None)
    revise_b2_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    revise_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    revise_b2_parser.add_argument(
        "--audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    )
    revise_b2_parser.add_argument("--timeout", type=int, default=900)

    apply_revision_b2_parser = subparsers.add_parser(
        "apply-review-writing-revision-b2",
        help="按显式决策集确定性应用B2风险句修订，不覆盖旧章节。",
    )
    apply_revision_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    apply_revision_b2_parser.add_argument(
        "--revision-suggestion-run-id",
        required=True,
    )
    apply_revision_b2_parser.add_argument(
        "--decision-file",
        type=Path,
        required=True,
    )
    apply_revision_b2_parser.add_argument("--run-id", default=None)

    audited_assembly_b2_parser = subparsers.add_parser(
        "assemble-review-b2-audited",
        help="按显式审计发布集合装配B2前文章节，并验证零阻断门禁。",
    )
    audited_assembly_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    audited_assembly_b2_parser.add_argument(
        "--release-file",
        type=Path,
        required=True,
    )
    audited_assembly_b2_parser.add_argument("--run-id", default=None)

    conclusion_b2_parser = subparsers.add_parser(
        "llm-review-conclusion-b2",
        help="只依据受审计正文Claim目录生成独立B2结论章。",
    )
    conclusion_b2_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    conclusion_b2_parser.add_argument(
        "--audited-assembly-run-id",
        required=True,
    )
    conclusion_b2_parser.add_argument("--run-id", default=None)
    conclusion_b2_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    conclusion_b2_parser.add_argument("--api-url", default=None)
    conclusion_b2_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    conclusion_b2_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    conclusion_b2_parser.add_argument(
        "--conclusion-config",
        type=Path,
        default=DEFAULT_REVIEW_CONCLUSION_B2_CONFIG,
    )
    conclusion_b2_parser.add_argument("--timeout", type=int, default=900)

    conclusion_audit_b2_parser = subparsers.add_parser(
        "llm-review-conclusion-audit-b2",
        help="逐句审计B2候选结论是否越出受审计来源Claim。",
    )
    conclusion_audit_b2_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    conclusion_audit_b2_parser.add_argument(
        "--conclusion-run-id", required=True
    )
    conclusion_audit_b2_parser.add_argument("--run-id", default=None)
    conclusion_audit_b2_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    conclusion_audit_b2_parser.add_argument("--api-url", default=None)
    conclusion_audit_b2_parser.add_argument(
        "--api-key-env", default="LLM_ANALYSIS_API_KEY"
    )
    conclusion_audit_b2_parser.add_argument(
        "--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE
    )
    conclusion_audit_b2_parser.add_argument(
        "--audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_B2_CONFIG,
    )
    conclusion_audit_b2_parser.add_argument("--timeout", type=int, default=900)

    conclusion_revision_b2_parser = subparsers.add_parser(
        "apply-review-conclusion-revision-b2",
        help="按显式决策集确定性修订B2候选结论。",
    )
    conclusion_revision_b2_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    conclusion_revision_b2_parser.add_argument("--audit-run-id", required=True)
    conclusion_revision_b2_parser.add_argument(
        "--decision-file", type=Path, required=True
    )
    conclusion_revision_b2_parser.add_argument("--run-id", default=None)

    final_b2_parser = subparsers.add_parser(
        "assemble-review-b2-final",
        help="验证正文与结论审计来源链并装配B2正式候选综述。",
    )
    final_b2_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    final_b2_parser.add_argument("--release-file", type=Path, required=True)
    final_b2_parser.add_argument("--run-id", default=None)

    direct_a2_parser = subparsers.add_parser(
        "llm-review-direct-a2",
        help="以同一14篇全文和同一九章Framework生成公平对照A2。",
    )
    direct_a2_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    direct_a2_parser.add_argument("--source-package-run-id", required=True)
    direct_a2_parser.add_argument("--audited-assembly-run-id", required=True)
    direct_a2_parser.add_argument("--run-id", default=None)
    direct_a2_parser.add_argument(
        "--provider", choices=["openai-compatible"], default="openai-compatible"
    )
    direct_a2_parser.add_argument("--api-url", default=None)
    direct_a2_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    direct_a2_parser.add_argument(
        "--config", type=Path, default=DEFAULT_DIRECT_A2_CONFIG
    )
    direct_a2_parser.add_argument("--timeout", type=int, default=1800)

    evaluate_ab2_parser = subparsers.add_parser(
        "llm-review-evaluate-ab2",
        help="以同一全文Claim审计器和盲化结构评价比较A2与B2。",
    )
    evaluate_ab2_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    evaluate_ab2_parser.add_argument("--config", type=Path, required=True)
    evaluate_ab2_parser.add_argument("--run-id", default=None)
    evaluate_ab2_parser.add_argument(
        "--provider", choices=["openai-compatible"], default="openai-compatible"
    )
    evaluate_ab2_parser.add_argument("--api-url", default=None)
    evaluate_ab2_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    evaluate_ab2_parser.add_argument(
        "--audit-config", type=Path, default=DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG
    )
    evaluate_ab2_parser.add_argument("--resume-from-run-id", default=None)
    evaluate_ab2_parser.add_argument("--timeout", type=int, default=1800)

    evaluate_ab2_stability_parser = subparsers.add_parser(
        "llm-review-evaluate-ab2-stability",
        help="反转候选展示顺序，复评A2/B2结构评分稳定性。",
    )
    evaluate_ab2_stability_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    evaluate_ab2_stability_parser.add_argument(
        "--source-evaluation-run-id", required=True
    )
    evaluate_ab2_stability_parser.add_argument("--run-id", default=None)
    evaluate_ab2_stability_parser.add_argument(
        "--provider", choices=["openai-compatible"], default="openai-compatible"
    )
    evaluate_ab2_stability_parser.add_argument("--api-url", default=None)
    evaluate_ab2_stability_parser.add_argument(
        "--api-key-env", default="LLM_ANALYSIS_API_KEY"
    )
    evaluate_ab2_stability_parser.add_argument("--timeout", type=int, default=1800)

    review_writing_parser = subparsers.add_parser(
        "llm-review-writing",
        help="按显式章节知识包集合逐章写作，并在全部成功后确定性装配综述。",
    )
    review_writing_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    review_writing_parser.add_argument(
        "--collection",
        type=Path,
        required=True,
    )
    review_writing_parser.add_argument("--run-id", default=None)
    review_writing_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    review_writing_parser.add_argument("--api-url", default=None)
    review_writing_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    review_writing_parser.add_argument("--timeout", type=int, default=900)

    claim_audit_parser = subparsers.add_parser(
        "llm-review-claim-audit",
        help="逐章拆解并核验综述正文Claim，判定草稿是否可发布。",
    )
    claim_audit_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    claim_audit_parser.add_argument("--writing-run-id", required=True)
    claim_audit_parser.add_argument("--run-id", default=None)
    claim_audit_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    claim_audit_parser.add_argument("--api-url", default=None)
    claim_audit_parser.add_argument(
        "--api-key-env", default="LLM_ANALYSIS_API_KEY"
    )
    claim_audit_parser.add_argument(
        "--audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
    )
    claim_audit_parser.add_argument(
        "--response-replay-run-id",
        default=None,
        help="不调用API，重放指定Claim审计运行中已冻结的九章响应。",
    )
    claim_audit_parser.add_argument(
        "--resume-from-run-id",
        default=None,
        help="复用指定运行中已验证章节，仅调用API重试失败章节。",
    )
    claim_audit_parser.add_argument("--timeout", type=int, default=1800)

    direct_review_parser = subparsers.add_parser(
        "llm-review-direct-baseline",
        help="仅基于完整Markdown和题录，一次调用生成直接写作基线。",
    )
    direct_review_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    direct_review_parser.add_argument(
        "--source-package-run-id", required=True
    )
    direct_review_parser.add_argument("--topic", required=True)
    direct_review_parser.add_argument("--review-goal", required=True)
    direct_review_parser.add_argument(
        "--expected-paper-count", type=int, default=14
    )
    direct_review_parser.add_argument("--run-id", default=None)
    direct_review_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    direct_review_parser.add_argument("--api-url", default=None)
    direct_review_parser.add_argument(
        "--api-key-env", default="LLM_ANALYSIS_API_KEY"
    )
    direct_review_parser.add_argument(
        "--min-body-chars", type=int, default=4000
    )
    direct_review_parser.add_argument(
        "--max-body-chars", type=int, default=35000
    )
    direct_review_parser.add_argument(
        "--max-output-tokens", type=int, default=32768
    )
    direct_review_parser.add_argument(
        "--response-replay-run-id",
        default=None,
        help="不调用API，重放指定直接写作运行的冻结响应。",
    )
    direct_review_parser.add_argument("--timeout", type=int, default=1800)

    review_evaluation_parser = subparsers.add_parser(
        "llm-review-evaluate",
        help="对同一论文集的A/B/C三种综述写作方案执行统一评价。",
    )
    review_evaluation_parser.add_argument(
        "--workspace", type=Path, default=Path("workspace")
    )
    review_evaluation_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_REVIEW_EVALUATION_CONFIG,
    )
    review_evaluation_parser.add_argument("--run-id", default=None)
    review_evaluation_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    review_evaluation_parser.add_argument("--api-url", default=None)
    review_evaluation_parser.add_argument(
        "--api-key-env", default="LLM_ANALYSIS_API_KEY"
    )
    review_evaluation_parser.add_argument(
        "--claim-audit-config",
        type=Path,
        default=DEFAULT_REVIEW_CLAIM_AUDIT_CONFIG,
    )
    review_evaluation_parser.add_argument(
        "--resume-from-run-id",
        default=None,
        help="复用指定评价运行中已保存的候选审计和结构评价响应。",
    )
    review_evaluation_parser.add_argument("--timeout", type=int, default=1800)

    topic_synthesis_parser = subparsers.add_parser(
        "llm-topic-synthesis",
        help="把多篇单篇主题简报聚合为主题矩阵和段落级综述提纲。",
    )
    topic_synthesis_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    topic_synthesis_parser.add_argument("--collection", type=Path, required=True)
    topic_synthesis_parser.add_argument("--run-id", default=None)
    topic_synthesis_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    topic_synthesis_parser.add_argument("--api-url", default=None)
    topic_synthesis_parser.add_argument(
        "--api-key-env",
        default="LLM_ANALYSIS_API_KEY",
    )
    topic_synthesis_parser.add_argument(
        "--model-profile",
        type=Path,
        default=DEFAULT_MODEL_PROFILE,
    )
    topic_synthesis_parser.add_argument(
        "--synthesis-config",
        type=Path,
        default=DEFAULT_TOPIC_SYNTHESIS_CONFIG,
    )
    topic_synthesis_parser.add_argument("--timeout", type=int, default=120)

    reference_catalog_parser = subparsers.add_parser(
        "reference-catalog",
        help="从本地 Markdown 与显式核验表生成可审计参考文献目录。",
    )
    reference_catalog_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
    )
    reference_catalog_parser.add_argument("--project", type=Path, required=True)
    reference_catalog_parser.add_argument(
        "--synthesis-run-dir",
        type=Path,
        required=True,
    )
    reference_catalog_parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
    )
    reference_catalog_parser.add_argument(
        "--review-draft",
        type=Path,
        default=None,
        help="可选：为已有综述草稿的证据来源加入编号并附加参考文献。",
    )
    reference_catalog_parser.add_argument("--run-id", default=None)

    llm_revision_parser = subparsers.add_parser(
        "llm-analysis-revise",
        help="显式执行一次受约束的失败陈述修订并重新核验整篇分析。",
    )
    llm_revision_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_revision_parser.add_argument("--source-run-id", required=True)
    llm_revision_parser.add_argument("--run-id", default=None)
    llm_revision_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    llm_revision_parser.add_argument("--api-url", default=None)
    llm_revision_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    llm_revision_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    llm_revision_parser.add_argument("--timeout", type=int, default=120)
    llm_revision_parser.add_argument(
        "--retry-support-from",
        default=None,
        help="复用指定首轮修订候选，仅重新执行一次全文陈述支持核验。",
    )

    llm_semantic_audit_parser = subparsers.add_parser(
        "llm-semantic-audit",
        help="只读核验既有论文分析候选的 Evidence claim 和字段职责。",
    )
    llm_semantic_audit_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_semantic_audit_parser.add_argument("--source-run-id", required=True)
    llm_semantic_audit_parser.add_argument("--run-id", default=None)
    llm_semantic_audit_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    llm_semantic_audit_parser.add_argument("--api-url", default=None)
    llm_semantic_audit_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    llm_semantic_audit_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    llm_semantic_audit_parser.add_argument("--timeout", type=int, default=120)

    llm_semantic_decisions_parser = subparsers.add_parser(
        "llm-semantic-decisions-init",
        help="为已完成的语义审计生成人工裁决证据工作单和裁决表。",
    )
    llm_semantic_decisions_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_semantic_decisions_parser.add_argument("--audit-run-id", required=True)
    llm_semantic_decisions_parser.add_argument(
        "--replace-pending",
        action="store_true",
        help="仅当现有表仍全部待裁决且没有人工说明时，替换为新版证据工作单。",
    )

    llm_semantic_auto_parser = subparsers.add_parser(
        "llm-semantic-auto-resolve",
        help="受约束自动处置语义问题；可接续语义审计或上一轮自动处置运行。",
    )
    llm_semantic_auto_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_semantic_auto_parser.add_argument(
        "--audit-run-id",
        required=True,
        help="语义审计 run_id，或仍有门禁阻断的上一轮自动处置 run_id。",
    )
    llm_semantic_auto_parser.add_argument("--run-id", default=None)
    llm_semantic_auto_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    llm_semantic_auto_parser.add_argument("--api-url", default=None)
    llm_semantic_auto_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    llm_semantic_auto_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    llm_semantic_auto_parser.add_argument("--timeout", type=int, default=120)

    llm_semantic_adjudication_parser = subparsers.add_parser(
        "llm-semantic-adjudicate",
        help="按人工裁决执行一次受约束语义处置、重新综合和全文门禁。",
    )
    llm_semantic_adjudication_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_semantic_adjudication_parser.add_argument("--audit-run-id", required=True)
    llm_semantic_adjudication_parser.add_argument("--decisions", type=Path, default=None)
    llm_semantic_adjudication_parser.add_argument("--run-id", default=None)
    llm_semantic_adjudication_parser.add_argument(
        "--provider",
        choices=["openai-compatible"],
        default="openai-compatible",
    )
    llm_semantic_adjudication_parser.add_argument("--api-url", default=None)
    llm_semantic_adjudication_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    llm_semantic_adjudication_parser.add_argument("--model-profile", type=Path, default=DEFAULT_MODEL_PROFILE)
    llm_semantic_adjudication_parser.add_argument("--timeout", type=int, default=120)

    llm_review_import_parser = subparsers.add_parser("llm-review-import", help="校验并导入 LLM 分析人工审核决策。")
    llm_review_import_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    llm_review_import_parser.add_argument("--run-id", required=True)
    llm_review_import_parser.add_argument("--decisions", type=Path, default=None)

    provider_probe_parser = subparsers.add_parser(
        "llm-provider-probe",
        help="验证模型路由是否接受 Non-think 和生产输出上限。",
    )
    provider_probe_parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    provider_probe_parser.add_argument("--model-profile", type=Path, required=True)
    provider_probe_parser.add_argument("--api-url", default=None)
    provider_probe_parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    provider_probe_parser.add_argument("--timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    load_env_file(args.env_file)
    if args.command == "full-pipeline":
        result = EndToEndPipelineRunner(
            args.workspace,
            pdf_converter=_build_pdf_converter(args),
        ).run(
            collection_path=args.collection,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
            review_config_path=args.review_config,
            synthesis_config_path=args.synthesis_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "run":
        result = LiteraturePipeline(args.workspace, pdf_converter=_build_pdf_converter(args)).run(
            args.source,
            paper_id=args.paper_id,
            workspace_id=args.workspace_id,
            review_topic=args.topic,
        )
        print(
            json.dumps(
                {
                    "paper_id": result.paper_id,
                    "status": result.status,
                    "structure_quality": result.structure_quality,
                    "issue_count": result.issue_count,
                    "workspace_path": result.workspace_path,
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status == "completed" else 1
    if args.command == "paper-pool-inventory":
        result = PaperPoolInventoryRunner(args.workspace).run(
            args.source_dir, run_id=args.run_id
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-paper-pool-screen":
        result = PaperPoolScreeningRunner(args.workspace).run(
            inventory_run_id=args.inventory_run_id,
            topic=args.topic,
            run_id=args.run_id,
            max_papers=args.max_papers,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
            review_config_path=args.review_config,
            resume_from_run_id=args.resume_from_run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {
            "completed", "completed_partial", "completed_with_failures"
        } else 1
    if args.command == "paper-pool-prepare-cards":
        result = PaperPoolCardPreparationRunner(
            args.workspace, pdf_converter=_build_pdf_converter(args)
        ).run(
            inventory_run_id=args.inventory_run_id,
            topic=args.topic,
            run_id=args.run_id,
            max_papers=args.max_papers,
            resume_from_run_id=args.resume_from_run_id,
            max_workers=args.max_workers,
            min_start_interval_seconds=args.min_start_interval_seconds,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {
            "completed", "completed_partial", "completed_with_failures",
            "paused", "cancelled",
        } else 1
    if args.command == "paper-pool-card-control":
        result = request_paper_pool_card_action(
            args.workspace,
            run_id=args.run_id,
            action=args.action,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "audit":
        result = BatchAuditRunner(args.workspace).run(args.source_root, topic=args.topic, run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "material-audit":
        result = MaterialQualityAuditor(args.workspace).run(run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "material-review":
        result = MaterialReviewExporter(args.workspace).run(run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "llm-analysis":
        result = LLMAnalysisRunner(args.workspace).run(
            run_id=args.run_id,
            topic=args.topic,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            paper_id=args.paper_id,
            limit=args.limit,
            model_profile_path=args.model_profile,
            dry_run=args.dry_run,
            retry_from=args.retry_from,
            retry_batch_id=args.retry_batch_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        successful_status = result["status"] in {"completed", "dry_run_completed"}
        smoke_succeeded = result["failure_codes"] == ["scope.partial_smoke_not_publishable"]
        return 0 if successful_status or smoke_succeeded else 1
    if args.command == "llm-topic-brief":
        result = TopicReviewRunner(args.workspace).run(
            run_id=args.run_id,
            topic=args.topic,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            paper_id=args.paper_id,
            workspace_paper_id=args.workspace_id,
            model_profile_path=args.model_profile,
            review_config_path=args.review_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {
            "completed",
            "completed_with_failures",
            "completed_with_revisit_failure",
            "excluded",
        } else 1
    if args.command == "llm-paper-understanding":
        result = PaperUnderstandingRunner(args.workspace).run(
            paper_id=args.paper_id,
            workspace_paper_id=args.workspace_paper_id,
            topic=args.topic,
            source_topic_review_run_id=args.source_topic_review_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            understanding_config_path=args.understanding_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-paper-understanding-batch":
        result = PaperUnderstandingBatchRunner(args.workspace).run(
            screening_run_id=args.screening_run_id,
            run_id=args.run_id,
            relevance=args.relevance or ("core",),
            record_ids=args.record_id,
            max_papers=args.max_papers,
            resume_from_run_id=args.resume_from_run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            understanding_config_path=args.understanding_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {
            "completed", "completed_partial", "completed_with_failures"
        } else 1
    if args.command == "hierarchical-landscape-plan":
        result = HierarchicalLandscapePlanRunner(args.workspace).run(
            collection_path=args.collection,
            routing_path=args.routing,
            run_id=args.run_id,
            max_papers_per_cluster=args.max_papers_per_cluster,
            max_memberships_per_paper=args.max_memberships_per_paper,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "hierarchical-landscape-local-run":
        result = HierarchicalLocalLandscapeBatchRunner(args.workspace).run(
            plan_run_id=args.plan_run_id,
            run_id=args.run_id,
            max_clusters=args.max_clusters,
            resume_from_run_id=args.resume_from_run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            landscape_config_path=args.landscape_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {
            "completed", "completed_partial", "completed_with_failures"
        } else 1
    if args.command == "llm-research-landscape":
        result = ResearchLandscapeRunner(args.workspace).run(
            collection_path=args.collection,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            landscape_config_path=args.landscape_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-framework":
        result = ReviewFrameworkRunner(args.workspace).run(
            landscape_run_id=args.landscape_run_id,
            reference_catalog_run_id=args.reference_catalog_run_id,
            review_goal=args.review_goal,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "chapter-knowledge-package":
        result = ChapterKnowledgePackageBuilder(args.workspace).build(
            framework_run_id=args.framework_run_id,
            section_id=args.section_id,
            section_index=args.section_index,
            run_id=args.run_id,
            model_profile_path=args.model_profile,
            writing_config_path=args.writing_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "source-window-selection":
        result = SourceWindowSelectionBuilder(args.workspace).build(
            framework_run_id=args.framework_run_id,
            section_id=args.section_id,
            section_index=args.section_index,
            material_tier=args.material_tier,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "chapter-knowledge-package-b2-preview":
        result = B2ChapterKnowledgePackageBuilder(args.workspace).build(
            source_window_run_id=args.source_window_run_id,
            run_id=args.run_id,
            model_profile_path=args.model_profile,
            writing_config_path=args.writing_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "review-framework-b2":
        result = ReviewFrameworkB2Builder(args.workspace).build(
            source_framework_run_id=args.source_framework_run_id,
            target_total_chars=args.target_total_chars,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-claim-ledger":
        result = ClaimLedgerRunner(args.workspace).run(
            framework_b2_run_id=args.framework_b2_run_id,
            source_window_run_id=args.source_window_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "chapter-knowledge-package-b2":
        result = B2PackageV2Builder(args.workspace).build(
            source_window_run_id=args.source_window_run_id,
            claim_ledger_run_id=args.claim_ledger_run_id,
            run_id=args.run_id,
            model_profile_path=args.model_profile,
            writing_config_path=args.writing_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-writing-b2":
        result = ReviewWritingB2Runner(args.workspace).run(
            package_run_id=args.package_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            writing_config_path=args.writing_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "assemble-review-b2-phase3a":
        result = ReviewWritingB2Assembler(args.workspace).build(
            chapter_run_ids=args.chapter_run_ids,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-claim-audit-b2":
        result = ReviewClaimAuditB2Runner(args.workspace).run(
            writing_run_id=args.writing_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            audit_config_path=args.audit_config,
            resume_from_run_id=args.resume_from_run_id,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-claim-audit-b2-adjudicate":
        result = ReviewClaimAuditB2AdjudicationRunner(args.workspace).run(
            audit_run_id=args.audit_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            audit_config_path=args.audit_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-claim-revision-b2":
        result = ReviewClaimRevisionB2Runner(args.workspace).run(
            adjudication_run_id=args.adjudication_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            audit_config_path=args.audit_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "apply-review-writing-revision-b2":
        result = ReviewWritingB2RevisionRunner(args.workspace).run(
            revision_suggestion_run_id=args.revision_suggestion_run_id,
            decision_file=args.decision_file,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "assemble-review-b2-audited":
        result = ReviewWritingB2AuditedAssembler(args.workspace).build(
            release_file=args.release_file,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-conclusion-b2":
        result = ReviewConclusionB2Runner(args.workspace).run(
            audited_assembly_run_id=args.audited_assembly_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            conclusion_config_path=args.conclusion_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-conclusion-audit-b2":
        result = ReviewConclusionAuditB2Runner(args.workspace).run(
            conclusion_run_id=args.conclusion_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model_profile_path=args.model_profile,
            audit_config_path=args.audit_config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "apply-review-conclusion-revision-b2":
        result = ReviewConclusionRevisionB2Runner(args.workspace).run(
            audit_run_id=args.audit_run_id,
            decision_file=args.decision_file,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "assemble-review-b2-final":
        result = ReviewB2FinalAssembler(args.workspace).build(
            release_file=args.release_file,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-direct-a2":
        result = ReviewDirectA2Runner(args.workspace).run(
            source_package_run_id=args.source_package_run_id,
            audited_assembly_run_id=args.audited_assembly_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            config_path=args.config,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-evaluate-ab2":
        result = ReviewAB2EvaluationRunner(args.workspace).run(
            config_path=args.config,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            audit_config_path=args.audit_config,
            resume_from_run_id=args.resume_from_run_id,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-evaluate-ab2-stability":
        result = ReviewAB2StructuralStabilityRunner(args.workspace).run(
            source_evaluation_run_id=args.source_evaluation_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-writing":
        result = ReviewWritingRunner(args.workspace).run(
            collection_path=args.collection,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-claim-audit":
        result = ReviewClaimAuditRunner(args.workspace).run(
            writing_run_id=args.writing_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            audit_config_path=args.audit_config,
            response_replay_run_id=args.response_replay_run_id,
            resume_from_run_id=args.resume_from_run_id,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-direct-baseline":
        result = DirectReviewBaselineRunner(args.workspace).run(
            source_package_run_id=args.source_package_run_id,
            topic=args.topic,
            review_goal=args.review_goal,
            expected_paper_count=args.expected_paper_count,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            min_body_chars=args.min_body_chars,
            max_body_chars=args.max_body_chars,
            max_output_tokens=args.max_output_tokens,
            response_replay_run_id=args.response_replay_run_id,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-evaluate":
        result = ReviewEvaluationRunner(args.workspace).run(
            config_path=args.config,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            claim_audit_config_path=args.claim_audit_config,
            resume_from_run_id=args.resume_from_run_id,
            timeout=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-topic-synthesis":
        result = TopicSynthesisRunner(args.workspace).run(
            collection_path=args.collection,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
            synthesis_config_path=args.synthesis_config,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "reference-catalog":
        result = ReferenceCatalogRunner(args.workspace).run(
            project_path=args.project,
            synthesis_run_dir=args.synthesis_run_dir,
            overrides_path=args.overrides,
            review_draft_path=args.review_draft,
            run_id=args.run_id,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "llm-analysis-revise":
        result = LLMStatementRevisionRunner(args.workspace).run(
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
            retry_support_from=args.retry_support_from,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-semantic-audit":
        result = LLMSemanticAuditRunner(args.workspace).run(
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-semantic-decisions-init":
        result = initialize_semantic_decisions(
            args.workspace,
            args.audit_run_id,
            replace_pending=args.replace_pending,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "llm-semantic-auto-resolve":
        result = LLMSemanticAutoResolutionRunner(args.workspace).run(
            audit_run_id=args.audit_run_id,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-semantic-adjudicate":
        result = LLMSemanticAdjudicationRunner(args.workspace).run(
            audit_run_id=args.audit_run_id,
            decisions_path=args.decisions,
            run_id=args.run_id,
            provider=args.provider,
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
            model_profile_path=args.model_profile,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if args.command == "llm-review-import":
        run_dir = args.workspace / "_llm_analysis" / "runs" / args.run_id
        result = import_review_decisions(run_dir, decisions_path=args.decisions)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "llm-provider-probe":
        result = _run_llm_provider_probe(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    return 2


def _build_pdf_converter(args: argparse.Namespace):
    if getattr(args, "pdf_provider", "none") == "none":
        return None
    if args.pdf_provider == "mineru":
        return MinerUApiClient.from_env(
            api_url=args.mineru_api_url,
            api_key_env=args.mineru_api_key_env,
            timeout=args.mineru_timeout,
            max_wait_seconds=args.mineru_max_wait_seconds,
            poll_interval_seconds=args.mineru_poll_interval_seconds,
            model_version=args.mineru_model_version,
            language=args.mineru_language,
            page_ranges=args.mineru_page_ranges,
        )
    raise ValueError(f"不支持的 PDF 解析 provider：{args.pdf_provider}")


def _run_llm_provider_probe(args: argparse.Namespace) -> dict[str, object]:
    profile = load_model_profile(args.model_profile)
    if profile.provider != "openai-compatible":
        raise ValueError(f"能力探针不支持 model profile provider：{profile.provider!r}。")
    cache_root = Path(__file__).resolve().parent / ".model_cache"
    token_counter = DeepSeekV4TokenCounter(profile, cache_root)
    system_prompt = "你是模型路由能力探针。只输出指定 JSON，不要解释。"
    user_prompt = '请立即输出：{"ok": true}'
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    local_count = token_counter.count_messages(messages)
    request_payload = build_chat_request(
        profile.request_model,
        system_prompt,
        user_prompt,
        max_tokens=profile.evidence_max_output_tokens,
        thinking=profile.thinking,
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = args.workspace / "_llm_analysis" / "provider_probes" / run_id
    run_dir.mkdir(parents=True)
    _write_json(run_dir / "request.json", request_payload)

    result: dict[str, object] = {
        "schema_version": "llm.provider_probe.v1",
        "status": "failed",
        "run_id": run_id,
        "profile_id": profile.profile_id,
        "profile_sha256": profile.sha256,
        "requested_model": profile.request_model,
        "thinking": profile.thinking,
        "max_tokens": profile.evidence_max_output_tokens,
        "local_prompt_tokens": local_count.prompt_tokens,
        "encoded_prompt_sha256": local_count.encoded_prompt_sha256,
        "run_path": str(run_dir),
    }
    try:
        client = OpenAICompatibleAnalysisClient.from_env(
            api_url=args.api_url,
            api_key_env=args.api_key_env,
            model=profile.request_model,
            timeout=args.timeout,
        )
        provider_result = client.complete("provider_probe", request_payload, {})
        (run_dir / "raw_response.json").write_bytes(provider_result.raw_body)
        payload = json.loads(extract_chat_content(provider_result.parsed_response))
        if payload != {"ok": True}:
            raise ProviderCallError("provider.probe_payload_invalid")
        actual_prompt_tokens = provider_result.usage["prompt_tokens"]
        if actual_prompt_tokens != local_count.prompt_tokens:
            raise ProviderCallError(
                "provider.prompt_token_mismatch: "
                f"local={local_count.prompt_tokens}, provider={actual_prompt_tokens}"
            )
        result.update(
            {
                "status": "completed",
                "response_id": provider_result.response_id,
                "response_model": provider_result.response_model,
                "finish_reason": provider_result.finish_reason,
                "reasoning_content_chars": provider_result.reasoning_content_chars,
                "provider_prompt_tokens": actual_prompt_tokens,
                "prompt_token_difference": (
                    actual_prompt_tokens - local_count.prompt_tokens
                    if actual_prompt_tokens is not None
                    else None
                ),
                "usage": provider_result.usage,
            }
        )
    except Exception as exc:
        if isinstance(exc, ProviderCallError) and exc.raw_body:
            (run_dir / "raw_response.json").write_bytes(exc.raw_body)
        result.update({"error_type": type(exc).__name__, "error_message": str(exc)})
    _write_json(run_dir / "result.json", result)
    return result


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
