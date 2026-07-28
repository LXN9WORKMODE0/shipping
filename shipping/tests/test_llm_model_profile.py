from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_model_profile import ModelProfileError, load_model_profile


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"


def _make_temp_dir() -> Path:
    path = TEST_TMP_ROOT / f"llm_model_profile_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _valid_profile() -> dict:
    return {
        "schema_version": "llm.model_profile.v4",
        "profile_id": "siliconflow-deepseek-v4-pro",
        "provider": "openai-compatible",
        "request_model": "deepseek-ai/DeepSeek-V4-Pro",
        "tokenizer_repo": "deepseek-ai/DeepSeek-V4-Pro",
        "tokenizer_revision": "0e1a0e5e52aea73055f50fef6f2423db370265b6",
        "prompt_token_overhead": 0,
        "context_window_tokens": 1_000_000,
        "model_max_output_tokens": 384_000,
        "safety_margin_tokens": 10_000,
        "evidence_output_base_tokens": 2_048,
        "evidence_output_tokens_per_card": 1_024,
        "evidence_max_output_tokens": 65_536,
        "large_paper_evidence_batch_max_cards": 12,
        "section_summary_max_output_tokens": 32_768,
        "paper_synthesis_max_output_tokens": 32_768,
        "evidence_claim_batch_size": 12,
        "evidence_claim_output_base_tokens": 1_024,
        "evidence_claim_output_tokens_per_unit": 512,
        "evidence_claim_support_max_output_tokens": 8_192,
        "evidence_revision_max_output_tokens": 8_192,
        "statement_revision_max_output_tokens": 8_192,
        "statement_role_max_output_tokens": 16_384,
        "statement_support_max_output_tokens": 16_384,
        "thinking": {"type": "disabled"},
    }


class ModelProfileTests(unittest.TestCase):
    def _write(self, directory: Path, payload: dict) -> Path:
        path = directory / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _temp_dir(self) -> Path:
        path = _make_temp_dir()
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def test_valid_profile_loads(self) -> None:
        profile = load_model_profile(self._write(self._temp_dir(), _valid_profile()))
        self.assertEqual(profile.profile_id, "siliconflow-deepseek-v4-pro")
        self.assertEqual(profile.thinking, {"type": "disabled"})
        self.assertEqual(profile.prompt_token_overhead, 0)
        self.assertEqual(profile.evidence_output_tokens(19), 21_504)
        self.assertEqual(profile.evidence_claim_output_tokens(12), 7_168)

    def test_unknown_or_missing_fields_are_rejected(self) -> None:
        for mutation in ("unknown", "missing"):
            with self.subTest(mutation=mutation):
                payload = _valid_profile()
                if mutation == "unknown":
                    payload["extra"] = True
                else:
                    payload.pop("request_model")
                with self.assertRaisesRegex(ModelProfileError, "字段"):
                    load_model_profile(self._write(self._temp_dir(), payload))

    def test_invalid_budget_revision_and_thinking_are_rejected(self) -> None:
        mutations = {
            "output": ("evidence_max_output_tokens", 400_001, "model_max_output_tokens"),
            "margin": ("safety_margin_tokens", 1_000_000, "safety_margin_tokens"),
            "revision": ("tokenizer_revision", "main", "tokenizer_revision"),
            "thinking": ("thinking", {"type": "enabled"}, "Non-think"),
            "zero": ("evidence_output_base_tokens", 0, "正整数"),
            "zero_cards": ("large_paper_evidence_batch_max_cards", 0, "正整数"),
            "negative_overhead": (
                "prompt_token_overhead",
                -1,
                "非负整数",
            ),
        }
        for name, (field, value, message) in mutations.items():
            with self.subTest(name=name):
                payload = _valid_profile()
                payload[field] = value
                with self.assertRaisesRegex(ModelProfileError, message):
                    load_model_profile(self._write(self._temp_dir(), payload))


if __name__ == "__main__":
    unittest.main()
