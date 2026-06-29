"""novel_v2 —— 基于 LangGraph + RAG 的可控长篇小说生成系统核心包。

核心能力：
  1. 多智能体工作流编排   —— graph/ (LangGraph StateGraph + 自愈修订环 + interrupt)
  2. 多粒度向量记忆(RAG) —— rag/  (向量库抽象 + 三路混合检索 + 完整 CRUD)
  3. 长任务可靠性         —— Checkpointer 断点续跑 / 质量门自愈循环
  4. Human-in-the-Loop    —— interrupt() 关键点暂停 + Command(resume) 原节点续跑

整个包自包含、可独立运行：默认 mock LLM / mock embedding，零 API key。
"""

__all__ = ["state", "llm", "rag", "graph"]
__version__ = "0.1.0"
