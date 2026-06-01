"""tests for agents.pov_checker."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from tests._helpers import make_minimal_state
from agents import pov_checker as pov
from agents.pov_checker import POVViolation, AuditResult


class TestAuditChapter(unittest.TestCase):
    def test_returns_new_facts_and_violations(self):
        state = make_minimal_state()
        fake = {
            "new_known_facts": [
                {"fact": "知道反派叫张三", "source": "对话"},
                {"fact": "见过京都布局", "source": "亲见"},
            ],
            "pov_violations": [
                {"excerpt": "主角准确说出反派密谋",
                 "explanation": "主角不在场",
                 "severity": "critical"},
            ],
        }
        with patch.object(pov, "request_json_with_profile", return_value=fake):
            result = pov.audit_chapter(state, 5, "x" * 200)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.new_facts), 2)
        self.assertEqual(result.new_facts[0]["fact"], "知道反派叫张三")
        self.assertEqual(result.new_facts[0]["learned_chapter"], 5)
        self.assertEqual(len(result.violations), 1)
        self.assertEqual(result.violations[0].severity, "critical")

    def test_skips_short_text(self):
        state = make_minimal_state()
        with patch.object(pov, "request_json_with_profile") as mock_llm:
            result = pov.audit_chapter(state, 5, "短")
        self.assertTrue(result.ok)
        self.assertEqual(result.new_facts, [])
        mock_llm.assert_not_called()

    def test_no_protagonist_skips(self):
        state = make_minimal_state()
        state.characters = []  # 无主角
        with patch.object(pov, "request_json_with_profile") as mock_llm:
            result = pov.audit_chapter(state, 5, "x" * 200)
        self.assertTrue(result.ok)
        mock_llm.assert_not_called()

    def test_llm_failure_returns_not_ok(self):
        state = make_minimal_state()
        with patch.object(pov, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            result = pov.audit_chapter(state, 5, "x" * 200)
        self.assertFalse(result.ok)
        self.assertEqual(result.new_facts, [])

    def test_clamps_new_facts_to_8(self):
        state = make_minimal_state()
        fake = {
            "new_known_facts": [{"fact": f"事实{i}", "source": ""} for i in range(20)],
            "pov_violations": [],
        }
        with patch.object(pov, "request_json_with_profile", return_value=fake):
            result = pov.audit_chapter(state, 5, "x" * 200)
        self.assertLessEqual(len(result.new_facts), 8)


class TestMergeFactsIntoState(unittest.TestCase):
    def test_appends_new_facts(self):
        state = make_minimal_state()
        # 确保字段存在
        state.protagonist_known_facts = []
        pov.merge_facts_into_state(state, [
            {"fact": "A", "source": "对话", "learned_chapter": 3},
            {"fact": "B", "source": "亲见", "learned_chapter": 3},
        ])
        self.assertEqual(len(state.protagonist_known_facts), 2)

    def test_dedup_by_fact_string(self):
        state = make_minimal_state()
        state.protagonist_known_facts = [
            {"fact": "A", "source": "对话", "learned_chapter": 3},
        ]
        pov.merge_facts_into_state(state, [
            {"fact": "A", "source": "对话", "learned_chapter": 5},  # 重复 → 跳
            {"fact": "C", "source": "推理", "learned_chapter": 5},  # 新 → 留
        ])
        self.assertEqual(len(state.protagonist_known_facts), 2)
        # 第一个 A 仍是 chapter 3(没被覆盖)
        self.assertEqual(state.protagonist_known_facts[0]["learned_chapter"], 3)

    def test_creates_field_if_absent(self):
        state = make_minimal_state()
        if hasattr(state, "protagonist_known_facts"):
            delattr(state, "protagonist_known_facts")
        pov.merge_facts_into_state(state, [
            {"fact": "X", "source": "对话", "learned_chapter": 1},
        ])
        self.assertTrue(hasattr(state, "protagonist_known_facts"))
        self.assertEqual(len(state.protagonist_known_facts), 1)


class TestAuditAndApply(unittest.TestCase):
    def test_full_cycle(self):
        state = make_minimal_state()
        state.protagonist_known_facts = []
        fake = {
            "new_known_facts": [{"fact": "新事实", "source": "对话"}],
            "pov_violations": [],
        }
        with patch.object(pov, "request_json_with_profile", return_value=fake):
            result = pov.audit_and_apply(state, 5, "x" * 200)
        self.assertTrue(result.ok)
        # 事实入库了
        self.assertEqual(len(state.protagonist_known_facts), 1)
        self.assertEqual(state.protagonist_known_facts[0]["fact"], "新事实")


class TestFormatKnownFacts(unittest.TestCase):
    def test_empty_returns_empty(self):
        state = make_minimal_state()
        self.assertEqual(pov._format_known_facts(state), "")

    def test_renders_recent_facts(self):
        state = make_minimal_state()
        state.protagonist_known_facts = [
            {"fact": "A", "source": "对话", "learned_chapter": 3},
            {"fact": "B", "source": "亲见", "learned_chapter": 5},
        ]
        text = pov._format_known_facts(state)
        self.assertIn("A", text)
        self.assertIn("B", text)
        self.assertIn("第3章", text)


if __name__ == "__main__":
    unittest.main()
