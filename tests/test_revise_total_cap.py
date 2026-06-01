"""tests for revise_loop P0-2 跨 audit 总上限。"""
from __future__ import annotations
import unittest

from core.revise_loop import (
    MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER,
    get_total_rounds_used, add_total_rounds_used, is_total_cap_exceeded,
    ReviseConfig, run_revise_loop, ReviseResult,
)


class _DummyDirective:
    """裸壳供 setattr。"""
    chapter_index = 1
    volume_index = 1


class TestCounterAPI(unittest.TestCase):
    def test_initial_zero(self):
        d = _DummyDirective()
        self.assertEqual(get_total_rounds_used(d), 0)
        self.assertFalse(is_total_cap_exceeded(d))

    def test_add_and_read(self):
        d = _DummyDirective()
        add_total_rounds_used(d, 3)
        self.assertEqual(get_total_rounds_used(d), 3)
        add_total_rounds_used(d, 1)
        self.assertEqual(get_total_rounds_used(d), 4)

    def test_cap_at_max(self):
        d = _DummyDirective()
        add_total_rounds_used(d, MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER)
        self.assertTrue(is_total_cap_exceeded(d))

    def test_cap_just_below(self):
        d = _DummyDirective()
        add_total_rounds_used(d, MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER - 1)
        self.assertFalse(is_total_cap_exceeded(d))

    def test_none_directive_safe(self):
        """directive=None 不应崩,且永远 not exceeded。"""
        self.assertEqual(get_total_rounds_used(None), 0)
        self.assertFalse(is_total_cap_exceeded(None))
        # add 在 None 上也不应崩
        add_total_rounds_used(None, 3)
        self.assertEqual(get_total_rounds_used(None), 0)


class TestReviseLoopShortCircuit(unittest.TestCase):
    """已达上限时 run_revise_loop 应直接 short-circuit。"""

    def test_skip_when_cap_exceeded(self):
        d = _DummyDirective()
        add_total_rounds_used(d, MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER)

        revise_calls = []
        def _revise(s, dd, t, fb):
            revise_calls.append(1)
            return t + " REVISED"

        cfg = ReviseConfig(
            label="test-revise",
            audit_fn=lambda s, ci, t: {"needs": True},
            needs_revise=lambda a: True,
            feedback_builder=lambda a, r: "feedback",
            revise_fn=_revise,
            max_rounds=3,
        )
        result = run_revise_loop(
            state=None, chapter_index=1, directive=d,
            config=cfg, initial_text="原稿",
        )
        # 应 short-circuit
        self.assertEqual(result.exit_reason, "total_cap_exceeded")
        self.assertEqual(result.final_text, "原稿")
        self.assertEqual(len(revise_calls), 0)  # revise_fn 不被调用

    def test_accepts_rounds_under_cap(self):
        d = _DummyDirective()
        # 1 已用,容量还剩 4

        add_total_rounds_used(d, 1)

        revise_count = [0]
        # 第 1 轮 revise 后 needs_revise 立刻 False -> clean 退出
        def _audit(s, ci, t):
            return {"round": revise_count[0]}

        def _needs(a):
            return a.get("round", 0) == 0  # 只第 1 轮需要 revise

        def _revise(s, dd, t, fb):
            revise_count[0] += 1
            return t + " R" + str(revise_count[0])

        cfg = ReviseConfig(
            label="test",
            audit_fn=_audit,
            needs_revise=_needs,
            feedback_builder=lambda a, r: "f",
            revise_fn=_revise,
            max_rounds=3,
            min_length_ratio=0.0,  # 不丢弃短输出(避免长度兜底干扰)
        )
        result = run_revise_loop(
            state=None, chapter_index=1, directive=d,
            config=cfg, initial_text="原稿",
        )
        # 应跑成功 1 轮
        self.assertEqual(result.rounds_accepted, 1)
        self.assertEqual(result.exit_reason, "clean")
        # 计数器累加 1
        self.assertEqual(get_total_rounds_used(d), 2)

    def test_accumulator_persists_across_calls(self):
        """两次 run_revise_loop 调用,计数器累加。"""
        d = _DummyDirective()

        def _revise(s, dd, t, fb):
            return t + " R"

        # 第 1 次: 1 轮 accept
        cfg1 = ReviseConfig(
            label="r1",
            audit_fn=lambda s, ci, t: {"x": True},
            needs_revise=lambda a: a.get("x", False),  # 永远 needs revise
            feedback_builder=lambda a, r: "f",
            revise_fn=lambda s, dd, t, fb: t + "R1",
            max_rounds=1,
            min_length_ratio=0.0,
        )
        r1 = run_revise_loop(
            state=None, chapter_index=1, directive=d,
            config=cfg1, initial_text="原稿",
        )
        self.assertEqual(get_total_rounds_used(d), 1)

        # 第 2 次: 1 轮 accept
        r2 = run_revise_loop(
            state=None, chapter_index=1, directive=d,
            config=cfg1, initial_text="原稿2",
        )
        self.assertEqual(get_total_rounds_used(d), 2)


if __name__ == "__main__":
    unittest.main()
