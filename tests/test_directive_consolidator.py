"""tests for agents.directive_consolidator.

验证：
- 优先级链选 core_goal 正确（灵感>反馈>爽点>反转>伏笔>must_include>primary_line）
- P0/P1 字段分类正确
- 风格指针抓 tension/rhythm/word_pace 等
- 冲突检测能识别"节奏缓 + 爽点触发"等典型冲突
- source_hint_count 准确
- to_prompt_block 在无内容时返回空串、有内容时含关键标记
- 失败兜底不抛异常
"""
from __future__ import annotations
import unittest

from tests._helpers import make_minimal_state
from persistence.state import (
    ChapterDirective, TensionLevel, RhythmType, SatisfactionPoint,
    SatisfactionType, ForeshadowItem, ForeshadowImportance,
)
from agents.directive_consolidator import consolidate, ConsolidatedBrief


def _make_directive(**overrides) -> ChapterDirective:
    """构造一个最小 ChapterDirective。"""
    base = dict(
        chapter_index=10,
        volume_index=1,
        tension=TensionLevel.RISING,
        rhythm=RhythmType.SLOW_BUILD,
        active_lines=["main_line"],
        primary_line="main_line",
        must_include=["推进 A 事件"],
        satisfaction_points=[],
        foreshadow_plant=[],
        foreshadow_resolve=[],
        emotional_note="紧张",
        chapter_position="卷中",
        word_pace="中等",
    )
    base.update(overrides)
    return ChapterDirective(**base)


def _make_sp(sp_id: str, title: str, payoff: str) -> SatisfactionPoint:
    return SatisfactionPoint(
        sp_id=sp_id, sp_type=SatisfactionType.SLAP_FACE, title=title,
        description=payoff, intensity=8, volume=1, target_chapter=10,
        setup_chain=[], payoff_description=payoff,
    )


def _make_fw(fw_id: str, content: str, resolution: str) -> ForeshadowItem:
    return ForeshadowItem(
        fw_id=fw_id, content=content, hidden_meaning="",
        importance=ForeshadowImportance.MAJOR,
        planted_chapter=3, planned_resolve_volume=1,
        planned_resolve_chapter=10, resolution_description=resolution,
    )


class TestCoreGoalPriority(unittest.TestCase):
    """北极星优先级链 —— 灵感>反馈>爽点>反转>伏笔>must_include>primary_line。"""

    def test_user_inspiration_wins_all(self):
        state = make_minimal_state()
        d = _make_directive(
            user_inspiration="作者要写一场雨夜对峙",
            user_feedback="上版节奏太慢",
            satisfaction_points=["sp1"],
            twist_reveals=["chain1:2"],
            foreshadow_resolve=["fw1"],
        )
        brief = consolidate(d, state)
        self.assertIn("作者灵感", brief.core_goal)
        self.assertIn("雨夜对峙", brief.core_goal)

    def test_user_feedback_wins_when_no_inspiration(self):
        state = make_minimal_state()
        d = _make_directive(
            user_feedback="节奏太慢,加冲突",
            satisfaction_points=["sp1"],
        )
        brief = consolidate(d, state)
        self.assertIn("反馈重写", brief.core_goal)

    def test_satisfaction_point_wins_over_twist(self):
        state = make_minimal_state()
        state.satisfaction_points = [_make_sp("sp1", "打脸大反派", "主角当众揭穿反派假面")]
        d = _make_directive(
            satisfaction_points=["sp1"],
            twist_reveals=["chain1:2"],
        )
        brief = consolidate(d, state)
        self.assertIn("打脸大反派", brief.core_goal)
        self.assertIn("揭穿", brief.core_goal)

    def test_twist_wins_when_no_sp(self):
        state = make_minimal_state()
        d = _make_directive(twist_reveals=["chain1:2"])
        brief = consolidate(d, state)
        self.assertIn("反转", brief.core_goal)
        self.assertIn("chain1:2", brief.core_goal)

    def test_foreshadow_resolve_wins_when_no_twist(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", "某物来历不明", "揭示是反派故意送的")]
        d = _make_directive(foreshadow_resolve=["fw1"])
        brief = consolidate(d, state)
        self.assertIn("兑现伏笔", brief.core_goal)
        self.assertIn("反派故意送的", brief.core_goal)

    def test_must_include_fallback(self):
        state = make_minimal_state()
        d = _make_directive(must_include=["主角去酒馆探听消息"])
        brief = consolidate(d, state)
        self.assertIn("推进主线", brief.core_goal)
        self.assertIn("酒馆", brief.core_goal)

    def test_primary_line_last_resort(self):
        state = make_minimal_state()
        d = _make_directive(must_include=[], primary_line="主角成长线")
        brief = consolidate(d, state)
        self.assertIn("主角成长线", brief.core_goal)


class TestP0Must(unittest.TestCase):
    def test_inspiration_in_p0(self):
        state = make_minimal_state()
        d = _make_directive(user_inspiration="本章必须下大雨")
        brief = consolidate(d, state)
        self.assertTrue(any("作者灵感" in s for s in brief.p0_must))

    def test_callback_seeds_in_p0(self):
        state = make_minimal_state()
        d = _make_directive(callback_seeds=["[humiliation·第3章·张三] 「废物」 — 当众嘲讽"])
        brief = consolidate(d, state)
        self.assertTrue(any("callback" in s.lower() or "锚点" in s for s in brief.p0_must))

    def test_twist_in_p0(self):
        state = make_minimal_state()
        d = _make_directive(twist_reveals=["chain1:2", "chain2:1"])
        brief = consolidate(d, state)
        p0_text = "\n".join(brief.p0_must)
        self.assertIn("chain1:2", p0_text)
        self.assertIn("chain2:1", p0_text)

    def test_character_states_in_p0(self):
        state = make_minimal_state()
        d = _make_directive(
            character_states={
                "主角": {"location": "京都", "injury": "断臂", "realm": "三层"},
            },
        )
        brief = consolidate(d, state)
        p0_text = "\n".join(brief.p0_must)
        self.assertIn("京都", p0_text)
        self.assertIn("断臂", p0_text)


class TestP1Should(unittest.TestCase):
    def test_structure_role_in_p1(self):
        state = make_minimal_state()
        d = _make_directive(structure_role="承", purpose="承接上章遇袭", expression="主角的怒")
        brief = consolidate(d, state)
        p1_text = "\n".join(brief.p1_should)
        self.assertIn("承", p1_text)
        self.assertIn("遇袭", p1_text)
        self.assertIn("怒", p1_text)

    def test_foreshadow_plant_in_p1(self):
        state = make_minimal_state()
        d = _make_directive(foreshadow_plant=["fw_x", "fw_y"])
        brief = consolidate(d, state)
        p1_text = "\n".join(brief.p1_should)
        self.assertIn("fw_x", p1_text)
        self.assertIn("fw_y", p1_text)


class TestP2Style(unittest.TestCase):
    def test_tension_rhythm_in_style(self):
        state = make_minimal_state()
        d = _make_directive(
            tension=TensionLevel.PEAK,
            rhythm=RhythmType.FAST_ACTION,
            word_pace="快",
            chapter_type="战斗章",
        )
        brief = consolidate(d, state)
        self.assertEqual(brief.p2_style.get("张力"), TensionLevel.PEAK.value)
        self.assertEqual(brief.p2_style.get("节奏"), RhythmType.FAST_ACTION.value)
        self.assertEqual(brief.p2_style.get("语速"), "快")
        self.assertEqual(brief.p2_style.get("章型"), "战斗章")


class TestConflictResolution(unittest.TestCase):
    def test_slow_rhythm_vs_satisfaction_conflict_logged(self):
        state = make_minimal_state()
        d = _make_directive(
            rhythm=RhythmType.SLOW_BUILD,
            satisfaction_points=["sp1"],
        )
        brief = consolidate(d, state)
        joined = "\n".join(brief.conflicts_log)
        self.assertTrue(any("爽点" in c for c in brief.conflicts_log),
                        f"应识别节奏 vs 爽点冲突: {brief.conflicts_log}")

    def test_inspiration_plus_feedback_conflict_logged(self):
        state = make_minimal_state()
        d = _make_directive(
            user_inspiration="大雨夜",
            user_feedback="节奏太慢",
        )
        brief = consolidate(d, state)
        self.assertTrue(any("反馈" in c and "灵感" in c for c in brief.conflicts_log))

    def test_sp_plus_twist_orders_dramatic_sequence(self):
        state = make_minimal_state()
        d = _make_directive(
            satisfaction_points=["sp1"],
            twist_reveals=["chain1:2"],
        )
        brief = consolidate(d, state)
        self.assertTrue(any("反转" in c and "爽点" in c for c in brief.conflicts_log))

    def test_no_conflict_no_log(self):
        state = make_minimal_state()
        d = _make_directive()  # 平凡 directive
        brief = consolidate(d, state)
        self.assertEqual(brief.conflicts_log, [])


class TestHintCount(unittest.TestCase):
    def test_counts_all_hint_sources(self):
        state = make_minimal_state()
        d = _make_directive(
            must_include=["a", "b"],
            forbidden_content=["x"],
            callback_seeds=["seed1"],
            twist_reveals=["t1"],
            foreshadow_plant=["fw_a"],
            satisfaction_points=["sp1"],
            user_inspiration="灵感",
        )
        brief = consolidate(d, state)
        # 2(must)+1(forb)+1(seed)+1(twist)+1(fwplant)+1(sp)+1(insp)=8
        self.assertEqual(brief.source_hint_count, 8)


class TestPromptBlock(unittest.TestCase):
    def test_empty_brief_returns_empty_string(self):
        state = make_minimal_state()
        d = _make_directive(must_include=[], primary_line="")
        brief = consolidate(d, state)
        # 没 must_include 没 primary_line —— core_goal 走兜底文本
        # 但 p0_must 仍然空，所以 to_prompt_block 不为空（至少含 core_goal）
        block = brief.to_prompt_block()
        # 既不应崩,也至少含 北极星 行
        self.assertIn("北极星", block)

    def test_truly_empty_returns_empty(self):
        # 直接 new 一个空 brief
        brief = ConsolidatedBrief()
        self.assertEqual(brief.to_prompt_block(), "")

    def test_block_contains_key_sections(self):
        state = make_minimal_state()
        d = _make_directive(
            user_inspiration="大雨",
            must_include=["主线 A"],
            forbidden_content=["不得提及王朝"],
            reader_expectations=[],
        )
        brief = consolidate(d, state)
        block = brief.to_prompt_block()
        self.assertIn("北极星", block)
        self.assertIn("P0 硬约束", block)
        self.assertIn("P3 禁忌", block)
        self.assertIn("不得提及王朝", block)


class TestFailureSafety(unittest.TestCase):
    def test_none_fields_dont_crash(self):
        """ChapterDirective 各种字段为 None 时不应崩。"""
        state = make_minimal_state()
        d = _make_directive()
        # 强行把列表字段设成 None（模拟坏数据）
        d.must_include = None
        d.foreshadow_plant = None
        d.callback_seeds = None
        # consolidate 必须不抛
        brief = consolidate(d, state)
        self.assertIsInstance(brief, ConsolidatedBrief)

    def test_to_dict_roundtrip(self):
        state = make_minimal_state()
        d = _make_directive(must_include=["A"], user_inspiration="X")
        brief = consolidate(d, state)
        dct = brief.to_dict()
        self.assertEqual(dct["chapter_index"], 10)
        self.assertIn("A", " ".join(dct["p0_must"]))


if __name__ == "__main__":
    unittest.main()
