"""
WriterAgent — 写章节正文，感知张力/节奏/爽点/伏笔/级别/势力全套系统（按本书题材自适应）。
"""
from llm import system_user
from state import NovelState, ChapterDirective, TensionLevel, RhythmType
from context_manager import build_writer_context, ContextBuilder, CRITICAL, HIGH, MEDIUM
from agents.rhythm_designer import get_rhythm_instruction
from agents.concept_pitch import format_tone_brief, format_concept_brief


SYSTEM_TEMPLATE = """你是一位才华横溢的{genre}小说作家，用极其细腻的笔触写让读者舍不得翻页、每一段都要细细品味的故事。

核心铁律：

一、【绝不赶场】节奏要慢。哪怕只是一个眼神、一次推门、一次端杯，都可以铺陈一整段。读者爱读不是为了"接下来发生什么"，是为了"在这一刻感到什么"。每一幕都要让画面在读者眼前停留足够久。

二、【细腻为王】不写抽象情绪词（"紧张/难过/愤怒"），写让读者自己识别情绪的具体身体反应、空间细节、半句未说完的话。捕捉"眼神停留时长 / 指尖温度 / 极细的气味 / 袖口的褶 / 一个被打断的动作"这种粒度。**每章必须找新的具象切入点——避免反复使用同一种身体反应（比如总写指节、总写茶杯）或同一类比喻本体**。如果上章用过"像 X 一样"，本章就换"如 Y 般"换 Y；如果上章靠"指节"传递紧张，本章就靠"喉结上下动一次"或"耳廓发烫"。

三、【铺陈比推进优先】进入高潮前一定要有足够的铺垫——环境、气味、人物内心的细小波动、对话里欲言又止的停顿。一个高潮需要至少三段铺垫托着；一个情感峰值需要前面所有的细节共同塑造。没有铺垫的爽和泪都是假的。

四、【人物戏份要够厚】主角每个重要动作前都有一段内心活动（至少 3-4 句），每个关键对话后都有一段情绪余波（至少 2-3 句）。配角的每次开口都要带出他自己的声音（口癖、节奏、用词），而不是推剧情的工具声。

五、【对话要带血肉】不要"他说…她说…"的乒乓球对话。每一轮对话之间要有动作、表情、沉默、环境的打断；每一句话都要有弦外之音；该停顿的地方用"——""……"让读者自己感受到呼吸。

六、【内心独白必须真实多层】人很少只有一个念头。主角想一件事时，常常是两三个声音在辩驳。这种自我对话能让角色立体。

七、【起承转合只是骨架】你现在写的这一章在哪一段（起/承/转/合）决定文字的气质倾向，但不要被标签束缚——该慢就慢，该静就静，该让读者心疼就让他心疼。

八、【字数必须写足】每个场景要写够指定的 word_quota——不是凑字数，而是让每一幕都有充分的空间呼吸。宁可多写感官细节、内心独白、环境铺陈，也不要草率收场。常规一章 2000-3000 字，给读者一段完整的沉浸体验。

九、【配角服务主角】整本书围绕唯一主角——配角戏份要服务于主角的感受/抉择/变化，而不是自成一体。

十、【反复多样性】同一类描写手法/比喻本体/过渡词在相邻 5 章内尽量不重复。如果【本章禁止出现】中列出"近 N 章已用过的笔触"，必须主动换花样——这才是高质量长篇该有的笔触新鲜度。

十一、【真·AI 占位符——总原则】
如果【本章能力使用规划】里某条能力标了"🔌 真 AI 接入"（具体占位格式见该处指令），主角"问它"时**必须用那条占位符**——绝对不要自己脑补 AI 的回答内容，回答会由真实大模型生成并替换。一章里最多 2-3 次占位符，写在主角铺陈式触发动作之后、配以反应描写。如果【本章能力使用规划】里没有"🔌 真 AI 接入"标记，本条占位符语法**一律不得使用**。

直接输出正文，以"第X章 标题"开头，不要加任何说明或元评论。"""


# 单次 LLM 调用写不完的阈值——超过就按场景分批
SCENE_SPLIT_THRESHOLD = 6000
# 字数兜底：初稿低于 target * MIN_FILL_RATIO 就触发扩写
MIN_FILL_RATIO = 0.85
# 每场景单次 LLM 输出的 max_tokens（中文 ≈ 1.5 token/字，留缓冲）
SCENE_MAX_TOKENS = 8000


def write_chapter(state: NovelState, directive: ChapterDirective, target_words: int,
                  prev_tail: str = "") -> str:
    """
    写一章。策略：
    - 目标字数 <= 阈值 或 没有场景蓝图 → 单次 LLM 调用
    - 否则 → 按场景分批，每场景一次 LLM，拼接后再检查是否达标
    - 不达标 → 触发扩写 pass
    - 如果 directive.user_feedback 非空（来自重写），会贯穿到所有 LLM 调用
    """
    # chapter_dispatcher 路由一次，挂在 directive 上，后续所有 prompt 构造共用
    try:
        from agents.chapter_dispatcher import dispatch as _dispatch
        plan = _dispatch(state, directive)
        directive._prompt_plan = plan
        if plan.archetype != "default":
            print(f"  [dispatcher] 章节原型={plan.archetype} "
                  f"writer={plan.writer_variant} blocks={plan.context_block_ids}")
    except Exception as e:
        print(f"  ⚠ dispatcher 失败，走默认路径：{type(e).__name__}: {e}")
        directive._prompt_plan = None

    bp = directive.blueprint
    if target_words <= SCENE_SPLIT_THRESHOLD or not bp or len(bp.scene_beats) < 2:
        draft = _write_single_shot(state, directive, target_words, prev_tail)
    else:
        draft = _write_by_scenes(state, directive, target_words, prev_tail)

    # 字数兜底：不够就扩写——按【中文小说字数】判定（汉字+英文word+数字），不是 len(字符数)
    from state import count_chapter_words as _cw
    min_needed = int(target_words * MIN_FILL_RATIO)
    cur_wc = _cw(draft)
    attempts = 0
    while cur_wc < min_needed and attempts < 3:
        attempts += 1
        print(f"  ⚠ 初稿 {cur_wc} 字 < 目标 {target_words} 字的 {int(MIN_FILL_RATIO*100)}%（={min_needed}）——第 {attempts} 次扩写")
        new_draft = _expand_to_target(state, directive, draft, target_words)
        new_wc = _cw(new_draft)
        if new_wc <= cur_wc:
            # 扩写后没增长——再 retry 也无意义，跳出
            print(f"  ⚠ 扩写后字数未增长（{cur_wc} → {new_wc}），停止扩写")
            break
        draft = new_draft
        cur_wc = new_wc
        print(f"  ✓ 扩写后 {cur_wc} 字")
    return draft


def _get_plan_temperature(directive: ChapterDirective, default: float = 0.88) -> float:
    """读 dispatcher plan 的温度，没有就用默认值。"""
    plan = getattr(directive, "_prompt_plan", None)
    if plan and plan.writer_temperature is not None:
        return plan.writer_temperature
    return default


def _get_plan_blocks_text(directive: ChapterDirective) -> str:
    """把 dispatcher plan 选中的 context blocks 拼成一段文本，塞到 prompt 顶部。"""
    plan = getattr(directive, "_prompt_plan", None)
    if not plan or not plan.context_block_ids:
        return ""
    try:
        from agents.chapter_dispatcher import compose_blocks
        return compose_blocks(plan)
    except Exception:
        return ""


def _write_single_shot(state: NovelState, directive: ChapterDirective,
                        target_words: int, prev_tail: str = "") -> str:
    """单次 LLM 调用写完整章——只在短章或无蓝图时用。"""
    prompt, system = _build_full_prompt(state, directive, target_words, prev_tail)
    return system_user(system, prompt,
                        temperature=_get_plan_temperature(directive),
                        max_tokens=min(SCENE_MAX_TOKENS, 4096))


def _write_by_scenes(state: NovelState, directive: ChapterDirective,
                      target_words: int, prev_tail: str = "") -> str:
    """
    按场景分批写——每场景一次 LLM 调用。
    让 writer 能真正吐出 20K 字/章，而不是被 4K token 限制卡死。
    """
    bp = directive.blueprint
    beats = list(bp.scene_beats)
    _rebalance_scene_quotas(beats, target_words)

    print(f"  按 {len(beats)} 个场景分批生成（各幕目标 {[b.word_quota for b in beats]} 字）")

    system = _load_writer_system(state, directive)
    pieces: list[str] = []
    scene_prev_tail = prev_tail

    for i, beat in enumerate(beats):
        is_first = (i == 0)
        is_last = (i == len(beats) - 1)
        scene_text = _write_one_scene(
            state, directive, beat, system, scene_prev_tail,
            is_first=is_first, is_last=is_last,
            scene_num=i + 1, total_scenes=len(beats),
        )
        if scene_text:
            pieces.append(scene_text.strip())
            scene_prev_tail = scene_text[-400:]
            print(f"    场景 {i+1}/{len(beats)}: {len(scene_text)} 字")
        else:
            print(f"    ⚠ 场景 {i+1} 写作失败，跳过")

    return "\n\n".join(pieces)


def _rebalance_scene_quotas(beats, target_words: int):
    """让各场景 word_quota 加起来约等于 target_words。"""
    total = sum(b.word_quota for b in beats)
    n = len(beats)
    if total <= 0:
        per = target_words // max(n, 1)
        for b in beats:
            b.word_quota = per
        return
    if abs(total - target_words) / max(target_words, 1) < 0.1:
        return
    ratio = target_words / total
    for b in beats:
        b.word_quota = max(800, int(b.word_quota * ratio))


def _write_one_scene(state, directive, beat, system, prev_tail,
                      is_first: bool, is_last: bool,
                      scene_num: int, total_scenes: int) -> str:
    """为单个场景跑一次 LLM 调用——让 writer 全神贯注在这一幕上。"""
    tone_block = format_tone_brief(state)
    concept_block = format_concept_brief(state)
    structure_section = _format_structure(state, directive)
    character_state_block = _format_character_states(directive)
    forbidden_block = _format_forbidden(directive)
    rhythm_instruction = get_rhythm_instruction(state, directive.chapter_index)
    context = build_writer_context(state, directive)
    ability_plan_block = _format_ability_plan(directive)
    external_ai_block = _format_external_ai_constraint(state)

    role_tag = f"[{beat.structure_role}]" if beat.structure_role else ""
    chars_str = "、".join(beat.characters) if beat.characters else "按需"
    purpose_line = f"本幕作用：{beat.purpose}" if beat.purpose else ""
    expression_line = f"本幕情绪/表达：{beat.expression}" if beat.expression else ""

    # 本幕出场角色的 voice card（让对话一落笔就像对的人）
    voice_cards_block = _format_voice_cards_for_scene(state, beat.characters or [])

    # 本幕对白/感官/戏剧节拍锚点（P4：让 writer 不再"自由编剧"）
    anchors_block = _format_beat_anchors(beat)

    # 本幕氛围库片段（P6：让世界活起来）
    atmosphere_block = ""
    try:
        from agents.customs_designer import format_atmosphere_for_scene
        # 推断本幕涉及的势力（如果蓝图里没有，就空）
        scene_factions = []
        atmosphere_block = format_atmosphere_for_scene(
            state,
            volume_index=directive.volume_index,
            location_name=beat.location or "",
            factions=scene_factions,
            max_fragments=6, max_customs=3,
        )
    except Exception as _e:
        atmosphere_block = ""

    if is_first:
        # 标题：优先用 outline 给的（volume_planner 已统一去重设计），缺失才让 writer 自拟
        planned_title = ""
        try:
            outlines = getattr(state, "_chapter_outlines_cache", None) or []
            for v in state.volumes:
                for o in (v.chapter_outlines or []):
                    if o.get("index") == directive.chapter_index:
                        planned_title = (o.get("title") or "").strip()
                        break
                if planned_title: break
        except Exception:
            planned_title = ""
        if planned_title:
            opening_instr = (
                f"请以\"第{directive.chapter_index}章 {planned_title}\"作为开头（标题已由大纲规划——不得擅自改动，"
                f"标题已统一去重设计；如果你强烈认为标题不贴章意可在正文末尾另起一行加 [建议改标题: <新标题>]，但本章正文必须用此标题）。"
                f"开篇直接承接上一章末尾的具体画面/动作——不要另起炉灶，不要概括过渡；从上章最后那个镜头继续。"
            )
        else:
            opening_instr = (
                f"请以\"第{directive.chapter_index}章 标题\"作为开头（自拟标题——但**必须避开【本章禁止出现的内容】里列出的近 N 章已用过的标题指纹**：换不同前缀、不同句式骨架）。"
                f"开篇直接承接上一章末尾的具体画面/动作——不要另起炉灶，不要概括过渡；从上章最后那个镜头继续。"
            )
    else:
        # 按 transition_type 决定衔接风格
        trans = getattr(beat, "transition_type", "continuous") or "continuous"
        trans_note = getattr(beat, "transition_note", "") or ""
        note_hint = f"衔接提示：{trans_note}" if trans_note else ""
        if trans == "hard_cut":
            opening_instr = (
                "本幕是硬切——切换时空/视角（全章只允许一次）。用一行空行或一个明确的时空标记起幕"
                "（如「三日后，京都。」）。" + note_hint
            )
        elif trans == "soft_cut":
            opening_instr = (
                "本幕是软切——同一线索延续，但过了一段时间/换了个小位置。"
                "不要另起炉灶，直接用一个短句过渡（如「半个时辰后，他仍坐在石阶上。」），"
                "保持主视角不变。" + note_hint
            )
        else:  # continuous
            opening_instr = (
                "本幕是无缝延续——和上一幕是同一时刻、同一地点、同一视角的后续。"
                "绝对不要换场景，也不要跳时间；从上一幕末尾那个具体动作/画面/对白直接往下写。"
                "不要出现「稍后」「此时」「过了一会」这种跳跃措辞，让读者感觉根本没断过。"
                + note_hint
            )

    if is_last:
        ending_instr = f"本幕是全章最后一幕——必须有结尾钩子：{_get_hook_instruction(directive)}"
    else:
        ending_instr = "本幕不是最后一幕——结尾留出自然的衔接点（未说完的话/未完成的动作/情绪未散），下一幕会继续。"

    prev_tail_block = ""
    if prev_tail:
        label = "上一章末尾原文" if is_first else "上一幕末尾"
        prev_tail_block = f"\n═══ {label}（必须无缝衔接） ═══\n{prev_tail}\n"

    target_qw = beat.word_quota
    min_qw = int(target_qw * 0.85)

    inspiration_block = _format_user_inspiration(directive)
    volume_stage_map_block = _format_volume_stage_map(state, directive)
    feedback_block = _format_user_feedback(directive)
    plan_blocks = _get_plan_blocks_text(directive)
    prompt = f"""写第{directive.chapter_index}章·第{scene_num}/{total_scenes}幕{role_tag}。

{inspiration_block}
{volume_stage_map_block}
{plan_blocks}
{feedback_block}
{tone_block}

{concept_block}

═══ 本章在全书中的位置 ═══
{structure_section}

═══ 本章气质 ═══
张力：{directive.tension.value}（{directive.emotional_note}）
节奏：{rhythm_instruction}

{character_state_block}

{ability_plan_block}

{external_ai_block}

{forbidden_block}

═══ 本幕蓝图 ═══
场景类型：{beat.scene_type}
地点：{beat.location or '按剧情定'}
出场角色：{chars_str}
{purpose_line}
{expression_line}
内容骨架：{beat.content}
场景结束后情绪/局势变化：{beat.emotional_shift}

{anchors_block}

{atmosphere_block}

{voice_cards_block}
{prev_tail_block}
═══ 上下文 ═══
{context}

═══ 本幕写作参数 ═══
★ 本幕目标字数：{target_qw} 字（硬下限 {min_qw} 字——不够就多写内心/感官/铺陈/对话余韵，不要草草收场）
{opening_instr}
{ending_instr}

写作纪律：
- 慢下来。这一幕要让读者感受到"在这一刻发生了什么"，不是"接下来会怎样"。
- 每个关键动作前 3-4 句内心活动，每句对话后 2-3 句情绪余波或动作反应。
- 不要抽象词，全用具体的感官细节/微表情/未说完的话。
- 禁用词清单里的词绝对不能出现。
- 配角出场都要带自己的声音（口癖/节奏），服务主角，不抢戏。

现在，专注地写好这一幕。"""
    try:
        return system_user(system, prompt,
                            temperature=_get_plan_temperature(directive),
                            max_tokens=SCENE_MAX_TOKENS)
    except Exception as e:
        print(f"    ⚠ 场景 {scene_num} LLM 失败：{e}")
        return ""


def _build_full_prompt(state, directive, target_words, prev_tail):
    """整章单次生成的 prompt 组装（短章用）。"""
    outline = _get_outline(state, directive.chapter_index)
    rhythm_instruction = get_rhythm_instruction(state, directive.chapter_index)
    context = build_writer_context(state, directive)
    structure_section = _format_structure(state, directive)
    blueprint_section = _format_blueprint(directive)
    tone_block = format_tone_brief(state)
    concept_block = format_concept_brief(state)
    character_state_block = _format_character_states(directive)
    forbidden_block = _format_forbidden(directive)
    ability_plan_block = _format_ability_plan(directive)
    external_ai_block = _format_external_ai_constraint(state)
    type_block = f"\n【本章类型】{directive.chapter_type}" if directive.chapter_type else ""

    prev_tail_section = ""
    if prev_tail:
        prev_tail_section = f"\n═══ 上章末尾原文（必须无缝衔接） ═══\n{prev_tail}\n"

    system = _load_writer_system(state, directive)
    inspiration_block = _format_user_inspiration(directive)
    volume_stage_map_block = _format_volume_stage_map(state, directive)
    feedback_block = _format_user_feedback(directive)
    plan_blocks = _get_plan_blocks_text(directive)
    # 整章单次写作时，voice card 覆盖所有本章涉及角色
    scene_chars = list((directive.character_states or {}).keys())
    voice_cards_block = _format_voice_cards_for_scene(state, scene_chars)
    prompt = f"""写第{directive.chapter_index}章。

{inspiration_block}
{volume_stage_map_block}
{plan_blocks}
{feedback_block}
{tone_block}

{concept_block}

═══ 本章在全书中的位置 ═══
{structure_section}

═══ 本章气质 ═══{type_block}
张力：{directive.tension.value}（{directive.emotional_note}）
节奏：{rhythm_instruction}
位置：{directive.chapter_position}
大纲目标：{outline.get('goal', '继续推进故事')}

{character_state_block}

{voice_cards_block}

{ability_plan_block}

{external_ai_block}

{forbidden_block}

═══ 场景蓝图 ═══
{blueprint_section}
{prev_tail_section}
═══ 写作上下文 ═══
{context}

═══ 写作参数 ═══
目标字数：约{target_words}字（不能少于 {int(target_words*MIN_FILL_RATIO)} 字——宁可多铺陈，不要草率收场）
{_get_volume_hint(state, directive)}
结尾：{_get_hook_instruction(directive)}

节奏要慢，细节要多。文风手册里的禁用词不得出现。"""
    return prompt, system


def _expand_to_target(state: NovelState, directive: ChapterDirective,
                      draft: str, target_words: int) -> str:
    """
    扩写 pass：初稿字数不够时调用。
    只补细腻/内心/感官/铺陈，严禁动剧情/改结构。
    """
    from state import count_chapter_words as _cw
    tone_block = format_tone_brief(state)
    cur_wc = _cw(draft)
    shortage = max(target_words - cur_wc, target_words // 4)  # 至少要求补四分之一目标，避免 LLM 觉得"差不多就行"
    system = _load_writer_system(state, directive)

    if len(draft) > 16000:
        draft_for_prompt = draft[:8000] + "\n\n[...中段略去，请保持中段内容不变...]\n\n" + draft[-8000:]
    else:
        draft_for_prompt = draft

    prompt = f"""以下是第{directive.chapter_index}章的初稿，当前字数 {cur_wc}，目标 {target_words}——还差 {shortage} 字。

{tone_block}

═══ 扩写任务 ═══
请在不改变剧情骨架和场景序列的前提下，把这一章扩写到 {target_words} 字左右。
只允许加这些东西：
  · 主角内心独白（每个关键动作前后可以加 3-5 句）
  · 感官细节（视觉/听觉/触觉/嗅觉——让画面更立体）
  · 微表情和小动作（眼神、指尖、呼吸的变化）
  · 对话间的停顿、未说完的话、沉默的余响
  · 环境/氛围的铺陈（空气里的味道、光影的变化、场景的细微响动）
  · 配角的特色反应（符合他们的口癖/性格）

严禁：
  · 增加新的场景/情节/角色
  · 改变任何情节走向和结局
  · 删除任何已有内容
  · 为凑字数写抽象套话（"他感到紧张""她心里难过"这种）

═══ 初稿 ═══
{draft_for_prompt}

直接输出扩写后的完整正文（保留原有"第X章 标题"行）。"""
    try:
        expanded = system_user(system, prompt, temperature=0.85,
                                max_tokens=SCENE_MAX_TOKENS)
        # 用字数（不是字符数）判断扩写是否有效——扩写后字数变少就丢弃
        new_wc = _cw(expanded)
        if new_wc < cur_wc * 0.95:
            print(f"  ⚠ 扩写后字数变少（{new_wc} < {cur_wc}），保留原稿")
            return draft
        return expanded
    except Exception as e:
        print(f"  ⚠ 扩写失败：{e}——保留原稿")
        return draft


def _format_user_inspiration(directive: ChapterDirective) -> str:
    """
    本章灵感——一旦作者填写，本章必须以它为主轴展开。
    返回的 block 应当在 prompt 最顶部（标题之后第一块），优先级高于一切蓝图/上下文。
    若未填，返回空串。
    """
    insp = (getattr(directive, "user_inspiration", "") or "").strip()
    if not insp:
        return ""
    return (
        "═══ ★★★ 本章主轴 · 作者灵感（这一章就是为它而写）★★★ ═══\n"
        f"{insp}\n"
        '—— 这是本章写作的【核心目标】，不是普通约束、不是参考、不是"也要体现"的元素之一。\n'
        "整章的情绪走向、场景取舍、人物动作、笔触轻重，都要以**兑现这条灵感**为第一优先。\n"
        "蓝图、张力标签、节奏建议、forbidden 列表都是辅料——它们辅助你把这条灵感写好，\n"
        "而不是反过来让灵感为它们让路。如果蓝图/上下文与灵感冲突，**以灵感为准**。\n"
        "写完之后回头看：读者带走的最强烈感受，必须就是这条灵感想表达的东西。\n"
    )


def _format_volume_stage_map(state, directive: ChapterDirective) -> str:
    """
    给 writer 一张"全景图"——本卷的 stage 序列 + 当前 stage 内章节地图。
    让 writer 知道本章在卷里的哪一段戏的哪一节，承接前章、铺垫后章不至于盲写。
    若没有 stage 设计或卷数据，返回空串。
    """
    chapter_index = directive.chapter_index
    vol = None
    for v in state.volumes:
        if v.chapter_start <= chapter_index <= v.chapter_end:
            vol = v
            break
    if not vol:
        return ""

    stages = state.stages_in_volume(vol.index)
    cur_stage = state.primary_stage_for_chapter(chapter_index)

    lines = [f"═══ 本卷·大情节地图（你正在哪一段戏的哪一节）═══"]
    vol_role = f"[{vol.structure_role}]" if vol.structure_role else ""
    lines.append(
        f"卷《{vol.title}》{vol_role} 使命：{vol.purpose[:50] or vol.theme}"
    )
    if vol.expression:
        lines.append(f"卷表达：{vol.expression[:50]}")

    if stages:
        lines.append("\n本卷 stage 序列（★ 是本章所在）：")
        for st in stages:
            mark = "★" if cur_stage and st.stage_id == cur_stage.stage_id else " "
            role = f"[{st.structure_role}]" if st.structure_role else ""
            lines.append(
                f"  {mark} {role} {st.name} (Ch{st.chapter_start}-{st.chapter_end}) "
                f"使命：{st.purpose[:35]}"
            )

    if cur_stage:
        # 当前 stage 内的章节 outlines
        stage_outlines = []
        for o in (vol.chapter_outlines or []):
            sid = o.get("stage_id") or ""
            ci_o = o.get("index")
            if sid == cur_stage.stage_id:
                stage_outlines.append(o)
            elif not sid and isinstance(ci_o, int) and cur_stage.chapter_start <= ci_o <= cur_stage.chapter_end:
                stage_outlines.append(o)
        stage_outlines.sort(key=lambda x: x.get("index", 0))
        if stage_outlines:
            lines.append(f"\n当前 stage《{cur_stage.name}》章节地图（▶ 是本章）：")
            for o in stage_outlines:
                ci_o = o.get("index", "?")
                marker = "▶" if ci_o == chapter_index else " "
                title = (o.get("title") or "").strip()
                goal = (o.get("goal") or "").strip()[:50]
                lines.append(f"  {marker} 第{ci_o}章《{title}》：{goal}")
            lines.append(
                "—— 写作时承接 ▶ 之前章节留下的钩子/伏笔/情绪余波，给 ▶ 之后章节留下推进的素材，"
                "不要把本 stage 写成孤立单章。"
            )

    return "\n".join(lines) + "\n"


def _format_user_feedback(directive: ChapterDirective) -> str:
    """
    重写反馈：作者对上一版本不满意时给出的修改方向。
    与 _format_user_inspiration 分离——灵感是本章主轴，反馈是针对上一版的修正。
    """
    fb = (directive.user_feedback or "").strip()
    if not fb:
        return ""
    return (
        "═══ ⚠ 重写任务——作者对上一版本不满意，请按以下反馈改进 ═══\n"
        f"{fb}\n"
        "蓝图已经按这条反馈重新设计过了（场景取舍/节奏/落点都已调整）。\n"
        "你这一层的任务：按新蓝图写出来，同时在描写/对话/情绪/人物刻画上\n"
        "也针对这条反馈发力——不要复现上一版本的笔触和走向。\n"
    )


def _load_writer_system(state: NovelState, directive: ChapterDirective) -> str:
    """
    选择 writer SYSTEM 的变体。
    优先级：
      1. chapter_dispatcher 给出的 variant（按小说子类型/章节位置/功能路由）
         - 非 default 时返回 prompt_variants.WRITER_SYSTEM_<UPPER>（已 format genre）
      2. 兜底：代码内置 SYSTEM_TEMPLATE
    """
    try:
        from agents.chapter_dispatcher import dispatch, get_writer_system
        plan = dispatch(state, directive)
        variant = plan.writer_variant or "default"
        if variant != "default":
            sys_text = get_writer_system(variant, genre=state.genre)
            if sys_text:
                return sys_text
    except Exception as e:
        print(f"[writer] dispatcher/variant 加载失败，走兜底：{type(e).__name__}: {e}")
    return SYSTEM_TEMPLATE.format(genre=state.genre)


def _format_beat_anchors(beat) -> str:
    """
    本幕的三类锚点——chapter_planner 规划时留给 writer 的具体抓手。
    对白/感官/戏剧节拍三层，每层"参考不强制"——让 writer 融入但不是照抄。
    """
    dialogue = [s for s in (getattr(beat, "dialogue_seeds", []) or []) if s]
    sensory = [s for s in (getattr(beat, "sensory_anchors", []) or []) if s]
    dramatic = [s for s in (getattr(beat, "dramatic_beats", []) or []) if s]

    if not (dialogue or sensory or dramatic):
        return ""

    parts = ["═══ 本幕锚点（规划留下的抓手——融入其中大部分，不必全用；但不得整体忽略）═══"]
    if dialogue:
        parts.append("【对白种子】")
        for s in dialogue:
            parts.append(f"  · {s}")
        parts.append("  规则：这些是方向示范，不要逐字抄。抄原句=机械感；完全不融入=失去规划意图。")
    if sensory:
        parts.append("【感官锚点】（视/听/嗅/触/内感 —— 至少把其中 3-5 个自然融入文字）")
        for s in sensory:
            parts.append(f"  · {s}")
    if dramatic:
        parts.append("【戏剧节拍】（这几个节点必须在本幕内某处出现，哪怕换句式表达）")
        for s in dramatic:
            parts.append(f"  · {s}")
    return "\n".join(parts)


def _format_voice_cards_for_scene(state: NovelState, scene_characters: list) -> str:
    """
    本幕出场角色的声音卡片——让 writer 写对白时直接看到"这个人该怎么说话"。

    只为真正出场的角色生成卡片（上限 5 个），不然 prompt 会无谓膨胀。
    配角如果没有独立 voice card 字段，就走 Character.brief()。
    """
    if not scene_characters:
        return ""
    present = []
    for name in scene_characters[:5]:
        c = next((x for x in (state.characters or []) if x.name == name), None)
        if c:
            present.append(c)
    if not present:
        return ""
    blocks = [c.voice_card() for c in present]
    return "═══ 本幕出场角色·声音卡 ═══\n" + "\n\n".join(blocks) + "\n"


def _format_character_states(directive: ChapterDirective) -> str:
    """PreChapterBrief 角色状态部分——给 writer 看本章涉及角色此刻的位置/伤势/情绪/物品。"""
    if not directive.character_states:
        return ""
    lines = ["【本章相关角色此刻状态（硬事实，不得违反）】"]
    for name, st in list(directive.character_states.items())[:6]:
        parts = [f"  {name}"]
        if st.get("realm"):
            parts.append(f"级别/身份：{st['realm']}")
        if st.get("location"):
            parts.append(f"位置：{st['location']}")
        if st.get("emotion"):
            parts.append(f"情绪：{st['emotion']}")
        if st.get("injury"):
            parts.append(f"伤势：{st['injury']}")
        if st.get("items"):
            parts.append(f"物品：{' / '.join(st['items'][:3])}")
        lines.append("｜".join(parts))
    return "\n".join(lines)


def _format_ability_plan(directive: ChapterDirective) -> str:
    """ability_planner 写完的本章能力使用规划——挂在 directive.ability_plan。"""
    plan = getattr(directive, "ability_plan", None)
    if not plan:
        return ""
    try:
        from agents.ability_planner import format_ability_plan_brief
        return format_ability_plan_brief(plan)
    except Exception:
        return ""


def _format_external_ai_constraint(state) -> str:
    """全书绑了真 LLM 的金手指——无论本章 ability_plan 怎么规划，writer 看到
    这些 asset 名时**必须**用 [[ASK_AI:名|问题]] 占位符，绝不允许自己脑补回答。

    这是顶层硬约束，独立于 ability_plan：即便 ability_planner 没标记，
    state 里只要登记了 external_llm_profile，writer 看到 asset 名就必须走占位。
    防止 v1 之前那种"writer 自己编豆包回答"的漂移。
    """
    if not state.power_system or not state.power_system.special_abilities:
        return ""
    bound = [ab for ab in state.power_system.special_abilities
              if (ab.external_llm_profile or "").strip()]
    if not bound:
        return ""
    # 用第一个 asset 名做示例（避免硬编码任何具体项目的术语）
    sample = bound[0].name

    lines = [
        "【⚠️ 真·AI 接入 — 顶层硬约束（独立于本章能力规划）】",
        "本书有 asset 绑定了真实大语言模型——主角与它们的任何交互（问答、查询、求建议、",
        "求方案、要数据），正文里**只能用占位符**，绝对不允许 writer 自己编它们说什么。",
        "占位会在章节定稿前被真发给 LLM 拿真实回答替换。writer 既不需要也不应该想象内容。",
        "",
        "═══ 占位符规则（铁律）═══",
        "  正确：主角铺陈触发动作 → 写 [[ASK_AI:<asset 名>|具体问题]] 占位 → 写主角的反应/思考",
        f"  示例：[[ASK_AI:{sample}|<具体问题文本——必须是 AI 训练数据可推出的真实知识内容>]]",
        "  违规（必触发审核 critical）：",
        f"    × 「{sample}说：『……』」直接编它的回答",
        f"    × 「{sample}告诉他……」总结它的内容",
        f"    × 「{sample}浮现出建议……」隐式回避占位",
        "",
        "═══ 功能边界（铁律——决定问什么能问、问什么不能问）═══",
        "AI 训练数据里**只有现代真实世界的知识**，不知道本书虚构设定的任何专有信息。",
        "判断什么能问的依据：是不是**现代真实世界已经存在、AI 训练数据里可能有的**知识？",
        "",
        "  ✓ 能问（现代真实世界已有的知识 / 普世原理 / 跨情境通用思路）：",
        "    根据本书 asset 自身定位（见下方「描述」/「来源」字段）判断它擅长哪类现代知识。",
        "    一般包括：现代科学、工程技术、商业经济、法律原理（不是具体条文）、医学、心理学、",
        "    数学逻辑、现代世界真实历史与案例 等——只要是现代真实世界存在的、AI 训练数据",
        "    可能涵盖的知识。",
        "",
        "  ✗ 不能问（本书虚构设定的专有信息——AI 完全不可能知道）：",
        "    · 本书自创的具体律法/条文/规则/制度（小说设定文档里的内容，AI 训练数据没有）",
        "    · 本书虚构的人名/势力名/地名/具体事件/朝代史实",
        "    · 本书虚构的本地行情/价格/物资分布",
        "    · 任何只在本书设定文档/前文剧情里出现的专有信息",
        "    · 预言未来 / 占卜吉凶（AI 不是预言家）",
        "    → 这些主角要靠智计、打听、观察、翻阅设定里已有的文献来获取，**不是问 AI**。",
        "",
        "  ✗ 不该问（戏剧节奏，避免主角变 AI 傀儡）：",
        "    · 具体行动策略（要不要跟某人合作、是否信任某人）——策略是主角自己定的，",
        "      AI 只提供原理；非要写「主角向 AI 请教方向」，让 AI 给「现代/类似情境下」",
        "      的一般原则，由主角自己折算到当下情境",
        "    · 简单道理 / 不用 AI 也能想出的事（滥用破坏节制感）",
        "",
        "  ★ 正确套路：AI 给「现代普世知识/原理」 + 主角靠自己拿到「本书设定里的当地具体信息」",
        "    → 主角自己组合两者做决策。**保留主角的「人」味，不是 AI 的傀儡**。",
        "    这才是「AI 加持类」小说的精髓——主角的人物魅力来自智计本身，",
        "    不是来自直接照搬 AI 的输出。",
        "",
        "═══ 戏剧形式（自由）═══",
        "  · asset 怎么获取（来源）——任意戏剧化形式都可以（具体见下面每个 asset 的「来源」）",
        "  · 使用时的呈现形式——脑内浮现、低语回响、视觉幻象、屏幕显化、符箓发光、键入查询、",
        "    意识流字符滚动 等等，根据 asset 设定自由设计",
        "  · 但**功能边界**和**占位符规则**不可妥协",
        "",
        "═══ 本书绑定的 asset 清单（writer 必读）═══",
    ]
    for ab in bound:
        lines.append(
            f"  · 《{ab.name}》（外接 LLM profile: {ab.external_llm_profile}）"
        )
        if ab.description:
            lines.append(f"      描述：{ab.description[:120]}")
        if ab.source:
            lines.append(f"      来源/获取：{ab.source}（戏剧形式可在此基础上发挥）")
        if ab.unlock_condition:
            lines.append(f"      使用条件：{ab.unlock_condition}")
    lines.append("")
    lines.append("**铁律**：本章正文出现以上 asset 名 → 主角对它的任何输出请求必须用占位符；"
                  "writer 自己脑补回答 = 违规；问超出功能边界（AI 不可能知道的本书专有信息）= 设定漂移。")
    return "\n".join(lines)


def _format_forbidden(directive: ChapterDirective) -> str:
    """PreChapterBrief 禁止内容——防剧透/设定冲突 + 笔触多样性。"""
    if not directive.forbidden_content:
        return ""
    lines = ["【本章禁止出现的内容（硬性约束）】"]
    for item in directive.forbidden_content[:14]:
        lines.append(f"  ✕ {item}")
    return "\n".join(lines)


def _format_structure(state, directive: ChapterDirective) -> str:
    """将本章的分形结构定位、purpose、expression 组装成写作前的必读块。"""
    lines = []

    # 完整结构链
    chain = directive.structure_chain or state.structure_chain_for_chapter(
        directive.chapter_index, chapter_role=directive.structure_role
    )
    lines.append(f"结构链：{chain}")

    # 各上层的 purpose/expression（让 writer 理解整条链上每一层想要什么）
    if state.book_structure.book_proposition:
        lines.append(f"  整本命题：{state.book_structure.book_proposition}"
                     f" | 最终表达：{state.book_structure.book_expression}")

    vol = state.get_volume(directive.volume_index)
    if vol:
        role = vol.structure_role or state.book_structure.role_for_volume(vol.index)
        lines.append(
            f"  本卷[{role}]：{vol.purpose or vol.theme}"
            f" | 卷内想表达：{vol.expression or vol.theme}"
        )

    stage = state.primary_stage_for_chapter(directive.chapter_index)
    if stage:
        role = f"[{stage.structure_role}]" if stage.structure_role else ""
        lines.append(
            f"  大情节·{stage.name}{role}：{stage.purpose or stage.atmosphere}"
            f" | 想表达：{stage.expression or stage.atmosphere}"
        )

    sub = state.primary_sub_scene_for_chapter(directive.chapter_index)
    if sub:
        role = f"[{sub.structure_role}]" if sub.structure_role else ""
        lines.append(
            f"  小情节·{sub.name}{role}：{sub.purpose or sub.description}"
            f" | 想表达：{sub.expression}"
        )

    # 本章自身
    ch_role = f"[{directive.structure_role}]" if directive.structure_role else ""
    lines.append(f"")
    lines.append(f"★ 本章{ch_role}")
    lines.append(f"  purpose（为什么必须写）：{directive.purpose or '（未显式声明，按大纲目标处理）'}")
    lines.append(f"  expression（想让读者感受到）：{directive.expression or '（未显式声明）'}")
    return "\n".join(lines)


def _format_blueprint(directive: ChapterDirective) -> str:
    """将 ChapterBlueprint 格式化为写作提示。"""
    bp = directive.blueprint
    if not bp:
        must = " / ".join(directive.must_include) if directive.must_include else "按叙事线推进"
        return f"必须发生：{must}"

    lines = [
        f"【开篇承接】{bp.opening_state}",
        f"【本章核心变化】{bp.chapter_delta}",
        f"【节奏】{bp.pacing_note}",
        "",
        "【场景序列】（依次对应本章内部的起承转合）",
    ]
    for beat in bp.scene_beats:
        char_str = "、".join(beat.characters) if beat.characters else "按需"
        role_tag = f"[{beat.structure_role}]" if beat.structure_role else ""
        lines.append(
            f"  场景{beat.scene_index}{role_tag}·{beat.scene_type}"
            f"（{beat.location or '场景内'}，约{beat.word_quota}字）"
            f"\n    出场：{char_str}"
        )
        if beat.purpose:
            lines.append(f"    作用：{beat.purpose}")
        if beat.expression:
            lines.append(f"    表达：{beat.expression}")
        lines.append(f"    内容：{beat.content}")
        lines.append(f"    变化：{beat.emotional_shift}")
    lines.append(f"\n【结尾钩子方向】{bp.closing_hook}")
    return "\n".join(lines)


def revise_chapter(state: NovelState, directive: ChapterDirective, draft: str, feedback: str) -> str:
    system = _load_writer_system(state, directive)
    ch_role = f"[{directive.structure_role}]" if directive.structure_role else ""
    tone_block = format_tone_brief(state)
    prompt = f"""
根据修改意见修改第{directive.chapter_index}章。

{tone_block}

【修改意见】
{feedback}

【保持不变的要求】
- 分形结构定位：本章{ch_role}
- purpose：{directive.purpose or '(未声明)'}
- expression：{directive.expression or '(未声明)'}
- 张力：{directive.tension.value}
- 节奏：{directive.rhythm.value}（{directive.word_pace}）
- 情绪基调：{directive.emotional_note}
- 必须事件：{'; '.join(directive.must_include[:2])}
- 单主角原则：整章必须以主角为中心，配角不得抢戏
- 文风手册里的禁用词不可出现

【原稿】
{draft}

直接输出修改后完整正文，保留章节标题行。
"""
    return system_user(system, prompt, temperature=0.82)


def _build_lines_context(state: NovelState, directive: ChapterDirective) -> str:
    parts = []
    for lid in directive.active_lines:
        line = state.get_line(lid)
        if not line:
            continue
        phase = line.get_phase_for_chapter(directive.chapter_index)
        if not phase:
            continue
        prefix = "★【主推】" if lid == directive.primary_line else "  【辅助】"
        parts.append(
            f"{prefix}[{line.scope.value}/{line.line_type.value}] {line.name}\n"
            f"         阶段{phase.phase_index}/{len(line.phases)}《{phase.name}》"
            f"[{phase.tension.value}]：{phase.description}"
        )
    return "\n".join(parts) if parts else "按故事自然推进。"


def _build_characters_context(state: NovelState, directive: ChapterDirective) -> str:
    vol = state.current_volume()
    if not vol:
        return state.character_brief_list()
    active = state.active_characters_in_volume(vol.index)
    lines = []
    for c in active:
        status = state.memory.character_states.get(c.name, "")
        vol_arc = c.volume_arcs.get(vol.index, "")
        vol_realm = c.volume_realm.get(vol.index, c.realm)
        line = (f"【{c.role.value}】{c.name}（{c.realm}→{vol_realm}）\n"
                f"  {c.personality_detail[:60]}\n"
                f"  说话风格：{c.speech_pattern}")
        if vol_arc:
            line += f"\n  本卷弧线：{vol_arc}"
        if status:
            line += f"\n  当前状态：{status}"
        lines.append(line)
    return "\n".join(lines)


def _build_sp_context(state: NovelState, directive: ChapterDirective) -> str:
    if not directive.satisfaction_points:
        return "· 无爽点触发（可铺垫已规划的爽点）"
    lines = []
    for sp_id in directive.satisfaction_points:
        sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
        if sp:
            lines.append(f"· 【触发：{sp.sp_type.value}】{sp.title}（强度{sp.intensity}/10）\n  {sp.payoff_description}")
    return "\n".join(lines)


def _build_fw_context(state: NovelState, directive: ChapterDirective) -> str:
    lines = []
    for fw_id in directive.foreshadow_plant:
        fw = state.get_foreshadow(fw_id)
        if fw:
            lines.append(f"· 【植入伏笔】{fw.content}\n  （真实含义：{fw.hidden_meaning}，计划第{fw.planned_resolve_chapter}章兑现）")
    for fw_id in directive.foreshadow_resolve:
        fw = state.get_foreshadow(fw_id)
        if fw:
            lines.append(f"· 【兑现伏笔】{fw.resolution_description}")
    return "\n".join(lines) if lines else "· 无"


def _get_volume_hint(state: NovelState, directive: ChapterDirective) -> str:
    vol = state.get_volume(directive.volume_index)
    if not vol:
        return ""
    if directive.chapter_index == vol.chapter_start:
        return f"第{vol.index}卷《{vol.title}》卷首 — {vol.opening_hook}"
    if directive.chapter_index == vol.chapter_end:
        return f"第{vol.index}卷《{vol.title}》卷尾 — {vol.closing_hook}"
    local = directive.chapter_index - vol.chapter_start + 1
    return f"第{vol.index}卷《{vol.title}》第{local}/{vol.total_chapters}章"


def _get_hook_instruction(directive: ChapterDirective) -> str:
    if directive.chapter_position == "卷尾":
        return "必须有震撼的卷尾钩子，让读者立刻翻开下一卷"
    if directive.tension == TensionLevel.PEAK:
        return "高潮未完结，结尾在最紧张处切断"
    if directive.tension == TensionLevel.TWIST:
        return "反转后余震，结尾留下新的悬念"
    return "自然悬念，让读者想知道接下来发生什么"


def _get_outline(state: NovelState, index: int) -> dict:
    vol = state.current_volume()
    if vol:
        for o in vol.chapter_outlines:
            if o["index"] == index:
                return o
    return {"index": index, "goal": "继续推进故事"}
