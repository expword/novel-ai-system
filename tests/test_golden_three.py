"""黄金三章路由 + writer system 加载测试。

覆盖:
  · chapter_dispatcher 路由卷 1 章 1/2/3 到 golden_one/two/three
  · 卷 1 章 4/5 仍走 opening 变体(不是 golden)
  · 卷 2 章 1-3 不走 golden(只有卷 1 才是黄金三章)
  · get_writer_system 加载 3 个 golden system 各自包含本章硬约束
  · prompt_variants 模块包含 WRITER_SYSTEM_GOLDEN_ONE/TWO/THREE 常量

测试不发真 LLM 调用,纯路由逻辑 + system 模板加载。
"""
import unittest
from tests._helpers import make_minimal_state


def _make_directive(volume_index: int, chapter_index: int):
    from persistence.state import ChapterDirective, TensionLevel, RhythmType
    return ChapterDirective(
        chapter_index=chapter_index, volume_index=volume_index,
        tension=TensionLevel.RISING, rhythm=RhythmType.SLOW_BUILD,
        active_lines=[], primary_line="",
        must_include=[], satisfaction_points=[],
        foreshadow_plant=[], foreshadow_resolve=[],
        emotional_note="", chapter_position="开篇", word_pace="中等",
    )


class TestGoldenThreeRouting(unittest.TestCase):
    """dispatch() 路由黄金三章."""

    def test_vol1_ch1_routes_to_golden_one(self):
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=1))
        self.assertEqual(plan.writer_variant, "golden_one")
        self.assertEqual(plan.archetype, "golden_three:ch1")
        self.assertEqual(plan.signals.get("golden_three"), 1)

    def test_vol1_ch2_routes_to_golden_two(self):
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=2))
        self.assertEqual(plan.writer_variant, "golden_two")
        self.assertEqual(plan.archetype, "golden_three:ch2")

    def test_vol1_ch3_routes_to_golden_three(self):
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=3))
        self.assertEqual(plan.writer_variant, "golden_three")
        self.assertEqual(plan.archetype, "golden_three:ch3")

    def test_vol1_ch4_stays_opening(self):
        """卷 1 章 4 是开篇章但不是黄金三章——保留 opening."""
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=4))
        self.assertEqual(plan.writer_variant, "opening")
        self.assertNotIn("golden", plan.archetype.lower())

    def test_vol1_ch5_stays_opening(self):
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=5))
        self.assertEqual(plan.writer_variant, "opening")

    def test_vol1_ch6_no_special_variant(self):
        """卷 1 章 6 已不在开篇章窗口内."""
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=1, chapter_index=6))
        self.assertEqual(plan.writer_variant, "default")

    def test_vol2_ch1_not_golden(self):
        """卷 2 章 1 仍是开篇章变体,但不是黄金三章(只有卷 1 才是)."""
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        plan = dispatch(state, _make_directive(volume_index=2, chapter_index=1))
        self.assertEqual(plan.writer_variant, "opening")
        self.assertNotIn("golden", plan.archetype.lower())


class TestGoldenSystemTemplates(unittest.TestCase):
    """get_writer_system 加载 3 个 golden system."""

    def test_golden_one_contains_first_chapter_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("golden_one", genre="测试题材")
        self.assertIsNotNone(sys)
        # 第 1 章核心硬约束: 首句勾人
        self.assertIn("首句", sys)
        self.assertIn("第 1 章", sys)
        # genre 变量被替换
        self.assertIn("测试题材", sys)

    def test_golden_two_contains_second_chapter_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("golden_two", genre="测试题材")
        self.assertIsNotNone(sys)
        # 第 2 章核心: 小爽 + 主动行动
        self.assertIn("小爽", sys)
        self.assertIn("主动", sys)
        self.assertIn("第 2 章", sys)

    def test_golden_three_contains_third_chapter_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("golden_three", genre="测试题材")
        self.assertIsNotNone(sys)
        # 第 3 章核心: 大爽 + 拍案级钩子
        self.assertIn("大爽", sys)
        self.assertIn("拍案", sys)
        self.assertIn("第 3 章", sys)

    def test_default_variant_returns_none(self):
        from agents.chapter_dispatcher import get_writer_system
        self.assertIsNone(get_writer_system("default"))
        self.assertIsNone(get_writer_system(""))

    def test_unknown_variant_returns_none(self):
        from agents.chapter_dispatcher import get_writer_system
        self.assertIsNone(get_writer_system("nonexistent_variant"))


class TestPromptVariantsModule(unittest.TestCase):
    """prompt_variants 模块结构."""

    def test_all_three_golden_constants_exist(self):
        import agents.prompt_variants as pv
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_GOLDEN_ONE"))
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_GOLDEN_TWO"))
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_GOLDEN_THREE"))

    def test_all_golden_contain_genre_placeholder(self):
        import agents.prompt_variants as pv
        for name in ("WRITER_SYSTEM_GOLDEN_ONE", "WRITER_SYSTEM_GOLDEN_TWO",
                     "WRITER_SYSTEM_GOLDEN_THREE"):
            tmpl = getattr(pv, name)
            self.assertIn("{genre}", tmpl, f"{name} 缺少 {{genre}} 占位符")

    def test_golden_systems_have_substantive_length(self):
        """每个 golden system 都应是认真写的内容(>500 字),不是空壳."""
        import agents.prompt_variants as pv
        for name in ("WRITER_SYSTEM_GOLDEN_ONE", "WRITER_SYSTEM_GOLDEN_TWO",
                     "WRITER_SYSTEM_GOLDEN_THREE"):
            tmpl = getattr(pv, name)
            self.assertGreater(len(tmpl), 500, f"{name} 内容过短(<500 字符)")


class TestPromptsRegistryEntries(unittest.TestCase):
    """prompts_registry 注册了 3 个新 entry."""

    def test_three_golden_entries_registered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_ONE", ids)
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_TWO", ids)
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_THREE", ids)


if __name__ == "__main__":
    unittest.main()
