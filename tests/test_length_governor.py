"""tests for agents.length_governor."""
from __future__ import annotations
import unittest

from tests._helpers import make_minimal_state
from agents.length_governor import (
    compute_target_words, check_length, report_chapter_length,
    DEFAULT_TARGET,
)


class _ConceptPitch:
    """轻量 stub——避免引入完整 ConceptPitch dataclass。"""
    def __init__(self, target_platform: str):
        self.target_platform = target_platform


class TestComputeTarget(unittest.TestCase):
    def test_no_concept_pitch_returns_default(self):
        state = make_minimal_state()
        self.assertEqual(compute_target_words(state), DEFAULT_TARGET)

    def test_qidian_returns_qidian_range(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("起点中文网")
        # 起点 2500-3500 → 中位 3000
        self.assertEqual(compute_target_words(state), 3000)

    def test_fanqie_shorter(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("番茄小说")
        # 番茄 2000-3000 → 中位 2500
        self.assertEqual(compute_target_words(state), 2500)

    def test_jinjiang_longer(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("晋江文学城")
        # 晋江 3000-5000 → 中位 4000
        self.assertEqual(compute_target_words(state), 4000)

    def test_battle_chapter_gets_bigger_target(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("起点中文网")
        normal = compute_target_words(state)
        battle = compute_target_words(state, chapter_type="战斗章")
        self.assertGreater(battle, normal)
        # mult=1.3 → 3000*1.3=3900
        self.assertEqual(battle, int(3000 * 1.30))

    def test_daily_chapter_gets_smaller_target(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("起点中文网")
        normal = compute_target_words(state)
        daily = compute_target_words(state, chapter_type="日常章")
        self.assertLess(daily, normal)

    def test_unknown_platform_uses_default(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("某个不存在的平台")
        self.assertEqual(compute_target_words(state), DEFAULT_TARGET)

    def test_clamped_to_bounds(self):
        state = make_minimal_state()
        state.concept_pitch = _ConceptPitch("晋江")
        # 晋江(4000) * 战斗章(1.30) = 5200——在 [1500, 8000] 内
        result = compute_target_words(state, chapter_type="战斗章")
        self.assertGreaterEqual(result, 1500)
        self.assertLessEqual(result, 8000)


class TestCheckLength(unittest.TestCase):
    def test_within_tolerance_ok(self):
        # target 3000, 实际 2800 — 在 ±30% 内
        result = check_length("一" * 2800, 3000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["severity"], "info")

    def test_too_short_critical(self):
        # target 3000, 实际 1000 — 33% < 50%
        result = check_length("一" * 1000, 3000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "critical")

    def test_too_short_warn(self):
        # target 3000, 实际 1900 — 63% (< 70% lower bound, > 50%)
        result = check_length("一" * 1900, 3000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "warn")

    def test_too_long_warn(self):
        # target 3000, 实际 4500 — 150% < 150% upper
        result = check_length("一" * 4200, 3000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "warn")

    def test_too_long_critical(self):
        # target 3000, 实际 5000 — 167%
        result = check_length("一" * 5000, 3000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["severity"], "critical")

    def test_zero_target_no_check(self):
        result = check_length("一" * 1000, 0)
        self.assertTrue(result["ok"])

    def test_empty_text(self):
        result = check_length("", 3000)
        self.assertFalse(result["ok"])
        self.assertEqual(result["actual"], 0)


class TestReportFunction(unittest.TestCase):
    def test_report_doesnt_raise(self):
        # 调一次，验证不抛
        result = report_chapter_length(1, "一" * 1500, 3000)
        self.assertIn("ok", result)
        self.assertIn("actual", result)


if __name__ == "__main__":
    unittest.main()
