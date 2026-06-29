"""LangGraph 写作循环 —— 端到端跑通 + 自愈环 + interrupt/resume。

需要 langgraph；未安装则整类跳过。
"""
import unittest

try:
    from langgraph.types import Command
    from novel_v2.graph import build_graph, make_checkpointer
    HAS_LANGGRAPH = True
except Exception:  # noqa: BLE001
    HAS_LANGGRAPH = False

from novel_v2.rag.indexer import RagMemory
from novel_v2.state import initial_state


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph 未安装")
class WritingLoop(unittest.TestCase):
    def _run(self, target=5, volume_size=3, inject_flaw=False):
        rag = RagMemory()
        graph = build_graph(rag, checkpointer=make_checkpointer())
        cfg = {"configurable": {"thread_id": "t"}, "recursion_limit": 200}
        res = graph.invoke(
            initial_state(volume_size=volume_size, target_chapters=target,
                          debug_inject_flaw=inject_flaw), cfg)
        gates = 0
        while True:
            snap = graph.get_state(cfg)
            if not snap.next:
                break
            gates += 1
            res = graph.invoke(Command(resume="approve"), cfg)
        return rag, res, gates

    def test_completes_all_chapters(self):
        _, res, _ = self._run(target=5)
        self.assertEqual([c["index"] for c in res["completed_chapters"]],
                         [1, 2, 3, 4, 5])

    def test_volume_gate_interrupts(self):
        # 5 章 / 每卷 3 章 → 第3章后触发一次卷级 HITL
        _, _, gates = self._run(target=5, volume_size=3)
        self.assertEqual(gates, 1)

    def test_self_heal_revise_loop_ran(self):
        # 第2章初稿故意违规 → 日志里应有 revise 记录，且终稿无占位符
        rag, res, _ = self._run(target=2, volume_size=99, inject_flaw=True)
        self.assertTrue(any("revise" in line for line in res["event_log"]))
        from novel_v2.llm import MockBackend
        ch2 = [c for c in res["completed_chapters"] if c["index"] == 2][0]
        # 终稿摘要不含占位符
        self.assertNotIn(MockBackend.FORBIDDEN, ch2["summary"])


if __name__ == "__main__":
    unittest.main()
