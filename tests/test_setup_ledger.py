"""setup_ledger 回归测试。

覆盖:
  · find_callback_seeds 按 sp_type → SetupKind 映射 + 近期优先 + suggested_sp_id 精确匹配
  · format_callback_seeds_for_directive 字符串拼接
  · chapter_cleanup 删章时回滚 setup_ledger
  · _load_state 兼容旧 state.json 无 setup_ledger 字段

测试用 tests/_helpers.make_minimal_state,不发真 LLM 调用。
extract_setups_from_chapter 涉及 LLM,这里不测——靠纯逻辑函数验证关键路径。
"""
import unittest
from tests._helpers import make_minimal_state


class TestFindCallbackSeeds(unittest.TestCase):
    """find_callback_seeds 按 sp_type 匹配 + 近期优先 + 精确 sp_id。"""

    def _build_state_with_sp_and_ledger(self, sp_type, entries):
        """构造 state: 加 1 个 sp_id='sp1' + 多个 SetupEntry."""
        from persistence.state import SatisfactionPoint, SetupEntry
        state = make_minimal_state()
        state.satisfaction_points = [SatisfactionPoint(
            sp_id="sp1", sp_type=sp_type, title="测试爽点", description="",
            intensity=8, volume=1, target_chapter=20,
            setup_chain=[], payoff_description="",
        )]
        state.setup_ledger = entries
        return state

    def test_matches_kind_by_sp_type(self):
        """SLAP_FACE 应优先匹配 HUMILIATION/UNDERESTIMATION/REJECTION,不匹配 VOW。"""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            SetupEntry(entry_id="e1", chapter=3, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="反派A", quote="你这种废物",
                       scene_summary="酒楼当众被嘲"),
            SetupEntry(entry_id="e2", chapter=5, kind=SetupKind.VOW,
                       actor="主角", counterpart="", quote="",
                       scene_summary="主角立誓苦修"),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.SLAP_FACE, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=20)
        ids = [e.entry_id for e in result]
        self.assertIn("e1", ids)
        self.assertNotIn("e2", ids)

    def test_recent_priority(self):
        """同 kind 多条 entry,近期(gap<=30)排在远期前面。"""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            SetupEntry(entry_id="old", chapter=5, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="老旧台词",
                       scene_summary=""),
            SetupEntry(entry_id="new", chapter=80, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="B", quote="新台词",
                       scene_summary=""),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.SLAP_FACE, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=90)
        # new(gap=10) 应排在 old(gap=85) 前面
        self.assertEqual(result[0].entry_id, "new")

    def test_empty_when_no_pending(self):
        """所有 entry payoff_status=paid → 返回空."""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            SetupEntry(entry_id="e1", chapter=3, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="x", scene_summary="",
                       payoff_status="paid", callback_chapter=10),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.SLAP_FACE, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=20)
        self.assertEqual(result, [])

    def test_suggested_sp_id_exact_match_first(self):
        """suggested_sp_id 精确匹配的条目排在前面,即使其他条目更近期."""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            # 近期但没有 suggested_sp_id
            SetupEntry(entry_id="recent", chapter=18, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="近期", scene_summary=""),
            # 远期但精确匹配 sp1
            SetupEntry(entry_id="exact", chapter=2, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="B", quote="精确",
                       scene_summary="", suggested_sp_id="sp1"),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.SLAP_FACE, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=20)
        self.assertEqual(result[0].entry_id, "exact")

    def test_revelation_matches_any_kind(self):
        """REVELATION 的映射为空 → 任意 kind 都能匹配."""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            SetupEntry(entry_id="vow", chapter=3, kind=SetupKind.VOW,
                       actor="主角", counterpart="", quote="", scene_summary=""),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.REVELATION, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=20)
        self.assertEqual([e.entry_id for e in result], ["vow"])

    def test_excludes_future_chapters(self):
        """entry.chapter > current_chapter 的条目(数据异常)被排除."""
        from persistence.state import SetupEntry, SetupKind, SatisfactionType
        from agents.setup_ledger import find_callback_seeds
        entries = [
            SetupEntry(entry_id="future", chapter=30, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="x", scene_summary=""),
        ]
        state = self._build_state_with_sp_and_ledger(SatisfactionType.SLAP_FACE, entries)
        result = find_callback_seeds(state, "sp1", current_chapter=20)
        self.assertEqual(result, [])


class TestFormatCallbackSeeds(unittest.TestCase):
    """format_callback_seeds_for_directive 字符串拼接."""

    def test_full_format(self):
        from persistence.state import SetupEntry, SetupKind
        from agents.setup_ledger import format_callback_seeds_for_directive
        entries = [SetupEntry(
            entry_id="e1", chapter=5, kind=SetupKind.HUMILIATION,
            actor="主角", counterpart="反派A", quote="你这种废物也配",
            scene_summary="酒楼当众羞辱",
        )]
        out = format_callback_seeds_for_directive(entries)
        self.assertEqual(len(out), 1)
        # 必须包含 kind、章号、counterpart、quote、scene_summary 五要素
        self.assertIn("humiliation", out[0])
        self.assertIn("第5章", out[0])
        self.assertIn("反派A", out[0])
        self.assertIn("你这种废物也配", out[0])
        self.assertIn("酒楼当众羞辱", out[0])

    def test_no_quote_no_counterpart(self):
        from persistence.state import SetupEntry, SetupKind
        from agents.setup_ledger import format_callback_seeds_for_directive
        entries = [SetupEntry(
            entry_id="e1", chapter=2, kind=SetupKind.VOW,
            actor="主角", counterpart="", quote="", scene_summary="主角发誓苦修",
        )]
        out = format_callback_seeds_for_directive(entries)
        self.assertEqual(len(out), 1)
        self.assertIn("vow", out[0])
        self.assertIn("第2章", out[0])
        self.assertIn("主角发誓苦修", out[0])

    def test_empty_list(self):
        from agents.setup_ledger import format_callback_seeds_for_directive
        self.assertEqual(format_callback_seeds_for_directive([]), [])


class TestChapterCleanupSetupLedger(unittest.TestCase):
    """删章时 setup_ledger 的回滚逻辑."""

    def test_entry_created_in_deleted_chapter_removed(self):
        from persistence.state import SetupEntry, SetupKind
        from persistence.chapter_cleanup import cleanup_chapter_state
        state = make_minimal_state()
        state.setup_ledger = [
            SetupEntry(entry_id="e1", chapter=5, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="x", scene_summary=""),
            SetupEntry(entry_id="e2", chapter=10, kind=SetupKind.LOSS,
                       actor="主角", counterpart="B", quote="y", scene_summary=""),
        ]
        cleanup_chapter_state(state, to_delete=[5])
        ids = [e.entry_id for e in state.setup_ledger]
        self.assertNotIn("e1", ids)
        self.assertIn("e2", ids)

    def test_callback_in_deleted_chapter_reverts_to_pending(self):
        from persistence.state import SetupEntry, SetupKind
        from persistence.chapter_cleanup import cleanup_chapter_state
        state = make_minimal_state()
        state.setup_ledger = [
            SetupEntry(entry_id="e1", chapter=5, kind=SetupKind.HUMILIATION,
                       actor="主角", counterpart="A", quote="x", scene_summary="",
                       payoff_status="paid", callback_chapter=20,
                       callback_quote="还回去了"),
        ]
        # 删除第 20 章(callback 发生章)
        cleanup_chapter_state(state, to_delete=[20])
        # entry 本身保留(chapter=5 不在删除范围),但 callback 状态回退
        self.assertEqual(len(state.setup_ledger), 1)
        e = state.setup_ledger[0]
        self.assertEqual(e.payoff_status, "pending")
        self.assertEqual(e.callback_chapter, -1)
        self.assertEqual(e.callback_quote, "")


class TestCheckpointBackwardCompat(unittest.TestCase):
    """旧 state.json 无 setup_ledger 字段 → 加载为空 list."""

    def test_load_state_no_setup_ledger_key(self):
        from persistence.checkpoint import _load_setup_entry
        # 单条反序列化容错
        entry = _load_setup_entry({
            "entry_id": "e1", "chapter": 5, "kind": "humiliation",
            "actor": "主角", "counterpart": "", "quote": "", "scene_summary": "",
        })
        self.assertEqual(entry.entry_id, "e1")
        self.assertEqual(entry.payoff_status, "pending")
        self.assertEqual(entry.callback_chapter, -1)

    def test_load_setup_entry_invalid_kind_falls_back(self):
        """未知 kind 字符串 → 兜底到默认值,不抛异常."""
        from persistence.checkpoint import _load_setup_entry
        from persistence.state import SetupKind
        entry = _load_setup_entry({
            "entry_id": "e1", "chapter": 5, "kind": "garbage_kind",
            "actor": "主角",
        })
        # _enum 兜底:取第一个枚举值或返回默认
        self.assertIsInstance(entry.kind, SetupKind)


if __name__ == "__main__":
    unittest.main()
