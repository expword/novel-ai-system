"""RagMemory —— RAG 记忆门面：多粒度索引 + 三路混合检索 + 重排 + 增删改查。

检索 = 向量(语义) + BM25(精确词) 两路 RRF 融合召回 → Reranker 精排，
再叠加 Canon-KG 的确定性证据，拼成可注入 prompt 的前情上下文。

生命周期：
  · index_chapter   写完一章 → 多粒度(正文块/摘要/实体)入向量库+BM25(insert)
  · retrieve        写下一章前 → 三路混合召回 + rerank
  · reindex_chapter 修订循环重写 → 删旧增新(upsert) ← 防记忆污染
  · rollback_to     回滚快照 → 删除游标之后的所有章(delete)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .bm25 import BM25
from .canon_kg import CanonKG
from .embeddings import Embedder, get_default_embedder
from .rerank import Reranker, get_default_reranker
from .vector_store import SearchHit, VectorRecord, VectorStore, make_vector_store

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")
_RRF_K = 60   # Reciprocal Rank Fusion 常数


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def _split_scenes(prose: str, max_len: int = 120) -> List[str]:
    sents = re.split(r"(?<=[。！？!?\n])", prose)
    chunks, buf = [], ""
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if len(buf) + len(s) > max_len and buf:
            chunks.append(buf); buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)
    return chunks or [prose]


def _entity_snippets(prose: str, names: List[str]) -> Dict[str, str]:
    """为每个角色抽「含其名的句子」拼成实体粒度文档。"""
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?\n])", prose) if s.strip()]
    out = {}
    for n in names:
        hit = [s for s in sents if n in s]
        if hit:
            out[n] = (n + "：" + "".join(hit))[:300]
    return out


class RagMemory:
    def __init__(self, embedder: Optional[Embedder] = None,
                 store: Optional[VectorStore] = None,
                 kg: Optional[CanonKG] = None,
                 reranker: Optional[Reranker] = None):
        self.embedder = embedder or get_default_embedder()
        self.store = store or make_vector_store(self.embedder.dim)
        self.kg = kg or CanonKG()
        self.reranker = reranker or get_default_reranker()
        self.bm25 = BM25()
        self._corpus: Dict[str, Tuple[str, dict]] = {}   # id -> (document, metadata)

    # ---- C / U：多粒度索引 ----
    def index_chapter(self, *, index: int, volume_index: int, title: str,
                      prose: str, summary: str = "",
                      characters: Optional[List[str]] = None) -> int:
        characters = characters or []
        records: List[VectorRecord] = []
        base = {"chapter_index": index, "volume_index": volume_index, "title": title}

        for i, chunk in enumerate(_split_scenes(prose)):
            records.append(VectorRecord(f"ch{index}:prose:{i}", chunk,
                           {**base, "granularity": "prose", "characters": characters}))
        if summary:
            records.append(VectorRecord(f"ch{index}:summary", summary,
                           {**base, "granularity": "summary", "characters": characters}))
        for name, snip in _entity_snippets(prose, characters).items():
            records.append(VectorRecord(f"ch{index}:entity:{name}", snip,
                           {**base, "granularity": "entity", "entity": name,
                            "characters": characters}))

        # 先删本章旧记录(upsert 幂等)，再写新
        self._forget(lambda m: m.get("chapter_index") == index)
        vectors = self.embedder.embed([r.document for r in records])
        self.store.add(vectors, records)
        for r in records:
            self.bm25.add(r.id, r.document)
            self._corpus[r.id] = (r.document, r.metadata)
        return len(records)

    def reindex_chapter(self, **kwargs) -> int:
        return self.index_chapter(**kwargs)

    # ---- D：删除 / 回滚 ----
    def _forget(self, pred) -> int:
        ids = [i for i, (_, m) in self._corpus.items() if pred(m)]
        if not ids:
            return 0
        self.store.delete(ids=ids)
        for i in ids:
            self.bm25.remove(i)
            self._corpus.pop(i, None)
        return len(ids)

    def delete_chapter(self, index: int) -> int:
        return self._forget(lambda m: m.get("chapter_index") == index)

    def rollback_to(self, max_index: int) -> int:
        return self._forget(lambda m: m.get("chapter_index", 0) > max_index)

    # ---- R：三路混合召回 + 重排 ----
    def retrieve(self, query: str, *, top_k: int = 4,
                 exclude_index: Optional[int] = None,
                 granularity: Optional[str] = None) -> List[SearchHit]:
        if not self._corpus:
            return []

        def ok(m: dict) -> bool:
            if exclude_index is not None and m.get("chapter_index") == exclude_index:
                return False
            if granularity is not None and m.get("granularity") != granularity:
                return False
            return True

        # 路1：向量语义
        qvec = self.embedder.embed([query])[0]
        vec_hits = self.store.search(qvec, top_k=top_k * 4, flt=ok)
        vec_map = {h.id: h for h in vec_hits}
        # 路2：BM25 精确词
        bm = [(i, s) for (i, s) in self.bm25.search(query, top_k * 4)
              if i in self._corpus and ok(self._corpus[i][1])]

        # RRF 融合两路排名
        rrf: Dict[str, float] = {}
        for r, h in enumerate(vec_hits):
            rrf[h.id] = rrf.get(h.id, 0.0) + 1.0 / (_RRF_K + r)
        for r, (i, _) in enumerate(bm):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (_RRF_K + r)

        cand: List[SearchHit] = []
        for hid in sorted(rrf, key=lambda k: -rrf[k])[: top_k * 3]:
            if hid in vec_map:
                cand.append(vec_map[hid])
            else:
                doc, meta = self._corpus[hid]
                cand.append(SearchHit(hid, rrf[hid], doc, meta))

        # 路3思路收口：Reranker 精排
        return self.reranker.rerank(query, cand, top_k)

    def build_context(self, query: str, **kw) -> str:
        """Canon-KG 确定性证据 + 混合召回片段，拼成前情上下文。"""
        lines: List[str] = []
        for ev in self.kg.query(query):          # KG 确定性证据优先
            lines.append(ev)
        for h in self.retrieve(query, **kw):
            ci = h.metadata.get("chapter_index")
            g = h.metadata.get("granularity")
            lines.append(f"[第{ci}章·{g}] {h.document}")
        return "\n".join(lines) if lines else "(暂无相关前情)"

    def count(self) -> int:
        return self.store.count()
