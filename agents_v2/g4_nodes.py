"""G4 情节组 11 节点：3A/3B/3B2/3C/3D/3D2/3E3/3E/3E2/3F/3G。

  3A 全局叙事线 / 3B 卷内叙事线 / 3B2 冲突阶梯 / 3C 爽点系统 /
  3D 节奏 / 3D2 情绪曲线 / 3E3 反转系统（先于 3E）/ 3E 伏笔 /
  3E2 红鲱鱼 / 3F 机缘 / 3G 主角历程

依赖：3E3 必须在 3E 之前（反转层的 clues 给伏笔阶段铺路）。其余按 director 顺序。
"""
from __future__ import annotations

from state_v2 import NovelStateV2
from adapter import ensure_v1_env, load_or_build_v1_state, to_jsonable


def _skip(state: NovelStateV2, phase_id: str, label: str):
    if phase_id in state.phases_done:
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成，跳过）"}
    return None


def node_phase_3A(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3A", "全局叙事线")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.line_planner import plan_global_lines    # type: ignore
    from checkpoint import mark_phase_done                # type: ignore
    print("  ▶ [Phase 3A] LinePlanner：全局叙事线")
    plan_global_lines(v1)
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.global_lines = []
            plan_global_lines(s)
        review_and_regenerate(v1, "3A", _re)
    except Exception as e:
        print(f"  ⚠ 3A 模块审核失败：{type(e).__name__}: {e}")
    mark_phase_done("3A", v1)
    return {
        "global_lines": to_jsonable(v1.global_lines) or [],
        "phases_done": state.phases_done + ["3A"],
        "current_phase": "3A", "current_phase_label": "全局叙事线 完成",
    }


def node_phase_3B(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3B", "卷内叙事线")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.line_planner import plan_all_volume_lines_parallel  # type: ignore
    from checkpoint import mark_phase_done                            # type: ignore
    print("  ▶ [Phase 3B] LinePlanner：各卷专属叙事线（并发）")
    plan_all_volume_lines_parallel(v1)
    mark_phase_done("3B", v1)
    return {
        "volume_lines": to_jsonable(v1.volume_lines) or [],
        "phases_done": state.phases_done + ["3B"],
        "current_phase": "3B", "current_phase_label": "卷内叙事线 完成",
    }


def node_phase_3B2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3B2", "冲突阶梯")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.conflict_ladder import design_conflict_ladder  # type: ignore
    from checkpoint import mark_phase_done                      # type: ignore
    print("  ▶ [Phase 3B2] ConflictLadder：冲突类型+层级")
    design_conflict_ladder(v1)
    mark_phase_done("3B2", v1)
    return {
        "conflict_ladder": to_jsonable(v1.conflict_ladder),
        "phases_done": state.phases_done + ["3B2"],
        "current_phase": "3B2", "current_phase_label": "冲突阶梯 完成",
    }


def node_phase_3C(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3C", "爽点系统")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.satisfaction_system import plan_all_satisfaction_points  # type: ignore
    from checkpoint import mark_phase_done                                  # type: ignore
    print("  ▶ [Phase 3C] SatisfactionSystem：爽点规划")
    plan_all_satisfaction_points(v1)
    mark_phase_done("3C", v1)
    return {
        "satisfaction_points": to_jsonable(v1.satisfaction_points) or [],
        "phases_done": state.phases_done + ["3C"],
        "current_phase": "3C", "current_phase_label": "爽点系统 完成",
    }


def node_phase_3D(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3D", "节奏")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.rhythm_designer import design_all_rhythms  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore
    print("  ▶ [Phase 3D] RhythmDesigner：情节节奏")
    design_all_rhythms(v1)
    mark_phase_done("3D", v1)
    return {
        "rhythm_plans": to_jsonable(v1.rhythm_plans) or [],
        "phases_done": state.phases_done + ["3D"],
        "current_phase": "3D", "current_phase_label": "节奏 完成",
    }


def node_phase_3D2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3D2", "情绪曲线")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.emotion_curve import design_emotion_curve  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore
    print("  ▶ [Phase 3D2] EmotionCurve：每卷基调+低谷+高点")
    design_emotion_curve(v1)
    mark_phase_done("3D2", v1)
    return {
        "emotion_curve": to_jsonable(v1.emotion_curve),
        "phases_done": state.phases_done + ["3D2"],
        "current_phase": "3D2", "current_phase_label": "情绪曲线 完成",
    }


def node_phase_3E3(state: NovelStateV2) -> dict:
    """先于 3E：反转链先声明 clues，让 3E 伏笔阶段优先满足这些 clues。"""
    if (s := _skip(state, "3E3", "反转系统")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.twist_designer import design_twists  # type: ignore
    from state_v2 import TwistSystem as _TwistSystem    # type: ignore
    from checkpoint import mark_phase_done            # type: ignore
    print("  ▶ [Phase 3E3] TwistDesigner：反转系统（先于伏笔）")
    design_twists(v1)
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.twist_system = _TwistSystem()
            design_twists(s)
        review_and_regenerate(v1, "3E3", _re)
    except Exception as e:
        print(f"  ⚠ 3E3 模块审核失败：{type(e).__name__}: {e}")
    mark_phase_done("3E3", v1)
    return {
        "twist_system": to_jsonable(v1.twist_system),
        "phases_done": state.phases_done + ["3E3"],
        "current_phase": "3E3", "current_phase_label": "反转系统 完成",
    }


def node_phase_3E(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3E", "伏笔")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.foreshadow_manager import plan_all_foreshadowing  # type: ignore
    from checkpoint import mark_phase_done                          # type: ignore
    print("  ▶ [Phase 3E] ForeshadowManager：伏笔体系（为反转铺路）")
    plan_all_foreshadowing(v1)
    mark_phase_done("3E", v1)
    return {
        "foreshadow_items": to_jsonable(v1.foreshadow_items) or [],
        "phases_done": state.phases_done + ["3E"],
        "current_phase": "3E", "current_phase_label": "伏笔 完成",
    }


def node_phase_3E2(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3E2", "红鲱鱼")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.foreshadow_manager import plan_red_herrings  # type: ignore
    from checkpoint import mark_phase_done                    # type: ignore
    print("  ▶ [Phase 3E2] 红鲱鱼（假线索）规划")
    plan_red_herrings(v1)
    mark_phase_done("3E2", v1)
    return {
        "red_herrings": to_jsonable(v1.red_herrings) or [],
        "phases_done": state.phases_done + ["3E2"],
        "current_phase": "3E2", "current_phase_label": "红鲱鱼 完成",
    }


def node_phase_3F(state: NovelStateV2) -> dict:
    if (s := _skip(state, "3F", "机缘")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.fortune_planner import plan_all_fortunes  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore
    print("  ▶ [Phase 3F] FortunePlanner：机缘体系")
    plan_all_fortunes(v1)
    mark_phase_done("3F", v1)
    return {
        "fortunes": to_jsonable(v1.fortunes) or [],
        "phases_done": state.phases_done + ["3F"],
        "current_phase": "3F", "current_phase_label": "机缘 完成",
    }


def node_phase_3G(state: NovelStateV2) -> dict:
    """主角历程：卷级里程碑。内核（overall_theme/fatal_flaw）在 0.6 已定，这里加 milestones。"""
    if (s := _skip(state, "3G", "主角历程")): return s
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.protagonist_journey import _step1_overall_arc, _step2_volume_milestones  # type: ignore
    from checkpoint import mark_phase_done                                                 # type: ignore
    print("  ▶ [Phase 3G] ProtagonistJourney：卷级 milestones")
    if not v1.protagonist_journey.overall_theme:
        print("    ⓘ 内核未填，补跑 step1_overall_arc")
        _step1_overall_arc(v1)
    _step2_volume_milestones(v1)
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        def _re(s):
            s.protagonist_journey.milestones = []
            _step2_volume_milestones(s)
        review_and_regenerate(v1, "3G", _re)
    except Exception as e:
        print(f"  ⚠ 3G 模块审核失败：{type(e).__name__}: {e}")
    mark_phase_done("3G", v1)
    return {
        "protagonist_journey": to_jsonable(v1.protagonist_journey),
        "phases_done": state.phases_done + ["3G"],
        "current_phase": "3G", "current_phase_label": "主角历程 完成",
    }
