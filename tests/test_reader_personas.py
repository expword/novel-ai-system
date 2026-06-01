"""tests for utils.reader_personas."""
from __future__ import annotations
import unittest

from utils.reader_personas import (
    ReaderPersona, DIE_HARD, NITPICKER, CASUAL, QUOTER,
    ALL_PERSONAS, PERSONA_BY_KEY, PERSONA_BY_LABEL,
    get_persona, format_all_for_prompt, format_personas_for_prompt,
    all_labels, all_keys,
)


class TestPersonaData(unittest.TestCase):
    def test_4_personas_defined(self):
        self.assertEqual(len(ALL_PERSONAS), 4)

    def test_all_keys_unique(self):
        keys = [p.key for p in ALL_PERSONAS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_all_labels_unique(self):
        labels = [p.label for p in ALL_PERSONAS]
        self.assertEqual(len(labels), len(set(labels)))

    def test_labels_in_chinese(self):
        for p in ALL_PERSONAS:
            self.assertIn(p.label, ["追读派", "挑刺派", "路过派", "章评党"])

    def test_each_has_required_fields(self):
        for p in ALL_PERSONAS:
            self.assertTrue(p.key)
            self.assertTrue(p.label)
            self.assertIn(p.stance, ["positive", "critical", "neutral", "mixed"])
            self.assertGreater(len(p.interests), 0)
            self.assertGreater(len(p.pain_points), 0)
            self.assertGreater(len(p.delight_points), 0)


class TestGetPersona(unittest.TestCase):
    def test_by_key(self):
        self.assertIs(get_persona("die_hard"), DIE_HARD)
        self.assertIs(get_persona("nitpicker"), NITPICKER)

    def test_by_label(self):
        self.assertIs(get_persona("追读派"), DIE_HARD)
        self.assertIs(get_persona("挑刺派"), NITPICKER)

    def test_unknown_returns_none(self):
        self.assertIsNone(get_persona("不存在的"))
        self.assertIsNone(get_persona(""))


class TestFormatForPrompt(unittest.TestCase):
    def test_all_includes_4_personas(self):
        text = format_all_for_prompt()
        for p in ALL_PERSONAS:
            self.assertIn(p.label, text)

    def test_selected_only(self):
        text = format_personas_for_prompt(DIE_HARD, NITPICKER)
        self.assertIn("追读派", text)
        self.assertIn("挑刺派", text)
        self.assertNotIn("路过派", text)
        self.assertNotIn("章评党", text)

    def test_empty_returns_empty(self):
        self.assertEqual(format_personas_for_prompt(), "")

    def test_header_prepended(self):
        text = format_all_for_prompt(header="═ HEADER ═")
        self.assertTrue(text.startswith("═ HEADER ═"))


class TestPersonaSerialization(unittest.TestCase):
    def test_to_dict(self):
        d = DIE_HARD.to_dict()
        self.assertEqual(d["key"], "die_hard")
        self.assertEqual(d["label"], "追读派")
        self.assertIsInstance(d["interests"], list)

    def test_prompt_block_format(self):
        block = DIE_HARD.to_prompt_block()
        self.assertIn("追读派", block)
        self.assertIn("关注", block)
        self.assertIn("会骂", block)
        self.assertIn("会赞", block)


class TestPublicAPI(unittest.TestCase):
    def test_all_labels(self):
        self.assertEqual(set(all_labels()), {"追读派", "挑刺派", "路过派", "章评党"})

    def test_all_keys(self):
        self.assertEqual(set(all_keys()), {"die_hard", "nitpicker", "casual", "quoter"})


class TestCommentSimulatorIntegration(unittest.TestCase):
    """验证 comment_simulator 真的引用了 reader_personas。"""

    def test_comment_simulator_uses_personas(self):
        from agents import comment_simulator
        # SYSTEM 应含 4 个 label(通过 format_all_for_prompt 注入)
        for label in ["追读派", "挑刺派", "路过派", "章评党"]:
            self.assertIn(label, comment_simulator.SYSTEM)

    def test_allowed_types_synced(self):
        from agents import comment_simulator
        self.assertEqual(
            comment_simulator._ALLOWED_TYPES,
            {"追读派", "挑刺派", "路过派", "章评党"},
        )


if __name__ == "__main__":
    unittest.main()
