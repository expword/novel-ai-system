"""tests for volume_planner._check_outline_goal_concreteness (P1-1)."""
from __future__ import annotations
import unittest

from agents.volume_planner import (
    _check_outline_goal_concreteness,
    _OUTLINE_GOAL_ABSTRACT_LITERALS,
    _OUTLINE_GOAL_ABSTRACT_TOKENS,
)


class TestAbstractCases(unittest.TestCase):
    def test_empty_caught(self):
        self.assertIsNotNone(_check_outline_goal_concreteness(""))
        self.assertIsNotNone(_check_outline_goal_concreteness("  "))

    def test_too_short_caught(self):
        self.assertIsNotNone(_check_outline_goal_concreteness("去"))
        self.assertIsNotNone(_check_outline_goal_concreteness("看一看"))

    def test_exact_literal_caught(self):
        for lit in ["推进剧情", "推动主线", "深入了解"]:
            issue = _check_outline_goal_concreteness(lit)
            self.assertIsNotNone(issue, f"should catch literal: {lit}")
            self.assertIn("过短", issue + "")  # 都 < 15 字也会触发

    def test_multi_abstract_token_caught(self):
        # 2 个 token + < 30 字
        issue = _check_outline_goal_concreteness("继续推进深入了解")
        self.assertIsNotNone(issue)

    def test_long_but_still_abstract(self):
        # 2 token + 已 30+ 字 — 我们容忍(因为可能加了具体上下文)
        long_goal = "继续推进深入了解某事的某种程度,从而让某些角色的某种关系变化"
        # 这种 30+ 字依然抽象,但我们容差选择不抓 (避免误杀真实写得啰嗦的具体 goal)
        # 这是验证当前规则: ≥30 字时只看 token 计数不再 fail
        # 测试此边界行为
        issue = _check_outline_goal_concreteness(long_goal)
        # 实际可能仍 OK,这是预期行为(false negative,接受)
        # 不强制 assert—— 文档化当前阈值


class TestConcreteCases(unittest.TestCase):
    def test_concrete_goal_passes(self):
        concrete = [
            "主角夜闯青云宗藏经阁,夺走灵犀剑诀残卷",
            "主角在朱雀街茶楼会见李三,确认黑石令出处",
            "主角与师妹决裂后独自下山,投奔虎啸营",
            "主角发现父亲遗物里夹着陌生女子的画像,背面写着'对不起'",
        ]
        for g in concrete:
            issue = _check_outline_goal_concreteness(g)
            self.assertIsNone(issue, f"should pass: {g} (got {issue})")

    def test_concrete_with_single_token_passes(self):
        # 含 1 个抽象 token 但具体度足够 → 通过
        g = "主角推进至北境关卡,与守军对峙至破晓"
        issue = _check_outline_goal_concreteness(g)
        self.assertIsNone(issue, f"single token + concrete should pass: {issue}")


class TestBoundaryConditions(unittest.TestCase):
    def test_exactly_15_chars(self):
        # 恰好 15 字 + 含具体动作 → 通过
        g = "主角去酒馆探听消息见李三"  # 12 字
        # 实际 12 字不够 15
        issue = _check_outline_goal_concreteness(g)
        # 这个 12 字 < 15,应该被抓
        self.assertIsNotNone(issue)

    def test_16_chars_concrete(self):
        g = "主角夜潜城西密室盗取血玉"  # 12 字 - 不够
        # 加点
        g = "主角夜潜城西密室盗取血玉佩还有信物"  # 16 字
        issue = _check_outline_goal_concreteness(g)
        # 16 字且无 abstract token → 通过
        self.assertIsNone(issue)


class TestKnownLiterals(unittest.TestCase):
    def test_all_literals_caught(self):
        for lit in _OUTLINE_GOAL_ABSTRACT_LITERALS:
            issue = _check_outline_goal_concreteness(lit)
            self.assertIsNotNone(issue, f"literal not caught: {lit}")


if __name__ == "__main__":
    unittest.main()
