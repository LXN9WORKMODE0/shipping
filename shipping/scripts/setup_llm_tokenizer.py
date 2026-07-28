from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path
from urllib import error, parse, request

from tokenizers import Tokenizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_model_profile import load_model_profile


ASSET_PATHS = (
    "tokenizer.json",
    "tokenizer_config.json",
    "encoding/encoding_dsv4.py",
    "encoding/README.md",
    "encoding/tests/test_input_1.json",
    "encoding/tests/test_input_2.json",
    "encoding/tests/test_input_3.json",
    "encoding/tests/test_input_4.json",
    "encoding/tests/test_output_1.txt",
    "encoding/tests/test_output_2.txt",
    "encoding/tests/test_output_3.txt",
    "encoding/tests/test_output_4.txt",
    "LICENSE",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装并验证 DeepSeek-V4-Pro 官方 tokenizer 资产。")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=PROJECT_ROOT / ".model_cache")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    profile = load_model_profile(args.profile)
    target = args.cache_root / "deepseek-v4-pro" / profile.tokenizer_revision
    if target.exists():
        _verify_existing(target, profile.tokenizer_repo, profile.tokenizer_revision)
        print(json.dumps({"status": "already_installed", "path": str(target)}, ensure_ascii=False))
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.parent / f"tokenizer-install-{uuid.uuid4().hex}"
    temp_path.mkdir()
    try:
        for relative_path in ASSET_PATHS:
            _download(profile.tokenizer_repo, profile.tokenizer_revision, relative_path, temp_path, args.timeout)
        _verify_official_examples(temp_path)
        files = {relative_path: _sha256(temp_path / relative_path) for relative_path in ASSET_PATHS}
        manifest = {
            "schema_version": "llm.tokenizer_manifest.v1",
            "repository": profile.tokenizer_repo,
            "revision": profile.tokenizer_revision,
            "files": files,
        }
        (temp_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(target)
    except Exception:
        if temp_path.exists() and target.parent.resolve() in temp_path.resolve().parents:
            shutil.rmtree(temp_path, ignore_errors=True)
        raise
    print(json.dumps({"status": "installed", "path": str(target)}, ensure_ascii=False))
    return 0


def _download(repository: str, revision: str, relative_path: str, destination: Path, timeout: int) -> None:
    encoded_path = "/".join(parse.quote(part, safe="") for part in relative_path.split("/"))
    url = f"https://huggingface.co/{repository}/resolve/{revision}/{encoded_path}?download=true"
    output = destination / relative_path
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except (error.HTTPError, error.URLError) as exc:
        raise RuntimeError(f"下载 tokenizer 资产失败：{relative_path}") from exc
    if not body:
        raise RuntimeError(f"下载 tokenizer 资产为空：{relative_path}")
    output.write_bytes(body)


def _verify_official_examples(asset_dir: Path) -> None:
    encoder_path = asset_dir / "encoding" / "encoding_dsv4.py"
    spec = importlib.util.spec_from_file_location("shipping_setup_deepseek_v4_encoding", encoder_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载官方 DeepSeek-V4 encoding。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests_dir = asset_dir / "encoding" / "tests"
    for index in range(1, 5):
        payload = json.loads((tests_dir / f"test_input_{index}.json").read_text(encoding="utf-8"))
        if index == 1:
            messages = payload["messages"]
            messages[0]["tools"] = payload["tools"]
        else:
            messages = payload
        mode = "chat" if index == 4 else "thinking"
        encoded = module.encode_messages(messages, thinking_mode=mode)
        expected = (tests_dir / f"test_output_{index}.txt").read_text(encoding="utf-8")
        if encoded != expected:
            raise RuntimeError(f"官方 DeepSeek-V4 encoding 样例不一致：case={index}")
    tokenizer = Tokenizer.from_file(str(asset_dir / "tokenizer.json"))
    if not tokenizer.encode(expected, add_special_tokens=False).ids:
        raise RuntimeError("官方 DeepSeek-V4 tokenizer 未产生 Token。")


def _verify_existing(target: Path, repository: str, revision: str) -> None:
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("已存在 tokenizer 目录但缺少 manifest，拒绝覆盖。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("repository") != repository or manifest.get("revision") != revision:
        raise RuntimeError("已存在 tokenizer manifest 与请求配置不一致。")
    files = manifest.get("files", {})
    if set(files) != set(ASSET_PATHS):
        raise RuntimeError("已存在 tokenizer manifest 文件集合不完整。")
    for relative_path, expected_sha256 in files.items():
        path = target / relative_path
        if not path.is_file() or _sha256(path) != expected_sha256:
            raise RuntimeError(f"已存在 tokenizer 资产校验失败：{relative_path}")
    _verify_official_examples(target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
