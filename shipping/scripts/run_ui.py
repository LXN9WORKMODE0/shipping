from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动本地文献综述工作台。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(
        "shipping_ui.app:create_app",
        host=args.host,
        port=args.port,
        factory=True,
        app_dir=str(SRC_ROOT),
    )


if __name__ == "__main__":
    main()
