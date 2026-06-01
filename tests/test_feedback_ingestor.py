"""tests for agents.feedback_ingestor."""
from __future__ import annotations
import unittest
from unittest.mock import patch
from dataclasses import dataclass, field

from tests._helpers import make_minimal_state
from persistence.state import (
    ChapterDirective, TensionLevel, RhythmType,
)
from agents import feedback_ingestor as fi
from agents.feedback_ingestor import IngestedFeedback


def _make_directive(ch_idx: int = 5, **kw) -> ChapterDirective:
    base = dict(
        chapter_index=ch_idx, volume_index=1,
        tension=TensionLevel.RISING, rhythm=RhythmType.SLOW_BUILD,
        active_lines=[], primary_line="",
        must_include=[], satisfaction_points=[],
        foreshadow_plant=[], foreshadow_resolve=[],
        emotional_note="", chapter_position="", word_pace="",
    )
    base.update(kw)
    return ChapterDirective(**base)


class TestIngest(unittest.TestCase):
    def test_empty_returns_ok_empty(self):
        result = fi.ingest("")
        self.assertEqual(result.raw_text, "")
        self.assertTrue(result.ok)

    def test_valid_response_parsed(self):
        fake = {
            "scope": "next_chapter",
            "target_aspect": "rhythm",
            "severity": "major",
            "summary": "节奏太慢",
            "action_for_writer": "压缩描写,加快对话",
            "action_for_planner": "下章去掉 1 个铺垫场景",
        }
        with patch.object(fi, "request_json_with_profile", return_value=fake):
            result = fi.ingest("这一章节奏太慢了,看着昏昏欲睡")
        self.assertTrue(result.ok)
        self.assertEqual(result.scope, "next_chapter")
        self.assertEqual(result.target_aspect, "rhythm")
        self.assertEqual(result.severity, "major")
        self.assertIn("节奏太慢", result.summary)

    def test_invalid_scope_normalized(self):
        fake = {"scope": "gibberish", "target_aspect": "rhythm", "severity": "major"}
        with patch.object(fi, "request_json_with_profile", return_value=fake):
            result = fi.ingest("X")
        self.assertEqual(result.scope, "next_chapter")  # 不合法→默认

    def test_invalid_aspect_normalized(self):
        fake = {"scope": "next_chapter", "target_aspect": "fake", "severity": "minor"}
        with patch.object(fi, "request_json_with_profile", return_value=fake):
            result = fi.ingest("X")
        self.assertEqual(result.target_aspect, "other")

    def test_invalid_severity_normalized(self):
        fake = {"scope": "next_chapter", "target_aspect": "rhythm", "severity": "wtf"}
        with patch.object(fi, "request_json_with_profile", return_value=fake):
            result = fi.ingest("X")
        self.assertEqual(result.severity, "major")

    def test_llm_failure_returns_fallback(self):
        with patch.object(fi, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            result = fi.ingest("节奏太慢")
        self.assertFalse(result.ok)
        # fallback 把 raw_text 当 action_for_writer
        self.assertEqual(result.action_for_writer, "节奏太慢")


class TestEnqueue(unittest.TestCase):
    def test_appends_to_queue(self):
        state = make_minimal_state()
        state.user_feedback_queue = []
        fb = IngestedFeedback(raw_text="X", scope="next_chapter",
                               target_aspect="rhythm", severity="major")
        fi.enqueue(state, fb, target_chapter_index=10)
        self.assertEqual(len(state.user_feedback_queue), 1)
        self.assertEqual(state.user_feedback_queue[0]["target_chapter_index"], 10)
        self.assertFalse(state.user_feedback_queue[0]["consumed"])

    def test_creates_queue_if_absent(self):
        state = make_minimal_state()
        if hasattr(state, "user_feedback_queue"):
            delattr(state, "user_feedback_queue")
        fb = IngestedFeedback(raw_text="X")
        fi.enqueue(state, fb, target_chapter_index=10)
        self.assertTrue(hasattr(state, "user_feedback_queue"))


class TestApplyToDirective(unittest.TestCase):
    def test_critical_injects_to_must_include_head_and_feedback(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": 5,
            "ingested": {
                "scope": "next_chapter",
                "target_aspect": "plot",
                "severity": "critical",
                "summary": "S",
                "action_for_writer": "W",
                "action_for_planner": "P",
            },
            "consumed": False,
        }]
        d = _make_directive(ch_idx=5, must_include=["原 must"])
        applied = fi.apply_to_directive(state, d)
        self.assertEqual(applied, 1)
        # critical 注入到 must_include 头部
        self.assertIn("P", d.must_include[0])
        self.assertIn("critical", d.must_include[0])
        # action_for_writer 注入 user_feedback
        self.assertIn("W", d.user_feedback)
        # 标记 consumed
        self.assertTrue(state.user_feedback_queue[0]["consumed"])

    def test_major_appends_must_include(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": 5,
            "ingested": {
                "scope": "next_chapter",
                "target_aspect": "rhythm",
                "severity": "major",
                "action_for_writer": "W",
                "action_for_planner": "P",
            },
            "consumed": False,
        }]
        d = _make_directive(ch_idx=5, must_include=["原"])
        fi.apply_to_directive(state, d)
        # major 追加到末尾
        self.assertEqual(d.must_include[0], "原")
        self.assertIn("P", d.must_include[-1])

    def test_minor_adds_to_user_feedback(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": 5,
            "ingested": {
                "scope": "next_chapter",
                "target_aspect": "tone",
                "severity": "minor",
                "action_for_writer": "W",
                "action_for_planner": "P",
            },
            "consumed": False,
        }]
        d = _make_directive(ch_idx=5)
        fi.apply_to_directive(state, d)
        self.assertIn("W", d.user_feedback)
        self.assertIn("minor", d.user_feedback)

    def test_other_chapter_not_applied(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": 10,  # 不是本章
            "ingested": {"scope": "next_chapter", "severity": "major", "action_for_planner": "X"},
            "consumed": False,
        }]
        d = _make_directive(ch_idx=5)
        applied = fi.apply_to_directive(state, d)
        self.assertEqual(applied, 0)
        self.assertFalse(state.user_feedback_queue[0]["consumed"])

    def test_global_scope_always_applies_and_not_consumed(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": -1,  # 不重要,scope=global
            "ingested": {"scope": "global", "severity": "major", "action_for_planner": "ALL"},
            "consumed": False,
        }]
        d = _make_directive(ch_idx=5)
        applied = fi.apply_to_directive(state, d)
        self.assertEqual(applied, 1)
        # global scope 不 mark consumed(每章都用)
        self.assertFalse(state.user_feedback_queue[0]["consumed"])

    def test_consumed_items_skipped(self):
        state = make_minimal_state()
        state.user_feedback_queue = [{
            "target_chapter_index": 5,
            "ingested": {"scope": "next_chapter", "severity": "major", "action_for_planner": "X"},
            "consumed": True,
        }]
        d = _make_directive(ch_idx=5)
        applied = fi.apply_to_directive(state, d)
        self.assertEqual(applied, 0)


if __name__ == "__main__":
    unittest.main()
