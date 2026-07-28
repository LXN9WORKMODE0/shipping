from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from tokenizers import Tokenizer

from shipping_pipeline.llm_model_profile import ModelProfile


TOKENIZER_MANIFEST_VERSION = "llm.tokenizer_manifest.v1"


class TokenizerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenCount:
    prompt_tokens: int
    encoded_prompt_sha256: str


class DeepSeekV4TokenCounter:
    def __init__(self, profile: ModelProfile, cache_root: Path) -> None:
        self.profile = profile
        self.asset_dir = cache_root / "deepseek-v4-pro" / profile.tokenizer_revision
        manifest = _load_and_verify_manifest(self.asset_dir, profile)
        tokenizer_path = self.asset_dir / "tokenizer.json"
        encoder_path = self.asset_dir / "encoding" / "encoding_dsv4.py"
        if "tokenizer.json" not in manifest["files"] or "encoding/encoding_dsv4.py" not in manifest["files"]:
            raise TokenizerUnavailableError("tokenizer manifest 缺少运行必需文件。")
        try:
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._encoder = load_pinned_encoder(encoder_path)
        except Exception as exc:
            raise TokenizerUnavailableError("无法加载固定版本的 DeepSeek-V4 tokenizer。") from exc

    def encode_messages(self, messages: list[dict[str, Any]]) -> str:
        try:
            prompt = self._encoder.encode_messages(messages, thinking_mode="chat")
        except Exception as exc:
            raise TokenizerUnavailableError("DeepSeek-V4 官方消息编码失败。") from exc
        if not isinstance(prompt, str) or not prompt:
            raise TokenizerUnavailableError("DeepSeek-V4 官方消息编码返回空结果。")
        return prompt

    def count_messages(self, messages: list[dict[str, Any]]) -> TokenCount:
        prompt = self.encode_messages(messages)
        token_ids = self._tokenizer.encode(prompt, add_special_tokens=False).ids
        return TokenCount(
            prompt_tokens=len(token_ids) + self.profile.prompt_token_overhead,
            encoded_prompt_sha256="sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )

    def count_text(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)


def _load_and_verify_manifest(asset_dir: Path, profile: ModelProfile) -> dict[str, Any]:
    manifest_path = asset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise TokenizerUnavailableError(f"缺少 tokenizer manifest：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TokenizerUnavailableError("tokenizer manifest 不是有效 JSON。") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != TOKENIZER_MANIFEST_VERSION:
        raise TokenizerUnavailableError("tokenizer manifest 版本无效。")
    if manifest.get("repository") not in {None, profile.tokenizer_repo}:
        raise TokenizerUnavailableError("tokenizer manifest repository 与模型配置不一致。")
    if manifest.get("revision") not in {None, profile.tokenizer_revision}:
        raise TokenizerUnavailableError("tokenizer manifest revision 与模型配置不一致。")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise TokenizerUnavailableError("tokenizer manifest 没有文件清单。")
    for relative_path, expected_sha256 in files.items():
        if not isinstance(relative_path, str) or not isinstance(expected_sha256, str):
            raise TokenizerUnavailableError("tokenizer manifest 文件清单格式错误。")
        asset_path = asset_dir / Path(relative_path)
        try:
            resolved = asset_path.resolve(strict=True)
        except OSError as exc:
            raise TokenizerUnavailableError(f"tokenizer 资产缺失：{relative_path}") from exc
        if asset_dir.resolve() not in resolved.parents:
            raise TokenizerUnavailableError(f"tokenizer manifest 路径越界：{relative_path}")
        if _sha256_file(resolved) != expected_sha256:
            raise TokenizerUnavailableError(f"tokenizer 资产哈希不符：{relative_path}")
    return manifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pinned_encoder(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("shipping_deepseek_v4_encoding", path)
    if spec is None or spec.loader is None:
        raise TokenizerUnavailableError("无法加载固定版本的 DeepSeek-V4 encoding。")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "encode_messages", None)):
        raise TokenizerUnavailableError("固定版本 encoding 缺少 encode_messages。")
    return module
