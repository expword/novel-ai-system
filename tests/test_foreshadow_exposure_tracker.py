"""tests for agents.foreshadow_exposure_tracker."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from tests._helpers import make_minimal_state
from persistence.state import ForeshadowItem, ForeshadowImportance
from agents import foreshadow_exposure_tracker as fet
from agents.foreshadow_exposure_tracker import ExposureEvent, EXPOSURE_THRESHOLD


def _make_fw(fw_id: str, planted: int, resolved: bool = False, exposure: int = 0):
    fw = ForeshadowItem(
        fw_id=fw_id, content=f"内容{fw_id}", hidden_meaning="",
        importance=ForeshadowImportance.MAJOR,
        planted_chapter=planted, planned_resolve_volume=1,
        planned_resolve_chapter=20, resolution_description="",
        resolved=resolved,
    )
    fw.exposure_count = exposure
    return fw


class TestActiveFilter(unittest.TestCase):
    def test_excludes_future_planted(self):
        state = make_minimal_state()
        state.foreshadow_items = [
            _make_fw("fw1", planted=3),   # 已植入
            _make_fw("fw2", planted=10),  # 未来才植入
        ]
        active = fet._get_active_foreshadows(state, chapter_index=5)
        self.assertEqual([f.fw_id for f in active], ["fw1"])

    def test_excludes_resolved(self):
        state = make_minimal_state()
        state.foreshadow_items = [
            _make_fw("fw1", planted=3, resolved=False),
            _make_fw("fw2", planted=3, resolved=True),
        ]
        active = fet._get_active_foreshadows(state, chapter_index=5)
        self.assertEqual([f.fw_id for f in active], ["fw1"])


class TestAuditChapter(unittest.TestCase):
    def test_short_text_returns_empty(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        with patch.object(fet, "request_json_with_profile") as mock:
            events = fet.audit_chapter(state, 5, "短")
        self.assertEqual(events, [])
        mock.assert_not_called()

    def test_no_active_skips_llm(self):
        state = make_minimal_state()
        state.foreshadow_items = []
        with patch.object(fet, "request_json_with_profile") as mock:
            events = fet.audit_chapter(state, 5, "x" * 200)
        self.assertEqual(events, [])
        mock.assert_not_called()

    def test_returns_events(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        fake = {"exposures": [
            {"fw_id": "fw1", "evidence": "主角再次梦到那个人", "exposure_level": "暗示"},
        ]}
        with patch.object(fet, "request_json_with_profile", return_value=fake):
            events = fet.audit_chapter(state, 5, "x" * 200)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].fw_id, "fw1")
        self.assertEqual(events[0].level, "暗示")

    def test_filters_unknown_fw_ids(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        fake = {"exposures": [
            {"fw_id": "unknown_fw", "evidence": "X", "exposure_level": "提及"},
            {"fw_id": "fw1", "evidence": "Y", "exposure_level": "暗示"},
        ]}
        with patch.object(fet, "request_json_with_profile", return_value=fake):
            events = fet.audit_chapter(state, 5, "x" * 200)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].fw_id, "fw1")

    def test_dedup_same_fw(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        fake = {"exposures": [
            {"fw_id": "fw1", "evidence": "A", "exposure_level": "提及"},
            {"fw_id": "fw1", "evidence": "B", "exposure_level": "强烈暗示"},
        ]}
        with patch.object(fet, "request_json_with_profile", return_value=fake):
            events = fet.audit_chapter(state, 5, "x" * 200)
        self.assertEqual(len(events), 1)
        # 取第一个(代码用 seen_ids 去重,后续同 fw_id 被跳)
        self.assertEqual(events[0].evidence, "A")

    def test_llm_failure_returns_empty(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        with patch.object(fet, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            events = fet.audit_chapter(state, 5, "x" * 200)
        self.assertEqual(events, [])


class TestApplyExposures(unittest.TestCase):
    def test_increments_exposure_count(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3, exposure=0)]
        over = fet.apply_exposures(state, [
            ExposureEvent("fw1", "evidence", "提及"),  # +1
        ])
        self.assertEqual(state.foreshadow_items[0].exposure_count, 1)
        self.assertEqual(over, [])  # 未超阈值

    def test_strong_hint_adds_2(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3, exposure=0)]
        fet.apply_exposures(state, [
            ExposureEvent("fw1", "evidence", "强烈暗示"),  # +2
        ])
        self.assertEqual(state.foreshadow_items[0].exposure_count, 2)

    def test_over_threshold_returned(self):
        state = make_minimal_state()
        # 已经 EXPOSURE_THRESHOLD-1 暴露度,再 +1 应触发
        state.foreshadow_items = [_make_fw("fw1", planted=3,
                                            exposure=EXPOSURE_THRESHOLD - 1)]
        over = fet.apply_exposures(state, [
            ExposureEvent("fw1", "evidence", "提及"),
        ])
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["fw_id"], "fw1")
        self.assertGreaterEqual(over[0]["exposure_count"], EXPOSURE_THRESHOLD)

    def test_resolved_fw_not_triggered(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3, resolved=True,
                                            exposure=EXPOSURE_THRESHOLD)]
        over = fet.apply_exposures(state, [
            ExposureEvent("fw1", "evidence", "暗示"),
        ])
        # resolved 的 fw 不报警
        self.assertEqual(over, [])

    def test_unknown_fw_id_ignored(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3)]
        fet.apply_exposures(state, [
            ExposureEvent("unknown", "X", "暗示"),  # 没匹配的 fw
        ])
        self.assertEqual(state.foreshadow_items[0].exposure_count, 0)


class TestAuditAndApply(unittest.TestCase):
    def test_full_cycle(self):
        state = make_minimal_state()
        state.foreshadow_items = [_make_fw("fw1", planted=3, exposure=0)]
        fake = {"exposures": [
            {"fw_id": "fw1", "evidence": "evidence", "exposure_level": "暗示"},
        ]}
        with patch.object(fet, "request_json_with_profile", return_value=fake):
            stats = fet.audit_and_apply(state, 5, "x" * 200)
        self.assertEqual(stats["exposed_count"], 1)
        self.assertEqual(state.foreshadow_items[0].exposure_count, 1)


if __name__ == "__main__":
    unittest.main()
