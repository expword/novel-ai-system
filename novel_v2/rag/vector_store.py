"""VectorStore —— 向量库抽象层 + 后端实现。

为什么要抽象层：业务只依赖 insert/query/upsert/delete 四个动作，
后端先用 **FAISS**(进程内、零服务、文件持久化)落地；以后换 Milvus 只新增一个
后端类、不动任何业务代码。make_vector_store() 自动选 FAISS，缺失则降级 Numpy。

为什么强调「完整 CRUD 而非只写」：小说是 **边写边被修订循环重写** 的语料。
若向量库只能 insert，被改掉的旧剧情会被反复召回、与最新正文自相矛盾 → 记忆污染。
所以 delete / upsert 是刚需，且要和 canon-revise、快照回滚联动。
"""
from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

# 元数据过滤器：等值 dict，或自定义 callable(metadata)->bool
Filter = Optional[object]


@dataclass
class VectorRecord:
    id: str
    document: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    id: str
    score: float
    document: str
    metadata: Dict[str, Any]


def _str_to_int64(s: str) -> int:
    """字符串 id → 稳定 int64(FAISS IDMap 需要整型 id)。"""
    h = hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big", signed=True)


def _match(metadata: Dict[str, Any], flt: Filter) -> bool:
    if flt is None:
        return True
    if callable(flt):
        return bool(flt(metadata))
    if isinstance(flt, dict):
        for k, v in flt.items():
            mv = metadata.get(k)
            if isinstance(mv, list):          # 标量 in 列表(如 角色 ∈ characters)
                if v not in mv:
                    return False
            elif mv != v:
                return False
        return True
    raise TypeError(f"不支持的 filter 类型: {type(flt)}")


class VectorStore(ABC):
    """四个动作 + 读取/计数/持久化。所有后端统一这套契约。"""

    dim: int

    @abstractmethod
    def add(self, vectors: np.ndarray, records: List[VectorRecord]) -> None: ...

    @abstractmethod
    def search(self, query: np.ndarray, top_k: int = 5, flt: Filter = None) -> List[SearchHit]: ...

    @abstractmethod
    def delete(self, ids: Optional[List[str]] = None, flt: Filter = None) -> int:
        """按 id 或按 metadata 过滤删除，返回删除条数。"""

    @abstractmethod
    def get(self, ids: List[str]) -> List[VectorRecord]: ...

    @abstractmethod
    def count(self) -> int: ...

    def upsert(self, vectors: np.ndarray, records: List[VectorRecord]) -> None:
        """改：先按 id 删除旧向量，再写新向量。delete+add 的原子语义封装。"""
        self.delete(ids=[r.id for r in records])
        self.add(vectors, records)

    def persist(self, path: str) -> None:  # 可选
        raise NotImplementedError

    @classmethod
    def load(cls, path: str, dim: int) -> "VectorStore":  # 可选
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 后端 1：NumpyVectorStore —— 纯 numpy，零额外依赖，永远可跑(默认兜底 + 测试用)
# --------------------------------------------------------------------------- #
class NumpyVectorStore(VectorStore):
    def __init__(self, dim: int):
        self.dim = dim
        self._vecs = np.zeros((0, dim), dtype="float32")
        self._records: List[VectorRecord] = []
        self._pos: Dict[str, int] = {}   # id -> 行号

    def add(self, vectors: np.ndarray, records: List[VectorRecord]) -> None:
        assert vectors.shape[0] == len(records)
        for v, r in zip(vectors, records):
            if r.id in self._pos:          # 同 id 直接覆盖(add 也具幂等性)
                self._vecs[self._pos[r.id]] = v
                self._records[self._pos[r.id]] = r
            else:
                self._pos[r.id] = len(self._records)
                self._records.append(r)
                self._vecs = np.vstack([self._vecs, v.reshape(1, -1)])

    def search(self, query: np.ndarray, top_k: int = 5, flt: Filter = None) -> List[SearchHit]:
        if len(self._records) == 0:
            return []
        sims = self._vecs @ query.reshape(-1)          # 归一化向量 → 内积=余弦
        order = np.argsort(-sims)
        hits: List[SearchHit] = []
        for idx in order:
            rec = self._records[idx]
            if not _match(rec.metadata, flt):
                continue
            hits.append(SearchHit(rec.id, float(sims[idx]), rec.document, rec.metadata))
            if len(hits) >= top_k:
                break
        return hits

    def delete(self, ids: Optional[List[str]] = None, flt: Filter = None) -> int:
        kill = set()
        if ids:
            kill |= {i for i in ids if i in self._pos}
        if flt is not None:
            kill |= {r.id for r in self._records if _match(r.metadata, flt)}
        if not kill:
            return 0
        keep = [i for i, r in enumerate(self._records) if r.id not in kill]
        self._vecs = self._vecs[keep] if keep else np.zeros((0, self.dim), dtype="float32")
        self._records = [self._records[i] for i in keep]
        self._pos = {r.id: i for i, r in enumerate(self._records)}
        return len(kill)

    def get(self, ids: List[str]) -> List[VectorRecord]:
        return [self._records[self._pos[i]] for i in ids if i in self._pos]

    def count(self) -> int:
        return len(self._records)

    def persist(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        np.save(path + ".vecs.npy", self._vecs)
        with open(path + ".meta.json", "w", encoding="utf-8") as f:
            json.dump([{"id": r.id, "document": r.document, "metadata": r.metadata}
                       for r in self._records], f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str, dim: int) -> "NumpyVectorStore":
        store = cls(dim)
        store._vecs = np.load(path + ".vecs.npy")
        with open(path + ".meta.json", encoding="utf-8") as f:
            raw = json.load(f)
        store._records = [VectorRecord(d["id"], d["document"], d["metadata"]) for d in raw]
        store._pos = {r.id: i for i, r in enumerate(store._records)}
        return store


# --------------------------------------------------------------------------- #
# 后端 2：FaissVectorStore —— IndexIDMap2(IndexFlatIP)，支持 remove_ids 删除
# --------------------------------------------------------------------------- #
class FaissVectorStore(VectorStore):
    def __init__(self, dim: int):
        import faiss
        self._faiss = faiss
        self.dim = dim
        self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        self._meta: Dict[int, VectorRecord] = {}     # int64 id -> record
        self._sid: Dict[str, int] = {}               # str id -> int64 id

    def add(self, vectors: np.ndarray, records: List[VectorRecord]) -> None:
        assert vectors.shape[0] == len(records)
        # 同 id 先删，保证 add 幂等(避免 IDMap 内出现重复 id)
        dup = [r.id for r in records if r.id in self._sid]
        if dup:
            self.delete(ids=dup)
        iids = np.array([_str_to_int64(r.id) for r in records], dtype="int64")
        self._index.add_with_ids(np.ascontiguousarray(vectors, dtype="float32"), iids)
        for iid, r in zip(iids.tolist(), records):
            self._meta[iid] = r
            self._sid[r.id] = iid

    def search(self, query: np.ndarray, top_k: int = 5, flt: Filter = None) -> List[SearchHit]:
        if self._index.ntotal == 0:
            return []
        # 有过滤时多取一些候选再后过滤(flat 索引无原生标量过滤)
        k = top_k * 5 if flt is not None else top_k
        k = min(k, self._index.ntotal)
        scores, iids = self._index.search(
            np.ascontiguousarray(query.reshape(1, -1), dtype="float32"), k)
        hits: List[SearchHit] = []
        for score, iid in zip(scores[0].tolist(), iids[0].tolist()):
            if iid == -1:
                continue
            rec = self._meta.get(iid)
            if rec is None or not _match(rec.metadata, flt):
                continue
            hits.append(SearchHit(rec.id, float(score), rec.document, rec.metadata))
            if len(hits) >= top_k:
                break
        return hits

    def delete(self, ids: Optional[List[str]] = None, flt: Filter = None) -> int:
        targets: List[int] = []
        if ids:
            targets += [self._sid[i] for i in ids if i in self._sid]
        if flt is not None:
            targets += [iid for iid, r in self._meta.items() if _match(r.metadata, flt)]
        targets = list(set(targets))
        if not targets:
            return 0
        self._index.remove_ids(np.array(targets, dtype="int64"))
        for iid in targets:
            rec = self._meta.pop(iid, None)
            if rec is not None:
                self._sid.pop(rec.id, None)
        return len(targets)

    def get(self, ids: List[str]) -> List[VectorRecord]:
        return [self._meta[self._sid[i]] for i in ids if i in self._sid]

    def count(self) -> int:
        return int(self._index.ntotal)

    def persist(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._faiss.write_index(self._index, path + ".faiss")
        with open(path + ".meta.json", "w", encoding="utf-8") as f:
            json.dump([{"iid": iid, "id": r.id, "document": r.document, "metadata": r.metadata}
                       for iid, r in self._meta.items()], f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str, dim: int) -> "FaissVectorStore":
        store = cls(dim)
        store._index = store._faiss.read_index(path + ".faiss")
        with open(path + ".meta.json", encoding="utf-8") as f:
            raw = json.load(f)
        for d in raw:
            rec = VectorRecord(d["id"], d["document"], d["metadata"])
            store._meta[d["iid"]] = rec
            store._sid[d["id"]] = d["iid"]
        return store


# --------------------------------------------------------------------------- #
# 后端 3：MilvusVectorStore —— 生产级向量库(pymilvus MilvusClient)
#   uri: 本地 "./xxx.db"(milvus-lite, 仅 Linux/Mac) 或远程 "http://host:19530"
#   metadata 存 JSON 动态字段；callable flt 走「取回后 Python 过滤」(同 FAISS)
# --------------------------------------------------------------------------- #
class MilvusVectorStore(VectorStore):
    def __init__(self, dim: int, uri: str = "./milvus_novel.db",
                 collection: str = "novel_mem"):
        from pymilvus import MilvusClient
        self.dim = dim
        self.collection = collection
        self._client = MilvusClient(uri)
        if not self._client.has_collection(collection):
            self._client.create_collection(
                collection_name=collection, dimension=dim, metric_type="IP",
                id_type="string", max_length=512, auto_id=False)

    def add(self, vectors: np.ndarray, records: List[VectorRecord]) -> None:
        rows = [{"id": r.id, "vector": v.tolist(),
                 "document": r.document, "metadata": r.metadata}
                for v, r in zip(vectors, records)]
        self._client.upsert(self.collection, rows)   # 原生 upsert，幂等

    def search(self, query: np.ndarray, top_k: int = 5, flt: Filter = None) -> List[SearchHit]:
        k = top_k * 5 if flt is not None else top_k
        res = self._client.search(self.collection, data=[query.tolist()], limit=k,
                                  output_fields=["document", "metadata"])
        hits: List[SearchHit] = []
        for r in (res[0] if res else []):
            ent = r.get("entity", {})
            meta = ent.get("metadata", {}) or {}
            if not _match(meta, flt):
                continue
            hits.append(SearchHit(str(r["id"]), float(r["distance"]),
                                  ent.get("document", ""), meta))
            if len(hits) >= top_k:
                break
        return hits

    def delete(self, ids: Optional[List[str]] = None, flt: Filter = None) -> int:
        targets = list(ids) if ids else []
        if flt is not None:
            allr = self._client.query(self.collection, filter="",
                                      output_fields=["id", "metadata"], limit=16384)
            targets += [r["id"] for r in allr if _match(r.get("metadata", {}) or {}, flt)]
        targets = list(set(targets))
        if not targets:
            return 0
        self._client.delete(self.collection, ids=targets)
        return len(targets)

    def get(self, ids: List[str]) -> List[VectorRecord]:
        rows = self._client.get(self.collection, ids=list(ids))
        return [VectorRecord(str(r["id"]), r.get("document", ""), r.get("metadata", {}) or {})
                for r in rows]

    def count(self) -> int:
        try:
            return len(self._client.query(self.collection, filter="",
                                          output_fields=["id"], limit=16384))
        except Exception:
            return 0


def make_vector_store(dim: int, prefer: str = "auto", **kw) -> VectorStore:
    """工厂：prefer="faiss"|"numpy"|"milvus"|"auto"。auto 优先 FAISS，缺失降级 Numpy。

    业务只依赖 VectorStore 契约，换后端不动业务代码。
    milvus 额外 kw：uri / collection。
    """
    if prefer == "milvus":
        try:
            return MilvusVectorStore(dim, **kw)
        except Exception:
            pass  # pymilvus 未装 / 本地 lite 不可用(如 Windows) → 自动降级
    if prefer in ("faiss", "auto", "milvus"):
        try:
            import faiss  # noqa: F401
            return FaissVectorStore(dim)
        except Exception:
            if prefer == "faiss":
                raise
    return NumpyVectorStore(dim)
