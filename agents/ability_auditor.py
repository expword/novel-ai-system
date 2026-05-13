"""
章节能力审计 —— 检查金手指/技能使用合理性。

职责：
  · 每章写完后（或手动触发）对章节正文做一次"能力使用审计"
  · 输入：章节正文 + 金手指定义 + power_system + characters + fortunes
  · 输出：AbilityAudit（用到的能力清单 + 发现的问题 + 综合评分）

不做自动改写——只报告。用户看到报告后用 对话调整 / 重写 自己处理。
"""
from __future__ import annotations
from typing import Optional

from state import NovelState, AbilityAudit, AbilityUse, AbilityIssue


# ═══════════════════════════════════════════════════════════════
#  SYSTEM 模板（模块级常量，供 prompts_registry 覆盖）
# ═══════════════════════════════════════════════════════════════

SYSTEM_TEMPLATE = """你是{genre}小说的能力设定审计员。你的职责是读一章正文，核对主角在本章里使用金手指/技能时是否符合设定、是否合理。

审计维度：

一、【金手指/技能使用清单】
逐一列出本章主角（或其他主要角色）使用了哪些金手指或特殊能力。每次使用记录：
- 能力名称（设定里叫什么）
- 怎么用的（一句话）
- 付了什么代价（冷却/消耗/副作用；设定要求付但没付 → 重点标记）
- 是否在设定边界内（能力做的事在设定允许范围吗）

二、【问题诊断】
找出以下典型问题：
- overuse：本章使用次数/强度超出设定节奏（比如"一天一次"连用三次）
- overreach：能力做的事超出设定边界（系统本来只能 A，结果做了 B）
- no_cost：该付代价的地方没付
- scale_mismatch：展现的威力/境界跟主角当前阶段不匹配，又没合理铺垫
- underuse：设定该用的地方主角没想到用（比如金手指能解的困境，主角却手足无措）
- over_dependence：主角完全靠金手指，没有自己的判断/挣扎/付出
- reaction_missing：对手/配角看到金手指效果的反应跟他们的认知水平不匹配
- other：其他合理性问题

每条问题标：
- type（上面枚举之一）
- severity（minor 小问题/major 值得改/critical 必须改）
- description（60字）
- suggested_fix（40字建议方向；不是要你替作者改）

三、【综合评分】
- 10 = 金手指使用完美符合设定且有戏剧张力
- 7-9 = 小问题，瑕不掩瑜
- 4-6 = 中等问题，读者会出戏
- 1-3 = 设定崩坏或金手指失控

严格 JSON 输出，不要任何额外文字：
{{
  "ability_uses": [
    {{"ability_name": "...", "how_used": "...", "cost_paid": "...", "setting_match": true, "notes": ""}}
  ],
  "issues": [
    {{"type": "overuse|overreach|no_cost|scale_mismatch|underuse|over_dependence|reaction_missing|other",
      "severity": "minor|major|critical",
      "description": "...",
      "suggested_fix": "..."}}
  ],
  "overall_score": 8,
  "summary": "一句话总结（30字）"
}}

若本章没有任何金手指/技能使用，ability_uses 和 issues 都为空数组，overall_score=10，summary="本章无能力使用"。"""


# ═══════════════════════════════════════════════════════════════
#  上下文拼装
# ═══════════════════════════════════════════════════════════════

def _format_gold_finger(state: NovelState) -> str:
    """
    把金手指/设定/power_system 相关信息拼成给 auditor 的"设定参照"。
    """
    parts = []

    # 立项层面：creative_intent 里往往有详尽的金手指描述
    ci = getattr(state, "creative_intent", None)
    if ci:
        desc = (getattr(ci, "raw_description", "") or "").strip()
        if desc:
            parts.append("═══ 作者原始设定（含金手指细节）═══\n" + desc[:2500])
        ta = (getattr(ci, "suggested_theme", "") or "").strip()
        if ta:
            parts.append("【主题】" + ta[:400])
        tone = (getattr(ci, "tone_summary", "") or "").strip()
        if tone:
            parts.append("【基调】" + tone[:300])

    # 力量体系
    ps = getattr(state, "power_system", None)
    if ps:
        try:
            from dataclasses import asdict
            ps_d = asdict(ps) if hasattr(ps, "__dataclass_fields__") else {}
            # 只列境界/能力名称即可
            realms = ps_d.get("realms") or []
            if realms:
                names = [r.get("name") or r.get("realm") or str(r) for r in realms][:20]
                parts.append("【力量体系境界】" + " → ".join(str(n) for n in names))
            abilities = ps_d.get("abilities") or ps_d.get("special_abilities") or []
            if abilities:
                parts.append(f"【特殊能力设定】共 {len(abilities)} 条，示例："
                             + "; ".join(str(a.get('name', a) if isinstance(a, dict) else a)
                                         for a in abilities[:6]))
        except Exception:
            pass

    # 机缘（获得的特殊能力）
    fortunes = getattr(state, "fortunes", []) or []
    if fortunes:
        fs = []
        for f in fortunes[:10]:
            if getattr(f, "obtained", False):
                fs.append(f"· {f.name}：{f.effect_on_growth[:50]}（已于第{f.actual_chapter}章获得）")
            else:
                fs.append(f"· {f.name}（未获得，计划第{f.target_chapter}章）")
        if fs:
            parts.append("【机缘/特殊能力】\n" + "\n".join(fs))

    # 主角 signature_mannerisms / abilities（若有）
    prot = None
    for c in (getattr(state, "characters", None) or []):
        r = getattr(c, "role", None)
        r_val = getattr(r, "value", r)
        if r_val == "主角":
            prot = c
            break
    if prot:
        lines = [f"【主角·{prot.name}】"]
        if getattr(prot, "realm", ""):
            lines.append(f"  当前境界：{prot.realm}")
        if getattr(prot, "abilities", None):
            abs_ = prot.abilities[:6] if isinstance(prot.abilities, list) else [str(prot.abilities)]
            lines.append("  掌握能力：" + "; ".join(str(a) for a in abs_))
        parts.append("\n".join(lines))

    if not parts:
        return "（state 里暂未明确记录金手指/力量体系 —— 按章节正文隐含的设定审计）"
    return "\n\n".join(parts)


def _format_character_state(state: NovelState, chapter_index: int) -> str:
    """主角本章前的最近状态快照（境界/装备等）——用于判断"越级"问题。"""
    prot = None
    for c in (getattr(state, "characters", None) or []):
        r = getattr(c, "role", None)
        if getattr(r, "value", r) == "主角":
            prot = c; break
    if not prot:
        return ""
    snaps = state.character_state_history.get(prot.name, []) or []
    # 取本章之前最近的快照
    past = [s for s in snaps if getattr(s, "chapter_index", 0) < chapter_index]
    if not past:
        return ""
    latest = past[-1]
    parts = [f"【主角本章前状态（第{latest.chapter_index}章末）】"]
    for k in ("realm", "location", "injury", "emotion"):
        v = getattr(latest, k, "") or ""
        if v:
            parts.append(f"  {k}：{v}")
    items = getattr(latest, "items_on_hand", []) or []
    if items:
        parts.append("  手头物品：" + "、".join(str(i) for i in items[:6]))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
#  主函数
# ═══════════════════════════════════════════════════════════════

def audit_chapter(
    state: NovelState,
    chapter_index: int,
    chapter_text: str,
    *,
    max_retries: int = 2,
) -> Optional[AbilityAudit]:
    """
    对一章做能力审计。
    返回 AbilityAudit；失败返回 None（调用方静默跳过）。
    不抛异常——这是 nice-to-have 的增强，不能阻塞写作。
    """
    from json_utils import run_chapter_audit

    settings_block = _format_gold_finger(state)
    character_block = _format_character_state(state, chapter_index)

    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "")
    user = (
        f"═══ 审计目标：第 {chapter_index} 章 ═══\n\n"
        f"{settings_block}\n\n"
        f"{character_block}\n\n"
        f"═══ 章节正文 ═══\n{chapter_text}\n\n"
        f"按 SYSTEM 要求输出 JSON。"
    )

    result = run_chapter_audit(
        chapter_index=chapter_index,
        chapter_text=chapter_text,
        system=system, user=user,
        required_keys=["ability_uses", "issues", "overall_score", "summary"],
        agent_label="AbilityAuditor",
        temperature=0.4,
        max_retries=max_retries,
    )
    if result is None:
        return None
    data, ts, profile_id = result

    uses = []
    for u in data.get("ability_uses", []) or []:
        if not isinstance(u, dict):
            continue
        uses.append(AbilityUse(
            ability_name=str(u.get("ability_name", ""))[:60],
            how_used=str(u.get("how_used", ""))[:200],
            cost_paid=str(u.get("cost_paid", ""))[:80],
            setting_match=bool(u.get("setting_match", True)),
            notes=str(u.get("notes", ""))[:120],
        ))

    issues = []
    for i in data.get("issues", []) or []:
        if not isinstance(i, dict):
            continue
        issues.append(AbilityIssue(
            type=str(i.get("type", "other"))[:30],
            severity=str(i.get("severity", "minor"))[:16],
            description=str(i.get("description", ""))[:200],
            suggested_fix=str(i.get("suggested_fix", ""))[:150],
        ))

    try:
        score = int(data.get("overall_score", 10))
    except (TypeError, ValueError):
        score = 10
    score = max(1, min(10, score))

    return AbilityAudit(
        chapter_index=chapter_index,
        ability_uses=uses,
        issues=issues,
        overall_score=score,
        summary=str(data.get("summary", ""))[:120],
        ts=ts,
        auditor_model=profile_id,
    )
