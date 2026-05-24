"""平台 rulebook + flavor_advisor (Batch 6) 回归测试。

覆盖:
  · resolve_platform_alias: 中文 / 英文 / 大小写归一
  · load_platform_rulebook: 6 个平台都能加载,未知平台返回空
  · format_platform_block: 空规则返回空字符串
  · FlavorAdvice dataclass 默认值
  · get_latest_advice_for_chapter: 过期 advice 被跳过
  · format_advice_for_prompt: 字符串拼接 + 空 list
  · generate_advice: 章节不够时直接返回 None(不发 LLM)
  · checkpoint._load_flavor_advice 反序列化 + 旧 state.json 兼容
"""
import unittest
from tests._helpers import make_minimal_state


class TestPlatformAlias(unittest.TestCase):
    def test_chinese_aliases(self):
        from utils.platform_rulebook import resolve_platform_alias
        self.assertEqual(resolve_platform_alias("起点"), "qidian")
        self.assertEqual(resolve_platform_alias("起点中文网"), "qidian")
        self.assertEqual(resolve_platform_alias("晋江"), "jjwxc")
        self.assertEqual(resolve_platform_alias("番茄"), "fanqie")
        self.assertEqual(resolve_platform_alias("飞卢"), "feilu")
        self.assertEqual(resolve_platform_alias("QQ阅读"), "qqyuedu")
        self.assertEqual(resolve_platform_alias("掌阅"), "zhangyue")

    def test_english_aliases_case_insensitive(self):
        from utils.platform_rulebook import resolve_platform_alias
        self.assertEqual(resolve_platform_alias("qidian"), "qidian")
        self.assertEqual(resolve_platform_alias("QIDIAN"), "qidian")
        self.assertEqual(resolve_platform_alias("JJWXC"), "jjwxc")

    def test_unknown_platform_returns_empty(self):
        from utils.platform_rulebook import resolve_platform_alias
        self.assertEqual(resolve_platform_alias("纵横中文"), "")
        self.assertEqual(resolve_platform_alias(""), "")


class TestPlatformRulebookLoad(unittest.TestCase):
    """6 个 rulebook 都存在,加载非空 + 包含核心 section 标题."""

    def _load(self, name):
        from utils.platform_rulebook import load_platform_rulebook
        return load_platform_rulebook(name)

    def test_qidian_loads(self):
        text = self._load("起点")
        self.assertGreater(len(text), 500)
        self.assertIn("读者画像", text)

    def test_jjwxc_loads(self):
        text = self._load("晋江")
        self.assertGreater(len(text), 500)
        self.assertIn("感情", text)

    def test_fanqie_loads(self):
        text = self._load("番茄")
        self.assertGreater(len(text), 500)
        self.assertIn("爽点", text)

    def test_feilu_loads(self):
        text = self._load("飞卢")
        self.assertGreater(len(text), 500)

    def test_qqyuedu_loads(self):
        text = self._load("QQ阅读")
        self.assertGreater(len(text), 500)

    def test_zhangyue_loads(self):
        text = self._load("掌阅")
        self.assertGreater(len(text), 500)

    def test_unknown_platform_returns_empty(self):
        self.assertEqual(self._load("未知平台"), "")
        self.assertEqual(self._load(""), "")

    def test_list_supported_returns_six(self):
        from utils.platform_rulebook import list_supported_platforms
        bases = list_supported_platforms()
        self.assertEqual(set(bases),
                          {"qidian", "jjwxc", "fanqie", "feilu", "qqyuedu", "zhangyue"})


class TestFormatPlatformBlock(unittest.TestCase):
    def test_empty_rules_returns_empty(self):
        from utils.platform_rulebook import format_platform_block
        state = make_minimal_state()
        # 默认 platform_rules=""
        self.assertEqual(format_platform_block(state), "")

    def test_loaded_rules_returns_block(self):
        from utils.platform_rulebook import format_platform_block
        state = make_minimal_state()
        state.platform_rules = "# 测试规则\n喜欢: 爽文\n讨厌: 圣母"
        out = format_platform_block(state)
        self.assertIn("平台读者偏好", out)
        self.assertIn("测试规则", out)


class TestFlavorAdviceDataclass(unittest.TestCase):
    def test_construct_with_defaults(self):
        from persistence.state import FlavorAdvice
        a = FlavorAdvice(generated_at_chapter=5, target_range="下 1-3 章",
                          advice=["加反派出场", "推感情线"])
        self.assertEqual(a.reasoning, "")
        self.assertEqual(len(a.advice), 2)


class TestFlavorAdvisorHelpers(unittest.TestCase):
    def test_latest_advice_returns_recent(self):
        from agents.flavor_advisor import get_latest_advice_for_chapter
        from persistence.state import FlavorAdvice
        state = make_minimal_state()
        state.flavor_advices = [
            FlavorAdvice(generated_at_chapter=3, target_range="下 1-3 章",
                          advice=["旧建议"], reasoning=""),
            FlavorAdvice(generated_at_chapter=12, target_range="下 1-3 章",
                          advice=["新建议"], reasoning=""),
        ]
        a = get_latest_advice_for_chapter(state, chapter_index=13)
        self.assertIsNotNone(a)
        self.assertEqual(a.advice, ["新建议"])

    def test_latest_advice_expired_returns_none(self):
        """advice.generated_at + 3 < chapter_index → 过期跳过."""
        from agents.flavor_advisor import get_latest_advice_for_chapter
        from persistence.state import FlavorAdvice
        state = make_minimal_state()
        state.flavor_advices = [
            FlavorAdvice(generated_at_chapter=3, target_range="下 1-3 章",
                          advice=["过期了"], reasoning=""),
        ]
        # chapter 10 离 3 太远(generated_at + 3 = 6 < 10)
        a = get_latest_advice_for_chapter(state, chapter_index=10)
        self.assertIsNone(a)

    def test_format_empty_advice_returns_empty(self):
        from agents.flavor_advisor import format_advice_for_prompt
        from persistence.state import FlavorAdvice
        a = FlavorAdvice(generated_at_chapter=3, target_range="x", advice=[])
        self.assertEqual(format_advice_for_prompt(a), "")

    def test_format_advice_includes_items(self):
        from agents.flavor_advisor import format_advice_for_prompt
        from persistence.state import FlavorAdvice
        a = FlavorAdvice(
            generated_at_chapter=5, target_range="下 1-3 章",
            advice=["让反派 X 主动找主角", "推感情线进度"],
            reasoning="最近爽感分高",
        )
        out = format_advice_for_prompt(a)
        self.assertIn("调味建议", out)
        self.assertIn("让反派 X 主动找主角", out)
        self.assertIn("推感情线进度", out)
        self.assertIn("最近爽感分高", out)

    def test_generate_advice_skips_when_chapters_insufficient(self):
        """章节不够 lookback 时直接返回 None,不发 LLM."""
        from agents.flavor_advisor import generate_advice
        state = make_minimal_state()
        # 没有 completed_chapters
        self.assertIsNone(generate_advice(state, chapter_index=2))


class TestCheckpointBackwardCompat(unittest.TestCase):
    def test_load_flavor_advice_with_data(self):
        from persistence.checkpoint import _load_flavor_advice
        a = _load_flavor_advice({
            "generated_at_chapter": 5,
            "target_range": "下 1-3 章",
            "advice": ["a1", "a2"],
            "reasoning": "test",
        })
        self.assertEqual(a.generated_at_chapter, 5)
        self.assertEqual(a.advice, ["a1", "a2"])

    def test_load_flavor_advice_with_missing_fields(self):
        from persistence.checkpoint import _load_flavor_advice
        a = _load_flavor_advice({})
        self.assertEqual(a.generated_at_chapter, 0)
        self.assertEqual(a.advice, [])

    def test_novelstate_default_platform_rules_empty(self):
        state = make_minimal_state()
        self.assertEqual(state.platform_rules, "")
        self.assertEqual(state.flavor_advices, [])


class TestPromptsRegistryEntry(unittest.TestCase):
    def test_flavor_advisor_registered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertIn("agents.flavor_advisor:SYSTEM", ids)


if __name__ == "__main__":
    unittest.main()
