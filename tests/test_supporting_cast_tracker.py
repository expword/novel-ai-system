"""tests for agents.supporting_cast_tracker."""
from __future__ import annotations
import unittest
from dataclasses import dataclass, field

from tests._helpers import make_minimal_state
from persistence.state import Character, CharacterRole, ChapterSummary, TensionLevel
from agents import supporting_cast_tracker as sct


def _make_char(name: str, role: str):
    return Character(
        name=name, role=CharacterRole(role),
        gender="", age_desc="", appearance="", personality="",
        personality_detail="", background="", trauma="", desire="",
        fear="", speech_pattern="", ability="", realm="",
        arc="", motivation="", fatal_flaw="",
        first_volume=1, last_volume=-1,
    )


def _make_summary(idx: int, vol: int, summary_text: str = "", key_events: list = None):
    return ChapterSummary(
        index=idx, volume_index=vol, title=f"第{idx}章",
        summary=summary_text, word_count=3000,
        tension=TensionLevel.RISING,
        key_events=key_events or [],
    )


class TestSingleChapterUpdate(unittest.TestCase):
    def test_records_character_appearance(self):
        state = make_minimal_state()
        state.characters = [
            _make_char("李慕白", "主要配角"),
            _make_char("张三", "反派"),
        ]
        state.completed_chapters = [
            _make_summary(5, 1, summary_text="李慕白拔剑而出,张三跌坐在地。"),
        ]
        result = sct.update_after_chapter(state, 5, 1)
        self.assertEqual(result["updated_count"], 2)
        self.assertIn("李慕白", state.supporting_cast_stats)
        self.assertEqual(state.supporting_cast_stats["李慕白"]["appear_count"], 1)
        self.assertEqual(state.supporting_cast_stats["李慕白"]["last_seen_chapter"], 5)

    def test_skips_unmentioned_chars(self):
        state = make_minimal_state()
        state.characters = [
            _make_char("李慕白", "主要配角"),
            _make_char("张三", "反派"),
        ]
        state.completed_chapters = [
            _make_summary(5, 1, summary_text="李慕白拔剑而出。"),  # 张三没提
        ]
        sct.update_after_chapter(state, 5, 1)
        self.assertIn("李慕白", state.supporting_cast_stats)
        self.assertNotIn("张三", state.supporting_cast_stats)

    def test_dedup_same_chapter(self):
        state = make_minimal_state()
        state.characters = [_make_char("李慕白", "主要配角")]
        state.completed_chapters = [
            _make_summary(5, 1, summary_text="李慕白和李慕白对峙,李慕白怒。"),
        ]
        sct.update_after_chapter(state, 5, 1)
        # 同一章不论提多少次,只 +1
        self.assertEqual(state.supporting_cast_stats["李慕白"]["appear_count"], 1)

    def test_uses_key_events_text(self):
        state = make_minimal_state()
        state.characters = [_make_char("李慕白", "主要配角")]
        state.completed_chapters = [
            _make_summary(5, 1, summary_text="某场景", key_events=["李慕白出现"]),
        ]
        sct.update_after_chapter(state, 5, 1)
        self.assertIn("李慕白", state.supporting_cast_stats)

    def test_skips_too_short_names(self):
        state = make_minimal_state()
        state.characters = [_make_char("X", "主要配角")]  # 单字名
        state.completed_chapters = [_make_summary(5, 1, summary_text="X 出现")]
        sct.update_after_chapter(state, 5, 1)
        # 单字名跳过(避免误命中)
        self.assertNotIn("X", state.supporting_cast_stats)


class TestMissingThreshold(unittest.TestCase):
    def test_major_supporting_missing_too_long_triggers(self):
        state = make_minimal_state()
        state.characters = [_make_char("李慕白", "主要配角")]
        # 早期出场 3 次,然后消失很久
        state.completed_chapters = [
            _make_summary(1, 1, summary_text="李慕白出现"),
            _make_summary(2, 1, summary_text="李慕白说话"),
            _make_summary(3, 1, summary_text="李慕白离开"),
            # 第 4-19 章不再出场
            _make_summary(20, 1, summary_text="其他剧情"),
        ]
        # 跑前 3 章,累计 appear_count=3
        for i in [1, 2, 3]:
            sct.update_after_chapter(state, i, 1)
        # 第 20 章(% 5 == 0)触发检查
        result = sct.update_after_chapter(state, 20, 1)
        # gap = 20 - 3 = 17 > MISSING_THRESHOLD_MAJOR(10) → 应告警
        self.assertTrue(any(m["name"] == "李慕白" for m in result["missing_majors"]),
                        f"应识别李慕白失踪: {result['missing_majors']}")

    def test_check_only_every_5_chapters(self):
        state = make_minimal_state()
        state.characters = [_make_char("李慕白", "主要配角")]
        state.completed_chapters = [
            _make_summary(1, 1, summary_text="李慕白"),
            _make_summary(2, 1, summary_text="李慕白"),
            _make_summary(3, 1, summary_text="李慕白"),
            _make_summary(18, 1, summary_text="其他"),
        ]
        for i in [1, 2, 3]:
            sct.update_after_chapter(state, i, 1)
        # 第 18 章(% 5 != 0)不应触发检查
        result = sct.update_after_chapter(state, 18, 1)
        self.assertEqual(result["missing_majors"], [])


class TestHogWarning(unittest.TestCase):
    def test_supporting_hog_detected(self):
        state = make_minimal_state()
        state.characters = [
            _make_char("李慕白", "主要配角"),
        ]
        # 本卷 10 章,李慕白出场 5 章 = 50% > 30%
        state.completed_chapters = []
        for i in range(1, 11):
            text = "李慕白出现" if i <= 5 else "其他剧情"
            state.completed_chapters.append(_make_summary(i, 1, summary_text=text))
        for i in range(1, 11):
            sct.update_after_chapter(state, i, 1)
        # 第 10 章(% 5 == 0)触发
        result = sct.update_after_chapter(state, 10, 1)
        self.assertTrue(any(h["name"] == "李慕白" for h in result["hog_warnings"]),
                        f"应识别李慕白抢戏: {result['hog_warnings']}")

    def test_protagonist_excluded_from_hog(self):
        state = make_minimal_state()
        state.characters = [
            _make_char("主角甲", "主角"),
        ]
        for i in range(1, 11):
            state.completed_chapters = []
            for j in range(1, i + 1):
                state.completed_chapters.append(_make_summary(j, 1, summary_text="主角甲"))
            sct.update_after_chapter(state, i, 1)
        result = sct.update_after_chapter(state, 10, 1)
        # 主角永远不算抢戏
        self.assertEqual(result["hog_warnings"], [])


class TestGetCastStatsSummary(unittest.TestCase):
    def test_returns_sorted_top_n(self):
        state = make_minimal_state()
        state.supporting_cast_stats = {
            "A": {"name": "A", "appear_count": 5},
            "B": {"name": "B", "appear_count": 12},
            "C": {"name": "C", "appear_count": 3},
        }
        top = sct.get_cast_stats_summary(state, top_n=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["name"], "B")
        self.assertEqual(top[1]["name"], "A")


if __name__ == "__main__":
    unittest.main()
