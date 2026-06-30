"""系统引擎测试 —— 用「委托系统」配置验证通用引擎永远算得对。

证明:同一引擎,灌进委托系统配置,各种正常/越界操作都被正确处理。
"""
import unittest

from novel_v2.examples.delegation_system import build_spec
from novel_v2.system import Change, EventLog, PlayerState, Proposal, SystemEngine


class TestSystemEngine(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec()
        self.engine = SystemEngine(self.spec)
        self.s0 = PlayerState.initial(self.spec)   # 市井, 信誉0 灵石0

    def test_initial_state(self):
        self.assertEqual(self.s0.tier, "市井")
        self.assertEqual(self.s0.resources["信誉"], 0)

    def test_complete_delegation_credits(self):
        p = Proposal(1, [Change("complete_quest", "u_blacksmith"),
                         Change("gain_resource", "信誉", 30),
                         Change("gain_resource", "灵石", 10),
                         Change("reward", "草药", 1)])
        r = self.engine.apply(self.s0, p)
        self.assertTrue(r.ok, r.violations)
        self.assertEqual(r.state.resources["信誉"], 30)
        self.assertIn("草药", r.state.items)
        self.assertIn("u_blacksmith", r.state.quests_done)

    def test_overpay_reward_blocked(self):
        # 市井阶层发了「仙器」(仙神奖励) -> 超发拦截
        r = self.engine.apply(self.s0, Proposal(1, [Change("reward", "仙器", 1)]))
        self.assertFalse(r.ok)
        self.assertTrue(any("超发" in v for v in r.violations))

    def test_spend_over_balance_blocked(self):
        r = self.engine.apply(self.s0, Proposal(1, [Change("spend_resource", "灵石", 50)]))
        self.assertFalse(r.ok)
        # 守恒:被拦后状态不变
        self.assertEqual(r.state.resources["灵石"], 0)

    def test_advance_tier_requires_credit(self):
        # 信誉不够 -> 升不了
        r = self.engine.apply(self.s0, Proposal(2, [Change("advance_tier")]))
        self.assertFalse(r.ok)
        # 攒够信誉 + 过了豪门锚点(第50章) -> 可升
        s = PlayerState.initial(self.spec)
        s.resources["信誉"] = 120
        r2 = self.engine.apply(s, Proposal(60, [Change("advance_tier")]))
        self.assertTrue(r2.ok, r2.violations)
        self.assertEqual(r2.state.tier, "豪门")

    def test_growth_anchor_blocks_inflation(self):
        # 第2章就想升豪门(锚点第50章) -> 即便信誉够也拦(防通胀)
        s = PlayerState.initial(self.spec)
        s.resources["信誉"] = 999
        r = self.engine.apply(s, Proposal(2, [Change("advance_tier")]))
        self.assertFalse(r.ok)
        self.assertTrue(any("通胀" in v or "超前" in v for v in r.violations))

    def test_used_skill_must_be_learned(self):
        # 正文用了没学的技能 -> 双向校验拦截
        r = self.engine.apply(self.s0, Proposal(1, [], used_skills=["问诊术"]))
        self.assertFalse(r.ok)
        # 先学再用 -> 通过
        learned = self.engine.apply(self.s0, Proposal(1, [Change("learn_skill", "问诊术")]))
        r2 = self.engine.apply(learned.state, Proposal(2, [], used_skills=["问诊术"]))
        self.assertTrue(r2.ok, r2.violations)

    def test_learn_skill_not_in_pool(self):
        r = self.engine.apply(self.s0, Proposal(1, [Change("learn_skill", "御剑飞行")]))
        self.assertFalse(r.ok)

    def test_cooldown(self):
        p1 = Proposal(1, [Change("gain_resource", "信誉", 1)], cooldown_uses=["每日签到"])
        r1 = self.engine.apply(self.s0, p1)
        self.assertTrue(r1.ok)
        # 冷却内(第1章)再签 -> 拦
        r2 = self.engine.apply(r1.state, Proposal(1, [], cooldown_uses=["每日签到"]))
        self.assertFalse(r2.ok)
        # 隔一章再签 -> 过
        r3 = self.engine.apply(r1.state, Proposal(2, [], cooldown_uses=["每日签到"]))
        self.assertTrue(r3.ok, r3.violations)

    def test_event_log_rollback(self):
        log = EventLog()
        s = self.s0
        for ch in range(1, 4):
            r = self.engine.apply(s, Proposal(ch, [Change("gain_resource", "信誉", 10)]))
            log.commit(r.events)
            s = r.state
        self.assertEqual(s.resources["信誉"], 30)
        # 撤销第2、3章 -> 重放只剩第1章的 10
        log.rollback_after(1)
        restored = log.replay(self.spec)
        self.assertEqual(restored.resources["信誉"], 10)

    def test_spec_roundtrip(self):
        # 配置可序列化:规划期生成 -> 存盘 -> 读回
        from novel_v2.system import SystemSpec
        spec2 = SystemSpec.from_dict(self.spec.to_dict())
        self.assertEqual(spec2.tiers, self.spec.tiers)
        self.assertEqual(spec2.anchor_tier_at(60), "豪门")


if __name__ == "__main__":
    unittest.main()
