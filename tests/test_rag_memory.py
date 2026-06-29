"""RagMemory 生命周期 —— 索引 / 召回 / 改写 upsert / 回滚 delete。"""
import unittest

from novel_v2.rag.indexer import RagMemory


class RagMemoryLifecycle(unittest.TestCase):
    def setUp(self):
        self.rag = RagMemory()
        self.rag.index_chapter(index=1, volume_index=1, title="第1章",
                               prose="林川引气入体，初窥练气。", summary="林川入门",
                               characters=["林川"])
        self.rag.index_chapter(index=2, volume_index=1, title="第2章",
                               prose="林川获得残破剑诀，剑光初成。", summary="林川得剑诀",
                               characters=["林川"])

    def test_indexed_multi_granularity(self):
        # 每章至少 prose + summary 两条
        self.assertGreaterEqual(self.rag.count(), 4)

    def test_retrieve_excludes_current_chapter(self):
        hits = self.rag.retrieve("剑诀 剑光", top_k=5, exclude_index=2)
        self.assertTrue(all(h.metadata["chapter_index"] != 2 for h in hits))

    def test_retrieve_finds_relevant(self):
        hits = self.rag.retrieve("练气 引气", top_k=2)
        self.assertTrue(any(h.metadata["chapter_index"] == 1 for h in hits))

    def test_reindex_is_upsert(self):
        before = self.rag.count()
        self.rag.reindex_chapter(index=2, volume_index=1, title="第2章",
                                 prose="改写：林川得的是刀法。", summary="改写",
                                 characters=["林川"])
        # 改写不应让总数翻倍(删旧增新)
        self.assertLessEqual(self.rag.count(), before + 1)
        hits = self.rag.retrieve("刀法", top_k=1)
        self.assertIn("刀", hits[0].document)

    def test_rollback_deletes_later_chapters(self):
        removed = self.rag.rollback_to(max_index=1)
        self.assertGreater(removed, 0)
        # 回滚后再召回不应出现第2章
        hits = self.rag.retrieve("剑诀", top_k=5)
        self.assertTrue(all(h.metadata["chapter_index"] <= 1 for h in hits))


if __name__ == "__main__":
    unittest.main()
