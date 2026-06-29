# novel_v2 —— 可控长文生成系统(LangGraph + RAG 重构)

把原 `xiaoshuo` 项目手搓的编排/记忆/续跑，重构到 **LangGraph + 向量 RAG** 上的可运行 PoC。
聚焦简历最不可压缩的三块工程能力，**零 API key、零外部服务即可跑通**。

## 快速开始

```bash
pip install -r requirements.txt        # numpy 必需；faiss/langgraph 可选(缺失自动降级)
python -m novel_v2.demo                 # 离线 mock 演示(零 API key)
python -m unittest discover -s tests    # 27 个测试(离线；Milvus 需 pymilvus 否则自动跳过)
```

## 真实生成(接你自己的 API)

读 `user_models.json`(默认 `f:\xiaoshuo\user_models.json`，或环境变量 `NOVEL_V2_MODELS`)，
用其中 `usage` 含 `main` 的 OpenAI 兼容模型写正文 + `text-embedding-3-small` 做向量：

```bash
python -m novel_v2.run --title 代码修仙 --chapters 3 --auto-approve \
    --premise "现代程序员穿越修仙世界，用工程思维在宗门崛起"
# 正文逐章写入 output/<title>/chapter_XXXX.txt；RAG 跨章召回维持连贯
```

实测：deepseek-chat(via yunwu) + text-embedding-3-small + FaissVectorStore，
逐章生成真实正文并落盘，第 N 章自动召回前 N-1 章记忆保持连贯。

> demo/测试默认 mock(离线确定性)；run 用真实后端。两者共用同一套 graph 与 RAG。

## 简历技术栈 → 落地对照(全部已实现)

| 简历技术栈 | 落地 | 文件 |
|---|---|---|
| LangGraph(StateGraph DAG) | ✅ retrieve→plan→write→canon→critic→**revise 自愈环**→finalize | `graph/` |
| Multi-Agent Orchestration | ✅ 各节点=单一职责 agent，条件路由 + 循环 | `graph/nodes.py` |
| Human-in-the-Loop(interrupt + Checkpointer) | ✅ 卷级 `interrupt()` 暂停 + `Command(resume)` 原节点续跑 | `graph/writing_loop.py` |
| **Multi-Vector RAG** | ✅ 正文 / 摘要 / **实体** 多粒度向量 | `rag/indexer.py` |
| **Hybrid Retrieval(Vector + KG + BM25)** | ✅ 向量+BM25 **RRF 融合** + Canon-KG 确定性证据 | `indexer.py`、`bm25.py`、`canon_kg.py` |
| **Cross-Encoder Rerank** | ✅ Keyword(离线)/**LLM-as-reranker**/CrossEncoder 可插拔 | `rag/rerank.py` |
| **Milvus** | ✅ `MilvusVectorStore`(pymilvus)，工厂 `prefer="milvus"`；本地默认 FAISS | `rag/vector_store.py` |
| **Multi-Model Routing** | ✅ 按用途(main/reviewer/fallback)路由 + 失败 fallback | `llm.py`、`config.py` |
| 完整 CRUD / 防记忆污染 | ✅ write→insert / revise→upsert / 回滚→delete | `rag/indexer.py` |

> 全部能力 **离线 mock 可跑(27 个测试) + 真实 API 实测通过**(deepseek-chat 写作 / gemini 重排 / text-embedding-3-small / FAISS)。
>
> 检索链路:`query →` 向量语义召回 + BM25 精确词召回 `→ RRF 融合 →` Reranker 精排 `+` Canon-KG 确定性证据 `→` 注入 prompt。

## 架构

```
StateGraph(NovelState: TypedDict + reducer)
  START → retrieve → plan → write → canon → critic ─┐
                       ▲                            │ route_after_critic
                       └──────── revise ◀───────────┘ critical 未清零→自愈重写
  finalize ─ route_after_finalize ─→ END / gate[interrupt 人审] / 下一章
                       │
                       └─ RagMemory: write→insert / revise→upsert / 定稿→index 摘要
```

- **状态传递**：节点不 mutate，只返回增量 dict，reducer 合并；每超步被 Checkpointer 快照
- **CRUD 为何刚需**：小说边写边被修订循环重写，向量库必须删旧增新，否则旧剧情被召回→与正文矛盾

## 可插拔后端(换真实环境)

```python
from novel_v2 import llm
from novel_v2.rag.embeddings import OpenAIEmbedder
from novel_v2.rag.indexer import RagMemory
from novel_v2.rag.vector_store import make_vector_store

# 真实 LLM
llm.set_backend(llm.OpenAIBackend(base_url="...", api_key="...", model="..."))

# 真实 embedding + FAISS(以后换 Milvus 只在 make_vector_store 加一个后端分支)
emb = OpenAIEmbedder(base_url="...", api_key="...")
rag = RagMemory(embedder=emb, store=make_vector_store(emb.dim, prefer="faiss"))
```

跨进程断点续跑用 SqliteSaver：`build_graph(rag, checkpointer=make_checkpointer("novel.db"))`。

## 与原项目的关系

原 `f:\xiaoshuo`(~3万行/86 agent)是手搓编排；本 PoC 只迁「写作循环 + RAG 记忆 + HITL + 续跑」
这一最有价值的核心闭环，证明可落地。把 86 个 agent 逐步 node 化即可扩展为完整系统。
