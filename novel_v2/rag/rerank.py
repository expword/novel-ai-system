"""Reranker —— 混合召回后的精排层。

粗召回(向量+BM25)快但糙；rerank 用更强的「query×候选」配对判分重排。
三种可插拔后端：
  · KeywordReranker —— 关键词重合度，离线零依赖(默认)
  · LLMReranker     —— 调 LLM 对每个候选判相关分(真实可用，走 reviewer 用途模型)
  · CrossEncoderReranker —— sentence-transformers CrossEncoder(可选，需装包)
"""
from __future__ import annotations

import re
from typing import List, Protocol

from .vector_store import SearchHit

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tok(t: str) -> set:
    return set(_TOKEN_RE.findall(t.lower()))


class Reranker(Protocol):
    def rerank(self, query: str, hits: List[SearchHit], top_k: int) -> List[SearchHit]: ...


class KeywordReranker:
    """离线默认：按 query 与候选文本的关键词重合度重排。"""

    def rerank(self, query: str, hits: List[SearchHit], top_k: int) -> List[SearchHit]:
        q = _tok(query)
        scored = []
        for h in hits:
            overlap = len(q & _tok(h.document)) / (len(q) or 1)
            scored.append((h.score + 0.5 * overlap, h))
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:top_k]]


class LLMReranker:
    """真实：让 LLM 给每个候选打 0-10 相关分(批量一次调用)，按分重排。

    用途模型默认 reviewer(轻量快)。失败时回退到候选原序。
    """

    def __init__(self, usage: str = "reviewer"):
        self.usage = usage

    def rerank(self, query: str, hits: List[SearchHit], top_k: int) -> List[SearchHit]:
        if not hits:
            return []
        from .. import llm
        listing = "\n".join(f"[{i}] {h.document[:200]}" for i, h in enumerate(hits))
        user = (f"问题：{query}\n\n候选片段：\n{listing}\n\n"
                f"为每个候选给出与问题的相关性分(0-10)，只输出形如 `编号:分数` 每行一个。")
        try:
            resp = llm.system_user("你是检索重排器，只输出编号与分数。", user,
                                   usage=self.usage, temperature=0)
            scores = {}
            for m in re.finditer(r"(\d+)\s*[:：]\s*(\d+(?:\.\d+)?)", resp):
                scores[int(m.group(1))] = float(m.group(2))
            ranked = sorted(range(len(hits)),
                            key=lambda i: -scores.get(i, hits[i].score))
            return [hits[i] for i in ranked[:top_k]]
        except Exception:
            return hits[:top_k]


class CrossEncoderReranker:
    """可选：sentence-transformers CrossEncoder(如 bge-reranker)。需装包+下模型。"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        from sentence_transformers import CrossEncoder  # 延迟导入
        self._ce = CrossEncoder(model_name)

    def rerank(self, query: str, hits: List[SearchHit], top_k: int) -> List[SearchHit]:
        if not hits:
            return []
        pairs = [(query, h.document) for h in hits]
        scores = self._ce.predict(pairs)
        order = sorted(range(len(hits)), key=lambda i: -scores[i])
        return [hits[i] for i in order[:top_k]]


def get_default_reranker() -> Reranker:
    return KeywordReranker()
