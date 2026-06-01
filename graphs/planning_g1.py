"""G1 意图组子图 —— 4 节点 + SqliteSaver checkpointer。

阶段 1 目标：验证 LangGraph 框架在本项目能跑通：
  · 节点顺序执行：-1 → 0 → 0.5 → 0.6 → END
  · state 自动持久化到 SQLite（每节点跑完都落盘）
  · 跑一半中断（杀进程）后用 thread_id resume 能从断点继续

阶段 2 起会在 G1 末尾加 interrupt_after，让用户在 web 上审产物再 resume 进 G2。
"""
from __future__ import annotations
from pathlib import Path

from langgraph.graph import StateGraph, START, END

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from state_v2 import NovelStateV2


def build_g1_graph(checkpointer=None, mock: bool = False):
    """构造 G1 子图。
    · checkpointer：调用方注入（SqliteSaver / MemorySaver）
    · mock=True：用 mock 节点（不调 v1 agent / LLM），用于验证框架机制
    """
    builder = StateGraph(NovelStateV2)

    if mock:
        from agents_v2.g1_mock_nodes import (
            mock_phase_minus1 as n_m1,
            mock_phase_0 as n_0,
            mock_phase_0_5 as n_05,
            mock_phase_0_6 as n_06,
        )
    else:
        from agents_v2.g1_nodes import (
            node_phase_minus1 as n_m1,
            node_phase_0 as n_0,
            node_phase_0_5 as n_05,
            node_phase_0_6 as n_06,
        )

    builder.add_node("phase_-1", n_m1)
    builder.add_node("phase_0", n_0)
    builder.add_node("phase_0.5", n_05)
    builder.add_node("phase_0.6", n_06)

    builder.add_edge(START, "phase_-1")
    builder.add_edge("phase_-1", "phase_0")
    builder.add_edge("phase_0", "phase_0.5")
    builder.add_edge("phase_0.5", "phase_0.6")
    builder.add_edge("phase_0.6", END)

    return builder.compile(checkpointer=checkpointer)
