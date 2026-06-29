"""VectorStore 增删改查正确性 —— 只依赖 numpy，永远可跑。"""
import unittest

import numpy as np

from novel_v2.rag.vector_store import (
    NumpyVectorStore, VectorRecord, make_vector_store,
)


def _rec(i, text, **meta):
    return VectorRecord(id=i, document=text, metadata=meta)


class VectorStoreCRUD(unittest.TestCase):
    def _store(self):
        return NumpyVectorStore(dim=4)

    def _vecs(self, *rows):
        v = np.array(rows, dtype="float32")
        n = np.linalg.norm(v, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return v / n

    def test_add_and_search(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0], [0, 1, 0, 0]),
              [_rec("a", "x", ch=1), _rec("b", "y", ch=2)])
        self.assertEqual(s.count(), 2)
        hits = s.search(self._vecs([1, 0, 0, 0])[0], top_k=1)
        self.assertEqual(hits[0].id, "a")

    def test_add_is_idempotent_on_same_id(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0]), [_rec("a", "v1")])
        s.add(self._vecs([0, 1, 0, 0]), [_rec("a", "v2")])
        self.assertEqual(s.count(), 1)               # 不重复
        self.assertEqual(s.get(["a"])[0].document, "v2")

    def test_delete_by_id(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0], [0, 1, 0, 0]),
              [_rec("a", "x"), _rec("b", "y")])
        self.assertEqual(s.delete(ids=["a"]), 1)
        self.assertEqual(s.count(), 1)
        self.assertEqual(s.get(["b"])[0].id, "b")

    def test_delete_by_filter(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]),
              [_rec("a", "x", ch=1), _rec("b", "y", ch=2), _rec("c", "z", ch=2)])
        removed = s.delete(flt=lambda m: m.get("ch") == 2)
        self.assertEqual(removed, 2)
        self.assertEqual(s.count(), 1)

    def test_upsert_replaces_not_appends(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0]), [_rec("a", "old", ch=1)])
        s.upsert(self._vecs([0, 1, 0, 0]), [_rec("a", "new", ch=1)])
        self.assertEqual(s.count(), 1)
        self.assertEqual(s.get(["a"])[0].document, "new")

    def test_filter_membership_on_list_metadata(self):
        s = self._store()
        s.add(self._vecs([1, 0, 0, 0], [0, 1, 0, 0]),
              [_rec("a", "x", chars=["林川", "苏婉"]), _rec("b", "y", chars=["王越"])])
        hits = s.search(self._vecs([0, 1, 0, 0])[0], top_k=5, flt={"chars": "林川"})
        self.assertEqual([h.id for h in hits], ["a"])

    def test_factory_returns_working_store(self):
        s = make_vector_store(dim=4, prefer="auto")   # faiss 或 numpy 都行
        s.add(self._vecs([1, 0, 0, 0]), [_rec("a", "x")])
        self.assertEqual(s.count(), 1)
        self.assertEqual(s.delete(ids=["a"]), 1)


if __name__ == "__main__":
    unittest.main()
