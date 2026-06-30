"""build_graph —— 把节点组装成 LangGraph StateGraph 并编译。

模式开关：
  · stepwise=False(auto) —— 一气跑通，仅卷级 interrupt 处暂停
  · stepwise=True        —— 通过 interrupt_before 在更多节点前暂停(逐步审核)
"""
from __future__ import annotations

import functools
from typing import Optional

from langgraph.graph import END, START, StateGraph

from ..rag.indexer import RagMemory
from ..state import NovelState
from . import nodes


def build_graph(rag: RagMemory, checkpointer=None, *, stepwise: bool = False, narrative=None):
    def bind(fn):
        return functools.partial(fn, rag=rag)

    g = StateGraph(NovelState)
    g.add_node("retrieve", bind(nodes.retrieve_node))
    g.add_node("plan", functools.partial(nodes.directive_node, bible=narrative))
    g.add_node("write", bind(nodes.write_node))
    g.add_node("system", nodes.system_node)
    g.add_node("canon", functools.partial(nodes.canon_node, bible=narrative))
    g.add_node("critic", nodes.critic_node)
    g.add_node("revise", bind(nodes.revise_node))
    g.add_node("finalize", bind(nodes.finalize_node))
    g.add_node("gate", nodes.volume_gate_node)

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "plan")
    g.add_edge("plan", "write")
    g.add_edge("write", "system")
    g.add_edge("system", "canon")
    g.add_edge("canon", "critic")
    # 质量门：critical 未清零 → 自愈修订；否则定稿
    g.add_conditional_edges("critic", nodes.route_after_critic,
                            {"revise": "revise", "finalize": "finalize"})
    g.add_edge("revise", "system")       # 修订后重新过系统+canon —— 成环
    # 定稿后：结束 / 卷级人审 / 写下一章
    g.add_conditional_edges("finalize", nodes.route_after_finalize,
                            {"END": END, "gate": "gate", "retrieve": "retrieve"})
    g.add_edge("gate", "retrieve")

    interrupt_before = ["write"] if stepwise else None
    return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def make_checkpointer(sqlite_path: Optional[str] = None):
    """断点续跑用的 checkpointer。

    默认 MemorySaver(进程内即可演示 interrupt/resume)。
    传 sqlite_path 则用 SqliteSaver 做 **跨进程** 持久化(崩溃后另起进程按 thread_id 续跑)。
    """
    if sqlite_path:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            import sqlite3
            conn = sqlite3.connect(sqlite_path, check_same_thread=False)
            return SqliteSaver(conn)
        except Exception as e:  # noqa: BLE001
            print(f"[checkpointer] SqliteSaver 不可用({e})，降级 MemorySaver")
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
