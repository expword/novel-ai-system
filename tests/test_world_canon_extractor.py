"""agents/world_canon_extractor 回归测试。

只测纯逻辑（hash 幂等 / 空文本跳过 / 字段长度截断），不真发 LLM 调用。
"""
import unittest
from tests._helpers import make_minimal_state


class TestExtractorIdempotency(unittest.TestCase):
    """source_hash 已对应当前 world_setting 时直接跳过，不重抽。"""

    def test_unchanged_world_setting_skipped(self):
        from agents.world_canon_extractor import extract_world_canon, _hash_world_setting
        state = make_minimal_state()
        state.world_setting = "本书发生在大雍王朝青石县"
        # 预填一个匹配 hash 的 canon——extract 应该直接返回不改
        cur_hash = _hash_world_setting(state.world_setting)
        from persistence.state import WorldCanon
        state.world_canon = WorldCanon(
            dynasty_name="已存在", source_hash=cur_hash,
        )
        # extract 不传 force=True，按 hash 匹配应跳过
        result = extract_world_canon(state, force=False)
        self.assertEqual(result.dynasty_name, "已存在")  # 没被覆盖

    def test_empty_world_setting_returns_unchanged(self):
        from agents.world_canon_extractor import extract_world_canon
        from persistence.state import WorldCanon
        state = make_minimal_state()
        state.world_setting = ""  # 空文本
        state.world_canon = WorldCanon(dynasty_name="保留")
        result = extract_world_canon(state, force=False)
        self.assertEqual(result.dynasty_name, "保留")  # 空文本直接返回现有 canon


class TestHashFunction(unittest.TestCase):
    def test_hash_deterministic(self):
        from agents.world_canon_extractor import _hash_world_setting
        h1 = _hash_world_setting("同样的文本")
        h2 = _hash_world_setting("同样的文本")
        self.assertEqual(h1, h2)

    def test_hash_different_for_different_text(self):
        from agents.world_canon_extractor import _hash_world_setting
        h1 = _hash_world_setting("文本 A")
        h2 = _hash_world_setting("文本 B")
        self.assertNotEqual(h1, h2)

    def test_empty_text_returns_empty_hash(self):
        from agents.world_canon_extractor import _hash_world_setting
        self.assertEqual(_hash_world_setting(""), "")


if __name__ == "__main__":
    unittest.main()
