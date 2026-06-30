"""系统流端到端:把系统引擎接进 LangGraph 写作循环，验证逐章面板演进。

证明同一张图,挂上委托系统配置就按"系统流"跑:每章经引擎裁决、入账、面板增长。
"""
import unittest

try:
    from langgraph.types import Command
    from novel_v2.graph import build_graph, make_checkpointer
    HAS_LANGGRAPH = True
except Exception:
    HAS_LANGGRAPH = False

from novel_v2.examples.delegation_system import build_spec
from novel_v2.rag.indexer import RagMemory
from novel_v2.state import initial_state
from novel_v2.system import PlayerState


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph 未安装")
class SystemFlowGraph(unittest.TestCase):
    def _run_system(self, target=3, thread="sys"):
        spec = build_spec()
        ps0 = PlayerState.initial(spec).to_dict()
        rag = RagMemory()
        graph = build_graph(rag, checkpointer=make_checkpointer())
        cfg = {"configurable": {"thread_id": thread}, "recursion_limit": 200}
        init = initial_state(volume_size=99, target_chapters=target,
                             system_spec=spec.to_dict(), player_state=ps0)
        res = graph.invoke(init, cfg)
        while True:
            snap = graph.get_state(cfg)
            if not snap.next:
                break
            res = graph.invoke(Command(resume="approve"), cfg)
        return res

    def test_panel_grows_each_chapter(self):
        res = self._run_system(target=3, thread="grow")
        ps = res["player_state"]
        self.assertEqual(ps["resources"]["信誉"], 60)      # 每章默认 +20，3 章 = 60
        self.assertEqual(len(ps["quests_done"]), 3)        # 完成 3 个单元
        self.assertEqual(ps["tier"], "市井")                # 未晋升(防通胀锚点)

    def test_system_events_accumulate(self):
        res = self._run_system(target=2, thread="ev")
        self.assertGreater(len(res["system_events"]), 0)

    def test_non_system_flow_unaffected(self):
        # 不带 spec → 系统节点透明，正常完成、面板为空
        rag = RagMemory()
        graph = build_graph(rag, checkpointer=make_checkpointer())
        cfg = {"configurable": {"thread_id": "plain"}, "recursion_limit": 200}
        res = graph.invoke(initial_state(volume_size=99, target_chapters=2), cfg)
        self.assertEqual([c["index"] for c in res["completed_chapters"]], [1, 2])
        self.assertFalse(res.get("player_state"))


if __name__ == "__main__":
    unittest.main()
