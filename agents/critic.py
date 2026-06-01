"""
CriticAgent — 章节审校。

═══ 设计原则（2026-05-20 重构）═══

按 [用户指导] "一个 agent 不要做太多事，防止一次性塞太多东西"——
原来 1 次 LLM 调用打 10 维度评分（prompt 600+ 字、LLM 注意力散、每维评分粗糙）
拆成 3 个聚焦小调用，并发跑：

  · _review_story_level     叙事/张力/角色/钩子 — 4 维（"读起来抓不抓人"）
  · _review_structure_level  结构/purpose/主角中心 — 3 维（"骨架对不对"）
  · _review_craft_level     细腻/戏剧/文风 + 爽点伏笔自检 — 3 维（"写得细不细"）

外层接口 `review_chapter(state, directive, content) -> dict` 不变——
调用方（director._write_one_chapter）无感知。返回结构同构合并。

每次 LLM 调用：
  · system prompt 30-80 字，只讲本类维度
  · user prompt 共享 content_sample + structure_info（少量）
  · 失败的子调用降级到 7 分中性，不阻塞其他
"""
from __future__ import annotations
from utils.json_utils import request_json
from utils.concurrency import parallel_map
from persistence.state import NovelState, ChapterDirective
from utils.context_manager import build_critic_context
from agents.concept_pitch import format_tone_brief


# ═══════════════════════════════════════════════════════════════
#  3 类聚焦 SYSTEM —— 每类只讲自己的维度，不堆其他
# ═══════════════════════════════════════════════════════════════

SYSTEM_STORY = """你是文学编辑，专注审核章节的"故事级表现"（4 维）：

1. 叙事完成度 (narrative)：主推线阶段目标和必须事件是否落地
2. 张力/节奏匹配 (tension)：文字节奏和情绪密度是否贴合声明张力/节奏
3. 角色一致性 (character)：每个角色言行是否像他自己
4. 钩子质量 (hook)：结尾是否让人想翻下一章

只看这 4 维——不要评结构 / 不评工艺细节。

输出严格 JSON。"""

SYSTEM_STRUCTURE = """你是文学编辑，专注审核章节的"结构级骨架"（3 维）：

1. 结构角色到位 (structure)：本章在所属小情节的起承转合角色是否兑现
   · 标了"转"却没转折 / "起"却没建立新东西 / "合"却没收束 → 扣分
2. purpose/expression 兑现 (purpose_expression)：读完能感受到声明的 purpose 和 expression 吗
   · 若 purpose/expression 未声明（空字符串），填 -1 跳过
3. 主角中心度 (protagonist_centric)：本章围绕主角展开吗
   · 配角是否服务主角？戏份占比合理？配角行动是否触碰主角？

只看这 3 维——不评叙事张力 / 不评工艺细节。

输出严格 JSON。"""

SYSTEM_CRAFT = """你是网文编辑，专注审核章节的"工艺级质感"（6 维 + 专项自检）：

1. 细腻度 (delicacy)：文字有没有血有肉
   · 关键情绪用感官细节 / 微表情 / 小动作承载，而不是干巴巴说"他很紧张"
   · 对话有留白、弦外之音、个性节奏
   · 场景切换有呼吸感
2. 戏剧张力 (drama)：这章让读者心跳了吗
   · 有没有让读者"啊"的反差或反转（不是每章都需要——但 purpose 声明要有却没做到=扣分）
   · 有没有让读者对主角产生具体情绪（心疼/紧张/雀跃/愤怒）的瞬间
3. 文风符合度 (tone_compliance)：视角/笔触/禁用词/对话风格是否贴合文风手册
   · 出现禁用词 = 6 分及以下
4. 打脸回响度 (callback_fidelity)：本章如有 callback_seeds (前面被嘲讽/被夺/失败的具体细节)，
   有没有精确引用具体台词或场景？
   · 精确引用原句/对场景细节 = 9-10
   · 泛泛带过同类情境 = 6-7
   · 完全没回响 = ≤5
   · 本章无 callback_seeds (一般章节)时填 -1 跳过
5. 字数合规度 (length_compliance)：网文读者通勤读章,3000-3500 字甜点区
   · 2800-3500 字 = 9-10 分(理想区间)
   · 2500-2800 或 3500-4000 = 7-8 分
   · 4000-5000 = 5-6 分(读者易疲劳)
   · >5000 或 <2500 = ≤4 分(读者大概率划走)
   · 大高潮章/卷尾章可适当放宽(≤4500),给 8-9 分
6. 钩子多样性 (hook_type_compliance)：本章钩子类型 + 本卷历史分布
   · 本卷最近 5 章如已有 ≥3 个同类钩子,本章再用同类 = ≤6 分
   · 本章钩子类型与计划一致且贴合 = 9-10 分
   · 钩子完全偏离计划 / 无钩子 = ≤5 分
   · 本章无钩子计划(开篇章/特殊章)时填 -1 跳过
7. 首段钩子力度 (opening_hook_strength) ★前 10 章硬指标★
   网文铁律: 第 1 段决定 30%+ 读者去留; 第 1 句决定 10%+ 滑走。
   只评本章前 300 字(约第 1-2 段)是否含以下至少 1 项:
     · 主角的具体当下处境(困境/羞辱/濒死/欲望/秘密)，**写到具体场景**而非概述
     · 一个有张力的对话/动作/反常细节("门外传来""房间里所有镜子都被砸碎""血从指缝渗出")
     · 一个让读者"想再看一眼"的悬念/未解
     · 主角的强情绪锚点(不甘/孤勇/不服)被一个具体瞬间承载
   · 含 2 项以上且自然 = 9-10
   · 含 1 项且有力 = 7-8
   · 含 1 项但平淡 = 5-6
   · 白描式开篇("她叫 X, 今年 Y 岁, 是一个普通的 Z") = ≤4
   · 前 10 章后(ch > 10)本项不强制 → 填 -1 跳过

外加专项自检：
  · sp_check    爽点（"到位"/"未触发"/"部分"）
  · fw_check    伏笔（"完成"/"遗漏"/"部分"）
  · highlights  亮点（段落/金句/好描写）1-3 条，无则空数组
  · issues      严重问题，无则空数组
  · feedback    像资深编辑跟作家对话——先亮点再可改进

只看这一类——不评叙事节奏 / 不评结构骨架。

输出严格 JSON。"""


# ═══════════════════════════════════════════════════════════════
#  公共：上下文构造（3 个子调用共享）
# ═══════════════════════════════════════════════════════════════

def _build_shared_context(state: NovelState, directive: ChapterDirective,
                            content: str) -> dict:
    """3 个子调用共用的上下文——一次构造，传给每个调用避免重复计算。"""
    content_sample = _sample_content(content, max_chars=3000)
    ch_role = directive.structure_role or "(未声明)"
    structure_info = (
        f"结构链：{directive.structure_chain or '(未生成)'}\n"
        f"本章角色：{ch_role}\n"
        f"本章 purpose：{directive.purpose or '(未声明)'}\n"
        f"本章 expression：{directive.expression or '(未声明)'}"
    )
    tone_block = format_tone_brief(state)
    banned_hits = [w for w in (state.tone_manual.banned_words or []) if w and w in content]
    banned_hit_report = (
        f"\n★★★ 本地扫描：本章出现禁用词 {banned_hits[:8]}（违反文风手册，必须扣分）"
        if banned_hits else ""
    )
    critic_context = build_critic_context(state, directive)

    # Batch 3:字数 + 本卷钩子分布(给 critic 评 length_compliance / hook_type_compliance)
    word_count = len(content.replace(" ", "").replace("\n", ""))
    length_info = f"本章字数: {word_count} 字 (网文标准 3000-3500 字甜点区,>4000 字读者易划走)"
    hook_planned = ""
    if directive.blueprint and directive.blueprint.closing_hook_spec:
        hook_planned = directive.blueprint.closing_hook_spec.type.value
    recent_hook_types = [
        s.closing_hook_type for s in state.completed_chapters
        if s.volume_index == directive.volume_index and s.closing_hook_type
    ][-5:]
    hook_distribution_info = (
        f"本章计划钩子类型: {hook_planned or '(未声明)'};本卷最近 5 章已用类型: "
        f"{recent_hook_types or '(无历史)'}"
    )

    # Batch 6:平台 rulebook(若已加载) —— critic 也按平台读者偏好评分
    platform_block = ""
    try:
        from utils.platform_rulebook import format_platform_block
        platform_block = format_platform_block(state)
    except Exception:
        pass

    return {
        "content_sample": content_sample,
        "structure_info": structure_info,
        "tone_block": tone_block,
        "banned_hit_report": banned_hit_report,
        "critic_context": critic_context,
        "chapter_index": directive.chapter_index,
        "length_info": length_info,
        "hook_distribution_info": hook_distribution_info,
        "platform_block": platform_block,
    }


def _shared_user_prompt(ctx: dict, extra_focus: str = "") -> str:
    """3 个子调用共享的 user prompt 头部——再加各自的 focus 说明 + JSON schema。"""
    platform_section = ""
    if ctx.get("platform_block"):
        platform_section = "\n" + ctx["platform_block"] + "\n"
    return f"""审校第 {ctx['chapter_index']} 章。
{platform_section}
═══ 本章分形结构定位 ═══
{ctx['structure_info']}

{ctx['tone_block']}{ctx['banned_hit_report']}

{ctx['critic_context']}

【章节正文（节选）】
{ctx['content_sample']}
{extra_focus}"""


# ═══════════════════════════════════════════════════════════════
#  3 个聚焦子调用
# ═══════════════════════════════════════════════════════════════

def _review_story_level(ctx: dict) -> dict:
    """故事级 4 维：叙事/张力/角色/钩子。"""
    user = _shared_user_prompt(ctx) + """

按 SYSTEM 4 个维度评分。输出 JSON：
{
  "narrative": 1-10 整数,
  "tension": 1-10 整数,
  "character": 1-10 整数,
  "hook": 1-10 整数,
  "story_feedback": "本类维度的一句话编辑反馈"
}"""
    return request_json(
        system=SYSTEM_STORY, user=user,
        required_keys=["narrative", "tension", "character", "hook"],
        max_retries=2, temperature=0.3,
        agent_name=f"Critic.story[Ch{ctx['chapter_index']}]",
        empty_ok=True,
    ) or {}


def _review_structure_level(ctx: dict) -> dict:
    """结构级 3 维：structure / purpose_expression / protagonist_centric。"""
    user = _shared_user_prompt(ctx) + """

按 SYSTEM 3 个维度评分。输出 JSON：
{
  "structure": 1-10 整数,
  "purpose_expression": 1-10 整数（未声明 purpose/expression 时填 -1）,
  "protagonist_centric": 1-10 整数,
  "structure_check": "结构角色自检（到位/偏差/缺失，一句话）",
  "protagonist_check": "主角中心度自检（到位/配角抢戏/主角失语，一句话）",
  "structure_feedback": "本类维度的一句话编辑反馈"
}"""
    return request_json(
        system=SYSTEM_STRUCTURE, user=user,
        required_keys=["structure", "protagonist_centric"],
        max_retries=2, temperature=0.3,
        agent_name=f"Critic.structure[Ch{ctx['chapter_index']}]",
        empty_ok=True,
    ) or {}


def _review_craft_level(ctx: dict) -> dict:
    """工艺级 3 维 + 专项自检：delicacy / drama / tone_compliance + sp/fw/highlights/issues/feedback。"""
    extra = f"""

═══ 字数 / 钩子分布(用于 length_compliance / hook_type_compliance 评分)═══
{ctx.get('length_info', '')}
{ctx.get('hook_distribution_info', '')}"""
    user = _shared_user_prompt(ctx, extra_focus=extra) + """

按 SYSTEM 6 个维度 + 专项自检评分。输出 JSON：
{
  "delicacy": 1-10 整数,
  "drama": 1-10 整数,
  "tone_compliance": 1-10 整数（出现禁用词必 ≤6）,
  "callback_fidelity": 1-10 整数（无 callback_seeds 时填 -1 跳过）,
  "length_compliance": 1-10 整数,
  "hook_type_compliance": 1-10 整数（无钩子计划时填 -1 跳过）,
  "opening_hook_strength": 1-10 整数（ch > 10 时填 -1 跳过, ch ≤ 10 必须评）,
  "sp_check": "爽点（到位/未触发/部分）",
  "fw_check": "伏笔（完成/遗漏/部分）",
  "highlights": ["亮点 1", "亮点 2"],
  "issues": ["严重问题（若无则空数组）"],
  "feedback": "资深编辑跟作家说话——先亮点再可改进的具体段落和方向"
}"""
    return request_json(
        system=SYSTEM_CRAFT, user=user,
        required_keys=["delicacy", "drama"],
        max_retries=2, temperature=0.3,
        agent_name=f"Critic.craft[Ch{ctx['chapter_index']}]",
        empty_ok=True,
    ) or {}


# ═══════════════════════════════════════════════════════════════
#  主入口：并发跑 3 个子调用 + 聚合
# ═══════════════════════════════════════════════════════════════

def review_chapter(state: NovelState, directive: ChapterDirective, content: str) -> dict:
    """章节审校——并发跑 3 个聚焦子调用 + 聚合到统一 dict。

    外层接口不变，调用方（director._write_one_chapter）无感知。
    """
    ctx = _build_shared_context(state, directive, content)

    # 3 个聚焦子调用并发——每个 prompt 短、专注、注意力集中
    results = parallel_map(
        fn=lambda fn: fn(ctx),
        items=[_review_story_level, _review_structure_level, _review_craft_level],
        max_workers=3,
        label=f"Critic[Ch{ctx['chapter_index']}]",
    )
    story     = results[0] if results and len(results) > 0 else None
    structure = results[1] if results and len(results) > 1 else None
    craft     = results[2] if results and len(results) > 2 else None

    # 失败统计——空 dict 或 None 都算失败（不用兜底默认值）
    failed_subs = []
    if not story:     failed_subs.append("story")
    if not structure: failed_subs.append("structure")
    if not craft:     failed_subs.append("craft")

    # **不再用默认 7 分兜底**——失败的维度就是 None，不参与综合分计算
    # 这样 caller 看到的 passed 反映真实审校结果，不会"全失败 = 默认通过"
    def _pick(d, k):
        if not d: return None
        v = d.get(k)
        try: return int(v) if v is not None else None
        except (TypeError, ValueError): return None

    dim_scores = {
        "narrative":          _pick(story, "narrative"),
        "tension":            _pick(story, "tension"),
        "character":          _pick(story, "character"),
        "hook":               _pick(story, "hook"),
        "structure":          _pick(structure, "structure"),
        "purpose_expression": _pick(structure, "purpose_expression"),
        "protagonist_centric":_pick(structure, "protagonist_centric"),
        "delicacy":           _pick(craft, "delicacy"),
        "drama":              _pick(craft, "drama"),
        "tone_compliance":    _pick(craft, "tone_compliance"),
        "callback_fidelity":  _pick(craft, "callback_fidelity"),
        "length_compliance":  _pick(craft, "length_compliance"),
        "hook_type_compliance": _pick(craft, "hook_type_compliance"),
        "opening_hook_strength": _pick(craft, "opening_hook_strength"),
    }

    # 综合分：只算非 None 且 ≥0 的维度。**没数据时返回 None / passed=False**
    valid_scores = [v for v in dim_scores.values() if v is not None and v >= 0]
    if not valid_scores:
        # 3 个子调用全失败 → critic 完全失效 → 不允许默认通过
        return {
            "passed": False,
            "score": 0,
            "review_failed": True,
            "failed_subs": failed_subs,
            "dim_scores": dim_scores,
            "sp_check": "(critic 失败)",
            "fw_check": "(critic 失败)",
            "structure_check": "(critic 失败)",
            "protagonist_check": "(critic 失败)",
            "highlights": [],
            "issues": [f"critic 全部 {len(failed_subs)} 个子调用失败：{failed_subs}——审校未跑通，不允许默认通过"],
            "feedback": f"⚠ critic 审校失败（{'/'.join(failed_subs)} 子调用都没返回）——章节未通过审校，必须重审或重写",
        }
    overall_score = round(sum(valid_scores) / len(valid_scores))

    # passed 判定：score >= 7 + 结构/主角/细腻三个 severe 维度都 ≥6（且必须真有评分，不是 None）
    severe_dims = ("structure", "protagonist_centric", "delicacy")
    structural_ok = all(
        dim_scores[k] is not None and dim_scores[k] >= 6
        for k in severe_dims
    )
    passed = (overall_score >= 7) and structural_ok and not failed_subs
    # ↑ 任一子调用失败 → 即便其他评分高也不算 passed（避免部分失败误判通过）

    # 聚合 feedback——只用成功子调用的反馈，失败的明确说
    feedback_parts = []
    if story and story.get("story_feedback"):
        feedback_parts.append("【故事】" + str(story["story_feedback"]))
    if structure and structure.get("structure_feedback"):
        feedback_parts.append("【结构】" + str(structure["structure_feedback"]))
    if craft and craft.get("feedback"):
        feedback_parts.append("【工艺】" + str(craft["feedback"]))
    if failed_subs:
        feedback_parts.append(f"⚠ 子调用失败：{failed_subs}（影响 passed 判定）")
    feedback = " | ".join(feedback_parts)

    return {
        "passed": passed,
        "score": overall_score,
        "review_failed": bool(failed_subs),  # ← 显式失败信号，让 caller 看清
        "failed_subs": failed_subs,
        "dim_scores": dim_scores,
        "sp_check":         (craft or {}).get("sp_check", "(未评)"),
        "fw_check":         (craft or {}).get("fw_check", "(未评)"),
        "structure_check":  (structure or {}).get("structure_check", "(未评)"),
        "protagonist_check":(structure or {}).get("protagonist_check", "(未评)"),
        "highlights":       (craft or {}).get("highlights") or [],
        "issues":           (craft or {}).get("issues") or [],
        "feedback":         feedback,
    }


# ═══════════════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════════════

def _sample_content(content: str, max_chars: int = 3000) -> str:
    """智能采样正文：取开头 1000 字 + 中间 500 字 + 结尾 1000 字。
    比直接截断更能让 Critic 看到钩子质量。"""
    if len(content) <= max_chars:
        return content
    head = content[:1000]
    mid_start = len(content) // 2 - 250
    mid = content[mid_start:mid_start + 500]
    tail = content[-1000:]
    return f"{head}\n\n[...中间省略...]\n\n{mid}\n\n[...省略...]\n\n{tail}"
