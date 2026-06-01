"""tests for agents.chapter_title_generator.

不发真 LLM 调用——monkeypatch request_json_with_profile。
"""
from __future__ import annotations
import unittest
from unittest.mock import patch

from tests._helpers import make_minimal_state
from agents import chapter_title_generator as ctg


class TestGenerateTitle(unittest.TestCase):
    def test_picks_highest_appeal(self):
        state = make_minimal_state()
        fake_resp = {
            "candidates": [
                {"title": "雪夜叩门", "appeal_score": 9, "reason": "悬念+意象"},
                {"title": "战", "appeal_score": 5, "reason": "动作"},
                {"title": "破局者", "appeal_score": 7, "reason": "身份揭示"},
            ]
        }
        with patch.object(ctg, "request_json_with_profile", return_value=fake_resp):
            title = ctg.generate_title(state, 10, chapter_goal="主角对峙反派")
        self.assertEqual(title, "雪夜叩门")

    def test_avoids_recent_titles(self):
        """avoid_titles 命中相似标题应跳过。"""
        state = make_minimal_state()
        fake_resp = {
            "candidates": [
                {"title": "雪夜叩门", "appeal_score": 9, "reason": ""},
                {"title": "破局者", "appeal_score": 7, "reason": ""},
            ]
        }
        with patch.object(ctg, "request_json_with_profile", return_value=fake_resp):
            title = ctg.generate_title(
                state, 10,
                avoid_titles=["雪夜歃血"],  # 同首字 + 长度相近 → 视为撞
            )
        # "雪夜叩门"被 avoid 跳过 → "破局者"
        self.assertEqual(title, "破局者")

    def test_returns_fallback_on_llm_failure(self):
        state = make_minimal_state()
        with patch.object(ctg, "request_json_with_profile",
                          side_effect=RuntimeError("LLM down")):
            title = ctg.generate_title(state, 10, fallback="兜底标题")
        self.assertEqual(title, "兜底标题")

    def test_returns_fallback_on_empty_candidates(self):
        state = make_minimal_state()
        with patch.object(ctg, "request_json_with_profile",
                          return_value={"candidates": []}):
            title = ctg.generate_title(state, 10, fallback="X")
        self.assertEqual(title, "X")

    def test_strips_quotes_from_title(self):
        state = make_minimal_state()
        fake_resp = {"candidates": [
            {"title": "《破局》", "appeal_score": 8, "reason": ""},
        ]}
        with patch.object(ctg, "request_json_with_profile", return_value=fake_resp):
            title = ctg.generate_title(state, 10)
        self.assertEqual(title, "破局")


class TestGenerateCandidates(unittest.TestCase):
    def test_returns_full_list(self):
        state = make_minimal_state()
        fake_resp = {"candidates": [
            {"title": "A", "appeal_score": 9, "reason": "r1"},
            {"title": "B", "appeal_score": 7, "reason": "r2"},
        ]}
        with patch.object(ctg, "request_json_with_profile", return_value=fake_resp):
            cands = ctg.generate_candidates(state, 10)
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["title"], "A")
        self.assertEqual(cands[1]["appeal_score"], 7)

    def test_skips_empty_titles(self):
        state = make_minimal_state()
        fake_resp = {"candidates": [
            {"title": "", "appeal_score": 9, "reason": ""},
            {"title": "B", "appeal_score": 7, "reason": ""},
        ]}
        with patch.object(ctg, "request_json_with_profile", return_value=fake_resp):
            cands = ctg.generate_candidates(state, 10)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["title"], "B")

    def test_returns_empty_on_llm_failure(self):
        state = make_minimal_state()
        with patch.object(ctg, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            cands = ctg.generate_candidates(state, 10)
        self.assertEqual(cands, [])


class TestSimilarityHeuristic(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(ctg._titles_too_similar("破局", "破局"))

    def test_same_first_char_close_length(self):
        self.assertTrue(ctg._titles_too_similar("雪夜叩门", "雪夜歃血"))
        self.assertTrue(ctg._titles_too_similar("战", "战吼"))

    def test_different_first_char_no_match(self):
        self.assertFalse(ctg._titles_too_similar("雪夜", "夜雪"))

    def test_empty_strings(self):
        self.assertFalse(ctg._titles_too_similar("", "x"))
        self.assertFalse(ctg._titles_too_similar("x", ""))


if __name__ == "__main__":
    unittest.main()
