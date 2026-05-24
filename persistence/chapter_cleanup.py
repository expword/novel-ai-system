"""
删章时的 state 清理——把所有按章追加/按章打标的派生状态回滚到干净状态。

被调方：
  - web/app.py:api_chapter_delete  （用户主动删章）
  - web/rewrite_chapter.py         （重写前删旧稿）

保留（不动）：
  - version_snapshots / checkpoint/history  —— 安全网，用于回滚
  - pending_approvals                        —— 人工审核记录
  - chapter_inspirations                     —— 作者创作输入，不是生成内容

清理（新增）：
  - chapter_chats[idx]    —— 对话历史引用的是旧正文，留着会误导
  - ability_audits[idx]   —— 审计结果基于旧正文，分数/issues 全错位
"""
from __future__ import annotations
import re
from typing import Iterable

from persistence.state import NovelState, StoryThread


_CHAPTER_PREFIX_RE = re.compile(r"^\[第(\d+)章\]")


def cleanup_chapter_state(state: NovelState, to_delete: Iterable[int]) -> None:
    """
    把 to_delete 集合里的章节在 state 上留下的所有派生数据回退/清除。
    调用前提：state.completed_chapters 已被调用方过滤（不含被删章）。
    """
    to_delete = set(to_delete)
    if not to_delete:
        return

    # 1) 按章记录的记忆条目 —— 直接过滤
    state.memory.entries = [
        e for e in state.memory.entries if e.chapter_index not in to_delete
    ]

    # 2) 每角色的状态快照 —— 按章过滤
    for name in list(state.character_state_history.keys()):
        kept = [
            s for s in state.character_state_history[name]
            if s.chapter_index not in to_delete
        ]
        if kept:
            state.character_state_history[name] = kept
        else:
            del state.character_state_history[name]

    # 3) 世界事件日历 —— 按章过滤
    state.world_events = [
        ev for ev in state.world_events if ev.chapter_index not in to_delete
    ]

    # 4) tension_history 是按章位置 append 的平铺列表——
    #    按剩余 completed_chapters 的 tension 重建，保持位置对齐
    state.tension_history = [c.tension for c in state.completed_chapters]

    # 5) 爽点：触发章被删 → 取消 triggered
    for sp in state.satisfaction_points:
        if sp.triggered and sp.actual_chapter in to_delete:
            sp.triggered = False
            sp.actual_chapter = -1

    # 6) 伏笔：植入/激活/回收如果发生在被删章，对应状态回退
    for fw in state.foreshadow_items:
        if fw.resolved and fw.actual_resolve_chapter in to_delete:
            fw.resolved = False
            fw.actual_resolve_chapter = -1
            fw.resolution_quality = ""
        if fw.planted_chapter in to_delete:
            # planted_chapter 既是"计划"也是"已发生"字段，被删了就回到未植入
            fw.planted_chapter = 0
        if fw.activation_chapter in to_delete:
            fw.activation_chapter = -1
            fw.activation_sign = ""

    # 7) 红鲱鱼：planted / debunked 状态按实际发生章回退
    #    （planted_chapter / debunk_chapter 是"计划"字段，不改）
    for rh in state.red_herrings:
        if rh.planted and rh.planted_chapter in to_delete:
            rh.planted = False
        if rh.debunked and rh.debunk_chapter in to_delete:
            rh.debunked = False

    # 7B) setup_ledger:被删章里新建的 entry 直接移除;callback 在被删章发生的回退为 pending
    if getattr(state, "setup_ledger", None):
        state.setup_ledger = [e for e in state.setup_ledger if e.chapter not in to_delete]
        for e in state.setup_ledger:
            if e.callback_chapter in to_delete:
                e.payoff_status = "pending"
                e.callback_chapter = -1
                e.callback_quote = ""

    # 8) 机缘：获得章被删 → 回退到未获得
    for f in state.fortunes:
        if f.obtained and f.actual_chapter in to_delete:
            f.obtained = False
            f.actual_chapter = -1

    # 9) 叙事线阶段 completed / current_phase / resolved ——
    #    按剩余最大已写章重算
    max_written = max((c.index for c in state.completed_chapters), default=0)
    for line in state.all_lines:
        n_completed = 0
        for phase in line.phases:
            phase.completed = max_written >= phase.chapter_end
            if phase.completed:
                n_completed += 1
        if line.phases and n_completed < len(line.phases):
            line.current_phase = n_completed + 1
            line.resolved = False
        elif line.phases:
            line.current_phase = len(line.phases)
            line.resolved = True
        # 无 phases 的线不动

    # 10) memory.character_states: value 是 "[第N章] 状态" 格式，
    #     若 N 在 to_delete 里，移除该角色的当前状态记录
    for name in list(state.memory.character_states.keys()):
        m = _CHAPTER_PREFIX_RE.match(state.memory.character_states[name] or "")
        if m and int(m.group(1)) in to_delete:
            del state.memory.character_states[name]

    # 11) story_thread 的尾部状态：如果删的是"尾巴"章节，scene_end_state
    #     之类的滚动状态已经不对——清空让下一次写章重建。
    #     判定方式：被删章里存在 > 剩余最大章 的 index（即尾部被砍）
    if to_delete and max(to_delete) > max_written:
        _emergent = getattr(state.story_thread, "_emergent_pending", None)
        state.story_thread = StoryThread()
        # 涌现角色列表挂在 story_thread 上，这里顺便按章过滤后保留
        if _emergent:
            state.story_thread._emergent_pending = [
                e for e in _emergent
                if e.get("first_appeared", 0) not in to_delete
                and e.get("first_appeared", 0) <= max_written
            ]
    else:
        # 非尾部删（only_this 删中间章）：story_thread 主体保留，
        # 只过滤涌现列表和开放循环
        _emergent = getattr(state.story_thread, "_emergent_pending", None)
        if _emergent:
            state.story_thread._emergent_pending = [
                e for e in _emergent if e.get("first_appeared", 0) not in to_delete
            ]
        # 开放循环：开在被删章的直接剔除；关在被删章的重新打开
        kept_loops = []
        for loop in state.story_thread.open_loops:
            if loop.opened_chapter in to_delete:
                continue
            if loop.closed and loop.target_close_chapter in to_delete:
                loop.closed = False
            kept_loops.append(loop)
        state.story_thread.open_loops = kept_loops

    # 12) 章节对话历史 + 各类章级审计 —— 指向旧正文的派生数据，必须清
    # 留着会造成：UI 显示"Ch5 审计 ⚠3"，点开看是针对已删/已重写旧内容的问题
    for idx in to_delete:
        if getattr(state, "chapter_chats", None):
            state.chapter_chats.pop(idx, None)
        if getattr(state, "ability_audits", None):
            state.ability_audits.pop(idx, None)
        if getattr(state, "reader_audits", None):
            state.reader_audits.pop(idx, None)
        if getattr(state, "dialogue_audits", None):
            state.dialogue_audits.pop(idx, None)
        if getattr(state, "protagonist_power_log", None):
            state.protagonist_power_log.pop(idx, None)

    # 13) 设定护栏审计累积列表（director 每章 append）—— 按 chapter_index 过滤
    _canon = getattr(state, "_canon_audit", None)
    if isinstance(_canon, list):
        state._canon_audit = [
            r for r in _canon
            if (r.get("chapter_index") if isinstance(r, dict) else getattr(r, "chapter_index", -1))
               not in to_delete
        ]

    # 14) lifecycle 节点状态回退（金手指/物品 lifecycle 系统）——
    # 如果某节点被落到被删章里，把它的 triggered / actual_chapter 回退到未落章状态。
    # 注意：target_chapter（粗规划）不动，只动 actual 触发记录。
    if state.power_system and state.power_system.special_abilities:
        for asset in state.power_system.special_abilities:
            for node in (asset.lifecycle_nodes or []):
                if node.triggered and node.actual_chapter in to_delete:
                    node.triggered = False
                    node.actual_chapter = -1
                # target_chapter 若指向被删章——这是规划本身，保留（重写时章号不变）
                # 但如果该章已写过 + 现在被删 = 该节点应该重新等待落章
                # （这里不动 target_chapter，让 chapter_planner 下次自动重新分配）

    # 15) 感情线：被删章里的 RomanceEvent 移除 + 重算 last_interaction_chapter
    for arc in (state.romance_arcs or []):
        arc.actual_events = [
            ev for ev in (arc.actual_events or [])
            if ev.chapter_index not in to_delete
        ]
        if arc.last_interaction_chapter in to_delete:
            # 用剩余 events 的最大 chapter_index 重新设；都没了就归 0
            arc.last_interaction_chapter = max(
                (ev.chapter_index for ev in arc.actual_events), default=0
            )
