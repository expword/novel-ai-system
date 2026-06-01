"""规划期完整图：G1 + G2 + G3 + G4 + 卷级循环（30 个固定节点 + 5 个卷级循环节点）。

拓扑（含 LangGraph cycle 验证）：

  START
    → [G1] -1 → 0 → 0.5 → 0.6                       ⏸ G1 末
    → [G2] 1A → 1A2 → 1B → 1C → 1D → 1E → 1F → 1G → 1H ⏸ G2 末
    → [G3] 2 → 2A2 → 2B → 2C → 2D → 2C2              ⏸ G3 末
    → [G4] 3A → 3B → 3B2 → 3C → 3D → 3D2 → 3E3 → 3E → 3E2 → 3F → 3G ⏸ G4 末
    →
    ┌─→ vol_stage → vol_beats → vol_outline → vol_ctp → vol_lifecycle ─┐
    │   (5 节点为本卷跑一遍；vol_lifecycle 推进 current_volume_index)    │
    │                                                                    │
    └─── conditional_edges：还有下一卷？──── yes ────┘
                                            │
                                            no
                                            ▼
                                           END

依赖说明：
  · 3E3 必须在 3E 之前（反转层声明 clues，伏笔阶段优先满足）
  · 2C2 在 2D 之后（需要 character_arcs 标 ability_trigger）
  · 卷级 cycle：vol_lifecycle 跑完后 current_volume_index +=1；conditional edge
    判 current_volume_index 是否 <= len(volumes)，是就回到 vol_stage，否则 END
"""
from __future__ import annotations
import sys
from pathlib import Path

from langgraph.graph import StateGraph, START, END

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from state_v2 import NovelStateV2


def build_planning_graph(checkpointer=None, mock: bool = False,
                          stepwise: bool = True):
    """构造规划期完整图。

    · stepwise=True：每组末 interrupt（G1/G2/G3/G4 末，共 4 个暂停点）
    · stepwise=False：纯 auto 模式不暂停
    · mock=True：所有节点用 mock 版本（不调 v1 agent）
    """
    builder = StateGraph(NovelStateV2)

    if mock:
        from agents_v2.g1_mock_nodes import (
            mock_phase_minus1, mock_phase_0, mock_phase_0_5, mock_phase_0_6,
        )
        from agents_v2.g2_mock_nodes import (
            mock_1A, mock_1A2, mock_1B, mock_1C, mock_1D,
            mock_1E, mock_1F, mock_1G, mock_1H,
        )
        from agents_v2.g34_mock_nodes import (
            mock_2, mock_2A2, mock_2B, mock_2C, mock_2D, mock_2C2,
            mock_3A, mock_3B, mock_3B2, mock_3C, mock_3D, mock_3D2,
            mock_3E3, mock_3E, mock_3E2, mock_3F, mock_3G,
        )
        from agents_v2.vol_mock_nodes import (
            vol_stage, vol_beats, vol_outline, vol_ctp, vol_lifecycle,
        )
        g1 = (mock_phase_minus1, mock_phase_0, mock_phase_0_5, mock_phase_0_6)
        g2 = (mock_1A, mock_1A2, mock_1B, mock_1C, mock_1D,
              mock_1E, mock_1F, mock_1G, mock_1H)
        g3 = (mock_2, mock_2A2, mock_2B, mock_2C, mock_2D, mock_2C2)
        g4 = (mock_3A, mock_3B, mock_3B2, mock_3C, mock_3D, mock_3D2,
              mock_3E3, mock_3E, mock_3E2, mock_3F, mock_3G)
        vol = (vol_stage, vol_beats, vol_outline, vol_ctp, vol_lifecycle)
    else:
        from agents_v2.g1_nodes import (
            node_phase_minus1, node_phase_0, node_phase_0_5, node_phase_0_6,
        )
        from agents_v2.g2_nodes import (
            node_phase_1A, node_phase_1A2, node_phase_1B, node_phase_1C,
            node_phase_1D, node_phase_1E, node_phase_1F, node_phase_1G,
            node_phase_1H,
        )
        from agents_v2.g3_nodes import (
            node_phase_2, node_phase_2A2, node_phase_2B, node_phase_2C,
            node_phase_2D, node_phase_2C2,
        )
        from agents_v2.g4_nodes import (
            node_phase_3A, node_phase_3B, node_phase_3B2, node_phase_3C,
            node_phase_3D, node_phase_3D2, node_phase_3E3, node_phase_3E,
            node_phase_3E2, node_phase_3F, node_phase_3G,
        )
        from agents_v2.vol_nodes import (
            node_vol_stage, node_vol_beats, node_vol_outline,
            node_vol_ctp, node_vol_lifecycle,
        )
        g1 = (node_phase_minus1, node_phase_0, node_phase_0_5, node_phase_0_6)
        g2 = (node_phase_1A, node_phase_1A2, node_phase_1B, node_phase_1C,
              node_phase_1D, node_phase_1E, node_phase_1F, node_phase_1G,
              node_phase_1H)
        g3 = (node_phase_2, node_phase_2A2, node_phase_2B, node_phase_2C,
              node_phase_2D, node_phase_2C2)
        g4 = (node_phase_3A, node_phase_3B, node_phase_3B2, node_phase_3C,
              node_phase_3D, node_phase_3D2, node_phase_3E3, node_phase_3E,
              node_phase_3E2, node_phase_3F, node_phase_3G)
        vol = (node_vol_stage, node_vol_beats, node_vol_outline,
               node_vol_ctp, node_vol_lifecycle)

    # ── 添加 30 个固定节点 ──
    g1_ids = ("phase_-1", "phase_0", "phase_0.5", "phase_0.6")
    g2_ids = ("phase_1A", "phase_1A2", "phase_1B", "phase_1C", "phase_1D",
              "phase_1E", "phase_1F", "phase_1G", "phase_1H")
    g3_ids = ("phase_2", "phase_2A2", "phase_2B", "phase_2C", "phase_2D", "phase_2C2")
    g4_ids = ("phase_3A", "phase_3B", "phase_3B2", "phase_3C", "phase_3D", "phase_3D2",
              "phase_3E3", "phase_3E", "phase_3E2", "phase_3F", "phase_3G")
    for nid, fn in zip(g1_ids + g2_ids + g3_ids + g4_ids, g1 + g2 + g3 + g4):
        builder.add_node(nid, fn)

    # ── 卷级 5 节点（循环跑 N 卷，每卷重跑一遍这 5 个） ──
    vol_ids = ("vol_stage", "vol_beats", "vol_outline", "vol_ctp", "vol_lifecycle")
    for vid, fn in zip(vol_ids, vol):
        builder.add_node(vid, fn)

    # ── 章级循环 3 节点（嵌套在卷级 cycle 内） ──
    from agents_v2.chapter_loop_nodes import (
        node_chapter_loop_init, node_chapter_write_mock, node_chapter_write_real,
        node_chapter_advance, chapter_router,
    )
    chapter_write_fn = node_chapter_write_mock if mock else node_chapter_write_real
    builder.add_node("chapter_loop_init", node_chapter_loop_init)
    builder.add_node("chapter_write", chapter_write_fn)
    builder.add_node("chapter_advance", node_chapter_advance)

    # ── 固定段顺序边 ──
    fixed_ids = g1_ids + g2_ids + g3_ids + g4_ids
    builder.add_edge(START, fixed_ids[0])
    for prev, nxt in zip(fixed_ids, fixed_ids[1:]):
        builder.add_edge(prev, nxt)

    # ── G4 末 → 卷级第一节点 ──
    builder.add_edge(fixed_ids[-1], vol_ids[0])

    # ── 卷级内部顺序边 ──
    for prev, nxt in zip(vol_ids, vol_ids[1:]):
        builder.add_edge(prev, nxt)

    # ── 卷级 lifecycle 落章完成后 → 进入本卷的章节循环 ──
    builder.add_edge(vol_ids[-1], "chapter_loop_init")
    builder.add_edge("chapter_loop_init", "chapter_write")
    builder.add_edge("chapter_write", "chapter_advance")

    # ── 嵌套 cycle 路由：next_chapter / next_volume / end ──
    builder.add_conditional_edges(
        "chapter_advance",
        chapter_router,
        {
            "next_chapter": "chapter_write",   # 同卷下一章（章级 cycle）
            "next_volume": vol_ids[0],         # 下一卷重跑卷级 5 phase（卷级 cycle）
            "end": END,
        },
    )

    interrupts = []
    if stepwise:
        # 4 组规划期末（让用户审产物再 resume）；章级循环不暂停（让它跑到底）
        interrupts = [g1_ids[-1], g2_ids[-1], g3_ids[-1], g4_ids[-1]]
    return builder.compile(checkpointer=checkpointer,
                            interrupt_after=interrupts)
