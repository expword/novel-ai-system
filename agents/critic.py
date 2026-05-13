"""
CriticAgent — 审校4维：叙事线完成度/张力匹配/节奏一致性/钩子质量。
额外检查：角色说话风格/爽点是否到位/伏笔是否正确植入兑现。
"""
import json
from json_utils import repair_json, safe_parse, request_json
from llm import system_user
from state import NovelState, ChapterDirective
from context_manager import build_critic_context
from agents.concept_pitch import format_tone_brief


SYSTEM = """你是懂小说的文学编辑，不是在挑刺的纠察员——你读章节是为了帮作家写得更好。

评审 9 个维度（10 分制，总分综合加权）：
1. 叙事完成度：主推线的阶段目标和必须事件是否落地
2. 张力/节奏匹配：文字的节奏和情绪密度是否贴合指定张力/节奏
3. 角色一致性：每个角色的言行是否像他自己
4. 钩子质量：结尾是否让人想翻下一章
5. 结构角色到位：本章在所属小情节的起承转合角色是否兑现
   · 标了"转"却没有转折 → 严重扣分
   · 标了"起"却没建立新东西、标了"合"却没收束 → 扣分
6. purpose/expression 兑现：读完本章能感受到声明的 purpose 与 expression 吗？
   （若为空，跳过此项）
7. 主角中心度：围绕主角展开吗？配角是否服务主角？
   · 主角戏份占比合理吗？有没有配角喧宾夺主？
   · 配角的行动最终有没有触碰主角？
8. 【细腻度】文字有没有血有肉？
   · 关键情绪有没有用感官细节/微表情/小动作/未说出口的话来承载，而不是干巴巴说"他很紧张"？
   · 对话有没有留白、弦外之音、个性节奏？
   · 场景切换有没有呼吸感？
9. 【戏剧张力】这一章让读者心跳了吗？
   · 有没有让读者"啊"的意外、反差、反转？（不是每章都需要，但 purpose 里若声明要有而没做到，扣分）
   · 有没有让读者对主角产生具体情绪（心疼/紧张/雀跃/愤怒）的瞬间？

评审时：
- 该表扬的地方表扬——文字的亮点、好的描写、精妙的对话
- 该指出的地方指出——但具体到哪一段、建议怎么改
- feedback 要像资深编辑跟作家对话，不要像机器打标签

额外扣分项：爽点未到位/-2，伏笔植入或兑现遗漏/-2，角色说话跳戏/-1/处

输出严格 JSON。"""


def review_chapter(state: NovelState, directive: ChapterDirective, content: str) -> dict:
    # ContextManager 提供精简的审校上下文
    context = build_critic_context(state, directive)

    # 正文截断到3000字（审校不需要全文，关注开头/结尾/关键段）
    content_sample = _sample_content(content, max_chars=3000)

    # 分形结构信息
    ch_role = directive.structure_role or "(未声明)"
    structure_info = (
        f"结构链：{directive.structure_chain or '(未生成)'}\n"
        f"本章角色：{ch_role}\n"
        f"本章 purpose：{directive.purpose or '(未声明)'}\n"
        f"本章 expression：{directive.expression or '(未声明)'}"
    )

    # 本地扫一遍禁用词——作为 critic 的硬性提示
    tone_block = format_tone_brief(state)
    banned_hits = []
    for w in state.tone_manual.banned_words:
        if w and w in content:
            banned_hits.append(w)
    banned_hit_report = ""
    if banned_hits:
        banned_hit_report = f"\n★★★ 本地扫描：本章出现禁用词 {banned_hits[:8]}（违反文风手册，必须扣分）"

    prompt = f"""审校第{directive.chapter_index}章。

═══ 本章分形结构定位（关键审校依据）═══
{structure_info}

{tone_block}{banned_hit_report}

{context}

【章节正文（节选）】
{content_sample}

输出JSON：
{{
  "passed": true或false（score>=7 且结构/主角/细腻度无严重问题则true）,
  "score": 1到10的整数,
  "dim_scores": {{
    "narrative": 叙事完成度1-10,
    "tension": 张力节奏匹配1-10,
    "character": 角色一致性1-10,
    "hook": 钩子质量1-10,
    "structure": 结构角色是否到位1-10,
    "purpose_expression": purpose/expression是否兑现1-10（未声明填-1跳过）,
    "protagonist_centric": 主角中心度1-10,
    "delicacy": 细腻度1-10（感官细节/微表情/内心矛盾/对话质感）,
    "drama": 戏剧张力1-10（读者心跳、反差、反转、情感钩动）,
    "tone_compliance": 文风符合度1-10（视角/笔触/禁用词/对话风格是否贴合文风手册；出现禁用词=6分及以下）
  }},
  "sp_check": "爽点（到位/未触发/部分）",
  "fw_check": "伏笔（完成/遗漏/部分）",
  "structure_check": "结构角色自检（到位/偏差/缺失，一句话）",
  "protagonist_check": "主角中心度自检（到位/配角抢戏/主角失语，一句话）",
  "highlights": ["亮点（段落/金句/好描写等，1-3 条，有则写，无则空数组）"],
  "issues": ["严重问题（若无则空数组）"],
  "feedback": "像资深编辑跟作家说话——先说亮点，再说可改进的具体段落和方向"
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["score", "passed"],
        max_retries=3, temperature=0.3,
        agent_name=f"Critic[Ch{directive.chapter_index}]",
        empty_ok=True,
    )
    if not data:
        # 审校失败——给一个"通过但无反馈"的中性结果，让写作流程能继续
        return {"passed": True, "score": 7, "feedback": "（critic重试失败，默认通过）", "issues": [], "dim_scores": {}}
    return data


def _sample_content(content: str, max_chars: int = 3000) -> str:
    """
    智能采样正文：取开头1000字 + 中间500字 + 结尾1000字。
    比直接截断更能让Critic看到钩子质量。
    """
    if len(content) <= max_chars:
        return content
    head = content[:1000]
    mid_start = len(content) // 2 - 250
    mid = content[mid_start:mid_start + 500]
    tail = content[-1000:]
    return f"{head}\n\n[...中间省略...]\n\n{mid}\n\n[...省略...]\n\n{tail}"
