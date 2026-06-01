"""
ChapterPlannerAgent — 生成章节场景蓝图（两阶段：规划 + 创意增强）。

核心原则（分形起承转合 + 单主角 + 先答 why/what）：
1. 每一章是分形起承转合的一环——它在所属小情节（SubScene）中扮演"起/承/转/合"之一；
   章自身内部由 3-5 个场景（SceneBeat）分别承担起承转合。
2. 动笔前必须先回答两个问题（purpose / expression）——说不清就不能写。
3. 所有场景都以【唯一主角】为中心；配角出现必须说明"此刻对主角起什么作用"。
4. 以 StoryThread 为主要输入，精确承接上章末尾。
5. 多线交叉编织：并行事件、背景暗流、潜伏线索都是写作素材。
6. 不限制场景类型——关键是每个场景有具体的 Goal→Conflict→Outcome。
7. 两阶段生成：先做骨架规划，再做创意增强（加入意外角度/反差/惊喜）。
8. 新角色出现时，规划如何自然融入后续。
"""
from utils.json_utils import repair_json, request_json
from llm_layer.llm import system_user
from persistence.state import (
    NovelState, ChapterDirective, ChapterBlueprint, SceneBeat,
    TensionLevel, HookType, HookSpec,
)
from agents.thread_tracker import format_thread_for_writer
from agents.protagonist_journey import get_stage_beat_context
from agents.character_web import get_web_context_for_chapter


# ═══════════════════════════════════════════════════════════════════
#  锚点具体度校验 (P0-1) —— 让 LLM 写"具体的话/感官细节",不接受水句
#  通过 request_json 的 custom_validator 路径触发自动重试 (max_retries 配合)
# ═══════════════════════════════════════════════════════════════════

# 整条命中即视为占位/水句
_BEAT_FILLER_LITERALS = {
    "话1", "话2", "话3", "话4", "示范对话", "示范台词", "示范对白",
    "具体台词", "占位",
    "细节1", "细节2", "细节3", "感官细节", "感官细节1",
    "悬念待续", "留下悬念", "且听下回", "未完待续",
    "待定", "todo", "TODO", "TBD", "tbd",
    "可能反转", "情绪变化", "推进剧情",
}


def _safe_int(v, default: int = 0, lo: int = -10, hi: int = 10) -> int:
    """把任意值规范为 [lo, hi] 区间的 int,失败返回 default。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


def _check_emotion_steps(chapter_index: int, beats: list) -> None:
    """P0-5: 情绪压差校验——相邻幕台阶不合理 → progress_warning。

    规则:
    · 连续 3 幕 exit_emotion ≤ -6 = 连续抑郁(读者陪着抑郁→弃书)
    · 连续 3 幕 entry_emotion ≥ +7 = 连续高潮(审美疲劳)
    · 相邻幕情绪台阶 |Δ| > 12 = 暴起暴跌(除非戏剧节拍要求)
    """
    if not beats or len(beats) < 2:
        return
    issues: list[str] = []

    # 连续低谷
    deep_streak = 0
    for b in beats:
        if int(getattr(b, "exit_emotion", 0) or 0) <= -6:
            deep_streak += 1
            if deep_streak >= 3:
                issues.append(f"连续 3+ 幕 exit_emotion ≤ -6 (连续抑郁,读者易弃书)")
                break
        else:
            deep_streak = 0

    # 连续高潮
    high_streak = 0
    for b in beats:
        if int(getattr(b, "entry_emotion", 0) or 0) >= 7:
            high_streak += 1
            if high_streak >= 3:
                issues.append(f"连续 3+ 幕 entry_emotion ≥ +7 (连续高潮,审美疲劳)")
                break
        else:
            high_streak = 0

    # 暴起暴跌
    for i in range(1, len(beats)):
        prev_exit = int(getattr(beats[i - 1], "exit_emotion", 0) or 0)
        this_entry = int(getattr(beats[i], "entry_emotion", 0) or 0)
        delta = abs(this_entry - prev_exit)
        if delta > 12:
            issues.append(
                f"幕{i}↔幕{i+1} 情绪台阶 |Δ|={delta} > 12 "
                f"({prev_exit:+d} → {this_entry:+d},暴起暴跌)"
            )

    if not issues:
        return
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:emotion_steps",
            message=(
                f"第 {chapter_index} 章情绪台阶异常: " + " | ".join(issues[:3])
                + " —— 检查 scene_beats[*].entry_emotion/exit_emotion,"
                "或在 web UI 编辑此章蓝图"
            ),
        )
    except Exception:
        pass


def _is_beat_filler(s: str) -> bool:
    """判定单条锚点是否水句/占位/抽象。"""
    s_clean = (s or "").strip().strip("「」\"'《》()（）")
    if not s_clean:
        return True
    if s_clean in _BEAT_FILLER_LITERALS:
        return True
    # 极短的(< 8 字)且不含具体动词/标点的大概率是水
    if len(s_clean) < 8:
        return True
    return False


def _validate_scene_concreteness(data: dict) -> tuple[bool, str]:
    """校验 scene_beats 锚点具体度,容差 ≤3 处瑕疵。

    检查项:
    · 每幕 content 长度 ≥180 字 (schema 要 200-300)
    · dialogue_seeds 每条 ≥20 字 + 非占位词
    · sensory_anchors 每条 ≥15 字 + 非占位词
    · closing_hook ≥20 字 + 非占位词

    返回 (ok, err_msg)。不通过时 request_json 自动重试。
    """
    if not isinstance(data, dict):
        return True, ""

    beats = data.get("scene_beats") or data.get("beats") or data.get("scenes") or []
    if not isinstance(beats, list) or not beats:
        return True, ""

    issues: list[str] = []
    for i, b in enumerate(beats, 1):
        if not isinstance(b, dict):
            continue

        content = (b.get("content") or "").strip()
        if 0 < len(content) < 180:
            issues.append(f"幕{i}.content 仅{len(content)}字(<180)")

        for j, s in enumerate((b.get("dialogue_seeds") or []), 1):
            if not isinstance(s, str):
                continue
            s_str = s.strip()
            if not s_str:
                continue
            if len(s_str) < 15:
                issues.append(f"幕{i}.dialogue_seed[{j}]仅{len(s_str)}字(<15)")
            elif _is_beat_filler(s_str):
                issues.append(f"幕{i}.dialogue_seed[{j}]是占位:「{s_str[:25]}」")

        for j, s in enumerate((b.get("sensory_anchors") or []), 1):
            if not isinstance(s, str):
                continue
            s_str = s.strip()
            if not s_str:
                continue
            if len(s_str) < 12:
                issues.append(f"幕{i}.sensory[{j}]仅{len(s_str)}字(<12)")
            elif _is_beat_filler(s_str):
                issues.append(f"幕{i}.sensory[{j}]是占位:「{s_str[:25]}」")

    hook = (data.get("closing_hook") or "").strip()
    if hook:
        if len(hook) < 15:
            issues.append(f"closing_hook 仅{len(hook)}字(<15)")
        elif _is_beat_filler(hook):
            issues.append(f"closing_hook 是占位:「{hook[:30]}」")

    # 容差: ≤3 处瑕疵接受 (避免 LLM 反复重试浪费 token)
    if len(issues) <= 3:
        return True, ""
    return False, (
        f"场景锚点具体度不足({len(issues)}处问题): "
        + " | ".join(issues[:6])
        + " —— 请填具体台词/感官细节/具体钩子,不要占位词"
    )





# Tighter default systems. The legacy versions were evocative, but they spent
# too much budget on advice and too little on verifiable output logic.
SYSTEM = """你是章节蓝图设计师。输出必须是严格 JSON；你的目标是给 Writer 一个可执行的场景计划，而不是文学评论。

设计顺序：
1. 先确定本章唯一核心变化 chapter_delta：本章结束时，局势/关系/信息/人物认知具体改变了什么。
2. 再确定 purpose/expression：为什么必须写这一章，读者读完应带走什么感受或悬念。
3. 再拆 scene_beats：常规 2-3 幕，最多 4 幕；每幕必须有 goal、conflict、outcome。
4. 最后补 sensory/dialogue/dramatic anchors：只给可落笔的细节，不写泛泛口号。

硬规则：
- 不凭空新增关键设定、能力、组织、物品、预言或未来事实。
- 不把并行线索单独切成无关场景；能穿插就穿插。
- 每幕必须围绕主角视角或主角处境服务，配角不能自成一章。
- transition_type 必须说明场景如何衔接；hard_cut 全章最多一次。
- content 字段写具体动作、信息交换、阻碍和结果，不能只写“推进剧情/情绪升温”。

输出严格 JSON。"""

ENHANCER_SYSTEM = """你是章节蓝图质检员。只做 1-2 个可执行补丁，不能推翻蓝图。
优先修补：核心变化不清、场景无阻碍、结尾钩子弱、人物选择不成立、能力/设定可能越界。
输出严格 JSON。"""


def build_chapter_blueprint(
    state: NovelState,
    directive: ChapterDirective,
    outline_goal: str,
    total_words: int = 3000,
) -> ChapterBlueprint:
    """两阶段生成：骨架规划 → 创意增强。"""

    # 阶段一：骨架规划
    blueprint = _plan_structure(state, directive, outline_goal, total_words)

    # 阶段二：创意增强（轻量级，不重写，只打补丁）
    _enhance_creatively(state, directive, blueprint, outline_goal)

    return blueprint


# ═══════════════════════════════════════════════════════
#  阶段一：骨架规划
# ═══════════════════════════════════════════════════════

def _plan_structure(
    state: NovelState,
    directive: ChapterDirective,
    outline_goal: str,
    total_words: int,
) -> ChapterBlueprint:
    chapter_index = directive.chapter_index
    thread = state.story_thread

    thread_context = format_thread_for_writer(thread, chapter_index)
    tasks = _build_tasks_summary(state, directive)
    lines_context = _build_lines_phase(state, directive)

    # 主角历程上下文（当前舞台节拍）
    active_stages = state.get_active_stages(chapter_index)
    journey_context = ""
    if active_stages:
        beat_parts = []
        for stage in active_stages[:2]:
            beat_ctx = get_stage_beat_context(state, stage.stage_id)
            if beat_ctx:
                beat_parts.append(f"[{stage.name}]\n{beat_ctx}")
        if beat_parts:
            journey_context = "\n═══ 主角历程节拍（本章在此弧段内）═══\n" + "\n".join(beat_parts)

    # 关系网上下文（本章涉及角色）
    chars_in_scene: list[str] = []
    for ln in state.lines_active_in_chapter(chapter_index)[:3]:
        chars_in_scene.extend(ln.characters)
    # 也加入主角
    prot = next((c for c in state.characters if c.role.value == "主角"), None)
    if prot:
        chars_in_scene.insert(0, prot.name)
    web_context = get_web_context_for_chapter(state, chapter_index, list(dict.fromkeys(chars_in_scene))[:6])
    if web_context:
        web_context = "\n" + web_context

    vol = state.get_volume(directive.volume_index)
    vol_progress = ""
    if vol:
        done = chapter_index - vol.chapter_start + 1
        vol_progress = f"第{done}/{vol.total_chapters}章（{int(done/vol.total_chapters*100)}%）"

    # ── 特殊能力觉醒上下文：本卷活跃能力/本章可能觉醒的阶段
    abilities_hint = ""
    if state.power_system:
        vol_idx = directive.volume_index
        abilities = state.abilities_for_volume(vol_idx)
        if abilities:
            ab_lines = []
            for ab in abilities:
                # 本卷是否有新阶段需要触发
                new_stage = next(
                    (st for st in ab.awakening_stages if st.target_volume == vol_idx),
                    None
                )
                holder = ab.holder_name or ab.holder_role or "未知"
                if new_stage:
                    ab_lines.append(
                        f"· 《{ab.name}》[{holder}] 本卷计划觉醒→{new_stage.stage_name}"
                        f"（触发事件：{new_stage.triggering_event[:40]}）"
                    )
            if ab_lines:
                abilities_hint = "\n═══ 本卷特殊能力觉醒计划（可安排在合适章节触发）═══\n" + "\n".join(ab_lines[:3])

    # ── 分形结构上下文：本章所处的完整起承转合链 ──────────
    structure_chain = state.structure_chain_for_chapter(chapter_index)

    # 本卷/大情节/小情节的 purpose/expression，让 LLM 理解为什么有这一章
    structure_context_parts = []
    if state.book_structure.book_proposition:
        structure_context_parts.append(f"【整本命题】{state.book_structure.book_proposition}")
    if vol:
        vol_role = vol.structure_role or state.book_structure.role_for_volume(vol.index)
        structure_context_parts.append(
            f"【第{vol.index}卷·{vol_role}段】{vol.purpose or vol.theme}"
            f"（表达：{vol.expression[:30] if vol.expression else vol.theme}）"
        )
    stage = state.primary_stage_for_chapter(chapter_index)
    if stage:
        stage_role = f"[{stage.structure_role}]" if stage.structure_role else ""
        structure_context_parts.append(
            f"【大情节·{stage.name}{stage_role}】{stage.purpose or stage.atmosphere}"
            f"（表达：{stage.expression[:30] if stage.expression else stage.atmosphere}）"
        )
    sub = state.primary_sub_scene_for_chapter(chapter_index)
    if sub:
        sub_role = f"[{sub.structure_role}]" if sub.structure_role else ""
        structure_context_parts.append(
            f"【小情节·{sub.name}{sub_role}】{sub.purpose or sub.description}"
            f"（表达：{sub.expression[:25] if sub.expression else ''}）"
        )
    structure_context = "\n".join(structure_context_parts) if structure_context_parts else ""

    # ── 同 stage 章节地图：让本章设计能看清"在大情节内承担哪一段戏" ──
    stage_map_block = ""
    if stage and vol:
        # 取本卷里 stage_id 命中本 stage 的所有 outline；缺 stage_id 的回退按章节范围
        stage_outlines = []
        for o in (vol.chapter_outlines or []):
            sid = o.get("stage_id") or ""
            ci_o = o.get("index")
            if sid == stage.stage_id:
                stage_outlines.append(o)
            elif not sid and isinstance(ci_o, int) and stage.chapter_start <= ci_o <= stage.chapter_end:
                stage_outlines.append(o)
        stage_outlines.sort(key=lambda x: x.get("index", 0))
        if stage_outlines:
            total_in_stage = len(stage_outlines)
            cur_pos = next(
                (i + 1 for i, o in enumerate(stage_outlines) if o.get("index") == chapter_index),
                None,
            )
            pos_str = f"（本章在大情节内位列 {cur_pos}/{total_in_stage}）" if cur_pos else ""
            map_lines = []
            for o in stage_outlines:
                ci_o = o.get("index", "?")
                marker = "▶" if ci_o == chapter_index else " "
                title = (o.get("title") or "").strip()
                goal = (o.get("goal") or "").strip()
                map_lines.append(
                    f"  {marker} 第{ci_o}章《{title}》：{goal[:50]}"
                )
            stage_map_block = (
                f"\n═══ 同大情节《{stage.name}》章节地图{pos_str} ═══\n"
                f"（本 stage 使命：{stage.purpose[:50]} | 表达：{stage.expression[:30]}）\n"
                + "\n".join(map_lines) +
                "\n—— 设计本章蓝图时：承接 ▶ 之前章节留下的钩子/伏笔，给 ▶ 之后章节留下推进的素材。"
                "\n—— 本章的 purpose/expression 必须服务于本 stage 的整体使命，不要独立成章。\n"
            )

    # 待融入的新角色
    emergent_hint = ""
    pending = getattr(thread, '_emergent_pending', [])
    if pending:
        emergent_hint = "\n【待融入的新角色（可寻找时机自然引入）】\n" + "\n".join(
            f"  {e['name']}：{e.get('potential_future_role', '')}（已出现{chapter_index - e.get('first_appeared', chapter_index)}章未再出现）"
            for e in pending[:3]
        )

    # 作者灵感（如果有）——本章主轴，整章场景设计围绕它展开
    inspiration_block = ""
    insp = (getattr(directive, "user_inspiration", "") or "").strip()
    if insp:
        inspiration_block = (
            "\n═══ ★★★ 本章主轴 · 作者灵感（这一章就是为它而设计）★★★ ═══\n"
            f"{insp}\n"
            '—— 本章场景蓝图的【核心目标】是兑现这条灵感，不是把它"也安排进去"。\n'
            "purpose / expression / chapter_delta / scene_beats 都要直接围绕它构建：\n"
            "  · purpose：直接说明本章如何兑现这条灵感\n"
            "  · expression：读者带走的应当就是这条灵感想传达的情绪/领悟\n"
            "  · 场景取舍/数量/节奏/落点：以最大化兑现这条灵感为标准来选\n"
            "下面的分形结构、张力节奏、对齐 hints、reconcile 建议都是辅料——它们辅助你\n"
            "把这条灵感落到合适的结构位上，不是反过来让灵感削足适履。冲突时**以灵感为准**。\n"
        )

    # 作者重写反馈（如果有）——让场景设计真正改变，而不是换个皮继续走老路
    feedback_block = ""
    fb = (getattr(directive, "user_feedback", "") or "").strip()
    if fb:
        feedback_block = (
            "\n═══ ⚠ 重写任务：作者对上一版本的蓝图/情节不满意 ═══\n"
            f"{fb}\n"
            "这是重写——如果只是微调文笔，作者不会再不满意一次。\n"
            "请**重新思考场景设计本身**：该调的节奏/冲突/场景取舍/落点都要调，\n"
            "不要复现上一版本的走向。在 purpose、chapter_delta、scene_beats 里\n"
            "都要体现出对这条反馈的直接回应。\n"
        )

    # ── chapter_dispatcher 路由 —— 按小说子类型/章节位置/功能选 SYSTEM 变体与注入块 ──
    dispatcher_plan = None
    dispatcher_blocks = ""
    dispatcher_hints_block = ""
    try:
        from agents.chapter_dispatcher import dispatch as _dispatch, compose_blocks, get_planner_system
        dispatcher_plan = _dispatch(state, directive)
        dispatcher_blocks = compose_blocks(dispatcher_plan)
        if dispatcher_plan.must_include_hints:
            dispatcher_hints_block = "\n═══ ⚠ 本章额外硬约束（由章节原型路由注入） ═══\n" + "\n".join(
                f"- {h}" for h in dispatcher_plan.must_include_hints
            ) + "\n"
    except Exception as e:
        print(f"  ⚠ planner dispatcher 失败，走默认：{type(e).__name__}: {e}")

    # ── PlanReconciler 注入的"节奏调整"建议 ────────────────
    reconcile_hints_block = ""
    try:
        from agents.plan_reconciler import get_planning_hints
        rec_hints = get_planning_hints(state, chapter_index)
        if rec_hints:
            reconcile_hints_block = "\n═══ 📊 节奏反馈（基于前面章节读者体验累积状态）═══\n" + "\n".join(
                f"- {h}" for h in rec_hints
            ) + "\n"
    except Exception as e:
        print(f"  ⚠ plan_reconciler hints 失败：{type(e).__name__}: {e}")

    # ── LongTermCohesion 提醒（跨卷连贯性）────────────────
    cohesion_hints_block = ""
    try:
        from agents.long_term_cohesion import get_planning_hints as _coh_hints
        coh = _coh_hints(state, chapter_index)
        if coh:
            cohesion_hints_block = "\n═══ 🧭 跨卷连贯性提醒 ═══\n" + "\n".join(
                f"- {h}" for h in coh
            ) + "\n"
    except Exception as e:
        print(f"  ⚠ cohesion hints 失败：{type(e).__name__}: {e}")

    # ── Romance 提醒（感情线戏份）─────────────────────────
    romance_hints_block = ""
    try:
        from agents.romance_arc_planner import get_planning_hints as _rom_hints
        rh = _rom_hints(state, chapter_index)
        if rh:
            romance_hints_block = "\n═══ 💕 感情线提醒 ═══\n" + "\n".join(
                f"- {h}" for h in rh
            ) + "\n"
    except Exception as e:
        print(f"  ⚠ romance hints 失败：{type(e).__name__}: {e}")

    # ── 线 × 舞台 对齐校验（防"在错的舞台推错的线"）────
    align_hints_block = ""
    try:
        from agents.line_stage_alignment import get_planning_hints as _align_hints
        ah = _align_hints(state, chapter_index)
        if ah:
            align_hints_block = "\n═══ 🎯 线-舞台对齐 ═══\n" + "\n".join(
                f"- {h}" for h in ah
            ) + "\n"
    except Exception as e:
        print(f"  ⚠ alignment hints 失败：{type(e).__name__}: {e}")

    # Batch 5:读者预期块——expectation_manager 写章前预测的读者预期
    # chapter_planner LLM 必须对每条标 decision (satisfy/reverse/stack)
    expectations_block = ""
    try:
        from agents.expectation_manager import format_expectations_for_prompt
        _exp_text = format_expectations_for_prompt(directive.reader_expectations or [])
        if _exp_text:
            expectations_block = "\n" + _exp_text + "\n"
    except Exception as _e:
        print(f"  ⚠ expectations block 失败:{type(_e).__name__}: {_e}")

    # Batch 6:调味建议块——flavor_advisor 每 N 章生成一条,本章读最近的
    flavor_block = ""
    try:
        from agents.flavor_advisor import (
            get_latest_advice_for_chapter, format_advice_for_prompt,
        )
        _advice = get_latest_advice_for_chapter(state, chapter_index)
        if _advice:
            flavor_block = "\n" + format_advice_for_prompt(_advice) + "\n"
    except Exception as _e:
        print(f"  ⚠ flavor block 失败:{type(_e).__name__}: {_e}")

    # Batch 6:平台 rulebook 块——立项时加载好的 rulebook
    platform_block = ""
    try:
        from utils.platform_rulebook import format_platform_block
        _pb = format_platform_block(state)
        if _pb:
            platform_block = "\n" + _pb + "\n"
    except Exception:
        pass

    # 钩子类型分布(Batch 3:防本卷连发同类型钩子导致读者审美疲劳)
    recent_hook_types = [
        s.closing_hook_type for s in state.completed_chapters
        if s.volume_index == directive.volume_index and s.closing_hook_type
    ][-5:]
    hook_distribution_hint = ""
    if recent_hook_types:
        from collections import Counter
        _cnt = Counter(recent_hook_types)
        _overused = [(t, n) for t, n in _cnt.items() if n >= 3]
        if _overused:
            _str = " / ".join(f"{t}({n}次)" for t, n in _overused)
            hook_distribution_hint = (
                f"\n  ⚠ 本卷最近 5 章钩子分布: {dict(_cnt)}\n"
                f"  ⚠ 连发钩子类型: {_str}——本章请用其他 hook_type 类型,避免读者审美疲劳"
            )
        else:
            hook_distribution_hint = f"\n  [本卷最近 5 章钩子分布: {dict(_cnt)}]"

    prompt = f"""为第{chapter_index}章设计场景蓝图。
{inspiration_block}{dispatcher_blocks}{dispatcher_hints_block}{reconcile_hints_block}{cohesion_hints_block}{romance_hints_block}{align_hints_block}{expectations_block}{flavor_block}{platform_block}{feedback_block}
═══ 分形结构定位（本章在全书起承转合中的位置链）═══
{structure_chain}

═══ 各层级的 purpose / expression（为什么有这一章）═══
{structure_context or '（无上层上下文，按独立章处理）'}
{stage_map_block}
{abilities_hint}
═══ 实时故事状态（精确承接此处开始）═══
{thread_context}
{emergent_hint}
{journey_context}
{web_context}
═══ 本章写作任务 ═══
大纲目标：{outline_goal}
{_format_chapter_hook(state, directive)}
张力：{directive.tension.value} | 节奏：{directive.rhythm.value}（{directive.word_pace}）
位置：{directive.chapter_position} | {vol_progress}
情绪基调：{directive.emotional_note}
{tasks}

═══ 叙事线当前阶段 ═══
{lines_context}

═══ 设计流程（先想清楚，再动手）═══

先想明白：
- purpose：这一章非写不可的理由（40字，具体可感，别用抽象词）
- expression：读者带走什么（30字，情绪/领悟/悬念）
- structure_role：本章在所属小情节中的位置（起/承/转/合之一，按自然位置判断）

再设计场景（**连贯性优先**——少而长，不是多而碎）：
- opening_state：开篇切入点（直接从上章末尾状态继续，50字）
- chapter_delta：本章结束时故事状态的具体变化（40字，能兑现 purpose 和 expression）
- **场景数量**——目标字数 {total_words} 字，**数量尽量少**（长幕 > 多幕）：
  · ≤ 3000 字 → **2 幕**（首选）或 3 幕
  · ≤ 6000 字 → **2-3 幕**
  · ≤ 12000 字 → 3-4 幕
  · > 12000 字 → 4-6 幕（每幕约 2500-4000 字，不要超过 6 幕）
  · 每幕 word_quota 加起来≈目标字数
- **每幕的 transition_type（非常重要）**：
  · 第 1 幕填 "continuous"（默认就是承接上章）
  · 第 2 幕起：默认 "continuous"（同一时刻延续）或 "soft_cut"（过了一小段时间，仍在同一线）
  · 只有**真正必要**才用 "hard_cut"（切换地点/视角/大跨时间）——全章最多 1 次
  · 同时填 transition_note（20字说明衔接位置，如"他刚走出议事堂"/"三个时辰后，同一密室"）
- 每幕 structure_role（起/承/转/合）
- 每幕以主角为中心；配角出现时在 content 里说明他此刻对主角的作用
- 并行线索尽量穿插在**同一幕内**（如对话中突然收到消息），不要为此单开新幕
- closing_hook_type:从 7 类钩子中选一个(防止本卷连发同类型导致读者审美疲劳)
  · suspense    悬念钩——话音未落/门外传来声音/某角色突然出现
  · reversal    反转钩——全章被压制,末句主角微笑/反派惊愕
  · info_reveal 信息钩——揭露翻盘信息后停笔(身份/秘密/真相)
  · emotional   情感钩——主角决断/感情转折,后果留下章
  · physical    物理钩——看到不该出现的人/物/场景(惊鸿一瞥)
  · death       死亡钩——重要角色突然出事(伤亡/失踪/中毒)
  · cliff       悬崖钩——字面危险情境(被追/中毒/坠落)
  {hook_distribution_hint}
- closing_hook：章末具体画面（50字，下章的起点），必须**落地 closing_hook_type 选的类型**
  ⚠ 如果上面【本章读者钩子】里的 reader_hook 非空——closing_hook 必须**直接落地它**，不许换内核
- 戏剧性：如果需要小反转（一幕走向突然翻面），在对应场景标出；不需要就老实推进
- 细腻线索：每场景可以给 writer 一个细节钩子（一个眼神/小动作/气味/未说出口的话，15 字）
- 总字数约 {total_words} 字

输出JSON：
{{
  "structure_role": "起|承|转|合",
  "purpose": "这一章为什么非写不可（40字，具体可感）",
  "expression": "读者带走什么（30字）",
  "opening_state": "开篇切入（50字）",
  "chapter_delta": "本章结束时的状态变化（40字，具体）",
  "scene_beats": [
    {{
      "scene_index": 1,
      "structure_role": "起|承|转|合",
      "purpose": "本场景的作用（20字）",
      "expression": "本场景传达的核心（15字）",
      "scene_type": "场景类型（自由描述：对峙/审讯/逃跑/密谋/突破/重逢/独白/余韵/...）",
      "location": "地点",
      "characters": ["角色"],
      "goal": "场景里谁想达成什么",
      "conflict": "阻碍",
      "outcome": "结果（达成/失败/意外；可以写反转）",
      "content": "具体发生什么（**200-300 字**，给 writer 足够骨架——关键动作、关键信息交换、阻碍、结果都说清楚；配角出现时说明对主角的作用）",
      "parallel_weave": "可穿插的并行线索（可空）",
      "emotional_shift": "场景后情绪/局势变化",

      "_comment_anchors": "下面 3 个锚点列表是本幕写出好文的关键——writer 会被要求融入其中大部分。每条 20-35 字，具体可感。",

      "dialogue_seeds": [
        "3-5 条示范对白/关键台词锚点。每条 20-50 字，格式：「角色名（状态）：具体台词或话的核心」",
        "例：师父（压低声，指尖轻叩）：此剑一出，便再无回头",
        "例：主角（站起来转身）：你可知我在等什么？",
        "例：（沉默片刻）—— 三人谁都没先开口"
      ],
      "sensory_anchors": [
        "5-8 个感官细节候选，每条 20-35 字，覆盖视/听/嗅/触/内感",
        "例：门缝漏出半寸烛光，带着松烟的焦味",
        "例：她指节发白，却始终没收回那张薄纸",
        "例：远处有更夫敲锣，一下、两下——第三下之前他开了口"
      ],
      "dramatic_beats": [
        "0-3 个戏剧节拍标记，15-25 字，用于场景内的反转/顿挫/突变",
        "例：他抬手按住她的肩——但手停在半寸外",
        "例：玉佩突然烫手，刚才还冰凉"
      ],
      "paragraph_mix": {
        "dialogue": 40, "action": 30, "inner": 20, "desc": 10,
        "_comment": "本幕段落比例(总和必须=100)。按场景类型给:对峙幕高 dialogue(50+),战斗幕高 action(50+),独白幕高 inner(40+),环境/铺垫幕 desc 提高。writer 按此分配段落。"
      },
      "emotional_residue_from_prev": "本幕开场要承接上一幕末的情绪余波(30-50 字具体身体/感官/思绪表现)。例:「他手指仍在微微发抖,刚才那一刀的反震还停留在手腕」。**第1幕填空串**(它没有上一幕)。",
      "entry_emotion": 0,
      "exit_emotion": 0,
      "_emotion_comment": "本幕入场/末尾主角情绪值(-10 深渊 → 0 平静 → +10 极致高潮)。chapter_planner 必填,防连续抑郁或连续高潮。",

      "sensory_hook": "【已废弃字段，保留兼容性，写空串即可】",
      "transition_type": "continuous|soft_cut|hard_cut（第1幕必须 continuous；其余默认 continuous，只在真必要时才用 hard_cut）",
      "transition_note": "与上一幕的衔接方式（20字，如'他刚走出议事堂'/'三个时辰后'）",
      "word_quota": 字数
    }}
  ],
  "closing_hook": "章末画面（50字）",
  "closing_hook_secondary": "章末二钩——情感回响层(30-50 字)。主钩(closing_hook)是悬念/反转/物理钩;二钩是情感回响。两钩必须**不同类型**。例: 主钩='反派一句话颠覆主角认知',二钩='主角看着月亮想起母亲那句话'。无法配二钩时填空串。",
  "closing_hook_type": "suspense|reversal|info_reveal|emotional|physical|death|cliff",
  "reader_expectation_decisions": [
    "satisfy|reverse|stack（对每条 reader_expectations 按顺序给一个决策;无 expectations 时填空数组）"
  ],
  "pacing_note": "整章节奏特点"
}}
"""
    # SYSTEM 按 dispatcher 的 planner_variant 选择
    active_system = SYSTEM
    if dispatcher_plan and dispatcher_plan.planner_variant != "default":
        try:
            from agents.chapter_dispatcher import get_planner_system as _gps
            sys_variant = _gps(dispatcher_plan.planner_variant, genre=state.genre)
            if sys_variant:
                active_system = sys_variant
        except Exception:
            pass

    data = request_json(
        system=active_system, user=prompt,
        list_candidates=["scene_beats", "beats", "scenes"],
        min_items=2,
        max_retries=4, temperature=0.65, agent_name=f"ChapterPlanner[Ch{chapter_index}]",
        empty_ok=True,
        custom_validator=_validate_scene_concreteness,  # 锚点具体度校验,水句重生
    )
    if not data:
        data = {}

    beats = []
    for b in data.get("scene_beats", []):
        gco = f"[目标:{b.get('goal','')} | 冲突:{b.get('conflict','')} | 结果:{b.get('outcome','')}]"
        pw = f"\n  (穿插：{b['parallel_weave']})" if b.get("parallel_weave") else ""
        sh = f"\n  (细节钩子：{b['sensory_hook']})" if b.get("sensory_hook") else ""
        # 第1幕始终连续；其他默认 continuous，LLM 填了 hard_cut 就尊重
        scene_idx_val = b.get("scene_index", len(beats) + 1)
        raw_trans = (b.get("transition_type") or "continuous").strip()
        if raw_trans not in ("continuous", "soft_cut", "hard_cut"):
            raw_trans = "continuous"
        if scene_idx_val == 1:
            raw_trans = "continuous"

        # P4：从 LLM 取出对白/感官/戏剧锚点列表（过滤空值 + 长度裁切）
        def _clean_list(key: str, min_n: int, max_n: int, max_len: int) -> list[str]:
            raw = b.get(key) or []
            if not isinstance(raw, list):
                return []
            out: list[str] = []
            for item in raw:
                if not isinstance(item, str):
                    continue
                s = item.strip()
                if not s:
                    continue
                # 过滤掉 prompt 示例里的说明文字（以"例："开头或全是 markdown）
                if s.startswith("例：") or s.startswith("例:"):
                    continue
                out.append(s[:max_len])
                if len(out) >= max_n:
                    break
            return out

        dialogue_seeds = _clean_list("dialogue_seeds", 0, 6, 120)
        sensory_anchors = _clean_list("sensory_anchors", 0, 10, 80)
        dramatic_beats = _clean_list("dramatic_beats", 0, 4, 60)

        # P1-3: paragraph_mix 解析(总和归一化到 100,缺失字段补 0)
        raw_mix = b.get("paragraph_mix") or {}
        paragraph_mix = {}
        if isinstance(raw_mix, dict):
            cleaned = {}
            for k in ("dialogue", "action", "inner", "desc"):
                v = raw_mix.get(k, 0)
                try:
                    cleaned[k] = max(0, int(v))
                except Exception:
                    cleaned[k] = 0
            total = sum(cleaned.values())
            if total > 0:
                # 归一化到总和 100 (避免 LLM 给的总和略偏)
                scale = 100.0 / total
                paragraph_mix = {k: round(v * scale) for k, v in cleaned.items()}
                # 修正舍入误差让总和精确 100
                diff = 100 - sum(paragraph_mix.values())
                if diff != 0:
                    # 把误差加到最大项
                    max_key = max(paragraph_mix, key=lambda kk: paragraph_mix[kk])
                    paragraph_mix[max_key] += diff

        # 兼容老的 sensory_hook——升级为 sensory_anchors[0]
        if not sensory_anchors and b.get("sensory_hook"):
            sh_v = str(b.get("sensory_hook"))[:80].strip()
            if sh_v:
                sensory_anchors.append(sh_v)

        beats.append(SceneBeat(
            scene_index=scene_idx_val,
            scene_type=b.get("scene_type", "推进"),
            location=b.get("location", ""),
            characters=b.get("characters", []),
            content=f"{gco} {b.get('content', '')}{pw}",
            emotional_shift=b.get("emotional_shift", ""),
            word_quota=b.get("word_quota", total_words // max(len(data.get("scene_beats", [1])), 1)),
            structure_role=b.get("structure_role", ""),
            purpose=b.get("purpose", ""),
            expression=b.get("expression", ""),
            transition_type=raw_trans,
            transition_note=b.get("transition_note", ""),
            dialogue_seeds=dialogue_seeds,
            sensory_anchors=sensory_anchors,
            dramatic_beats=dramatic_beats,
            paragraph_mix=paragraph_mix,
            emotional_residue_from_prev=str(b.get("emotional_residue_from_prev") or "").strip()[:120],
            entry_emotion=_safe_int(b.get("entry_emotion"), 0, -10, 10),
            exit_emotion=_safe_int(b.get("exit_emotion"), 0, -10, 10),
        ))
    # 硬切限额：全章最多 1 次，超出降为 soft_cut
    hard_cut_count = 0
    for b in beats:
        if b.transition_type == "hard_cut":
            if hard_cut_count >= 1:
                b.transition_type = "soft_cut"
            hard_cut_count += 1

    if not beats:
        beats = _fallback_beats(directive, thread, total_words)

    # 章节级分形字段：如果 LLM 没给，降级——从小情节/舞台推导一个兜底
    ch_structure_role = data.get("structure_role", "")
    ch_purpose = data.get("purpose", "")
    ch_expression = data.get("expression", "")

    # closing_hook_type 解析为 HookSpec(LLM 给的字符串可能不规范——容错)
    _ht_str = (data.get("closing_hook_type") or "").strip().lower()
    _hook_spec: HookSpec | None = None
    if _ht_str:
        try:
            _hook_spec = HookSpec(
                type=HookType(_ht_str),
                text=str(data.get("closing_hook", ""))[:50],
            )
        except ValueError:
            # LLM 给的 hook type 不在枚举里——降级到 None,critic 会扣 hook_type_compliance
            print(f"  ⚠ chapter_planner 第 {chapter_index} 章 closing_hook_type={_ht_str!r} 不在 HookType 枚举")

    # Batch 5: reader_expectations 的 decision 回写
    _decisions = data.get("reader_expectation_decisions") or []
    if isinstance(_decisions, list) and directive.reader_expectations:
        _valid = {"satisfy", "reverse", "stack"}
        for i, exp in enumerate(directive.reader_expectations):
            if i < len(_decisions):
                _d = str(_decisions[i] or "").strip().lower()
                exp.decision = _d if _d in _valid else ""

    bp = ChapterBlueprint(
        chapter_index=chapter_index,
        opening_state=data.get("opening_state", thread.scene_end_state[:50] if thread.scene_end_state else "故事继续"),
        chapter_delta=data.get("chapter_delta", outline_goal[:40]),
        scene_beats=beats,
        closing_hook=data.get("closing_hook", "悬念待续"),
        closing_hook_secondary=str(data.get("closing_hook_secondary") or "").strip()[:120],
        pacing_note=data.get("pacing_note", directive.word_pace),
        structure_role=ch_structure_role,
        purpose=ch_purpose,
        expression=ch_expression,
        closing_hook_spec=_hook_spec,
    )

    # P0-5: 情绪压差校验——防"连续抑郁"/"连续高潮"——只 warn 不 reject
    _check_emotion_steps(chapter_index, bp.scene_beats)

    # 回写到 ChapterDirective（供 writer/critic 使用）
    directive.structure_role = ch_structure_role or directive.structure_role
    directive.purpose = ch_purpose or directive.purpose
    directive.expression = ch_expression or directive.expression
    # 更新结构链，带上章节自身的角色
    directive.structure_chain = state.structure_chain_for_chapter(
        chapter_index, chapter_role=directive.structure_role
    )

    return bp


# ═══════════════════════════════════════════════════════
#  阶段二：创意增强（打补丁，不颠覆）
# ═══════════════════════════════════════════════════════

def _enhance_creatively(
    state: NovelState,
    directive: ChapterDirective,
    blueprint: ChapterBlueprint,
    outline_goal: str,
) -> None:
    """
    轻量级创意审视：找出1-2个"可以更好"的地方，直接修改 blueprint。
    不重写，只打补丁——添加意外角度、反差、情感层次、意外来客等。
    """
    thread = state.story_thread
    beats_desc = "\n".join(
        f"场景{b.scene_index}·{b.scene_type}（{b.location}）：{b.content[:60]}"
        for b in blueprint.scene_beats
    )

    # 检查是否有待融入的新角色
    pending_chars = getattr(thread, '_emergent_pending', [])
    char_hint = ""
    if pending_chars:
        char_hint = f"新角色待融入：{', '.join(e['name'] for e in pending_chars[:2])}"

    prompt = f"""审视以下第{blueprint.chapter_index}章蓝图，提出1-2个创意改进建议。

【当前蓝图】
开篇：{blueprint.opening_state}
核心变化：{blueprint.chapter_delta}
{beats_desc}
结尾钩子：{blueprint.closing_hook}

【背景信息】
张力：{directive.tension.value} | 大纲目标：{outline_goal[:40]}
{char_hint}
并行事件：{' / '.join(thread.parallel_events[:2]) if thread.parallel_events else '无'}

【审视角度】
- 是否有场景过于"按部就班"，可以加入意外/反转/反差？
- 是否有某个场景可以引入新角色或让久未出现的角色现身？
- 结尾钩子是否足够有力？是否能制造更强的"必须看下一章"冲动？
- 是否遗漏了某个本该在此章有进展的开放循环？
- 是否有机会加入一个读者意想不到但合理的细节？

输出JSON（只给可落地的修改，不要大改方向）：
{{
  "improvements": [
    {{
      "target_scene": 场景序号（1-5）或0表示整体,
      "type": "意外转折|引入角色|加强钩子|穿插线索|情感层次|细节点睛",
      "suggestion": "具体怎么改（50字内，可操作）"
    }}
  ],
  "enhanced_closing_hook": "改进后的结尾钩子（若无需改动则与原文一致）"
}}
"""
    try:
        data = request_json(
            system=ENHANCER_SYSTEM, user=prompt,
            max_retries=2, temperature=0.7,
            agent_name=f"ChapterEnhancer[Ch{blueprint.chapter_index}]",
            empty_ok=True,
        )
        if not data:
            return

        improvements = data.get("improvements", [])
        if improvements:
            # 将改进建议追加到对应场景的 content 中（作为写作提示）
            for imp in improvements[:2]:
                scene_idx = imp.get("target_scene", 0)
                suggestion = imp.get("suggestion", "")
                imp_type = imp.get("type", "改进")
                if not suggestion:
                    continue
                if scene_idx == 0:
                    # 整体改进——追加到 pacing_note
                    blueprint.pacing_note += f" | 创意补丁[{imp_type}]：{suggestion}"
                else:
                    for beat in blueprint.scene_beats:
                        if beat.scene_index == scene_idx:
                            beat.content += f"\n  ⭐[{imp_type}]：{suggestion}"
                            break

        enhanced_hook = data.get("enhanced_closing_hook", "")
        if enhanced_hook and enhanced_hook != blueprint.closing_hook:
            blueprint.closing_hook = enhanced_hook

    except Exception:
        pass  # 创意增强失败不影响主流程


# ── 内部辅助 ──────────────────────────────────────────

def _format_chapter_hook(state: NovelState, directive: ChapterDirective) -> str:
    """从 volume.chapter_outlines 拉本章的 chapter_focus + reader_hook，
    格式化成 chapter_planner prompt 的硬约束段。
    """
    vol = None
    for v in state.volumes:
        if v.chapter_start <= directive.chapter_index <= v.chapter_end:
            vol = v
            break
    if not vol:
        return ""
    outline = None
    for o in (vol.chapter_outlines or []):
        if o.get("index") == directive.chapter_index:
            outline = o
            break
    if not outline:
        return ""
    focus = (outline.get("chapter_focus") or "").strip()
    hook = (outline.get("reader_hook") or "").strip()
    if not focus and not hook:
        return ""
    lines = ["【本章读者钩子（卷规划阶段已审过——这一章你的设计必须命中它们）】"]
    if focus:
        lines.append(f"  · 本章一件最重要的事：{focus}")
    if hook:
        lines.append(f"  · 让读者翻下一页的钩子：{hook}")
        lines.append("    ⚠ 你设计的 closing_hook 必须直接落地这条 reader_hook——画面/对话/悬念都行，但不许偷换内核")
    return "\n".join(lines)


def _build_tasks_summary(state: NovelState, directive: ChapterDirective) -> str:
    parts = []
    if directive.must_include:
        parts.append("必须事件：" + " / ".join(directive.must_include[:3]))
    for sp_id in directive.satisfaction_points:
        sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
        if sp:
            parts.append(f"[爽点触发] {sp.title}：{sp.payoff_description[:40]}")
    if directive.foreshadow_plant:
        parts.append(f"植入伏笔×{len(directive.foreshadow_plant)}")
    for fw_id in directive.foreshadow_resolve:
        fw = state.get_foreshadow(fw_id)
        if fw:
            parts.append(f"[兑现伏笔] {fw.resolution_description[:30]}")
    return "\n".join(parts) if parts else "无强制任务"


def _build_lines_phase(state: NovelState, directive: ChapterDirective) -> str:
    parts = []
    for lid in directive.active_lines[:3]:
        line = state.get_line(lid)
        if not line:
            continue
        phase = line.get_phase_for_chapter(directive.chapter_index)
        if phase:
            tag = "★" if lid == directive.primary_line else "·"
            parts.append(f"{tag}{line.name}[{phase.name}/{phase.tension.value}]：{phase.description[:50]}")
    return "\n".join(parts) if parts else "自然推进"


def _fallback_beats(directive: ChapterDirective, thread, total_words: int) -> list[SceneBeat]:
    tension = directive.tension.value
    opening = thread.scene_end_state[:30] if thread.scene_end_state else "承接前情"
    if tension == "高潮":
        items = [("对峙", opening, 600), ("激战", "正面冲突爆发", 1200),
                 ("转折", "意外介入打破局面", 600), ("余波", "新危机浮现", 600)]
    elif tension in ("平静", "下落"):
        items = [("喘息", opening, 700), ("暗流", "表面平静下的算计", 900),
                 ("伏笔", "某个细节引发主角注意", 700), ("钩子", "平静中出现异变", 600)]
    else:
        items = [("承接", opening, 500), ("推进", "矛盾推进", 900),
                 ("反应", "角色抉择", 700), ("转折", "出现新情况", 900)]
    return [
        SceneBeat(scene_index=i+1, scene_type=t[0], location="", characters=[],
                  content=t[1], emotional_shift="", word_quota=t[2])
        for i, t in enumerate(items)
    ]

