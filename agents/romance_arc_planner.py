"""
RomanceArcPlanner —— 感情线规划与跟踪。

职责：
  1. 在立项 / 人物设计完成后，扫描 character_web.bonds 找出主角的潜在感情线
  2. 为每条线设计典型节拍（陌生→好感→心动→...→确立）
  3. 写章后扫文本检测感情戏，更新 RomanceArc.actual_events 和 progress_score
  4. 给 chapter_planner 提供 hints："X 已经 25 章没和主角互动了，本卷该有戏份"

设计原则：
  · 不强制——作者可以不写感情线（题材若是纯爽文），系统就跳过
  · 多女主：每条独立 arc，跟踪戏份平衡（互动间隔太久 → 警告）
  · 节拍非僵硬：典型曲线作为参考，作者可改 planned_beats
"""
from __future__ import annotations
from typing import Optional


# 默认节拍模板（可被 LLM 或用户改写）
DEFAULT_BEATS = [
    "首次相遇（带印象点）",
    "误会/小冲突",
    "第一次共处一室或同行",
    "暗中相助/雪中送炭",
    "情绪坦诚的瞬间",
    "外部威胁让两人靠近",
    "误会加深",
    "对方做出让主角触动的牺牲",
    "确认心意（一方/双方）",
    "被迫分离 / 阻碍",
    "重逢",
    "和解/确立关系",
]


def _make_arc_id(state, char_a: str, char_b: str) -> str:
    return f"romance_{char_a}_{char_b}"


def register_arc(
    state,
    char_a: str,
    char_b: str,
    *,
    relationship_label: str = "",
    target_progress: int = 80,
    planned_beats: Optional[list] = None,
) -> str:
    """登记一条感情线。"""
    from state import RomanceArc
    aid = _make_arc_id(state, char_a, char_b)
    # 已存在就更新
    existing = next((a for a in (state.romance_arcs or []) if a.relationship_id == aid), None)
    if existing:
        existing.relationship_label = relationship_label or existing.relationship_label
        existing.target_progress = target_progress
        if planned_beats:
            existing.planned_beats = list(planned_beats)
        return aid

    state.romance_arcs.append(RomanceArc(
        relationship_id=aid,
        char_a=char_a,
        char_b=char_b,
        relationship_label=relationship_label,
        target_progress=target_progress,
        planned_beats=list(planned_beats) if planned_beats else list(DEFAULT_BEATS[:6]),
    ))
    return aid


def design_arcs_from_state(state) -> int:
    """
    扫描 character_web.bonds，把"romance/mutual_attraction/courtship/intimate"类关系登记为 RomanceArc。
    不调 LLM——纯规则。返回新增的 arc 数量。
    """
    rw = getattr(state, "relationship_web", None)
    if not rw:
        return 0
    prot = next((c for c in (state.characters or [])
                 if getattr(getattr(c, "role", None), "value", "") == "主角"), None)
    if not prot:
        return 0
    added = 0
    for bond in (rw.bonds or []):
        # 找跟主角相关的浪漫类 bond
        if prot.name not in (bond.char_a, bond.char_b):
            continue
        other = bond.char_b if bond.char_a == prot.name else bond.char_a
        rel_text = (bond.surface_relation or "") + " " + (bond.true_relation or "")
        if not any(k in rel_text for k in ("情", "恋", "爱", "侶", "暗恋", "夫妻", "未婚", "妾", "妃", "红颜")):
            continue
        register_arc(
            state, prot.name, other,
            relationship_label=bond.surface_relation or "感情线",
        )
        added += 1
    return added


def update_after_chapter(state, chapter_index: int, content: str) -> None:
    """章后扫文：检查每条 arc 的两人是否在本章有互动，更新 last_interaction_chapter 和 progress_score。"""
    if not content or not state.romance_arcs:
        return
    for arc in state.romance_arcs:
        # 简单判定：双方名字都出现 → 视为有互动
        if arc.char_a in content and arc.char_b in content:
            arc.last_interaction_chapter = chapter_index
            # 进度小幅累加（每次互动 +2，但很多次互动收益递减）
            cap = arc.target_progress
            inc = max(1, (cap - arc.progress_score) // 20)
            arc.progress_score = min(cap, arc.progress_score + inc)


def get_planning_hints(state, chapter_index: int) -> list[str]:
    """给 chapter_planner 用——感情线戏份失衡的提醒。"""
    hints: list[str] = []
    if not state.romance_arcs:
        return hints
    cur_vol = state.current_volume_index or 1
    # 每条线检查"互动间隔"
    for arc in state.romance_arcs:
        if arc.progress_score >= 100:
            continue
        last = arc.last_interaction_chapter
        if last <= 0:
            # 还没第一次互动
            if chapter_index > 5:
                hints.append(
                    f"💕 感情线 {arc.char_a}↔{arc.char_b}（{arc.relationship_label}）还没有过互动场景——"
                    f"如果本章合理，可以安排一次小相遇"
                )
            continue
        gap = chapter_index - last
        if gap >= 25:
            hints.append(
                f"💕 感情线 {arc.char_a}↔{arc.char_b} 已 {gap} 章无互动，"
                f"当前进度 {arc.progress_score}/{arc.target_progress}——本卷需要补一次戏份"
            )
    # 多女主戏份平衡
    if len(state.romance_arcs) >= 2:
        gaps = [(a.relationship_id, chapter_index - a.last_interaction_chapter if a.last_interaction_chapter else chapter_index)
                for a in state.romance_arcs]
        gaps.sort(key=lambda x: x[1], reverse=True)
        if gaps[0][1] - gaps[-1][1] >= 20:
            hints.append(
                f"💕 多女主戏份失衡：最久未互动 {gaps[0][0]} 间隔 {gaps[0][1]} 章，"
                f"最近活跃 {gaps[-1][0]} 间隔 {gaps[-1][1]} 章——需要均衡"
            )
    return hints
