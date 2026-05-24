"""HookType / HookSpec / ChapterBlueprint.closing_hook_spec 回归测试。

覆盖:
  · HookType 枚举 7 类完整
  · HookSpec dataclass 构造 + Optional 属性
  · ChapterBlueprint 默认 closing_hook_spec=None(向后兼容)
  · _load_chapter_summary 容忍旧 state.json 缺 closing_hook_type 字段
  · writer._get_hook_instruction 按 HookType 选指引
  · critic dim_scores 包含 length_compliance / hook_type_compliance

测试用 tests/_helpers.make_minimal_state,不发真 LLM 调用。
"""
import unittest
from tests._helpers import make_minimal_state


class TestHookTypeEnum(unittest.TestCase):
    """7 类 HookType."""

    def test_seven_types_exist(self):
        from persistence.state import HookType
        expected = {
            "suspense", "reversal", "info_reveal", "emotional",
            "physical", "death", "cliff",
        }
        actual = {h.value for h in HookType}
        self.assertEqual(actual, expected)

    def test_value_lookup(self):
        from persistence.state import HookType
        self.assertEqual(HookType("suspense"), HookType.SUSPENSE)
        self.assertEqual(HookType("info_reveal"), HookType.INFO_REVEAL)

    def test_invalid_value_raises(self):
        from persistence.state import HookType
        with self.assertRaises(ValueError):
            HookType("nonexistent_type")


class TestHookSpec(unittest.TestCase):
    """HookSpec dataclass."""

    def test_construct_with_type_and_text(self):
        from persistence.state import HookSpec, HookType
        spec = HookSpec(type=HookType.SUSPENSE, text="门外传来师父的咳嗽声")
        self.assertEqual(spec.type, HookType.SUSPENSE)
        self.assertEqual(spec.text, "门外传来师父的咳嗽声")

    def test_default_text_empty(self):
        from persistence.state import HookSpec, HookType
        spec = HookSpec(type=HookType.PHYSICAL)
        self.assertEqual(spec.text, "")


class TestChapterBlueprintBackwardCompat(unittest.TestCase):
    """ChapterBlueprint.closing_hook_spec 默认 None,旧代码不传也能构造."""

    def test_blueprint_without_hook_spec(self):
        from persistence.state import ChapterBlueprint
        bp = ChapterBlueprint(
            chapter_index=1, opening_state="开始", chapter_delta="变化",
            scene_beats=[], closing_hook="悬念待续", pacing_note="中等",
        )
        self.assertIsNone(bp.closing_hook_spec)

    def test_blueprint_with_hook_spec(self):
        from persistence.state import ChapterBlueprint, HookSpec, HookType
        spec = HookSpec(type=HookType.REVERSAL, text="主角微笑")
        bp = ChapterBlueprint(
            chapter_index=1, opening_state="开始", chapter_delta="变化",
            scene_beats=[], closing_hook="主角嘴角微扬", pacing_note="中等",
            closing_hook_spec=spec,
        )
        self.assertEqual(bp.closing_hook_spec.type, HookType.REVERSAL)


class TestChapterSummaryClosingHookType(unittest.TestCase):
    """ChapterSummary.closing_hook_type 字段 + 反序列化兼容."""

    def test_default_empty_string(self):
        from persistence.state import ChapterSummary, TensionLevel
        s = ChapterSummary(
            index=1, volume_index=1, title="测试", summary="测试摘要",
            word_count=3000, tension=TensionLevel.RISING,
        )
        self.assertEqual(s.closing_hook_type, "")
        # setup_callbacks_invoked 也是 Batch 2 加的字段
        self.assertEqual(s.setup_callbacks_invoked, [])

    def test_load_chapter_summary_no_hook_type_key(self):
        from persistence.checkpoint import _load_chapter_summary
        d = {
            "index": 5, "volume_index": 1, "title": "测试章",
            "summary": "测试", "word_count": 3000, "tension": "RISING",
            # 没有 closing_hook_type 字段——模拟旧 state.json
        }
        s = _load_chapter_summary(d)
        self.assertEqual(s.closing_hook_type, "")
        self.assertEqual(s.setup_callbacks_invoked, [])

    def test_load_chapter_summary_with_hook_type(self):
        from persistence.checkpoint import _load_chapter_summary
        d = {
            "index": 5, "volume_index": 1, "title": "测试章",
            "summary": "测试", "word_count": 3000, "tension": "RISING",
            "closing_hook_type": "suspense",
            "setup_callbacks_invoked": ["setup_0001", "setup_0002"],
        }
        s = _load_chapter_summary(d)
        self.assertEqual(s.closing_hook_type, "suspense")
        self.assertEqual(s.setup_callbacks_invoked, ["setup_0001", "setup_0002"])


class TestWriterHookInstruction(unittest.TestCase):
    """writer._get_hook_instruction 按 HookType 选具体写作指引."""

    def _make_directive(self, hook_type=None, position="中段"):
        from persistence.state import (
            ChapterDirective, ChapterBlueprint, HookSpec, HookType,
            TensionLevel, RhythmType,
        )
        d = ChapterDirective(
            chapter_index=10, volume_index=1,
            tension=TensionLevel.RISING, rhythm=RhythmType.SLOW_BUILD,
            active_lines=[], primary_line="",
            must_include=[], satisfaction_points=[],
            foreshadow_plant=[], foreshadow_resolve=[],
            emotional_note="", chapter_position=position, word_pace="中等",
        )
        if hook_type is not None:
            d.blueprint = ChapterBlueprint(
                chapter_index=10, opening_state="", chapter_delta="",
                scene_beats=[], closing_hook="测试画面", pacing_note="",
                closing_hook_spec=HookSpec(type=hook_type, text="测试画面"),
            )
        return d

    def test_hook_spec_drives_instruction(self):
        from persistence.state import HookType
        from agents.writer import _get_hook_instruction
        d = self._make_directive(hook_type=HookType.REVERSAL)
        result = _get_hook_instruction(d)
        self.assertIn("reversal", result)
        self.assertIn("反转", result)

    def test_no_blueprint_falls_back_to_position(self):
        from agents.writer import _get_hook_instruction
        d = self._make_directive(hook_type=None, position="卷尾")
        result = _get_hook_instruction(d)
        # 卷尾 → 震撼卷尾钩子(无 blueprint 时降级)
        self.assertIn("卷尾", result)

    def test_each_hook_type_has_hint(self):
        from persistence.state import HookType
        from agents.writer import _HOOK_TYPE_HINTS
        # 每个 HookType 都要有指引
        for h in HookType:
            self.assertIn(h.value, _HOOK_TYPE_HINTS,
                          f"HookType.{h.name} 在 _HOOK_TYPE_HINTS 中缺指引")


class TestCriticDimsExtended(unittest.TestCase):
    """critic.review_chapter 的 dim_scores 包含新加的两个维度."""

    def test_dim_scores_keys(self):
        # 不实际调 LLM,只看 _build_shared_context 返回结构和 dim_scores 字段名
        # 这两个字段必须在 critic.review_chapter 的 dim_scores 字典里被定义
        # (即使 LLM 失败 → None,字段名仍存在)
        import agents.critic as critic_mod
        import inspect
        src = inspect.getsource(critic_mod.review_chapter)
        self.assertIn("length_compliance", src)
        self.assertIn("hook_type_compliance", src)

    def test_build_shared_context_has_length_and_hook_info(self):
        from agents.critic import _build_shared_context
        from persistence.state import (
            ChapterDirective, TensionLevel, RhythmType,
        )
        state = make_minimal_state()
        d = ChapterDirective(
            chapter_index=5, volume_index=1,
            tension=TensionLevel.RISING, rhythm=RhythmType.SLOW_BUILD,
            active_lines=[], primary_line="",
            must_include=[], satisfaction_points=[],
            foreshadow_plant=[], foreshadow_resolve=[],
            emotional_note="", chapter_position="中段", word_pace="中等",
        )
        ctx = _build_shared_context(state, d, "测试正文" * 100)
        self.assertIn("length_info", ctx)
        self.assertIn("hook_distribution_info", ctx)
        self.assertIn("字", ctx["length_info"])


if __name__ == "__main__":
    unittest.main()
