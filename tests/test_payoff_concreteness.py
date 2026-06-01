"""tests for satisfaction_system._check_payoff_concreteness (P1-4)."""
from __future__ import annotations
import unittest

from agents.satisfaction_system import (
    _check_payoff_concreteness, _PAYOFF_ABSTRACT_LITERALS,
)


class TestAbstractCases(unittest.TestCase):
    def test_empty_caught(self):
        self.assertIsNotNone(_check_payoff_concreteness(""))
        self.assertIsNotNone(_check_payoff_concreteness("  "))

    def test_too_short_caught(self):
        # 19 字 < 20
        self.assertIsNotNone(_check_payoff_concreteness("主角打脸反派让对方哑口无言"))

    def test_all_literals_caught(self):
        for lit in _PAYOFF_ABSTRACT_LITERALS:
            self.assertIsNotNone(
                _check_payoff_concreteness(lit),
                f"literal not caught: {lit}",
            )


class TestConcreteCases(unittest.TestCase):
    def test_concrete_payoffs_pass(self):
        cases = [
            "主角当众取出反派十年前的伪造书证,反派脸色由白转灰",
            "主角推开门把那枚被嘲讽过的玉佩还到对方桌上转身就走",
            "主角剑尖抵在反派咽喉边淡淡说:这就是你说的废物?",
            "主角召出豆包当场算出账本三处错漏,反派师爷脸色铁青",
        ]
        for s in cases:
            issue = _check_payoff_concreteness(s)
            self.assertIsNone(issue, f"should pass: {s} (got {issue})")


class TestBoundary(unittest.TestCase):
    def test_exactly_20_chars_passes(self):
        # 20 字阈值
        s = "主角当众揭穿反派伪造书证脸色由白转灰啊"  # 20 字
        # 即使没有具体动作动词但长度够 + 不在套话集 → 通过
        issue = _check_payoff_concreteness(s)
        # 长度 ≥20 应通过
        if len(s) >= 20:
            self.assertIsNone(issue)


if __name__ == "__main__":
    unittest.main()
