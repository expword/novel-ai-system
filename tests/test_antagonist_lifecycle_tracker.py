"""tests for agents.antagonist_lifecycle_tracker."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from dataclasses import dataclass, field

from tests._helpers import make_minimal_state
from persistence.state import Character, CharacterRole, ChapterSummary, TensionLevel
from agents import antagonist_lifecycle_tracker as alt
from agents.antagonist_lifecycle_tracker import (
    AntagonistLifecycle, LifecycleNode,
    INTRO_TO_FIRST_CONFLICT_MAX_GAP, INTRO_TO_DEFEAT_MIN_GAP,
)


def _make_char(name: str, role: str = "反派", **kw):
    base = dict(
        name=name, role=CharacterRole(role),
        gender="", age_desc="", appearance="", personality="",
        personality_detail="", background="", trauma="", desire="",
        fear="", speech_pattern="", ability="", realm="",
        arc="", motivation="", fatal_flaw="",
        first_volume=1, last_volume=-1,
    )
    base.update(kw)
    return Character(**base)


def _make_summary(idx: int, vol: int = 1, summary_text: str = ""):
    return ChapterSummary(
        index=idx, volume_index=vol, title=f"第{idx}章",
        summary=summary_text, word_count=3000,
        tension=TensionLevel.RISING,
    )


def _make_lifecycle_dict(name: str, with_triggers: dict = None):
    """构造 lifecycle dict (state.antagonist_lifecycles 的元素格式)。"""
    triggers = with_triggers or {}
    nodes = []
    for k, label, desc in alt.LIFECYCLE_NODES:
        t = triggers.get(k, {})
        nodes.append({
            "key": k, "label": label, "description": desc,
            "planned_chapter": t.get("planned", -1),
            "triggered": t.get("triggered", False),
            "actual_chapter": t.get("actual", -1),
        })
    return {
        "antagonist_name": name,
        "motivation_brief": "",
        "threat_level": "全书",
        "nodes": nodes,
    }


class TestDesignLifecycle(unittest.TestCase):
    def test_returns_none_if_character_missing(self):
        state = make_minimal_state()
        result = alt.design_lifecycle(state, "不存在的反派")
        self.assertIsNone(result)

    def test_designs_6_nodes_from_llm(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [_make_char("反派甲")]
        fake = {
            "motivation_brief": "复仇",
            "threat_level": "全书",
            "nodes": [
                {"key": "introduction", "description": "出场", "planned_chapter": 5},
                {"key": "first_conflict", "description": "冲突", "planned_chapter": 12},
                {"key": "true_threat_revealed", "description": "威胁", "planned_chapter": 20},
                {"key": "escalation", "description": "升级", "planned_chapter": 35},
                {"key": "final_confrontation", "description": "决战", "planned_chapter": 60},
                {"key": "defeat_or_redemption", "description": "败北", "planned_chapter": 62},
            ],
        }
        with patch.object(alt, "request_json_with_profile", return_value=fake):
            result = alt.design_lifecycle(state, "反派甲")
        self.assertIsNotNone(result)
        self.assertEqual(len(result.nodes), 6)
        self.assertEqual(result.get_node("introduction").planned_chapter, 5)
        self.assertEqual(result.get_node("defeat_or_redemption").planned_chapter, 62)

    def test_llm_failure_returns_none(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [_make_char("反派甲")]
        with patch.object(alt, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            result = alt.design_lifecycle(state, "反派甲")
        self.assertIsNone(result)


class TestDesignAllIfNeeded(unittest.TestCase):
    def test_skips_already_designed(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [_make_char("反派甲")]
        state.antagonist_lifecycles = {"反派甲": _make_lifecycle_dict("反派甲")}
        with patch.object(alt, "request_json_with_profile") as mock:
            count = alt.design_all_if_needed(state)
        self.assertEqual(count, 0)
        mock.assert_not_called()

    def test_only_designs_antagonists(self):
        state = make_minimal_state()
        # 加 1 个反派 + 1 个非反派
        state.characters = list(state.characters) + [
            _make_char("反派甲", "反派"),
            _make_char("配角乙", "主要配角"),
        ]
        state.antagonist_lifecycles = {}
        fake = {
            "motivation_brief": "X",
            "threat_level": "卷级",
            "nodes": [
                {"key": k, "description": "d", "planned_chapter": i * 5}
                for i, (k, _, _) in enumerate(alt.LIFECYCLE_NODES, 1)
            ],
        }
        with patch.object(alt, "request_json_with_profile", return_value=fake):
            count = alt.design_all_if_needed(state)
        self.assertEqual(count, 1)  # 只设计了反派甲
        self.assertIn("反派甲", state.antagonist_lifecycles)
        self.assertNotIn("配角乙", state.antagonist_lifecycles)


class TestTrackAfterChapter(unittest.TestCase):
    def test_no_lifecycles_returns_empty(self):
        state = make_minimal_state()
        result = alt.track_after_chapter(state, 5, "")
        self.assertEqual(result["triggered_count"], 0)

    def test_antagonist_appearance_triggers_first_unfilled_node(self):
        state = make_minimal_state()
        state.antagonist_lifecycles = {"反派甲": _make_lifecycle_dict("反派甲")}
        state.completed_chapters = [
            _make_summary(5, 1, summary_text="反派甲突然出现在城门口"),
        ]
        result = alt.track_after_chapter(state, 5)
        self.assertEqual(result["triggered_count"], 1)
        lc = state.antagonist_lifecycles["反派甲"]
        intro = lc["nodes"][0]
        self.assertTrue(intro["triggered"])
        self.assertEqual(intro["actual_chapter"], 5)

    def test_neglect_warning_when_too_long_no_first_conflict(self):
        state = make_minimal_state()
        # introduction 已 trigger 在第 3 章,本章 第 20 章,反派未出现
        state.antagonist_lifecycles = {
            "反派甲": _make_lifecycle_dict("反派甲",
                with_triggers={"introduction": {"triggered": True, "actual": 3}}
            ),
        }
        # 第 20 章 summary 不提反派
        state.completed_chapters = [_make_summary(20, 1, summary_text="主角独自修炼")]
        result = alt.track_after_chapter(state, 20)
        # gap = 20 - 3 = 17 > INTRO_TO_FIRST_CONFLICT_MAX_GAP(8)
        self.assertTrue(any(w["issue"] == "搁置" for w in result["missing_warnings"]),
                        f"应识别搁置: {result['missing_warnings']}")

    def test_no_neglect_when_within_gap(self):
        state = make_minimal_state()
        state.antagonist_lifecycles = {
            "反派甲": _make_lifecycle_dict("反派甲",
                with_triggers={"introduction": {"triggered": True, "actual": 18}}
            ),
        }
        state.completed_chapters = [_make_summary(20, 1, summary_text="主角独自修炼")]
        result = alt.track_after_chapter(state, 20)
        # gap = 20 - 18 = 2 ≤ 8 → no warning
        self.assertEqual(result["missing_warnings"], [])


class TestSurfaceBehavior(unittest.TestCase):
    def test_short_antagonist_name_not_matched(self):
        state = make_minimal_state()
        # 单字反派名 —— 不会被匹配(haystack 检查 len < 2 跳过)
        state.antagonist_lifecycles = {"X": _make_lifecycle_dict("X")}
        state.completed_chapters = [_make_summary(5, 1, summary_text="X 出场")]
        result = alt.track_after_chapter(state, 5)
        # 单字名 X 被 len 检查跳过,所以本章没 trigger
        self.assertEqual(result["triggered_count"], 0)


if __name__ == "__main__":
    unittest.main()
