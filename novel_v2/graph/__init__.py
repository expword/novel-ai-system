"""graph —— LangGraph 写作循环编排。

writing_loop.build_graph() 返回一张编译好的 StateGraph：
  retrieve → directive → write → canon → critic →┐
                            ▲                     │(条件)
                            └──── revise ◀────────┘ critical 未清零则自愈重写
  finalize →(条件)→ volume_gate[interrupt] / 下一章 / END
"""
from .writing_loop import build_graph, make_checkpointer

__all__ = ["build_graph", "make_checkpointer"]
