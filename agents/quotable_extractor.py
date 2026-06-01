"""
QuotableExtractor —— 章后扫稿挖"金句/可截图段"。

═══ 解决的问题 ═══

读者口耳相传靠金句、可截图场景、可被截评的对白。当前系统所有 audit 都是
**挑错**(critic / canon / dialogue / reader / ...),没有一个 agent 在主动
**挖亮点**——好句子被埋没,下章 writer 也没法参考"上章哪段写得最好"。

QuotableExtractor 章后扫一遍正文,LLM 评出 3-8 段"传播价值高"的金句/场景,
写到 ChapterSummary.quotable_moments。下章写章前可选注入,告诉 writer
"上章这种调性写得好,本章相似情境继续用"。

═══ 4 类金句 ═══

· 对白金句   —— 角色台词(主角宣言/反派狠话/师徒训诫)
· 心理独白   —— 主角内心戏的精彩瞬间
· 场景描写   —— 画面感强的环境/动作段落
· 主题点睛   —— 全章主旨被一句话点破

═══ 单章一次 LLM 调用 ═══

· 输入: 本章正文 + 主角名 + chapter_type
· 输出: 3-8 段 quotable_moments
· 走 'extractor' usage(轻量便宜),empty_ok=True
· 失败写 progress_warning(chapter:N:quotable),不阻塞主流程

═══ 设计原则(按 [[feedback_generic_prompts]])═══

· prompt 通用——不硬编码具体项目术语
· 主角名 / 章型 等从 state 动态取
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="quotable_extractor.extract_from_chapter",
    inputs=[
        "characters[*].name",
        "characters[*].role",
    ],
    outputs=[
        "completed_chapters[*].quotable_moments",
    ],
    invariants=[],
    notes=(
        "章后扫稿挖 3-8 段金句/可截图场景,挂在 ChapterSummary.quotable_moments。"
        "走 extractor usage,失败写 'chapter:N:quotable' progress_warning。"
        "为下章 writer 提供'本书已有的成功调性'参考。"
    ),
))


SYSTEM = """你是【网文亮点猎人】——专精挖出可被截图传播/章评引用的金句和场景。

═══ 你的任务 ═══

读一章正文,挖出 3-8 段"如果发到论坛/微博/章评最可能被引用的内容"。

═══ 4 类金句 ═══

1. 对白金句 —— 主角宣言/反派狠话/智者点拨。短、狠、记得住
2. 心理独白 —— 主角内心戏的精彩瞬间(决断/挣扎/顿悟)
3. 场景描写 —— 画面感强、节奏控制好的环境/动作段(读者会"喔/笑/哭")
4. 主题点睛 —— 全章主旨被一两句话点破(读者画下来发朋友圈)

═══ 你的判定标准 ═══

★ 高分(8-10): 即便单独贴出来也立得住、有冲击力的段落
★ 中分(5-7): 上下文中精彩、单独看略弱
★ 低分(<5): 别选

═══ 输出格式 ═══

JSON:
{
  "quotable_moments": [
    {
      "kind": "对白|独白|场景|点睛",
      "text": "选段原文(完整摘录,不超过 80 字)",
      "reason": "为何精彩(20 字)",
      "impact_score": 1-10
    },
    ...
  ]
}

3-8 段,按 impact_score 降序。低于 5 分的不要选。"""


@dataclass
class QuotableMoment:
    kind: str          # 对白|独白|场景|点睛
    text: str          # 原文摘录(≤80 字)
    reason: str        # 为何精彩
    impact_score: int  # 1-10


def extract_from_chapter(
    state,
    chapter_index: int,
    chapter_text: str,
    *,
    chapter_type: str = "",
) -> list[QuotableMoment]:
    """
    章后扫一遍正文,返回 3-8 段 QuotableMoment(按 impact_score 降序)。
    失败返回空列表(不阻塞)。
    """
    if not chapter_text or len(chapter_text) < 100:
        return []

    proto = _get_protagonist_name(state)
    user_parts = [f"以下是第 {chapter_index} 章正文,挖出 3-8 段最'传播向'的内容。"]
    if proto:
        user_parts.append(f"主角: {proto}")
    if chapter_type:
        user_parts.append(f"章型: {chapter_type}")
    user_parts.append("")
    user_parts.append("═══ 正文 ═══")
    # 截断超长正文(避免 token 爆炸,extractor 看前 6000 字足够定调性)
    user_parts.append(chapter_text[:6000])
    user_parts.append("")
    user_parts.append("输出 JSON 严格按 schema: {\"quotable_moments\":[{\"kind\":...,\"text\":...,\"reason\":...,\"impact_score\":1-10}]}")
    user = "\n".join(user_parts)

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["quotable_moments"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return []

    if not isinstance(result, dict):
        return []
    raw = result.get("quotable_moments") or []
    if not isinstance(raw, list):
        return []

    out: list[QuotableMoment] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        text = (r.get("text") or "").strip()
        if not text or len(text) < 5:
            continue
        try:
            score = int(r.get("impact_score") or 0)
        except Exception:
            score = 0
        if score < 5:
            continue  # 低分跳过
        out.append(QuotableMoment(
            kind=(r.get("kind") or "对白").strip(),
            text=text[:200],
            reason=(r.get("reason") or "").strip()[:60],
            impact_score=max(1, min(score, 10)),
        ))
    out.sort(key=lambda x: -x.impact_score)
    return out[:8]


def attach_to_summary(summary, moments: list[QuotableMoment]) -> None:
    """把 QuotableMoment 列表附到 ChapterSummary。"""
    if not hasattr(summary, "quotable_moments"):
        return
    summary.quotable_moments = [
        {
            "kind": m.kind,
            "text": m.text,
            "reason": m.reason,
            "impact_score": m.impact_score,
        }
        for m in moments
    ]


def format_recent_for_writer(state, lookback: int = 3, top_per_chapter: int = 2) -> str:
    """
    给下章 writer 用:取最近 N 章的高分金句,作为"本书已成功的调性"参考。

    返回 prompt block 字符串(空串=无内容)。
    """
    chapters = list(getattr(state, "completed_chapters", None) or [])[-lookback:]
    if not chapters:
        return ""
    lines: list[str] = []
    for ch in chapters:
        moments = getattr(ch, "quotable_moments", None) or []
        if not moments:
            continue
        # 排序取 top_per_chapter
        sorted_m = sorted(
            (m for m in moments if isinstance(m, dict)),
            key=lambda m: -int(m.get("impact_score") or 0),
        )[:top_per_chapter]
        if not sorted_m:
            continue
        ch_idx = getattr(ch, "index", "?")
        for m in sorted_m:
            kind = m.get("kind", "")
            text = (m.get("text") or "")[:60]
            lines.append(f"  · [第{ch_idx}章·{kind}] {text}")
    if not lines:
        return ""
    return (
        "【本书已成功的调性参考——最近章节中读者最可能截图传播的段落】\n"
        + "\n".join(lines)
        + "\n（不要逐字复用,但本章相似情境可以用类似调性/节奏/句式骨架）"
    )


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _get_protagonist_name(state) -> str:
    try:
        for c in (getattr(state, "characters", None) or []):
            role_val = getattr(c.role, "value", str(c.role))
            if role_val == "主角":
                return c.name
    except Exception:
        pass
    return ""


def _surface_failure(chapter_index: int, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:quotable",
            message=f"金句提取失败,无 quotable_moments 入库: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
