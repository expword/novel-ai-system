"""叙事引擎接进 graph 的测试:揭晓章注入真相+灵魂角色、普通章不注入、不破坏写作循环。"""
import unittest

from novel_v2.graph.nodes import _narrative_block, _narrative_for_chapter, directive_node
from novel_v2.examples.delegation_narrative import build_bible

try:
    from novel_v2.graph import build_graph, make_checkpointer
    from novel_v2.rag.indexer import RagMemory
    from novel_v2.state import initial_state
    HAS_LANGGRAPH = True
except Exception:
    HAS_LANGGRAPH = False


class TestNarrativeInjection(unittest.TestCase):
    def setUp(self):
        self.bible = build_bible()

    def _st(self, ci):
        return {"current_chapter_index": ci, "debug_inject_flaw": False}

    def test_reveal_chapter_injects_truth_and_soul(self):
        narr = directive_node(self._st(80), bible=self.bible)["directive"]["narrative"]
        self.assertIsNotNone(narr["reveal"])
        self.assertEqual(narr["reveal"]["reveal_layers"][0]["level"], 2)
        self.assertIn("张铁山", narr["reveal"]["reveal_souls"])

    def test_ordinary_chapter_no_reveal(self):
        narr = directive_node(self._st(45), bible=self.bible)["directive"]["narrative"]
        self.assertIsNone(narr["reveal"])

    def test_souls_injected_after_introduced(self):
        narr = directive_node(self._st(5), bible=self.bible)["directive"]["narrative"]
        self.assertIn("张铁山", [s["name"] for s in narr["souls"]])

    def test_narrative_block_renders(self):
        block = _narrative_block(_narrative_for_chapter(self.bible, 80))
        self.assertIn("重大反转", block)
        self.assertIn("张铁山", block)
        self.assertIn("创伤", block)

    def test_no_bible_no_narrative(self):
        out = directive_node(self._st(1), bible=None)
        self.assertNotIn("narrative", out["directive"])


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph 未安装")
class TestNarrativeGraph(unittest.TestCase):
    def test_graph_runs_with_narrative(self):
        bible = build_bible()
        rag = RagMemory()
        graph = build_graph(rag, checkpointer=make_checkpointer(), narrative=bible)
        cfg = {"configurable": {"thread_id": "narr"}, "recursion_limit": 200}
        res = graph.invoke(initial_state(volume_size=99, target_chapters=2), cfg)
        self.assertEqual([c["index"] for c in res["completed_chapters"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
