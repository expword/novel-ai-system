"""tests for new HITL gates (volume_end / critic_unrecoverable / pov_critical / master_antagonist)
+ major death detection helper."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from tests._helpers import make_minimal_state
from persistence.state import (
    Character, CharacterRole, ChapterSummary, TensionLevel,
)
from project_mgmt import human_in_loop


class TestNewGateFunctions(unittest.TestCase):
    """4 个新 gate function 在 skip mode 下能调用、不抛、返回 True (默认许可)。"""

    def test_gate_volume_end_skip_mode(self):
        state = make_minimal_state()
        ok = human_in_loop.gate_volume_end(
            state, volume_index=1,
            vol_review={"chapter_count": 20, "avg_critic_score": 7.5},
            mode="skip",
        )
        self.assertTrue(ok)

    def test_gate_critic_unrecoverable_skip(self):
        state = make_minimal_state()
        ok = human_in_loop.gate_critic_unrecoverable(
            state, chapter_index=10,
            last_score=5.5, last_feedback="scene 3 节奏过慢",
            mode="skip",
        )
        self.assertTrue(ok)

    def test_gate_pov_critical_skip(self):
        state = make_minimal_state()
        violations = [
            {"excerpt": "主角准确说出反派密谋",
             "explanation": "主角不在场无来源"},
        ]
        ok = human_in_loop.gate_pov_critical_residual(
            state, chapter_index=10, violations=violations, mode="skip",
        )
        self.assertTrue(ok)

    def test_gate_canon_critical_skip(self):
        state = make_minimal_state()
        ok = human_in_loop.gate_canon_critical_residual(
            state, chapter_index=10, issues=[{"kind": "x"}], mode="skip",
        )
        self.assertTrue(ok)

    def test_gate_master_antagonist_skip(self):
        state = make_minimal_state()
        ok = human_in_loop.gate_master_antagonist(
            state, antagonist_summary={"antagonist_slots": []}, mode="skip",
        )
        self.assertTrue(ok)


class TestMajorDeathDetection(unittest.TestCase):
    """director._detect_major_deaths 启发式正确识别 + 排除修辞。"""

    def _make_director(self):
        from core.director import DirectorAgent
        state = make_minimal_state()
        # 3 个重要角色 + 1 个次要
        for name, role in [
            ("李慕白", "主要配角"),
            ("张三", "反派"),
            ("赵四", "主要配角"),
            ("陈五", "次要配角"),
        ]:
            state.characters.append(Character(
                name=name, role=CharacterRole(role),
                gender="", age_desc="", appearance="", personality="",
                personality_detail="", background="", trauma="", desire="",
                fear="", speech_pattern="", ability="", realm="",
                arc="", motivation="", fatal_flaw="",
                first_volume=1, last_volume=-1,
            ))
        d = object.__new__(DirectorAgent)
        d.state = state
        return d

    def _make_summary(self, text):
        return ChapterSummary(
            index=1, volume_index=1, title="", summary=text,
            word_count=3000, tension=TensionLevel.RISING,
        )

    def test_genuine_death_detected(self):
        d = self._make_director()
        result = d._detect_major_deaths(self._make_summary("李慕白被流箭穿心当场断气"))
        self.assertIn("李慕白", result)

    def test_negation_not_detected(self):
        d = self._make_director()
        result = d._detect_major_deaths(self._make_summary("李慕白没死,只是昏过去了"))
        self.assertNotIn("李慕白", result)

    def test_rhetorical_not_detected(self):
        d = self._make_director()
        # "死灰" / "差点死" / "死路一条" / "死气沉沉" 是修辞
        for text in ["主角看着李慕白死灰一般的脸",
                      "李慕白差点死过去",
                      "李慕白说这是死路一条"]:
            result = d._detect_major_deaths(self._make_summary(text))
            self.assertNotIn("李慕白", result, f"误判: {text}")

    def test_minor_role_not_detected(self):
        d = self._make_director()
        # 陈五是次要配角 (不在重要角色列表)
        result = d._detect_major_deaths(self._make_summary("陈五意外丧命"))
        self.assertNotIn("陈五", result)

    def test_multiple_deaths(self):
        d = self._make_director()
        result = d._detect_major_deaths(
            self._make_summary("张三和李慕白同时陨落")
        )
        self.assertEqual(set(result), {"张三", "李慕白"})

    def test_empty_text(self):
        d = self._make_director()
        result = d._detect_major_deaths(self._make_summary(""))
        self.assertEqual(result, [])


class TestVolumeEndReviewBuilder(unittest.TestCase):
    """_build_volume_end_review 聚合本卷数据。"""

    def test_empty_volume(self):
        from core.director import DirectorAgent
        state = make_minimal_state()
        # 加一个空卷
        from persistence.state import Volume
        vol = Volume(
            index=1, title="测试卷", theme="", arc="",
            chapter_start=1, chapter_end=20,
            opening_hook="", closing_hook="", volume_antagonist="",
        )
        state.volumes = [vol]
        d = object.__new__(DirectorAgent)
        d.state = state
        result = d._build_volume_end_review(1, vol)
        self.assertEqual(result["chapter_count"], 0)
        self.assertEqual(result["avg_critic_score"], 0)
        self.assertEqual(result["volume_index"], 1)

    def test_with_chapters(self):
        from core.director import DirectorAgent
        state = make_minimal_state()
        from persistence.state import Volume
        vol = Volume(
            index=1, title="测试卷", theme="", arc="",
            chapter_start=1, chapter_end=5,
            opening_hook="", closing_hook="", volume_antagonist="",
        )
        state.volumes = [vol]
        # 加 3 章 ChapterSummary
        for i in range(1, 4):
            cs = ChapterSummary(
                index=i, volume_index=1, title=f"第{i}章",
                summary="x", word_count=3000, tension=TensionLevel.RISING,
                closing_hook_type=("suspense" if i % 2 else "physical"),
            )
            cs.critic_review = {"score": 7 + i, "passed": True}
            state.completed_chapters.append(cs)
        d = object.__new__(DirectorAgent)
        d.state = state
        result = d._build_volume_end_review(1, vol)
        self.assertEqual(result["chapter_count"], 3)
        self.assertGreater(result["avg_critic_score"], 0)
        # 2 不同 hook type / 3 总 → 0.67
        self.assertGreater(result["hook_diversity"], 0.5)


class TestMasterAntagonistPayload(unittest.TestCase):
    """_build_master_antagonist_payload 抽反派槽位。"""

    def test_no_master_outline(self):
        from core.director import DirectorAgent
        state = make_minimal_state()
        d = object.__new__(DirectorAgent)
        d.state = state
        payload = d._build_master_antagonist_payload()
        self.assertEqual(payload, {})

    def test_extracts_antagonist_slots(self):
        from core.director import DirectorAgent
        from persistence.state import CharacterSlot, PlotSetpiece, MasterOutline
        state = make_minimal_state()
        state.master_outline = MasterOutline(
            generated=True,
            central_conflict="主角对抗张氏家族",
            thematic_core="复仇与原谅",
            character_slots=[
                CharacterSlot(slot_id="mc_01", role_tag="主角",
                              function="x", brief_hint="x"),
                CharacterSlot(slot_id="ant_01", role_tag="反派",
                              function="对主角致命压制",
                              brief_hint="家族家主,杀主角父亲",
                              narrative_arc_hint="由盛转衰",
                              first_volume=1, last_volume=5),
            ],
            plot_setpieces=[
                PlotSetpiece(
                    anchor="第3卷末", kind="对决",
                    gist="主角首次正面对阵",
                    involved_slot_ids=["mc_01", "ant_01"],
                ),
            ],
        )
        d = object.__new__(DirectorAgent)
        d.state = state
        payload = d._build_master_antagonist_payload()
        self.assertEqual(len(payload["antagonist_slots"]), 1)
        self.assertEqual(payload["antagonist_slots"][0]["slot_id"], "ant_01")
        self.assertEqual(len(payload["antagonist_setpieces"]), 1)
        self.assertEqual(payload["central_conflict"], "主角对抗张氏家族")


if __name__ == "__main__":
    unittest.main()
