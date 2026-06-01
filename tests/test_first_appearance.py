"""tests for P1-2: Character.first_appearance_signature + writer 首秀注入。"""
from __future__ import annotations
import unittest

from tests._helpers import make_minimal_state
from persistence.state import (
    Character, CharacterRole, ChapterSummary, TensionLevel,
)
from agents.writer import _is_first_appearance, _format_voice_cards_for_scene


def _make_char(name, role="主要配角", signature=""):
    c = Character(
        name=name, role=CharacterRole(role),
        gender="", age_desc="", appearance="", personality="",
        personality_detail="", background="", trauma="", desire="",
        fear="", speech_pattern="冷峻短句", ability="", realm="",
        arc="", motivation="", fatal_flaw="",
        first_volume=1, last_volume=-1,
    )
    c.first_appearance_signature = signature
    return c


def _summary(idx, summary_text=""):
    return ChapterSummary(
        index=idx, volume_index=1, title=f"第{idx}章",
        summary=summary_text, word_count=3000, tension=TensionLevel.RISING,
    )


class TestIsFirstAppearance(unittest.TestCase):
    def test_no_history_is_first(self):
        state = make_minimal_state()
        state.completed_chapters = []
        self.assertTrue(_is_first_appearance(state, "李慕白", 5))

    def test_already_mentioned_not_first(self):
        state = make_minimal_state()
        state.completed_chapters = [_summary(3, "李慕白现身于朱雀街")]
        self.assertFalse(_is_first_appearance(state, "李慕白", 5))

    def test_future_chapters_ignored(self):
        state = make_minimal_state()
        # 第 7 章提过(但本章是 5),应当作 first
        state.completed_chapters = [_summary(7, "李慕白现身")]
        self.assertTrue(_is_first_appearance(state, "李慕白", 5))

    def test_short_name_returns_false(self):
        state = make_minimal_state()
        # 单字名容易误命中,直接返 False
        self.assertFalse(_is_first_appearance(state, "X", 5))


class TestVoiceCardsInjectsSignature(unittest.TestCase):
    def test_first_appearance_with_signature_injected(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [
            _make_char("李慕白", signature="他立在屋檐下,左手扶腰间断裂玉佩,挑眉:你也来送死?"),
        ]
        state.completed_chapters = []  # 首次出场
        block = _format_voice_cards_for_scene(state, ["李慕白"], chapter_index=5)
        self.assertIn("李慕白", block)
        self.assertIn("首秀亮相镜头", block)
        self.assertIn("玉佩", block)

    def test_not_first_appearance_no_signature(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [
            _make_char("李慕白", signature="他立在屋檐下,左手扶玉佩"),
        ]
        state.completed_chapters = [_summary(3, "李慕白已出现过")]
        block = _format_voice_cards_for_scene(state, ["李慕白"], chapter_index=5)
        self.assertIn("李慕白", block)
        self.assertNotIn("首秀亮相镜头", block)

    def test_empty_signature_no_injection(self):
        state = make_minimal_state()
        state.characters = list(state.characters) + [
            _make_char("李慕白", signature=""),
        ]
        state.completed_chapters = []
        block = _format_voice_cards_for_scene(state, ["李慕白"], chapter_index=5)
        self.assertNotIn("首秀亮相镜头", block)


class TestCharacterFieldExists(unittest.TestCase):
    def test_field_default_empty(self):
        c = _make_char("X")
        self.assertEqual(c.first_appearance_signature, "")

    def test_field_can_be_set(self):
        c = _make_char("X", signature="A signature")
        self.assertEqual(c.first_appearance_signature, "A signature")


if __name__ == "__main__":
    unittest.main()
