from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地文献综述工作台。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    uvicorn.run(
        "shipping_ui.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        app_dir=str(project_root / "src"),
    )


if __name__ == "__main__":
    main()
