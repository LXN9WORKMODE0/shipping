from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import UIError


@dataclass(frozen=True)
class CardCommand:
    paper_id: str
    workspace_paper_id: str
    source: Path
    argv: tuple[str, ...]

    def public_argv(self) -> list[str]:
        return list(self.argv)


class CardCommandFactory:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        python_executable: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = self._inside(self.project_root, workspace)
        self.main_path = self._inside(self.project_root, "main.py")
        self.env_path = self._inside(self.project_root, ".env")
        self.python_executable = str(
            Path(python_executable or sys.executable).resolve()
        )

    def build(
        self,
        *,
        paper_id: str,
        workspace_paper_id: str,
        source: str | Path,
        topic: str,
        pdf_provider: str,
    ) -> CardCommand:
        if not self.main_path.is_file():
            raise UIError(
                "ui.command_entry_missing",
                f"缺少 pipeline 入口：{self.main_path}",
            )
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise UIError(
                "ui.source_missing",
                f"来源文件不存在：{source_path}",
            )
        suffix = source_path.suffix.lower()
        if suffix == ".pdf" and pdf_provider != "mineru":
            raise UIError(
                "ui.pdf_provider_required",
                f"PDF 必须显式使用 MinerU：{paper_id}",
            )
        if suffix != ".pdf" and pdf_provider not in {"none", "mineru"}:
            raise UIError(
                "ui.pdf_provider_invalid",
                f"PDF provider 不受支持：{pdf_provider}",
            )
        argv = [
            self.python_executable,
            str(self.main_path),
            "--env-file",
            str(self.env_path),
            "run",
            str(source_path),
            "--workspace",
            str(self.workspace),
            "--paper-id",
            paper_id,
            "--workspace-id",
            workspace_paper_id,
            "--topic",
            topic,
            "--pdf-provider",
            "mineru" if suffix == ".pdf" else "none",
        ]
        return CardCommand(
            paper_id=paper_id,
            workspace_paper_id=workspace_paper_id,
            source=source_path,
            argv=tuple(argv),
        )

    @staticmethod
    def _inside(base: Path, candidate: str | Path) -> Path:
        base = base.resolve()
        path = Path(candidate)
        resolved = path.resolve() if path.is_absolute() else (base / path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise UIError(
                "ui.path_outside_root",
                f"路径越过允许根目录：{candidate}",
            ) from exc
        return resolved


@dataclass(frozen=True)
class TopicBriefCommand:
    paper_id: str
    workspace_paper_id: str
    run_id: str
    argv: tuple[str, ...]

    def public_argv(self) -> list[str]:
        return list(self.argv)


class TopicBriefCommandFactory:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        python_executable: str | Path | None = None,
        env_file: str | Path = ".env",
        model_profile: str | Path = "config/models/deepseek-v4-pro-official.json",
        review_config: str | Path = "config/topic-review-default.json",
        timeout: int = 900,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = CardCommandFactory._inside(self.project_root, workspace)
        self.main_path = CardCommandFactory._inside(self.project_root, "main.py")
        self.env_path = CardCommandFactory._inside(
            self.project_root, env_file
        )
        self.model_profile = CardCommandFactory._inside(
            self.project_root, model_profile
        )
        self.review_config = CardCommandFactory._inside(
            self.project_root, review_config
        )
        self.python_executable = str(
            Path(python_executable or sys.executable).resolve()
        )
        self.timeout = timeout

    def build(
        self,
        *,
        paper_id: str,
        workspace_paper_id: str,
        topic: str,
        run_id: str,
    ) -> TopicBriefCommand:
        required = {
            "pipeline 入口": self.main_path,
            "环境文件": self.env_path,
            "模型配置": self.model_profile,
            "主题分析配置": self.review_config,
        }
        for label, path in required.items():
            if not path.is_file():
                raise UIError(
                    "ui.analysis_configuration_missing",
                    f"缺少{label}：{path}",
                )
        if not topic.strip():
            raise UIError("ui.topic_empty", "综述主题不能为空。")
        argv = [
            self.python_executable,
            str(self.main_path),
            "--env-file",
            str(self.env_path),
            "llm-topic-brief",
            "--workspace",
            str(self.workspace),
            "--paper-id",
            paper_id,
            "--workspace-id",
            workspace_paper_id,
            "--topic",
            topic.strip(),
            "--run-id",
            run_id,
            "--provider",
            "openai-compatible",
            "--model-profile",
            str(self.model_profile),
            "--review-config",
            str(self.review_config),
            "--timeout",
            str(self.timeout),
        ]
        return TopicBriefCommand(
            paper_id=paper_id,
            workspace_paper_id=workspace_paper_id,
            run_id=run_id,
            argv=tuple(argv),
        )


@dataclass(frozen=True)
class TopicSynthesisCommand:
    run_id: str
    collection: Path
    argv: tuple[str, ...]

    def public_argv(self) -> list[str]:
        return list(self.argv)


class TopicSynthesisCommandFactory:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        python_executable: str | Path | None = None,
        env_file: str | Path = ".env",
        model_profile: str | Path = "config/models/deepseek-v4-pro-official.json",
        synthesis_config: str | Path = "config/topic-synthesis-default.json",
        timeout: int = 900,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = CardCommandFactory._inside(self.project_root, workspace)
        self.main_path = CardCommandFactory._inside(self.project_root, "main.py")
        self.env_path = CardCommandFactory._inside(self.project_root, env_file)
        self.model_profile = CardCommandFactory._inside(
            self.project_root, model_profile
        )
        self.synthesis_config = CardCommandFactory._inside(
            self.project_root, synthesis_config
        )
        self.python_executable = str(
            Path(python_executable or sys.executable).resolve()
        )
        self.timeout = timeout

    def build(
        self,
        *,
        collection: str | Path,
        run_id: str,
    ) -> TopicSynthesisCommand:
        collection_path = CardCommandFactory._inside(
            self.project_root, collection
        )
        required = {
            "pipeline 入口": self.main_path,
            "环境文件": self.env_path,
            "模型配置": self.model_profile,
            "综合配置": self.synthesis_config,
            "综合 collection": collection_path,
        }
        for label, path in required.items():
            if not path.is_file():
                raise UIError(
                    "ui.synthesis_configuration_missing",
                    f"缺少{label}：{path}",
                )
        argv = [
            self.python_executable,
            str(self.main_path),
            "--env-file",
            str(self.env_path),
            "llm-topic-synthesis",
            "--workspace",
            str(self.workspace),
            "--collection",
            str(collection_path),
            "--run-id",
            run_id,
            "--provider",
            "openai-compatible",
            "--model-profile",
            str(self.model_profile),
            "--synthesis-config",
            str(self.synthesis_config),
            "--timeout",
            str(self.timeout),
        ]
        return TopicSynthesisCommand(
            run_id=run_id,
            collection=collection_path,
            argv=tuple(argv),
        )


@dataclass(frozen=True)
class FullPipelineCommand:
    run_id: str
    collection: Path
    argv: tuple[str, ...]

    def public_argv(self) -> list[str]:
        return list(self.argv)


class FullPipelineCommandFactory:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace: str | Path = "workspace",
        python_executable: str | Path | None = None,
        env_file: str | Path = ".env",
        model_profile: str | Path = "config/models/deepseek-v4-pro-official.json",
        review_config: str | Path = "config/topic-review-default.json",
        synthesis_config: str | Path = "config/topic-synthesis-default.json",
        timeout: int = 900,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace = CardCommandFactory._inside(self.project_root, workspace)
        self.main_path = CardCommandFactory._inside(self.project_root, "main.py")
        self.env_path = CardCommandFactory._inside(self.project_root, env_file)
        self.model_profile = CardCommandFactory._inside(
            self.project_root, model_profile
        )
        self.review_config = CardCommandFactory._inside(
            self.project_root, review_config
        )
        self.synthesis_config = CardCommandFactory._inside(
            self.project_root, synthesis_config
        )
        self.python_executable = str(
            Path(python_executable or sys.executable).resolve()
        )
        self.timeout = timeout

    def build(
        self,
        *,
        collection: str | Path,
        run_id: str,
        pdf_provider: str,
    ) -> FullPipelineCommand:
        collection_path = CardCommandFactory._inside(
            self.project_root, collection
        )
        required = {
            "pipeline 入口": self.main_path,
            "环境文件": self.env_path,
            "模型配置": self.model_profile,
            "主题分析配置": self.review_config,
            "综合配置": self.synthesis_config,
            "完整流程 collection": collection_path,
        }
        for label, path in required.items():
            if not path.is_file():
                raise UIError(
                    "ui.full_pipeline_configuration_missing",
                    f"缺少{label}：{path}",
                )
        if pdf_provider not in {"none", "mineru"}:
            raise UIError(
                "ui.pdf_provider_invalid",
                f"PDF provider 不受支持：{pdf_provider}",
            )
        argv = [
            self.python_executable,
            str(self.main_path),
            "--env-file",
            str(self.env_path),
            "full-pipeline",
            "--workspace",
            str(self.workspace),
            "--collection",
            str(collection_path),
            "--run-id",
            run_id,
            "--provider",
            "openai-compatible",
            "--model-profile",
            str(self.model_profile),
            "--review-config",
            str(self.review_config),
            "--synthesis-config",
            str(self.synthesis_config),
            "--timeout",
            str(self.timeout),
            "--pdf-provider",
            pdf_provider,
        ]
        return FullPipelineCommand(
            run_id=run_id,
            collection=collection_path,
            argv=tuple(argv),
        )
