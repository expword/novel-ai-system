"""user_models in_story_ai usage 回归测试。

覆盖：
  · USAGE_BUILTIN 包含 in_story_ai
  · all_usages 返回包含 in_story_ai
  · list_in_story_ai_profiles 过滤逻辑
  · usage_descriptions 字典完整
"""
import unittest
from tests._helpers import make_minimal_state  # noqa
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestUsageBuiltin(unittest.TestCase):
    def test_in_story_ai_in_builtin(self):
        from llm_layer.user_models import USAGE_BUILTIN
        self.assertIn("in_story_ai", USAGE_BUILTIN)
        # 文案非空
        self.assertTrue(USAGE_BUILTIN["in_story_ai"])

    def test_extractor_in_builtin(self):
        from llm_layer.user_models import USAGE_BUILTIN
        self.assertIn("extractor", USAGE_BUILTIN)
        self.assertTrue(USAGE_BUILTIN["extractor"])

    def test_planner_in_builtin(self):
        from llm_layer.user_models import USAGE_BUILTIN, UNIQUE_USAGES
        self.assertIn("planner", USAGE_BUILTIN)
        self.assertIn("planner", UNIQUE_USAGES)  # planner 是互斥的

    def test_all_usages_includes_new_ones(self):
        from llm_layer.user_models import all_usages
        all_u = all_usages()
        self.assertIn("in_story_ai", all_u)
        self.assertIn("extractor", all_u)
        self.assertIn("planner", all_u)

    def test_usage_descriptions_has_all_builtins(self):
        from llm_layer.user_models import usage_descriptions, USAGE_BUILTIN
        d = usage_descriptions()
        for k in USAGE_BUILTIN:
            self.assertIn(k, d)
            self.assertTrue(d[k])


class TestListInStoryAIFiltering(unittest.TestCase):
    """list_in_story_ai_profiles 只返回 usage 含 in_story_ai 的 profile。"""

    def test_filter_logic_directly(self):
        # 不动磁盘——直接测过滤逻辑
        from llm_layer.user_models import list_in_story_ai_profiles, list_all
        all_models = list_all()
        in_story = list_in_story_ai_profiles()
        # 子集关系
        all_ids = {m["id"] for m in all_models}
        in_story_ids = {m["id"] for m in in_story}
        self.assertTrue(in_story_ids.issubset(all_ids))
        # 每条都含 in_story_ai
        for m in in_story:
            self.assertIn("in_story_ai", m.get("usage", []),
                          f"{m['id']} 没勾 in_story_ai 却被返回")


if __name__ == "__main__":
    unittest.main()
