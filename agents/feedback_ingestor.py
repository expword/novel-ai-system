"""
FeedbackIngestor —— 真反馈结构化翻译。

═══ 解决的问题 ═══

当前 user_feedback 是 free text,直接塞到下章 writer prompt——但 writer 不知道
"节奏太慢" 该如何具体改:
  · 应该减少描写比例?加快对话节奏?压缩场景幕数?
  · 仅本章改?还是后续都按此调整?
  · 严重到要重写,还是下章微调即可?

FeedbackIngestor 把作者自由文字反馈翻译成结构化指令:
  · scope        作用范围(本章/下章/后续/全书)
  · target_aspect 改的方向(节奏/角色/情节/对白/描写/钩子/...)
  · severity     严重程度(minor/major/critical)
  · action_for_writer / action_for_planner  具体可执行指令

输出可直接挂到 state.user_feedback_queue,director.generate_directive 取
applicable 的注入到下章 directive.must_include / forbidden_content / user_feedback。

═══ 单 LLM 调用 ═══

· 输入: 反馈原文 + 上下文(章号 / 章型 / 最近 critic 评分摘要可选)
· 输出: IngestedFeedback dict
· 走 'extractor' usage(轻量便宜)
· 失败兜底:返回 raw_text 作为 fallback action(向后兼容原 user_feedback 行为)

═══ 设计原则 ═══

· 按 [[feedback_generic_prompts]] —— prompt 通用,不针对具体项目术语
· 失败 → 不阻塞作者操作(returns fallback dict),只写 progress_warning
· 翻译后的指令优先级 = severity:
  - critical: 注入 directive.must_include + user_feedback
  - major:    注入 directive.must_include
  - minor:    注入 directive.user_feedback(参考)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="feedback_ingestor.ingest",
    inputs=[
        # 输入是作者自由文字,不需要 state 字段
    ],
    outputs=[
        # 返回 IngestedFeedback dict 给调用方(通常是 web app 写入 user_feedback_queue)
    ],
    invariants=[],
    notes=(
        "把作者自由文字反馈翻译成结构化指令(scope/target/severity/actions)。"
        "走 extractor usage,失败兜底返回 raw_text 作为 fallback action。"
    ),
))


# 可识别的 scope(作用范围)
VALID_SCOPES = {"this_chapter", "next_chapter", "next_volume", "global"}

# 可识别的 target_aspect(改的方向)
VALID_ASPECTS = {
    "rhythm",         # 节奏(快/慢/张弛)
    "character",      # 人物(性格/动机/对白口吻)
    "plot",           # 情节(走向/转折/逻辑)
    "dialogue",       # 对白(质量/数量/差异化)
    "description",    # 描写(场景/动作/感官)
    "psychology",     # 心理戏(主角内心/动机)
    "hook",           # 钩子(章末悬念/留白)
    "world",          # 世界观(设定/背景)
    "satisfaction",   # 爽点(强度/密度/铺垫)
    "foreshadow",     # 伏笔(明显度/回收)
    "length",         # 字数(过长/过短)
    "tone",           # 文风(过于学究/过于网文/口语化)
    "other",
}

VALID_SEVERITIES = {"minor", "major", "critical"}


SYSTEM = """你是【作者反馈解析器】——把作者自由文字反馈翻译成结构化指令,
让下游 chapter_planner / writer 能精确执行。

═══ 你的任务 ═══

读作者一段自然语言反馈,输出结构化 JSON。

═══ 你判定的维度 ═══

1. scope: 反馈作用范围
   · this_chapter   仅本章(立即重写本章)
   · next_chapter   下一章开始改(本章保留)
   · next_volume    下一卷开始改
   · global         全书风格调整

2. target_aspect: 反馈针对的方面
   · rhythm         节奏(快/慢/张弛失调)
   · character      人物(性格/动机/口吻)
   · plot           情节(走向/转折/逻辑)
   · dialogue       对白(质量/差异化)
   · description    描写(场景/动作/感官)
   · psychology     心理戏
   · hook           章末钩子
   · world          世界观/设定
   · satisfaction   爽点(强度/密度/铺垫)
   · foreshadow     伏笔
   · length         字数(过长/过短)
   · tone           文风
   · other          其他

3. severity: 严重程度
   · minor    微调(下章注意即可)
   · major    明显问题(下章必须改)
   · critical 重大问题(本章重写)

═══ 输出 ═══

JSON:
{
  "scope": "next_chapter",
  "target_aspect": "rhythm",
  "severity": "major",
  "summary": "30 字总结作者意思",
  "action_for_writer": "writer 具体怎么改(50 字以内,可执行)",
  "action_for_planner": "chapter_planner 具体怎么改(50 字以内,可执行)"
}"""


@dataclass
class IngestedFeedback:
    raw_text: str
    scope: str = "next_chapter"
    target_aspect: str = "other"
    severity: str = "major"
    summary: str = ""
    action_for_writer: str = ""
    action_for_planner: str = ""
    ok: bool = True  # LLM 是否成功

    def to_dict(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "scope": self.scope,
            "target_aspect": self.target_aspect,
            "severity": self.severity,
            "summary": self.summary,
            "action_for_writer": self.action_for_writer,
            "action_for_planner": self.action_for_planner,
            "ok": self.ok,
        }


def ingest(
    raw_text: str,
    *,
    current_chapter_index: Optional[int] = None,
    chapter_type: str = "",
    recent_critic_summary: str = "",
) -> IngestedFeedback:
    """
    把作者反馈翻译成结构化指令。
    失败 → 返回 fallback(把 raw_text 当作 action_for_writer)。
    """
    raw = (raw_text or "").strip()
    if not raw:
        return IngestedFeedback(raw_text="", ok=True)

    user_parts = ["作者反馈原文:"]
    user_parts.append(f"「{raw}」")
    user_parts.append("")
    if current_chapter_index is not None:
        user_parts.append(f"当前章号: 第 {current_chapter_index} 章")
    if chapter_type:
        user_parts.append(f"章型: {chapter_type}")
    if recent_critic_summary:
        user_parts.append(f"近期 critic 反馈: {recent_critic_summary[:200]}")
    user_parts.append("")
    user_parts.append(
        "输出 JSON 严格按 schema: {\"scope\":...,\"target_aspect\":...,\"severity\":...,"
        "\"summary\":...,\"action_for_writer\":...,\"action_for_planner\":...}"
    )
    user = "\n".join(user_parts)

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["scope", "target_aspect", "severity"],
            usage="extractor",
            max_attempts=2,
            empty_ok=False,
        )
    except Exception as e:
        _surface_failure(current_chapter_index, e)
        return _fallback_dict(raw)

    if not isinstance(result, dict):
        return _fallback_dict(raw)

    scope = (result.get("scope") or "next_chapter").strip()
    if scope not in VALID_SCOPES:
        scope = "next_chapter"
    aspect = (result.get("target_aspect") or "other").strip()
    if aspect not in VALID_ASPECTS:
        aspect = "other"
    severity = (result.get("severity") or "major").strip()
    if severity not in VALID_SEVERITIES:
        severity = "major"

    return IngestedFeedback(
        raw_text=raw,
        scope=scope,
        target_aspect=aspect,
        severity=severity,
        summary=(result.get("summary") or "").strip()[:80],
        action_for_writer=(result.get("action_for_writer") or "").strip()[:200],
        action_for_planner=(result.get("action_for_planner") or "").strip()[:200],
        ok=True,
    )


def enqueue(state, feedback: IngestedFeedback, target_chapter_index: int) -> None:
    """把 IngestedFeedback 加到 state.user_feedback_queue。

    queue 元素: {target_chapter_index, ingested: IngestedFeedback.to_dict(), consumed: False}
    director.generate_directive 之后会取 applicable 的注入到 directive 并 mark consumed。
    """
    if not hasattr(state, "user_feedback_queue") or state.user_feedback_queue is None:
        state.user_feedback_queue = []
    state.user_feedback_queue.append({
        "target_chapter_index": target_chapter_index,
        "ingested": feedback.to_dict(),
        "consumed": False,
    })


def apply_to_directive(state, directive) -> int:
    """
    director._generate_directive 调一次:
    把 state.user_feedback_queue 里 target 命中本章且未消化的反馈
    注入到 directive(按 severity 决定字段):
      · critical → must_include 头部 + user_feedback
      · major    → must_include 末尾
      · minor    → user_feedback(参考)
    标记 consumed=True。返回应用条数。
    """
    queue = getattr(state, "user_feedback_queue", None) or []
    if not queue:
        return 0
    ch_idx = getattr(directive, "chapter_index", -1)
    applied = 0
    for item in queue:
        if not isinstance(item, dict) or item.get("consumed"):
            continue
        target = item.get("target_chapter_index", -1)
        # scope=global 时无 target 也命中(每章都用)
        ingested = item.get("ingested") or {}
        scope = ingested.get("scope", "")
        if scope != "global" and target != ch_idx:
            continue
        # 应用
        severity = ingested.get("severity", "major")
        action_writer = (ingested.get("action_for_writer") or "").strip()
        action_planner = (ingested.get("action_for_planner") or "").strip()
        if severity == "critical":
            # 注入 must_include 头部
            if action_planner:
                directive.must_include = [f"【作者反馈·critical】{action_planner}"] + (directive.must_include or [])
            if action_writer:
                existing_fb = getattr(directive, "user_feedback", "") or ""
                directive.user_feedback = (
                    f"【critical】{action_writer}"
                    + (("\n" + existing_fb) if existing_fb else "")
                )
        elif severity == "major":
            if action_planner:
                if directive.must_include is None:
                    directive.must_include = []
                directive.must_include.append(f"【作者反馈·major】{action_planner}")
        else:  # minor
            if action_writer:
                existing_fb = getattr(directive, "user_feedback", "") or ""
                directive.user_feedback = (
                    existing_fb + ("\n" if existing_fb else "")
                    + f"【minor 参考】{action_writer}"
                )
        # global scope 不 mark consumed(每章都用),其他 scope mark
        if scope != "global":
            item["consumed"] = True
        applied += 1
    return applied


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _fallback_dict(raw_text: str) -> IngestedFeedback:
    """LLM 失败时:把 raw_text 当 action_for_writer(向后兼容原 user_feedback 行为)。"""
    return IngestedFeedback(
        raw_text=raw_text,
        scope="next_chapter",
        target_aspect="other",
        severity="major",
        summary=raw_text[:60],
        action_for_writer=raw_text,
        action_for_planner=raw_text,
        ok=False,
    )


def _surface_failure(ch_idx: Optional[int], e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        ch_str = f"chapter:{ch_idx}" if ch_idx is not None else "feedback"
        add_progress_warning(
            level="warn",
            source=f"{ch_str}:feedback_ingest",
            message=f"反馈结构化失败,fallback 用原文: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
