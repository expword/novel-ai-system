"""chapter_asset_tracker 回归测试。

覆盖：
  · scan_chapter_for_asset_candidates 抓包装符号 / 能力后缀
  · 已登记 asset / canon 已定义术语不报为候选
  · update_asset_candidates 跨章累计 + 阈值 promote
  · list_pending_candidates 过滤 min_chapters
"""
import unittest
from tests._helpers import make_minimal_state


class TestScan(unittest.TestCase):
    def setUp(self):
        from agents.chapter_asset_tracker import scan_chapter_for_asset_candidates
        self.scan = scan_chapter_for_asset_candidates

    def test_bracket_terms_caught(self):
        s = make_minimal_state()
        c = self.scan(s, 1, "他启动了《破天经》和【系统面板】")
        self.assertIn("破天经", c)
        self.assertIn("系统面板", c)

    def test_ability_suffixes_caught(self):
        s = make_minimal_state()
        c = self.scan(s, 1, "突然脑中浮现一道凌云诀和九天玄典")
        # 规则是 conservative + greedy——"一道凌云" 可能被吃，但核心 token "凌云" 应在候选某条里
        # （比精确字符串更稳健的测试断言）
        self.assertTrue(any("凌云" in t for t in c), f"凌云核心 token 应该出现，实际 {c}")
        self.assertTrue(any("九天" in t for t in c), f"九天核心 token 应该出现，实际 {c}")

    def test_known_asset_not_reported(self):
        s = make_minimal_state(real_ai_asset=True, asset_name="豆包")
        c = self.scan(s, 1, "他启动了《豆包》系统")
        # 豆包已登记，不算新候选
        self.assertNotIn("豆包", c)

    def test_stop_words_filtered(self):
        from agents.chapter_asset_tracker import _STOP_BRACKET_TERMS
        s = make_minimal_state()
        c = self.scan(s, 1, "屏幕弹出【系统提示】【确认】【弹窗】")
        for stop in ("系统提示", "确认", "弹窗"):
            self.assertNotIn(stop, c, f"应过滤 stop word: {stop}")


class TestAccumulation(unittest.TestCase):
    def test_promote_after_threshold(self):
        from agents.chapter_asset_tracker import update_asset_candidates
        s = make_minimal_state()
        # 同一 asset 连续 3 章出现
        for ch in (1, 2, 3):
            r = update_asset_candidates(
                s, ch, "他启动了《破天经》",
                persist_threshold=3,
            )
        self.assertIn("破天经", r["promoted_for_review"])
        # 第 4 章再出现不重复 promote
        r4 = update_asset_candidates(s, 4, "《破天经》威力大增", persist_threshold=3)
        self.assertNotIn("破天经", r4["promoted_for_review"])

    def test_single_chapter_not_promoted(self):
        from agents.chapter_asset_tracker import update_asset_candidates
        s = make_minimal_state()
        r = update_asset_candidates(s, 1, "《修辞性表达》",
                                       persist_threshold=3)
        self.assertEqual(r["promoted_for_review"], [])

    def test_pending_list_filters(self):
        from agents.chapter_asset_tracker import (
            update_asset_candidates, list_pending_candidates,
        )
        s = make_minimal_state()
        update_asset_candidates(s, 1, "《单次出现》《重复出现》")
        update_asset_candidates(s, 2, "《重复出现》")
        pending = list_pending_candidates(s, min_chapters=2)
        terms = [p["term"] for p in pending]
        self.assertIn("重复出现", terms)
        self.assertNotIn("单次出现", terms)


if __name__ == "__main__":
    unittest.main()
