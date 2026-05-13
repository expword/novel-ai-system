"""
MasterDispatcher —— Phase 0.5：中央调度器。

**职责单一**：读 Phase -1/0 的输出（意图 + 立项三件套），一次 LLM 调用产出
整本书的"骨架蓝图"——从此下游所有设计类 agent 都读这份蓝图做"局部填充"，
而不是各自从零推断。

产出：
  - story_premise：3-5 句讲清整本书
  - central_conflict：核心矛盾一句话
  - thematic_core：主题内核
  - character_slots[]：10-20 个角色槽位（仅轻量占位：role_tag/function/brief_hint）
    下游 CharacterDesigner 按槽位**并发**填充每个角色的完整档案
  - faction_skeleton[]：3-7 层势力骨架（tier_label + tier_function + 风格提示）
    下游 FactionArchitect 按每层**并发**生成具体势力
  - plot_setpieces[]：5-10 个全书关键剧情节点（反转/揭露/牺牲/对决）
    下游 VolumePlanner/Foreshadow 对齐时机使用
  - world_seed：世界观一句话
  - tone_anchors[]：3-5 个文风锚点

设计原则：
  本调用的输出**必须简洁**——每个字段都是"种子"而非"细节"，
  让下游 agent 有分批并发填充的空间。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import (
    NovelState, MasterOutline, CharacterSlot, FactionSkeletonItem, PlotSetpiece,
    NarrativeFunction,
)
from config import (
    NUM_VOLUMES,
    PROTAGONIST_CIRCLE_MIN, PROTAGONIST_CIRCLE_MAX,
    MAJOR_ALLIES_MIN, MAJOR_ALLIES_MAX,
    ANTAGONISTS_MIN, ANTAGONISTS_MAX,
)


SYSTEM = """你是小说项目总架构师（Master Dispatcher）——你不写具体内容，你出"蓝图"。

作者已给你:
  - 创作意图（自然语言）
  - 立项（题材/卖点/读者）
  - 套路库（拥抱/规避/原型/基调）
  - 文风手册（视角/笔触/禁用词）

你要做的事：把这本书的骨架一次规划好。具体包括：
1. story_premise：3-5 句把整本书讲清楚——主角是谁、他要做什么、面对什么阻力、最后怎样
2. central_conflict：主角面对的核心矛盾（一句话）
3. character_slots：10-20 个角色"槽位"——只写 role_tag/function/brief_hint/relationship_hint/arc_hint，**不要写具体名字和外貌**（那是下游 CharacterDesigner 的事）。覆盖：主角 1 / 主要配角 3-5 / 反派层级 3-5 / 关键卷内角色若干
4. faction_skeleton：3-7 层势力骨架——只写 tier_label/tier_function/风格提示，**不要写具体势力名**
5. plot_setpieces：全书 5-10 个关键剧情节点——反转 / 揭露 / 牺牲 / 对决 / 重逢 / 堕落 / 觉醒——每个有锚点（第 N 卷）、类型、一句话描述、涉及哪些槽位 id
6. world_seed：世界观一句话（100-150 字）
7. tone_anchors：3-5 个文风锚点（"多用感官细节替代心理描写"这种具体可操作的指令）

【关键约束】
- 每个字段都要"短而准"——你提供的是种子，下游 agent 会并发展开细节
- character_slots 里写"35 岁女性 CEO，单亲妈妈，表面强硬内心脆弱"——不要写"她叫李梦璃"
- faction_skeleton 里写"第 1 层：主角创业的小型互联网创业圈，扮演舞台入口"——不要写"清风工作室"
- plot_setpieces 涉及的 slot_id 必须引用你上面已定义的 character_slots 的 slot_id
- 所有内容贴合本书题材——别硬塞修真/宗门到都市文，也别给玄幻文写"KPI/季度冲刺"

【单主角铁律】
character_slots 里只有 1 个 role_tag=主角。所有其他槽位的 function 都必须说明
"对主角意味着什么"。

输出严格 JSON。"""


def dispatch_master_outline(state: NovelState) -> None:
    """
    生成 MasterOutline——4 步拆分（先总→并发分）：
      Step A: overview（story_premise/central_conflict/thematic_core/world_seed/tone_anchors）~400 字
      Step B: faction_skeleton + plot_setpieces（~800 字）
      Step C: character_slots（~2000 字，可能仍大）
      Step D（可选）: 若 char slots 不够再补一次

    并发化：B 和 C 都只依赖 A，可以并发。
    这样每次 LLM 调用的输出都控制在 <2500 字以内，大幅减少截断风险。
    """
    from utils.concurrency import parallel_map

    context = _build_context(state)

    # ═══ Step A：先跑总览（premise/conflict/theme/world_seed/tone）═══
    print("  [MasterDispatcher] Step A: 生成整体概念（premise/conflict/theme/world_seed/tone）")
    overview = _dispatch_overview(state, context)
    if not overview or not overview.get("story_premise"):
        print("  [MasterDispatcher] ✗ Step A 总览生成失败——MasterOutline 不可用")
        return

    # ═══ Step B & C 并发：势力骨架 + 情节节点 + 角色槽位 ═══
    print("  [MasterDispatcher] Step B+C 并发：faction_skeleton + plot_setpieces + character_slots")
    augmented_context = context + f"""

═══ 本书已确定的总览（上一步产出）═══
故事前提：{overview.get('story_premise', '')[:200]}
核心矛盾：{overview.get('central_conflict', '')[:100]}
主题内核：{overview.get('thematic_core', '')[:80]}
世界种子：{overview.get('world_seed', '')[:150]}
"""

    def _run_b_factions_plot():
        return _dispatch_factions_and_plot(state, augmented_context)

    def _run_c_characters():
        return _dispatch_character_slots(state, augmented_context)

    results = parallel_map(
        fn=lambda f: f(),
        items=[_run_b_factions_plot, _run_c_characters],
        max_workers=2,
        label="MasterDispatcher-BC",
    )
    factions_plot = results[0] or {}
    characters = results[1] or {}

    # 组合所有结果
    combined = {
        **overview,
        "faction_skeleton": factions_plot.get("faction_skeleton", []),
        "plot_setpieces": factions_plot.get("plot_setpieces", []),
        "character_slots": characters.get("character_slots", []),
    }

    # 报告缺失
    missing = []
    if not combined["faction_skeleton"]:
        missing.append("faction_skeleton（势力骨架）")
    if not combined["plot_setpieces"]:
        missing.append("plot_setpieces（关键节点）")
    if not combined["character_slots"]:
        missing.append("character_slots（角色槽位）")
    if missing:
        print(f"  [MasterDispatcher] ⚠ 未生成的部分：{' / '.join(missing)}——下游 agent 会退化")

    _write_outline_to_state(state, combined)


def _build_context(state) -> str:
    """构造公共上下文（前两 phase 摘要 + 规模）。三个子步共用。"""
    intent = state.creative_intent
    pitch = state.concept_pitch
    lib = state.trope_library
    tm = state.tone_manual

    context_parts = ["═══ 作者意图 + 立项 ═══"]
    if intent.raw_description:
        context_parts.append(f"【原始意图】\n{intent.raw_description[:600]}")
    if intent.tone_summary:
        context_parts.append(f"【整体气质】{intent.tone_summary}")
    if pitch.one_line_pitch:
        context_parts.append(f"【一句话梗概】{pitch.one_line_pitch}")
    if pitch.core_selling_points:
        context_parts.append(f"【核心卖点】{' / '.join(pitch.core_selling_points)}")
    if pitch.target_audience:
        context_parts.append(f"【读者】{pitch.target_audience}｜{pitch.target_platform}｜{pitch.target_age_group}")
    if pitch.benchmark_works:
        context_parts.append(f"【对标】{' vs '.join(pitch.benchmark_works)}")
    if pitch.differentiation:
        context_parts.append(f"【差异化】{pitch.differentiation}")
    if lib.embrace_tropes or lib.avoid_tropes:
        context_parts.append(f"【拥抱套路】{' / '.join(lib.embrace_tropes)}")
        context_parts.append(f"【规避套路】{' / '.join(lib.avoid_tropes)}")
    if lib.protagonist_archetype:
        context_parts.append(f"【主角原型】{lib.protagonist_archetype}")
    if lib.world_tone:
        context_parts.append(f"【世界基调】{lib.world_tone}")
    if lib.villain_policy:
        context_parts.append(f"【反派处理】{lib.villain_policy}")
    if lib.romance_policy:
        context_parts.append(f"【感情线】{lib.romance_policy}")
    if tm.narrative_voice:
        context_parts.append(f"【叙述视角】{tm.narrative_voice}")
    if tm.style_reference:
        context_parts.append(f"【笔触参考】{tm.style_reference}")

    context_parts.append(f"\n本书规模：{NUM_VOLUMES} 卷｜题材：{state.genre}")
    return "\n".join(context_parts)


# ═══════════════════════════════════════════════════════
#  Step A：总览（premise/conflict/theme/world_seed/tone）~300-400 字
# ═══════════════════════════════════════════════════════

def _dispatch_overview(state: NovelState, context: str) -> dict:
    """
    Step A：生成全书整体概念。输出量小（5 字段，总 ~300-400 字），几乎不会被截断。
    产出：story_premise / central_conflict / thematic_core / world_seed / tone_anchors
    """
    prompt = f"""{context}

═══ 任务：生成全书总览（仅 5 个字段，不要超出这些）═══

只做一件事——把这本书的"整体概念"定下来。每个字段都要短而准。
不要写角色名、势力名、具体情节——那是后续步骤的工作。

输出 JSON：
{{
  "story_premise": "3-5 句讲清整本书（主角是谁 / 要做什么 / 阻力 / 走向）",
  "central_conflict": "核心矛盾一句话（主角 vs 谁/什么）",
  "thematic_core": "主题内核（关于什么的故事，30 字）",
  "world_seed": "世界观一句话（100-150 字，贴本书题材）",
  "tone_anchors": ["文风锚点1（具体可操作，如'多用感官细节替代心理描写'）", "锚点2", "锚点3"]
}}
"""
    example = (
        '{"story_premise":"...","central_conflict":"...","thematic_core":"...",'
        '"world_seed":"...","tone_anchors":["..."]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["story_premise", "central_conflict"],
        max_retries=4, temperature=0.72,
        agent_name="MasterDispatcher/Overview",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  Step B：势力骨架 + 关键节点 ~700-1000 字
# ═══════════════════════════════════════════════════════

def _dispatch_factions_and_plot(state: NovelState, context: str) -> dict:
    """
    Step B：只生成势力骨架 + 关键剧情节点。不碰角色槽位。
    输出量约 700-1000 字，远低于合并生成时的 3000+ 字。
    """
    prompt = f"""{context}

═══ 任务：只生成两件东西——势力骨架 + 关键剧情节点 ═══

1. **faction_skeleton**：3-7 层势力骨架
   · 只写 tier / tier_label / tier_function / faction_count_hint / style_hint
   · 不要写具体势力名（那是下游 FactionArchitect 的事）
   · tier_label 贴本书题材，如"主角创业的小型互联网圈"或"{state.genre}的底层小帮派"
   · 每层 faction_count_hint 建议 2-4 个

2. **plot_setpieces**：全书 5-10 个关键节点
   · 类型 kind：反转 / 揭露 / 牺牲 / 对决 / 重逢 / 堕落 / 觉醒 其中一种
   · 锚点 anchor 示例："第1卷末" / "第3卷中段" / "全书收尾"
   · involved_slot_ids 先留空 []（下一步角色槽位生成后再关联）

不要输出 story_premise / character_slots 等其他字段。

输出 JSON：
{{
  "faction_skeleton": [
    {{
      "tier": 1,
      "tier_label": "本层定位（按题材）",
      "tier_function": "本层对主角的功能（舞台入口/第一道门槛/短期目标...）",
      "faction_count_hint": 3,
      "style_hint": "本层势力的风格（20字）"
    }}
  ],
  "plot_setpieces": [
    {{
      "anchor": "第1卷末",
      "kind": "反转|揭露|牺牲|对决|重逢|堕落|觉醒",
      "gist": "一句话概括这个节点（50字）",
      "involved_slot_ids": []
    }}
  ]
}}
"""
    example = (
        '{"faction_skeleton":[{"tier":1,"tier_label":"...","tier_function":"...",'
        '"faction_count_hint":3,"style_hint":"..."}],'
        '"plot_setpieces":[{"anchor":"...","kind":"反转","gist":"...","involved_slot_ids":[]}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["faction_skeleton"],
        max_retries=4, temperature=0.7,
        agent_name="MasterDispatcher/FactionsPlot",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  Step C：角色槽位 ~1500-2000 字
# ═══════════════════════════════════════════════════════

def _dispatch_character_slots(state: NovelState, context: str) -> dict:
    """
    Step C：只生成 15-25 个核心角色槽位。不碰其他字段。
    输出量约 1500-2000 字——每个 slot ~80-100 字 × 20 个。
    """
    prompt = f"""{context}

═══ 任务：只生成 15-25 个核心角色槽位（不输出其他字段）═══

**每个角色槽位同时标两重身份**：
  · role_tag（组织身份）：主角 / 主要配角 / 次要配角 / 反派 / 卷内角色
  · narrative_function（叙事功能）：从下方 12 类选最贴切的一类

**12 类叙事功能（必须覆盖核心 3 类）**：
  核心（必须）：
    · 情感支撑者  —— 给主角情感归属的人（伴侣 / 家人 / 挚友）——填 support_role="伴侣/家人/挚友"
    · 成长引导者  —— 传授知识/技能的人（师父 / 导师 / 前辈）——填 support_role="师父/导师/前辈/上司"
    · 对立冲突者  —— 反派 / 宿敌 / 竞争对手——填 support_role="大反派/阶段反派/竞争对手/宿敌"
  情节推动：
    · 信使引路者  · 考验者  · 搅局者
  关系网络：
    · 盟友伙伴    · 背叛者   · 中立者
  特殊（可选）：
    · 象征性人物  · 叙述观察者  · 镜像角色

**数量要求（按主角/题材动态决定，严格达到）**：
  · 1 个主角（narrative_function="主角本人"）
  · 情感支撑者：按感情线政策决定
      单恋专一 / 无感情线 → 1 个伴侣 + 1-2 个家人/挚友
      双女主/多女主 → 2-3 个伴侣候选 + 1-2 个家人/挚友
      言情类 → 可多到 3-4 个情感支撑者
  · 成长引导者：2-3 个（师父/导师/前辈/上司 视题材定）
      修真/武侠 → 师承明确，建议 2-3
      都市/职场 → 上司/前辈，建议 1-2
  · 对立冲突者（反派）：{ANTAGONISTS_MIN}-{ANTAGONISTS_MAX} 个（大反派 + 阶段反派 + 竞争对手）
  · 盟友伙伴：按 {MAJOR_ALLIES_MIN}-{MAJOR_ALLIES_MAX} 数量范围安排（非情感支撑也非反派的核心配角）
  · 背叛者：至少 1 个（产生情感冲击）
  · 其他（搅局者/信使/考验者/中立者/镜像）：各 0-2 个按故事需要
  · **至少覆盖 8 种不同的 narrative_function**

**不要写具体名字和外貌**——brief_hint 只写"年龄/性别/身份/核心缺陷"这种结构化描述。具体名字是下游 CharacterDesigner 的事。

输出 JSON（只这一个字段）：
{{
  "character_slots": [
    {{
      "slot_id": "mc_01",
      "role_tag": "主角",
      "narrative_function": "主角本人",
      "support_role": "",
      "function": "全书驱动者（30字）",
      "brief_hint": "年龄/性别/社会位置+核心缺陷（40字）",
      "relationship_hint": "—",
      "narrative_arc_hint": "内在弧光方向（20字）",
      "function_detail": "",
      "first_volume": 1,
      "last_volume": -1
    }},
    {{
      "slot_id": "lover_01",
      "role_tag": "主要配角",
      "narrative_function": "情感支撑者",
      "support_role": "伴侣",
      "function": "主角情感归属",
      "brief_hint": "一句话人设（40字）",
      "relationship_hint": "与主角是什么关系",
      "narrative_arc_hint": "弧光方向",
      "function_detail": "最低谷时的唯一港湾；第 3 卷有一次关系危机",
      "first_volume": 1,
      "last_volume": -1
    }},
    {{
      "slot_id": "mentor_01",
      "role_tag": "主要配角",
      "narrative_function": "成长引导者",
      "support_role": "师父",
      "function": "传授 X 技 + 早期引路",
      "brief_hint": "一句话（40字）",
      "relationship_hint": "主角恩师",
      "narrative_arc_hint": "中期离开/死亡",
      "function_detail": "第 3 卷因保护主角牺牲，成为主角黑化/觉醒的关键触发",
      "first_volume": 1,
      "last_volume": 3
    }},
    {{
      "slot_id": "antag_01",
      "role_tag": "反派",
      "narrative_function": "对立冲突者",
      "support_role": "大反派",
      "function": "全书终局压力",
      "brief_hint": "一句话（40字）",
      "relationship_hint": "与主角的暗线关系",
      "narrative_arc_hint": "",
      "function_detail": "不是纯恶，有自洽的理念对立",
      "first_volume": 2,
      "last_volume": -1
    }}
  ]
}}
"""
    example = (
        '{"character_slots":[{"slot_id":"mc_01","role_tag":"主角",'
        '"narrative_function":"主角本人","support_role":"","function":"...",'
        '"brief_hint":"...","relationship_hint":"—","narrative_arc_hint":"...",'
        '"function_detail":"","first_volume":1,"last_volume":-1}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["character_slots"],
        max_retries=4, temperature=0.72,
        agent_name="MasterDispatcher/CharacterSlots",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  写回 state
# ═══════════════════════════════════════════════════════

def _write_outline_to_state(state: NovelState, combined: dict) -> None:
    """把拼好的 dict 写入 state.master_outline，打印摘要。"""
    outline = MasterOutline(
        generated=True,
        story_premise=combined.get("story_premise", ""),
        central_conflict=combined.get("central_conflict", ""),
        thematic_core=combined.get("thematic_core", ""),
        world_seed=combined.get("world_seed", ""),
        tone_anchors=combined.get("tone_anchors", []),
        character_slots=[
            CharacterSlot(
                slot_id=s.get("slot_id", f"slot_{i+1:02d}"),
                role_tag=s.get("role_tag", "次要配角"),
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
            for i, s in enumerate(combined.get("character_slots", []))
            if isinstance(s, dict)
        ],
        faction_skeleton=[
            FactionSkeletonItem(
                tier=int(s.get("tier", i + 1)),
                tier_label=s.get("tier_label", ""),
                tier_function=s.get("tier_function", ""),
                faction_count_hint=int(s.get("faction_count_hint", 3)),
                style_hint=s.get("style_hint", ""),
            )
            for i, s in enumerate(combined.get("faction_skeleton", []))
            if isinstance(s, dict)
        ],
        plot_setpieces=[
            PlotSetpiece(
                anchor=p.get("anchor", ""),
                kind=p.get("kind", ""),
                gist=p.get("gist", ""),
                involved_slot_ids=p.get("involved_slot_ids", []),
            )
            for p in combined.get("plot_setpieces", [])
            if isinstance(p, dict)
        ],
    )
    state.master_outline = outline

    print(f"  ✓ MasterOutline 就绪")
    print(f"    故事：{outline.story_premise[:80]}")
    print(f"    核心矛盾：{outline.central_conflict[:60]}")
    print(f"    角色槽位：{len(outline.character_slots)} 个")
    print(f"    势力骨架：{len(outline.faction_skeleton)} 层")
    print(f"    关键节点：{len(outline.plot_setpieces)} 个")


# ═══════════════════════════════════════════════════════
#  下游 agent 用的格式化辅助
# ═══════════════════════════════════════════════════════

def format_master_brief(state: NovelState, include_slots: bool = False,
                         include_factions: bool = False,
                         include_setpieces: bool = False) -> str:
    """
    供下游 agent 注入 prompt 的 MasterOutline 精简上下文。
    默认只给 story_premise/central_conflict/thematic_core/world_seed/tone_anchors。
    需要 slots/factions/setpieces 的 agent 按需传参。
    """
    mo = state.master_outline
    if not mo.generated:
        return ""
    lines = ["【Master Outline（全书蓝图·下游设计的最高种子）】"]
    if mo.story_premise:
        lines.append(f"故事：{mo.story_premise}")
    if mo.central_conflict:
        lines.append(f"核心矛盾：{mo.central_conflict}")
    if mo.thematic_core:
        lines.append(f"主题：{mo.thematic_core}")
    if mo.world_seed:
        lines.append(f"世界种子：{mo.world_seed[:150]}")
    if mo.tone_anchors:
        lines.append("文风锚点：" + " / ".join(mo.tone_anchors[:5]))
    if include_slots and mo.character_slots:
        lines.append("\n角色槽位（供角色设计引用 slot_id）：")
        for s in mo.character_slots[:20]:
            lines.append(
                f"  · [{s.slot_id}] {s.role_tag}｜{s.brief_hint[:40]}"
                f"（功能：{s.function[:30]}）"
            )
    if include_factions and mo.faction_skeleton:
        lines.append("\n势力骨架：")
        for t in mo.faction_skeleton:
            lines.append(f"  · [第{t.tier}层] {t.tier_label}：{t.tier_function}")
    if include_setpieces and mo.plot_setpieces:
        lines.append("\n关键剧情节点：")
        for p in mo.plot_setpieces[:10]:
            ids = f"[{', '.join(p.involved_slot_ids[:3])}]" if p.involved_slot_ids else ""
            lines.append(f"  · {p.anchor}·{p.kind}：{p.gist} {ids}")
    return "\n".join(lines)
