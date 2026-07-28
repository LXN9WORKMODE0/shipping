from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_ui.project_repository import ProjectRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把旧版只读UI项目迁移到workspace/_ui项目存储。"
    )
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    project = ProjectRepository(PROJECT_ROOT).migrate_legacy(args.project_id)
    print(
        json.dumps(
            {
                "project_id": project["project_id"],
                "schema_version": project["schema_version"],
                "revision": project["revision"],
                "paper_count": len(project["papers"]),
                "current_collection_path": project["current_collection_path"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
