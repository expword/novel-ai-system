"""新增能力测试:BM25 / Canon-KG / Rerank / 多粒度 / 三路混合 / 多模型路由 / Milvus 接口。"""
import unittest

from novel_v2 import llm
from novel_v2.rag.bm25 import BM25
from novel_v2.rag.canon_kg import CanonKG
from novel_v2.rag.indexer import RagMemory
from novel_v2.rag.rerank import KeywordReranker, LLMReranker
from novel_v2.rag.vector_store import SearchHit, make_vector_store


class TestBM25(unittest.TestCase):
    def test_exact_term_hits(self):
        b = BM25()
        b.add("d1", "独孤求败 在 剑冢 留下 剑法")
        b.add("d2", "林川 在 青崖宗 修炼 引气")
        hits = b.search("独孤求败 剑法", top_k=2)
        self.assertEqual(hits[0][0], "d1")

    def test_remove(self):
        b = BM25(); b.add("d1", "独孤求败"); b.add("d2", "林川")
        b.remove("d1")
        self.assertEqual(len(b), 1)
        self.assertTrue(all(did != "d1" for did, _ in b.search("独孤求败")))


class TestCanonKG(unittest.TestCase):
    def test_query_returns_evidence(self):
        kg = CanonKG()
        kg.add_entity("林川", "角色", 境界="练气三层")
        kg.add_relation("林川", "师父", "玄机子")
        kg.add_foreshadow("f1", "残破剑诀来历未明")
        ev = kg.query("林川 此刻在做什么")
        self.assertTrue(any("林川" in e and "师父" in e for e in ev))
        self.assertTrue(any("未回收伏笔" in e for e in ev))

    def test_resolve_foreshadow(self):
        kg = CanonKG(); kg.add_foreshadow("f1", "x")
        kg.resolve_foreshadow("f1")
        self.assertNotIn("未回收伏笔", "".join(kg.query("任意")))


class TestRerank(unittest.TestCase):
    def _hits(self):
        return [SearchHit("a", 0.1, "林川 引气 入体", {}),
                SearchHit("b", 0.1, "苏婉 在 藏经阁", {})]

    def test_keyword_rerank_prefers_overlap(self):
        out = KeywordReranker().rerank("林川 引气", self._hits(), top_k=2)
        self.assertEqual(out[0].id, "a")


class TestMultiGranularityAndHybrid(unittest.TestCase):
    def setUp(self):
        self.rag = RagMemory()
        self.rag.index_chapter(index=1, volume_index=1, title="第1章",
                               prose="林川在青崖宗引气入体。苏婉递来一卷独孤求败的剑诀。",
                               summary="林川入门得剑诀", characters=["林川", "苏婉"])

    def test_entity_granularity_indexed(self):
        ids = list(self.rag._corpus.keys())
        self.assertIn("ch1:entity:林川", ids)
        self.assertIn("ch1:entity:苏婉", ids)

    def test_bm25_recalls_proper_noun(self):
        # 专有名词精确命中(三路里 BM25 一路补位)
        hits = self.rag.retrieve("独孤求败 剑诀", top_k=3)
        self.assertTrue(any("独孤求败" in h.document for h in hits))

    def test_build_context_includes_kg_evidence(self):
        kg = CanonKG()
        kg.add_entity("林川", "角色", 境界="练气一层")
        kg.add_relation("林川", "师父", "玄机子")
        rag = RagMemory(kg=kg)
        rag.index_chapter(index=1, volume_index=1, title="第1章",
                          prose="林川在山上修炼。", characters=["林川"])
        ctx = rag.build_context("林川 修炼", exclude_index=99)
        self.assertIn("【设定】", ctx)
        self.assertIn("师父", ctx)


class TestMultiModelRouting(unittest.TestCase):
    class _Tagged:
        def __init__(self, tag, fail=False, payload=None):
            self.tag, self.fail, self.payload = tag, fail, payload
        def complete(self, system, user, *, temperature, max_tokens):
            if self.fail:
                raise RuntimeError("boom")
            return self.payload if self.payload is not None else f"[{self.tag}]"

    def tearDown(self):
        llm.reset_backends()   # 不污染其它测试的离线 mock 默认

    def test_routes_by_usage(self):
        llm.reset_backends()
        llm.set_backend(self._Tagged("main"), "main")
        llm.set_backend(self._Tagged("reviewer"), "reviewer")
        self.assertEqual(llm.system_user("s", "u", usage="reviewer"), "[reviewer]")
        self.assertEqual(llm.system_user("s", "u", usage="main"), "[main]")
        self.assertEqual(llm.system_user("s", "u", usage="unknown"), "[main]")  # 回退 main

    def test_fallback_on_failure(self):
        llm.reset_backends()
        llm.set_backend(self._Tagged("main", fail=True), "main")
        llm.set_backend(self._Tagged("fb"), "fallback")
        self.assertEqual(llm.system_user("s", "u", usage="main"), "[fb]")

    def test_llm_reranker_uses_scores(self):
        llm.reset_backends()
        llm.set_backend(self._Tagged("rev", payload="0:2\n1:9"), "reviewer")
        hits = [SearchHit("a", 0.5, "doc a", {}), SearchHit("b", 0.5, "doc b", {})]
        out = LLMReranker(usage="reviewer").rerank("q", hits, top_k=2)
        self.assertEqual(out[0].id, "b")   # 候选1分更高 -> 排前


class TestMilvusInterface(unittest.TestCase):
    def test_factory_milvus_branch_importable(self):
        # pymilvus 未装时跳过;装了则真实建 store
        try:
            import pymilvus  # noqa: F401
        except Exception:
            self.skipTest("pymilvus 未装")
        try:
            s = make_vector_store(4, prefer="milvus", uri="./_test_milvus.db")
        except Exception as e:
            self.skipTest(f"milvus 本地不可用(Windows 无 lite): {e}")
        self.assertTrue(hasattr(s, "search"))


if __name__ == "__main__":
    unittest.main()
