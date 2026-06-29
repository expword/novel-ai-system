"""Embedder —— 文本向量化，可插拔。默认 mock(确定性、离线、零依赖)。

mock 用 hashing-trick 把词散列进固定维度并归一化：同样的文本永远得到同样的向量，
语义相近(共享词)的文本向量也相近，足以让 demo 的召回行为稳定可验证。
真实场景换 OpenAIEmbedder 或 sentence-transformers 即可，接口不变。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import List, Protocol

import numpy as np

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


class Embedder(Protocol):
    dim: int
    def embed(self, texts: List[str]) -> np.ndarray: ...


class MockEmbedder:
    """确定性 hashing embedder。无需任何模型或网络。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> List[str]:
        # 中文按字、英文/数字按词 —— 与 Outline-based chunking 的语义单元粒度一致
        return _TOKEN_RE.findall(text.lower())

    def embed(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            for tok in self._tokens(text):
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 8) & 1 else -1.0
                out[i, idx] += sign
            norm = math.sqrt(float(np.dot(out[i], out[i]))) or 1.0
            out[i] /= norm   # L2 归一化 → 内积即余弦相似度
        return out


class OpenAIEmbedder:
    """真实 embedding 后端(OpenAI 兼容)。需 `pip install openai`。"""

    def __init__(self, *, base_url: str, api_key: str,
                 model: str = "text-embedding-3-small", dim: int = 1536):
        from openai import OpenAI
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model
        self.dim = dim

    def embed(self, texts: List[str]) -> np.ndarray:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        vecs = np.array([d.embedding for d in resp.data], dtype="float32")
        # 归一化，保持与 IndexFlatIP / 余弦的一致语义
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


_default: Embedder | None = None


def get_default_embedder() -> Embedder:
    global _default
    if _default is None:
        _default = MockEmbedder()
    return _default
