"""tests for agents.progress_dashboard."""
from __future__ import annotations
import unittest
from dataclasses import dataclass, field

from tests._helpers import make_minimal_state
from persistence.state import (
    ChapterSummary, TensionLevel, ForeshadowItem, ForeshadowImportance,
    SatisfactionPoint, SatisfactionType,
)
from agents import progress_dashboard as dash


def _make_summary(idx, vol=1, score=7, hook="suspense", tension=TensionLevel.RISING,
                  word_count=3000, quotables=None):
    s = ChapterSummary(
        index=idx, volume_index=vol, title=f"第{idx}章",
        summary="x", word_count=word_count, tension=tension,
        closing_hook_type=hook,
    )
    s.critic_review = {"score": score, "passed": True, "dim_scores": {"叙事": score, "张力": score}}
    s.quotable_moments = quotables or []
    return s


class TestCompute(unittest.TestCase):
    def test_returns_7_sections(self):
        state = make_minimal_state()
        result = dash.compute_dashboard(state)
        for key in ["overall", "quality", "plot_health", "character_health",
                    "pacing", "risks", "quotables"]:
            self.assertIn(key, result)

    def test_empty_state_safe(self):
        state = make_minimal_state()
        result = dash.compute_dashboard(state)
        self.assertEqual(result["overall"]["completed_chapters"], 0)
        self.assertEqual(result["quality"]["sample_count"], 0)
        self.assertEqual(result["plot_health"]["foreshadow_total"], 0)


class TestOverall(unittest.TestCase):
    def test_counts_chapters_and_words(self):
        state = make_minimal_state()
        state.completed_chapters = [
            _make_summary(1, word_count=2500),
            _make_summary(2, word_count=3500),
            _make_summary(3, word_count=3000),
        ]
        r = dash._compute_overall(state)
        self.assertEqual(r["completed_chapters"], 3)
        self.assertEqual(r["current_chapter"], 3)
        self.assertEqual(r["total_words"], 2500 + 3500 + 3000)


class TestQuality(unittest.TestCase):
    def test_avg_scores(self):
        state = make_minimal_state()
        state.completed_chapters = [
            _make_summary(i, score=s)
            for i, s in enumerate([6, 7, 8, 9, 7], 1)
        ]
        r = dash._compute_quality(state)
        self.assertEqual(r["last_critic_score"], 7)
        self.assertGreater(r["avg_score_last_10"], 6)
        self.assertEqual(r["sample_count"], 5)
        self.assertIn("叙事", r["dim_scores_avg"])

    def test_no_chapters_zero_avg(self):
        state = make_minimal_state()
        r = dash._compute_quality(state)
        self.assertEqual(r["avg_score_last_10"], 0)
        self.assertEqual(r["sample_count"], 0)


class TestPlotHealth(unittest.TestCase):
    def test_counts_fw_states(self):
        state = make_minimal_state()
        state.foreshadow_items = [
            ForeshadowItem(fw_id=f"fw{i}", content="x", hidden_meaning="",
                            importance=ForeshadowImportance.MAJOR,
                            planted_chapter=i, planned_resolve_volume=1,
                            planned_resolve_chapter=20,
                            resolution_description="x",
                            resolved=(i == 1))
            for i in range(1, 4)
        ]
        r = dash._compute_plot_health(state)
        self.assertEqual(r["foreshadow_total"], 3)
        self.assertEqual(r["foreshadow_planted"], 3)
        self.assertEqual(r["foreshadow_resolved"], 1)
        self.assertEqual(r["foreshadow_open"], 2)

    def test_overdue_detected(self):
        state = make_minimal_state()
        state.completed_chapters = [_make_summary(30)]  # 当前 30 章
        state.foreshadow_items = [
            ForeshadowItem(fw_id="fw1", content="x", hidden_meaning="",
                            importance=ForeshadowImportance.MAJOR,
                            planted_chapter=3, planned_resolve_volume=1,
                            planned_resolve_chapter=20,  # 已过期 10 章
                            resolution_description="x",
                            resolved=False),
        ]
        r = dash._compute_plot_health(state)
        self.assertEqual(r["foreshadow_overdue"], 1)

    def test_over_exposed_detected(self):
        state = make_minimal_state()
        fw = ForeshadowItem(fw_id="fw1", content="x", hidden_meaning="",
                            importance=ForeshadowImportance.MAJOR,
                            planted_chapter=3, planned_resolve_volume=1,
                            planned_resolve_chapter=20,
                            resolution_description="x",
                            resolved=False)
        fw.exposure_count = 5  # >= 4
        state.foreshadow_items = [fw]
        r = dash._compute_plot_health(state)
        self.assertEqual(r["foreshadow_over_exposed"], 1)


class TestCharacterHealth(unittest.TestCase):
    def test_supporting_cast_sorted(self):
        state = make_minimal_state()
        state.supporting_cast_stats = {
            "A": {"name": "A", "role": "主要配角", "appear_count": 3, "last_seen_chapter": 5},
            "B": {"name": "B", "role": "反派", "appear_count": 8, "last_seen_chapter": 12},
        }
        r = dash._compute_character_health(state)
        self.assertEqual(r["supporting_cast_top"][0]["name"], "B")
        self.assertEqual(r["supporting_cast_top"][1]["name"], "A")

    def test_antagonist_lifecycle_progress(self):
        state = make_minimal_state()
        state.antagonist_lifecycles = {
            "反派甲": {
                "antagonist_name": "反派甲",
                "nodes": [
                    {"key": "introduction", "triggered": True},
                    {"key": "first_conflict", "triggered": True},
                    {"key": "true_threat_revealed", "triggered": False},
                    {"key": "escalation", "triggered": False},
                ],
            },
        }
        r = dash._compute_character_health(state)
        self.assertEqual(r["antagonist_lifecycles"][0]["nodes_triggered"], 2)
        self.assertEqual(r["antagonist_lifecycles"][0]["progress_pct"], 50)


class TestPacing(unittest.TestCase):
    def test_last_5_hook_diversity(self):
        state = make_minimal_state()
        state.completed_chapters = [
            _make_summary(i, hook=h) for i, h in enumerate(
                ["suspense", "physical", "suspense", "emotional", "suspense"], 1
            )
        ]
        r = dash._compute_pacing(state)
        # 3 个不同 / 5 总数 = 0.6
        self.assertEqual(r["hook_diversity_ratio"], 0.6)


class TestQuotables(unittest.TestCase):
    def test_picks_most_recent_5(self):
        state = make_minimal_state()
        state.completed_chapters = [
            _make_summary(1, quotables=[{"kind": "对白", "text": "T1", "impact_score": 9}]),
            _make_summary(2, quotables=[{"kind": "场景", "text": "T2", "impact_score": 7}]),
        ]
        r = dash._compute_quotables(state)
        self.assertEqual(r["count"], 2)
        # 最近章节优先(reversed),所以 ch=2 在前
        self.assertEqual(r["recent"][0]["chapter_index"], 2)


class TestErrorIsolation(unittest.TestCase):
    def test_one_section_error_doesnt_break_others(self):
        state = make_minimal_state()
        # 故意制造一个会抛异常的 supporting_cast_stats
        state.supporting_cast_stats = "not a dict"
        result = dash.compute_dashboard(state)
        # 其他板块仍应工作
        self.assertIn("overall", result)
        self.assertIn("completed_chapters", result["overall"])


if __name__ == "__main__":
    unittest.main()
