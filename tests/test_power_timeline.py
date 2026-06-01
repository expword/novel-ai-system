"""power_timeline_tracker.validate_power_consistency 回归测试。

不调 LLM——只测纯校验逻辑。
"""
import unittest
from tests._helpers import make_minimal_state


class TestValidatePowerConsistency(unittest.TestCase):

    def _add_profile(self, state, holder, *ability_names):
        from persistence.state import CharacterAbilityProfile, LearnedAbility
        prof = CharacterAbilityProfile(
            holder_name=holder,
            learned_abilities=[LearnedAbility(name=n) for n in ability_names],
        )
        state.character_ability_profiles[holder] = prof

    def _add_event(self, state, ch, user, ability):
        from persistence.state import PowerEvent
        state.power_events.append(PowerEvent(
            chapter_index=ch, user=user, ability_name=ability,
        ))

    def test_used_unregistered_ability_caught(self):
        from agents.power_timeline_tracker import validate_power_consistency
        s = make_minimal_state()
        self._add_profile(s, "测试主角", "破天剑")
        self._add_event(s, 5, "测试主角", "未登记的能力")
        issues = validate_power_consistency(s)
        criticals = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(len(criticals), 1)
        self.assertEqual(criticals[0]["kind"], "used_unregistered_ability")
        self.assertEqual(criticals[0]["ability"], "未登记的能力")

    def test_registered_ability_passes(self):
        from agents.power_timeline_tracker import validate_power_consistency
        s = make_minimal_state()
        self._add_profile(s, "测试主角", "破天剑")
        self._add_event(s, 5, "测试主角", "破天剑")
        issues = validate_power_consistency(s)
        criticals = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(criticals, [])

    def test_unknown_user_skipped(self):
        from agents.power_timeline_tracker import validate_power_consistency
        s = make_minimal_state()
        self._add_event(s, 5, "未知", "某能力")
        issues = validate_power_consistency(s)
        criticals = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(criticals, [])  # 未知 user 不算 error

    def test_user_without_profile_warns(self):
        from agents.power_timeline_tracker import validate_power_consistency
        s = make_minimal_state()
        # 角色没 profile（直接用了能力）→ warn 级
        self._add_event(s, 5, "某配角", "随手出招")
        issues = validate_power_consistency(s)
        warns = [i for i in issues if i["severity"] == "warn"
                 and i["kind"] == "no_ability_profile"]
        self.assertEqual(len(warns), 1)


class TestCharacterAbilityBlock(unittest.TestCase):
    """writer.py:_format_character_ability_block 渲染。"""

    def test_no_profiles_returns_empty(self):
        from agents.writer import _format_character_ability_block
        from persistence.state import ChapterDirective, TensionLevel, RhythmType
        s = make_minimal_state()
        d = ChapterDirective(
            chapter_index=1, volume_index=1,
            tension=TensionLevel.PEAK, rhythm=RhythmType.SLOW_BUILD,
            emotional_note="", chapter_position="", chapter_type="",
            structure_role="", active_lines=[], primary_line="",
            must_include=[], satisfaction_points=[], foreshadow_plant=[],
            foreshadow_resolve=[], word_pace="medium",
        )
        block = _format_character_ability_block(s, d)
        self.assertEqual(block, "")

    def test_lists_only_learned_abilities_so_far(self):
        from agents.writer import _format_character_ability_block
        from persistence.state import (
            ChapterDirective, TensionLevel, RhythmType,
            CharacterAbilityProfile, LearnedAbility, CharacterStateSnapshot,
        )
        s = make_minimal_state()
        s.character_ability_profiles["测试主角"] = CharacterAbilityProfile(
            holder_name="测试主角",
            ceiling_now="筑基期",
            learned_abilities=[
                LearnedAbility(name="起手会的", learned_at_chapter=-1),
                LearnedAbility(name="第 5 章学到", learned_at_chapter=5),
                LearnedAbility(name="第 20 章学到", learned_at_chapter=20),
            ],
        )
        d = ChapterDirective(
            chapter_index=10, volume_index=1,
            tension=TensionLevel.PEAK, rhythm=RhythmType.SLOW_BUILD,
            emotional_note="", chapter_position="", chapter_type="",
            structure_role="", active_lines=[], primary_line="",
            must_include=[], satisfaction_points=[], foreshadow_plant=[],
            foreshadow_resolve=[], word_pace="medium",
            character_states={"测试主角": CharacterStateSnapshot(chapter_index=10)},
        )
        block = _format_character_ability_block(s, d)
        self.assertIn("测试主角", block)
        self.assertIn("起手会的", block)
        self.assertIn("第 5 章学到", block)
        self.assertNotIn("第 20 章学到", block)  # 第 10 章时还没学
        self.assertIn("筑基期", block)


if __name__ == "__main__":
    unittest.main()
