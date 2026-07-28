from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shipping_pipeline.llm_quotes import (
    MAX_QUOTE_CANDIDATE_CHARS,
    QuoteCandidateError,
    build_quote_candidates,
    project_quote_candidates,
    validate_quote_candidates,
)


def card(material_id: str, extract: str, *, content_kind: str = "prose") -> dict:
    return {"material_id": material_id, "content_kind": content_kind, "extract": extract}


class LLMQuoteCandidateTests(unittest.TestCase):
    def test_candidates_are_exact_bounded_stable_and_cover_all_nonspace_text(self) -> None:
        cards = [card("m1", "第一段。\n\n" + "长文本，" * 100 + "结尾。")]

        first = build_quote_candidates(cards)
        second = build_quote_candidates(cards)

        self.assertEqual(first, second)
        self.assertGreater(len(first), 2)
        self.assertTrue(all(len(row["text"]) <= MAX_QUOTE_CANDIDATE_CHARS for row in first))
        self.assertTrue(all("\n" not in row["text"] for row in first))
        validate_quote_candidates(cards, first)

    def test_image_has_no_candidate_and_model_projection_hides_offsets(self) -> None:
        cards = [card("text", "可引用文本。"), card("image", "images/a.jpg", content_kind="image")]

        candidates = build_quote_candidates(cards)
        projected = project_quote_candidates(candidates)

        self.assertEqual({row["material_id"] for row in candidates}, {"text"})
        self.assertEqual(tuple(projected[0]), ("quote_id", "material_id", "text"))
        self.assertNotIn("start_char", projected[0])

    def test_tampered_candidate_coordinate_is_rejected(self) -> None:
        cards = [card("m1", "精确原文。")]
        candidates = build_quote_candidates(cards)
        candidates[0]["text"] = "修改原文。"

        with self.assertRaisesRegex(QuoteCandidateError, "精确坐标切片"):
            validate_quote_candidates(cards, candidates)

    def test_decimal_is_not_split_at_period_candidate_boundary(self) -> None:
        extract = "背景。" + "长文本" * 78 + "吞吐量677.2万TEU，同比增长21.0%。"

        candidates = build_quote_candidates([card("m1", extract)])

        self.assertTrue(any("677.2" in row["text"] for row in candidates))
        self.assertFalse(any(row["text"].endswith("677.") for row in candidates))


if __name__ == "__main__":
    unittest.main()
