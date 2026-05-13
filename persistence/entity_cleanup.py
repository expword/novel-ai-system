"""
实体重建（regen_*）时的孤儿引用清理。

问题场景：`regen_characters()` 把 `state.characters = []` 清空后重新生成，
但其他模块对老角色名字的引用（character_arcs / relationship_web.bonds
/ twist_chains.involved_characters / world_events 等）不会自动更新——
变成"孤儿引用"，UI 显示/LLM 读取时会看到已不存在的名字/id。

设计：每种实体对应一个 after_regen_xxx(state) 函数，regen 完新数据后调用。
原则：宁可丢也不留——孤儿引用比数据丢失更难调试。
"""
from __future__ import annotations


def after_regen_characters(state) -> dict:
    """角色重建完后：清/过滤所有按角色名引用的下游。"""
    stats = {"removed": {}}
    # 当前存活的角色名集合
    live_names = {c.name for c in (state.characters or []) if getattr(c, "name", "")}

    # 1. character_arcs —— 按 character_name
    before = len(state.character_arcs or [])
    state.character_arcs = [a for a in (state.character_arcs or []) if a.character_name in live_names]
    stats["removed"]["character_arcs"] = before - len(state.character_arcs)

    # 2. character_state_history —— dict[name, list[snapshot]]
    dropped = 0
    for name in list((state.character_state_history or {}).keys()):
        if name not in live_names:
            state.character_state_history.pop(name, None)
            dropped += 1
    stats["removed"]["character_state_history"] = dropped

    # 3. memory.character_states —— dict[name, status_str]
    dropped = 0
    for name in list((state.memory.character_states or {}).keys()):
        if name not in live_names:
            state.memory.character_states.pop(name, None)
            dropped += 1
    stats["removed"]["memory.character_states"] = dropped

    # 4. relationship_web.bonds —— char_a/char_b 都在才保留
    rw = state.relationship_web
    if rw and rw.bonds:
        before = len(rw.bonds)
        rw.bonds = [b for b in rw.bonds if b.char_a in live_names and b.char_b in live_names]
        stats["removed"]["relationship_web.bonds"] = before - len(rw.bonds)

    # 5. relationship_web.faction_affiliations —— key 是角色名
    if rw and rw.faction_affiliations:
        dropped = 0
        for name in list(rw.faction_affiliations.keys()):
            if name not in live_names:
                rw.faction_affiliations.pop(name, None)
                dropped += 1
        stats["removed"]["faction_affiliations"] = dropped

    # 6. twist_system.chains[*].involved_characters —— list[name]
    if state.twist_system and state.twist_system.chains:
        dropped = 0
        for ch in state.twist_system.chains:
            before = len(ch.involved_characters or [])
            ch.involved_characters = [n for n in (ch.involved_characters or []) if n in live_names]
            dropped += before - len(ch.involved_characters)
        stats["removed"]["twist.involved_characters"] = dropped

    # 7. narrative_line.characters —— list[name]
    for ln in list((state.global_lines or [])) + list((state.volume_lines or [])):
        if ln.characters:
            ln.characters = [n for n in ln.characters if n in live_names]

    return stats


def after_regen_lines(state) -> dict:
    """叙事线重建完后：清对已删 line_id 的所有引用。"""
    stats = {"removed": {}}
    live_ids = {ln.line_id for ln in state.all_lines if getattr(ln, "line_id", "")}

    # 1. memory.entries.line_ids —— list[line_id]
    if state.memory and state.memory.entries:
        dropped_entries = 0
        for e in state.memory.entries:
            if e.line_ids:
                before = len(e.line_ids)
                e.line_ids = [lid for lid in e.line_ids if lid in live_ids]
                if before != len(e.line_ids):
                    dropped_entries += 1
        stats["removed"]["memory_entries_rewritten"] = dropped_entries

    # 2. completed_chapters.lines_advanced —— list[line_id]
    for ch in (state.completed_chapters or []):
        if ch.lines_advanced:
            ch.lines_advanced = [lid for lid in ch.lines_advanced if lid in live_ids]

    return stats


def after_regen_satisfaction(state) -> dict:
    """爽点重建完后：清对已删 sp_id 的引用。"""
    stats = {"removed": {}}
    live_ids = {sp.sp_id for sp in (state.satisfaction_points or []) if getattr(sp, "sp_id", "")}

    # 1. foreshadow_items.related_sp_id —— 单个 id 字段
    dropped = 0
    for fw in (state.foreshadow_items or []):
        if fw.related_sp_id and fw.related_sp_id not in live_ids:
            fw.related_sp_id = ""
            dropped += 1
    stats["removed"]["foreshadow.related_sp_id"] = dropped

    # 2. completed_chapters.sp_triggered —— list[sp_id]
    for ch in (state.completed_chapters or []):
        if ch.sp_triggered:
            ch.sp_triggered = [i for i in ch.sp_triggered if i in live_ids]

    return stats


def after_regen_foreshadows(state) -> dict:
    """伏笔重建完后：清对已删 fw_id 的引用。"""
    stats = {"removed": {}}
    live_ids = {fw.fw_id for fw in (state.foreshadow_items or []) if getattr(fw, "fw_id", "")}

    # twist_chain.linked_foreshadow_ids
    if state.twist_system and state.twist_system.chains:
        dropped = 0
        for ch in state.twist_system.chains:
            if ch.linked_foreshadow_ids:
                before = len(ch.linked_foreshadow_ids)
                ch.linked_foreshadow_ids = [i for i in ch.linked_foreshadow_ids if i in live_ids]
                dropped += before - len(ch.linked_foreshadow_ids)
        stats["removed"]["twist.linked_foreshadow_ids"] = dropped

    return stats


def after_regen_factions(state) -> dict:
    """势力重建完后：清对已删势力名的引用。"""
    stats = {"removed": {}}
    live_names = {f.name for f in (state.factions or []) if getattr(f, "name", "")}

    # 1. character.faction —— 字符串字段
    dropped_chars = 0
    for c in (state.characters or []):
        fac = getattr(c, "faction", "")
        if fac and fac not in live_names:
            c.faction = ""
            dropped_chars += 1
    stats["removed"]["character.faction"] = dropped_chars

    # 2. world_events.affected_factions —— list[name]
    for e in (state.world_events or []):
        if e.affected_factions:
            e.affected_factions = [n for n in e.affected_factions if n in live_names]

    # 3. twist_chain.involved_factions —— list[name]
    if state.twist_system and state.twist_system.chains:
        for ch in state.twist_system.chains:
            if ch.involved_factions:
                ch.involved_factions = [n for n in ch.involved_factions if n in live_names]

    # 4. relationship_web.faction_affiliations values —— dict[char_name, list[faction]]
    rw = state.relationship_web
    if rw and rw.faction_affiliations:
        for char, factions in list(rw.faction_affiliations.items()):
            rw.faction_affiliations[char] = [f for f in (factions or []) if f in live_names]

    return stats


def after_regen_stages(state) -> dict:
    """叙事舞台重建完后：清对已删 stage_id 的引用。"""
    stats = {"removed": {}}
    live_ids = {s.stage_id for s in (state.story_stages or []) if getattr(s, "stage_id", "")}

    # 1. fortunes.stage_id —— 单字段
    dropped = 0
    for f in (state.fortunes or []):
        if getattr(f, "stage_id", "") and f.stage_id not in live_ids:
            f.stage_id = ""
            dropped += 1
    stats["removed"]["fortune.stage_id"] = dropped

    # 2. protagonist_journey.stage_beats[*].stage_id —— 直接过滤条目
    pj = state.protagonist_journey
    if pj and pj.stage_beats:
        before = len(pj.stage_beats)
        pj.stage_beats = [b for b in pj.stage_beats if getattr(b, "stage_id", "") in live_ids]
        stats["removed"]["protagonist_journey.stage_beats"] = before - len(pj.stage_beats)

    return stats


def after_regen_volumes(state) -> dict:
    """
    卷重建完后：清按卷号引用的下游（最复杂——几乎所有规划都挂卷号）。
    保守策略：按新卷号集合过滤；卷数变化后 stage/rhythm/conflict/foreshadow 里的卷号引用全部按集合过滤。
    """
    stats = {"removed": {}}
    live_vols = {v.index for v in (state.volumes or [])}

    # 1. story_stages.volume
    if state.story_stages:
        before = len(state.story_stages)
        state.story_stages = [s for s in state.story_stages if getattr(s, "volume", 0) in live_vols]
        stats["removed"]["story_stages"] = before - len(state.story_stages)

    # 2. rhythm_plans 按 volume
    if state.rhythm_plans:
        before = len(state.rhythm_plans)
        state.rhythm_plans = [r for r in state.rhythm_plans if getattr(r, "volume", 0) in live_vols]
        stats["removed"]["rhythm_plans"] = before - len(state.rhythm_plans)

    # 3. fortunes.volume —— 如果卷号不在了就置 -1（保留机缘本身，只失联卷号）
    dropped = 0
    for f in (state.fortunes or []):
        if getattr(f, "volume", 0) not in live_vols:
            f.volume = 0
            dropped += 1
    stats["removed"]["fortunes.volume_reset"] = dropped

    # 4. conflict_ladder.entries.volume
    if state.conflict_ladder and state.conflict_ladder.entries:
        before = len(state.conflict_ladder.entries)
        state.conflict_ladder.entries = [
            e for e in state.conflict_ladder.entries if getattr(e, "volume", 0) in live_vols
        ]
        stats["removed"]["conflict_ladder"] = before - len(state.conflict_ladder.entries)

    # 5. twist_chain.volume_span / anchor_volume
    if state.twist_system and state.twist_system.chains:
        for ch in state.twist_system.chains:
            if ch.volume_span:
                ch.volume_span = [v for v in ch.volume_span if v in live_vols]
            if ch.anchor_volume and ch.anchor_volume not in live_vols:
                ch.anchor_volume = 0

    # 6. line phases.volume
    for ln in list((state.global_lines or [])) + list((state.volume_lines or [])):
        if ln.phases:
            for p in ln.phases:
                if getattr(p, "volume", 0) not in live_vols:
                    # phase 所在卷消失——标记未完成，防止误以为该 phase 已推进
                    p.completed = False

    return stats
