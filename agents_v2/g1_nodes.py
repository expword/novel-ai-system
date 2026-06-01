"""G1 意图组的 4 个节点：-1（意图分析）/ 0（立项三件套）/ 0.5（全书蓝图）/ 0.6（主角内核）。

每个节点：
  · 接 NovelStateV2，返回 patch dict
  · 内部走 adapter：构造 v1 NovelState → 调旧 agent 函数 → 提取产物 → 转 dict patch
  · 标 phases_done + current_phase（供 web/CLI 显示）

LangGraph 的 reducer：默认 dict 字段是"完全替换"。list 字段 phases_done 我们手动做累加（每次返回时构造完整新 list）。
后续如果要 append-only 语义，可改用 Annotated[list, add] reducer。
"""
from __future__ import annotations

from state_v2 import NovelStateV2
from adapter import ensure_v1_env, load_or_build_v1_state, to_jsonable


# ─────────────────────────────────────────────────────
#  Phase -1：意图分析
# ─────────────────────────────────────────────────────
def node_phase_minus1(state: NovelStateV2) -> dict:
    """从 raw_description 拆出主题/动机/主角原型/读者画像/卖点钩子……"""
    if "-1" in state.phases_done:
        return {"current_phase": "-1", "current_phase_label": "意图分析（已完成，跳过）"}
    if not state.intent_description:
        # 没传意图描述就跳过（让用户后续补充再走 regen）
        return {
            "phases_done": state.phases_done + ["-1"],
            "current_phase": "-1",
            "current_phase_label": "意图分析（无 intent，跳过）",
            "warnings": state.warnings + [{
                "level": "warn",
                "source": "phase:-1",
                "message": "未提供 intent_description——下游 phase 将缺少作者意图信息",
            }],
        }

    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.intent_analyzer import analyze_intent  # type: ignore
    from checkpoint import mark_phase_done             # type: ignore

    print(f"  ▶ [Phase -1] 意图分析：{state.intent_description[:60]}...")
    analyze_intent(v1, state.intent_description)
    mark_phase_done("-1", v1)

    return {
        "creative_intent": to_jsonable(v1.creative_intent),
        "phases_done": state.phases_done + ["-1"],
        "current_phase": "-1",
        "current_phase_label": "意图分析 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 0：立项三件套（ConceptPitch + TropeLibrary + ToneManual）
# ─────────────────────────────────────────────────────
def node_phase_0(state: NovelStateV2) -> dict:
    if "0" in state.phases_done:
        return {"current_phase": "0", "current_phase_label": "立项三件套（已完成，跳过）"}

    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.concept_pitch import design_concept_phase  # type: ignore
    from checkpoint import mark_phase_done                  # type: ignore

    print(f"  ▶ [Phase 0] 立项：卖点 + 套路库 + 文风手册")
    design_concept_phase(v1)
    mark_phase_done("0", v1)

    return {
        "concept_pitch": to_jsonable(v1.concept_pitch),
        "trope_library": to_jsonable(v1.trope_library),
        "tone_manual": to_jsonable(v1.tone_manual),
        "phases_done": state.phases_done + ["0"],
        "current_phase": "0",
        "current_phase_label": "立项三件套 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 0.5：全书骨架蓝图（MasterDispatcher）
# ─────────────────────────────────────────────────────
def node_phase_0_5(state: NovelStateV2) -> dict:
    if "0.5" in state.phases_done:
        return {"current_phase": "0.5", "current_phase_label": "全书蓝图（已完成，跳过）"}

    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.master_dispatcher import dispatch_master_outline  # type: ignore
    from checkpoint import mark_phase_done                          # type: ignore

    print(f"  ▶ [Phase 0.5] MasterDispatcher：全书骨架蓝图")
    dispatch_master_outline(v1)
    mark_phase_done("0.5", v1)

    return {
        "master_outline": to_jsonable(v1.master_outline),
        "phases_done": state.phases_done + ["0.5"],
        "current_phase": "0.5",
        "current_phase_label": "全书蓝图 完成",
    }


# ─────────────────────────────────────────────────────
#  Phase 0.6：主角内核（创伤/真实目标/致命弱点）
# ─────────────────────────────────────────────────────
def node_phase_0_6(state: NovelStateV2) -> dict:
    if "0.6" in state.phases_done:
        return {"current_phase": "0.6", "current_phase_label": "主角内核（已完成，跳过）"}

    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)

    from agents.protagonist_journey import design_protagonist_core  # type: ignore
    from checkpoint import mark_phase_done                            # type: ignore

    print(f"  ▶ [Phase 0.6] ProtagonistCore：核心创伤 / 真实目标 / 致命弱点")
    design_protagonist_core(v1)

    # 模块审核 + 重生（与 v1 director 行为一致）
    try:
        from agents.module_reviewer import review_and_regenerate  # type: ignore
        review_and_regenerate(v1, "0.6", lambda s: design_protagonist_core(s))
    except Exception as e:
        print(f"  ⚠ 0.6 模块审核失败（不阻塞）：{type(e).__name__}: {e}")

    mark_phase_done("0.6", v1)

    # 只取主角内核核心字段（不收 milestones / stage_beats——那是 3G/4 阶段才填的）
    pj = v1.protagonist_journey
    core = {}
    if pj:
        core = {
            "overall_theme": getattr(pj, "overall_theme", "") or "",
            "fatal_flaw": getattr(pj, "fatal_flaw", "") or "",
            "core_trauma": getattr(pj, "core_trauma", "") or getattr(pj, "trauma", "") or "",
            "real_goal": getattr(pj, "real_goal", "") or getattr(pj, "true_goal", "") or "",
            # 完整 journey 留到后续阶段；这里只存核心 4 字段供阶段 1 验证
            "_full_journey": to_jsonable(pj),
        }

    return {
        "protagonist_journey": core,
        "phases_done": state.phases_done + ["0.6"],
        "current_phase": "0.6",
        "current_phase_label": "主角内核 完成",
    }
