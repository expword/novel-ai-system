"""
ProtagonistJourneyAgent — 主角舞台级节拍规划。

【精简历史 · 2026-05-25】
原本是三层结构（整体弧线 → 卷级里程碑 → 舞台节拍）。审计后：
  · "整体弧线"（overall_theme/core_wound/true_goal/fatal_flaw/...）下游零消费，删
  · "卷级里程碑"（milestones）下游零消费，删
  · "舞台节拍"（stage_beats）是唯一被 chapter_planner 真正读到的产物，保留

stage_beats 由 _beats_for_volume（core/director.py）在卷级写作时逐卷调用，
不再统一通过 plan_protagonist_journey 入口。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, ProtagonistStageBeat
from config import NUM_VOLUMES


SYSTEM = """你是小说主角弧线设计师，专注于人物成长的内在逻辑。

【单主角铁律】
这是全书唯一的主角——所有配角、反派、机缘、势力、伏笔都是为了给这段弧线制造张力、代价、机缘和蜕变。
设计时不要为了让配角"更出彩"而削弱主角的弧线重量。

你设计的舞台节拍需要：
- 外部行动（推进卷主线）和内部变化（心理/观念）同步推进
- 每个节拍主角的"变化"要真实（不是突然顿悟）
- 节拍承接卷主题与卷弧线，不能脱离卷结构凭空发挥
输出严格JSON。"""


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


# ── 舞台级节拍（唯一保留的产出步骤） ──────────────────────

def _step3_stage_beats(state: NovelState) -> None:
    """为每个叙事舞台设计主角的具体经历节拍——读卷字段（master_outline + volumes）。"""
    j = state.protagonist_journey
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"

    if not state.story_stages:
        print("  ⚠ 舞台尚未设计，跳过舞台级节拍（将在卷写作阶段补充）")
        return

    # master_outline 提供全书故事内核（替代已删的 j.overall_theme 等字段）
    mo = getattr(state, "master_outline", None)
    book_core_block = ""
    if mo and getattr(mo, "thematic_core", ""):
        book_core_block = (
            f"全书主题内核：{mo.thematic_core}\n"
            f"全书核心矛盾：{getattr(mo, 'central_conflict', '') or '(未填)'}"
        )

    # 主角致命弱点（替代已删的 j.fatal_flaw）
    proto_flaw = (protagonist.fatal_flaw if protagonist else "") or "(未填)"

    # 分卷处理舞台节拍
    for vol_idx in range(1, NUM_VOLUMES + 1):
        vol_stages = [s for s in state.story_stages if s.volume == vol_idx]
        if not vol_stages:
            continue

        vol = state.get_volume(vol_idx)
        if not vol:
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
{book_core_block}
主角【{prot_name}】致命弱点：{proto_flaw}

第{vol_idx}卷《{vol.title}》[{vol.chapter_start}-{vol.chapter_end}章]
  卷主题：{vol.theme}
  卷弧线：{vol.arc[:100]}
  对手：{vol.volume_antagonist}
  卷首钩子：{vol.opening_hook}
  卷尾钩子：{vol.closing_hook}
  本卷在全书起承转合中的角色：{vol.structure_role or '(未指定)'}
  本卷想表达：{vol.expression or '(未指定)'}

{abilities_block}

第{vol_idx}卷叙事舞台（{len(vol_stages)}个）：
{stages_desc}

═══ 舞台节拍设计要求 ═══
为每个舞台设计主角在其中的具体经历节拍——承接卷主题与卷弧线，
说明这个舞台在卷内"起/承/转/合"中处于哪个阶段，主角因此获得/失去什么。

milestone_phase 可选："起"（开局铺垫）/"承"（发展深化）/"转"（转折危机）/"合"（收束高潮余波）

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
