"""G2 世界组的 9 个节点：1A/1A2/1B/1C/1D/1E/1F/1G/1H。

  1A 力量体系 / 1A2 力量刻度 / 1B 卷结构 / 1C 势力 / 1D 世界观 /
  1E 世界观校验 / 1F 地理 / 1G 时间线 / 1H 经济

每个节点：调 v1 agent 函数 → 提取产物字段转 dict → 返回 patch。
"""
from __future__ import annotations

from state_v2 import NovelStateV2
from adapter import ensure_v1_env, load_or_build_v1_state, to_jsonable


def _common_skip(state: NovelStateV2, phase_id: str, label: str) -> dict | None:
    """已完成 phase 的统一短路。"""
    if phase_id in state.phases_done:
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成，跳过）"}
    return None


# ─────────────────────────────────────────────────────
#  Phase 1A：境界/力量体系
# ─────────────────────────────────────────────────────
def node_phase_1A(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1A", "力量体系")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.realm_designer import design_realm_system  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore

    print(f"  ▶ [Phase 1A] RealmDesigner：境界/力量体系")
    design_realm_system(v1)
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.power_system = None
            design_realm_system(s)
        review_and_regenerate(v1, "1A", _re)
    except Exception as e:
        print(f"  ⚠ 1A 模块审核失败（不阻塞）：{type(e).__name__}: {e}")
    mark_phase_done("1A", v1)

    return {
        "power_system": to_jsonable(v1.power_system),
        "phases_done": state.phases_done + ["1A"],
        "current_phase": "1A",
        "current_phase_label": "力量体系 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1A2：力量刻度
# ─────────────────────────────────────────────────────
def node_phase_1A2(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1A2", "力量刻度")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.realm_designer import design_power_scaling  # type: ignore
    from checkpoint import mark_phase_done                    # type: ignore

    print(f"  ▶ [Phase 1A2] 力量刻度（战力/寿命/神识/越级规则）")
    design_power_scaling(v1)
    mark_phase_done("1A2", v1)

    return {
        # 1A2 在 power_system 内部追加 scaling 字段——覆盖 1A 的 power_system 即可
        "power_system": to_jsonable(v1.power_system),
        "phases_done": state.phases_done + ["1A2"],
        "current_phase": "1A2",
        "current_phase_label": "力量刻度 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1B：卷结构
# ─────────────────────────────────────────────────────
def node_phase_1B(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1B", "卷结构")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.volume_planner import plan_all_volumes_dispatched  # type: ignore
    from checkpoint import mark_phase_done                          # type: ignore

    print(f"  ▶ [Phase 1B] VolumePlanner：起承转合分配 + 并发各卷详情")
    plan_all_volumes_dispatched(v1)
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.volumes = []
            plan_all_volumes_dispatched(s)
        review_and_regenerate(v1, "1B", _re)
    except Exception as e:
        print(f"  ⚠ 1B 模块审核失败（不阻塞）：{type(e).__name__}: {e}")
    mark_phase_done("1B", v1)

    return {
        "volumes": to_jsonable(v1.volumes) or [],
        "phases_done": state.phases_done + ["1B"],
        "current_phase": "1B",
        "current_phase_label": "卷结构 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1C：势力架构
# ─────────────────────────────────────────────────────
def node_phase_1C(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1C", "势力架构")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.faction_architect import design_factions  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore

    print(f"  ▶ [Phase 1C] FactionArchitect：势力架构")
    design_factions(v1)
    mark_phase_done("1C", v1)

    return {
        "factions": to_jsonable(v1.factions) or [],
        "phases_done": state.phases_done + ["1C"],
        "current_phase": "1C",
        "current_phase_label": "势力架构 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1D：世界观构建
# ─────────────────────────────────────────────────────
def node_phase_1D(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1D", "世界观")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.world_builder import build_world  # type: ignore
    from checkpoint import mark_phase_done         # type: ignore

    print(f"  ▶ [Phase 1D] WorldBuilder：世界观构建")
    build_world(v1)
    mark_phase_done("1D", v1)

    return {
        "world_setting": to_jsonable(v1.world_setting),
        "phases_done": state.phases_done + ["1D"],
        "current_phase": "1D",
        "current_phase_label": "世界观 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1E：世界观完整性校验
# ─────────────────────────────────────────────────────
def node_phase_1E(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1E", "世界观校验")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.world_builder import run_world_checklist  # type: ignore
    from checkpoint import mark_phase_done                 # type: ignore

    print(f"  ▶ [Phase 1E] 世界观完整性校验")
    gaps = run_world_checklist(v1) or []
    if gaps:
        print(f"  ⚠ 世界观还有 {len(gaps)} 处提示性缺失（非阻塞）：{gaps[:3]}")
    mark_phase_done("1E", v1)

    patch = {
        "world_checklist_gaps": [str(g) for g in gaps],
        "phases_done": state.phases_done + ["1E"],
        "current_phase": "1E",
        "current_phase_label": f"世界观校验 完成（{len(gaps)} 处提示）",
    }
    if gaps:
        patch["warnings"] = state.warnings + [{
            "level": "warn",
            "source": "phase:1E",
            "message": f"世界观 {len(gaps)} 处提示性缺失（非阻塞）：" + " / ".join(str(g)[:30] for g in gaps[:3]),
        }]
    return patch


# ─────────────────────────────────────────────────────
#  Phase 1F：地理
# ─────────────────────────────────────────────────────
def node_phase_1F(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1F", "地理")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.geography_designer import design_geography  # type: ignore
    from checkpoint import mark_phase_done                    # type: ignore

    print(f"  ▶ [Phase 1F] GeographyDesigner：区划/交通/距离矩阵")
    design_geography(v1)
    mark_phase_done("1F", v1)

    return {
        "geography": to_jsonable(v1.geography),
        "phases_done": state.phases_done + ["1F"],
        "current_phase": "1F",
        "current_phase_label": "地理 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1G：时间线
# ─────────────────────────────────────────────────────
def node_phase_1G(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1G", "时间线")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.timeline_anchor import design_timeline  # type: ignore
    from checkpoint import mark_phase_done               # type: ignore

    print(f"  ▶ [Phase 1G] TimelineAnchor：历史事件时间轴")
    design_timeline(v1)
    mark_phase_done("1G", v1)

    return {
        "timeline": to_jsonable(v1.timeline),
        "phases_done": state.phases_done + ["1G"],
        "current_phase": "1G",
        "current_phase_label": "时间线 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 1H：经济
# ─────────────────────────────────────────────────────
def node_phase_1H(state: NovelStateV2) -> dict:
    skip = _common_skip(state, "1H", "经济")
    if skip:
        return skip
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.economy_designer import design_economy  # type: ignore
    from checkpoint import mark_phase_done               # type: ignore

    print(f"  ▶ [Phase 1H] EconomyDesigner：货币/物价/财富曲线")
    design_economy(v1)
    mark_phase_done("1H", v1)

    return {
        "economy": to_jsonable(v1.economy),
        "phases_done": state.phases_done + ["1H"],
        "current_phase": "1H",
        "current_phase_label": "经济 完成",
    }
