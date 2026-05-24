"""
Regenerate —— 前端触发的上下游重建。

用户改了 A，系统应该按需重建 B/C/D。
每次重建前自动 version_control.snapshot 以便回退。

提供的重建动作：
- concept_pitch        → 用新 pitch 重算 trope_library + tone_manual
- trope_library        → 重算 tone_manual
- tone_manual          → 只重算自己（通常用户直接编辑完就行）
- volume_structure     → 重算整本书 book_structure 下的章节大纲
- volume_outline(v)    → 某卷的章节大纲
- stages(v)            → 某卷叙事舞台
- chapter_types(v)     → 某卷章节类型分派
- character_arc(name)  → 某角色心理弧
- character_refine(name) → 某角色细腻刻画
- conflict_ladder      → 冲突阶梯
- emotion_curve        → 情绪曲线
- special_abilities    → 特殊能力
- relationships        → 人物关系网

所有重建都通过 agents.* 原函数调用，不重新实现逻辑。
"""
from __future__ import annotations
from typing import Callable

from persistence.checkpoint import save_state, load_state
from persistence import version_control


def _load_or_error():
    state = load_state()
    if state is None:
        raise RuntimeError("state.json 不存在。请先跑 python main.py 生成初始状态。")
    return state


def _snapshot_and_save(state, label: str):
    version_control.snapshot(state, label=label, notes="web UI edit")
    save_state(state)


def _assert_upstream(state, label: str, **specs) -> None:
    """重建动作的上游守卫——缺就 raise，web 路由会把这个错给前端。

    与 agents.require_upstream 互补：那边返回 bool 让 agent 自行 return；
    这边重建是用户主动触发，缺上游就该明确报错让用户先建上游。
    """
    missing = []
    for key, predicate in specs.items():
        try:
            ok = bool(predicate(state))
        except Exception:
            ok = False
        if not ok:
            missing.append(key)
    if not missing:
        return
    msg = f"重建【{label}】失败——上游缺失：{' / '.join(missing)}。请先重建对应上游模块再回来。"
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(level="error", source=f"regen:{label}", message=msg)
    except Exception:
        pass
    raise RuntimeError(msg)


# ═══════════════════════════════════════════════════════
#  各重建动作——每个都接受可选 state 参数（复用已加载的），或自加载
# ═══════════════════════════════════════════════════════

def regen_trope_library(state=None) -> dict:
    from agents.concept_pitch import _design_trope_library
    state = state or _load_or_error()
    _design_trope_library(state)
    _snapshot_and_save(state, "regen_trope_library")
    return _dump_trope(state)


def regen_tone_manual(state=None) -> dict:
    from agents.concept_pitch import _design_tone_manual
    state = state or _load_or_error()
    _design_tone_manual(state)
    _snapshot_and_save(state, "regen_tone_manual")
    return _dump_tone(state)


def regen_after_concept_pitch() -> dict:
    """concept_pitch 改了——重算 trope + tone。"""
    from agents.concept_pitch import _design_trope_library, _design_tone_manual
    state = _load_or_error()
    _design_trope_library(state)
    _design_tone_manual(state)
    _snapshot_and_save(state, "regen_after_pitch")
    return {"trope_library": _dump_trope(state), "tone_manual": _dump_tone(state)}


def regen_volume_outline(volume_index: int) -> dict:
    """某卷章节大纲。"""
    from agents.volume_planner import plan_volume_chapters
    state = _load_or_error()
    vol = state.get_volume(volume_index)
    if not vol:
        raise RuntimeError(f"第 {volume_index} 卷不存在")
    vol.chapter_outlines = []
    plan_volume_chapters(state, volume_index)
    _snapshot_and_save(state, f"regen_outline_v{volume_index}")
    return {"chapter_outlines": vol.chapter_outlines}


def regen_chapter_outline(chapter_index: int) -> dict:
    """**只重生一章 outline**——保持 stage_id 不变、相邻章不动。

    用例：staleness 警告说"V1Ch9 outline 失效"，重生整 80 章太重，
    直接调本函数只动一章。返回 {old_goal, new_goal, old_title, new_title} 让 UI 对比。
    """
    from agents.volume_planner import regen_one_outline
    state = _load_or_error()
    result = regen_one_outline(state, chapter_index)
    _snapshot_and_save(state, f"regen_outline_ch{chapter_index}")
    return result


def regen_stages(volume_index: int) -> dict:
    from agents.stage_architect import design_volume_stages
    from agents.volume_planner import plan_volume_chapters
    state = _load_or_error()
    vol = state.get_volume(volume_index)
    if not vol:
        raise RuntimeError(f"第 {volume_index} 卷不存在")
    # 清掉旧舞台
    state.story_stages = [s for s in state.story_stages if s.volume != volume_index]
    design_volume_stages(state, volume_index)
    # 舞台是章纲的上游。舞台重建后旧 outline 的 stage_id/节奏范围必然失效，
    # 这里同步重建，避免 UI 保存出“新舞台 + 旧章纲”的混合态。
    vol.chapter_outlines = []
    plan_volume_chapters(state, volume_index)
    _snapshot_and_save(state, f"regen_stages_v{volume_index}")
    return {
        "stages": [s.__dict__ for s in state.story_stages if s.volume == volume_index],
        "chapter_outlines": vol.chapter_outlines,
    }


def regen_chapter_types(volume_index: int) -> dict:
    from agents.chapter_type_planner import plan_chapter_types
    state = _load_or_error()
    state.chapter_type_plans = [p for p in state.chapter_type_plans if p.volume != volume_index]
    plan_chapter_types(state, volume_index)
    _snapshot_and_save(state, f"regen_chaptertypes_v{volume_index}")
    ctp = next((p for p in state.chapter_type_plans if p.volume == volume_index), None)
    return {
        "type_distribution": ctp.type_distribution if ctp else {},
        "per_chapter": [a.__dict__ for a in ctp.per_chapter] if ctp else [],
    }


def regen_character_refine(name: str) -> dict:
    """单独重新深化某角色的 VoiceProfile/细腻字段。"""
    from agents.major_supporting_refiner import refine_major_characters
    state = _load_or_error()
    char = state.get_character(name)
    if not char:
        raise RuntimeError(f"角色 {name} 不存在")
    # 清掉旧刻画（让 refine 觉得它没做过）
    char.signature_mannerisms = []
    refine_major_characters(state)
    _snapshot_and_save(state, f"regen_refine_{name}")
    return {"character": _char_to_dict(char)}


def regen_conflict_ladder() -> dict:
    from agents.conflict_ladder import design_conflict_ladder
    state = _load_or_error()
    design_conflict_ladder(state)
    _snapshot_and_save(state, "regen_conflict_ladder")
    return {"entries": [e.__dict__ for e in state.conflict_ladder.entries]}


def regen_emotion_curve() -> dict:
    from agents.emotion_curve import design_emotion_curve
    state = _load_or_error()
    design_emotion_curve(state)
    _snapshot_and_save(state, "regen_emotion_curve")
    return {"notes": [n.__dict__ for n in state.emotion_curve.notes]}


def regen_special_abilities() -> dict:
    from agents.realm_designer import design_special_abilities, bind_abilities_to_characters
    state = _load_or_error()
    if not state.power_system:
        raise RuntimeError("力量体系未设计")
    state.power_system.special_abilities = []
    design_special_abilities(state)
    bind_abilities_to_characters(state)
    _snapshot_and_save(state, "regen_abilities")
    return {"abilities": [_ability_to_dict(a) for a in state.power_system.special_abilities]}


def regen_relationships() -> dict:
    from agents.character_web import design_relationship_web
    state = _load_or_error()
    design_relationship_web(state)
    _snapshot_and_save(state, "regen_relationships")
    return _dump_relationship_web(state)


def regen_geography() -> dict:
    from agents.geography_designer import design_geography
    state = _load_or_error()
    design_geography(state)
    _snapshot_and_save(state, "regen_geography")
    return _dump_geo(state)


def regen_economy() -> dict:
    from agents.economy_designer import design_economy
    state = _load_or_error()
    design_economy(state)
    _snapshot_and_save(state, "regen_economy")
    return _dump_economy(state)


def regen_power_scaling() -> dict:
    """只补充战力刻度（不重建整个体系）——适合已有体系只想补/刷战力值的场景。"""
    from agents.realm_designer import design_power_scaling
    state = _load_or_error()
    design_power_scaling(state)
    _snapshot_and_save(state, "regen_power_scaling")
    return {"realms": [_realm_to_dict(r) for r in state.power_system.realms]} if state.power_system else {}


def regen_power_system() -> dict:
    """
    真·重建力量体系——清空现有 power_system，从头重跑 design_realm_system + power_scaling。
    这才是用户点"🔄 重建本模块"应该得到的行为。
    """
    from agents.realm_designer import design_realm_system, design_power_scaling
    state = _load_or_error()
    # 清空现有体系，让 LLM 重新设计
    state.power_system = None
    design_realm_system(state)
    if state.power_system and state.power_system.realms:
        design_power_scaling(state)
    _snapshot_and_save(state, "regen_power_system")
    if not state.power_system:
        return {}
    return {
        "system_name": state.power_system.system_name,
        "system_description": state.power_system.system_description,
        "realms": [_realm_to_dict(r) for r in state.power_system.realms],
        "special_abilities": [_ability_to_dict(a) for a in state.power_system.special_abilities],
    }


# ═══════════════════════════════════════════════════════
#  回退
# ═══════════════════════════════════════════════════════

def rollback(timestamp: str) -> dict:
    state = version_control.rollback(timestamp)
    if state is None:
        raise RuntimeError(f"回退失败：未找到 timestamp={timestamp}")
    return {"status": "ok", "reloaded_at": timestamp}


# ═══════════════════════════════════════════════════════
#  dict 转换辅助
# ═══════════════════════════════════════════════════════

def _dump_trope(state):
    l = state.trope_library
    return l.__dict__


def _dump_tone(state):
    return state.tone_manual.__dict__


def _char_to_dict(c):
    d = dict(c.__dict__)
    if "role" in d and hasattr(d["role"], "value"):
        d["role"] = d["role"].value
    d["relationships"] = [r.__dict__ for r in c.relationships]
    return d


def _ability_to_dict(a):
    d = dict(a.__dict__)
    d["awakening_stages"] = [s.__dict__ for s in a.awakening_stages]
    return d


def _realm_to_dict(r):
    return r.__dict__


def _dump_relationship_web(state):
    web = state.relationship_web
    return {
        "bonds": [b.__dict__ for b in web.bonds],
        "power_chains": web.power_chains,
        "hidden_alliances": web.hidden_alliances,
        "faction_affiliations": web.faction_affiliations,
    }


def _dump_geo(state):
    g = state.geography
    return {
        "regions": [r.__dict__ for r in g.regions],
        "transport_modes": [m.__dict__ for m in g.transport_modes],
        "distances": [d.__dict__ for d in g.distances],
        "world_map_desc": g.world_map_desc,
    }


def _dump_economy(state):
    e = state.economy
    return {
        "currencies": [c.__dict__ for c in e.currencies],
        "price_anchors": [p.__dict__ for p in e.price_anchors],
        "protagonist_wealth_curve": [w.__dict__ for w in e.protagonist_wealth_curve],
        "trade_notes": e.trade_notes,
    }


# ═══════════════════════════════════════════════════════
#  路由映射：动作名 → 函数
# ═══════════════════════════════════════════════════════

def regen_master_outline() -> dict:
    from agents.master_dispatcher import dispatch_master_outline
    state = _load_or_error()
    state.master_outline.generated = False
    dispatch_master_outline(state)
    _snapshot_and_save(state, "regen_master_outline")
    mo = state.master_outline
    return {"generated": mo.generated, "slots": len(mo.character_slots),
            "factions": len(mo.faction_skeleton), "setpieces": len(mo.plot_setpieces)}


def regen_volumes() -> dict:
    from agents.volume_planner import plan_all_volumes_dispatched
    from persistence.entity_cleanup import after_regen_volumes
    state = _load_or_error()
    _assert_upstream(state, "卷结构",
        master_outline=lambda s: bool(s.master_outline and s.master_outline.generated),
        power_system=lambda s: bool(s.power_system),
    )
    state.volumes = []
    plan_all_volumes_dispatched(state)
    stats = after_regen_volumes(state)
    _snapshot_and_save(state, "regen_volumes")
    return {"count": len(state.volumes), "orphans_cleaned": stats}


def regen_factions() -> dict:
    from agents.faction_architect import design_factions
    from persistence.entity_cleanup import after_regen_factions
    state = _load_or_error()
    _assert_upstream(state, "势力格局",
        power_system=lambda s: bool(s.power_system),
        volumes=lambda s: bool(s.volumes),
    )
    state.factions = []
    design_factions(state)
    stats = after_regen_factions(state)
    _snapshot_and_save(state, "regen_factions")
    return {"count": len(state.factions), "orphans_cleaned": stats}


def regen_world() -> dict:
    from agents.world_builder import build_world
    state = _load_or_error()
    state.world_setting = ""
    build_world(state)
    _snapshot_and_save(state, "regen_world")
    return {"length": len(state.world_setting)}


def regen_timeline() -> dict:
    from agents.timeline_anchor import design_timeline
    state = _load_or_error()
    state.timeline.events = []
    design_timeline(state)
    _snapshot_and_save(state, "regen_timeline")
    return {"events": len(state.timeline.events)}


def regen_characters() -> dict:
    from agents.character_designer import design_all_characters
    from persistence.entity_cleanup import after_regen_characters
    state = _load_or_error()
    _assert_upstream(state, "人物档案",
        master_outline=lambda s: bool(s.master_outline and s.master_outline.generated),
        volumes=lambda s: bool(s.volumes),
        factions=lambda s: bool(s.factions),
    )
    state.characters = []
    design_all_characters(state)
    stats = after_regen_characters(state)
    _snapshot_and_save(state, "regen_characters")
    return {"count": len(state.characters), "orphans_cleaned": stats}


def regen_satisfaction() -> dict:
    from agents.satisfaction_system import plan_all_satisfaction_points
    from persistence.entity_cleanup import after_regen_satisfaction
    state = _load_or_error()
    _assert_upstream(state, "爽点系统",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    )
    state.satisfaction_points = []
    plan_all_satisfaction_points(state)
    stats = after_regen_satisfaction(state)
    _snapshot_and_save(state, "regen_satisfaction")
    return {"count": len(state.satisfaction_points), "orphans_cleaned": stats}


def regen_foreshadows() -> dict:
    from agents.foreshadow_manager import plan_all_foreshadowing
    from persistence.entity_cleanup import after_regen_foreshadows
    state = _load_or_error()
    _assert_upstream(state, "伏笔体系",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    )
    state.foreshadow_items = []
    plan_all_foreshadowing(state)
    stats = after_regen_foreshadows(state)
    _snapshot_and_save(state, "regen_foreshadows")
    return {"count": len(state.foreshadow_items), "orphans_cleaned": stats}


def regen_twists() -> dict:
    from agents.twist_designer import design_twists
    from persistence.state import TwistSystem
    state = _load_or_error()
    _assert_upstream(state, "反转系统",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
        master_outline=lambda s: bool(s.master_outline and s.master_outline.generated),
    )
    state.twist_system = TwistSystem()
    design_twists(state)
    _snapshot_and_save(state, "regen_twists")
    return {"chains": len(state.twist_system.chains)}


def regen_lines() -> dict:
    from agents.line_planner import plan_global_lines, plan_all_volume_lines_parallel
    from persistence.entity_cleanup import after_regen_lines
    state = _load_or_error()
    _assert_upstream(state, "叙事线",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    )
    state.global_lines = []
    state.volume_lines = []
    plan_global_lines(state)
    plan_all_volume_lines_parallel(state)
    stats = after_regen_lines(state)
    _snapshot_and_save(state, "regen_lines")
    return {"global": len(state.global_lines), "volume": len(state.volume_lines),
            "orphans_cleaned": stats}


def regen_all_stages() -> dict:
    from agents.stage_architect import design_volume_stages
    from persistence.entity_cleanup import after_regen_stages
    from config import NUM_VOLUMES
    state = _load_or_error()
    _assert_upstream(state, "叙事舞台",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    )
    state.story_stages = []
    for vol in state.volumes:
        vol.chapter_outlines = []
    for vi in range(1, NUM_VOLUMES + 1):
        design_volume_stages(state, vi)
    stats = after_regen_stages(state)
    _snapshot_and_save(state, "regen_all_stages")
    return {"count": len(state.story_stages), "orphans_cleaned": stats}


REGEN_ACTIONS: dict[str, Callable] = {
    "trope_library": regen_trope_library,
    "tone_manual": regen_tone_manual,
    "after_concept_pitch": regen_after_concept_pitch,
    "conflict_ladder": regen_conflict_ladder,
    "emotion_curve": regen_emotion_curve,
    "special_abilities": regen_special_abilities,
    "relationships": regen_relationships,
    "geography": regen_geography,
    "economy": regen_economy,
    "power_scaling": regen_power_scaling,        # 只补战力刻度（不清空体系）
    "power_system": regen_power_system,          # 真·重建整个力量体系
    # ── 补齐：审计复盘用的各 section 重建 ─────────────
    "master_outline": regen_master_outline,
    "volumes": regen_volumes,
    "factions": regen_factions,
    "world": regen_world,
    "timeline": regen_timeline,
    "characters": regen_characters,
    "satisfaction": regen_satisfaction,
    "foreshadows": regen_foreshadows,
    "twists": regen_twists,
    "lines": regen_lines,
    "stages": regen_all_stages,
}

# 带参数的动作（单独路由）
REGEN_ACTIONS_WITH_ARG: dict[str, Callable] = {
    "volume_outline": regen_volume_outline,     # (volume_index: int)
    "chapter_outline": regen_chapter_outline,   # (chapter_index: int) —— 章级，不动相邻章
    "stages": regen_stages,                      # (volume_index: int)
    "chapter_types": regen_chapter_types,        # (volume_index: int)
    "character_refine": regen_character_refine,  # (name: str)
}
