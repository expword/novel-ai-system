"""卷级 5 节点真实版本——包 v1 prepare_volume_planning 里那 5 个 phase。

每个节点都用 state.current_volume_index 取当前卷，对该卷跑相应 phase。
phase id 命名：`4_<short>_<volume_index>`（与 v1 progress 兼容）。
"""
from __future__ import annotations

from state_v2 import NovelStateV2
from adapter import ensure_v1_env, load_or_build_v1_state, to_jsonable


def _vol_skip(state: NovelStateV2, short: str, label: str,
               advance: bool = False) -> dict | None:
    vi = state.current_volume_index
    phase_id = f"4_{short}_{vi}"
    if phase_id in state.phases_done:
        patch = {"current_phase": phase_id,
                 "current_phase_label": f"V{vi} {label}（已完成，跳过）"}
        if advance:
            patch["current_volume_index"] = vi + 1
        return patch
    return None


def _vol_done(state, short: str, label: str, advance: bool = False,
               extra: dict | None = None) -> dict:
    vi = state.current_volume_index
    phase_id = f"4_{short}_{vi}"
    patch = {
        "phases_done": state.phases_done + [phase_id],
        "current_phase": phase_id,
        "current_phase_label": f"V{vi} {label} 完成",
    }
    if advance:
        patch["current_volume_index"] = vi + 1
    if extra:
        patch.update(extra)
    return patch


def node_vol_stage(state: NovelStateV2) -> dict:
    skip = _vol_skip(state, "stage", "卷叙事舞台")
    if skip: return skip
    vi = state.current_volume_index
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.volume_stage_designer import design_volume_stages  # type: ignore
    from checkpoint import mark_phase_done                            # type: ignore
    print(f"  ▶ [V{vi}·4-stage] 叙事舞台设计")
    design_volume_stages(v1, vi)
    mark_phase_done(f"4_stage_{vi}", v1)
    return _vol_done(state, "stage", "卷叙事舞台", extra={
        # story_stages 是全局 list（含 volume 字段），全量回写
        "story_stages": to_jsonable(getattr(v1, "story_stages", [])) or [],
    })


def node_vol_beats(state: NovelStateV2) -> dict:
    skip = _vol_skip(state, "beats", "主角舞台节拍")
    if skip: return skip
    vi = state.current_volume_index
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    # _beats_for_volume 是 director 内部辅助；调它需要先找到入口
    # v1 director 是 from director import _beats_for_volume，但 _beats_for_volume 是 module-level？
    from director import _beats_for_volume  # type: ignore
    from checkpoint import mark_phase_done   # type: ignore
    print(f"  ▶ [V{vi}·4-beats] 主角舞台节拍")
    _beats_for_volume(v1, vi)
    mark_phase_done(f"4_beats_{vi}", v1)
    return _vol_done(state, "beats", "主角舞台节拍", extra={
        "protagonist_journey": to_jsonable(v1.protagonist_journey),
    })


def node_vol_outline(state: NovelStateV2) -> dict:
    skip = _vol_skip(state, "vol", "逐章大纲")
    if skip: return skip
    vi = state.current_volume_index
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.volume_planner import plan_volume_chapters  # type: ignore
    from checkpoint import mark_phase_done                    # type: ignore
    print(f"  ▶ [V{vi}·4-vol] 逐章大纲")
    plan_volume_chapters(v1, vi)
    mark_phase_done(f"4_vol{vi}", v1)
    return _vol_done(state, "vol", "逐章大纲", extra={
        "volumes": to_jsonable(v1.volumes) or [],
    })


def node_vol_ctp(state: NovelStateV2) -> dict:
    skip = _vol_skip(state, "ctp", "章节类型")
    if skip: return skip
    vi = state.current_volume_index
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.chapter_type_planner import plan_chapter_types  # type: ignore
    from checkpoint import mark_phase_done                        # type: ignore
    print(f"  ▶ [V{vi}·4-ctp] 章节类型分布")
    plan_chapter_types(v1, vi)
    mark_phase_done(f"4_ctp_{vi}", v1)
    return _vol_done(state, "ctp", "章节类型", extra={
        "chapter_type_plans": to_jsonable(getattr(v1, "chapter_type_plans", [])) or [],
    })


def node_vol_lifecycle(state: NovelStateV2) -> dict:
    """lifecycle 落章——把粗粒度 lifecycle 节点（target_chapter=0）按 node_type
    启发式分到具体章。同时推进 current_volume_index 到下一卷。"""
    skip = _vol_skip(state, "lifecycle", "lifecycle 落章", advance=True)
    if skip: return skip
    vi = state.current_volume_index
    ensure_v1_env(state.project_id)
    v1 = load_or_build_v1_state(state)
    from agents.ability_roadmap_planner import assign_chapter_to_lifecycle_nodes  # type: ignore
    from checkpoint import mark_phase_done                                          # type: ignore
    print(f"  ▶ [V{vi}·4-lifecycle] lifecycle 节点落章")
    # 已写章信息（v2 暂没章级写作，传空 set）
    count = assign_chapter_to_lifecycle_nodes(v1, vi, set())
    if count:
        print(f"  ✓ V{vi} 落章 {count} 个 lifecycle 节点")
    mark_phase_done(f"4_lifecycle_{vi}", v1)
    return _vol_done(state, "lifecycle", "lifecycle 落章", advance=True, extra={
        "power_system": to_jsonable(v1.power_system),
        "satisfaction_points": to_jsonable(v1.satisfaction_points) or [],
    })
