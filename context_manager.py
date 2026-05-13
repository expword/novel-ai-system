"""
ContextManager — 上下文预算管理系统。

设计原则：
- 每个智能体有字符预算，超出则按优先级裁剪
- 信息分为4级：CRITICAL(必须) / HIGH(重要) / MEDIUM(有用) / LOW(可选)
- 静态信息（世界观/规则）预先压缩为摘要，不重复传完整版
- 动态信息（记忆/状态）只取相关的最近N条
- 角色信息只包含本章实际出场的角色

字符预算参考（中文约2字/token）：
  Writer:    6000字  ≈ 3000 tokens
  Critic:    3000字  ≈ 1500 tokens
  Memory:    2000字  ≈ 1000 tokens
  Director:  2000字  ≈ 1000 tokens
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from state import NovelState, NarrativeLine, Character, ChapterDirective


# ── 优先级常量 ────────────────────────────────────────
CRITICAL = 0   # 必须包含，超出预算也保留
HIGH     = 1   # 优先包含
MEDIUM   = 2   # 有预算则包含
LOW      = 3   # 空间充足时包含


# ── 各智能体字符预算 ──────────────────────────────────
BUDGETS = {
    "writer":   6000,
    "critic":   3000,
    "memory":   2000,
    "director": 2000,
}


@dataclass
class ContextSection:
    name: str
    content: str
    priority: int   # 0=CRITICAL, 1=HIGH, 2=MEDIUM, 3=LOW

    @property
    def char_count(self) -> int:
        return len(self.content)


class ContextBuilder:
    """按优先级组装上下文，超出预算时从低优先级开始裁剪。"""

    def __init__(self, budget: int):
        self.budget = budget
        self.sections: list[ContextSection] = []

    def add(self, name: str, content: str, priority: int = HIGH) -> "ContextBuilder":
        """添加一个上下文段落。"""
        if content and content.strip():
            self.sections.append(ContextSection(name, content.strip(), priority))
        return self

    def build(self) -> str:
        """按预算组装，优先级高的优先保留。"""
        # 按优先级排序，CRITICAL(0) 最优先
        sorted_sections = sorted(self.sections, key=lambda s: s.priority)

        selected = []
        used = 0

        for sec in sorted_sections:
            chars = sec.char_count
            if sec.priority == CRITICAL:
                # CRITICAL 超出预算也保留，但截断到2000字
                content = sec.content[:2000] if chars > 2000 else sec.content
                selected.append((sec.name, content))
                used += len(content)
            elif used + chars <= self.budget:
                selected.append((sec.name, sec.content))
                used += chars
            elif used + 200 <= self.budget:
                # 放得下一个截断版本（至少200字）
                available = self.budget - used
                truncated = sec.content[:available - 10] + "…"
                selected.append((sec.name, truncated))
                used = self.budget
                break
            # 完全放不下则跳过

        # 恢复原始顺序（按priority排序后index已变，需要按插入顺序重排）
        original_order = {id(s): i for i, s in enumerate(self.sections)}
        selected_names = {name for name, _ in selected}
        result = []
        for sec in self.sections:
            if sec.name in selected_names:
                for name, content in selected:
                    if name == sec.name:
                        result.append(f"【{name}】\n{content}")
                        break

        return "\n\n".join(result)

    def stats(self) -> str:
        total = sum(s.char_count for s in self.sections)
        return f"上下文：{total}字 → 预算{self.budget}字"


# ═══════════════════════════════════════════════════════
#  Writer 上下文（6000字预算）
# ═══════════════════════════════════════════════════════

def build_writer_context(
    state: NovelState,
    directive: ChapterDirective,
) -> str:
    """
    精确裁剪后的写作上下文。
    核心原则：只传本章实际需要的信息。
    """
    chapter_index = directive.chapter_index
    volume_index = directive.volume_index
    vol = state.get_volume(volume_index)

    builder = ContextBuilder(budget=BUDGETS["writer"])

    # ── CRITICAL: 实时故事状态（连续性核心）────────────
    from agents.thread_tracker import format_thread_for_writer
    thread_ctx = format_thread_for_writer(state.story_thread, chapter_index)
    builder.add("实时故事状态（精确承接）", thread_ctx, CRITICAL)

    # ── CRITICAL: 本章叙事任务 ───────────────────────
    lines_task = _build_active_lines_context(state, directive)
    builder.add("本章叙事线任务", lines_task, CRITICAL)

    # ── CRITICAL: 本章出场角色（仅相关角色）─────────
    chars_ctx = _build_relevant_characters(state, directive)
    builder.add("本章出场人物", chars_ctx, CRITICAL)

    # ── HIGH: 爽点与伏笔指令 ──────────────────────────
    sp_fw = _build_sp_fw_brief(state, directive)
    if sp_fw:
        builder.add("本章爽点与伏笔", sp_fw, HIGH)

    # ── CRITICAL: 本章反转揭露（若有）—— 新增 ──
    twist_reveal_ctx = _build_twist_reveal_context(state, directive)
    if twist_reveal_ctx:
        builder.add("本章反转揭露（必须执行）", twist_reveal_ctx, CRITICAL)

    # ── HIGH: 本章要埋的反转伏笔 + 近期反转链背景 ──
    twist_planting_ctx = _build_twist_planting_context(state, directive)
    if twist_planting_ctx:
        builder.add("本章要埋的反转伏笔", twist_planting_ctx, HIGH)

    # ── HIGH: MasterOutline 本卷关键节点（全书蓝图对齐）──
    mo_ctx = _build_master_outline_for_volume(state, directive)
    if mo_ctx:
        builder.add("全书蓝图·本卷关键节点", mo_ctx, HIGH)

    # ── HIGH: 红鲱鱼植入/揭穿 ────────────────────────
    rh_ctx = _build_red_herring_context(state, directive)
    if rh_ctx:
        builder.add("本章红鲱鱼操作", rh_ctx, HIGH)

    # ── HIGH: 主推线的最近记忆（只取主线，最多3条）──
    primary_line_memory = _build_primary_line_memory(state, directive, max_entries=3)
    if primary_line_memory:
        builder.add("主推线近期事件", primary_line_memory, HIGH)

    # ── HIGH: 角色当前状态（精简版）─────────────────
    char_states = _build_char_states(state, directive, max_chars=300)
    if char_states:
        builder.add("角色当前状态", char_states, HIGH)

    # ── MEDIUM: 辅助线记忆（每条最多2条记录）─────────
    secondary_memory = _build_secondary_lines_memory(state, directive, max_entries=2)
    if secondary_memory:
        builder.add("辅助线近期事件", secondary_memory, MEDIUM)

    # ── MEDIUM: 世界规则摘要（仅关键规则，非全文）──
    world_rules = _get_world_rules_brief(state, max_chars=400)
    builder.add("世界关键规则", world_rules, MEDIUM)

    # ── HIGH: 本卷活跃特殊能力（含本章是否有新觉醒阶段触发）
    abilities_brief = state.abilities_context_for_volume(volume_index, max_chars=400)
    if abilities_brief:
        builder.add("本卷特殊能力状态", abilities_brief, HIGH)

    # ── HIGH: 主角当前境界的力量刻度（防止战力崩坏）
    scaling_brief = _build_realm_scaling_brief(state, volume_index, max_chars=300)
    if scaling_brief:
        builder.add("主角境界力量刻度", scaling_brief, HIGH)

    # ── HIGH: 战斗/对峙章——主角"此刻 vs 当前对手"实力差面板
    ch_type = (getattr(directive, "chapter_type", "") or "").strip()
    needs_power_panel = ch_type in ("战斗章", "对峙章") or directive.tension.value in ("高潮", "反转")
    if needs_power_panel:
        power_panel = _build_protagonist_power_panel(state, directive, max_chars=350)
        if power_panel:
            builder.add("主角此刻实力 vs 本章对手", power_panel, HIGH)

    # ── HIGH: 本章附近的人物弧转折点（若有）
    arc_brief = _build_arc_transitions_near(state, chapter_index, max_chars=300)
    if arc_brief:
        builder.add("本章附近的人物心理转折", arc_brief, HIGH)

    # ── MEDIUM: 卷势力背景（分层，按揭露进度过滤）─────
    faction_brief = _get_faction_brief_for_volume(state, volume_index, max_chars=300)
    if faction_brief:
        builder.add("本卷势力格局", faction_brief, MEDIUM)

    # ── MEDIUM: 地理/交通（让"从A到B多久"有依据）
    geo_brief = state.geography.brief_for_volume(max_chars=300)
    if geo_brief:
        builder.add("地理/交通锚点", geo_brief, MEDIUM)

    # ── MEDIUM: 经济/物价（让金钱描写有尺度）
    eco_brief = _build_economy_brief(state, volume_index, max_chars=250)
    if eco_brief:
        builder.add("经济/物价锚点", eco_brief, MEDIUM)

    # ── LOW: 历史时间轴（仅当本章可能涉及古早典故时）
    timeline_brief = state.timeline.brief(max_events=4)
    if timeline_brief:
        builder.add("世界历史锚点", timeline_brief, LOW)

    # ── MEDIUM: 已登记的专有名词（防止 Writer 给同一地方起不同名字）
    glossary_brief = state.glossary_brief(max_items=15)
    if glossary_brief:
        builder.add("已登记专有名词（本章涉及时沿用，不要新造）", glossary_brief, MEDIUM)

    # ── LOW: 待兑现伏笔列表（只列标题，不含详情）────
    pending_fw = _get_pending_foreshadow_titles(state, max_items=4)
    if pending_fw:
        builder.add("待兑现伏笔（背景参考）", pending_fw, LOW)

    return builder.build()


# ═══════════════════════════════════════════════════════
#  Critic 上下文（3000字预算）
# ═══════════════════════════════════════════════════════

def build_critic_context(
    state: NovelState,
    directive: ChapterDirective,
) -> str:
    """
    Critic只需要：要求是什么 + 角色说话规则。
    不需要世界观/记忆/详细设定。
    """
    builder = ContextBuilder(budget=BUDGETS["critic"])

    # ── CRITICAL: 叙事线任务（要求）─────────────────
    lines_task = _build_lines_requirement(state, directive)
    builder.add("叙事线任务要求", lines_task, CRITICAL)

    # ── CRITICAL: 必须完成的事件 ─────────────────────
    must = "\n".join(f"· {e}" for e in directive.must_include) if directive.must_include else "无"
    builder.add("必须完成事件", must, CRITICAL)

    # ── CRITICAL: 角色说话风格（用于一致性检查）─────
    char_styles = _build_char_speech_styles(state, directive)
    builder.add("角色性格与说话风格", char_styles, CRITICAL)

    # ── HIGH: 爽点/伏笔完成检查清单 ─────────────────
    sp_fw_check = _build_sp_fw_checklist(state, directive)
    if sp_fw_check:
        builder.add("爽点伏笔检查清单", sp_fw_check, HIGH)

    # ── HIGH: 张力/节奏要求说明 ──────────────────────
    tension_guide = _get_tension_requirement(directive)
    builder.add("张力节奏要求", tension_guide, HIGH)

    # ── MEDIUM: 近期张力历史（判断节奏是否合适）────
    tension_history = _get_recent_tension_history(state, n=5)
    builder.add("近期张力历史", tension_history, MEDIUM)

    return builder.build()


# ═══════════════════════════════════════════════════════
#  Memory 上下文（2000字预算）
# ═══════════════════════════════════════════════════════

def build_memory_context(
    state: NovelState,
    chapter_index: int,
    volume_index: int,
) -> str:
    """
    Memory agent只需要：线ID列表 + 角色名 + 待处理的伏笔ID。
    正文本身已经包含所有需要提取的信息。
    """
    builder = ContextBuilder(budget=BUDGETS["memory"])

    # ── CRITICAL: 活跃叙事线ID和名称 ─────────────────
    active_lines = state.lines_active_in_chapter(chapter_index)
    lines_ids = "\n".join(
        f"- {ln.line_id}（{ln.scope.value}/{ln.line_type.value}：{ln.name}）"
        for ln in active_lines
    ) if active_lines else "无"
    builder.add("活跃叙事线", lines_ids, CRITICAL)

    # ── CRITICAL: 本卷活跃角色名单 ───────────────────
    chars = state.active_characters_in_volume(volume_index)
    char_names = "、".join(c.name for c in chars)
    builder.add("活跃角色", char_names, CRITICAL)

    # ── HIGH: 本章计划植入/兑现的伏笔ID ─────────────
    plant_fws = [fw for fw in state.foreshadow_items if fw.planted_chapter == chapter_index]
    resolve_fws = [fw for fw in state.foreshadow_items
                   if not fw.resolved and fw.planned_resolve_chapter == chapter_index]
    fw_info = []
    for fw in plant_fws:
        fw_info.append(f"[植入] {fw.fw_id}：{fw.content[:40]}")
    for fw in resolve_fws:
        fw_info.append(f"[兑现] {fw.fw_id}：{fw.resolution_description[:40]}")
    if fw_info:
        builder.add("本章伏笔操作", "\n".join(fw_info), HIGH)

    # ── HIGH: 本章计划触发的爽点ID ───────────────────
    sp_trigger = [sp for sp in state.satisfaction_points
                  if not sp.triggered and abs(sp.target_chapter - chapter_index) <= 1]
    if sp_trigger:
        sp_info = "\n".join(f"[爽点] {sp.sp_id}：{sp.title}" for sp in sp_trigger)
        builder.add("本章爽点", sp_info, HIGH)

    return builder.build()


# ═══════════════════════════════════════════════════════
#  内部构建函数
# ═══════════════════════════════════════════════════════

def _build_active_lines_context(state: NovelState, directive: ChapterDirective) -> str:
    """只返回活跃线的当前阶段描述，不含历史。"""
    parts = []
    for lid in directive.active_lines:
        line = state.get_line(lid)
        if not line:
            continue
        phase = line.get_phase_for_chapter(directive.chapter_index)
        if not phase:
            continue
        prefix = "★主推" if lid == directive.primary_line else "  辅助"
        parts.append(
            f"{prefix} [{line.scope.value}/{line.line_type.value}] {line.name}\n"
            f"  阶段{phase.phase_index}/{len(line.phases)}《{phase.name}》"
            f"[{phase.tension.value}]：{phase.description}"
        )
    return "\n".join(parts) if parts else "按故事自然推进"


def _build_recent_summaries(state: NovelState, n: int = 3) -> str:
    """最近N章的摘要，每条限制在80字内。"""
    recent = state.completed_chapters[-n:]
    if not recent:
        return "本书第一章，无前情。"
    lines = []
    for c in recent:
        summary = c.summary[:80] + ("…" if len(c.summary) > 80 else "")
        lines.append(f"第{c.index}章《{c.title}》[{c.tension.value}]：{summary}")
    return "\n".join(lines)


def _build_relevant_characters(state: NovelState, directive: ChapterDirective) -> str:
    """
    只返回本章很可能出场的角色。
    判断标准：active_lines涉及的角色 + 主角 + 本卷主要对手。
    """
    vol = state.get_volume(directive.volume_index)

    # 收集本章活跃线涉及的角色名
    relevant_names: set[str] = set()
    for lid in directive.active_lines:
        line = state.get_line(lid)
        if line:
            relevant_names.update(line.characters)

    # 主角必须有
    for c in state.characters:
        if c.role.value == "主角":
            relevant_names.add(c.name)
            break

    # 本卷对手
    if vol and vol.volume_antagonist:
        relevant_names.add(vol.volume_antagonist)

    # 过滤：只取本卷活跃的角色
    active = state.active_characters_in_volume(directive.volume_index)
    chars = [c for c in active if c.name in relevant_names]

    # 如果过滤后太少，补充到最多4个
    if len(chars) < 2:
        chars = active[:4]

    lines = []
    for c in chars[:5]:  # 最多5个角色
        vol_realm = c.volume_realm.get(directive.volume_index, c.realm)
        status = state.memory.character_states.get(c.name, "")
        status_str = f" | 当前：{status[:30]}" if status else ""
        lines.append(
            f"【{c.role.value}】{c.name}（{vol_realm}）"
        )
        lines.append(f"  {c.personality}｜{c.speech_pattern}")
        lines.append(f"  动机：{c.motivation}{status_str}")
        # 细腻刻画钩子（若有）——给 writer 立体化的抓手
        hooks = []
        if c.signature_mannerisms:
            hooks.append(f"动作：{' / '.join(c.signature_mannerisms[:2])}")
        if c.verbal_tics:
            hooks.append(f"口癖：{' / '.join(c.verbal_tics[:2])}")
        if c.sensory_signature:
            hooks.append(f"感官：{c.sensory_signature[:30]}")
        if c.default_stress_response:
            hooks.append(f"压力反应：{c.default_stress_response[:25]}")
        if hooks:
            lines.append(f"  · {' ｜ '.join(hooks)}")
    return "\n".join(lines)


def _build_primary_line_memory(
    state: NovelState,
    directive: ChapterDirective,
    max_entries: int = 3,
) -> str:
    """只取主推线的最近N条记忆事件。"""
    if not directive.primary_line:
        return ""
    entries = state.memory.get_by_line(directive.primary_line, last_n=max_entries)
    if not entries:
        return ""
    return "\n".join(
        f"[第{e.chapter_index}章/{e.event_type}] {e.content[:60]}"
        for e in entries
    )


def _build_secondary_lines_memory(
    state: NovelState,
    directive: ChapterDirective,
    max_entries: int = 2,
) -> str:
    """辅助线，每条只取最近N条，合并后最多返回300字。"""
    parts = []
    secondary = [lid for lid in directive.active_lines if lid != directive.primary_line]
    for lid in secondary[:2]:  # 最多处理2条辅助线
        line = state.get_line(lid)
        entries = state.memory.get_by_line(lid, last_n=max_entries)
        if entries and line:
            entry_strs = " / ".join(f"[{e.chapter_index}章]{e.content[:40]}" for e in entries)
            parts.append(f"{line.name}：{entry_strs}")
    result = "\n".join(parts)
    return result[:300]  # 硬截断


def _build_char_states(state: NovelState, directive: ChapterDirective, max_chars: int = 300) -> str:
    """精简版角色状态，只包含有状态更新的角色。"""
    vol = state.get_volume(directive.volume_index)
    active = state.active_characters_in_volume(directive.volume_index) if vol else state.characters
    lines = []
    for c in active[:6]:
        status = state.memory.character_states.get(c.name, "")
        if status and "待登场" not in status:
            # 只保留最后一次更新的核心内容
            status_brief = status.split("] ")[-1] if "] " in status else status
            lines.append(f"{c.name}：{status_brief[:40]}")
    result = "\n".join(lines)
    return result[:max_chars]


def _get_world_rules_brief(state: NovelState, max_chars: int = 400) -> str:
    """从facts中提取世界规则，不返回完整世界观文本。"""
    rules = [f for f in state.memory.facts if f.startswith("[世界规则]")]
    parts = []
    if rules:
        parts.append("\n".join(r[6:] for r in rules[:8]))
    else:
        brief = state.world_setting[:200] if state.world_setting else ""
        if brief:
            parts.append(brief)
    ps = state.power_system
    if ps:
        parts.append(f"力量体系：{state.power_system_brief()}")
        # 新增：流派 + 特殊机制（让 writer 写作时保持流派风格）
        if ps.power_flow or ps.special_mechanics:
            parts.append(f"流派：{ps.flow_brief()}")
        if ps.special_mechanics:
            mech_lines = "\n".join(
                f"  · {m.name}：{m.description[:50]}"
                for m in ps.special_mechanics[:4]
            )
            parts.append(f"特殊机制：\n{mech_lines}")
    result = "\n".join(parts)
    return result[:max_chars]


def _get_faction_brief_for_volume(state: NovelState, volume_index: int, max_chars: int = 300) -> str:
    """
    按层级返回本卷已揭露的活跃势力，含中立势力和内部矛盾提示。
    隐藏势力（is_hidden=True）在 reveal_volume 之前不出现。
    """
    from agents.faction_architect import get_faction_context_for_writer
    return get_faction_context_for_writer(state, volume_index, max_chars=max_chars)


def _get_pending_foreshadow_titles(state: NovelState, max_items: int = 4) -> str:
    """只列出已植入且未兑现的主线伏笔标题，不含详情。"""
    from state import ForeshadowImportance
    pending = [
        fw for fw in state.foreshadow_items
        if not fw.resolved and fw.planted_chapter > 0
           and fw.importance == ForeshadowImportance.MAJOR
    ][:max_items]
    if not pending:
        return ""
    return "\n".join(f"- [{fw.fw_id}] {fw.content[:40]}（计划{fw.planned_resolve_chapter}章兑现）"
                     for fw in pending)


def _build_lines_requirement(state: NovelState, directive: ChapterDirective) -> str:
    """为Critic生成叙事线要求清单。"""
    parts = []
    for lid in directive.active_lines:
        line = state.get_line(lid)
        if not line:
            continue
        phase = line.get_phase_for_chapter(directive.chapter_index)
        if phase:
            tag = "★主推" if lid == directive.primary_line else "辅助"
            parts.append(f"[{tag}] {line.name}：{phase.description}")
    return "\n".join(parts) if parts else "自由推进"


def _build_char_speech_styles(state: NovelState, directive: ChapterDirective) -> str:
    """为Critic提供角色性格和说话风格，用于一致性检查。"""
    vol = state.get_volume(directive.volume_index)
    active = state.active_characters_in_volume(directive.volume_index) if vol else state.characters
    lines = []
    for c in active[:6]:
        lines.append(
            f"{c.name}（{c.role.value}）：{c.personality} | 说话：{c.speech_pattern}"
        )
    return "\n".join(lines)


def _build_sp_fw_brief(state: NovelState, directive: ChapterDirective) -> str:
    """爽点和伏笔的精简指令。"""
    parts = []
    for sp_id in directive.satisfaction_points:
        sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
        if sp:
            parts.append(f"[触发爽点/{sp.sp_type.value}] {sp.title}：{sp.payoff_description[:60]}")
    for fw_id in directive.foreshadow_plant:
        fw = state.get_foreshadow(fw_id)
        if fw:
            parts.append(f"[植入伏笔] {fw.content[:50]}")
    for fw_id in directive.foreshadow_resolve:
        fw = state.get_foreshadow(fw_id)
        if fw:
            parts.append(f"[兑现伏笔] {fw.resolution_description[:50]}")
    return "\n".join(parts)


def _build_sp_fw_checklist(state: NovelState, directive: ChapterDirective) -> str:
    """为Critic生成爽点和伏笔检查清单。"""
    items = []
    for sp_id in directive.satisfaction_points:
        sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
        if sp:
            items.append(f"□ 爽点触发：{sp.title}（{sp.sp_type.value}，强度{sp.intensity}）")
    for fw_id in directive.foreshadow_plant:
        fw = state.get_foreshadow(fw_id)
        if fw:
            items.append(f"□ 伏笔植入：{fw.content[:40]}")
    for fw_id in directive.foreshadow_resolve:
        fw = state.get_foreshadow(fw_id)
        if fw:
            items.append(f"□ 伏笔兑现：{fw.resolution_description[:40]}")
    return "\n".join(items)


def _get_tension_requirement(directive: ChapterDirective) -> str:
    tension_desc = {
        "平静": "节奏舒缓，铺垫为主，不应出现激烈冲突",
        "上升": "矛盾积累，节奏偏快，对话有摩擦",
        "高潮": "极度紧张，短句密集，冲突爆发",
        "下落": "余波沉淀，节奏放慢，情绪沉淀",
        "反转": "意外转折，打碎预期，必须有前文支撑",
    }
    return (f"张力：{directive.tension.value} — "
            f"{tension_desc.get(directive.tension.value, '')}\n"
            f"节奏：{directive.rhythm.value}（{directive.word_pace}）\n"
            f"位置：{directive.chapter_position}")


def _build_protagonist_power_panel(state: NovelState, directive, max_chars: int = 350) -> str:
    """战斗/对峙章注入——主角"此刻"能调用什么 vs 本章主要对手在哪一档。

    数据源：
      · state.protagonist_power_log[chapter_index-1]（章后回写的章级日志）
      · directive.character_states 里本章对手的 realm
      · state.power_system.power_ratio_table（如果有）
    """
    from state import CharacterRole
    proto = next((c for c in state.characters if c.role == CharacterRole.PROTAGONIST), None)
    if not proto:
        return ""
    log = state.protagonist_power_log.get(directive.chapter_index - 1, {})
    cur_realm = log.get("realm") or proto.realm or "(未知)"
    means = log.get("key_means") or []
    breakthrough = log.get("recent_breakthrough") or ""
    lines = [
        f"主角【{proto.name}】此刻：境界={cur_realm}",
    ]
    if means:
        lines.append("最近 1 章用过的能力/手段：" + " / ".join(means[:5]))
    if breakthrough:
        lines.append(f"最近突破：{breakthrough}")
    # 本章对手——从 directive.character_states 取主角以外、role 是反派/major 的
    if hasattr(directive, "character_states") and directive.character_states:
        opp_lines = []
        for name, st in (directive.character_states or {}).items():
            if name == proto.name: continue
            opp_realm = st.get("realm") if isinstance(st, dict) else getattr(st, "realm", None)
            if opp_realm:
                opp_lines.append(f"  · {name}：境界={opp_realm}")
        if opp_lines:
            lines.append("本章对手境界：")
            lines.extend(opp_lines[:4])
    # 战力比表（如果 realm_designer 填了）
    ps = state.power_system
    if ps and getattr(ps, "power_ratio_table", None) and cur_realm:
        ratio_for_me = ps.power_ratio_table.get(cur_realm, {}) or {}
        if ratio_for_me:
            lines.append("主角对各档战力比（参考）：")
            for opp_r, ratio_desc in list(ratio_for_me.items())[:3]:
                lines.append(f"  · vs {opp_r}：{ratio_desc[:50]}")
    lines.append("【写作硬约束】战斗/对峙描写不得超出主角当前境界能力上限——越级必须付代价（耗血/反噬/瞬间失力）；不能让主角凭空开挂。")
    result = "\n".join(lines)
    return result[:max_chars]


def _build_realm_scaling_brief(state: NovelState, volume_index: int, max_chars: int = 300) -> str:
    """返回主角当前境界及前后一级的战力刻度——防止 writer 写崩战力。"""
    if not state.power_system or not state.power_system.realms:
        return ""
    # 主角本卷末境界
    target_realm_name = state.power_system.protagonist_realm_plan.get(volume_index, "")
    if not target_realm_name:
        return ""
    # 找到对应 realm 及前后相邻的
    realms = state.power_system.realms
    target_idx = None
    for i, r in enumerate(realms):
        if target_realm_name in r.name or r.name in target_realm_name:
            target_idx = i
            break
    if target_idx is None:
        return ""
    # 取本级 + 上下各一级
    show_range = list(range(max(0, target_idx - 1), min(len(realms), target_idx + 2)))
    lines = []
    for i in show_range:
        r = realms[i]
        tag = "★主角本卷" if i == target_idx else ""
        parts = [f"{r.name}{tag}"]
        if r.combat_capability:
            parts.append(f"战力：{r.combat_capability[:40]}")
        if r.overleap_rule:
            parts.append(f"越级：{r.overleap_rule[:25]}")
        lines.append(" | ".join(parts))
    result = "\n".join(lines)
    return result[:max_chars]


def _build_arc_transitions_near(state: NovelState, chapter_index: int, max_chars: int = 300) -> str:
    """返回本章前后几章将要发生的人物弧转折——给 writer 预警。"""
    transitions = state.arc_transitions_near_chapter(chapter_index, window=3)
    if not transitions:
        return ""
    lines = []
    for name, tr in transitions[:4]:
        lines.append(
            f"· {name} 第{tr.chapter_approx}章：{tr.trigger_event[:30]}"
            f"（内心：{tr.inner_change[:25]}）"
        )
    result = "\n".join(lines)
    return result[:max_chars]


def _build_economy_brief(state: NovelState, volume_index: int, max_chars: int = 250) -> str:
    """经济简报：货币 + 主角本卷财富 + 2-3 个跨档位物价锚点。"""
    eco = state.economy
    if not eco.currencies and not eco.price_anchors:
        return ""
    lines = []
    if eco.currencies:
        cs = " / ".join(f"{c.name}(1={c.exchange_to_base})" for c in eco.currencies[:4])
        lines.append(f"货币：{cs}")
    wealth = eco.wealth_at_volume(volume_index)
    if wealth:
        lines.append(f"★主角本卷财富：{wealth[:50]}")
    if eco.price_anchors:
        # 选 3 个跨档位锚点
        tiers_seen = set()
        picks = []
        for a in eco.price_anchors:
            if a.tier not in tiers_seen:
                picks.append(a)
                tiers_seen.add(a.tier)
            if len(picks) >= 3:
                break
        if picks:
            lines.append("物价参考：" + " / ".join(f"{a.item}={a.price}" for a in picks))
    result = "\n".join(lines)
    return result[:max_chars]


def _get_recent_tension_history(state: NovelState, n: int = 5) -> str:
    recent = state.tension_history[-n:]
    if not recent:
        return "暂无历史"
    return " → ".join(t.value for t in recent) + f" → [本章:{state.tension_history[-1].value if state.tension_history else '?'}]"


def _build_twist_reveal_context(state: NovelState, directive: ChapterDirective) -> str:
    """本章要揭露的反转层——CRITICAL：Writer 必须写到这几层揭露。"""
    if not getattr(directive, "twist_reveals", None):
        return ""
    parts = []
    for token in directive.twist_reveals:
        try:
            chain_id, layer_str = token.split(":")
            layer_num = int(layer_str)
        except ValueError:
            continue
        hit = state.find_twist_layer(chain_id, layer_num)
        if not hit:
            continue
        chain, layer = hit
        parts.append(
            f"▶ [{chain.title}] Layer {layer.layer}/{chain.target_layers} "
            f"（{chain.category}｜{layer.twist_mechanism}）\n"
            f"  读者原以为：{layer.surface_belief}\n"
            f"  本章揭露：{layer.reveal}\n"
            f"  情感冲击：{layer.emotional_impact}"
        )
        # 提示已埋的伏笔（让揭露来得公平）
        if layer.clues_planted:
            parts.append("  对应的前置伏笔（读者应已在前几章看到）：" +
                         " / ".join(c[:40] for c in layer.clues_planted[:3]))
    return "\n".join(parts)


def _build_twist_planting_context(state: NovelState, directive: ChapterDirective) -> str:
    """本章要埋的反转伏笔——让未来的反转揭露有根。"""
    if not getattr(directive, "twist_clues_plant", None):
        return ""
    parts = []
    for token in directive.twist_clues_plant:
        try:
            chain_id, layer_str = token.split(":")
            layer_num = int(layer_str)
        except ValueError:
            continue
        hit = state.find_twist_layer(chain_id, layer_num)
        if not hit:
            continue
        chain, layer = hit
        if not layer.clues_planted:
            continue
        parts.append(
            f"● [{chain.title}] Layer {layer.layer}（将于 {layer.reveal_anchor} 揭露）\n"
            f"  本章可埋的伏笔：" + " / ".join(c[:50] for c in layer.clues_planted[:2]) + "\n"
            f"  注意：不能直接揭露，只能让读者事后回味才觉察"
        )
    return "\n".join(parts[:3])  # 最多 3 条以免干扰主线


def _build_master_outline_for_volume(state: NovelState, directive: ChapterDirective) -> str:
    """MasterOutline 本卷关键节点（plot_setpieces）——让 writer 对齐全书蓝图。"""
    setpieces = state.plot_setpieces_for_volume(directive.volume_index)
    if not setpieces:
        return ""
    lines = []
    for p in setpieces[:5]:
        ids = f"（涉及：{', '.join(p.involved_slot_ids[:3])}）" if p.involved_slot_ids else ""
        lines.append(f"· {p.anchor}｜{p.kind}：{p.gist[:60]}{ids}")
    mo = state.master_outline
    if mo and mo.story_premise:
        return f"全书主线：{mo.story_premise[:100]}\n\n本卷锚点：\n" + "\n".join(lines)
    return "\n".join(lines)


def _build_red_herring_context(state: NovelState, directive: ChapterDirective) -> str:
    """本章植入/揭穿的红鲱鱼。"""
    plant_ids = getattr(directive, "red_herring_plant", []) or []
    debunk_ids = getattr(directive, "red_herring_debunk", []) or []
    if not plant_ids and not debunk_ids:
        return ""
    parts = []
    for rh_id in plant_ids:
        rh = state.get_red_herring(rh_id)
        if rh:
            parts.append(
                f"◐ [植入红鲱鱼 {rh.rh_id}] {rh.content[:50]}\n"
                f"   误导目的：{rh.misdirection_purpose[:50]}\n"
                f"   真相（作者视角，不要写出来）：{rh.actual_truth[:50]}"
            )
    for rh_id in debunk_ids:
        rh = state.get_red_herring(rh_id)
        if rh:
            parts.append(
                f"○ [揭穿红鲱鱼 {rh.rh_id}] 此前的假线索：{rh.content[:40]}\n"
                f"   本章要让读者意识到这是假的，真相是：{rh.actual_truth[:60]}"
            )
    return "\n".join(parts)
