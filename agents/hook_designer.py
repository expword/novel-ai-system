"""
HookDesigner —— 章末钩子主动设计器（纯规则）。

═══ 解决的问题 ═══

HookType / closing_hook_spec / critic.hook_type_compliance 已存在,
但「选哪个 HookType」目前由 chapter_planner LLM 自由决定——容易向「悬念钩」
单一收敛（最稳妥的选择,但读者审美疲劳)。

HookDesigner 写章前用纯规则**主动**推荐 hook_type:
  · 输入: 本卷历史 hook 分布 + 本章 chapter_type + tension + chapter_index
  · 输出: suggested HookType + 推荐理由
  · 挂到 directive.closing_hook_type → chapter_planner LLM 优先采用,允许覆盖
  · 写入 directive.p2_style["章末钩子类型"](DirectiveConsolidator 已读取)

═══ 决策规则 ═══

1. 硬约束优先（章型决定钩子）:
   · 战斗章 → physical/reversal
   · 真相章 → info_reveal
   · 反转章 → reversal
   · 感情章 → emotional
2. 历史平衡（避免同类型连发):
   · 本卷最近 5 章 ≥3 个同类型 → 排除该类型
   · 剩余候选中选"本卷累计最少"的
3. 张力调整:
   · PEAK + 战斗 → death/reversal
   · CALM + 情感 → emotional
4. 兜底: SUSPENSE（最通用，但仅在其他规则未命中时）

═══ 设计原则 ═══

· 纯规则,零 LLM 调用（不增成本）
· chapter_planner LLM 可覆盖（suggested 是软锚不是硬约束）
· 失败 → 返回 SUSPENSE,不阻塞主流程
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from persistence.state import HookType, NovelState, ChapterDirective, TensionLevel
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="hook_designer.suggest_hook_type",
    inputs=[
        "completed_chapters[*].closing_hook_type",
        "completed_chapters[*].volume_index",
    ],
    outputs=[
        # 写到 directive.closing_hook_type,不直接改 state
    ],
    invariants=[],
    notes=(
        "纯规则推荐 章末 HookType,挂在 directive.closing_hook_type。"
        "chapter_planner 优先采用此 suggested,允许覆盖。"
        "DirectiveConsolidator p2_style 直接读取该字段。"
    ),
))


# ═══════════════════════════════════════════════════════
#  章型 → 优先 hook 类型映射
# ═══════════════════════════════════════════════════════

CHAPTER_TYPE_PREFERENCE: dict[str, list[HookType]] = {
    "战斗章":   [HookType.PHYSICAL, HookType.REVERSAL, HookType.DEATH],
    "高潮章":   [HookType.REVERSAL, HookType.DEATH, HookType.PHYSICAL],
    "反转章":   [HookType.REVERSAL, HookType.INFO_REVEAL],
    "真相章":   [HookType.INFO_REVEAL, HookType.REVERSAL],
    "感情章":   [HookType.EMOTIONAL, HookType.SUSPENSE],
    "打脸章":   [HookType.REVERSAL, HookType.INFO_REVEAL],
    "升级章":   [HookType.PHYSICAL, HookType.SUSPENSE],
    "铺垫章":   [HookType.SUSPENSE, HookType.PHYSICAL],
    "日常章":   [HookType.EMOTIONAL, HookType.SUSPENSE],
    "过渡章":   [HookType.SUSPENSE, HookType.EMOTIONAL],
    "调剂章":   [HookType.EMOTIONAL, HookType.SUSPENSE],
}

# 最近 N 章作为历史窗口（同类型 ≥ THRESHOLD 时排除）
HISTORY_WINDOW = 5
SATURATION_THRESHOLD = 3

# 所有可选钩子类型（按"通用度"排,前面更通用）
ALL_HOOK_TYPES = [
    HookType.SUSPENSE, HookType.PHYSICAL, HookType.EMOTIONAL,
    HookType.INFO_REVEAL, HookType.REVERSAL, HookType.DEATH,
]


@dataclass
class HookSuggestion:
    hook_type: HookType
    reason: str          # 一句话决策依据
    excluded: list[HookType]  # 因历史饱和被排除的类型


def suggest_hook_type(
    state: NovelState,
    chapter_index: int,
    *,
    chapter_type: str = "",
    tension: Optional[TensionLevel] = None,
    volume_index: Optional[int] = None,
) -> HookSuggestion:
    """
    纯规则推荐 章末 HookType。

    返回 HookSuggestion(hook_type, reason, excluded)。
    失败兜底返回 SUSPENSE。
    """
    try:
        return _suggest_impl(state, chapter_index,
                              chapter_type=chapter_type,
                              tension=tension,
                              volume_index=volume_index)
    except Exception as e:
        # 失败兜底
        return HookSuggestion(
            hook_type=HookType.SUSPENSE,
            reason=f"规则失败兜底→悬念钩: {type(e).__name__}",
            excluded=[],
        )


def _suggest_impl(
    state: NovelState,
    chapter_index: int,
    *,
    chapter_type: str,
    tension: Optional[TensionLevel],
    volume_index: Optional[int],
) -> HookSuggestion:
    # 1. 取本卷最近 N 章历史 hook 分布
    recent_hooks = _get_recent_hooks(state, chapter_index, volume_index)
    counts = Counter(recent_hooks)

    # 2. 排除饱和类型
    excluded = [ht for ht, n in counts.items() if n >= SATURATION_THRESHOLD]

    # 3. 章型偏好优先
    ch_type = (chapter_type or "").strip()
    preferences = CHAPTER_TYPE_PREFERENCE.get(ch_type, [])

    # 4. 张力 + 章型联合调整
    if tension is not None:
        ten_val = getattr(tension, "value", str(tension))
        if "高潮" in ten_val or "反转" in ten_val:
            # 高峰章节优先反转/物理/死亡（强冲击）
            preferences = [HookType.REVERSAL, HookType.PHYSICAL, HookType.DEATH] + preferences
        elif "平静" in ten_val or "下落" in ten_val:
            # 缓节奏优先情感/悬念（不要硬上反转）
            preferences = [HookType.EMOTIONAL, HookType.SUSPENSE] + preferences

    # 5. 按 preferences 顺序找第一个未饱和的
    for ht in preferences:
        if ht not in excluded:
            reason = _build_reason(ch_type, tension, ht, excluded, "章型偏好命中")
            return HookSuggestion(hook_type=ht, reason=reason, excluded=excluded)

    # 6. 章型偏好全被排除 → 选累计使用最少的未饱和类型
    available = [ht for ht in ALL_HOOK_TYPES if ht not in excluded]
    if available:
        # 按 counts 升序选最少使用的
        best = min(available, key=lambda h: counts.get(h, 0))
        reason = _build_reason(ch_type, tension, best, excluded, "历史最少使用")
        return HookSuggestion(hook_type=best, reason=reason, excluded=excluded)

    # 7. 都饱和（极端：连续 N 章 hook 高度集中）→ 兜底 SUSPENSE
    return HookSuggestion(
        hook_type=HookType.SUSPENSE,
        reason="所有类型饱和,兜底→悬念钩",
        excluded=excluded,
    )


def apply_to_directive(state: NovelState, directive: ChapterDirective) -> Optional[HookSuggestion]:
    """
    director._generate_directive 调一次:计算并挂在 directive.closing_hook_type。
    返回 HookSuggestion(供日志);失败返回 None。
    """
    try:
        sug = suggest_hook_type(
            state,
            directive.chapter_index,
            chapter_type=directive.chapter_type or "",
            tension=directive.tension,
            volume_index=directive.volume_index,
        )
        directive.closing_hook_type = sug.hook_type.value
        return sug
    except Exception:
        return None


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _get_recent_hooks(
    state: NovelState,
    chapter_index: int,
    volume_index: Optional[int],
) -> list[HookType]:
    """取本卷(若有)/全书最近 N 章已写章节的 closing_hook_type。"""
    chapters = getattr(state, "completed_chapters", None) or []
    if not chapters:
        return []

    # 仅取本章之前的
    valid = []
    for ch in chapters:
        idx = getattr(ch, "index", -1)
        if idx <= 0 or idx >= chapter_index:
            continue
        # 同卷过滤
        if volume_index is not None:
            ch_vol = getattr(ch, "volume_index", None)
            if ch_vol is not None and ch_vol != volume_index:
                continue
        valid.append(ch)

    # 取最近 N
    recent = valid[-HISTORY_WINDOW:]
    out = []
    for ch in recent:
        ht_str = (getattr(ch, "closing_hook_type", "") or "").strip()
        if not ht_str:
            continue
        try:
            out.append(HookType(ht_str))
        except ValueError:
            # 不在枚举里 → skip
            continue
    return out


def _build_reason(
    ch_type: str,
    tension: Optional[TensionLevel],
    chosen: HookType,
    excluded: list[HookType],
    rule: str,
) -> str:
    parts = [f"推荐{chosen.value}"]
    if ch_type:
        parts.append(f"章型={ch_type}")
    if tension is not None:
        parts.append(f"张力={getattr(tension, 'value', str(tension))}")
    parts.append(f"规则={rule}")
    if excluded:
        parts.append(f"已排除={','.join(h.value for h in excluded)}")
    return " · ".join(parts)
