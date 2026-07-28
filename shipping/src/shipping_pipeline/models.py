from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Issue:
    stage: str
    code: str
    message: str
    severity: str = "error"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "details": self.details,
        }


@dataclass(frozen=True)
class PipelineResult:
    paper_id: str
    status: str
    structure_quality: str
    issue_count: int
    workspace_path: str

