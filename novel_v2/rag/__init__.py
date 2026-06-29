"""rag —— 多粒度向量记忆与混合检索。

模块：
  embeddings   —— Embedder 抽象 + mock(确定性) / OpenAI 真实后端
  vector_store —— VectorStore 抽象 + FAISS / Numpy 后端，完整增删改查(CRUD)
  indexer      —— 把正文/摘要切块、多粒度入库；修订/回滚时删旧增新
  retriever    —— 写章前的 filtered ANN + 轻量混合召回
"""
from .embeddings import Embedder, MockEmbedder, get_default_embedder
from .vector_store import (VectorStore, VectorRecord, make_vector_store,
                          NumpyVectorStore, FaissVectorStore, MilvusVectorStore)
from .bm25 import BM25
from .canon_kg import CanonKG
from .rerank import (Reranker, KeywordReranker, LLMReranker,
                     CrossEncoderReranker, get_default_reranker)
from .indexer import RagMemory

__all__ = [
    "Embedder", "MockEmbedder", "get_default_embedder",
    "VectorStore", "VectorRecord", "make_vector_store",
    "NumpyVectorStore", "FaissVectorStore", "MilvusVectorStore",
    "BM25", "CanonKG",
    "Reranker", "KeywordReranker", "LLMReranker", "CrossEncoderReranker",
    "get_default_reranker",
    "RagMemory",
]
