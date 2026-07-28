from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tokenizers import Tokenizer, models, pre_tokenizers

from shipping_pipeline.llm_model_profile import ModelProfile
from shipping_pipeline.llm_tokenizer import DeepSeekV4TokenCounter, TokenizerUnavailableError


REVISION = "0e1a0e5e52aea73055f50fef6f2423db370265b6"
TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"


def _make_temp_dir() -> Path:
    path = TEST_TMP_ROOT / f"llm_tokenizer_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _profile() -> ModelProfile:
    return ModelProfile(
        schema_version="llm.model_profile.v4",
        profile_id="test-profile",
        provider="openai-compatible",
        request_model="deepseek-ai/DeepSeek-V4-Pro",
        tokenizer_repo="deepseek-ai/DeepSeek-V4-Pro",
        tokenizer_revision=REVISION,
        prompt_token_overhead=20,
        context_window_tokens=1_000_000,
        model_max_output_tokens=384_000,
        safety_margin_tokens=10_000,
        evidence_output_base_tokens=2_048,
        evidence_output_tokens_per_card=1_024,
        evidence_max_output_tokens=65_536,
        large_paper_evidence_batch_max_cards=12,
        section_summary_max_output_tokens=32_768,
        paper_synthesis_max_output_tokens=32_768,
        evidence_claim_batch_size=12,
        evidence_claim_output_base_tokens=1_024,
        evidence_claim_output_tokens_per_unit=512,
        evidence_claim_support_max_output_tokens=8_192,
        evidence_revision_max_output_tokens=8_192,
        statement_revision_max_output_tokens=8_192,
        statement_role_max_output_tokens=16_384,
        statement_support_max_output_tokens=16_384,
        thinking={"type": "disabled"},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_cache(root: Path) -> Path:
    asset_dir = root / "deepseek-v4-pro" / REVISION
    (asset_dir / "encoding").mkdir(parents=True)
    tokenizer = Tokenizer(models.WordLevel({"<unk>": 0}, unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(asset_dir / "tokenizer.json"))
    encoder = asset_dir / "encoding" / "encoding_dsv4.py"
    encoder.write_text(
        "def encode_messages(messages, thinking_mode):\n"
        "    assert thinking_mode == 'chat'\n"
        "    return '<bos>' + ''.join(str(row['content']) for row in messages) + '<assistant>'\n",
        encoding="utf-8",
    )
    files = {
        "tokenizer.json": _sha256(asset_dir / "tokenizer.json"),
        "encoding/encoding_dsv4.py": _sha256(encoder),
    }
    (asset_dir / "manifest.json").write_text(
        json.dumps({"schema_version": "llm.tokenizer_manifest.v1", "files": files}),
        encoding="utf-8",
    )
    return asset_dir


class TokenizerTests(unittest.TestCase):
    def _temp_dir(self) -> Path:
        path = _make_temp_dir()
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_message_count_uses_chat_encoding_and_is_deterministic(self) -> None:
        cache_root = self._temp_dir()
        _build_cache(cache_root)
        counter = DeepSeekV4TokenCounter(_profile(), cache_root)
        first = counter.count_messages(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}]
        )
        second = counter.count_messages(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "user"}]
        )
        self.assertGreater(first.prompt_tokens, 20)
        self.assertEqual(first, second)

    def test_missing_manifest_is_rejected(self) -> None:
        with self.assertRaisesRegex(TokenizerUnavailableError, "manifest"):
            DeepSeekV4TokenCounter(_profile(), self._temp_dir())

    def test_tampered_asset_is_rejected(self) -> None:
        cache_root = self._temp_dir()
        asset_dir = _build_cache(cache_root)
        (asset_dir / "encoding" / "encoding_dsv4.py").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(TokenizerUnavailableError, "哈希"):
            DeepSeekV4TokenCounter(_profile(), cache_root)


if __name__ == "__main__":
    unittest.main()
