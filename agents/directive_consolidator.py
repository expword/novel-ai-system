"""
DirectiveConsolidator —— 写章前枢纽：把 N 个 hint 合并成 1 个优先级化 brief。

═══ 解决的问题 ═══

ChapterDirective 有 30+ 字段（must_include / forbidden / reader_expectations /
callback_seeds / twist_reveals / red_herrings / user_inspiration / user_feedback /
foreshadow_plant/resolve / character_states / structure_role / purpose / ...）。
writer 拿到全部平铺字段时：
  · 没有优先级——LLM 不知道哪个最重要
  · 没有冲突解决——节奏=缓冲 + 爽点=触发，writer 自己猜
  · 注意力分散——30+ 字段超 LLM 注意力容量

DirectiveConsolidator 用纯规则把所有 hint 重新组织为 1 个 ConsolidatedBrief：
  · core_goal: 单一北极星目标（1 句话）
  · p0_must:  硬约束（不可妥协）
  · p1_should: 软约束（推荐做到）
  · p2_style:  风格指针（节奏/张力/钩子类型）
  · p3_forbidden: 禁忌
  · conflicts_log: 解决了哪些 hint 冲突（透明日志）

writer prompt 顶部插这段 brief（高于 priority_contract），原 30+ 字段保留供细节查阅
但不再是 writer 的入口。

═══ 纯规则实现（不调 LLM）═══

避免 LLM-eval-LLM 的回声房。按确定性优先级链推导核心目标和冲突解决。
失败兜底→空 brief，writer 走原路径不阻塞。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from persistence.state import ChapterDirective, NovelState
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="directive_consolidator.consolidate",
    inputs=[
        # ChapterDirective 自身字段——consolidator 只读 directive,不读 state 字段
        # （state 仅用于 satisfaction/foreshadow lookup 取 title/description）
        "satisfaction_points[*].sp_id",
        "satisfaction_points[*].title",
        "foreshadow_items[*].fw_id",
    ],
    outputs=[
        # 不直接写 state——返回 ConsolidatedBrief 供 director 挂在 directive 上
    ],
    invariants=[],
    notes=(
        "纯规则合并 ChapterDirective 的 30+ 字段为单一优先级化 brief。"
        "写章前 director._generate_directive 末尾调用,挂在 directive.consolidated_brief。"
        "writer 在 prompt 顶部读这段 brief 作为'北极星'。"
        "失败→空 brief，writer 走原 30+ 字段路径。"
    ),
))


@dataclass
class ConsolidatedBrief:
    """合并后的写章导读——writer prompt 顶部的'北极星'。"""
    chapter_index: int = 0
    volume_index: int = 0
    core_goal: str = ""
    p0_must: list[str] = field(default_factory=list)
    p1_should: list[str] = field(default_factory=list)
    p2_style: dict = field(default_factory=dict)
    p3_forbidden: list[str] = field(default_factory=list)
    conflicts_log: list[str] = field(default_factory=list)
    source_hint_count: int = 0

    def to_prompt_block(self) -> str:
        """渲染为 writer prompt 顶部的导读段。"""
        if not self.core_goal and not self.p0_must:
            return ""
        lines: list[str] = []
        lines.append("═══ 本章「北极星」目标（请把全章的取舍都对准这条）═══")
        lines.append(self.core_goal or "（未明确——按 P0 硬约束推进）")
        if self.p0_must:
            lines.append("")
            lines.append("═══ P0 硬约束（绝对要做——任何一条缺失 = 章节失败）═══")
            for i, item in enumerate(self.p0_must, 1):
                lines.append(f"  {i}. {item}")
        if self.p1_should:
            lines.append("")
            lines.append("═══ P1 软约束（推荐做到——能做尽量做，做不到要在 P0 之后让位）═══")
            for i, item in enumerate(self.p1_should, 1):
                lines.append(f"  {i}. {item}")
        if self.p2_style:
            lines.append("")
            lines.append("═══ P2 风格指针（贯穿全章的基调）═══")
            for k, v in self.p2_style.items():
                if v:
                    lines.append(f"  · {k}：{v}")
        if self.p3_forbidden:
            lines.append("")
            lines.append("═══ P3 禁忌（任何一条违反 = 不通过审核）═══")
            for item in self.p3_forbidden[:10]:
                lines.append(f"  × {item}")
        if self.conflicts_log:
            lines.append("")
            lines.append("═══ Hint 冲突解决说明（合并器已为你裁决，按此执行即可）═══")
            for c in self.conflicts_log:
                lines.append(f"  ! {c}")
        lines.append("")
        lines.append(
            f"（提示：本章原始有 {self.source_hint_count} 条 hint，已合并为以上结构化指令。"
            f"如需细节请查 directive 其他字段，但执行的优先级以本段为准。）"
        )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "chapter_index": self.chapter_index,
            "volume_index": self.volume_index,
            "core_goal": self.core_goal,
            "p0_must": list(self.p0_must),
            "p1_should": list(self.p1_should),
            "p2_style": dict(self.p2_style),
            "p3_forbidden": list(self.p3_forbidden),
            "conflicts_log": list(self.conflicts_log),
            "source_hint_count": self.source_hint_count,
        }


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════

def consolidate(directive: ChapterDirective, state: NovelState) -> ConsolidatedBrief:
    """
    把 ChapterDirective 的 30+ 字段合并为单一优先级化 brief。
    纯规则——不调 LLM。失败兜底返回空 brief。
    """
    brief = ConsolidatedBrief(
        chapter_index=getattr(directive, "chapter_index", 0),
        volume_index=getattr(directive, "volume_index", 0),
    )
    try:
        brief.core_goal = _pick_core_goal(directive, state)
        brief.p0_must = _build_p0_must(directive)
        brief.p1_should = _build_p1_should(directive, state)
        brief.p2_style = _build_p2_style(directive)
        brief.p3_forbidden = list(getattr(directive, "forbidden_content", None) or [])
        brief.conflicts_log = _resolve_conflicts(directive)
        brief.source_hint_count = _count_source_hints(directive)
    except Exception as e:
        # 失败不阻塞 writer——返回到目前为止填好的部分
        _surface_consolidate_failure(directive, e)
    return brief


# ═══════════════════════════════════════════════════════
#  核心目标推导（优先级链）
# ═══════════════════════════════════════════════════════

def _pick_core_goal(directive: ChapterDirective, state: NovelState) -> str:
    """
    单一北极星目标的优先级链：
      1. user_inspiration（作者灵感是上帝）
      2. user_feedback（重写反馈）
      3. 触发爽点（本章爆点）
      4. 反转揭露（本章戏剧高峰）
      5. 兑现重要伏笔
      6. must_include 第一条（兜底）
      7. 推进 primary_line
    """
    insp = (getattr(directive, "user_inspiration", "") or "").strip()
    if insp:
        return f"贯彻作者灵感：{insp[:80]}"

    fb = (getattr(directive, "user_feedback", "") or "").strip()
    if fb:
        return f"基于作者反馈重写：{fb[:80]}"

    sps = list(getattr(directive, "satisfaction_points", None) or [])
    if sps:
        sp_id = sps[0]
        sp = _find_sp(state, sp_id)
        if sp:
            payoff = (getattr(sp, "payoff_description", "") or "").strip()
            title = (getattr(sp, "title", "") or sp_id).strip()
            if payoff:
                return f"触发爽点【{title}】：{payoff[:60]}"
            return f"触发爽点【{title}】"

    twists = list(getattr(directive, "twist_reveals", None) or [])
    if twists:
        return f"揭露反转层【{twists[0]}】——本章必须把这一层戏剧落地"

    fw_resolve = list(getattr(directive, "foreshadow_resolve", None) or [])
    if fw_resolve:
        fw_id = fw_resolve[0]
        fw = _find_fw(state, fw_id)
        if fw:
            desc = (getattr(fw, "resolution_description", "") or "").strip()
            if desc:
                return f"兑现伏笔：{desc[:60]}"
        return f"兑现伏笔【{fw_id}】"

    must = list(getattr(directive, "must_include", None) or [])
    if must:
        return f"推进主线：{must[0][:80]}"

    primary = (getattr(directive, "primary_line", "") or "").strip()
    if primary:
        return f"推进【{primary}】叙事线"

    return "推进章节（无明确核心——按蓝图执行）"


# ═══════════════════════════════════════════════════════
#  P0 硬约束（不可妥协）
# ═══════════════════════════════════════════════════════

def _build_p0_must(directive: ChapterDirective) -> list[str]:
    items: list[str] = []

    insp = (getattr(directive, "user_inspiration", "") or "").strip()
    if insp:
        items.append(f"【作者灵感·上帝指令】{insp[:200]}")

    fb = (getattr(directive, "user_feedback", "") or "").strip()
    if fb:
        items.append(f"【重写反馈·必须落地】{fb[:200]}")

    seeds = list(getattr(directive, "callback_seeds", None) or [])
    if seeds:
        items.append(
            "【爽点 callback 锚点·原文必须精确引用】" + " | ".join(seeds[:3])
        )

    for item in (getattr(directive, "must_include", None) or [])[:6]:
        s = (item or "").strip()
        if s:
            items.append(s)

    for layer_id in (getattr(directive, "twist_reveals", None) or [])[:3]:
        items.append(f"【反转揭露】{layer_id}（必须在本章戏剧落地）")

    fw_resolve = list(getattr(directive, "foreshadow_resolve", None) or [])
    for fw_id in fw_resolve[:3]:
        items.append(f"【兑现伏笔】{fw_id}")

    # 角色硬事实摘要（避免 writer 写出和当前 state 矛盾的位置/伤势）
    char_states = getattr(directive, "character_states", None) or {}
    if char_states:
        snippets = []
        for name, st in list(char_states.items())[:4]:
            loc = (st.get("location") or "").strip()
            injury = (st.get("injury") or "").strip()
            realm = (st.get("realm") or "").strip()
            parts = [name]
            if realm:
                parts.append(realm)
            if loc:
                parts.append(f"位置:{loc}")
            if injury:
                parts.append(f"伤势:{injury}")
            snippets.append("/".join(parts))
        if snippets:
            items.append("【角色硬事实·不得违反】" + "; ".join(snippets))

    return _dedup(items)


# ═══════════════════════════════════════════════════════
#  P1 软约束
# ═══════════════════════════════════════════════════════

def _build_p1_should(directive: ChapterDirective, state: NovelState) -> list[str]:
    items: list[str] = []

    role = (getattr(directive, "structure_role", "") or "").strip()
    if role:
        items.append(f"【结构定位】本章在上层小情节中扮演「{role}」")

    purpose = (getattr(directive, "purpose", "") or "").strip()
    if purpose:
        items.append(f"【为什么写】{purpose}")

    expr = (getattr(directive, "expression", "") or "").strip()
    if expr:
        items.append(f"【想表达】{expr}")

    # 读者预期——chapter_planner 会为每条标 decision(satisfy/reverse/stack)
    expectations = getattr(directive, "reader_expectations", None) or []
    for re_item in expectations[:3]:
        content = (
            getattr(re_item, "content", "")
            or getattr(re_item, "description", "")
            or str(re_item)
        )[:80]
        decision = (getattr(re_item, "decision", "") or "未定").strip()
        items.append(f"【读者预期·{decision}】{content}")

    # 软任务：植入伏笔
    for fw_id in (getattr(directive, "foreshadow_plant", None) or [])[:3]:
        items.append(f"【植入伏笔】{fw_id}")

    # 红鲱鱼
    for rh in (getattr(directive, "red_herring_plant", None) or [])[:2]:
        items.append(f"【植入红鲱鱼·假线索】{rh}")
    for rh in (getattr(directive, "red_herring_debunk", None) or [])[:2]:
        items.append(f"【揭穿红鲱鱼】{rh}")

    # 反转伏笔（未来反转章用）
    for clue in (getattr(directive, "twist_clues_plant", None) or [])[:2]:
        items.append(f"【埋反转伏笔】{clue}（≤5 章后揭露）")

    return _dedup(items)


# ═══════════════════════════════════════════════════════
#  P2 风格指针
# ═══════════════════════════════════════════════════════

def _build_p2_style(directive: ChapterDirective) -> dict:
    style: dict = {}
    tension = getattr(directive, "tension", None)
    if tension is not None:
        style["张力"] = getattr(tension, "value", str(tension))
    rhythm = getattr(directive, "rhythm", None)
    if rhythm is not None:
        style["节奏"] = getattr(rhythm, "value", str(rhythm))
    word_pace = (getattr(directive, "word_pace", "") or "").strip()
    if word_pace:
        style["语速"] = word_pace
    ch_type = (getattr(directive, "chapter_type", "") or "").strip()
    if ch_type:
        style["章型"] = ch_type
    emo = (getattr(directive, "emotional_note", "") or "").strip()
    if emo:
        style["情绪基调"] = emo
    pos = (getattr(directive, "chapter_position", "") or "").strip()
    if pos:
        style["章节位置"] = pos
    chain = (getattr(directive, "structure_chain", "") or "").strip()
    if chain:
        style["结构链"] = chain
    # 钩子类型（如果有 hook_designer 已经填）
    hook_type = (getattr(directive, "closing_hook_type", "") or "").strip()
    if hook_type:
        style["章末钩子类型"] = hook_type
    return style


# ═══════════════════════════════════════════════════════
#  冲突解决（透明日志）
# ═══════════════════════════════════════════════════════

def _resolve_conflicts(directive: ChapterDirective) -> list[str]:
    """识别明显的 hint 冲突并记录裁决方案。writer 看到知道为何这样取舍。"""
    log: list[str] = []
    rhythm = getattr(directive, "rhythm", None)
    rhythm_val = getattr(rhythm, "value", str(rhythm)) if rhythm is not None else ""
    tension = getattr(directive, "tension", None)
    tension_val = getattr(tension, "value", str(tension)) if tension is not None else ""

    sps = list(getattr(directive, "satisfaction_points", None) or [])
    twists = list(getattr(directive, "twist_reveals", None) or [])
    must = list(getattr(directive, "must_include", None) or [])
    insp = (getattr(directive, "user_inspiration", "") or "").strip()
    fb = (getattr(directive, "user_feedback", "") or "").strip()

    # 1. 节奏=缓冲 vs 触发爽点
    if sps and ("缓" in rhythm_val or "慢" in rhythm_val):
        log.append(
            f"节奏[{rhythm_val}]与爽点触发冲突 → 优先爽点（缓节奏中的局部高峰即可，"
            f"前后铺缓节奏的过渡和回落）"
        )

    # 2. 反转 vs 平稳张力
    if twists and ("平" in tension_val or "低" in tension_val):
        log.append(
            f"反转揭露要求情绪强度，但张力[{tension_val}]偏低 → 张力不变但反转必须在某一刻爆出（不可压平）"
        )

    # 3. 灵感 + 大量 must_include → 灵感为先
    if insp and len(must) > 4:
        log.append(
            f"作者灵感 + {len(must)} 条 must_include → 灵感为先；must_include 退为软目标"
            f"（能融入就融入，与灵感冲突时让步）"
        )

    # 4. 反馈 + 灵感同时存在 → 反馈是针对上一版本的具体修正，优先级最高
    if insp and fb:
        log.append(
            "作者灵感 + 重写反馈同时存在 → 重写反馈为最高优先（针对上版具体修正）；灵感作为基调保留"
        )

    # 5. 同时触发爽点 + 反转 → 给二者排序
    if sps and twists:
        log.append(
            f"本章同时触发爽点 + 反转揭露 → 戏剧顺序应是：先反转揭露（信息冲击）→ 爽点爆出（情绪宣泄）"
        )

    return log


# ═══════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════

def _count_source_hints(directive: ChapterDirective) -> int:
    n = 0
    n += len(getattr(directive, "must_include", None) or [])
    n += len(getattr(directive, "forbidden_content", None) or [])
    n += len(getattr(directive, "callback_seeds", None) or [])
    n += len(getattr(directive, "reader_expectations", None) or [])
    n += len(getattr(directive, "twist_reveals", None) or [])
    n += len(getattr(directive, "twist_clues_plant", None) or [])
    n += len(getattr(directive, "foreshadow_plant", None) or [])
    n += len(getattr(directive, "foreshadow_resolve", None) or [])
    n += len(getattr(directive, "red_herring_plant", None) or [])
    n += len(getattr(directive, "red_herring_debunk", None) or [])
    n += len(getattr(directive, "satisfaction_points", None) or [])
    n += len(getattr(directive, "character_states", None) or {})
    if (getattr(directive, "user_inspiration", "") or "").strip():
        n += 1
    if (getattr(directive, "user_feedback", "") or "").strip():
        n += 1
    return n


def _find_sp(state: NovelState, sp_id: str):
    for sp in getattr(state, "satisfaction_points", None) or []:
        if getattr(sp, "sp_id", None) == sp_id:
            return sp
    return None


def _find_fw(state: NovelState, fw_id: str):
    getter = getattr(state, "get_foreshadow", None)
    if callable(getter):
        try:
            return getter(fw_id)
        except Exception:
            pass
    for fw in getattr(state, "foreshadow_items", None) or []:
        if getattr(fw, "fw_id", None) == fw_id:
            return fw
    return None


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = (it or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def _surface_consolidate_failure(directive: ChapterDirective, e: Exception) -> None:
    """合并失败 → progress_warning + print，writer 走原路径不阻塞。"""
    try:
        from persistence.checkpoint import add_progress_warning
        ch_idx = getattr(directive, "chapter_index", "?")
        add_progress_warning(
            level="warn",
            source=f"chapter:{ch_idx}:directive_consolidator",
            message=f"directive 合并失败,writer 走原 30+ 字段路径: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
    print(f"  ⚠ directive_consolidator 失败(不阻塞,writer 走原路径): {type(e).__name__}: {e}")
