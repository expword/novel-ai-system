"""
CommentSimulator —— 章后模拟网文读者评论。

═══ 解决用户的诉求 ═══

真实网文作者每天看读者评论调整写法:谁夸了 / 谁骂了 / 谁猜对了。
本系统是 zero-feedback 闭门造车,reader_experience_auditor 给的是技术扣分,
不是"评论区会怎么说"。

CommentSimulator 章后扫稿,模拟 5-10 条读者评论(4 类身份),写到
ChapterSummary.simulated_comments,前端可见。下章的 expectation_manager
也可以参考这些评论(知道哪些钩子已经被读者"猜出来"了)。

═══ 4 类读者身份 ═══

· 追读派 —— 主线党,关心剧情推进/情感投入(positive 居多)
· 挑刺派 —— 逻辑党,挑设定漏洞/文笔毛病(critical)
· 路过派 —— 吐槽党,玩梗调侃(neutral)
· 章评党 —— 金句党,截图段落/夸或骂关键片段(mixed)

═══ 单章一次 LLM 调用 ═══

· 输入:本章正文 + 题材 + 主角名 + 上章末钩子(若有)
· 输出:5-10 条 SimulatedComment
· 走 'extractor' usage(轻量便宜),empty_ok=True
· 失败写 progress_warning 'chapter:N:comment_simulator'
"""
from __future__ import annotations
import re

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register
from persistence.state import NovelState, SimulatedComment


CONTRACT = register(AgentContract(
    name="comment_simulator.simulate_comments",
    inputs=[
        "characters[*].name",
        "completed_chapters[*].closing_hook",
        "concept_pitch.target_platform",
    ],
    outputs=[
        "completed_chapters[*].simulated_comments",
    ],
    invariants=[],
    notes=(
        "章后模拟 5-10 条读者评论(4 类身份),挂在 ChapterSummary 上,前端可见。"
        "失败写 'chapter:N:comment_simulator' progress_warning。"
        "绕过 [[ASK_AI:..]] 占位段。"
    ),
))


from utils.reader_personas import (
    ALL_PERSONAS as _ALL_PERSONAS,
    format_all_for_prompt as _format_all_personas,
    all_labels as _persona_labels,
)


SYSTEM = """你是网文读者评论模拟器——读完一章正文后,模拟真实读者在书评区会发什么评论。

═══ 4 类读者身份(必须覆盖至少 3 类)═══

""" + _format_all_personas() + """

═══ 每条评论格式 ═══

  reader_type   追读派 / 挑刺派 / 路过派 / 章评党 之一
  nickname      读者昵称(用真实网文读者的命名风格——网名/动物+数字/英文/古风名等,
                别用作者本名或主角名,12 字以内)
  text          评论内容(40-100 字,口语化,有真实感)
  sentiment     positive / neutral / negative / critical 之一

═══ 真实感铁律 ═══

· 不要每条都夸——挑刺派、路过派的负面/中性评论要有
· 不要把所有读者写成"高素质评论员"——可以有别字、口水话、玩梗
· 不要把评论变成剧情总结——读者只会聊**触动他们**的具体细节
· 不要替读者编造他们看不到的东西(他们没看过 state,只看本章正文)
· 章评党可以引用本章原文(15 字内片段)再点评
· 5-10 条评论,覆盖至少 3 类身份;如果本章只引发一种反应,可少到 4 条

═══ 输出严格 JSON ═══

{
  "comments": [
    {"reader_type":"追读派","nickname":"...","text":"...","sentiment":"positive"},
    ...
  ]
}"""

_PLACEHOLDER_RE = re.compile(r"\[\[ASK_AI:.*?\]\]", re.DOTALL)
_ALLOWED_TYPES = set(_persona_labels())
_ALLOWED_SENT = {"positive", "neutral", "negative", "critical"}


def simulate_comments(state: NovelState, chapter_index: int,
                       content: str) -> list[SimulatedComment]:
    """章后扫正文 → 模拟 5-10 条读者评论。

    返回 list[SimulatedComment]。失败返回空 list 并写 progress_warning。
    同时将结果写入 state.completed_chapters[*].simulated_comments。
    """
    if not content or len(content.strip()) < 200:
        return []

    text = _PLACEHOLDER_RE.sub("", content)
    snippet = text[:6000] if len(text) > 6000 else text

    protagonist = next(
        (c.name for c in state.characters
         if getattr(c.role, "value", "") == "主角"),
        "主角"
    )
    platform = ""
    try:
        platform = state.concept_pitch.target_platform or ""
    except Exception:
        pass

    # 上章末钩子(如有)——让读者评论时可以呼应"上章的悬念"
    prev_hook = ""
    if state.completed_chapters:
        prev = next(
            (s for s in state.completed_chapters if s.index == chapter_index - 1),
            None,
        )
        if prev:
            prev_hook = prev.closing_hook[:60]

    user = f"""═══ 本书元信息 ═══
题材: {state.genre or ''}
主角: {protagonist}
平台: {platform or '(未指定)'}

═══ 上章末钩子 ═══
{prev_hook or '(无)'}

═══ 第 {chapter_index} 章正文(节选) ═══
\"\"\"
{snippet}
\"\"\"

按 SYSTEM 规则生成 5-10 条读者评论。严格 JSON。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM, user=user,
            required_keys=["comments"], max_retries=2, temperature=0.7,
            agent_name=f"CommentSimulator[ch{chapter_index}]", empty_ok=True,
        )
    except Exception as _e:
        _emit_warning(chapter_index, f"模拟评论失败:{type(_e).__name__}: {_e}")
        return []

    if not data:
        return []

    comments: list[SimulatedComment] = []
    for raw in (data.get("comments") or []):
        if not isinstance(raw, dict):
            continue
        rt = str(raw.get("reader_type") or "").strip()
        if rt not in _ALLOWED_TYPES:
            rt = "路过派"
        nick = str(raw.get("nickname") or "").strip()[:24]
        body = str(raw.get("text") or "").strip()[:160]
        if not body:
            continue
        sent = str(raw.get("sentiment") or "neutral").strip().lower()
        if sent not in _ALLOWED_SENT:
            sent = "neutral"
        comments.append(SimulatedComment(
            reader_type=rt, nickname=nick or "匿名读者",
            text=body, sentiment=sent,
        ))

    # 写到 ChapterSummary
    if comments:
        summary = next(
            (s for s in state.completed_chapters if s.index == chapter_index),
            None,
        )
        if summary is not None:
            summary.simulated_comments = comments

    return comments


def _emit_warning(chapter_index: int, msg: str) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:comment_simulator",
            message=msg,
        )
    except Exception:
        pass
