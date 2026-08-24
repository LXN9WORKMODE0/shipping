from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.external_current_evidence import ExternalCurrentEvidenceRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="在论文证据过旧时构建带显式标签的联网当前事实层。")
    parser.add_argument("--writing-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--api-key-env", default="LLM_ANALYSIS_API_KEY")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    _load_env(args.env_file)
    api_url = args.api_url or os.environ.get("LLM_ANALYSIS_API_URL", "")
    result = ExternalCurrentEvidenceRunner(args.workspace).run(
        writing_run_id=args.writing_run_id,
        run_id=args.run_id,
        api_url=api_url,
        api_key_env=args.api_key_env,
        model=args.model,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed" else 1


def _load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


if __name__ == "__main__":
    raise SystemExit(main())
