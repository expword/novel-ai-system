"""叙事真相引擎测试 —— 用「乙方修仙」叙事圣经验证一致性硬约束。

证明引擎能守护:反转有铺垫(防凭空)、伏笔全收(防烂尾)、事实多层自洽、角色秘密对齐。
"""
import unittest

from novel_v2.examples.delegation_narrative import build_bible


class TestNarrativeBible(unittest.TestCase):
    def setUp(self):
        self.bible = build_bible()

    def test_design_is_self_consistent(self):
        # 整套设计(全书写完)应无瑕疵
        self.assertEqual(self.bible.plan_design_audit(), [])

    def test_truth_stack_basics(self):
        self.assertEqual(self.bible.truth.final().level, 3)
        self.assertEqual(self.bible.truth.reader_layer_at(50), 1)   # 第50章读者认知到第1层
        self.assertEqual(self.bible.truth.reader_layer_at(100), 2)

    def test_catch_groundless_twist(self):
        # 把第3层的伏笔改成"揭晓后才埋" → 凭空反转
        self.bible.foreshadow.items["fs_chenmo"].planted_at = 200
        issues = self.bible.plan_design_audit()
        self.assertTrue(any("凭空" in i for i in issues))

    def test_catch_unpaid_foreshadow(self):
        # 第1层伏笔不回收 → 第1层揭晓后(第30章)报烂尾
        self.bible.foreshadow.items["fs_guide"].paid_at = 0
        issues = self.bible.audit(40)
        self.assertTrue(any("烂尾" in i for i in issues))

    def test_catch_fact_unknown_layer(self):
        from novel_v2.narrative import ReframableFact
        self.bible.facts.add(ReframableFact("f_bad", "x", {9: "不存在的层"}, 1))
        self.assertTrue(any("无此层" in i for i in self.bible.plan_design_audit()))

    def test_catch_soul_layer_mismatch(self):
        # 角色秘密揭晓章与其绑定真相层的揭晓章不一致
        self.bible.souls.souls["张铁山"].secret_revealed_at = 70   # 应为 80
        self.assertTrue(any("不一致" in i for i in self.bible.plan_design_audit()))

    def test_reveal_plan_drives_writing(self):
        # 第80章:揭第2层真相 + 回收 fs_prevhost + 揭张铁山秘密 + reframe f_dingjin
        plan = self.bible.reveal_plan_at(80)
        self.assertEqual(plan["reveal_layers"][0]["level"], 2)
        self.assertIn("fs_prevhost", plan["pay_foreshadows"])
        self.assertIn("张铁山", plan["reveal_souls"])
        self.assertIn("f_dingjin", plan["reframe_facts"])

    def test_no_reveal_on_ordinary_chapter(self):
        plan = self.bible.reveal_plan_at(45)   # 非揭晓章
        self.assertEqual(plan["reveal_layers"], [])


if __name__ == "__main__":
    unittest.main()
