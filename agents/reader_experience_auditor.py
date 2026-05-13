"""
读者视角章后审计 —— 模拟挑剔的网文读者来审视"我想不想看下一章"。

跟现有审计的区别：
  · critic.py               = 文学质量 / 作者视角
  · continuity_checker      = 硬事实一致性 / 编辑视角
  · voice_consistency       = 角色说话像不像他 / 编辑视角
  · ability_auditor         = 金手指设定合规 / 规则视角
  · reader_experience       = 读者会不会弃书 / **读者视角** ← 本模块

读者不关心你的伏笔设计多精巧，他只关心：
  - 这章有没有让他代入主角
  - 这章有没有让他下一章还想看
  - 这章有没有让他觉得"套路了""拖了""没新意"
  - 这章有没有让他被信息过载、人物认不全、名词记不住

本 auditor 的 prompt 是全系统里最重要的 prompt 之一——
它决定了我们从"写得文学味浓"进化到"真能让读者追更"。
"""
from __future__ import annotations
from typing import Optional

from state import NovelState, ReaderExperienceAudit, ReaderExperienceIssue


# ═══════════════════════════════════════════════════════════════
#  SYSTEM 模板（模块级常量，供 prompts_registry 覆盖）
# ═══════════════════════════════════════════════════════════════

SYSTEM_TEMPLATE = """你是一位{genre}网文的**资深老读者**——不是编辑、不是作者、不是评论家。你每天在起点/番茄/飞卢打滚，对"什么时候想弃书"的肌肉记忆非常敏锐。

你的任务是读一章，**从一个挑剔读者的角度**审视它——不关心文学性，只关心：
1. 这章让我代入了吗？（主角的困境/痛点/渴望有没有打动我）
2. 这章让我还想看下一章吗？（钩子强不强、悬念有没有欠账）
3. 这章有没有让我出戏？（信息过载、说教、套路重复、节奏拖沓、人物脸谱）
4. 如果我是新读者（没看过前面章节），我能跟上吗？
5. 如果我是老读者（追了 30 章），这章让我觉得"还是那套"吗？

---

## 审计维度（每个 1-10 分，10 分 = 读者层面没毛病；1 分 = 读不下去）

### 一、new_info_density（信息密度）
- **满分场景**：本章引入 2-3 个新要素（新人物/新地点/新组织/新设定），每个都有"被读者记住"的画面（长相/口音/标志性动作/独特气味）
- **扣分项**：
  - 一章塞 5+ 个新名词且都是白描（"他叫X，来自Y，属于Z"式报菜名）→ -3
  - 塞大段世界观设定 Infobump（"我们这个世界是这样的...blabla 500字"）→ -2
  - 人名/地名相似难区分（比如"沈砚 vs 林砚"同时登场没区分）→ -2
  - 新设定后没有立刻示范它怎么用（读者抽象记不住）→ -1

### 二、emotional_anchor（代入深度）
- **满分场景**：开章 3 段内让读者为主角"心疼/紧张/愤懑/共鸣"之一
- **扣分项**：
  - 主角全程冷静理性在执行计划，读者不知道他"在意什么"→ -3
  - 主角没有可感知的痛点/未了心愿/具体困境 → -3
  - 主角动作前没有内心戏——行为像工具不像人 → -2
  - 配角全是功能人（信使/对手/工具），没有自己的情感参与 → -1

### 三、hook_strength（钩子强度）
- **满分场景**：章末最后一句让人不翻下一章难受——未完的动作、突然的反转、大冲击的信息、一个悬而未决的画面
- **扣分项**：
  - 章末是"平淡收束"（"今天就这样结束了"式）→ -4
  - 钩子是纯标签式（"一场巨变即将到来"）而没有具体画面 → -3
  - 钩子跟本章主线毫无关联（突然换线）→ -2
  - 连续 3 章都是同类钩子（都是"此时忽听一声"）→ -2

### 四、novelty（新奇度）
- **满分场景**：本章有一个读者"以前没在网文里见过/没料到"的角度——人物反应反套路、情节方向意外、画面独特
- **扣分项**：
  - 本书已经第 N 次这样打脸 → -2 到 -4（看频次）
  - 情节发展完全符合"标准网文模板"（上一秒被嘲讽下一秒打脸）→ -2
  - 人物行为完全"典型化"（反派必癫狂主角必冷静）→ -1

### 五、satisfaction_balance（爽苦平衡）
- **满分场景**：本章在"最近 3-5 章的积累"里处在节奏合理的位置——苦了就该还、爽了就该铺垫代价
- **扣分项**：
  - 连续 4+ 章都是爽（没有张力松紧）→ -3（读者审美疲劳）
  - 连续 4+ 章都是压抑（没有喘息）→ -3（读者情绪透支）
  - 本章爽点强度和前面铺垫不匹配（铺垫很少却爆了巨爽 = 轻飘；铺垫很久却只给小爽 = 亏钱）→ -2
  - 爽点触发没有"代价描写"（主角连轴转不喘气 = 读者不信）→ -1

### 六、fluency（流畅度）
- **满分场景**：读者从头到尾没有"卡顿"——不需要回看确认角色、不需要跳过大段、不会觉得在读说明书
- **扣分项**：
  - 出现大段主角自说自话解释设定/历史（说教）→ -3
  - 出现不必要的技术性细节堆砌（"这个功法需要先汇聚丹田气 → 然后 → blabla"）→ -2
  - 场景切换跳跃（上一幕在山上下一幕突然在城里没过渡）→ -2
  - 角色对话不分辨身份（小厮和天王说话一个调调）→ -2

### 七、empathy_depth（情感深度）
- **满分场景**：读者读完有"跟主角一起难受/一起爽了/一起紧张了"的感觉
- **扣分项**：
  - 冲突场面像在看棋局（旁观感强，没有被卷入）→ -3
  - 感情戏冷淡（"他亲了她""她哭了"式描写，读者没被戳到）→ -3
  - 反派被打倒时读者没有"痛快感"（没铺垫对手的可恨）→ -2

---

## 综合判断

### retention_estimate（0-100）
这是个**新读者**（假设没看过前面章节）仅凭读完本章，估计他**会不会继续读下一章**的概率。参考：
- 90+ = 追起了，主动想看下一章
- 70-89 = 愿意继续，兴趣稳定
- 50-69 = 可继续可放弃，悬在弃书边缘
- 30-49 = 很可能下次不打开
- 0-29 = 这章就弃

### dropout_risk_points
列出本章**具体哪些地方**读者容易弃（每个 20-30 字，定位到段落级）——例如：
- "开头 500 字全是冷冷的决策推演，新读者无代入点"
- "中段 3 段连续解释设定，阅读拖沓"
- "结尾钩子过于抽象，没有具体画面"

### overall_score（1-10）
综合评分——本章总体上"读者会不会订阅/追更"的意愿分。不是 7 个维度的算术平均，而是**加权**——代入感和钩子强度是命脉，其他是辅助。

---

## 输出格式（严格 JSON，不要任何解释文字）

{{
  "new_info_density": 8,
  "emotional_anchor": 6,
  "hook_strength": 7,
  "novelty": 7,
  "satisfaction_balance": 8,
  "fluency": 8,
  "empathy_depth": 6,
  "retention_estimate": 72,
  "dropout_risk_points": ["具体位置 A", "具体位置 B"],
  "issues": [
    {{"type": "info_overload|character_dump|premature_power|hook_weak|novelty_repeat|satisfaction_fatigue|suspense_debt|pacing_drag|empathy_missing|other",
      "severity": "minor|major|critical",
      "description": "读者视角的描述（60字）",
      "suggested_fix": "改进方向（40字）"}}
  ],
  "overall_score": 7,
  "summary": "一句话总评（40字）"
}}

【硬要求】
- dropout_risk_points 如果没有，给空数组 []；有就必须具体到"第几段/哪个场景"
- issues 数组里只放"确实扣分"的条目——不要为了凑数硬找问题
- severity 判断标准：
  - critical = 这条问题足以让读者弃书
  - major = 读者会注意到并感到失望，但可能继续
  - minor = 瑕疵，读者基本察觉不到"""


# ═══════════════════════════════════════════════════════════════
#  上下文拼装 —— 读者视角审计需要的辅助信息
# ═══════════════════════════════════════════════════════════════

def _format_recent_context(state: NovelState, chapter_index: int, max_prev: int = 4) -> str:
    """给 auditor 提供"前 N 章的压缩上下文"——让它能判断套路重复/爽点通胀/悬念欠账。"""
    parts = []

    # 1) 本章前最多 4 章的摘要（题目 + 张力 + 关键事件 + 结尾钩子）
    past = sorted(
        [c for c in (state.completed_chapters or []) if c.index < chapter_index],
        key=lambda c: c.index,
    )[-max_prev:]
    if past:
        lines = []
        for c in past:
            tension = getattr(c.tension, "value", str(c.tension))
            events = " / ".join((c.key_events or [])[:2])
            hook = (c.closing_hook or "")[:40]
            lines.append(
                f"  Ch{c.index}《{c.title}》[{tension}] 事件：{events or '无'}"
                + (f" | 钩子：{hook}" if hook else "")
            )
        parts.append("【最近 " + str(len(past)) + " 章摘要（用于判断是否套路重复/节奏失衡）】\n" + "\n".join(lines))

    # 2) 最近章的读者审计历史（如有）—— 让 auditor 能看到"读者的累积情绪"
    recent_audits = []
    audits = state.reader_audits or {}
    for ch_i in sorted(audits.keys()):
        if ch_i >= chapter_index:
            continue
        recent_audits.append(audits[ch_i])
    recent_audits = recent_audits[-max_prev:]
    if recent_audits:
        lines = []
        for a in recent_audits:
            lines.append(
                f"  Ch{a.chapter_index} score={a.overall_score} retention={a.retention_estimate} "
                f"| {a.summary[:40]}"
            )
        parts.append("【最近 " + str(len(recent_audits)) + " 章读者审计回顾】\n" + "\n".join(lines))

    # 3) 本书已注册的"套路/爽点类型"——用于判断重复
    sp_types = set()
    for sp in (state.satisfaction_points or []):
        if getattr(sp, "sp_type", None):
            sp_types.add(getattr(sp.sp_type, "value", str(sp.sp_type)))
    if sp_types:
        parts.append(f"【本书已规划的爽点类型】{' / '.join(sorted(sp_types))}")

    # 4) 本章前的主角境界/位置快照
    prot = next(
        (c for c in (state.characters or [])
         if getattr(c.role, "value", str(c.role)) == "主角"),
        None,
    )
    if prot:
        snap = state.latest_state_snapshot(prot.name) if hasattr(state, "latest_state_snapshot") else None
        if snap and snap.chapter_index < chapter_index:
            parts.append(
                f"【主角本章前状态（第{snap.chapter_index}章末）】"
                f"{prot.name}: {snap.location or '?'}, {snap.emotion or '?'}"
            )

    return "\n\n".join(parts) if parts else "（本章为开篇/前面无历史，只按本章内容评估）"


def _format_book_context(state: NovelState) -> str:
    """给 auditor 本书的目标定位——让它知道这是什么类型的网文，评审标准要适配。"""
    ci = getattr(state, "creative_intent", None)
    if not ci:
        return ""
    parts = []
    parts.append(f"书名：《{state.title}》 | 题材：{state.genre}")
    if getattr(ci, "suggested_subgenre", ""):
        parts.append(f"子类型：{ci.suggested_subgenre}")
    if getattr(ci, "platform_hint", ""):
        parts.append(f"目标平台：{ci.platform_hint}")
    if getattr(ci, "audience_hint", ""):
        parts.append(f"目标读者：{ci.audience_hint}")
    if getattr(ci, "tone_summary", ""):
        parts.append(f"整体基调：{ci.tone_summary[:120]}")
    return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════
#  主函数
# ═══════════════════════════════════════════════════════════════

def audit_chapter(
    state: NovelState,
    chapter_index: int,
    chapter_text: str,
    *,
    max_retries: int = 2,
) -> Optional[ReaderExperienceAudit]:
    """
    从读者视角审一章。
    返回 ReaderExperienceAudit 或 None（失败静默跳过，不阻塞写作）。
    """
    from json_utils import run_chapter_audit

    book_ctx = _format_book_context(state)
    recent_ctx = _format_recent_context(state, chapter_index)

    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "网文")
    user = (
        f"═══ 审计目标：第 {chapter_index} 章 ═══\n\n"
        f"【本书定位】\n{book_ctx}\n\n"
        f"{recent_ctx}\n\n"
        f"═══ 本章正文 ═══\n{chapter_text}\n\n"
        f"严格按 SYSTEM 的 JSON schema 输出。"
    )

    result = run_chapter_audit(
        chapter_index=chapter_index,
        chapter_text=chapter_text,
        system=system, user=user,
        required_keys=[
            "new_info_density", "emotional_anchor", "hook_strength",
            "novelty", "satisfaction_balance", "fluency", "empathy_depth",
            "retention_estimate", "overall_score", "summary",
        ],
        agent_label="ReaderAuditor",
        temperature=0.35,
        max_retries=max_retries,
    )
    if result is None:
        return None
    data, ts, profile_id = result

    def _clamp(v, lo=1, hi=10, default=8):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    issues: list[ReaderExperienceIssue] = []
    for i in (data.get("issues") or []):
        if not isinstance(i, dict):
            continue
        issues.append(ReaderExperienceIssue(
            type=str(i.get("type", "other"))[:30],
            severity=str(i.get("severity", "minor"))[:16],
            description=str(i.get("description", ""))[:200],
            suggested_fix=str(i.get("suggested_fix", ""))[:150],
        ))

    # retention_estimate 是 0-100
    try:
        retention = int(data.get("retention_estimate", 80))
    except (TypeError, ValueError):
        retention = 80
    retention = max(0, min(100, retention))

    # dropout_risk_points 必须是字符串列表
    risks: list[str] = []
    for r in (data.get("dropout_risk_points") or []):
        if isinstance(r, str) and r.strip():
            risks.append(r.strip()[:120])

    return ReaderExperienceAudit(
        chapter_index=chapter_index,
        new_info_density=_clamp(data.get("new_info_density")),
        emotional_anchor=_clamp(data.get("emotional_anchor")),
        hook_strength=_clamp(data.get("hook_strength")),
        novelty=_clamp(data.get("novelty")),
        satisfaction_balance=_clamp(data.get("satisfaction_balance")),
        fluency=_clamp(data.get("fluency")),
        empathy_depth=_clamp(data.get("empathy_depth")),
        retention_estimate=retention,
        dropout_risk_points=risks,
        issues=issues,
        overall_score=_clamp(data.get("overall_score")),
        summary=str(data.get("summary", ""))[:150],
        ts=ts,
        auditor_model=profile_id,
    )
