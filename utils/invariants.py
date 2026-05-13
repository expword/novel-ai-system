"""
SSoT (Single Source of Truth) 一致性检查。

NovelState 是全局唯一事实源。所有 agent 都读它、写它。
本模块提供一组 invariant 检查，每个 Phase 完成后或 DriftDetector 扫描时调用，
发现"悬挂引用""孤儿 ID""数据不自洽"等问题。

只报告，不自动修。调用方决定如何处理。
"""
from persistence.state import NovelState


def check_all(state: NovelState) -> list[dict]:
    """
    跑全部不变量检查。返回 issues 列表：
    [{"severity": "warn"|"error", "area": "...", "message": "..."}]
    """
    issues = []
    issues.extend(_check_character_references(state))
    issues.extend(_check_ability_holder_refs(state))
    issues.extend(_check_fortune_refs(state))
    issues.extend(_check_foreshadow_sanity(state))
    issues.extend(_check_volume_chapter_alignment(state))
    issues.extend(_check_structure_role_coverage(state))
    issues.extend(_check_conflict_ladder_escalation(state))
    issues.extend(_check_character_arc_character_exists(state))
    # 补充检查（MED #15 系列）
    issues.extend(_check_global_lines_span(state))
    issues.extend(_check_red_herrings_chapter_order(state))
    issues.extend(_check_twist_reveal_anchors(state))
    issues.extend(_check_stage_chapter_range(state))
    issues.extend(_check_satisfaction_setup_order(state))
    return issues


def _check_character_references(state: NovelState) -> list[dict]:
    """关系网里引用的角色必须在 characters 列表里。"""
    names = {c.name for c in state.characters}
    issues = []
    for bond in state.relationship_web.bonds:
        for n in (bond.char_a, bond.char_b):
            if n and n not in names:
                issues.append({
                    "severity": "error", "area": "relationship_web",
                    "message": f"bond {bond.bond_id} 引用了不存在的角色：{n}",
                })
    return issues


def _check_ability_holder_refs(state: NovelState) -> list[dict]:
    """SpecialAbility.holder_name 必须在角色列表里（可以为空）。"""
    if not state.power_system:
        return []
    names = {c.name for c in state.characters}
    issues = []
    for ab in state.power_system.special_abilities:
        if ab.holder_name and ab.holder_name not in names:
            issues.append({
                "severity": "warn", "area": "special_abilities",
                "message": f"能力《{ab.name}》的 holder_name={ab.holder_name} 不在角色列表里",
            })
    return issues


def _check_fortune_refs(state: NovelState) -> list[dict]:
    """Fortune.stage_id 若有，必须指向有效的 StoryStage。"""
    stage_ids = {s.stage_id for s in state.story_stages}
    issues = []
    for f in state.fortunes:
        if f.stage_id and f.stage_id not in stage_ids:
            issues.append({
                "severity": "warn", "area": "fortunes",
                "message": f"机缘《{f.name}》的 stage_id={f.stage_id} 不存在",
            })
    return issues


def _check_foreshadow_sanity(state: NovelState) -> list[dict]:
    """伏笔植入章必须 ≤ 计划回收章。"""
    issues = []
    for fw in state.foreshadow_items:
        if fw.planted_chapter > 0 and fw.planned_resolve_chapter > 0:
            if fw.planted_chapter > fw.planned_resolve_chapter:
                issues.append({
                    "severity": "error", "area": "foreshadow",
                    "message": f"伏笔{fw.fw_id} 植入章 {fw.planted_chapter} 晚于回收章 {fw.planned_resolve_chapter}",
                })
    return issues


def _check_volume_chapter_alignment(state: NovelState) -> list[dict]:
    """卷章节范围不能重叠，不能跳号。"""
    issues = []
    sorted_vols = sorted(state.volumes, key=lambda v: v.index)
    for i, v in enumerate(sorted_vols):
        if v.chapter_start > v.chapter_end:
            issues.append({
                "severity": "error", "area": "volumes",
                "message": f"卷 {v.index} 章节范围反向：{v.chapter_start}-{v.chapter_end}",
            })
        if i > 0:
            prev = sorted_vols[i - 1]
            if v.chapter_start != prev.chapter_end + 1:
                issues.append({
                    "severity": "warn", "area": "volumes",
                    "message": f"卷{prev.index}结束于{prev.chapter_end}，卷{v.index}从{v.chapter_start}开始——不连续",
                })
    return issues


def _check_structure_role_coverage(state: NovelState) -> list[dict]:
    """整本书的起承转合四段，每段至少有 1 卷。"""
    issues = []
    if not state.book_structure.phase_volumes:
        return issues  # 未规划则跳过
    for role in ("起", "承", "转", "合"):
        vols = state.book_structure.phase_volumes.get(role, [])
        if not vols:
            issues.append({
                "severity": "warn", "area": "book_structure",
                "message": f"整本书起承转合缺少'{role}'段——没有卷承担这一角色",
            })
    return issues


def _check_conflict_ladder_escalation(state: NovelState) -> list[dict]:
    """冲突阶梯 opponent_tier 必须单调不减。"""
    issues = []
    entries = sorted(state.conflict_ladder.entries, key=lambda e: e.volume)
    for i, e in enumerate(entries):
        if i > 0 and e.opponent_tier < entries[i - 1].opponent_tier:
            issues.append({
                "severity": "warn", "area": "conflict_ladder",
                "message": f"卷{e.volume} tier={e.opponent_tier} 低于卷{entries[i-1].volume} tier={entries[i-1].opponent_tier}——对手等级倒退",
            })
    return issues


def _check_character_arc_character_exists(state: NovelState) -> list[dict]:
    """CharacterArc.character_name 必须在角色列表里。"""
    names = {c.name for c in state.characters}
    issues = []
    for a in state.character_arcs:
        if a.character_name and a.character_name not in names:
            issues.append({
                "severity": "warn", "area": "character_arcs",
                "message": f"心理弧引用的角色 {a.character_name} 不在角色列表",
            })
    return issues


def _check_global_lines_span(state: NovelState) -> list[dict]:
    """全局叙事线必须覆盖至少 2 卷——只覆盖 1 卷的不算"全局"。"""
    issues = []
    if len(state.volumes) < 2:
        return issues
    for ln in state.global_lines:
        try:
            lo, hi = ln.volume_range
        except Exception:
            continue
        span = max(0, (hi or 0) - (lo or 0)) + 1
        if span < 2:
            issues.append({
                "severity": "warn", "area": "global_lines",
                "message": f"全局线《{ln.name}》只覆盖 {span} 卷（{lo}-{hi}），应≥2 卷否则该是卷内线",
            })
    return issues


def _check_red_herrings_chapter_order(state: NovelState) -> list[dict]:
    """红鲱鱼的植入章必须早于揭穿章。"""
    issues = []
    for rh in state.red_herrings:
        plant = getattr(rh, "plant_chapter", 0) or 0
        debunk = getattr(rh, "debunk_chapter", 0) or 0
        if plant > 0 and debunk > 0 and plant >= debunk:
            issues.append({
                "severity": "error", "area": "red_herrings",
                "message": f"红鲱鱼{rh.rh_id} 植入章 {plant} 不早于揭穿章 {debunk}",
            })
    return issues


def _check_twist_reveal_anchors(state: NovelState) -> list[dict]:
    """反转层 reveal_anchor 必须能解析到具体卷/章——否则永远命中不到。"""
    import re
    issues = []
    pat_full = re.compile(r"第(\d+)卷第(\d+)章")  # "第3卷第15章"
    pat_chap = re.compile(r"第(\d+)章")          # 单章号
    if not (state.twist_system and state.twist_system.chains):
        return issues
    total_chapters = sum(v.total_chapters for v in state.volumes) if state.volumes else 0
    for chain in state.twist_system.chains:
        for layer in chain.layers:
            anchor = (layer.reveal_anchor or "").strip()
            if not anchor:
                issues.append({
                    "severity": "warn", "area": "twist_system",
                    "message": f"反转链 {chain.chain_id} L{layer.layer} 无 reveal_anchor",
                })
                continue
            m = pat_full.search(anchor) or pat_chap.search(anchor)
            if not m:
                issues.append({
                    "severity": "warn", "area": "twist_system",
                    "message": f"反转链 {chain.chain_id} L{layer.layer} 的 reveal_anchor"
                               f"《{anchor[:40]}》无法解析为'第N章'",
                })
            elif m.lastindex == 1 and total_chapters > 0:
                ch = int(m.group(1))
                if ch < 1 or ch > total_chapters:
                    issues.append({
                        "severity": "warn", "area": "twist_system",
                        "message": f"反转链 {chain.chain_id} L{layer.layer} reveal 章号 {ch} 越界（全书 {total_chapters} 章）",
                    })
    return issues


def _check_stage_chapter_range(state: NovelState) -> list[dict]:
    """每个叙事舞台的 chapter_start/end 必须落在它所属卷的章节范围内。"""
    issues = []
    vol_by_index = {v.index: v for v in state.volumes}
    for st in state.story_stages:
        vol = vol_by_index.get(st.volume)
        if not vol:
            issues.append({
                "severity": "error", "area": "story_stages",
                "message": f"舞台 {st.stage_id} 的 volume={st.volume} 不存在",
            })
            continue
        if st.chapter_start < vol.chapter_start or st.chapter_end > vol.chapter_end:
            issues.append({
                "severity": "warn", "area": "story_stages",
                "message": f"舞台 {st.stage_id} 章节 {st.chapter_start}-{st.chapter_end} "
                           f"超出卷{st.volume}范围 {vol.chapter_start}-{vol.chapter_end}",
            })
        if st.chapter_start > st.chapter_end:
            issues.append({
                "severity": "error", "area": "story_stages",
                "message": f"舞台 {st.stage_id} 章节范围反向：{st.chapter_start}-{st.chapter_end}",
            })
    return issues


def _check_satisfaction_setup_order(state: NovelState) -> list[dict]:
    """爽点的每个 setup 章节必须早于 target_chapter（爆发章）。"""
    issues = []
    for sp in state.satisfaction_points:
        target = getattr(sp, "target_chapter", 0) or 0
        if target <= 0:
            continue
        for s in (getattr(sp, "setup_chain", None) or []):
            ch = getattr(s, "chapter", 0) or 0
            if ch > 0 and ch >= target:
                issues.append({
                    "severity": "warn", "area": "satisfaction_points",
                    "message": f"爽点{sp.sp_id} 的 setup 章 {ch} 不早于爆发章 {target}",
                })
    return issues


def autofix(state: NovelState) -> list[str]:
    """对确定性可修的 invariant 违规做自动修复。返回修复条目列表（用于日志）。

    覆盖：
      · 伏笔 plant_chapter > planned_resolve_chapter → swap
      · 红鲱鱼 plant_chapter >= debunk_chapter → swap
      · 舞台 chapter_start/end 越出卷范围 → clamp 到卷范围
      · 舞台 chapter_start > chapter_end → swap
      · 角色弧引用不存在角色 → 删除该 arc
      · 关系网 bond 引用不存在角色 → 删除该 bond
      · 能力 holder_name 引用不存在角色 → 清空 holder_name
      · 机缘 stage_id 引用不存在 stage → 清空 stage_id

    不动：global_lines 跨度、爽点 setup<target、反转 reveal_anchor 解析这些
    需要语义修订的——只报告，让用户/LLM 处理。
    """
    fixed = []
    char_names = {c.name for c in state.characters}
    stage_ids = {s.stage_id for s in state.story_stages}
    vol_by_index = {v.index: v for v in state.volumes}

    # 1. 伏笔 plant > resolve → swap
    for fw in state.foreshadow_items:
        if fw.planted_chapter > 0 and fw.planned_resolve_chapter > 0:
            if fw.planted_chapter > fw.planned_resolve_chapter:
                fw.planted_chapter, fw.planned_resolve_chapter = fw.planned_resolve_chapter, fw.planted_chapter
                fixed.append(f"伏笔{fw.fw_id} plant/resolve 对调（修反向章序）")

    # 2. 红鲱鱼 plant >= debunk → swap（确保 plant<debunk）
    for rh in state.red_herrings:
        plant = getattr(rh, "plant_chapter", 0) or 0
        debunk = getattr(rh, "debunk_chapter", 0) or 0
        if plant > 0 and debunk > 0 and plant >= debunk:
            rh.plant_chapter, rh.debunk_chapter = debunk, plant
            fixed.append(f"红鲱鱼{rh.rh_id} plant/debunk 对调")

    # 3. 舞台章节范围
    for st in state.story_stages:
        # 反向 → swap
        if st.chapter_start > st.chapter_end:
            st.chapter_start, st.chapter_end = st.chapter_end, st.chapter_start
            fixed.append(f"舞台{st.stage_id} 章节范围对调（修反向）")
        # 越卷 → clamp
        vol = vol_by_index.get(st.volume)
        if vol:
            if st.chapter_start < vol.chapter_start:
                st.chapter_start = vol.chapter_start
                fixed.append(f"舞台{st.stage_id} chapter_start clamp 到卷起始 {vol.chapter_start}")
            if st.chapter_end > vol.chapter_end:
                st.chapter_end = vol.chapter_end
                fixed.append(f"舞台{st.stage_id} chapter_end clamp 到卷结束 {vol.chapter_end}")

    # 4. 角色弧引用不存在角色 → 删除
    valid_arcs = []
    for a in state.character_arcs:
        if a.character_name and a.character_name not in char_names:
            fixed.append(f"删除心理弧（角色 {a.character_name} 不存在）")
        else:
            valid_arcs.append(a)
    state.character_arcs = valid_arcs

    # 5. 关系网 bond 引用不存在角色 → 删除
    valid_bonds = []
    for b in state.relationship_web.bonds:
        if (b.char_a and b.char_a not in char_names) or (b.char_b and b.char_b not in char_names):
            fixed.append(f"删除 bond {b.bond_id}（引用不存在角色 {b.char_a}/{b.char_b}）")
        else:
            valid_bonds.append(b)
    state.relationship_web.bonds = valid_bonds

    # 6. 能力 holder_name 引用不存在 → 清空
    if state.power_system:
        for ab in state.power_system.special_abilities:
            if ab.holder_name and ab.holder_name not in char_names:
                old = ab.holder_name
                ab.holder_name = ""
                fixed.append(f"能力《{ab.name}》清空不存在 holder_name={old}")

    # 7. 机缘 stage_id 引用不存在 → 清空
    for f in state.fortunes:
        if f.stage_id and f.stage_id not in stage_ids:
            old = f.stage_id
            f.stage_id = ""
            fixed.append(f"机缘《{f.name}》清空不存在 stage_id={old}")

    return fixed


def print_report(state: NovelState, do_autofix: bool = True) -> int:
    """打印一致性报告，返回 error 数量（调用方可据此决定是否中断）。
    do_autofix=True 时先跑 autofix，再 check_all——剩下的 issues 是真正需要人工/语义修的。
    """
    if do_autofix:
        try:
            fixed = autofix(state)
            if fixed:
                print(f"  🔧 SSoT 自动修了 {len(fixed)} 处确定性问题：")
                for line in fixed[:8]:
                    print(f"    · {line}")
                if len(fixed) > 8:
                    print(f"    ... 还有 {len(fixed)-8} 处")
        except Exception as e:
            print(f"  ⚠ SSoT autofix 失败（不影响报告）：{type(e).__name__}: {e}")
    issues = check_all(state)
    if not issues:
        print("  ✓ SSoT 一致性检查通过（0 issues）")
        return 0
    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]
    print(f"  SSoT 一致性检查：{len(errors)} 个错误，{len(warns)} 个警告（剩下需人工/语义修）")
    for i in issues:
        tag = "✗" if i["severity"] == "error" else "⚠"
        print(f"    {tag} [{i['area']}] {i['message']}")
    return len(errors)
