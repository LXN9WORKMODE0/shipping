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
