"""真实后端装配 —— 从 user_models.json 读取配置，接上真实 LLM 与 embedding。

默认读原项目 f:\\xiaoshuo\\user_models.json(可用环境变量 NOVEL_V2_MODELS 覆盖)。
该文件结构：{"models": [{id, base_url, api_key, model, usage:[...]}, ...]}
"""
from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

from . import llm
from .llm import OpenAIBackend
from .rag.embeddings import OpenAIEmbedder
from .rag.indexer import RagMemory
from .rag.vector_store import make_vector_store

DEFAULT_MODELS_PATH = os.environ.get("NOVEL_V2_MODELS", r"F:\xiaoshuo\user_models.json")


def load_models(path: Optional[str] = None) -> List[dict]:
    with open(path or DEFAULT_MODELS_PATH, encoding="utf-8") as f:
        return json.load(f)["models"]


def _pick(models: List[dict], usage: str) -> Optional[dict]:
    for m in models:
        if usage in m.get("usage", []):
            return m
    return None


_ROUTED_USAGES = ("main", "reviewer", "fallback", "extractor", "planner")


def configure_real(*, models_path: Optional[str] = None,
                   embed_model: str = "text-embedding-3-small",
                   embed_dim: int = 1536,
                   prefer_store: str = "auto",
                   use_llm_rerank: bool = False) -> Tuple[RagMemory, str]:
    """接真实后端：按用途注册多模型(Multi-Model Routing) + 返回真实 RagMemory。

    - 每个 usage(main/reviewer/fallback/…) 注册各自模型，写作走 main、重排走 reviewer
    - use_llm_rerank=True 时用 LLM-as-reranker(reviewer 模型)做精排
    返回 (rag, chat_model_name)。
    """
    models = load_models(models_path)
    main = _pick(models, "main") or models[0]
    for usage in _ROUTED_USAGES:
        m = _pick(models, usage) or (main if usage == "main" else None)
        if m:
            llm.set_backend(OpenAIBackend(base_url=m["base_url"], api_key=m["api_key"],
                                          model=m["model"]), usage)

    emb = OpenAIEmbedder(base_url=main["base_url"], api_key=main["api_key"],
                         model=embed_model, dim=embed_dim)
    reranker = None
    if use_llm_rerank:
        from .rag.rerank import LLMReranker
        reranker = LLMReranker(usage="reviewer")
    rag = RagMemory(embedder=emb,
                    store=make_vector_store(embed_dim, prefer=prefer_store),
                    reranker=reranker)
    return rag, main["model"]
