"""agents/downstream_staleness 回归测试。

覆盖：
  · scan_outlines_for_violations 合规 outline 返回空
  · scan_outlines_for_violations 违规 outline 抓住
  · report_downstream_staleness 非 canon section 跳过
  · _CANON_SECTIONS 集合稳定（避免误添）
"""
import unittest
import os
import tempfile
from tests._helpers import make_minimal_state


def _add_outline(state, vol_idx, ch_idx, goal):
    """在 state 里插入一条 outline——避免与项目数据混淆。"""
    from persistence.state import Volume
    vol = next((v for v in state.volumes if v.index == vol_idx), None)
    if vol is None:
        vol = Volume(
            index=vol_idx, title=f"第{vol_idx}卷", theme="",
            arc="", chapter_start=1, chapter_end=10,
            opening_hook="", closing_hook="",
            volume_antagonist="", key_events=[],
        )
        state.volumes.append(vol)
    if vol.chapter_outlines is None:
        vol.chapter_outlines = []
    vol.chapter_outlines.append({
        "index": ch_idx, "title": f"第{ch_idx}章",
        "goal": goal, "position": "普通",
    })


class TestScanOutlines(unittest.TestCase):

    def test_clean_outlines_zero_violations(self):
        from agents.downstream_staleness import scan_outlines_for_violations
        state = make_minimal_state(dynasty="测试朝")
        _add_outline(state, 1, 1, "主角开局合规故事，没有违规字面")
        _add_outline(state, 1, 2, "主角推进剧情")
        out = scan_outlines_for_violations(state)
        self.assertEqual(out, [])

    def test_dynasty_mismatch_caught(self):
        from agents.downstream_staleness import scan_outlines_for_violations
        state = make_minimal_state(dynasty="大雍王朝")
        _add_outline(state, 1, 5, "主角穿越成白鹿朝寒门秀才")
        out = scan_outlines_for_violations(state)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["chapter"], 5)
        self.assertIn("dynasty_name_mismatch", out[0]["kinds"])

    def test_real_ai_dangerous_caught(self):
        from agents.downstream_staleness import scan_outlines_for_violations
        state = make_minimal_state(real_ai_asset=True, asset_name="豆包")
        _add_outline(state, 1, 3, "主角通过豆包确认本地势力底牌")
        out = scan_outlines_for_violations(state)
        self.assertEqual(len(out), 1)
        self.assertIn("real_ai_dangerous_command", out[0]["kinds"])


class TestReportSectionFiltering(unittest.TestCase):
    """非 canon section 编辑不触发扫描——节省 LLM 调用 / IO。"""

    def test_non_canon_section_skipped(self):
        from agents.downstream_staleness import report_downstream_staleness
        state = make_minimal_state()
        result = report_downstream_staleness(state, changed_section="rhythm_plans")
        # 跳过——返回空 dict 字段
        self.assertEqual(result["outline_violations"], [])
        self.assertEqual(result["chapter_violations"], [])

    def test_canon_sections_membership(self):
        """_CANON_SECTIONS 是稳定的集合——避免改错"""
        from agents.downstream_staleness import _CANON_SECTIONS
        # 必须包含的——这些 section 影响下游 canon 校验
        required = {"power_system", "world", "world_canon",
                    "factions", "characters"}
        self.assertTrue(required.issubset(_CANON_SECTIONS),
                         f"missing required: {required - _CANON_SECTIONS}")


if __name__ == "__main__":
    unittest.main()
