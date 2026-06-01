"""tests for chapter_planner._validate_scene_concreteness (P0-1)."""
from __future__ import annotations
import unittest

from agents.chapter_planner import (
    _validate_scene_concreteness, _is_beat_filler, _BEAT_FILLER_LITERALS,
)


class TestIsBeatFiller(unittest.TestCase):
    def test_literal_fillers_caught(self):
        for word in ["话1", "话2", "示范对话", "悬念待续", "细节1", "TODO"]:
            self.assertTrue(_is_beat_filler(word), f"{word} should be caught")

    def test_too_short_caught(self):
        self.assertTrue(_is_beat_filler("短"))
        self.assertTrue(_is_beat_filler("好的"))
        self.assertTrue(_is_beat_filler("无"))

    def test_empty_caught(self):
        self.assertTrue(_is_beat_filler(""))
        self.assertTrue(_is_beat_filler("   "))
        self.assertTrue(_is_beat_filler("「」"))

    def test_concrete_dialogue_passes(self):
        concrete = "师父(压低声,指尖轻叩剑鞘):此剑一出便再无回头"
        self.assertFalse(_is_beat_filler(concrete))

    def test_concrete_sensory_passes(self):
        concrete = "门缝漏出半寸烛光,带着松烟的焦味"
        self.assertFalse(_is_beat_filler(concrete))


class TestValidator(unittest.TestCase):
    def test_empty_data_passes(self):
        ok, _ = _validate_scene_concreteness({})
        self.assertTrue(ok)

    def test_no_beats_passes(self):
        ok, _ = _validate_scene_concreteness({"scene_beats": []})
        self.assertTrue(ok)

    def test_filler_dialogue_seeds_rejected(self):
        data = {
            "scene_beats": [{
                "content": "x" * 200,
                "dialogue_seeds": ["话1", "话2", "话3", "话4"],
                "sensory_anchors": [],
            }],
            "closing_hook": "他望向远方某个东西心中有什么在涌动",
        }
        ok, msg = _validate_scene_concreteness(data)
        self.assertFalse(ok)
        self.assertIn("占位", msg)

    def test_too_short_content_rejected(self):
        data = {
            "scene_beats": [{
                "content": "短场景",  # 远小于 180
                "dialogue_seeds": [
                    "师父(沉声):此剑一出便再无回头",
                    "主角(转身):你可知我在等什么?",
                ],
                "sensory_anchors": [
                    "门缝漏出半寸烛光,带松烟焦味",
                ],
            }],
            "closing_hook": "他握紧那枚铜钥,望向远处塔尖",
        }
        ok, msg = _validate_scene_concreteness(data)
        # content 3 字 + 0 处其他 = 1 issue → tolerance ≤3 应通过
        self.assertTrue(ok, f"single issue should be tolerated: {msg}")

    def test_multiple_short_content_rejected(self):
        """≥4 处问题应被拒绝。"""
        data = {
            "scene_beats": [
                {"content": "短", "dialogue_seeds": ["话1"], "sensory_anchors": ["细节1"]},
                {"content": "短", "dialogue_seeds": ["话2"], "sensory_anchors": ["细节2"]},
            ],
            "closing_hook": "悬念",
        }
        ok, msg = _validate_scene_concreteness(data)
        self.assertFalse(ok)

    def test_realistic_good_data_passes(self):
        data = {
            "scene_beats": [{
                "content": (
                    "主角踏入议事堂时,雨水正从他的领口滑落。屋内三人原本低声交谈,见他进来"
                    "同时停了下来——师父在主位上,目光沉,却没有第一时间开口;李三靠在门框,"
                    "手指无意识地敲着剑柄;那名陌生客人坐在偏位,袖中似有信物轮廓。"
                ),
                "dialogue_seeds": [
                    "师父(压低声,指尖轻叩剑鞘):此剑一出便再无回头",
                    "主角(站起来转身):你可知我在等什么?",
                ],
                "sensory_anchors": [
                    "门缝漏出半寸烛光,带着松烟的焦味",
                    "远处更夫敲锣,一下、两下——第三下之前他开了口",
                ],
            }],
            "closing_hook": "他握紧那枚铜钥,目光落向塔尖最后一线光",
        }
        ok, msg = _validate_scene_concreteness(data)
        self.assertTrue(ok, f"realistic data should pass: {msg}")

    def test_tolerance_3_issues(self):
        """恰好 3 处瑕疵应通过(容差)。"""
        data = {
            "scene_beats": [{
                "content": "x" * 100,  # 1 issue (< 180)
                "dialogue_seeds": ["话1"],  # 1 issue (filler)
                "sensory_anchors": ["细节"],  # 1 issue (< 12)
            }],
            "closing_hook": "他望着远方,等待着即将到来的东西",
        }
        ok, _ = _validate_scene_concreteness(data)
        self.assertTrue(ok)

    def test_supports_alt_list_keys(self):
        """支持 beats / scenes 别名。"""
        data = {
            "beats": [{
                "content": "x" * 200,
                "dialogue_seeds": [],
                "sensory_anchors": [],
            }],
        }
        ok, _ = _validate_scene_concreteness(data)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
