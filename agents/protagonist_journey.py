"""
ProtagonistJourneyAgent — 主角历程三层规划。

规划顺序（不可跳过）：
  步骤1: plan_protagonist_arc()        — 整体弧线（核心创伤/真实目标/致命弱点）
  步骤2: plan_volume_milestones()      — 卷级里程碑（每卷首尾状态+最艰难选择）
  步骤3: plan_stage_beats()            — 舞台级节拍（在每个叙事舞台中的经历）

三层结构确保：
- 章节有足够深的"根"（不是凭空生成）
- 主角的每一步都服务于整体弧线
- 同一卷内不同舞台有连贯的情感线索
"""
from utils.json_utils import repair_json, request_json, pick_list
from llm_layer.llm import system_user
from persistence.state import (
    NovelState, ProtagonistJourney, ProtagonistMilestone, ProtagonistStageBeat,
)
from config import NUM_VOLUMES


SYSTEM = """你是小说主角弧线设计师，专注于人物成长的内在逻辑。
【分形起承转合对齐】
- 主角的整体弧线（overall growth_arc）本身就是一次完整的起承转合，对应整本书"起/承/转/合"四段。
- 每卷的里程碑也是一次完整的起承转合——entry_state→triumph/darkest/hardest→exit_state，要能看出"起承转合"的走势。
- 舞台节拍的 milestone_phase 必须与所属卷里程碑内部的"起/承/转/合"对齐。

【单主角铁律】
这是全书唯一的主角——所有配角、反派、机缘、势力、伏笔都是为了给这段弧线制造张力、代价、机缘和蜕变。
设计时不要为了让配角"更出彩"而削弱主角的弧线重量。

你设计的主角历程需要：
- 外部成长（力量/地位）和内部成长（心理/观念）同步推进，有时相互矛盾
- 每个阶段主角的"变化"要真实（不是突然顿悟）
- 高光时刻由低谷铺垫，低谷由上一卷的选择导致
- 主角的致命弱点会在关键时刻造成代价
输出严格JSON。"""


def plan_protagonist_journey(state: NovelState) -> None:
    """完整的主角历程三步规划，写入 state.protagonist_journey。"""
    _step1_overall_arc(state)
    _step2_volume_milestones(state)
    _step3_stage_beats(state)


# ── 步骤1: 整体弧线 ──────────────────────────────────────

def _step1_overall_arc(state: NovelState) -> None:
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    if not protagonist:
        return

    realm_plan = ""
    if state.power_system:
        realm_plan = " → ".join(
            f"第{v}卷末:{r}" for v, r in sorted(state.power_system.protagonist_realm_plan.items())
        ) or "（本书无层级体系，按情节阶段推进）"

    volumes_brief = "\n".join(
        f"第{v.index}卷《{v.title}》：{v.theme} | 对手：{v.volume_antagonist}"
        for v in state.volumes
    )

    web_hints = ""
    if state.relationship_web.bonds:
        key_bonds = [b for b in state.relationship_web.bonds if b.affects_protagonist][:5]
        web_hints = "\n".join(
            f"- {b.char_a}↔{b.char_b}：{b.surface_relation}（真实：{b.true_relation}）"
            for b in key_bonds
        )

    prompt = f"""
为《{state.title}》主角【{protagonist.name}】设计完整的人生弧线。

主角基础信息：
  性格：{protagonist.personality_detail}
  创伤：{protagonist.trauma}
  渴望：{protagonist.desire}
  恐惧：{protagonist.fear}
  致命弱点：{protagonist.fatal_flaw}
  动机：{protagonist.motivation}

阶段/境界推进路线（按题材：境界/职位/学历/异能等级/家境……如本书无层级体系则忽略）：{realm_plan or '（无层级推进）'}

全书卷结构：
{volumes_brief}

主角关键关系（部分）：
{web_hints or '待设计'}

═══ 整体弧线设计要求 ═══
1. overall_theme：主角故事的核心主题（一句话，不超过30字）
2. core_wound：驱动主角前进的根源创伤（深层的，不是表面的）
3. true_goal：主角真正追求的（可能与表面目标不同，读者到后期才明白）
4. fatal_flaw：会反复让主角付出代价的弱点（性格/执念/认知盲区）
5. central_conflict：主角与命运/反派/自我的核心矛盾（一句话）
6. growth_arc：全书成长轨迹（3-5句话，描述内在变化而非外在成就）

输出JSON：
{{
  "overall_theme": "...",
  "core_wound": "...",
  "true_goal": "...",
  "fatal_flaw": "...",
  "central_conflict": "...",
  "growth_arc": "..."
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["overall_theme", "core_wound"],
        max_retries=4, temperature=0.72, agent_name="ProtagonistJourney[整体弧]",
        empty_ok=True,
    )
    if not data:
        data = {}

    j = state.protagonist_journey
    j.overall_theme = data.get("overall_theme", "")
    j.core_wound = data.get("core_wound", protagonist.trauma)
    j.true_goal = data.get("true_goal", protagonist.desire)
    j.fatal_flaw = data.get("fatal_flaw", protagonist.fatal_flaw)
    j.central_conflict = data.get("central_conflict", "")
    j.growth_arc = data.get("growth_arc", protagonist.arc)

    print(f"  ✓ 主角整体弧线")
    print(f"    主题：{j.overall_theme}")
    print(f"    核心创伤：{j.core_wound[:40]}")
    print(f"    真实目标：{j.true_goal[:40]}")
    print(f"    致命弱点：{j.fatal_flaw[:30]}")
    print(f"    核心矛盾：{j.central_conflict[:50]}")


# ── 早期入口（Phase 0.6）：仅用 MasterOutline 定主角内核 ──
# 跑在 1A/1B/2A 之前——下游卷结构 / 人物档案 / 叙事线都能围绕它展开
# 字段产出与 _step1_overall_arc 完全相同，3G 检测到已填会跳过

def design_protagonist_core(state: NovelState) -> None:
    """
    Phase 0.6：主角内核——只依赖 master_outline，跑在卷结构/人物档案之前。

    定义主角的：overall_theme / core_wound / true_goal / fatal_flaw /
    central_conflict / growth_arc 六字段。下游 1B 卷结构、2A 人物画像、
    3A 叙事线都应读这些字段，让全书围绕主角内在轨迹展开。
    """
    mo = state.master_outline
    if not (mo and mo.generated):
        # 上游不全；让 director 的 mark_phase_done_if 看到空产物
        print("  ! MasterOutline 未生成，无法早定主角内核")
        return

    # 找主角槽（character_slots 里 role_tag 是"主角"的）
    mc_slot = next((s for s in mo.character_slots if s.role_tag == "主角"), None)
    mc_brief = ""
    if mc_slot:
        mc_brief = (
            f"  function_detail: {mc_slot.function_detail or mc_slot.function}\n"
            f"  brief_hint: {mc_slot.brief_hint}\n"
            f"  narrative_arc_hint: {mc_slot.narrative_arc_hint or '(待你设计)'}"
        )
    else:
        mc_brief = "（MasterOutline 未明确主角槽，请基于 premise 推断）"

    setpieces_brief = ""
    if mo.plot_setpieces:
        setpieces_brief = "\n关键节点（plot_setpieces，可能成为主角弧线的拐点）：\n" + "\n".join(
            f"  · {p.anchor}｜{p.kind}：{p.gist[:60]}"
            for p in mo.plot_setpieces[:6]
        )

    intent_brief = ""
    ci = state.creative_intent
    raw_desc = getattr(ci, "raw_description", "") or ""
    archetype_hint = getattr(ci, "protagonist_archetype_hint", "") or ""
    tone_summary = getattr(ci, "tone_summary", "") or ""
    if raw_desc or archetype_hint:
        intent_brief = (
            "\n创作意图（用户原话——主角内核要服从这个意图）：\n"
            f"  {raw_desc[:200]}\n"
            f"  主角原型：{archetype_hint[:100] if archetype_hint else '(未指定)'}\n"
            f"  整体气质：{tone_summary[:100] if tone_summary else '(未指定)'}"
        )

    prompt = f"""
为《{state.title}》（题材：{state.genre}）设计主角的内在弧线骨架。

═══ 故事骨架（MasterOutline）═══
故事前提：{mo.story_premise}
核心矛盾：{mo.central_conflict}
主题内核：{mo.thematic_core}
世界种子：{mo.world_seed[:120]}

主角槽位：
{mc_brief}
{setpieces_brief}{intent_brief}

═══ 任务 ═══
你要在卷结构/人物档案/叙事线还没展开之前，先把主角的【内核 6 件套】定下来——
后续所有设计都会读这 6 个字段，让整本书围绕主角内在轨迹展开。

要求：
1. overall_theme：主角故事的核心主题（不超过 30 字，回答"这是关于什么的故事"）
2. core_wound：驱动主角前进的根源创伤（必须**深层**，不是表面遭遇——是主角不愿承认的东西）
3. true_goal：主角真正追求的（可能与表面目标不同——读者后期才明白）
4. fatal_flaw：会反复让主角付出代价的弱点（性格/执念/认知盲区——具体而非"骄傲""固执"这种泛词）
5. central_conflict：主角与命运/反派/自我的核心矛盾（一句话，紧扣 wound + flaw 与 true_goal 的张力）
6. growth_arc：全书内在变化轨迹（3-5 句话——只写"内在变化"，不要写外在成就）

输出 JSON（六字段）：
{{
  "overall_theme": "...",
  "core_wound": "...",
  "true_goal": "...",
  "fatal_flaw": "...",
  "central_conflict": "...",
  "growth_arc": "..."
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["overall_theme", "core_wound", "fatal_flaw"],
        max_retries=4, temperature=0.7, agent_name="ProtagonistCore[早期]",
        empty_ok=True,
    )
    if not data:
        return
    j = state.protagonist_journey
    j.overall_theme = data.get("overall_theme", j.overall_theme)
    j.core_wound = data.get("core_wound", j.core_wound)
    j.true_goal = data.get("true_goal", j.true_goal)
    j.fatal_flaw = data.get("fatal_flaw", j.fatal_flaw)
    j.central_conflict = data.get("central_conflict", j.central_conflict)
    j.growth_arc = data.get("growth_arc", j.growth_arc)

    print("  ✓ 主角内核（前置）：")
    print(f"    主题：{j.overall_theme}")
    print(f"    核心创伤：{j.core_wound[:50]}")
    print(f"    真实目标：{j.true_goal[:50]}")
    print(f"    致命弱点：{j.fatal_flaw[:50]}")
    print(f"    核心矛盾：{j.central_conflict[:60]}")


# ── 共享 helper：主角金手指/能力的具体名字 ─────────────────
# 让下游 prompt 都能用「豆包 / 灵眸」这种作者命名的具体术语，
# 而不是 LLM 脑补的「AI / 系统 / 算法 / 工具」泛词

def _format_protagonist_signature_abilities(state: NovelState) -> str:
    """返回主角金手指/标志能力的描述块——下游 prompt 注入。"""
    if not state.power_system or not state.power_system.special_abilities:
        return ""
    proto_name = next((c.name for c in state.characters if c.role.value == "主角"), None)
    if not proto_name:
        return ""
    abs_lines = []
    names_only = []
    for ab in state.power_system.special_abilities:
        if ab.holder_name == proto_name or ab.is_protagonist_signature:
            abs_lines.append(f"  · 《{ab.name}》（{ab.source}）：{ab.description}")
            names_only.append(ab.name)
    if not abs_lines:
        return ""
    block = "【主角的金手指/标志能力（命名硬约束——文本里出现主角调用能力时必须用这些具体名字）】\n"
    block += "\n".join(abs_lines)
    block += (
        f"\n\n⚠️ 严禁在产出文本里用 'AI / 系统 / 算法 / 数据 / 工具 / 引擎 / 软件' "
        f"这类**泛通用词**指代主角能力——必须用上面的具体名字（{' / '.join(names_only)}）。\n"
        f"举例：错 ✗ '主角让 AI 分析了对手' / 对 ✓ '主角向《{names_only[0]}》问出第一个问题'"
    )
    return block


# ── 步骤2: 卷级里程碑 ────────────────────────────────────

def _step2_volume_milestones(state: NovelState) -> None:
    j = state.protagonist_journey
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"

    volumes_detail = "\n".join(
        f"第{v.index}卷《{v.title}》[{v.chapter_start}-{v.chapter_end}章]\n"
        f"  主题：{v.theme}\n  弧线：{v.arc[:80]}\n"
        f"  对手：{v.volume_antagonist}\n  卷首钩子：{v.opening_hook}\n  卷尾钩子：{v.closing_hook}"
        for v in state.volumes
    )

    key_chars = "\n".join(
        f"- {c.name}【{c.role.value}】登场卷:{c.first_volume}-{c.last_volume} | {c.motivation[:30]}"
        for c in state.characters if c.role.value in ("主角", "主要配角", "反派")
    )

    abilities_block = _format_protagonist_signature_abilities(state)

    prompt = f"""
主角【{prot_name}】整体弧线已确定：
  主题：{j.overall_theme}
  核心创伤：{j.core_wound}
  真实目标：{j.true_goal}
  致命弱点：{j.fatal_flaw}
  核心矛盾：{j.central_conflict}

{abilities_block}

全书各卷详情：
{volumes_detail}

关键人物：
{key_chars}

═══ 卷级里程碑设计要求 ═══
为每卷设计主角的核心里程碑——描述主角在这卷的完整弧线。
重点不是"发生了什么事"，而是"主角内心经历了什么变化"。

每卷必须包含：
1. entry_state：卷首主角的状态（情感/心理/处境，40字）
2. exit_state：卷尾状态（必须与entry_state有真实变化，40字）
3. inner_growth：这一卷主角最重要的内心成长（30字）
4. outer_change：外部世界最重要的不可逆改变（30字）
5. key_relationships：这卷对主角影响最大的1-3段关系（角色名列表）
6. inner_conflict：这一卷主角的核心内心冲突（30字）
7. hardest_choice：主角被迫做出的最艰难选择（40字，必须有代价）
8. darkest_moment：主角最低谷（让读者揪心，40字）
9. triumph_moment：主角最高光（让读者燃起来，40字）

输出JSON：
{{
  "milestones": [
    {{
      "volume": 1,
      "entry_state": "...",
      "exit_state": "...",
      "inner_growth": "...",
      "outer_change": "...",
      "key_relationships": ["角色名1", "角色名2"],
      "inner_conflict": "...",
      "hardest_choice": "...",
      "darkest_moment": "...",
      "triumph_moment": "..."
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["milestones", "items"],
        min_items=1,
        max_retries=4, temperature=0.72, agent_name="ProtagonistJourney[里程碑]",
        empty_ok=True,
    )
    for md in (pick_list(data, "milestones", "items") if data else []):
        m = ProtagonistMilestone(
            volume=md.get("volume", 1),
            entry_state=md.get("entry_state", ""),
            exit_state=md.get("exit_state", ""),
            inner_growth=md.get("inner_growth", ""),
            outer_change=md.get("outer_change", ""),
            key_relationships=md.get("key_relationships", []),
            inner_conflict=md.get("inner_conflict", ""),
            hardest_choice=md.get("hardest_choice", ""),
            darkest_moment=md.get("darkest_moment", ""),
            triumph_moment=md.get("triumph_moment", ""),
        )
        j.milestones.append(m)

    # 兜底：LLM 完全失败时为每卷生成最小骨架——让流程能继续，
    # 同时写 error warning 让用户知道这是降级产物，需要重建
    expected = len(state.volumes)
    if len(j.milestones) < expected:
        existing_vols = {m.volume for m in j.milestones}
        added = 0
        for v in state.volumes:
            if v.index in existing_vols:
                continue
            m = ProtagonistMilestone(
                volume=v.index,
                entry_state=f"卷首：{(v.theme or '')[:30]}",
                exit_state=f"卷尾：{(v.closing_hook or '完成本卷主线')[:30]}",
                inner_growth=(j.growth_arc or "继续成长")[:30],
                outer_change=(v.arc or "推进主线")[:30],
                key_relationships=[],
                inner_conflict=(j.central_conflict or "面对内心矛盾")[:30],
                hardest_choice="(LLM 未产出，请在 web UI 主角历程面板重建)",
                darkest_moment="(LLM 未产出)",
                triumph_moment="(LLM 未产出)",
            )
            j.milestones.append(m)
            added += 1
        # 按卷排序保持有序
        j.milestones.sort(key=lambda m: m.volume)
        if added > 0:
            print(f"  ⚠ {added}/{expected} 卷的里程碑由 LLM 产出（缺失），其余用兜底骨架填充——下游写章质量会受影响")
            try:
                from persistence.checkpoint import add_progress_warning
                add_progress_warning(
                    level="error",
                    source="phase:3G",
                    message=f"主角历程：{added} 卷的里程碑用了兜底骨架（LLM 失败）。"
                            "请在 web UI 主角历程面板手动重建，或重跑 phase 3G",
                )
            except Exception:
                pass

    print(f"  ✓ 卷级里程碑：{len(j.milestones)} 卷")
    for m in j.milestones:
        print(f"    第{m.volume}卷 → 最艰难：{m.hardest_choice[:35]} | 最高光：{m.triumph_moment[:35]}")


# ── 步骤3: 舞台级节拍 ────────────────────────────────────

def _step3_stage_beats(state: NovelState) -> None:
    """为每个叙事舞台设计主角的具体经历节拍。"""
    j = state.protagonist_journey
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"

    if not state.story_stages:
        print("  ⚠ 舞台尚未设计，跳过舞台级节拍（将在卷写作阶段补充）")
        return

    # 分卷处理舞台节拍
    for vol_idx in range(1, NUM_VOLUMES + 1):
        vol_stages = [s for s in state.story_stages if s.volume == vol_idx]
        if not vol_stages:
            continue

        milestone = j.get_milestone(vol_idx)
        if not milestone:
            continue

        stages_desc = "\n".join(
            f"  舞台ID={s.stage_id} 《{s.name}》[{s.chapter_start}-{s.chapter_end}章]\n"
            f"    类型：{s.stage_type} | 氛围：{s.atmosphere}\n"
            f"    主角处境：{s.protagonist_role}\n"
            f"    关键活动：{' / '.join(s.key_activities[:3])}"
            for s in vol_stages
        )

        abilities_block = _format_protagonist_signature_abilities(state)

        prompt = f"""
主角【{prot_name}】第{vol_idx}卷里程碑：
  卷首状态：{milestone.entry_state}
  卷尾状态：{milestone.exit_state}
  内心冲突：{milestone.inner_conflict}
  最艰难选择：{milestone.hardest_choice}
  最低谷：{milestone.darkest_moment}
  最高光：{milestone.triumph_moment}

{abilities_block}

第{vol_idx}卷叙事舞台（{len(vol_stages)}个）：
{stages_desc}

═══ 舞台节拍设计要求 ═══
为每个舞台设计主角在其中的具体经历节拍。
节拍要承接卷级里程碑，说明这个舞台在主角整个卷弧中处于哪个阶段。

milestone_phase可选："起"（开局铺垫）/"承"（发展深化）/"转"（转折危机）/"合"（收束高潮余波）

输出JSON：
{{
  "stage_beats": [
    {{
      "beat_id": "beat_{vol_idx}_1",
      "stage_id": "对应舞台的stage_id",
      "volume": {vol_idx},
      "entry_state": "进入这个舞台时主角的状态（30字）",
      "exit_state": "离开时的状态（30字）",
      "key_actions": ["在这个舞台里的核心行动1（20字）", "行动2", "行动3"],
      "relationship_shifts": ["A与主角的关系在此变化（20字）"],
      "gained": "在这个舞台获得了什么（20字）",
      "lost": "失去或付出了什么代价（20字）",
      "milestone_phase": "起|承|转|合"
    }}
  ]
}}
"""
        data = request_json(
            system=SYSTEM, user=prompt,
            list_candidates=["stage_beats", "beats", "items"],
            min_items=1,
            max_retries=3, temperature=0.70, agent_name=f"ProtagonistJourney[舞台节拍V{vol_idx}]",
            empty_ok=True,
        )

        vol_beats = 0
        for bd in (pick_list(data, "stage_beats", "beats", "items") if data else []):
            beat = ProtagonistStageBeat(
                beat_id=bd.get("beat_id", f"beat_{vol_idx}_{len(j.stage_beats)+1}"),
                stage_id=bd.get("stage_id", ""),
                volume=bd.get("volume", vol_idx),
                entry_state=bd.get("entry_state", ""),
                exit_state=bd.get("exit_state", ""),
                key_actions=bd.get("key_actions", []),
                relationship_shifts=bd.get("relationship_shifts", []),
                gained=bd.get("gained", ""),
                lost=bd.get("lost", ""),
                milestone_phase=bd.get("milestone_phase", "承"),
            )
            j.stage_beats.append(beat)
            vol_beats += 1

        print(f"  ✓ 第{vol_idx}卷舞台节拍：{vol_beats} 个")

    print(f"  ✓ 舞台级节拍总计：{len(j.stage_beats)} 个")


def get_journey_context_for_volume(state: NovelState, volume_index: int) -> str:
    """为卷级写作提供主角历程上下文。"""
    j = state.protagonist_journey
    m = j.get_milestone(volume_index)
    if not m:
        return ""
    lines = [
        f"【主角弧线】主题：{j.overall_theme}",
        f"  核心矛盾：{j.central_conflict}",
        f"",
        f"【第{volume_index}卷里程碑】",
        f"  卷首：{m.entry_state}",
        f"  卷尾：{m.exit_state}",
        f"  内心冲突：{m.inner_conflict}",
        f"  最艰难选择：{m.hardest_choice}",
        f"  最低谷：{m.darkest_moment}",
        f"  最高光：{m.triumph_moment}",
    ]
    return "\n".join(lines)


def get_stage_beat_context(state: NovelState, stage_id: str) -> str:
    """为具体舞台写作提供节拍上下文。"""
    beat = state.protagonist_journey.get_stage_beat(stage_id)
    if not beat:
        return ""
    lines = [
        f"【舞台节拍·{beat.milestone_phase}段】",
        f"  进入状态：{beat.entry_state}",
        f"  离开状态：{beat.exit_state}",
        f"  核心行动：{' / '.join(beat.key_actions[:3])}",
        f"  获得：{beat.gained} | 失去：{beat.lost}",
    ]
    if beat.relationship_shifts:
        lines.append(f"  关系变化：{' | '.join(beat.relationship_shifts[:2])}")
    return "\n".join(lines)
