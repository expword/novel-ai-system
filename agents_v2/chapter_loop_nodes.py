"""章级循环控制节点（主图层面）——把 chapter_subgraph 嵌入卷级循环。

主图扩展拓扑（vol_lifecycle 之后）：

  vol_lifecycle
      │
      ▼
  chapter_loop_init    ← 把 current_chapter_index = 当前卷的 chapter_start
      │
      ▼
  ┌─→ chapter_write   ← 调 chapter_subgraph（mock 或真节点）写当前章
  │      │
  │      ▼
  │  chapter_advance  ← current_chapter_index +=1
  │      │
  │  conditional_edges：
  │   "next_chapter"   → 回 chapter_write（同卷下一章）
  │   "next_volume"    → 推进 current_volume_index、跳回 vol_stage 进下一卷
  │   "end"            → 全书写完 → END
  │      ▲
  └──────┘

state schema 转换：
  · 主图用 NovelStateV2；章级 subgraph 用 ChapterState
  · chapter_write 节点内 build_chapter_graph(...).invoke(ChapterState(...)) 跑一次完整 cycle
  · 子图 final state 关键字段（draft / word_count）汇总进主 state 的 chapters_done
"""
from __future__ import annotations
import time

from state_v2 import NovelStateV2


# ─────────────────────────────────────────────────────
#  Init：每卷开头初始化章节游标
# ─────────────────────────────────────────────────────
def node_chapter_loop_init(state: NovelStateV2) -> dict:
    """卷级 lifecycle 后第一站。
    current_volume_index 已被 vol_lifecycle 推进，所以"当前要写的卷"=
    current_volume_index - 1（注意：vol_lifecycle 节点结尾才 +1）。
    """
    vol_idx = state.current_volume_index - 1
    vol = _get_volume(state, vol_idx)
    if not vol:
        # 没有该卷数据：跳过章节循环
        print(f"  ⚠ [chapter_loop_init] V{vol_idx} 没有 volume 数据，跳过章节循环")
        return {"current_chapter_index": -1}
    start_ch = vol.get("chapter_start", 1)
    print(f"  ▶ [chapter_loop_init] V{vol_idx} 章节范围："
          f"{start_ch}~{vol.get('chapter_end', start_ch)}")
    return {"current_chapter_index": start_ch}


def _get_volume(state: NovelStateV2, volume_index: int) -> dict | None:
    for v in (state.volumes or []):
        if v.get("index") == volume_index:
            return v
    # 兼容 1-based 顺序
    if 1 <= volume_index <= len(state.volumes or []):
        return state.volumes[volume_index - 1]
    return None


# ─────────────────────────────────────────────────────
#  Write：调 chapter_subgraph 写当前章（mock）
#  阶段 4 暂时只走 mock；真节点版需配合完整 v1 state（vol 跑完真路径后）
# ─────────────────────────────────────────────────────
def node_chapter_write_mock(state: NovelStateV2) -> dict:
    """主图层面包装：实际跑章级 cycle 子图写一章。
    阶段 4 mock：直接产假 draft 模拟"写章"；不嵌入真 subgraph 避免 LangGraph
    嵌套调用复杂度。真节点 wrapper 见 node_chapter_write_real（待实测）。
    """
    if state.current_chapter_index <= 0:
        return {}
    if state.current_chapter_index in state.chapters_done:
        return {"current_phase_label": f"Ch{state.current_chapter_index}（已写，跳过）"}
    print(f"  ▶ [chapter_write·MOCK] Ch{state.current_chapter_index} 写章中...")
    time.sleep(0.03)
    print(f"  ✓ [chapter_write·MOCK] Ch{state.current_chapter_index} 完成")
    return {
        "chapters_done": state.chapters_done + [state.current_chapter_index],
        "current_phase": f"chapter_{state.current_chapter_index}",
        "current_phase_label": f"Ch{state.current_chapter_index} 完成（mock）",
    }


def node_chapter_write_real(state: NovelStateV2) -> dict:
    """真节点版：嵌入 chapter_subgraph 跑真 writer/critic/revise。
    需要 v1 完整 state 已就绪（含 directive/blueprint 数据源）。"""
    if state.current_chapter_index <= 0:
        return {}
    if state.current_chapter_index in state.chapters_done:
        return {"current_phase_label": f"Ch{state.current_chapter_index}（已写，跳过）"}

    from state_chapter import ChapterState
    from graphs.chapter_subgraph import build_chapter_graph
    from langgraph.checkpoint.memory import InMemorySaver

    vol_idx = state.current_volume_index - 1
    ci = state.current_chapter_index
    print(f"  ▶ [chapter_write·REAL] Ch{ci} 调 chapter_subgraph...")

    chapter_state = ChapterState(
        chapter_index=ci,
        volume_index=vol_idx,
        chapter_directive={"project_id": state.project_id},
        word_quota=3000,
        max_rounds=3,
    )
    sub = build_chapter_graph(checkpointer=InMemorySaver(), mock=False)
    final = sub.invoke(chapter_state, config={"configurable": {"thread_id": f"{state.project_id}_ch{ci}"}})
    print(f"  ✓ [chapter_write·REAL] Ch{ci} 完成 reason={final.get('finalize_reason')}")

    return {
        "chapters_done": state.chapters_done + [ci],
        "current_phase": f"chapter_{ci}",
        "current_phase_label": f"Ch{ci} 完成 ({final.get('finalize_reason','')})",
    }


# ─────────────────────────────────────────────────────
#  Advance：推进章节游标 + 决策回头/换卷/结束
# ─────────────────────────────────────────────────────
def node_chapter_advance(state: NovelStateV2) -> dict:
    """章节游标 +1。conditional_edges 根据返回的 next_chapter 决定走向。"""
    new_ci = state.current_chapter_index + 1
    return {"current_chapter_index": new_ci}


def chapter_router(state: NovelStateV2) -> str:
    """conditional_edges 路由：判断"还在本卷"/"换下一卷"/"全书写完"。"""
    vol_idx = state.current_volume_index - 1
    vol = _get_volume(state, vol_idx)
    if not vol:
        return "end"
    chapter_end = vol.get("chapter_end", 0)
    total_vols = len(state.volumes or [])

    if state.current_chapter_index <= chapter_end:
        return "next_chapter"          # 同卷下一章
    # 本卷写完了
    if state.current_volume_index <= total_vols:
        return "next_volume"           # 进下一卷（卷级 cycle 已经把 cv_idx 推进过了）
    return "end"
