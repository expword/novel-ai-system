"""
状态审计 —— 流水线跑完后（或任何时候）检查 state 里每个 section 是否真的生成到位。

用法：
    from persistence.state_audit import print_state_audit, audit_state
    print_state_audit(state)           # 控制台人眼可读
    report = audit_state(state)        # 程序化消费（web /api/state_audit）

审计原则：
  · 只看事实：字段是否为空、count 是否达到常识阈值
  · 空 = "未生成"，不判断"好坏"——好坏交给 validators
  · 对于 validators.py 里已有的 section，额外把 issues 列出来
"""
from __future__ import annotations
from typing import Any


# section_key -> (人类可读名, 检查函数)
# 检查函数返回 (status, count_detail_str, extra_issues_list)
# status: "ok" | "empty" | "partial"

def _audit_creative_intent(state) -> tuple[str, str, list[str]]:
    ci = getattr(state, "creative_intent", None)
    if not ci or not getattr(ci, "analyzed", False):
        return "empty", "未分析", []
    desc_len = len(getattr(ci, "raw_description", "") or "")
    return "ok", f"已分析（原始意图 {desc_len} 字）", []


def _audit_concept_pitch(state) -> tuple[str, str, list[str]]:
    cp = getattr(state, "concept_pitch", None)
    if not cp or not getattr(cp, "one_line_pitch", ""):
        return "empty", "未生成", []
    return "ok", f"一句话：{cp.one_line_pitch[:40]}", []


def _audit_master_outline(state) -> tuple[str, str, list[str]]:
    mo = getattr(state, "master_outline", None)
    if not mo or not getattr(mo, "generated", False):
        return "empty", "未生成", []
    slots = len(getattr(mo, "character_slots", []) or [])
    skel = len(getattr(mo, "faction_skeleton", []) or [])
    setp = len(getattr(mo, "plot_setpieces", []) or [])
    issues = []
    if slots < 5:
        issues.append(f"character_slots 仅 {slots} 个（期望 ≥ 10）")
    if skel < 2:
        issues.append(f"faction_skeleton 仅 {skel} 层（期望 ≥ 3）")
    status = "ok" if not issues else "partial"
    return status, f"槽位 {slots} / 骨架 {skel} 层 / 节点 {setp}", issues


def _audit_power_system(state) -> tuple[str, str, list[str]]:
    ps = getattr(state, "power_system", None)
    if not ps:
        return "empty", "未生成", []
    realms = len(getattr(ps, "realms", []) or [])
    stype = getattr(ps, "system_type", "") or "?"
    issues = []
    if realms < 3 and stype in ("realms", "skill_tiers"):
        issues.append(f"仅 {realms} 个境界/层级（期望 ≥ 3）")
    from utils.validators import validate_section
    issues.extend(validate_section(state, "power_system"))
    status = "ok" if not issues else "partial"
    return status, f"类型 {stype}｜{realms} 级", issues


def _audit_volumes(state) -> tuple[str, str, list[str]]:
    vols = getattr(state, "volumes", []) or []
    if not vols:
        return "empty", "未生成", []
    issues = []
    from config import NUM_VOLUMES
    if len(vols) < NUM_VOLUMES:
        issues.append(f"卷数 {len(vols)} < NUM_VOLUMES({NUM_VOLUMES})")
    from utils.validators import validate_section
    issues.extend(validate_section(state, "volumes"))
    status = "ok" if not issues else "partial"
    return status, f"{len(vols)} 卷", issues


def _audit_factions(state) -> tuple[str, str, list[str]]:
    factions = getattr(state, "factions", []) or []
    if not factions:
        return "empty", "未生成", []
    from utils.validators import validate_section
    issues = validate_section(state, "factions")
    tier_counts = {}
    for f in factions:
        tier_counts[getattr(f, "tier", 0)] = tier_counts.get(getattr(f, "tier", 0), 0) + 1
    tier_brief = " / ".join(f"T{t}:{c}" for t, c in sorted(tier_counts.items()))
    status = "ok" if not issues else "partial"
    return status, f"{len(factions)} 个（{tier_brief}）", issues


def _audit_world_setting(state) -> tuple[str, str, list[str]]:
    ws = getattr(state, "world_setting", "") or ""
    if len(ws) < 50:
        return "empty", f"过短（{len(ws)}字）", []
    return "ok", f"{len(ws)} 字", []


def _audit_geography(state) -> tuple[str, str, list[str]]:
    geo = getattr(state, "geography", None)
    regions = getattr(geo, "regions", []) if geo else []
    if not regions:
        return "empty", "未生成", []
    issues = []
    active = [r for r in regions if getattr(r, "importance", "") == "protagonist_active"]
    if len(active) < 2:
        issues.append(f"主角活跃区仅 {len(active)} 个（期望 ≥ 3）")
    if geo and not getattr(geo, "world_layout", ""):
        issues.append("world_layout 空（天下布局未生成）")
    if geo and not getattr(geo, "protagonist_route", []):
        issues.append("protagonist_route 空（主角路线未生成）")
    status = "ok" if not issues else "partial"
    return status, f"{len(regions)} 个区划（活跃 {len(active)}）", issues


def _audit_characters(state) -> tuple[str, str, list[str]]:
    chars = getattr(state, "characters", []) or []
    if not chars:
        return "empty", "未生成", []
    from utils.validators import validate_section
    issues = validate_section(state, "characters")
    roles = {}
    for c in chars:
        r = getattr(c.role, "value", str(c.role)) if hasattr(c, "role") else "?"
        roles[r] = roles.get(r, 0) + 1
    role_brief = " / ".join(f"{r}:{c}" for r, c in roles.items())
    status = "ok" if not issues else "partial"
    return status, f"{len(chars)} 人（{role_brief}）", issues


def _audit_timeline(state) -> tuple[str, str, list[str]]:
    tl = getattr(state, "timeline", None)
    events = getattr(tl, "events", []) if tl else []
    if not events:
        return "empty", "未生成", []
    return "ok", f"{len(events)} 条历史事件", []


def _audit_economy(state) -> tuple[str, str, list[str]]:
    ec = getattr(state, "economy", None)
    if not ec or not (getattr(ec, "currencies", None) or getattr(ec, "price_table", None)):
        return "empty", "未生成", []
    cur = len(getattr(ec, "currencies", []) or [])
    prices = len(getattr(ec, "price_table", []) or [])
    return "ok", f"{cur} 种货币 / {prices} 物价条目", []


def _audit_lines(state) -> tuple[str, str, list[str]]:
    """叙事线分两组存储：global_lines（全局）+ volume_lines（卷专属）。"""
    g = getattr(state, "global_lines", []) or []
    v = getattr(state, "volume_lines", []) or []
    total = len(g) + len(v)
    if total == 0:
        return "empty", "未生成", []
    issues = []
    # 简单合理性：phase 数量、章节范围
    no_phase = sum(1 for ln in g + v if not getattr(ln, "phases", None))
    if no_phase:
        issues.append(f"{no_phase} 条叙事线没有 phase（应该至少 3-4 个 phase 形成起承转合）")
    status = "ok" if not issues else "partial"
    return status, f"全局 {len(g)} 条 / 卷内 {len(v)} 条（共 {total}）", issues


def _audit_satisfaction(state) -> tuple[str, str, list[str]]:
    """爽点存在 state.satisfaction_points（list of SatisfactionPoint）"""
    sps = getattr(state, "satisfaction_points", []) or []
    if not sps:
        return "empty", "未生成", []
    issues = []
    # 强度分布
    high = sum(1 for sp in sps if getattr(sp, "intensity", 0) >= 8)
    if high == 0:
        issues.append(f"无高强度（≥8）爽点——大爽点至少 1-2 个")
    no_setup = sum(1 for sp in sps if not getattr(sp, "setup_chain", None))
    if no_setup:
        issues.append(f"{no_setup} 个爽点没有铺垫链")
    # 按卷分布检查：每卷至少 1 个高强度爽点（否则该卷没有"大爽"压轴）
    volumes = getattr(state, "volumes", []) or []
    if volumes:
        vols_no_high = []
        for v in volumes:
            vol_sps = [sp for sp in sps if getattr(sp, "volume", None) == v.index]
            if vol_sps and not any(getattr(sp, "intensity", 0) >= 8 for sp in vol_sps):
                vols_no_high.append(v.index)
            elif not vol_sps:
                vols_no_high.append(v.index)
        if vols_no_high:
            issues.append(f"{len(vols_no_high)} 卷无高强度爽点（卷 {vols_no_high[:6]}）——每卷至少 1 个 ≥8 强度爽点压轴")
    status = "ok" if not issues else "partial"
    return status, f"{len(sps)} 个爽点（{high} 个高强度8+）", issues


def _audit_foreshadows(state) -> tuple[str, str, list[str]]:
    fms = getattr(state, "foreshadow_items", []) or []
    if not fms:
        return "empty", "未生成", []
    return "ok", f"{len(fms)} 个伏笔", []


def _audit_twists(state) -> tuple[str, str, list[str]]:
    ts = getattr(state, "twist_system", None)
    chains = getattr(ts, "chains", []) if ts else []
    if not chains:
        return "empty", "未生成", []
    issues = []
    cross = sum(1 for c in chains if getattr(c, "scope", "") == "cross_volume")
    within = sum(1 for c in chains if getattr(c, "scope", "") == "within_volume")
    total_layers = sum(len(getattr(c, "layers", []) or []) for c in chains)
    empty_layers = [c.title for c in chains if not (getattr(c, "layers", []) or [])]
    if empty_layers:
        issues.append(f"{len(empty_layers)} 条链层未展开：{empty_layers[:3]}")
    if cross == 0:
        issues.append("无跨卷大反转（建议至少 1 条 brain_burning/mind_bending）")
    status = "ok" if not issues else "partial"
    return status, f"{len(chains)} 条（大 {cross}/小 {within}｜{total_layers} 层）", issues


def _audit_stages(state) -> tuple[str, str, list[str]]:
    """叙事舞台存在 state.story_stages（list of StoryStage）。"""
    stages = getattr(state, "story_stages", []) or []
    if not stages:
        return "empty", "未生成", []
    issues = []
    # 按卷分组统计
    from collections import Counter
    by_vol = Counter(getattr(s, "volume", 0) for s in stages)
    vols_with_stages = len(by_vol)
    no_substages = sum(1 for s in stages if not getattr(s, "sub_scenes", None))
    if no_substages:
        issues.append(f"{no_substages} 个舞台没有 sub_scenes（每个舞台应有 2-4 个子场景）")
    # 章节范围
    invalid_range = sum(
        1 for s in stages
        if (getattr(s, "chapter_start", 0) or 0) <= 0
        or (getattr(s, "chapter_end", 0) or 0) <= 0
        or (getattr(s, "chapter_start", 0) or 0) > (getattr(s, "chapter_end", 0) or 0)
    )
    if invalid_range:
        issues.append(f"{invalid_range} 个舞台的章节范围无效")
    vol_brief = " / ".join(f"V{v}:{c}" for v, c in sorted(by_vol.items()))
    status = "ok" if not issues else "partial"
    return status, f"{len(stages)} 个舞台（{vol_brief}）", issues


AUDIT_REGISTRY: list[tuple[str, str, Any]] = [
    ("creative_intent", "创作意图",       _audit_creative_intent),
    ("concept_pitch",   "立项",           _audit_concept_pitch),
    ("master_outline",  "全书蓝图",       _audit_master_outline),
    ("power_system",    "力量体系",       _audit_power_system),
    ("volumes",         "卷结构",         _audit_volumes),
    ("factions",        "势力格局",       _audit_factions),
    ("world_setting",   "世界观",         _audit_world_setting),
    ("geography",       "地理",           _audit_geography),
    ("timeline",        "时间线",         _audit_timeline),
    ("economy",         "经济",           _audit_economy),
    ("characters",      "人物档案",       _audit_characters),
    ("lines",           "叙事线",         _audit_lines),
    ("satisfaction",    "爽点系统",       _audit_satisfaction),
    ("foreshadows",     "伏笔",           _audit_foreshadows),
    ("twists",          "反转系统",       _audit_twists),
    ("stages",          "舞台",           _audit_stages),
]


def audit_state(state) -> dict:
    """
    返回结构化审计报告，给 web /api/state_audit 用。
    { "sections": [{"key","label","status","detail","issues"}], "summary": {"ok","partial","empty"} }
    """
    sections = []
    counts = {"ok": 0, "partial": 0, "empty": 0}
    for key, label, fn in AUDIT_REGISTRY:
        try:
            status, detail, issues = fn(state)
        except Exception as e:
            status, detail, issues = "empty", f"审计异常: {type(e).__name__}: {e}", []
        sections.append({
            "key": key, "label": label,
            "status": status, "detail": detail,
            "issues": issues,
        })
        counts[status] = counts.get(status, 0) + 1
    return {"sections": sections, "summary": counts}


def print_state_audit(state) -> None:
    """控制台打印审计结果，让用户一眼看出哪里缺哪里全。"""
    report = audit_state(state)
    c = report["summary"]
    print("\n═══ 状态审计（不盲目运行）═══")
    print(f"  OK {c.get('ok',0)} ｜ 部分 {c.get('partial',0)} ｜ 未生成 {c.get('empty',0)}")
    print()
    for s in report["sections"]:
        icon = {"ok": "✓", "partial": "⚠", "empty": "✗"}.get(s["status"], "?")
        label = s["label"].ljust(10, "　")
        print(f"  {icon} {label} {s['detail']}")
        for iss in (s["issues"] or [])[:3]:
            print(f"      · {iss}")
    print("═══════════════════════════════\n")
