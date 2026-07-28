from __future__ import annotations

import os
import re
from pathlib import Path

ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: Path, *, override: bool = False) -> dict[str, str]:
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"{path} 第 {line_number} 行 env 配置无效：应为 KEY=VALUE。")

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if not ENV_KEY_RE.match(key):
            raise ValueError(f"{path} 第 {line_number} 行 env key 无效：{key!r}。")
        if value == "":
            continue
        if not override and key in os.environ:
            continue

        os.environ[key] = value
        loaded[key] = value
    return loaded


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
