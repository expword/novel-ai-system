"""
断点续写系统 — 每个阶段完成后保存 NovelState，程序重启时自动恢复进度。

进度文件：output/checkpoint/progress.json  （记录已完成阶段/章节）
状态文件：output/checkpoint/state.json     （完整 NovelState 序列化）
"""
from __future__ import annotations
import os
import json
import dataclasses
from enum import Enum
from typing import Any

from state import (
    NovelState, PowerSystem, Realm, SpecialAbility, AbilityAwakeningStage, LifecycleNode, PowerMechanic,
    Faction, FactionRelation, FactionInfiltration,
    SatisfactionPoint, SatisfactionSetup, SatisfactionType,
    ForeshadowItem, ForeshadowImportance,
    RhythmSegment, VolumeRhythmPlan, RhythmType,
    Character, CharacterRole, Relationship,
    Volume, NarrativeLine, LinePhase, LineType, LineScope,
    MemoryEntry, MemoryBank, TensionLevel,
    ChapterSummary, StoryThread, OpenLoop,
    Fortune, SubScene, StoryStage,
    CharacterBond, RelationshipWeb,
    ProtagonistMilestone, ProtagonistStageBeat, ProtagonistJourney,
    BookStructurePlan,
    ConceptPitch, TropeLibrary, ToneManual, CreativeIntent, IntentRevision,
    MasterOutline, CharacterSlot, FactionSkeletonItem, PlotSetpiece,
    Geography, GeoRegion, TransportMode, TravelDistance,
    Timeline, TimelineEvent,
    Economy, Currency, PriceAnchor, WealthTierPoint,
    CharacterArc, ArcTransition,
    RedHerring, ConflictLadder, ConflictEntry, EmotionCurve, EmotionNote,
    TwistLayer, TwistChain, TwistSystem,
    ChapterTypeAssignment, VolumeChapterTypeDistribution,
    ChapterPacingStats, CharacterStateSnapshot, WorldEvent,
    GlossaryEntry, VersionSnapshot, PendingApproval,
)

import project_context as _pctx
CHECKPOINT_DIR = _pctx.checkpoint_dir()
STATE_FILE = _pctx.state_file()
PROGRESS_FILE = _pctx.progress_file()


# ═══════════════════════════════════════════════════════
#  序列化（State → JSON）
# ═══════════════════════════════════════════════════════

def _to_json(obj: Any) -> Any:
    """递归将 dataclass/Enum/tuple 转为 JSON 可序列化对象。"""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, tuple):
        return list(_to_json(i) for i in obj)
    if isinstance(obj, list):
        return [_to_json(i) for i in obj]
    if isinstance(obj, dict):
        return {str(k): _to_json(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {f.name: _to_json(getattr(obj, f.name))
             for f in dataclasses.fields(obj)}
        # 保存动态属性（如 _emergent_pending）
        for attr in ('_emergent_pending',):
            if hasattr(obj, attr):
                d[attr] = _to_json(getattr(obj, attr))
        return d
    return obj


# ═══════════════════════════════════════════════════════
#  反序列化（JSON → State）
# ═══════════════════════════════════════════════════════

def _enum(cls, val, default=None):
    try:
        return cls(val)
    except (ValueError, KeyError):
        return default or list(cls)[0]


def _load_realm(d: dict) -> Realm:
    return Realm(
        index=d["index"], name=d["name"],
        sub_realms=d.get("sub_realms", []),
        power_description=d.get("power_description", ""),
        breakthrough_condition=d.get("breakthrough_condition", ""),
        resource_requirement=d.get("resource_requirement", ""),
        average_time=d.get("average_time", ""),
        rarity=d.get("rarity", ""),
        combat_capability=d.get("combat_capability", ""),
        lifespan=d.get("lifespan", ""),
        consciousness_range=d.get("consciousness_range", ""),
        mana_capacity=d.get("mana_capacity", ""),
        overleap_rule=d.get("overleap_rule", ""),
        specific_examples=d.get("specific_examples", []),
    )


def _load_awakening_stage(d: dict) -> AbilityAwakeningStage:
    return AbilityAwakeningStage(
        stage_index=int(d.get("stage_index", 1)),
        stage_name=d.get("stage_name", ""),
        target_volume=int(d.get("target_volume", 1)),
        triggering_event=d.get("triggering_event", ""),
        new_power=d.get("new_power", ""),
        cost_or_risk=d.get("cost_or_risk", ""),
    )


def _load_lifecycle_node(d: dict) -> LifecycleNode:
    return LifecycleNode(
        node_type=d.get("node_type", ""),
        target_volume=int(d.get("target_volume", 1) or 1),
        target_chapter=int(d.get("target_chapter", 0) or 0),
        prerequisites=d.get("prerequisites", ""),
        narrative_purpose=d.get("narrative_purpose", ""),
        is_dramatic=bool(d.get("is_dramatic", False)),
        linked_sp_id=d.get("linked_sp_id", ""),
        triggered=bool(d.get("triggered", False)),
        actual_chapter=int(d.get("actual_chapter", -1) or -1),
    )


def _load_special_ability(d: dict) -> SpecialAbility:
    return SpecialAbility(
        name=d["name"], source=d.get("source", ""),
        description=d.get("description", ""),
        unlock_condition=d.get("unlock_condition", ""),
        holder_role=d.get("holder_role", ""),
        holder_name=d.get("holder_name", ""),
        is_protagonist_signature=d.get("is_protagonist_signature", False),
        entry_kind=d.get("entry_kind", "ability"),
        awakening_stages=[_load_awakening_stage(s) for s in d.get("awakening_stages", [])],
        lifecycle_nodes=[_load_lifecycle_node(n) for n in d.get("lifecycle_nodes", [])],
        plot_integration=d.get("plot_integration", ""),
        narrative_hook=d.get("narrative_hook", ""),
        external_llm_profile=d.get("external_llm_profile", ""),
    )


def _load_power_mechanic(d: dict) -> PowerMechanic:
    return PowerMechanic(
        name=d.get("name", ""),
        description=d.get("description", ""),
        protagonist_usage=d.get("protagonist_usage", ""),
        narrative_impact=d.get("narrative_impact", ""),
    )


def _load_power_system(d: dict) -> PowerSystem:
    if not d:
        return None
    return PowerSystem(
        system_name=d.get("system_name", ""),
        system_description=d.get("system_description", ""),
        realms=[_load_realm(r) for r in d.get("realms", [])],
        special_abilities=[_load_special_ability(a) for a in d.get("special_abilities", [])],
        cultivation_resources=d.get("cultivation_resources", []),
        protagonist_realm_plan={int(k): v for k, v in d.get("protagonist_realm_plan", {}).items()},
        system_type=d.get("system_type", "realms"),
        system_nature=d.get("system_nature", ""),
        power_flow=d.get("power_flow", ""),
        rank_unit=d.get("rank_unit", ""),
        special_mechanics=[_load_power_mechanic(m) for m in d.get("special_mechanics", [])],
        has_hierarchy=d.get("has_hierarchy", True),
    )


def _load_faction(d: dict) -> Faction:
    infiltrations = [
        FactionInfiltration(
            target_faction=i["target_faction"],
            method=i.get("method", "安插眼线"),
            depth=i.get("depth", "表层"),
            reveal_volume=i.get("reveal_volume", 1),
        )
        for i in d.get("infiltrations", [])
    ]
    return Faction(
        name=d["name"], faction_type=d["faction_type"],
        power_level=d["power_level"], territory=d.get("territory", ""),
        tier=d.get("tier", 1), tier_label=d.get("tier_label", ""),
        is_neutral=d.get("is_neutral", False),
        is_hidden=d.get("is_hidden", False),
        reveal_volume=d.get("reveal_volume", 1),
        protagonist_start=d.get("protagonist_start", False),
        surface_goal=d.get("surface_goal", ""),
        hidden_goal=d.get("hidden_goal", ""),
        core_strength=d.get("core_strength", ""),
        weakness=d.get("weakness", ""),
        key_members=d.get("key_members", []),
        internal_conflicts=d.get("internal_conflicts", []),
        infiltrations=infiltrations,
        power_vacuum_desc=d.get("power_vacuum_desc", ""),
        status=d.get("status", "active"),
        status_changed_volume=d.get("status_changed_volume", -1),
        relations=[FactionRelation(
            target=r["target"], relation_type=r["relation_type"],
            description=r.get("description", "")
        ) for r in d.get("relations", [])],
        volume_role={int(k): v for k, v in d.get("volume_role", {}).items()},
    )


def _load_sp(d: dict) -> SatisfactionPoint:
    return SatisfactionPoint(
        sp_id=d["sp_id"],
        sp_type=_enum(SatisfactionType, d["sp_type"]),
        title=d["title"], description=d["description"],
        intensity=int(d["intensity"]), volume=int(d["volume"]),
        target_chapter=int(d["target_chapter"]),
        setup_chain=[SatisfactionSetup(chapter=int(s["chapter"]), content=s["content"])
                     for s in d.get("setup_chain", [])],
        payoff_description=d["payoff_description"],
        triggered=d.get("triggered", False),
        actual_chapter=int(d.get("actual_chapter", -1)),
    )


def _load_fw(d: dict) -> ForeshadowItem:
    return ForeshadowItem(
        fw_id=d["fw_id"], content=d["content"],
        hidden_meaning=d["hidden_meaning"],
        importance=_enum(ForeshadowImportance, d["importance"]),
        planted_chapter=int(d["planted_chapter"]),
        planned_resolve_volume=int(d["planned_resolve_volume"]),
        planned_resolve_chapter=int(d["planned_resolve_chapter"]),
        resolution_description=d.get("resolution_description", ""),
        related_sp_id=d.get("related_sp_id", ""),
        resolved=d.get("resolved", False),
        actual_resolve_chapter=int(d.get("actual_resolve_chapter", -1)),
        activation_chapter=int(d.get("activation_chapter", -1)),
        activation_sign=d.get("activation_sign", ""),
        resolution_quality=d.get("resolution_quality", ""),
    )


def _load_red_herring(d: dict) -> RedHerring:
    return RedHerring(
        rh_id=d.get("rh_id", ""),
        content=d.get("content", ""),
        misdirection_purpose=d.get("misdirection_purpose", ""),
        planted_chapter=int(d.get("planted_chapter", 0)),
        debunk_chapter=int(d.get("debunk_chapter", -1)),
        actual_truth=d.get("actual_truth", ""),
        planted=d.get("planted", False),
        debunked=d.get("debunked", False),
    )


def _load_twist_layer(d: dict) -> TwistLayer:
    return TwistLayer(
        layer=int(d.get("layer", 1)),
        surface_belief=d.get("surface_belief", ""),
        reveal=d.get("reveal", ""),
        clues_planted=d.get("clues_planted", []) or [],
        reveal_anchor=d.get("reveal_anchor", ""),
        emotional_impact=d.get("emotional_impact", ""),
        twist_mechanism=d.get("twist_mechanism", ""),
    )


def _load_twist_chain(d: dict) -> TwistChain:
    return TwistChain(
        chain_id=d.get("chain_id", ""),
        title=d.get("title", ""),
        category=d.get("category", ""),
        initial_setup=d.get("initial_setup", ""),
        target_layers=int(d.get("target_layers", 2)),
        layers=[_load_twist_layer(x) for x in (d.get("layers") or []) if isinstance(x, dict)],
        involved_characters=d.get("involved_characters", []) or [],
        involved_factions=d.get("involved_factions", []) or [],
        difficulty=d.get("difficulty", "moderate"),
        design_rationale=d.get("design_rationale", ""),
        linked_foreshadow_ids=d.get("linked_foreshadow_ids", []) or [],
        scope=d.get("scope", "cross_volume"),
        volume_span=[int(x) for x in (d.get("volume_span") or []) if isinstance(x, (int, str))],
        anchor_volume=int(d.get("anchor_volume", 0)),
    )


def _load_twist_system(d: dict) -> TwistSystem:
    if not d:
        return TwistSystem()
    return TwistSystem(
        chains=[_load_twist_chain(c) for c in (d.get("chains") or []) if isinstance(c, dict)],
        design_principle=d.get("design_principle", ""),
        reader_experience_curve=d.get("reader_experience_curve", ""),
    )


def _load_conflict_entry(d: dict) -> ConflictEntry:
    return ConflictEntry(
        volume=int(d.get("volume", 1)),
        conflict_type=d.get("conflict_type", ""),
        core_conflict=d.get("core_conflict", ""),
        opponent_tier=int(d.get("opponent_tier", 1)),
        resolution_method=d.get("resolution_method", ""),
        escalation_note=d.get("escalation_note", ""),
        why_this_type=d.get("why_this_type", ""),
    )


def _load_conflict_ladder(d: dict) -> ConflictLadder:
    return ConflictLadder(
        entries=[_load_conflict_entry(e) for e in d.get("entries", [])]
    )


def _load_emotion_note(d: dict) -> EmotionNote:
    return EmotionNote(
        volume=int(d.get("volume", 1)),
        base_tone=d.get("base_tone", ""),
        low_point_chapter=int(d.get("low_point_chapter", 0)),
        low_point_desc=d.get("low_point_desc", ""),
        high_point_chapter=int(d.get("high_point_chapter", 0)),
        high_point_desc=d.get("high_point_desc", ""),
        contrast_with_prev=d.get("contrast_with_prev", ""),
    )


def _load_emotion_curve(d: dict) -> EmotionCurve:
    return EmotionCurve(
        notes=[_load_emotion_note(n) for n in d.get("notes", [])]
    )


def _load_chapter_type_assignment(d: dict) -> ChapterTypeAssignment:
    return ChapterTypeAssignment(
        chapter_index=int(d.get("chapter_index", 0)),
        chapter_type=d.get("chapter_type", ""),
        reason=d.get("reason", ""),
    )


def _load_volume_ctp(d: dict) -> VolumeChapterTypeDistribution:
    return VolumeChapterTypeDistribution(
        volume=int(d.get("volume", 1)),
        type_distribution={k: int(v) for k, v in d.get("type_distribution", {}).items()},
        per_chapter=[_load_chapter_type_assignment(a) for a in d.get("per_chapter", [])],
    )


def _load_pacing_stats(d: dict) -> ChapterPacingStats:
    return ChapterPacingStats(
        chapter_index=int(d.get("chapter_index", 0)),
        dialogue_ratio=float(d.get("dialogue_ratio", 0.0)),
        action_ratio=float(d.get("action_ratio", 0.0)),
        description_ratio=float(d.get("description_ratio", 0.0)),
        inner_monologue_ratio=float(d.get("inner_monologue_ratio", 0.0)),
        turns_per_1000_words=int(d.get("turns_per_1000_words", 0)),
        deviation_note=d.get("deviation_note", ""),
    )


def _load_state_snapshot(d: dict) -> CharacterStateSnapshot:
    return CharacterStateSnapshot(
        chapter_index=int(d.get("chapter_index", 0)),
        location=d.get("location", ""),
        injury=d.get("injury", ""),
        emotion=d.get("emotion", ""),
        items_on_hand=d.get("items_on_hand", []),
        realm=d.get("realm", ""),
        relationship_changes=d.get("relationship_changes", []),
    )


def _load_world_event(d: dict) -> WorldEvent:
    return WorldEvent(
        chapter_index=int(d.get("chapter_index", 0)),
        event_desc=d.get("event_desc", ""),
        affected_factions=d.get("affected_factions", []),
        affected_regions=d.get("affected_regions", []),
        importance=d.get("importance", "普通"),
    )


def _load_glossary_entry(d: dict) -> GlossaryEntry:
    return GlossaryEntry(
        term=d.get("term", ""),
        category=d.get("category", ""),
        definition=d.get("definition", ""),
        first_appeared_chapter=int(d.get("first_appeared_chapter", 0)),
        aliases=d.get("aliases", []),
    )


def _load_version_snapshot(d: dict) -> VersionSnapshot:
    return VersionSnapshot(
        timestamp=d.get("timestamp", ""),
        label=d.get("label", ""),
        phase=d.get("phase", ""),
        chapter_index=int(d.get("chapter_index", -1)),
        notes=d.get("notes", ""),
    )


def _load_pending_approval(d: dict) -> PendingApproval:
    return PendingApproval(
        approval_id=d.get("approval_id", ""),
        reason=d.get("reason", ""),
        trigger_chapter=int(d.get("trigger_chapter", -1)),
        trigger_phase=d.get("trigger_phase", ""),
        created_at=d.get("created_at", ""),
        approved=d.get("approved", False),
        approver_note=d.get("approver_note", ""),
    )


def _load_rhythm_segment(d: dict) -> RhythmSegment:
    return RhythmSegment(
        chapter_start=d["chapter_start"], chapter_end=d["chapter_end"],
        rhythm_type=_enum(RhythmType, d["rhythm_type"]),
        description=d.get("description", ""), word_pace=d.get("word_pace", "中等"),
    )


def _load_rhythm_plan(d: dict) -> VolumeRhythmPlan:
    return VolumeRhythmPlan(
        volume_index=d["volume_index"],
        overall_pattern=d.get("overall_pattern", ""),
        segments=[_load_rhythm_segment(s) for s in d.get("segments", [])],
        breathing_chapters=d.get("breathing_chapters", []),
        climax_chapters=d.get("climax_chapters", []),
    )


def _load_character(d: dict) -> Character:
    return Character(
        name=d["name"],
        role=_enum(CharacterRole, d["role"]),
        gender=d.get("gender", ""), age_desc=d.get("age_desc", ""),
        appearance=d.get("appearance", ""),
        personality=d.get("personality", ""),
        personality_detail=d.get("personality_detail", ""),
        background=d.get("background", ""),
        trauma=d.get("trauma", ""), desire=d.get("desire", ""), fear=d.get("fear", ""),
        speech_pattern=d.get("speech_pattern", ""),
        ability=d.get("ability", ""), realm=d.get("realm", ""),
        arc=d.get("arc", ""), motivation=d.get("motivation", ""),
        fatal_flaw=d.get("fatal_flaw", ""),
        first_volume=d.get("first_volume", 1), last_volume=d.get("last_volume", -1),
        relationships=[Relationship(
            target_name=r["target_name"], relation=r["relation"],
            evolution=r.get("evolution", "")
        ) for r in d.get("relationships", [])],
        volume_arcs={int(k): v for k, v in d.get("volume_arcs", {}).items()},
        volume_realm={int(k): v for k, v in d.get("volume_realm", {}).items()},
        signature_mannerisms=d.get("signature_mannerisms", []),
        verbal_tics=d.get("verbal_tics", []),
        sensory_signature=d.get("sensory_signature", ""),
        default_stress_response=d.get("default_stress_response", ""),
        defining_memory=d.get("defining_memory", ""),
        secret_desire=d.get("secret_desire", ""),
        contrast_with_protagonist=d.get("contrast_with_protagonist", ""),
        high_freq_vocab=d.get("high_freq_vocab", []),
        speech_taboo=d.get("speech_taboo", []),
        speech_under_anger=d.get("speech_under_anger", ""),
        speech_under_fear=d.get("speech_under_fear", ""),
        speech_under_joy=d.get("speech_under_joy", ""),
        sentence_length_preference=d.get("sentence_length_preference", ""),
        narrative_function=d.get("narrative_function", ""),
        support_role=d.get("support_role", ""),
        function_detail=d.get("function_detail", ""),
        source_slot_id=d.get("source_slot_id", ""),
    )


def _load_line_phase(d: dict) -> LinePhase:
    return LinePhase(
        phase_index=d["phase_index"], name=d["name"],
        description=d.get("description", ""),
        volume=d["volume"], chapter_start=d["chapter_start"], chapter_end=d["chapter_end"],
        tension=_enum(TensionLevel, d["tension"]),
        completed=d.get("completed", False),
    )


def _load_narrative_line(d: dict) -> NarrativeLine:
    vr = d.get("volume_range", [1, 1])
    return NarrativeLine(
        line_id=d["line_id"],
        line_type=_enum(LineType, d["line_type"]),
        scope=_enum(LineScope, d["scope"]),
        name=d["name"], description=d.get("description", ""),
        characters=d.get("characters", []),
        volume_range=tuple(vr),
        phases=[_load_line_phase(p) for p in d.get("phases", [])],
        current_phase=d.get("current_phase", 1),
        resolved=d.get("resolved", False),
    )


def _load_volume(d: dict) -> Volume:
    return Volume(
        index=int(d["index"]), title=d["title"], theme=d["theme"],
        arc=d.get("arc", ""),
        chapter_start=int(d["chapter_start"]), chapter_end=int(d["chapter_end"]),
        opening_hook=d.get("opening_hook", ""), closing_hook=d.get("closing_hook", ""),
        volume_antagonist=d.get("volume_antagonist", ""),
        key_events=d.get("key_events", []),
        chapter_outlines=d.get("chapter_outlines", []),
        structure_role=d.get("structure_role", ""),
        purpose=d.get("purpose", ""),
        expression=d.get("expression", ""),
    )


def _load_memory_entry(d: dict) -> MemoryEntry:
    return MemoryEntry(
        chapter_index=int(d["chapter_index"]), volume_index=int(d["volume_index"]),
        line_ids=d.get("line_ids", []), event_type=d["event_type"],
        content=d["content"],
        tension=_enum(TensionLevel, d["tension"]),
        tags=d.get("tags", []),
    )


def _load_chapter_summary(d: dict) -> ChapterSummary:
    ps = d.get("pacing_stats")
    return ChapterSummary(
        index=int(d["index"]), volume_index=int(d["volume_index"]),
        title=d["title"], summary=d["summary"],
        word_count=int(d.get("word_count", 0)),
        tension=_enum(TensionLevel, d["tension"]),
        key_events=d.get("key_events", []),
        lines_advanced=d.get("lines_advanced", []),
        sp_triggered=d.get("sp_triggered", []),
        closing_hook=d.get("closing_hook", ""),
        pacing_stats=_load_pacing_stats(ps) if isinstance(ps, dict) else None,
    )


def _load_story_thread(d: dict) -> StoryThread:
    loops = [
        OpenLoop(
            loop_id=l["loop_id"],
            description=l.get("description", ""),
            urgency=l.get("urgency", "持续"),
            opened_chapter=l.get("opened_chapter", 0),
            target_close_chapter=l.get("target_close_chapter", -1),
            current_progress=l.get("current_progress", ""),
            closed=l.get("closed", False),
        )
        for l in d.get("open_loops", [])
    ]
    t = StoryThread(
        current_location=d.get("current_location", ""),
        current_time_context=d.get("current_time_context", ""),
        protagonist_immediate_goal=d.get("protagonist_immediate_goal", ""),
        protagonist_immediate_obstacle=d.get("protagonist_immediate_obstacle", ""),
        protagonist_emotional_state=d.get("protagonist_emotional_state", ""),
        open_loops=loops,
        scene_end_state=d.get("scene_end_state", ""),
        next_chapter_opening=d.get("next_chapter_opening", ""),
        parallel_events=d.get("parallel_events", []),
        background_developments=d.get("background_developments", []),
        active_tensions=d.get("active_tensions", []),
    )
    # 恢复待融入新角色列表
    pending = d.get("_emergent_pending", [])
    if pending:
        t._emergent_pending = pending
    return t


def _load_character_bond(d: dict) -> CharacterBond:
    return CharacterBond(
        bond_id=d.get("bond_id", ""),
        char_a=d.get("char_a", ""),
        char_b=d.get("char_b", ""),
        surface_relation=d.get("surface_relation", ""),
        true_relation=d.get("true_relation", ""),
        hidden_secret=d.get("hidden_secret", ""),
        tension_source=d.get("tension_source", ""),
        volume_evolution={int(k): v for k, v in d.get("volume_evolution", {}).items()},
        reveal_volume=d.get("reveal_volume", -1),
        affects_protagonist=d.get("affects_protagonist", True),
        future_trajectory=d.get("future_trajectory", ""),
        projected_changes={int(k): v for k, v in d.get("projected_changes", {}).items()},
    )


def _load_relationship_web(d: dict) -> RelationshipWeb:
    web = RelationshipWeb(
        bonds=[_load_character_bond(b) for b in d.get("bonds", [])],
        power_chains=d.get("power_chains", []),
        hidden_alliances=d.get("hidden_alliances", []),
        faction_affiliations=d.get("faction_affiliations", {}),
    )
    return web


def _load_protagonist_milestone(d: dict) -> ProtagonistMilestone:
    return ProtagonistMilestone(
        volume=d.get("volume", 1),
        entry_state=d.get("entry_state", ""),
        exit_state=d.get("exit_state", ""),
        inner_growth=d.get("inner_growth", ""),
        outer_change=d.get("outer_change", ""),
        key_relationships=d.get("key_relationships", []),
        inner_conflict=d.get("inner_conflict", ""),
        hardest_choice=d.get("hardest_choice", ""),
        darkest_moment=d.get("darkest_moment", ""),
        triumph_moment=d.get("triumph_moment", ""),
    )


def _load_protagonist_stage_beat(d: dict) -> ProtagonistStageBeat:
    return ProtagonistStageBeat(
        beat_id=d.get("beat_id", ""),
        stage_id=d.get("stage_id", ""),
        volume=d.get("volume", 1),
        entry_state=d.get("entry_state", ""),
        exit_state=d.get("exit_state", ""),
        key_actions=d.get("key_actions", []),
        relationship_shifts=d.get("relationship_shifts", []),
        gained=d.get("gained", ""),
        lost=d.get("lost", ""),
        milestone_phase=d.get("milestone_phase", "承"),
    )


def _load_protagonist_journey(d: dict) -> ProtagonistJourney:
    return ProtagonistJourney(
        overall_theme=d.get("overall_theme", ""),
        core_wound=d.get("core_wound", ""),
        true_goal=d.get("true_goal", ""),
        fatal_flaw=d.get("fatal_flaw", ""),
        central_conflict=d.get("central_conflict", ""),
        growth_arc=d.get("growth_arc", ""),
        milestones=[_load_protagonist_milestone(m) for m in d.get("milestones", [])],
        stage_beats=[_load_protagonist_stage_beat(b) for b in d.get("stage_beats", [])],
    )


def _load_fortune(d: dict) -> Fortune:
    return Fortune(
        fortune_id=d["fortune_id"],
        fortune_type=d.get("fortune_type", "宝物"),
        name=d.get("name", ""),
        description=d.get("description", ""),
        location_desc=d.get("location_desc", ""),
        stage_id=d.get("stage_id", ""),
        acquisition_method=d.get("acquisition_method", ""),
        prerequisite=d.get("prerequisite", ""),
        volume=int(d.get("volume", 1)),
        target_chapter=int(d.get("target_chapter", -1)),
        effect_on_growth=d.get("effect_on_growth", ""),
        narrative_hook=d.get("narrative_hook", ""),
        obtained=d.get("obtained", False),
        actual_chapter=int(d.get("actual_chapter", -1)),
    )


def _load_sub_scene(d: dict) -> SubScene:
    return SubScene(
        sub_id=d["sub_id"],
        name=d.get("name", ""),
        sub_type=d.get("sub_type", "推进"),
        description=d.get("description", ""),
        chapter_start=int(d.get("chapter_start", 1)),
        chapter_end=int(d.get("chapter_end", 1)),
        key_events=d.get("key_events", []),
        fortune_ids=d.get("fortune_ids", []),
        structure_role=d.get("structure_role", ""),
        purpose=d.get("purpose", ""),
        expression=d.get("expression", ""),
    )


def _load_story_stage(d: dict) -> StoryStage:
    return StoryStage(
        stage_id=d["stage_id"],
        name=d.get("name", ""),
        stage_type=d.get("stage_type", "旅途/外出历练"),
        volume=int(d.get("volume", 1)),
        chapter_start=int(d.get("chapter_start", 1)),
        chapter_end=int(d.get("chapter_end", 1)),
        setting_desc=d.get("setting_desc", ""),
        atmosphere=d.get("atmosphere", ""),
        protagonist_role=d.get("protagonist_role", ""),
        key_activities=d.get("key_activities", []),
        sub_scenes=[_load_sub_scene(s) for s in d.get("sub_scenes", [])],
        fortune_ids=d.get("fortune_ids", []),
        transition_in=d.get("transition_in", ""),
        transition_out=d.get("transition_out", ""),
        parallel_stage_ids=d.get("parallel_stage_ids", []),
        active=d.get("active", True),
        structure_role=d.get("structure_role", ""),
        purpose=d.get("purpose", ""),
        expression=d.get("expression", ""),
    )


def _load_book_structure(d: dict) -> BookStructurePlan:
    return BookStructurePlan(
        book_proposition=d.get("book_proposition", ""),
        book_expression=d.get("book_expression", ""),
        phase_volumes={k: [int(x) for x in v] for k, v in d.get("phase_volumes", {}).items()},
        phase_purposes=d.get("phase_purposes", {}),
        phase_expressions=d.get("phase_expressions", {}),
    )


def _load_master_outline(d: dict) -> MasterOutline:
    return MasterOutline(
        generated=bool(d.get("generated", False)),
        story_premise=d.get("story_premise", ""),
        central_conflict=d.get("central_conflict", ""),
        thematic_core=d.get("thematic_core", ""),
        world_seed=d.get("world_seed", ""),
        tone_anchors=d.get("tone_anchors", []),
        character_slots=[
            CharacterSlot(
                slot_id=s.get("slot_id", ""),
                role_tag=s.get("role_tag", ""),
                function=s.get("function", ""),
                brief_hint=s.get("brief_hint", ""),
                relationship_hint=s.get("relationship_hint", ""),
                narrative_arc_hint=s.get("narrative_arc_hint", ""),
                first_volume=int(s.get("first_volume", 1)),
                last_volume=int(s.get("last_volume", -1)),
                narrative_function=s.get("narrative_function", ""),
                support_role=s.get("support_role", ""),
                function_detail=s.get("function_detail", ""),
            )
            for s in d.get("character_slots", []) if isinstance(s, dict)
        ],
        faction_skeleton=[
            FactionSkeletonItem(
                tier=int(s.get("tier", 1)),
                tier_label=s.get("tier_label", ""),
                tier_function=s.get("tier_function", ""),
                faction_count_hint=int(s.get("faction_count_hint", 3)),
                style_hint=s.get("style_hint", ""),
            )
            for s in d.get("faction_skeleton", []) if isinstance(s, dict)
        ],
        plot_setpieces=[
            PlotSetpiece(
                anchor=p.get("anchor", ""),
                kind=p.get("kind", ""),
                gist=p.get("gist", ""),
                involved_slot_ids=p.get("involved_slot_ids", []),
            )
            for p in d.get("plot_setpieces", []) if isinstance(p, dict)
        ],
    )


def _load_creative_intent(d: dict) -> CreativeIntent:
    return CreativeIntent(
        raw_description=d.get("raw_description", ""),
        analyzed=d.get("analyzed", False),
        revisions=[IntentRevision(**r) for r in d.get("revisions", []) if isinstance(r, dict)],
        suggested_title=d.get("suggested_title", ""),
        suggested_genre=d.get("suggested_genre", ""),
        suggested_subgenre=d.get("suggested_subgenre", ""),
        suggested_theme=d.get("suggested_theme", ""),
        audience_hint=d.get("audience_hint", ""),
        age_group_hint=d.get("age_group_hint", ""),
        platform_hint=d.get("platform_hint", ""),
        selling_points_hints=d.get("selling_points_hints", []),
        benchmark_hints=d.get("benchmark_hints", []),
        differentiation_hint=d.get("differentiation_hint", ""),
        embrace_tropes_hints=d.get("embrace_tropes_hints", []),
        avoid_tropes_hints=d.get("avoid_tropes_hints", []),
        preferred_sp_types_hints=d.get("preferred_sp_types_hints", []),
        villain_policy_hint=d.get("villain_policy_hint", ""),
        romance_policy_hint=d.get("romance_policy_hint", ""),
        harem_policy_hint=d.get("harem_policy_hint", ""),
        protagonist_archetype_hint=d.get("protagonist_archetype_hint", ""),
        world_tone_hint=d.get("world_tone_hint", ""),
        narrative_voice_hint=d.get("narrative_voice_hint", ""),
        style_reference_hint=d.get("style_reference_hint", ""),
        dialogue_style_hint=d.get("dialogue_style_hint", ""),
        tone_summary=d.get("tone_summary", ""),
        analyzer_notes=d.get("analyzer_notes", ""),
    )


def _load_concept_pitch(d: dict) -> ConceptPitch:
    return ConceptPitch(
        one_line_pitch=d.get("one_line_pitch", ""),
        core_selling_points=d.get("core_selling_points", []),
        target_audience=d.get("target_audience", ""),
        target_age_group=d.get("target_age_group", ""),
        target_platform=d.get("target_platform", ""),
        reader_profile=d.get("reader_profile", ""),
        benchmark_works=d.get("benchmark_works", []),
        differentiation=d.get("differentiation", ""),
        expected_total_words=int(d.get("expected_total_words", 0)),
        expected_volumes=int(d.get("expected_volumes", 0)),
        expected_completion_weeks=int(d.get("expected_completion_weeks", 0)),
    )


def _load_trope_library(d: dict) -> TropeLibrary:
    return TropeLibrary(
        embrace_tropes=d.get("embrace_tropes", []),
        avoid_tropes=d.get("avoid_tropes", []),
        preferred_sp_types=d.get("preferred_sp_types", []),
        villain_policy=d.get("villain_policy", ""),
        romance_policy=d.get("romance_policy", ""),
        harem_policy=d.get("harem_policy", ""),
        protagonist_archetype=d.get("protagonist_archetype", ""),
        world_tone=d.get("world_tone", ""),
    )


def _load_geo_region(d: dict) -> GeoRegion:
    return GeoRegion(
        region_id=d.get("region_id", ""),
        name=d.get("name", ""),
        level=d.get("level", ""),
        parent_id=d.get("parent_id", ""),
        description=d.get("description", ""),
        climate=d.get("climate", ""),
        products=d.get("products", ""),
        culture_notes=d.get("culture_notes", ""),
        notable_spots=d.get("notable_spots", []),
        importance=d.get("importance", "background"),
        detail_level=int(d.get("detail_level", 1)),
        protagonist_arc_note=d.get("protagonist_arc_note", ""),
        atmosphere=d.get("atmosphere", ""),
        key_scenes=d.get("key_scenes", []),
    )


def _load_transport_mode(d: dict) -> TransportMode:
    return TransportMode(
        name=d.get("name", ""),
        speed_description=d.get("speed_description", ""),
        realm_required=d.get("realm_required", ""),
        cost=d.get("cost", ""),
    )


def _load_travel_distance(d: dict) -> TravelDistance:
    return TravelDistance(
        from_region=d.get("from_region", ""),
        to_region=d.get("to_region", ""),
        distance_desc=d.get("distance_desc", ""),
        travel_time_by_mode=d.get("travel_time_by_mode", {}),
    )


def _load_route_stage(d: dict):
    from state import RouteStage
    return RouteStage(
        volume=int(d.get("volume", 1)),
        primary_region_id=d.get("primary_region_id", ""),
        visited_region_ids=d.get("visited_region_ids", []),
        arc_note=d.get("arc_note", ""),
    )


def _load_geography(d: dict) -> Geography:
    return Geography(
        regions=[_load_geo_region(r) for r in d.get("regions", [])],
        transport_modes=[_load_transport_mode(m) for m in d.get("transport_modes", [])],
        distances=[_load_travel_distance(t) for t in d.get("distances", [])],
        world_map_desc=d.get("world_map_desc", ""),
        world_layout=d.get("world_layout", ""),
        protagonist_route=[_load_route_stage(r) for r in d.get("protagonist_route", [])],
    )


def _load_timeline_event(d: dict) -> TimelineEvent:
    return TimelineEvent(
        event_id=d.get("event_id", ""),
        era=d.get("era", ""),
        years_ago=int(d.get("years_ago", 0)),
        name=d.get("name", ""),
        description=d.get("description", ""),
        consequences=d.get("consequences", ""),
        related_factions=d.get("related_factions", []),
        foreshadow_potential=d.get("foreshadow_potential", ""),
    )


def _load_timeline(d: dict) -> Timeline:
    return Timeline(
        events=[_load_timeline_event(e) for e in d.get("events", [])],
        current_era=d.get("current_era", ""),
        current_year_desc=d.get("current_year_desc", ""),
    )


def _load_currency(d: dict) -> Currency:
    return Currency(
        name=d.get("name", ""),
        rank=int(d.get("rank", 1)),
        exchange_to_base=int(d.get("exchange_to_base", 1)),
        notes=d.get("notes", ""),
    )


def _load_price_anchor(d: dict) -> PriceAnchor:
    return PriceAnchor(
        item=d.get("item", ""),
        price=d.get("price", ""),
        tier=d.get("tier", ""),
    )


def _load_wealth_tier_point(d: dict) -> WealthTierPoint:
    return WealthTierPoint(
        volume=int(d.get("volume", 1)),
        tier=d.get("tier", ""),
        description=d.get("description", ""),
    )


def _load_economy(d: dict) -> Economy:
    return Economy(
        currencies=[_load_currency(c) for c in d.get("currencies", [])],
        price_anchors=[_load_price_anchor(p) for p in d.get("price_anchors", [])],
        protagonist_wealth_curve=[_load_wealth_tier_point(w) for w in d.get("protagonist_wealth_curve", [])],
        trade_notes=d.get("trade_notes", ""),
    )


def _load_arc_transition(d: dict) -> ArcTransition:
    return ArcTransition(
        volume=int(d.get("volume", 1)),
        chapter_approx=int(d.get("chapter_approx", -1)),
        trigger_event=d.get("trigger_event", ""),
        state_before=d.get("state_before", ""),
        state_after=d.get("state_after", ""),
        inner_change=d.get("inner_change", ""),
        ability_trigger=d.get("ability_trigger", ""),
    )


def _load_character_arc(d: dict) -> CharacterArc:
    return CharacterArc(
        character_name=d.get("character_name", ""),
        theme=d.get("theme", ""),
        start_state=d.get("start_state", ""),
        end_state=d.get("end_state", ""),
        transitions=[_load_arc_transition(t) for t in d.get("transitions", [])],
    )


def _load_tone_manual(d: dict) -> ToneManual:
    return ToneManual(
        narrative_voice=d.get("narrative_voice", ""),
        style_reference=d.get("style_reference", ""),
        prose_rhythm=d.get("prose_rhythm", ""),
        dialogue_style=d.get("dialogue_style", ""),
        sensory_weight=d.get("sensory_weight", ""),
        banned_words=d.get("banned_words", []),
        careful_words=d.get("careful_words", []),
        metaphor_preference=d.get("metaphor_preference", ""),
        opening_habit=d.get("opening_habit", ""),
    )



def _load_state(d: dict) -> NovelState:
    state = NovelState(
        title=d["title"], genre=d["genre"], theme=d["theme"],
        world_setting=d.get("world_setting", ""),
        world_factions_desc=d.get("world_factions_desc", ""),
        overall_arc=d.get("overall_arc", ""),
        power_system=_load_power_system(d.get("power_system") or {}),
        factions=[_load_faction(f) for f in d.get("factions", [])],
        satisfaction_points=[_load_sp(s) for s in d.get("satisfaction_points", [])],
        foreshadow_items=[_load_fw(f) for f in d.get("foreshadow_items", [])],
        rhythm_plans=[_load_rhythm_plan(r) for r in d.get("rhythm_plans", [])],
        characters=[_load_character(c) for c in d.get("characters", [])],
        volumes=[_load_volume(v) for v in d.get("volumes", [])],
        current_volume_index=d.get("current_volume_index", 1),
        current_chapter_index=d.get("current_chapter_index", 0),
        global_lines=[_load_narrative_line(l) for l in d.get("global_lines", [])],
        volume_lines=[_load_narrative_line(l) for l in d.get("volume_lines", [])],
        tension_history=[_enum(TensionLevel, t) for t in d.get("tension_history", [])],
        completed_chapters=[_load_chapter_summary(c) for c in d.get("completed_chapters", [])],
    )
    mb = d.get("memory", {})
    state.memory = MemoryBank(
        entries=[_load_memory_entry(e) for e in mb.get("entries", [])],
        facts=mb.get("facts", []),
        character_states=mb.get("character_states", {}),
    )
    state.story_thread = _load_story_thread(d.get("story_thread", {}))
    state.fortunes = [_load_fortune(f) for f in d.get("fortunes", [])]
    state.story_stages = [_load_story_stage(s) for s in d.get("story_stages", [])]
    state.relationship_web = _load_relationship_web(d.get("relationship_web", {}))
    state.protagonist_journey = _load_protagonist_journey(d.get("protagonist_journey", {}))
    state.book_structure = _load_book_structure(d.get("book_structure", {}))
    state.creative_intent = _load_creative_intent(d.get("creative_intent", {}))
    state.concept_pitch = _load_concept_pitch(d.get("concept_pitch", {}))
    state.trope_library = _load_trope_library(d.get("trope_library", {}))
    state.tone_manual = _load_tone_manual(d.get("tone_manual", {}))
    state.master_outline = _load_master_outline(d.get("master_outline", {}))
    state.geography = _load_geography(d.get("geography", {}))
    state.timeline = _load_timeline(d.get("timeline", {}))
    state.economy = _load_economy(d.get("economy", {}))
    state.character_arcs = [_load_character_arc(a) for a in d.get("character_arcs", [])]
    state.conflict_ladder = _load_conflict_ladder(d.get("conflict_ladder", {}))
    state.emotion_curve = _load_emotion_curve(d.get("emotion_curve", {}))
    state.red_herrings = [_load_red_herring(r) for r in d.get("red_herrings", [])]
    state.twist_system = _load_twist_system(d.get("twist_system", {}))
    state.chapter_type_plans = [_load_volume_ctp(p) for p in d.get("chapter_type_plans", [])]
    state.character_state_history = {
        name: [_load_state_snapshot(s) for s in snaps]
        for name, snaps in d.get("character_state_history", {}).items()
    }
    state.world_events = [_load_world_event(e) for e in d.get("world_events", [])]

    # 氛围库（单体 JSON 兼容）
    try:
        from state_storage import _load_atmosphere_library
        atmo = d.get("atmosphere_library")
        if atmo:
            state.atmosphere_library = _load_atmosphere_library(atmo)
    except Exception as e:
        print(f"  [load_state] atmosphere_library 解析失败：{type(e).__name__}: {e}")
    # 章节灵感：JSON key 回来是 str，需要转 int
    raw_ins = d.get("chapter_inspirations", {}) or {}
    state.chapter_inspirations = {
        int(k): str(v) for k, v in raw_ins.items()
        if str(v).strip()
    }
    # 章节对话历史：JSON key 回来是 str，需要转 int
    # 加字段过滤，兼容老快照里可能多出/少出字段的场景
    from state import ChatMessage
    import dataclasses as _dc
    _CM_FIELDS = {f.name for f in _dc.fields(ChatMessage)}
    raw_chats = d.get("chapter_chats", {}) or {}
    state.chapter_chats = {}
    for k, msgs in raw_chats.items():
        try:
            ci = int(k)
        except (TypeError, ValueError):
            continue
        parsed = []
        for m in (msgs or []):
            if not isinstance(m, dict):
                continue
            try:
                parsed.append(ChatMessage(**{kk: vv for kk, vv in m.items() if kk in _CM_FIELDS}))
            except Exception as e:
                print(f"  [load_state] chapter_chats[{ci}] 消息解析失败，跳过：{type(e).__name__}")
        state.chapter_chats[ci] = parsed

    # 章节能力审计
    from state import AbilityAudit, AbilityUse, AbilityIssue
    _AU_FIELDS = {f.name for f in _dc.fields(AbilityUse)}
    _AI_FIELDS = {f.name for f in _dc.fields(AbilityIssue)}
    _AA_FIELDS = {f.name for f in _dc.fields(AbilityAudit)}
    raw_audits = d.get("ability_audits", {}) or {}
    state.ability_audits = {}
    for k, audit_d in raw_audits.items():
        if not isinstance(audit_d, dict):
            continue
        try:
            ci = int(k)
        except (TypeError, ValueError):
            continue
        uses = []
        for u in (audit_d.get("ability_uses") or []):
            if not isinstance(u, dict):
                continue
            try:
                uses.append(AbilityUse(**{kk: vv for kk, vv in u.items() if kk in _AU_FIELDS}))
            except Exception:
                continue
        issues = []
        for i in (audit_d.get("issues") or []):
            if not isinstance(i, dict):
                continue
            try:
                issues.append(AbilityIssue(**{kk: vv for kk, vv in i.items() if kk in _AI_FIELDS}))
            except Exception:
                continue
        try:
            # 不能直接 **audit_d——可能有 ability_uses/issues 这类嵌套字段被重复传
            audit_kwargs = {kk: vv for kk, vv in audit_d.items()
                            if kk in _AA_FIELDS and kk not in ("ability_uses", "issues")}
            audit_kwargs["chapter_index"] = audit_kwargs.get("chapter_index", ci)
            audit_kwargs["ability_uses"] = uses
            audit_kwargs["issues"] = issues
            state.ability_audits[ci] = AbilityAudit(**audit_kwargs)
        except Exception as e:
            print(f"  [load_state] ability_audits[{ci}] 解析失败，跳过：{type(e).__name__}: {e}")

    # 读者视角审计
    from state import ReaderExperienceAudit, ReaderExperienceIssue
    _RI_FIELDS = {f.name for f in _dc.fields(ReaderExperienceIssue)}
    _RA_FIELDS = {f.name for f in _dc.fields(ReaderExperienceAudit)}
    raw_r = d.get("reader_audits", {}) or {}
    state.reader_audits = {}
    for k, audit_d in raw_r.items():
        if not isinstance(audit_d, dict):
            continue
        try:
            ci = int(k)
        except (TypeError, ValueError):
            continue
        issues = []
        for i in (audit_d.get("issues") or []):
            if not isinstance(i, dict):
                continue
            try:
                issues.append(ReaderExperienceIssue(**{kk: vv for kk, vv in i.items() if kk in _RI_FIELDS}))
            except Exception:
                continue
        try:
            kw = {kk: vv for kk, vv in audit_d.items() if kk in _RA_FIELDS and kk != "issues"}
            kw["chapter_index"] = kw.get("chapter_index", ci)
            kw["issues"] = issues
            state.reader_audits[ci] = ReaderExperienceAudit(**kw)
        except Exception as e:
            print(f"  [load_state] reader_audits[{ci}] 解析失败：{type(e).__name__}: {e}")

    # 对话审计
    from state import DialogueAudit, DialogueIssue
    _DI_FIELDS = {f.name for f in _dc.fields(DialogueIssue)}
    _DA_FIELDS = {f.name for f in _dc.fields(DialogueAudit)}
    raw_d = d.get("dialogue_audits", {}) or {}
    state.dialogue_audits = {}
    for k, audit_d in raw_d.items():
        if not isinstance(audit_d, dict):
            continue
        try:
            ci = int(k)
        except (TypeError, ValueError):
            continue
        issues = []
        for i in (audit_d.get("issues") or []):
            if not isinstance(i, dict):
                continue
            try:
                issues.append(DialogueIssue(**{kk: vv for kk, vv in i.items() if kk in _DI_FIELDS}))
            except Exception:
                continue
        try:
            kw = {kk: vv for kk, vv in audit_d.items() if kk in _DA_FIELDS and kk != "issues"}
            kw["chapter_index"] = kw.get("chapter_index", ci)
            kw["issues"] = issues
            state.dialogue_audits[ci] = DialogueAudit(**kw)
        except Exception as e:
            print(f"  [load_state] dialogue_audits[{ci}] 解析失败：{type(e).__name__}: {e}")

    state.glossary = [_load_glossary_entry(g) for g in d.get("glossary", [])]
    state.version_snapshots = [_load_version_snapshot(v) for v in d.get("version_snapshots", [])]
    state.pending_approvals = [_load_pending_approval(p) for p in d.get("pending_approvals", [])]

    # Stage / Volume 级审查（legacy single-file 兼容）
    try:
        from state import ReviewIssue
        import dataclasses as _dc
        _RI = {f.name for f in _dc.fields(ReviewIssue)}
        raw_sr = d.get("stage_review_reports", {}) or {}
        state.stage_review_reports = {}
        for sid, issues in raw_sr.items():
            lst = []
            for i in (issues or []):
                if isinstance(i, dict):
                    try:
                        lst.append(ReviewIssue(**{kk: vv for kk, vv in i.items() if kk in _RI}))
                    except Exception: pass
            state.stage_review_reports[str(sid)] = lst
        raw_vr = d.get("volume_review_reports", {}) or {}
        state.volume_review_reports = {}
        for vi, issues in raw_vr.items():
            try: vi_i = int(vi)
            except (TypeError, ValueError): continue
            lst = []
            for i in (issues or []):
                if isinstance(i, dict):
                    try:
                        lst.append(ReviewIssue(**{kk: vv for kk, vv in i.items() if kk in _RI}))
                    except Exception: pass
            state.volume_review_reports[vi_i] = lst
        state.done_stage_ids = [str(x) for x in (d.get("done_stage_ids") or [])]
        state.done_volume_review_indices = []
        for x in (d.get("done_volume_review_indices") or []):
            try: state.done_volume_review_indices.append(int(x))
            except (TypeError, ValueError): pass
    except Exception as e:
        print(f"  [load_state] stage/volume review 解析失败：{type(e).__name__}: {e}")

    return state


# ═══════════════════════════════════════════════════════
#  公共接口
# ═══════════════════════════════════════════════════════

def save_state(state: NovelState):
    """
    保存 NovelState 到磁盘。
    新格式：分文件存储到 checkpoint/state/ 下每个 section 一个文件。
    老格式：checkpoint/state.json 整体保存（作为备份 + 兼容）。
    """
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    # 新：分文件
    try:
        import state_storage
        state_storage.save_split(state)
    except Exception as e:
        # 分文件失败就 fallback 到单文件
        print(f"  [!] 分文件保存失败，fallback 到 state.json：{e}")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_to_json(state), f, ensure_ascii=False, indent=2)


def save_state_section(state: NovelState, section_name: str):
    """
    增量保存——只写一个 section。
    大幅降低 mark_phase_done / mark_chapter_done 的磁盘 IO。
    """
    try:
        import state_storage
        # meta 字段的增量保存
        if section_name == "meta":
            state_storage.save_meta(state)
        else:
            state_storage.save_section(section_name, state)
    except Exception:
        # 增量失败就退化到全量
        save_state(state)


def load_state() -> NovelState | None:
    """
    加载 NovelState：
      1. 优先尝试分文件结构（checkpoint/state/）
      2. 若老项目只有 state.json → 自动迁移后加载
      3. 都没有 → 返回 None
    """
    import state_storage
    from state import NovelState as _NS

    # 老项目迁移——幂等，若已迁移过直接跳过
    if os.path.exists(STATE_FILE):
        try:
            state_storage.migrate_from_single(STATE_FILE)
        except Exception as e:
            print(f"  [!] 迁移老 state.json 失败：{e}")

    # 1. 分文件加载
    if os.path.isdir(state_storage.state_dir()):
        state = _NS(title="", genre="", theme="")
        if state_storage.load_split(state):
            return state

    # 2. 老格式兜底
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return _load_state(json.load(f))

    return None


# ── 进度跟踪 ──────────────────────────────────────────

def load_progress() -> dict:
    """返回进度字典：{phases: set[str], chapters: set[int]}"""
    if not os.path.exists(PROGRESS_FILE):
        return {"phases": [], "chapters": []}
    with open(PROGRESS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {"phases": raw.get("phases", []), "chapters": raw.get("chapters", [])}


def _save_progress(progress: dict):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def mark_phase_done(phase: str, state: NovelState):
    """
    标记一个阶段完成并同步保存 state。
    分文件存储：每个 section 独立文件，并发写不冲突、按需增量写。
    """
    p = load_progress()
    if phase not in p["phases"]:
        p["phases"].append(phase)
    _save_progress(p)
    save_state(state)  # 新版 save_state 内部走分文件（线程安全）
    # phase 标完成后，清掉它的过期 warning（避免历史 error 一直留在徽章上）
    try:
        clear_progress_warnings(phase=phase)
    except Exception:
        pass
    print(f"  💾 [断点] 阶段 {phase} 已保存")


def mark_phase_done_if(phase: str, state: NovelState, predicate, *, on_skip_msg: str = "") -> bool:
    """
    只在 predicate(state) 为真时才 mark_phase_done。
    用途：agent 跑完但产物为空（LLM 熔断/失败被 empty_ok 吞掉）时不写 progress,
          下次重启会重跑该 phase。
    返回是否真的标记了。
    """
    try:
        ok = bool(predicate(state))
    except Exception:
        ok = False
    if ok:
        mark_phase_done(phase, state)
        # 成功的 phase 把它的旧 warning 清掉
        clear_progress_warnings(phase=phase)
        return True
    extra = f"（{on_skip_msg}）" if on_skip_msg else ""
    print(f"  ⚠ Phase {phase} 产物为空——不标记完成，下次会重跑{extra}")
    # source 已含 phase 编号，message 不重复 phase 编号（避免前端"phase:3A Phase 3A ..."叠词）
    add_progress_warning(
        level="warn",
        source=f"phase:{phase}",
        message="产物为空——不标记完成，下次会重跑" + extra,
    )
    save_state(state)  # 即便不写 progress，已生成的 state 字段（哪怕空）也保存一下
    return False


# ─────────────────────────────────────────────────────────────
#  统一的 progress_status warning 通道
#  director / agent / regen 都可以往这写，前端轮询 progress_status.json
#  里的 warnings 字段，给用户看"哪个 phase 产物没出来 / 上游缺什么"。
# ─────────────────────────────────────────────────────────────
def _progress_status_path(project_id: str = None) -> str:
    """获取（指定或当前）项目的 progress_status.json 路径——延迟 import 避免循环。"""
    try:
        import project_context
        return project_context.progress_status_file(project_id)
    except Exception:
        return ""


def add_progress_warning(level: str, source: str, message: str) -> None:
    """追加一条 warning 到 progress_status.json 的 warnings 数组。
    level: 'warn' | 'error' | 'info'
    source: 'phase:3A' / 'agent:twist_designer' / 'regen:lines' / ...

    去重策略：同 source 只保留最新一条（message 不同也覆盖）——避免同一 phase 多次
    失败时 LLM 反馈不同导致累积一堆几乎一样的 warning。
    """
    path = _progress_status_path()
    if not path:
        return
    try:
        from datetime import datetime
        data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                data = {}
        warnings = data.get("warnings") or []
        # 同 source 去重——保留最新（覆盖 message + level + at）
        warnings = [w for w in warnings if w.get("source") != source]
        warnings.append({
            "level": level,
            "source": source,
            "message": message,
            "at": datetime.now().isoformat(timespec="seconds"),
        })
        # 上限——只保留最近 100 条
        warnings = warnings[-100:]
        data["warnings"] = warnings
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def clear_progress_warnings(*, phase: str = None, source: str = None, project_id: str = None) -> None:
    """清除 warnings：phase 传入会清 source=='phase:<phase>' 的；source 直接匹配；不传都清空。"""
    path = _progress_status_path(project_id)
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        warnings = data.get("warnings") or []
        target_source = source or (f"phase:{phase}" if phase else None)
        if target_source:
            warnings = [w for w in warnings if w.get("source") != target_source]
        else:
            warnings = []
        data["warnings"] = warnings
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
#  老项目 phase 别名迁移
#  历史：3E2 在 director.py 现是"红鲱鱼"，scheduler_tasks.py 现是"反转"；
#  统一后 3E3=反转、3E2=红鲱鱼。老 progress 若 3E2 出现且 state 看起来像
#  "反转跑过但红鲱鱼空"，把 3E2 改名为 3E3，让红鲱鱼能重跑。
# ─────────────────────────────────────────────────────────────
def migrate_legacy_phase_ids(state: NovelState) -> bool:
    """检测并迁移历史 phase id 不一致。返回是否做了迁移。"""
    p = load_progress()
    phases = p.get("phases", []) or []
    changed = False

    if "3E2" in phases:
        twist_chains = getattr(getattr(state, "twist_system", None), "chains", []) or []
        red_herrings = getattr(state, "red_herrings", []) or []
        if twist_chains and not red_herrings:
            # 老 3E2 跑的其实是反转——重命名为 3E3，让 director 的 3E2 红鲱鱼重新跑
            phases = [pid if pid != "3E2" else "3E3" for pid in phases]
            p["phases"] = phases
            _save_progress(p)
            print("  🔄 [迁移] 老项目 3E2(反转) → 3E3，下次启动会补跑红鲱鱼")
            changed = True
    return changed


def mark_chapter_done(chapter_index: int, state: NovelState):
    """标记一章完成并同步保存 state（每章写完调用一次）。"""
    p = load_progress()
    if chapter_index not in p["chapters"]:
        p["chapters"].append(chapter_index)
    _save_progress(p)
    save_state(state)


def is_phase_done(phase: str, progress: dict) -> bool:
    return phase in progress["phases"]


def is_chapter_done(chapter_index: int, progress: dict) -> bool:
    return chapter_index in progress["chapters"]


def clear_checkpoint():
    """清空所有断点（全新开始时调用）。"""
    for f in [STATE_FILE, PROGRESS_FILE]:
        if os.path.exists(f):
            os.remove(f)
    print("  🗑  断点已清除，从头开始")
