"""G3 人物组的 6 个节点：2(2A)/2A2/2B/2C/2D/2C2。

  2(2A) 人物档案 / 2A2 主要人物深化 / 2B 关系网络 /
  2C 特殊能力 / 2D 心理弧光 / 2C2 能力路线图（新）
"""
from __future__ import annotations

from state_v2 import NovelStateV2
from adapter import ensure_v1_env, load_or_build_v1_state, to_jsonable


def _skip(state: NovelStateV2, phase_id: str, label: str):
    if phase_id in state.phases_done:
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成，跳过）"}
    return None


def node_phase_2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2", "人物档案")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.character_designer import design_all_characters  # type: ignore
    from checkpoint import mark_phase_done                         # type: ignore

    print(f"  ▶ [Phase 2/2A] CharacterDesigner：核心人物档案（主角圈→盟友→反派→卷内）")
    try:
        design_all_characters(v1)
    except Exception as e:
        print(f"  ⚠ 2A 部分异常：{type(e).__name__}: {e}（已生成 {len(v1.characters)} 个）")
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.characters = []
            design_all_characters(s)
        review_and_regenerate(v1, "2A", _re)
    except Exception as e:
        print(f"  ⚠ 2A 模块审核失败（不阻塞）：{type(e).__name__}: {e}")
    mark_phase_done("2", v1)

    return {
        "characters": to_jsonable(v1.characters) or [],
        "phases_done": state.phases_done + ["2"],
        "current_phase": "2",
        "current_phase_label": "人物档案 完成",
    }


def node_phase_2A2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2A2", "人物深化")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.major_supporting_refiner import refine_major_characters  # type: ignore
    from checkpoint import mark_phase_done                                  # type: ignore

    print(f"  ▶ [Phase 2A2] 主角+主要配角+反派细腻深化")
    refine_major_characters(v1)
    mark_phase_done("2A2", v1)

    return {
        "characters": to_jsonable(v1.characters) or [],
        "phases_done": state.phases_done + ["2A2"],
        "current_phase": "2A2",
        "current_phase_label": "人物深化 完成",
    }


def node_phase_2B(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2B", "关系网络")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.character_web import design_relationship_web  # type: ignore
    from checkpoint import mark_phase_done                      # type: ignore

    print(f"  ▶ [Phase 2B] RelationshipWebDesigner：人物关系网络")
    design_relationship_web(v1)
    mark_phase_done("2B", v1)

    return {
        "relationship_web": to_jsonable(v1.relationship_web),
        "phases_done": state.phases_done + ["2B"],
        "current_phase": "2B",
        "current_phase_label": "关系网络 完成",
    }


def node_phase_2C(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2C", "特殊能力")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.realm_designer import design_special_abilities, bind_abilities_to_characters  # type: ignore
    from checkpoint import mark_phase_done                                                       # type: ignore

    print(f"  ▶ [Phase 2C] SpecialAbilityDesigner：能力设定 + 绑定持有者")
    design_special_abilities(v1)
    bind_abilities_to_characters(v1)
    mark_phase_done("2C", v1)

    return {
        # 2C 更新 power_system.special_abilities + 角色 holder
        "power_system": to_jsonable(v1.power_system),
        "characters": to_jsonable(v1.characters) or [],
        "phases_done": state.phases_done + ["2C"],
        "current_phase": "2C",
        "current_phase_label": "特殊能力 完成",
    }


def node_phase_2D(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2D", "心理弧光")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.character_arc_designer import design_character_arcs  # type: ignore
    from checkpoint import mark_phase_done                              # type: ignore

    print(f"  ▶ [Phase 2D] CharacterArcDesigner：每人一条成长弧")
    design_character_arcs(v1)
    mark_phase_done("2D", v1)

    return {
        "character_arcs": to_jsonable(v1.character_arcs) or [],
        "phases_done": state.phases_done + ["2D"],
        "current_phase": "2D",
        "current_phase_label": "心理弧光 完成",
    }


def node_phase_2C2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "2C2", "能力路线图")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.ability_roadmap_planner import run_phase_2c2  # type: ignore
    from checkpoint import mark_phase_done                     # type: ignore

    print(f"  ▶ [Phase 2C2] AbilityRoadmapPlanner：金手指 lifecycle + 反向 SP + 标 arc")
    run_phase_2c2(v1)
    mark_phase_done("2C2", v1)

    return {
        # 2C2 同时修改 power_system / satisfaction_points / character_arcs
        "power_system": to_jsonable(v1.power_system),
        "satisfaction_points": to_jsonable(v1.satisfaction_points) or [],
        "character_arcs": to_jsonable(v1.character_arcs) or [],
        "phases_done": state.phases_done + ["2C2"],
        "current_phase": "2C2",
        "current_phase_label": "能力路线图 完成",
    }
