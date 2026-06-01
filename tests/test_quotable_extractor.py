"""tests for agents.quotable_extractor."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from dataclasses import dataclass, field

from tests._helpers import make_minimal_state
from agents import quotable_extractor as qe
from agents.quotable_extractor import QuotableMoment


@dataclass
class _StubSummary:
    quotable_moments: list = field(default_factory=list)


class TestExtractFromChapter(unittest.TestCase):
    def test_returns_moments_sorted_by_impact(self):
        state = make_minimal_state()
        fake = {"quotable_moments": [
            {"kind": "对白", "text": "他说我废物——可惜我现在站着。", "reason": "反差", "impact_score": 9},
            {"kind": "场景", "text": "夜色压下来,像一块湿透的黑布。", "reason": "画面感", "impact_score": 7},
            {"kind": "独白", "text": "原来活着也是要勇气的。", "reason": "点睛", "impact_score": 8},
        ]}
        with patch.object(qe, "request_json_with_profile", return_value=fake):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        self.assertEqual(len(moments), 3)
        # 按 impact_score 降序
        self.assertEqual(moments[0].impact_score, 9)
        self.assertEqual(moments[1].impact_score, 8)
        self.assertEqual(moments[2].impact_score, 7)

    def test_skips_low_impact_below_5(self):
        state = make_minimal_state()
        fake = {"quotable_moments": [
            {"kind": "对白", "text": "好的好的好的", "reason": "", "impact_score": 3},  # 跳(低分)
            {"kind": "对白", "text": "我活着就是赢", "reason": "", "impact_score": 8},   # 留
        ]}
        with patch.object(qe, "request_json_with_profile", return_value=fake):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        self.assertEqual(len(moments), 1)
        self.assertEqual(moments[0].text, "我活着就是赢")

    def test_skips_empty_text(self):
        state = make_minimal_state()
        fake = {"quotable_moments": [
            {"kind": "对白", "text": "", "impact_score": 9},
            {"kind": "对白", "text": "  ", "impact_score": 8},
        ]}
        with patch.object(qe, "request_json_with_profile", return_value=fake):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        self.assertEqual(moments, [])

    def test_too_short_text_skipped(self):
        state = make_minimal_state()
        # 正文 < 100 字,不调 LLM,直接返回 []
        with patch.object(qe, "request_json_with_profile") as mock_llm:
            moments = qe.extract_from_chapter(state, 5, "短")
        self.assertEqual(moments, [])
        mock_llm.assert_not_called()

    def test_llm_failure_returns_empty(self):
        state = make_minimal_state()
        with patch.object(qe, "request_json_with_profile",
                          side_effect=RuntimeError("LLM down")):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        self.assertEqual(moments, [])

    def test_clamps_max_8(self):
        state = make_minimal_state()
        fake = {"quotable_moments": [
            {"kind": "对白", "text": f"句子{i}", "impact_score": 10 - i % 3}
            for i in range(20)
        ]}
        with patch.object(qe, "request_json_with_profile", return_value=fake):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        self.assertLessEqual(len(moments), 8)

    def test_clamps_score_to_1_10(self):
        state = make_minimal_state()
        fake = {"quotable_moments": [
            {"kind": "对白", "text": "ok", "impact_score": 99},
            {"kind": "对白", "text": "ok2", "impact_score": 7},
        ]}
        with patch.object(qe, "request_json_with_profile", return_value=fake):
            moments = qe.extract_from_chapter(state, 5, "x" * 200)
        for m in moments:
            self.assertLessEqual(m.impact_score, 10)
            self.assertGreaterEqual(m.impact_score, 1)


class TestAttachToSummary(unittest.TestCase):
    def test_attaches_moments_as_dicts(self):
        summary = _StubSummary()
        moments = [
            QuotableMoment(kind="对白", text="A", reason="r", impact_score=9),
            QuotableMoment(kind="场景", text="B", reason="s", impact_score=7),
        ]
        qe.attach_to_summary(summary, moments)
        self.assertEqual(len(summary.quotable_moments), 2)
        self.assertEqual(summary.quotable_moments[0]["kind"], "对白")
        self.assertEqual(summary.quotable_moments[0]["impact_score"], 9)

    def test_no_attribute_doesnt_crash(self):
        class _NoSlot:
            pass
        # 不应抛
        qe.attach_to_summary(_NoSlot(), [])


class TestFormatRecentForWriter(unittest.TestCase):
    def test_empty_state_returns_empty(self):
        state = make_minimal_state()
        # 默认 completed_chapters 是 []
        self.assertEqual(qe.format_recent_for_writer(state), "")

    def test_extracts_top_per_chapter(self):
        state = make_minimal_state()

        @dataclass
        class _ChStub:
            index: int = 0
            quotable_moments: list = field(default_factory=list)

        state.completed_chapters = [
            _ChStub(index=8, quotable_moments=[
                {"kind": "对白", "text": "甲八", "impact_score": 9},
                {"kind": "对白", "text": "乙八", "impact_score": 6},
                {"kind": "对白", "text": "丙八", "impact_score": 5},
            ]),
            _ChStub(index=9, quotable_moments=[
                {"kind": "场景", "text": "夜雨", "impact_score": 8},
            ]),
        ]
        block = qe.format_recent_for_writer(state, lookback=3, top_per_chapter=2)
        self.assertIn("第8章", block)
        self.assertIn("甲八", block)
        self.assertIn("乙八", block)  # top 2
        self.assertNotIn("丙八", block)  # 第 3 个被截断
        self.assertIn("夜雨", block)


if __name__ == "__main__":
    unittest.main()
