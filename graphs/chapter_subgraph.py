"""章级写作子图——含 critic 审校 cycle。

拓扑：

       START
         │
         ▼
     write_draft
         │
         ▼
     critic_review ◄────┐
         │              │ (cycle: 没通过且未达上限 → 回头 revise → 再审)
         ▼              │
   conditional_edges    │
   passed?  exhausted?  │
     │                  │
   passed | exhausted   │
     │       │          │
     │       └──────────┤ no? → revise
     ▼                  │
   finalize             │
     │                  │
     ▼                  │
    END

关键 LangGraph 机制：
  · conditional_edges 在 critic_review 后判 3 种走向：
      "finalize"  → 通过或达上限 → 收尾
      "revise"    → 没通过且未达上限 → 改稿 → 回 critic_review
  · revise 节点完后直接 add_edge 回 critic_review，形成 cycle
  · max_rounds 检查防无限循环（v1 director 也是 for rnd in MAX_REVISION_ROUNDS）

替代 v1 director 这段（director.py:1593-1656）：
    for rnd in range(1, MAX_REVISION_ROUNDS + 1):
        review = review_chapter(...)
        if passed and not force_revise: break
        final = revise_chapter(...)
"""
from __future__ import annotations
import sys
from pathlib import Path

from langgraph.graph import StateGraph, START, END

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from state_chapter import ChapterState


def _next_after_critic(state: ChapterState) -> str:
    """critic 后的决策：通过 / 达上限 → finalize；否则 → revise（cycle）。"""
    if state.review_passed:
        return "finalize"
    if state.revision_round >= state.max_rounds:
        return "finalize"  # 强制收尾防无限循环
    return "revise"


def build_chapter_graph(checkpointer=None, mock: bool = True):
    """构造章级子图。
    · mock=True：用 chapter_cycle_mock_nodes（不调 v1）—— 验证 cycle 机制
    · mock=False：用 chapter_cycle_nodes（真节点，包 v1 writer/critic/revise）
    """
    if mock:
        from agents_v2.chapter_cycle_mock_nodes import (
            write_draft, critic_review, revise, finalize,
        )
    else:
        from agents_v2.chapter_cycle_nodes import (
            node_write_draft as write_draft,
            node_critic_review as critic_review,
            node_revise as revise,
            node_finalize as finalize,
        )

    b = StateGraph(ChapterState)
    b.add_node("write_draft", write_draft)
    b.add_node("critic_review", critic_review)
    b.add_node("revise", revise)
    b.add_node("finalize", finalize)

    b.add_edge(START, "write_draft")
    b.add_edge("write_draft", "critic_review")

    # 关键：critic_review 后的条件分支
    b.add_conditional_edges(
        "critic_review",
        _next_after_critic,
        {"revise": "revise", "finalize": "finalize"},
    )

    # 关键 cycle：revise 完成 → 回 critic_review
    b.add_edge("revise", "critic_review")

    b.add_edge("finalize", END)

    return b.compile(checkpointer=checkpointer)


# LangGraph Studio 入口（无参 + 不带 checkpointer，Studio 自己接管）
chapter_graph_mock = build_chapter_graph(checkpointer=None, mock=True)
