"""开篇 10 章 3 阶段路由 + writer system 加载测试。

覆盖 (取代旧 test_golden_three.py):
  · chapter_dispatcher 路由
    - 卷 1 ch 1-3 → opening_kick_off
    - 卷 1 ch 4-7 → opening_establish
    - 卷 1 ch 8-10 → opening_main_line
    - 卷 1 ch 11+ → 不再走开篇路由
    - 卷 2 ch 1+ → 不走开篇 3 阶段(只卷 1 启用)
  · get_writer_system 加载 3 个 OPENING_ 模板
  · prompt_variants 模块包含新 3 个 WRITER_SYSTEM_OPENING_* 常量
  · prompts_registry 注册了新 entry
  · 旧 GOLDEN_ONE/TWO/THREE 常量应不再存在

测试不发真 LLM 调用,纯路由 + 模板加载。
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


class TestOpeningPhasesRouting(unittest.TestCase):
    """dispatch() 路由开篇 3 阶段 (取代旧黄金三章)。"""

    def _dispatch(self, vol: int, ch: int):
        from agents.chapter_dispatcher import dispatch
        state = make_minimal_state()
        return dispatch(state, _make_directive(volume_index=vol, chapter_index=ch))

    # ── 钩人期 1-3 ───────────────────────────────────
    def test_vol1_ch1_routes_to_kick_off(self):
        plan = self._dispatch(1, 1)
        self.assertEqual(plan.writer_variant, "opening_kick_off")
        self.assertEqual(plan.archetype, "opening_kick_off:ch1")
        self.assertEqual(plan.signals.get("opening_phase"), "opening_kick_off")

    def test_vol1_ch2_routes_to_kick_off(self):
        plan = self._dispatch(1, 2)
        self.assertEqual(plan.writer_variant, "opening_kick_off")

    def test_vol1_ch3_routes_to_kick_off(self):
        plan = self._dispatch(1, 3)
        self.assertEqual(plan.writer_variant, "opening_kick_off")

    # ── 立住期 4-7 ───────────────────────────────────
    def test_vol1_ch4_routes_to_establish(self):
        plan = self._dispatch(1, 4)
        self.assertEqual(plan.writer_variant, "opening_establish")
        self.assertEqual(plan.archetype, "opening_establish:ch4")

    def test_vol1_ch5_routes_to_establish(self):
        plan = self._dispatch(1, 5)
        self.assertEqual(plan.writer_variant, "opening_establish")

    def test_vol1_ch7_routes_to_establish(self):
        plan = self._dispatch(1, 7)
        self.assertEqual(plan.writer_variant, "opening_establish")

    # ── 入主线期 8-10 ───────────────────────────────
    def test_vol1_ch8_routes_to_main_line(self):
        plan = self._dispatch(1, 8)
        self.assertEqual(plan.writer_variant, "opening_main_line")
        self.assertEqual(plan.archetype, "opening_main_line:ch8")

    def test_vol1_ch10_routes_to_main_line(self):
        plan = self._dispatch(1, 10)
        self.assertEqual(plan.writer_variant, "opening_main_line")

    # ── 边界:卷 1 ch 11+ 不再走开篇路由 ──────────────
    def test_vol1_ch11_no_opening_variant(self):
        plan = self._dispatch(1, 11)
        # ch 11 已超出 OPENING_CHAPTER_THRESHOLD(=10) → 走 default
        self.assertEqual(plan.writer_variant, "default")
        self.assertNotIn("opening", plan.archetype.lower())

    # ── 卷 2 不走开篇 3 阶段 ────────────────────────
    def test_vol2_ch1_not_3_phase(self):
        """卷 2 章 1 仍是 is_book_opening(前 N 章窗口)但不走 3 阶段(只卷 1 启用)."""
        plan = self._dispatch(2, 1)
        # 卷 2 即便在窗口内,也走通用 "opening" 而非 3 阶段
        self.assertEqual(plan.writer_variant, "opening")
        self.assertNotIn("opening_kick_off", plan.archetype)


class TestOpeningSystemTemplates(unittest.TestCase):
    """get_writer_system 加载 3 个 OPENING 模板。"""

    def test_kick_off_returns_template_with_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("opening_kick_off", genre="测试题材")
        self.assertIsNotNone(sys)
        # 钩人期核心: 处境 / 悬念 / 不强制金手指
        self.assertIn("处境", sys)
        self.assertIn("悬念", sys)
        self.assertIn("测试题材", sys)  # genre 替换
        # 阶段名标记
        self.assertIn("钩人", sys)

    def test_establish_returns_template_with_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("opening_establish", genre="测试题材")
        self.assertIsNotNone(sys)
        # 立住期核心: 驱动力 / 世界规则 / 长线钩子
        self.assertIn("驱动力", sys)
        self.assertIn("世界规则", sys)
        self.assertIn("立住", sys)

    def test_main_line_returns_template_with_constraints(self):
        from agents.chapter_dispatcher import get_writer_system
        sys = get_writer_system("opening_main_line", genre="测试题材")
        self.assertIsNotNone(sys)
        # 入主线期核心: 方向感 / 不可逆 / 阶段性
        self.assertIn("方向感", sys)
        self.assertIn("不可逆", sys)
        self.assertIn("入主线", sys)

    def test_default_variant_returns_none(self):
        from agents.chapter_dispatcher import get_writer_system
        self.assertIsNone(get_writer_system("default"))
        self.assertIsNone(get_writer_system(""))

    def test_unknown_variant_returns_none(self):
        from agents.chapter_dispatcher import get_writer_system
        self.assertIsNone(get_writer_system("nonexistent_variant"))


class TestPromptVariantsModule(unittest.TestCase):
    """prompt_variants 模块结构 —— 新 3 阶段常量。"""

    def test_three_opening_constants_exist(self):
        import agents.prompt_variants as pv
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_OPENING_KICK_OFF"))
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_OPENING_ESTABLISH"))
        self.assertTrue(hasattr(pv, "WRITER_SYSTEM_OPENING_MAIN_LINE"))

    def test_old_golden_constants_removed(self):
        """旧的 GOLDEN_ONE/TWO/THREE 应不再存在(已被 OPENING_* 替代)。"""
        import agents.prompt_variants as pv
        self.assertFalse(hasattr(pv, "WRITER_SYSTEM_GOLDEN_ONE"),
                          "旧 WRITER_SYSTEM_GOLDEN_ONE 应已删除")
        self.assertFalse(hasattr(pv, "WRITER_SYSTEM_GOLDEN_TWO"),
                          "旧 WRITER_SYSTEM_GOLDEN_TWO 应已删除")
        self.assertFalse(hasattr(pv, "WRITER_SYSTEM_GOLDEN_THREE"),
                          "旧 WRITER_SYSTEM_GOLDEN_THREE 应已删除")

    def test_all_opening_contain_genre_placeholder(self):
        import agents.prompt_variants as pv
        for name in ("WRITER_SYSTEM_OPENING_KICK_OFF",
                      "WRITER_SYSTEM_OPENING_ESTABLISH",
                      "WRITER_SYSTEM_OPENING_MAIN_LINE"):
            tmpl = getattr(pv, name)
            self.assertIn("{genre}", tmpl, f"{name} 缺少 {{genre}} 占位符")

    def test_opening_systems_have_substantive_length(self):
        """每个 OPENING system 都应有实质内容(>800 字),不是空壳。"""
        import agents.prompt_variants as pv
        for name in ("WRITER_SYSTEM_OPENING_KICK_OFF",
                      "WRITER_SYSTEM_OPENING_ESTABLISH",
                      "WRITER_SYSTEM_OPENING_MAIN_LINE"):
            tmpl = getattr(pv, name)
            self.assertGreater(len(tmpl), 800, f"{name} 内容过短(<800 字符)")

    def test_no_meta_marketing_jargon(self):
        """新 prompt 应去掉 meta 营销语,改为给叙事原则。"""
        import agents.prompt_variants as pv
        forbidden_words = ["爆款", "拍案", "朋友圈", "截图发"]
        for name in ("WRITER_SYSTEM_OPENING_KICK_OFF",
                      "WRITER_SYSTEM_OPENING_ESTABLISH",
                      "WRITER_SYSTEM_OPENING_MAIN_LINE"):
            tmpl = getattr(pv, name)
            for word in forbidden_words:
                self.assertNotIn(word, tmpl,
                                  f"{name} 不应含 meta 营销语「{word}」")

    def test_no_hardcoded_word_count(self):
        """字数应由 length_governor 管,prompt 里不硬编码具体字数范围。"""
        import agents.prompt_variants as pv
        forbidden_patterns = ["2800-3500", "3000-4000", "3000 字", "字数 2"]
        for name in ("WRITER_SYSTEM_OPENING_KICK_OFF",
                      "WRITER_SYSTEM_OPENING_ESTABLISH",
                      "WRITER_SYSTEM_OPENING_MAIN_LINE"):
            tmpl = getattr(pv, name)
            for pat in forbidden_patterns:
                self.assertNotIn(pat, tmpl,
                                  f"{name} 不应硬编码字数「{pat}」(应由 length_governor 管)")


class TestPromptsRegistryEntries(unittest.TestCase):
    """prompts_registry 注册了新 3 阶段 entry,删除了旧黄金三章 entry。"""

    def test_three_opening_entries_registered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_OPENING_KICK_OFF", ids)
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_OPENING_ESTABLISH", ids)
        self.assertIn("agents.prompt_variants:WRITER_SYSTEM_OPENING_MAIN_LINE", ids)

    def test_old_golden_entries_unregistered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertNotIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_ONE", ids)
        self.assertNotIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_TWO", ids)
        self.assertNotIn("agents.prompt_variants:WRITER_SYSTEM_GOLDEN_THREE", ids)


class TestThresholdConfig(unittest.TestCase):
    """OPENING_CHAPTER_THRESHOLD 应为 10(配合 3 阶段)。"""

    def test_threshold_is_10(self):
        from agents.chapter_dispatcher import OPENING_CHAPTER_THRESHOLD
        self.assertEqual(OPENING_CHAPTER_THRESHOLD, 10)


if __name__ == "__main__":
    unittest.main()
