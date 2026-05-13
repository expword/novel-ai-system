"""
IntentAnalyzerAgent —— Phase -1：分析作者意图。

输入：作者用自然语言写的一段想法、背景、基调、情绪描述。
  例："我想写一个从现代穿越到修真世界的腹黑商人——主打反套路、种田发育、情感线细腻；
       风格偏烟火气+成人向，不要傻白甜；对标《大奉打更人》和《诡秘之主》；
       文风要有点古典韵味，但对话要现代口语化。"

输出：把这段描述解析成一组结构化信号，写入 state.creative_intent。
  这些信号会作为 Phase 0 ConceptPitch/TropeLibrary/ToneManual 的**硬约束**，
  优先级高于 config.py 种子，强制 LLM 的立项沿着作者想要的方向走。

只做一件事：把"人话"变成"字段"。
"""
from datetime import datetime
from json_utils import request_json
from state import NovelState, CreativeIntent, IntentRevision


SYSTEM = """你是小说策划咨询师。作者告诉你他想写什么，你要把这段自由发挥的话解析成结构化的立项信号。

读懂作者的意图——尤其注意他隐含透露的信息：
- 题材倾向（玄幻/都市/科幻/悬疑/言情）
- 读者群体（从词汇、节奏偏好、关注点推断男频/女频/平台）
- 核心卖点（他反复强调的东西、或明显的差异化点）
- 套路偏好（主动提到要/不要的）
- 文风暗示（提到的参考作品、他用的形容词）
- 反派/感情线/后宫 的态度
- 整体气质（情绪/基调——热血？沉郁？轻松？黑暗？治愈？）

你提取的每一项都要和作者的描述匹配，不能凭空发挥。作者没明说的字段可以留空或标"未指定"。
最后输出一段 100 字的 tone_summary，用一句话讲清这本书的整体气质。

输出严格 JSON。"""


def analyze_intent(state: NovelState, raw_description: str) -> CreativeIntent:
    """
    分析作者的自然语言描述，把提取结果写入 state.creative_intent 并返回。
    """
    if not raw_description or not raw_description.strip():
        print("  ⚠ IntentAnalyzer 没拿到任何描述，跳过")
        return state.creative_intent

    prompt = f"""作者的想法如下——请读懂它，提取所有结构化立项信号。

【作者原话】
{raw_description.strip()}

═══ 要求 ═══
1. 先判断题材、读者群、平台（从用词和关注点推断）
2. 找出作者强调的核心卖点（反复提的、做对比的、特别在意的）
3. 推断对标作品（如果作者提了就用，没提就根据气质建议 2-3 部真实作品）
4. 找出作者明示/暗示的套路偏好（想要的 / 排斥的）
5. 推断反派处理、感情线、后宫 的倾向——作者没明说就基于读者群推断
6. 推断文风——叙述视角、句法节奏、对话风格
7. 最后一句话 tone_summary：这本书整体是什么气质
8. analyzer_notes：如果作者的意图有冲突或需要作者进一步澄清的地方，写在这里（给作者看的提示，可以空）

作者没明确提的字段，**不要瞎编**——留空或只填最保守的推断。

输出 JSON：
{{
  "suggested_title": "（若作者未提或不便推荐，留空）",
  "suggested_genre": "...",
  "suggested_theme": "综合描述主题（80字，要忠实于作者原意）",

  "audience_hint": "男频|女频|混合",
  "age_group_hint": "如 18-30",
  "platform_hint": "起点|晋江|番茄|书旗|QQ阅读|飞卢",
  "selling_points_hints": ["核心卖点1", "卖点2"],
  "benchmark_hints": ["对标作品1", "作品2"],
  "differentiation_hint": "与对标的差异化点（30字）",

  "embrace_tropes_hints": ["拥抱的套路1"],
  "avoid_tropes_hints": ["规避的烂梗1"],
  "preferred_sp_types_hints": ["偏好的爽点类型"],
  "villain_policy_hint": "洗白型|彻底黑化型|灰色模糊型|人格魅力型",
  "romance_policy_hint": "甜宠|虐恋|轻感情|发糖+撒糖|无感情线",
  "harem_policy_hint": "单恋专一|双女主|多女主|不涉及",
  "protagonist_archetype_hint": "逆袭型|天才型|苟道型|腹黑型|热血型|成熟型|萝莉化",
  "world_tone_hint": "热血|沉郁|轻松|压抑|温暖|黑暗|治愈|古典",

  "narrative_voice_hint": "第一人称|第三人称限知|上帝视角|多视角切换",
  "style_reference_hint": "笔触参考（30字）",
  "dialogue_style_hint": "古风|现代|半文半白|诗化|口语化",

  "tone_summary": "整体气质一句话（100字）",
  "analyzer_notes": "给作者的澄清提示（可空）"
}}
"""
    example = (
        '{"suggested_title":"","suggested_genre":"玄幻","suggested_theme":"...",'
        '"audience_hint":"男频","platform_hint":"起点","selling_points_hints":["反套路","腹黑"],'
        '"benchmark_hints":["..."],"embrace_tropes_hints":["..."],"avoid_tropes_hints":["..."],'
        '"villain_policy_hint":"灰色模糊型","romance_policy_hint":"发糖+撒糖",'
        '"protagonist_archetype_hint":"腹黑型","world_tone_hint":"烟火",'
        '"narrative_voice_hint":"第三人称限知","style_reference_hint":"...",'
        '"dialogue_style_hint":"半文半白","tone_summary":"...","analyzer_notes":""}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["suggested_genre", "audience_hint", "tone_summary"],
        max_retries=4, temperature=0.4,
        agent_name="IntentAnalyzer",
        example_schema=example,
    )

    # 写入 state
    intent = state.creative_intent
    intent.raw_description = raw_description.strip()
    intent.analyzed = True
    intent.suggested_title = data.get("suggested_title", "") or ""
    intent.suggested_genre = data.get("suggested_genre", "") or ""
    intent.suggested_theme = data.get("suggested_theme", "") or ""

    # ★ 把推断出的题材/主题/书名同步写回 state 的顶层字段——
    #   否则下游所有 agent 的 prompt 顶部仍看的是创建项目时的默认值（如"玄幻/苍穹问道"）
    #   优先级：用户创建项目时填的值 > intent 推断（除非原值是默认兜底）
    _propagate_intent_to_state(state, intent)
    intent.audience_hint = data.get("audience_hint", "") or ""
    intent.age_group_hint = data.get("age_group_hint", "") or ""
    intent.platform_hint = data.get("platform_hint", "") or ""
    intent.selling_points_hints = data.get("selling_points_hints", []) or []
    intent.benchmark_hints = data.get("benchmark_hints", []) or []
    intent.differentiation_hint = data.get("differentiation_hint", "") or ""
    intent.embrace_tropes_hints = data.get("embrace_tropes_hints", []) or []
    intent.avoid_tropes_hints = data.get("avoid_tropes_hints", []) or []
    intent.preferred_sp_types_hints = data.get("preferred_sp_types_hints", []) or []
    intent.villain_policy_hint = data.get("villain_policy_hint", "") or ""
    intent.romance_policy_hint = data.get("romance_policy_hint", "") or ""
    intent.harem_policy_hint = data.get("harem_policy_hint", "") or ""
    intent.protagonist_archetype_hint = data.get("protagonist_archetype_hint", "") or ""
    intent.world_tone_hint = data.get("world_tone_hint", "") or ""
    intent.narrative_voice_hint = data.get("narrative_voice_hint", "") or ""
    intent.style_reference_hint = data.get("style_reference_hint", "") or ""
    intent.dialogue_style_hint = data.get("dialogue_style_hint", "") or ""
    intent.tone_summary = data.get("tone_summary", "") or ""
    intent.analyzer_notes = data.get("analyzer_notes", "") or ""

    _print_intent(intent)
    return intent


def refine_intent(state: NovelState, addition: str) -> CreativeIntent:
    """
    在已有意图上追加一段补充描述，拼接到 raw_description 并重跑 intent_analyzer。
    适用于作者在 Phase -1 分析完后，想"再补一刀"调整方向——比如"再加一条：
    主角不要是天才型，改成苟道型"。

    返回更新后的 creative_intent。
    """
    addition = (addition or "").strip()
    if not addition:
        print("  ⚠ refine_intent 收到空追加，跳过")
        return state.creative_intent

    intent = state.creative_intent
    # 拼接为新的完整描述：旧意图 + 本次追加（用分隔符标出轮次）
    round_idx = len(intent.revisions) + (2 if intent.analyzed else 1)
    separator = f"\n\n【第 {round_idx} 轮追加】\n"
    merged = (intent.raw_description.rstrip() + separator + addition) if intent.raw_description else addition

    # 记录本次追加
    revision = IntentRevision(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        addition=addition,
        round_index=round_idx,
    )

    # 保留旧 tone_summary 作为"本轮分析前"的基线，分析后对比得出变化点
    prev_summary = intent.tone_summary

    # 重跑分析（这会重置所有结构化字段到新一轮的解析结果）
    analyze_intent(state, merged)

    # 写入本轮变化概括
    new_summary = state.creative_intent.tone_summary
    if prev_summary and new_summary and prev_summary != new_summary:
        revision.summary = new_summary[:80]
    else:
        revision.summary = "（分析结果未显著变化）" if prev_summary == new_summary else new_summary[:80]

    state.creative_intent.revisions.append(revision)
    print(f"  ✓ 意图第 {round_idx} 轮已追加并重分析（历史共 {len(state.creative_intent.revisions)} 轮）")
    return state.creative_intent


def _propagate_intent_to_state(state: NovelState, intent: CreativeIntent) -> None:
    """
    把 intent 里推断出的 title/genre/theme 回填到 state 顶层字段，
    这样下游所有 prompt 里的 `{state.title}` / `{state.genre}` / `{state.theme}`
    都能看到新意图的真实取向——而不是创建项目时的默认值"玄幻/苍穹问道"。

    回填原则（保守版）：
    - 用户显式填过的字段保留（不覆盖）
    - 当前字段是默认兜底值时用 intent 推断值覆盖
    - 冲突时打印警告让用户知道（但仍以用户显式选择为准）
    """
    DEFAULT_GENRES = {"", "玄幻"}  # 前端新建项目默认给"玄幻"，视为"未指定"
    DEFAULT_TITLE_HINT = "苍穹问道"

    # 标题：只在用户没起名或用的是默认时覆盖
    if intent.suggested_title and (not state.title or state.title == DEFAULT_TITLE_HINT):
        print(f"  → state.title 从 '{state.title}' 更新为 '{intent.suggested_title}'（按意图推断）")
        state.title = intent.suggested_title

    # 题材：只在用户没明确选（空或默认"玄幻"）时覆盖
    if intent.suggested_genre:
        if state.genre in DEFAULT_GENRES:
            if state.genre != intent.suggested_genre:
                print(f"  → state.genre 从 '{state.genre}' 更新为 '{intent.suggested_genre}'（按意图推断）")
            state.genre = intent.suggested_genre
        elif state.genre != intent.suggested_genre:
            # 用户显式选了 A，但 intent 推断是 B——保留用户选择，仅警告
            print(f"  ⚠ 题材冲突：用户选择 '{state.genre}'，意图推断 '{intent.suggested_genre}'。"
                  f"保留用户选择，但下游 agent 会优先遵循意图原话。")

    # 主题：当前 theme 空或是默认占位时覆盖
    if intent.suggested_theme and (
        not state.theme
        or DEFAULT_TITLE_HINT in state.theme
        or len(state.theme) < 10
    ):
        print(f"  → state.theme 已按意图更新（{len(intent.suggested_theme)} 字）")
        state.theme = intent.suggested_theme


def _print_intent(intent: CreativeIntent) -> None:
    print(f"  ✓ 意图分析：")
    print(f"    题材：{intent.suggested_genre}｜读者：{intent.audience_hint}/{intent.platform_hint}")
    if intent.selling_points_hints:
        print(f"    卖点：{' / '.join(intent.selling_points_hints[:4])}")
    if intent.benchmark_hints:
        print(f"    对标：{' vs '.join(intent.benchmark_hints[:3])}")
    if intent.embrace_tropes_hints or intent.avoid_tropes_hints:
        print(f"    拥抱：{' / '.join(intent.embrace_tropes_hints[:4])}")
        print(f"    规避：{' / '.join(intent.avoid_tropes_hints[:3])}")
    print(f"    原型：{intent.protagonist_archetype_hint}｜基调：{intent.world_tone_hint}｜视角：{intent.narrative_voice_hint}")
    if intent.tone_summary:
        print(f"    整体气质：{intent.tone_summary[:100]}")
    if intent.analyzer_notes:
        print(f"    ⚠ 待澄清：{intent.analyzer_notes[:80]}")


# ═══════════════════════════════════════════════════════
#  格式化辅助——供 concept_pitch 注入 prompt 用
# ═══════════════════════════════════════════════════════

def format_intent_as_constraints(intent: CreativeIntent) -> str:
    """
    把 creative_intent 里已有的字段格式化成"作者明示约束"的 prompt 段。
    concept_pitch 里会注入这段，优先级在 config seeds 之上。
    空字段不输出。
    """
    if not intent.analyzed:
        return ""
    lines = ["【作者明示的创作意图（最高优先级约束，必须遵守）】"]
    if intent.raw_description:
        lines.append(f"原话：{intent.raw_description[:200]}")
    if intent.tone_summary:
        lines.append(f"整体气质：{intent.tone_summary}")

    def _add(label, value, empty_ok=False):
        if value or empty_ok:
            lines.append(f"  · {label}：{value}")

    def _add_list(label, items):
        if items:
            lines.append(f"  · {label}：{' / '.join(items)}")

    _add("题材", intent.suggested_genre)
    _add("读者群", f"{intent.audience_hint}｜{intent.platform_hint}｜{intent.age_group_hint}")
    _add_list("核心卖点", intent.selling_points_hints)
    _add_list("对标作品", intent.benchmark_hints)
    _add("差异化点", intent.differentiation_hint)
    _add_list("要拥抱的套路", intent.embrace_tropes_hints)
    _add_list("要规避的烂梗", intent.avoid_tropes_hints)
    _add_list("爽点偏好", intent.preferred_sp_types_hints)
    _add("反派处理", intent.villain_policy_hint)
    _add("感情线", intent.romance_policy_hint)
    _add("后宫", intent.harem_policy_hint)
    _add("主角原型", intent.protagonist_archetype_hint)
    _add("世界基调", intent.world_tone_hint)
    _add("叙述视角", intent.narrative_voice_hint)
    _add("笔触参考", intent.style_reference_hint)
    _add("对话风格", intent.dialogue_style_hint)
    return "\n".join(lines)
