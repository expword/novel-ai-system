"""
ConceptPitchAgent — Phase 0：创作立项层。

在所有其他 agent 动工之前，必须先明确三件事：
1. ConceptPitch（卖点定位）——这本书卖给谁、凭什么卖
2. TropeLibrary（套路库）——用哪些梗、避哪些梗、爽点偏好
3. ToneManual（文风手册）——视角/笔触/禁用词/对话风格

三件套决定所有下游 agent 的取向。
三步按顺序调用，后一步基于前一步的产出——pitch 决定读者群，读者群决定套路偏好，
套路偏好影响文风选择。

如果用户在 config.py 里给了种子（TARGET_AUDIENCE/CORE_SELLING_POINTS_SEEDS 等），
LLM 必须严格遵守这些偏好；否则让 LLM 根据题材/主题自行推断。
"""
from json_utils import request_json, pick_list
from state import NovelState, ConceptPitch, TropeLibrary, ToneManual
from config import (
    NUM_VOLUMES, WORDS_PER_CHAPTER, CHAPTERS_PER_VOLUME_MIN, CHAPTERS_PER_VOLUME_MAX,
    TARGET_AUDIENCE, TARGET_PLATFORM, CORE_SELLING_POINTS_SEEDS,
    EMBRACE_TROPES_SEEDS, AVOID_TROPES_SEEDS, VILLAIN_POLICY_SEED,
    NARRATIVE_VOICE_SEED, STYLE_REFERENCE_SEED,
)
from agents.intent_analyzer import format_intent_as_constraints


SYSTEM_PITCH = """你是资深网络小说策划编辑。你的工作是——在作者动笔之前——为一本小说做"立项"：
这本书一句话讲什么？卖什么？给谁看？和什么对标？差异化在哪？预期规模多大？
立项不准确，后续所有设计都会跑偏。你给的每一项都要具体、有平台感、能落地。
输出严格 JSON。"""


SYSTEM_TROPE = """你是网络小说套路分析师，对各平台/频道的读者口味了如指掌。
给定一本书的立项信息，你要决定：
- 哪些经典套路必须拥抱（读者就爱这口）
- 哪些烂梗必须规避（看了就弃书）
- 爽点类型的偏好排序（这本书的爽是哪种爽）
- 反派处理原则、感情线原则、主角原型
不同频道答案不同——晋江读者讨厌的，起点读者可能正爱；轻小说圈接受的，传统出版接受不了。
输出严格 JSON。"""


SYSTEM_TONE = """你是小说文风顾问。
根据立项信息和套路偏好，你要定出这本书的笔触——视角、句法节奏、对话风格、感官侧重、禁用词。
文风要和内容气质一致：热血爽文用不得诗意小句式，古典仙侠用不得网络梗。
禁用词要具体（直接列出单词），不要抽象指令。
输出严格 JSON。"""


# ═══════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════

def design_concept_phase(state: NovelState) -> None:
    """Phase 0 主入口：依次做 pitch → tropes → tone。"""
    _design_concept_pitch(state)
    _design_trope_library(state)
    _design_tone_manual(state)


# ═══════════════════════════════════════════════════════
#  Step 1：卖点定位
# ═══════════════════════════════════════════════════════

def _design_concept_pitch(state: NovelState) -> None:
    seeds_block = _build_seeds_block()
    intent_block = format_intent_as_constraints(state.creative_intent)
    expected_total_words = NUM_VOLUMES * ((CHAPTERS_PER_VOLUME_MIN + CHAPTERS_PER_VOLUME_MAX) // 2) * WORDS_PER_CHAPTER

    # 如果有创作意图，把它作为最高优先级的"硬约束"放在 prompt 最前面
    # 这样 LLM 不会被 state.title/genre/theme 里的默认值（玄幻/苍穹问道）带跑
    intent_header = ""
    ci = state.creative_intent
    if ci.analyzed and ci.raw_description:
        intent_header = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★★★ 作者原始意图（最高优先级——立项必须完全贴合以下内容）★★★
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{ci.raw_description[:800]}

【自动分析的关键信号（硬约束）】
- 推断题材：{ci.suggested_genre or '—'}
- 推断主题：{ci.suggested_theme or '—'}
- 整体气质：{ci.tone_summary or '—'}
- 目标读者：{ci.audience_hint or '—'}｜{ci.platform_hint or '—'}

【命名硬约束 ‼️】
作者原话里的具体名词（金手指/能力/系统/人物原型/物件 等的具体称呼）**必须原封不动地沿用**。
立项三件套（pitch/selling_points/differentiation 等）里**只能用作者原话里出现过的术语**，不要自己加修饰词。

**严禁**把作者的原创术语包装成下面这类**泛通用词或泛通用词组合**：
  · AI / 算法 / 数据 / 引擎 / 工具 / 系统（除非作者原话就用了"系统"）
  · 大数据搜索引擎 / 智能助手 / 信息处理装置 / 知识图谱 之类
  · 任何"<泛词>+作者原词"或"作者原词+<泛词>"的拼接（这等于偷换概念）

通用判断规则：
  · 如果作者原话里写了 X（不管 X 是什么），产出里就只能用 X，不要自创"AI X"/"X 助手"/"X 系统"。
  · 作者原话里没出现过的概念词，不要加。
错例 ✗ 作者写"主角带 X 穿越"，你写"主角带 AI X 当金手指" / "主角带 X 引擎 穿越"
对例 ✓ 作者写"主角带 X 穿越"，你写"主角带 X 穿越"（一字不改地用 X）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    prompt = f"""{intent_header}为以下小说做立项——如果上面有作者原始意图，立项必须严格贴合那段原话，
下面的【书名/题材/主题】只是系统元数据，**作者意图和它冲突时以作者意图为准**。

【书名】《{state.title}》
【题材】{state.genre}
【主题】{state.theme}
【预期规模】{NUM_VOLUMES} 卷，约 {expected_total_words // 10000} 万字
{intent_block}
{seeds_block}

═══ 要求 ═══
1. one_line_pitch：一句话讲清这本书卖什么（30字内，要有平台感，不要文学评论腔）
2. core_selling_points：3-5 个核心卖点（读者一看就想点开的那种）
3. target_audience：男频|女频|混合（如果 seeds 指定了，严格遵守）
4. target_age_group：主要读者年龄段
5. target_platform：最匹配的平台（起点/晋江/番茄/书旗/QQ阅读/飞卢/掌阅等）
6. reader_profile：读者画像（60字，具体到"什么样的人在什么心境下翻开这本书"）
7. benchmark_works：2-3 本对标作品（必须是真实存在、读者耳熟能详的）
8. differentiation：与对标作品的差异化点（60字，讲清"读者为什么选我不选他们"）
9. expected_volumes / expected_total_words / expected_completion_weeks：预期规模

输出 JSON：
{{
  "one_line_pitch": "...",
  "core_selling_points": ["卖点1", "卖点2", "卖点3"],
  "target_audience": "男频|女频|混合",
  "target_age_group": "...",
  "target_platform": "...",
  "reader_profile": "...",
  "benchmark_works": ["作品1", "作品2"],
  "differentiation": "...",
  "expected_total_words": 数字,
  "expected_volumes": {NUM_VOLUMES},
  "expected_completion_weeks": 数字
}}
"""
    data = request_json(
        system=SYSTEM_PITCH, user=prompt,
        required_keys=["one_line_pitch", "target_audience"],
        max_retries=5, temperature=0.7,
        agent_name="ConceptPitch",
    )

    # 创作意图 > LLM 输出 > config seeds  的 fallback 链
    intent = state.creative_intent
    pitch = ConceptPitch(
        one_line_pitch=data.get("one_line_pitch", ""),
        core_selling_points=(data.get("core_selling_points") or intent.selling_points_hints
                              or CORE_SELLING_POINTS_SEEDS),
        target_audience=(intent.audience_hint or data.get("target_audience", "") or TARGET_AUDIENCE),
        target_age_group=(intent.age_group_hint or data.get("target_age_group", "")),
        target_platform=(intent.platform_hint or data.get("target_platform", "") or TARGET_PLATFORM),
        reader_profile=data.get("reader_profile", ""),
        benchmark_works=(data.get("benchmark_works") or intent.benchmark_hints),
        differentiation=(data.get("differentiation", "") or intent.differentiation_hint),
        expected_total_words=int(data.get("expected_total_words", expected_total_words)),
        expected_volumes=int(data.get("expected_volumes", NUM_VOLUMES)),
        expected_completion_weeks=int(data.get("expected_completion_weeks", 0)),
    )
    state.concept_pitch = pitch

    print(f"  ✓ 立项：{pitch.one_line_pitch}")
    print(f"    读者：{pitch.target_audience}｜{pitch.target_platform}｜{pitch.target_age_group}")
    print(f"    卖点：{' / '.join(pitch.core_selling_points[:3])}")
    print(f"    对标：{' vs '.join(pitch.benchmark_works[:2])}")
    print(f"    差异：{pitch.differentiation[:60]}")


# ═══════════════════════════════════════════════════════
#  Step 2：套路库
# ═══════════════════════════════════════════════════════

def _design_trope_library(state: NovelState) -> None:
    pitch = state.concept_pitch
    seeds_block = _build_trope_seeds_block()
    intent_block = format_intent_as_constraints(state.creative_intent)

    prompt = f"""根据立项，决定这本书的套路偏好。
{intent_block}

【立项要点】
  一句话：{pitch.one_line_pitch}
  卖点：{' / '.join(pitch.core_selling_points)}
  读者：{pitch.target_audience}｜{pitch.target_platform}｜{pitch.target_age_group}
  读者画像：{pitch.reader_profile}
  对标：{' / '.join(pitch.benchmark_works)}
  差异化：{pitch.differentiation}
{seeds_block}

═══ 要求 ═══
重要：不同频道不同答案。男频爱"扮猪吃虎+打脸"，女频爱"误会+救赎+宠溺"；
起点要"爽感快"，晋江要"感情深"；番茄/书旗要下沉感，飞卢要梗密度。
根据上面的读者画像精准匹配。

1. embrace_tropes：要用的经典套路（5-8个，具体名字，如"打脸""扮猪吃虎""反差萌"）
2. avoid_tropes：要规避的烂梗（3-6个，具体名字，如"师门叛徒""女主圣母""扭捏作态"）
3. preferred_sp_types：爽点偏好排序（从最强到次强，2-4条，如["权力爽","打脸爽","升级爽"]）
4. villain_policy：反派处理原则（"洗白型"|"彻底黑化型"|"灰色模糊型"|"人格魅力型"）
5. romance_policy：感情线处理（"甜宠"|"虐恋"|"轻感情"|"发糖+撒糖"|"无感情线"）
6. harem_policy：后宫原则（"单恋专一"|"双女主"|"多女主"|"不涉及"）
7. protagonist_archetype：主角原型（"逆袭型"|"天才型"|"苟道型"|"腹黑型"|"热血型"|"成熟型"|"萝莉化"）
8. world_tone：世界基调（"热血"|"沉郁"|"轻松"|"黑暗"|"治愈"|"古典"）

输出 JSON（完整字段）：
{{
  "embrace_tropes": [...],
  "avoid_tropes": [...],
  "preferred_sp_types": [...],
  "villain_policy": "...",
  "romance_policy": "...",
  "harem_policy": "...",
  "protagonist_archetype": "...",
  "world_tone": "..."
}}
"""
    data = request_json(
        system=SYSTEM_TROPE, user=prompt,
        required_keys=["embrace_tropes", "preferred_sp_types", "villain_policy"],
        max_retries=5, temperature=0.7,
        agent_name="TropeLibrary",
    )

    intent = state.creative_intent
    lib = TropeLibrary(
        embrace_tropes=(data.get("embrace_tropes") or intent.embrace_tropes_hints or EMBRACE_TROPES_SEEDS),
        avoid_tropes=(data.get("avoid_tropes") or intent.avoid_tropes_hints or AVOID_TROPES_SEEDS),
        preferred_sp_types=(data.get("preferred_sp_types") or intent.preferred_sp_types_hints),
        villain_policy=(intent.villain_policy_hint or data.get("villain_policy", "") or VILLAIN_POLICY_SEED),
        romance_policy=(intent.romance_policy_hint or data.get("romance_policy", "")),
        harem_policy=(intent.harem_policy_hint or data.get("harem_policy", "")),
        protagonist_archetype=(intent.protagonist_archetype_hint or data.get("protagonist_archetype", "")),
        world_tone=(intent.world_tone_hint or data.get("world_tone", "")),
    )
    state.trope_library = lib

    print(f"  ✓ 套路库：")
    print(f"    拥抱：{' / '.join(lib.embrace_tropes[:5])}")
    print(f"    规避：{' / '.join(lib.avoid_tropes[:4])}")
    print(f"    爽点偏好：{' > '.join(lib.preferred_sp_types)}")
    print(f"    反派：{lib.villain_policy}｜感情：{lib.romance_policy}｜原型：{lib.protagonist_archetype}")
    print(f"    基调：{lib.world_tone}")


# ═══════════════════════════════════════════════════════
#  Step 3：文风手册
# ═══════════════════════════════════════════════════════

def _design_tone_manual(state: NovelState) -> None:
    pitch = state.concept_pitch
    lib = state.trope_library
    intent = state.creative_intent
    intent_block = format_intent_as_constraints(intent)
    seeds_block = ""
    extra_seeds = []
    if NARRATIVE_VOICE_SEED:
        extra_seeds.append(f"narrative_voice 必须使用：{NARRATIVE_VOICE_SEED}")
    if STYLE_REFERENCE_SEED:
        extra_seeds.append(f"style_reference 必须参考：{STYLE_REFERENCE_SEED}")
    if extra_seeds:
        seeds_block = "\n【用户种子偏好（必须遵守）】\n" + "\n".join(f"  - {s}" for s in extra_seeds)

    prompt = f"""根据立项和套路偏好，定这本书的文风。
{intent_block}

【立项要点】
  一句话：{pitch.one_line_pitch}
  读者：{pitch.target_audience}｜{pitch.target_platform}
  对标：{' / '.join(pitch.benchmark_works)}

【套路偏好】
  主角原型：{lib.protagonist_archetype}
  世界基调：{lib.world_tone}
  爽点偏好：{' / '.join(lib.preferred_sp_types)}
{seeds_block}

═══ 要求 ═══
1. narrative_voice：叙述视角（第一人称/第三人称限知/上帝视角/多视角切换）——要和世界基调匹配
2. style_reference：笔触参考（具体到某位作家或某种风格，如"烽火戏诸侯的诗意 + 天蚕土豆的热血"）
3. prose_rhythm：句法节奏（"长短句交织"|"短句密集"|"骈散结合"|"长句缠绕"）
4. dialogue_style：对话风格（"古风"|"现代"|"半文半白"|"诗化"|"口语化"）
5. sensory_weight：感官侧重（挑1-2个最突出的：视觉/听觉/触觉/嗅觉/味觉）
6. banned_words：禁用词清单（**具体单词**，5-10个，如"仿佛""似乎""突然""然而""只见""竟然"——这些是网文常见的弱化词或啰嗦词）
7. careful_words：慎用词清单（具体单词，3-6个，如"笑了笑""点了点头""叹了口气"这种偷懒描写）
8. metaphor_preference：比喻/意象偏好（如"自然物为主，避免现代工业词汇""侧重战斗的力学比喻"）
9. opening_habit：开场习惯（如"从一个具体动作或声音切入""不用环境描写开头"）

输出 JSON：
{{
  "narrative_voice": "...",
  "style_reference": "...",
  "prose_rhythm": "...",
  "dialogue_style": "...",
  "sensory_weight": "...",
  "banned_words": ["词1", "词2", ...],
  "careful_words": ["词1", "词2", ...],
  "metaphor_preference": "...",
  "opening_habit": "..."
}}
"""
    data = request_json(
        system=SYSTEM_TONE, user=prompt,
        required_keys=["narrative_voice", "style_reference"],
        max_retries=5, temperature=0.6,
        agent_name="ToneManual",
    )

    tm = ToneManual(
        narrative_voice=(intent.narrative_voice_hint or data.get("narrative_voice", "") or NARRATIVE_VOICE_SEED),
        style_reference=(intent.style_reference_hint or data.get("style_reference", "") or STYLE_REFERENCE_SEED),
        prose_rhythm=data.get("prose_rhythm", ""),
        dialogue_style=(intent.dialogue_style_hint or data.get("dialogue_style", "")),
        sensory_weight=data.get("sensory_weight", ""),
        banned_words=data.get("banned_words", []),
        careful_words=data.get("careful_words", []),
        metaphor_preference=data.get("metaphor_preference", ""),
        opening_habit=data.get("opening_habit", ""),
    )
    state.tone_manual = tm

    print(f"  ✓ 文风手册：")
    print(f"    视角：{tm.narrative_voice}｜笔触：{tm.style_reference}")
    print(f"    节奏：{tm.prose_rhythm}｜对话：{tm.dialogue_style}｜感官：{tm.sensory_weight}")
    print(f"    禁用词：{' / '.join(tm.banned_words[:6])}")
    print(f"    慎用词：{' / '.join(tm.careful_words[:4])}")


# ═══════════════════════════════════════════════════════
#  下游 agent 可用的快速上下文构建函数
# ═══════════════════════════════════════════════════════

def format_concept_brief(state: NovelState) -> str:
    """
    供下游 agent 注入到 prompt 的精简立项上下文（约 200-300 字）。
    包含卖点、读者、套路偏好核心项。
    如果有 creative_intent.tone_summary，放在最前面作为"整体气质"。
    """
    pitch = state.concept_pitch
    lib = state.trope_library
    intent = state.creative_intent
    if not pitch.one_line_pitch and not lib.embrace_tropes and not intent.tone_summary:
        return ""  # 立项未完成，不注入
    lines = ["【立项坐标（创作取向基准）】"]
    if intent.tone_summary:
        lines.append(f"★整体气质：{intent.tone_summary}")
    if pitch.one_line_pitch:
        lines.append(f"一句话：{pitch.one_line_pitch}")
    if pitch.target_audience or pitch.target_platform:
        lines.append(f"读者：{pitch.target_audience}｜{pitch.target_platform}")
    if pitch.core_selling_points:
        lines.append(f"卖点：{' / '.join(pitch.core_selling_points[:4])}")
    if lib.embrace_tropes:
        lines.append(f"拥抱套路：{' / '.join(lib.embrace_tropes[:5])}")
    if lib.avoid_tropes:
        lines.append(f"规避烂梗：{' / '.join(lib.avoid_tropes[:4])}")
    if lib.preferred_sp_types:
        lines.append(f"爽点偏好：{' > '.join(lib.preferred_sp_types)}")
    if lib.villain_policy:
        lines.append(f"反派处理：{lib.villain_policy}")
    if lib.romance_policy:
        lines.append(f"感情线：{lib.romance_policy}")
    if lib.world_tone:
        lines.append(f"世界基调：{lib.world_tone}")
    return "\n".join(lines)


def format_world_context_brief(state: NovelState) -> str:
    """
    供所有设计类 agent（faction/geography/economy/world/character ...）注入 prompt 顶部。
    让下游 agent 知道："这不是修真文，别硬塞境界/宗门/御剑/灵石。"

    返回一段结构化上下文：
      - 题材/流派/体系
      - 是否有修炼者分化
      - 是否需要超自然元素
      - 世界基调
    agent 根据这些自适应生成内容。
    """
    genre = state.genre or ""
    ps = state.power_system
    pitch = state.concept_pitch
    intent = state.creative_intent
    lib = state.trope_library

    lines = ["【本书题材/流派坐标（设计时必须贴合）】"]
    # 如果有意图原话，先把最关键的一段贴在顶部，下游设计不会偏离作者想要的方向
    if intent.analyzed and intent.raw_description:
        lines.append(f"★作者意图（最高优先级，设计必须贴合）：{intent.raw_description[:200]}")
    lines.append(f"题材：{genre}｜主题：{state.theme[:60]}")
    if pitch.one_line_pitch:
        lines.append(f"一句话：{pitch.one_line_pitch}")
    if intent.tone_summary:
        lines.append(f"整体气质：{intent.tone_summary[:80]}")
    if lib.world_tone:
        lines.append(f"世界基调：{lib.world_tone}")

    if ps:
        lines.append(f"体系类型：{ps.system_type}｜体系性质：{ps.system_nature or '—'}")
        if ps.power_flow:
            lines.append(f"流派：{ps.power_flow}")
        if ps.special_mechanics:
            mech = " / ".join(m.name for m in ps.special_mechanics[:4])
            lines.append(f"特殊机制：{mech}")
        # 关键提示：世界需不需要修炼者身份 / 超自然元素
        is_supernatural = ps.system_type in ("realms", "skill_tiers") or "修" in ps.system_nature or "异能" in ps.system_nature or "超能" in ps.power_flow or "魔法" in ps.power_flow
        if is_supernatural:
            lines.append("→ 世界存在超自然元素（修炼者/异能者/魔法等），设计时可用这类身份/阶层/力量")
        else:
            lines.append("→ 世界**不**存在修炼者/异能者/法器等超自然元素，设计时用普通人现实身份（学生/上班族/官员/商人/军人等）")

    return "\n".join(lines)


def format_tone_brief(state: NovelState) -> str:
    """供 writer/critic 注入的文风手册上下文。"""
    tm = state.tone_manual
    if not tm.narrative_voice and not tm.banned_words:
        return ""
    lines = ["【文风手册】"]
    if tm.narrative_voice:
        lines.append(f"视角：{tm.narrative_voice}")
    if tm.style_reference:
        lines.append(f"笔触参考：{tm.style_reference}")
    if tm.prose_rhythm:
        lines.append(f"句法节奏：{tm.prose_rhythm}")
    if tm.dialogue_style:
        lines.append(f"对话风格：{tm.dialogue_style}")
    if tm.sensory_weight:
        lines.append(f"感官侧重：{tm.sensory_weight}")
    if tm.metaphor_preference:
        lines.append(f"比喻偏好：{tm.metaphor_preference}")
    if tm.opening_habit:
        lines.append(f"开场习惯：{tm.opening_habit}")
    if tm.banned_words:
        lines.append(f"★ 禁用词（不可出现）：{' / '.join(tm.banned_words)}")
    if tm.careful_words:
        lines.append(f"  慎用词（别滥用）：{' / '.join(tm.careful_words)}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════

def _build_seeds_block() -> str:
    seeds = []
    if TARGET_AUDIENCE:
        seeds.append(f"target_audience 必须填：{TARGET_AUDIENCE}")
    if TARGET_PLATFORM:
        seeds.append(f"target_platform 必须填：{TARGET_PLATFORM}")
    if CORE_SELLING_POINTS_SEEDS:
        seeds.append(f"core_selling_points 必须至少包含：{CORE_SELLING_POINTS_SEEDS}")
    if not seeds:
        return ""
    return "\n【用户种子偏好（必须遵守）】\n" + "\n".join(f"  - {s}" for s in seeds)


def _build_trope_seeds_block() -> str:
    seeds = []
    if EMBRACE_TROPES_SEEDS:
        seeds.append(f"embrace_tropes 必须至少包含：{EMBRACE_TROPES_SEEDS}")
    if AVOID_TROPES_SEEDS:
        seeds.append(f"avoid_tropes 必须至少包含：{AVOID_TROPES_SEEDS}")
    if VILLAIN_POLICY_SEED:
        seeds.append(f"villain_policy 必须填：{VILLAIN_POLICY_SEED}")
    if not seeds:
        return ""
    return "\n【用户种子偏好（必须遵守）】\n" + "\n".join(f"  - {s}" for s in seeds)
