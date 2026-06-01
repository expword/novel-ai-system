"""core/revise_loop.run_revise_loop 回归测试。

覆盖：
  · clean exit ——audit 通过就立刻退出
  · max_rounds ——跑满都没改干净就退
  · short_streak ——连续过短退出
  · on_residual ——残留 critical 时回调
  · 长度兜底 ——单轮过短丢弃此轮、下一轮重试
"""
import unittest
from tests._helpers import make_minimal_state  # noqa
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeAudit:
    def __init__(self, critical_count):
        self.critical_count = critical_count

    def __repr__(self):
        return f"FakeAudit(critical={self.critical_count})"


class _FakeState:
    completed_chapters = []


def _make_config(*, max_rounds=3, short_threshold=2):
    """构造一个会逐轮把 critical 数 -1 的 audit 序列 + revise 函数。"""
    from core.revise_loop import ReviseConfig

    counter = {"a": 0, "r": 0}

    def audit(state, ci, text):
        counter["a"] += 1
        if "rev3" in text: return _FakeAudit(0)
        if "rev2" in text: return _FakeAudit(1)
        if "rev1" in text: return _FakeAudit(2)
        return _FakeAudit(3)

    def revise(state, directive, text, fb):
        counter["r"] += 1
        n = text.count("rev") + 1
        return text + f"_rev{n}" + "x" * len(text)

    cfg = ReviseConfig(
        label="test",
        audit_fn=audit,
        needs_revise=lambda a: a.critical_count > 0,
        feedback_builder=lambda a, r: f"round {r} fix {a.critical_count}",
        revise_fn=revise,
        max_rounds=max_rounds,
        max_short_streak=short_threshold,
    )
    return cfg, counter


class TestReviseLoop(unittest.TestCase):

    def test_clean_exit_when_audit_passes(self):
        from core.revise_loop import run_revise_loop
        cfg, counter = _make_config(max_rounds=3)
        result = run_revise_loop(
            state=_FakeState(), chapter_index=1, directive=None,
            config=cfg, initial_text="rev3" + "x" * 100,  # 初稿已是 rev3 = critical 0
        )
        self.assertEqual(result.exit_reason, "no_initial_revise_needed")
        self.assertEqual(result.rounds_run, 0)
        self.assertEqual(counter["r"], 0)  # 没调 revise

    def test_clean_after_3_rounds(self):
        from core.revise_loop import run_revise_loop
        cfg, counter = _make_config(max_rounds=3)
        result = run_revise_loop(
            state=_FakeState(), chapter_index=1, directive=None,
            config=cfg, initial_text="start" * 100,  # 初始 critical=3
        )
        self.assertEqual(result.exit_reason, "clean")
        self.assertEqual(result.rounds_accepted, 3)
        self.assertEqual(result.last_audit.critical_count, 0)
        self.assertFalse(result.residual_needs_revise)

    def test_max_rounds_residual(self):
        from core.revise_loop import ReviseConfig, run_revise_loop
        # 模拟"永远修不干净"——audit 总是返回 critical=2
        cfg = ReviseConfig(
            label="stuck",
            audit_fn=lambda s, ci, t: _FakeAudit(2),
            needs_revise=lambda a: a.critical_count > 0,
            feedback_builder=lambda a, r: "fix",
            revise_fn=lambda s, d, t, fb: t + "_more_text" + "x" * len(t),
            max_rounds=2,
        )
        residual = []
        cfg.on_residual_critical = lambda a: residual.append(a.critical_count)
        result = run_revise_loop(
            state=_FakeState(), chapter_index=1, directive=None,
            config=cfg, initial_text="start" * 100,
        )
        self.assertEqual(result.exit_reason, "max_rounds")
        self.assertEqual(result.rounds_run, 2)
        self.assertTrue(result.residual_needs_revise)
        self.assertEqual(residual, [2])

    def test_short_streak_breaks(self):
        from core.revise_loop import ReviseConfig, run_revise_loop
        # revise 永远返回短文本——应连续过短 2 次后退出
        short_logs = []
        cfg = ReviseConfig(
            label="short-llm",
            audit_fn=lambda s, ci, t: _FakeAudit(3),
            needs_revise=lambda a: a.critical_count > 0,
            feedback_builder=lambda a, r: "fix",
            revise_fn=lambda s, d, t, fb: "x" * 10,  # 极短
            max_rounds=5,
            min_length_ratio=0.7,
            max_short_streak=2,
            on_short=lambda r, n, o, s: short_logs.append((r, s)),
        )
        result = run_revise_loop(
            state=_FakeState(), chapter_index=1, directive=None,
            config=cfg, initial_text="x" * 1000,
        )
        self.assertEqual(result.exit_reason, "short_streak")
        self.assertEqual(result.rounds_accepted, 0)
        self.assertEqual(len(short_logs), 2)
        self.assertEqual(short_logs[-1][1], 2)  # streak 计数到 2 时退出

    def test_single_short_then_recovers(self):
        from core.revise_loop import ReviseConfig, run_revise_loop
        # 第 1 轮过短、第 2 轮正常 → 不会因第 1 轮过短而退；第 2 轮 audit 通过即退
        calls = {"r": 0}
        def rev(s, d, t, fb):
            calls["r"] += 1
            if calls["r"] == 1:
                return "x" * 10  # 第 1 轮过短
            return t + "_ok" + "x" * len(t)  # 第 2 轮正常

        # audit 调用顺序：① initial（入口判定 needs_revise）② 第 2 轮接受后
        # （第 1 轮过短被丢弃，没调 audit）
        audit_seq = [_FakeAudit(3), _FakeAudit(0)]
        def audit(s, ci, t):
            return audit_seq.pop(0) if audit_seq else _FakeAudit(0)

        cfg = ReviseConfig(
            label="recover", audit_fn=audit,
            needs_revise=lambda a: a.critical_count > 0,
            feedback_builder=lambda a, r: "fix",
            revise_fn=rev,
            max_rounds=3, max_short_streak=2,
        )
        result = run_revise_loop(
            state=_FakeState(), chapter_index=1, directive=None,
            config=cfg, initial_text="x" * 1000,
        )
        self.assertEqual(result.exit_reason, "clean")
        self.assertEqual(result.rounds_run, 2)  # 1 短轮 + 1 接受轮
        self.assertEqual(result.rounds_accepted, 1)


if __name__ == "__main__":
    unittest.main()
