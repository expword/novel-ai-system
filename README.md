# Novel AI System

> 基于 **LangGraph** 与向量 **RAG** 的可控长篇小说生成系统

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-27%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

把「生成一部跨数百章、设定前后一致的长篇小说」建模为一条可编排、可观测、可中断恢复的流水线：每章生成前从记忆中召回相关前情，生成后经设定检查与多维审校，必要时自动修订，并在关键节点暂停人审。

**零依赖即可离线试跑**（内置 mock LLM 与 mock embedding），接任意 OpenAI 兼容 API 即可真实生成。

## ✨ 特性

- **工作流编排** —— LangGraph `StateGraph` 把每个环节建成图节点：`retrieve → plan → write → canon → critic → revise(自愈环) → finalize`，条件路由 + 循环。
- **多粒度 RAG 记忆** —— 对每章并行建「正文块 / 摘要 / 实体」多种向量。
- **三路混合检索** —— 向量语义 + BM25 精确词 经 **RRF 融合**召回，叠加 Canon-KG 结构化设定的确定性证据，最后 **Reranker** 精排。
- **可插拔向量后端** —— FAISS / Numpy / Milvus，工厂统一接口，缺失自动降级。
- **完整 CRUD 记忆** —— 写入 `insert`、修订 `upsert`、回滚 `delete`，保证检索记忆与最新正文一致，杜绝过期内容污染。
- **Human-in-the-Loop** —— `interrupt()` 在关键节点暂停人审、`Command(resume)` 原节点续跑；Checkpointer 支持断点续跑。
- **多模型路由** —— 按用途（写作 / 审校 / 兜底）分流，主模型失败自动 fallback。
- **后端可插拔** —— LLM 与 embedding 均抽象，mock（离线确定性）与 OpenAI 兼容自由切换。

## 🏗 架构

```
StateGraph(NovelState: TypedDict + reducer)
  START → retrieve → plan → write → canon → critic ─┐
                       ▲                            │ route_after_critic
                       └──────── revise ◀───────────┘ critical 未清零 → 自愈重写
  finalize ─ route_after_finalize ─→ END / gate[interrupt 人审] / 下一章

RagMemory   write → insert     revise → upsert(删旧增新)     回滚 → delete
检索流程     query → 向量召回 + BM25 召回 → RRF 融合 → Reranker 精排
                                            + Canon-KG 确定性证据 → 注入 prompt
```

- **状态传递**：节点不 mutate，只返回增量 dict，reducer 合并；每个超步被 Checkpointer 快照。
- **为何要完整 CRUD**：正文会被修订循环重写，向量记忆必须删旧增新，否则旧版本内容被召回，与最新正文矛盾。

## 📦 安装

```bash
git clone -b langgraph-v2 https://github.com/expword/novel-ai-system.git
cd novel-ai-system
pip install -r requirements.txt
```

依赖说明：`numpy` 必需；`faiss-cpu` / `langgraph` 可选（向量库缺失会自动降级，图功能需 langgraph）；`pymilvus` 仅在使用 Milvus 后端时需要。

## 🚀 快速开始

**离线演示**（零 API key，mock 后端）：

```bash
python -m novel_v2.demo
```

**运行测试**：

```bash
python -m unittest discover -s tests        # 27 个测试，全部离线
```

**真实生成**（接你的模型，见下方配置）：

```bash
python -m novel_v2.run --title 代码修仙 --chapters 3 --auto-approve \
    --premise "现代程序员穿越修仙世界，用工程思维在宗门崛起"
# 正文逐章写入 output/<title>/chapter_XXXX.txt
```

常用参数：`--chapters N`、`--volume-size N`（每卷章数，到卷尾触发人审）、`--auto-approve`（人审自动通过）、`--sqlite novel.db`（跨进程断点续跑）。

## ⚙️ 配置真实模型

复制模板并填入你的 OpenAI 兼容 API：

```bash
cp user_models.example.json user_models.json   # 编辑 base_url / api_key / model
```

或用环境变量指定路径：`export NOVEL_V2_MODELS=/path/to/user_models.json`。

配置文件按 `usage` 标签路由模型：

```json
{
  "models": [
    {"id": "main",     "base_url": "...", "api_key": "...", "model": "...", "usage": ["main", "planner", "fallback"]},
    {"id": "reviewer", "base_url": "...", "api_key": "...", "model": "...", "usage": ["reviewer"]}
  ]
}
```

| usage | 用途 |
|---|---|
| `main` | 写正文 / 修订 / 摘要 |
| `reviewer` | LLM-as-reranker、审校（可用更轻更快的模型） |
| `fallback` | 主模型调用失败时兜底 |

> `user_models.json` 已在 `.gitignore` 中，不会被提交。

## 🧩 切换向量后端

```python
from novel_v2.rag.vector_store import make_vector_store

store = make_vector_store(dim=1536, prefer="faiss")    # FAISS（默认）
store = make_vector_store(dim=1536, prefer="numpy")    # 纯 numpy，零依赖
store = make_vector_store(dim=1536, prefer="milvus",   # Milvus，不可用时自动降级
                          uri="http://localhost:19530")
```

业务只依赖 `VectorStore` 抽象接口，换后端无需改动业务代码。

## 🗂 项目结构

```
novel_v2/
├── graph/
│   ├── writing_loop.py   StateGraph 组装 + Checkpointer
│   └── nodes.py          各节点（单一职责）+ 条件路由
├── rag/
│   ├── vector_store.py   VectorStore 抽象 + FAISS / Numpy / Milvus
│   ├── indexer.py        多粒度索引 + 三路混合检索 + 重排
│   ├── bm25.py           轻量 BM25
│   ├── canon_kg.py       设定知识图谱（实体 / 关系 / 伏笔）
│   ├── rerank.py         Reranker（Keyword / LLM / CrossEncoder）
│   └── embeddings.py     Embedder（mock / OpenAI）
├── llm.py                LLM 抽象 + 多模型路由
├── config.py             从 user_models.json 装配真实后端
├── state.py              NovelState（TypedDict + reducer）
├── run.py                真实生成入口
└── demo.py               离线演示
tests/                    27 个测试
```

## 🧪 测试

```bash
python -m unittest discover -s tests
```

全部离线（mock 后端，确定性），无需 API key。Milvus 相关测试在未安装 `pymilvus` 时自动跳过。

## 🛣 路线图

- [ ] Canon-KG 从正文自动抽取实体与关系
- [ ] 更多 Reranker / Embedder 后端
- [ ] 检索质量评测脚本（Recall@k）
- [ ] Web UI

## 📄 License

MIT
