"""轻量 BM25 —— 三路混合检索里的「精确词」一路(专有名词/人名地名补位)。

纯 Python 自实现(零依赖)。小语料每次查询重算 idf 足够快。
中文按字、英文/数字按词切分，与向量侧切词一致。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


def _tok(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._docs: Dict[str, List[str]] = {}      # id -> tokens
        self._len: Dict[str, int] = {}             # id -> length

    def add(self, doc_id: str, text: str) -> None:
        toks = _tok(text)
        self._docs[doc_id] = toks
        self._len[doc_id] = len(toks)

    def remove(self, doc_id: str) -> None:
        self._docs.pop(doc_id, None)
        self._len.pop(doc_id, None)

    def __len__(self) -> int:
        return len(self._docs)

    def _idf(self, term: str, n_docs: int) -> float:
        df = sum(1 for toks in self._docs.values() if term in toks)
        if df == 0:
            return 0.0
        return math.log(1 + (n_docs - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        if not self._docs:
            return []
        n = len(self._docs)
        avgdl = (sum(self._len.values()) / n) or 1.0
        q_terms = set(_tok(query))
        idf = {t: self._idf(t, n) for t in q_terms}
        scored: List[Tuple[str, float]] = []
        for did, toks in self._docs.items():
            if not toks:
                continue
            dl = self._len[did]
            s = 0.0
            for t in q_terms:
                if idf[t] == 0:
                    continue
                tf = toks.count(t)
                if tf == 0:
                    continue
                s += idf[t] * (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / avgdl))
            if s > 0:
                scored.append((did, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]
