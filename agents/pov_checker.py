"""
POVChecker —— 主角视角守门员（章后审计）。

═══ 解决的问题 ═══

网文最常见 OOC = 主角突然知道反派背后的事 / 敌方内部消息 / 未来事件。
voice_consistency_checker 只检角色台词口吻,**主角"什么知道什么不知道"的
视角破绽**没人查——读者代入主角阅读,POV 破绽直接破代入。

POVChecker 维护 state.protagonist_known_facts(主角已知事实集合),章后:
  · 扫本章正文,识别主角说/想/推理的"超出已知集合"的内容 → progress_warning
  · 提取本章主角"新得知"的事实(被告知/亲见/推理出) → 加入 known_facts 集合

═══ 单 LLM 调用,两输出 ═══

· 输入: 本章正文 + 主角名 + 已有 known_facts 摘要
· 输出: {new_known_facts: [...], pov_violations: [...]}
· 走 'extractor' usage(轻量便宜),empty_ok=True
· 失败写 progress_warning(chapter:N:pov),不阻塞主流程

═══ 设计原则 ═══

· 累积契约:之前几章主角"未知"的事,本章突然"已知"必须有合理来源(对话/线索/推理)
· LLM 失败 → 不更新 known_facts,本章不报 violation(避免误报阻塞主流程)
· 按 [[feedback_generic_prompts]] —— prompt 通用,不硬编码项目术语
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="pov_checker.audit_chapter",
    inputs=[
        "characters[*].name",
        "characters[*].role",
        "protagonist_known_facts",
    ],
    outputs=[
        "protagonist_known_facts",  # 累积更新
        # + progress_warning(chapter:N:pov)
    ],
    invariants=[],
    notes=(
        "章后主角视角守门。维护 protagonist_known_facts 累积事实集合。"
        "本章超出集合的'主角说/做/想' → progress_warning。"
        "走 extractor usage,失败不更新事实+不报警。"
    ),
))


SYSTEM = """你是【主角视角守门员】——专精识别小说中的"POV 破绽"。

═══ 你的任务 ═══

读一章正文 + 主角【截至上一章】的已知事实清单。判定两件事:

【A】new_known_facts: 本章中主角"新得知"的事实列表(被告知/亲见/亲身经历/合理推理)
【B】pov_violations: 本章中主角说/做/想了**超出已知事实范围**的内容(读者读到会出戏)

═══ POV 破绽典型场景 ═══

· 反派在自己的房间密谋 → 主角下一章直接说出反派的密谋内容(无来源)
· 主角从未见过 X 角色,却在心里准确评价 X 的性格
· 主角不在战场,却描述战场细节如亲见
· 主角说出了别人尚未告诉他的具体信息(姓名/地点/时间)

═══ 注意排除(这些不是破绽)═══

· 常识推理(看到血迹推断有打斗 —— 合理)
· 上文已交代来源(本章前几页有线索的 —— 不算破)
· 系统/金手指赋予的信息(明确写了"系统提示" —— 不算破)
· 旁白/全知视角(若本书是全知体不算 POV 破)

═══ 输出格式 ═══

JSON:
{
  "new_known_facts": [
    {"fact": "30 字内描述主角新知的事", "source": "对话|亲见|推理|被告知"}
  ],
  "pov_violations": [
    {
      "excerpt": "正文原文摘录(30 字内)",
      "explanation": "为什么是破绽(30 字)",
      "severity": "critical|warn"
    }
  ]
}

没有就给空数组。new_known_facts 不超过 8 条。"""


@dataclass
class POVViolation:
    excerpt: str
    explanation: str
    severity: str  # critical|warn


@dataclass
class AuditResult:
    new_facts: list[dict] = field(default_factory=list)
    violations: list[POVViolation] = field(default_factory=list)
    ok: bool = True  # LLM 是否调用成功


def audit_chapter(
    state,
    chapter_index: int,
    chapter_text: str,
) -> AuditResult:
    """
    章后审一次:返回 new_facts(本章主角新知事实) + violations(本章 POV 破绽)。
    失败返回 AuditResult(ok=False)。
    """
    if not chapter_text or len(chapter_text) < 100:
        return AuditResult(ok=True)

    proto = _get_protagonist_name(state)
    if not proto:
        # 找不到主角名 —— skip(避免误报)
        return AuditResult(ok=True)

    known_summary = _format_known_facts(state)

    user_parts = [
        f"主角姓名: {proto}",
        f"主角【截至上一章】已知的事实集合(供你判定 POV 破绽):",
        known_summary or "(无既往事实记录 —— 本章为第一次审计)",
        "",
        f"以下是第 {chapter_index} 章正文,请按 schema 输出:",
        "═══ 正文 ═══",
        chapter_text[:6000],
        "",
        "输出 JSON 严格按 schema: {\"new_known_facts\":[...],\"pov_violations\":[...]}",
    ]
    user = "\n".join(user_parts)

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["new_known_facts", "pov_violations"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return AuditResult(ok=False)

    if not isinstance(result, dict):
        return AuditResult(ok=False)

    out = AuditResult(ok=True)
    raw_facts = result.get("new_known_facts") or []
    if isinstance(raw_facts, list):
        for f in raw_facts[:8]:
            if not isinstance(f, dict):
                continue
            fact = (f.get("fact") or "").strip()
            if not fact:
                continue
            out.new_facts.append({
                "fact": fact[:120],
                "source": (f.get("source") or "").strip()[:30],
                "learned_chapter": chapter_index,
            })

    raw_vios = result.get("pov_violations") or []
    if isinstance(raw_vios, list):
        for v in raw_vios:
            if not isinstance(v, dict):
                continue
            excerpt = (v.get("excerpt") or "").strip()
            if not excerpt:
                continue
            out.violations.append(POVViolation(
                excerpt=excerpt[:120],
                explanation=(v.get("explanation") or "").strip()[:120],
                severity=(v.get("severity") or "warn").strip(),
            ))

    return out


def merge_facts_into_state(state, new_facts: list[dict]) -> None:
    """把本章 new_facts 累积到 state.protagonist_known_facts(去重)。"""
    if not new_facts:
        return
    if not hasattr(state, "protagonist_known_facts"):
        state.protagonist_known_facts = []
    existing = {(f.get("fact") or "").strip() for f in state.protagonist_known_facts if isinstance(f, dict)}
    for f in new_facts:
        if not isinstance(f, dict):
            continue
        fact_key = (f.get("fact") or "").strip()
        if fact_key and fact_key not in existing:
            state.protagonist_known_facts.append(f)
            existing.add(fact_key)


def surface_violations(chapter_index: int, violations: list[POVViolation]) -> None:
    """把 violations 推到 progress_warning。"""
    if not violations:
        try:
            from persistence.checkpoint import clear_progress_warnings
            clear_progress_warnings(source=f"chapter:{chapter_index}:pov")
        except Exception:
            pass
        return
    try:
        from persistence.checkpoint import add_progress_warning
        criticals = [v for v in violations if v.severity == "critical"]
        warns = [v for v in violations if v.severity != "critical"]
        if criticals:
            msg = (
                f"主角 POV 破绽 {len(criticals)} 处 critical: "
                + " | ".join(f"「{v.excerpt[:30]}」({v.explanation[:30]})" for v in criticals[:3])
            )
            add_progress_warning(level="error", source=f"chapter:{chapter_index}:pov", message=msg)
        elif warns:
            msg = (
                f"主角 POV 破绽 {len(warns)} 处 warn: "
                + " | ".join(f"「{v.excerpt[:30]}」({v.explanation[:30]})" for v in warns[:3])
            )
            add_progress_warning(level="warn", source=f"chapter:{chapter_index}:pov", message=msg)
    except Exception:
        pass


def audit_and_apply(state, chapter_index: int, chapter_text: str) -> AuditResult:
    """
    一站式:audit + 把 new_facts 入库 + violations → progress_warning。
    供 director 章后直接调一次。
    """
    result = audit_chapter(state, chapter_index, chapter_text)
    if result.ok:
        merge_facts_into_state(state, result.new_facts)
        surface_violations(chapter_index, result.violations)
    return result


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


def _format_known_facts(state, max_facts: int = 30) -> str:
    """格式化 state.protagonist_known_facts 给 LLM 看。"""
    facts = getattr(state, "protagonist_known_facts", None) or []
    if not facts:
        return ""
    lines = []
    for f in facts[-max_facts:]:
        if not isinstance(f, dict):
            continue
        ch = f.get("learned_chapter", "?")
        fact = (f.get("fact") or "").strip()
        src = (f.get("source") or "").strip()
        if fact:
            tail = f" ({src})" if src else ""
            lines.append(f"  · [第{ch}章] {fact[:80]}{tail}")
    return "\n".join(lines)


def _surface_failure(chapter_index: int, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:pov",
            message=f"POV 检查失败,本章不更新主角已知事实+不报 violation: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
