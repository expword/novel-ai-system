"""novel_v2 —— 可控长文生成系统的 LangGraph + RAG 重构核心。

设计目标(对标简历四大支柱)：
  1. 多智能体工作流编排   —— graph/ (LangGraph StateGraph + 自愈修订环 + interrupt)
  2. 多粒度向量记忆(RAG) —— rag/  (VectorStore 抽象 + FAISS 后端 + 完整 CRUD)
  3. 长任务可靠性         —— Checkpointer 断点续跑 / 质量门自愈循环
  4. Human-in-the-Loop    —— interrupt() 关键点暂停 + Command(resume) 原节点续跑

整个包自包含、可独立运行：默认 mock LLM / mock embedding，零 API key。
"""

__all__ = ["state", "llm", "rag", "graph"]
__version__ = "0.1.0"
