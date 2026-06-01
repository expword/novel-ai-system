"""
LengthGovernor —— 按平台 + 章型推算 target_words；章后字数兜底告警。

═══ 解决的问题 ═══

各网文平台对单章字数有明确预期：
  · 起点 / QQ阅读 / 纵横       2500-3500 字
  · 番茄 / 书旗 / 七猫        2000-3000 字（移动碎片阅读）
  · 晋江 / 言情向             3000-5000 字
  · 飞卢 / 短章流              2200-3200 字

当前 config.WORDS_PER_CHAPTER 硬编码 3000——不区分平台，也不区分章型。
LengthGovernor 按 state.concept_pitch.target_platform + ChapterDirective.chapter_type
动态推算合理字数,替代硬编码。

writer 已有 MIN_FILL_RATIO 的"过短自动扩写"兜底,本 agent 主要补两件事:
  1. 写前 compute_target_words(state, ch_idx, ch_type) → director 用作 total_words
  2. 写后 check_length(text, target) → 过短/过长写 progress_warning

═══ 设计原则 ═══

· 纯规则,不调 LLM
· 失败兜底返回 DEFAULT_TARGET,绝不让本 agent 阻塞写章
· 按 [[feedback_generic_prompts]]——所有平台/章型表是通用映射,不硬编码具体项目术语
"""
from __future__ import annotations
from typing import Optional

from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="length_governor.compute_target_words",
    inputs=[
        "concept_pitch.target_platform",
    ],
    outputs=[
        # 写章前: 返回 int 供 director 用作 total_words
        # 写章后: progress_warning(source=chapter:N:length)
    ],
    invariants=[],
    notes=(
        "按平台+章型推算 target_words。失败兜底返回 DEFAULT_TARGET。"
        "替代 config.WORDS_PER_CHAPTER 硬编码。"
    ),
))


DEFAULT_TARGET = 3000

# 平台 → (字数下限, 字数上限) —— 取中位数作 base
PLATFORM_RANGE: dict[str, tuple[int, int]] = {
    "起点": (2500, 3500),
    "起点中文网": (2500, 3500),
    "qq阅读": (2500, 3500),
    "纵横": (2500, 3500),
    "纵横中文网": (2500, 3500),
    "刺猬猫": (2500, 3500),
    "番茄": (2000, 3000),
    "番茄小说": (2000, 3000),
    "七猫": (2000, 3000),
    "书旗": (1800, 2800),
    "晋江": (3000, 5000),
    "晋江文学城": (3000, 5000),
    "飞卢": (2200, 3200),
    "17k": (2200, 3200),
}

# 章型 → 目标字数倍率（战斗/高潮章需要空间，铺垫/日常章精简）
CHAPTER_TYPE_MULT: dict[str, float] = {
    "战斗章": 1.30,
    "高潮章": 1.30,
    "反转章": 1.25,
    "真相章": 1.25,
    "感情章": 1.10,
    "升级章": 1.10,
    "打脸章": 1.10,
    "铺垫章": 0.90,
    "日常章": 0.85,
    "过渡章": 0.85,
    "调剂章": 0.85,
}

# 写后字数检查的容差（默认 ±30%）
DEFAULT_TOLERANCE = 0.30


def compute_target_words(
    state,
    chapter_index: int = 0,
    chapter_type: str = "",
    *,
    fallback: int = DEFAULT_TARGET,
) -> int:
    """
    按平台 + 章型推算本章目标字数。

    优先级：
      1. concept_pitch.target_platform 命中 PLATFORM_RANGE → base = 中位数
      2. 没命中 → base = fallback (DEFAULT_TARGET=3000)
      3. chapter_type 命中 CHAPTER_TYPE_MULT → base *= mult
      4. clamp 到 [1500, 8000]
    """
    base = fallback
    try:
        cp = getattr(state, "concept_pitch", None)
        platform = ""
        if cp:
            platform = (getattr(cp, "target_platform", "") or "").strip().lower()
        if platform:
            for key, (lo, hi) in PLATFORM_RANGE.items():
                if key.lower() in platform:
                    base = (lo + hi) // 2
                    break
    except Exception:
        # 失败兜底 → fallback
        base = fallback

    mult = 1.0
    try:
        ct = (chapter_type or "").strip()
        if ct in CHAPTER_TYPE_MULT:
            mult = CHAPTER_TYPE_MULT[ct]
    except Exception:
        mult = 1.0

    target = int(base * mult)
    return max(1500, min(target, 8000))


def check_length(
    text: str,
    target: int,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """
    章后字数兜底检查。

    返回 dict:
      ok        : bool 是否在容差范围内
      actual    : 实际字数
      target    : 目标字数
      ratio     : actual/target
      severity  : 'info' | 'warn' | 'critical'
      advice    : 一句话给作者的建议
    """
    actual = _count_words(text)
    if target <= 0:
        return {"ok": True, "actual": actual, "target": 0, "ratio": 1.0,
                "severity": "info", "advice": ""}
    ratio = actual / target if target else 1.0
    lo, hi = 1.0 - tolerance, 1.0 + tolerance

    if lo <= ratio <= hi:
        return {"ok": True, "actual": actual, "target": target, "ratio": ratio,
                "severity": "info", "advice": ""}

    if ratio < lo:
        sev = "critical" if ratio < 0.5 else "warn"
        advice = (
            f"章节字数 {actual} / 目标 {target} ({int(ratio*100)}%过短)。"
            "writer 已有扩写兜底；如仍短，可能蓝图场景幕数过少——下章规划加 1-2 幕。"
        )
        return {"ok": False, "actual": actual, "target": target, "ratio": ratio,
                "severity": sev, "advice": advice}

    # ratio > hi
    advice = (
        f"章节字数 {actual} / 目标 {target} ({int(ratio*100)}%过长)。"
        "下章节制（裁场景或合并幕）；如本章戏剧密度高也可考虑拆章。"
    )
    sev = "warn" if ratio < 1.5 else "critical"
    return {"ok": False, "actual": actual, "target": target, "ratio": ratio,
            "severity": sev, "advice": advice}


def report_chapter_length(
    chapter_index: int,
    text: str,
    target: int,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """
    章后调一次：检查 + 失败时写 progress_warning。
    返回 check_length 的结果 dict。
    """
    result = check_length(text, target, tolerance=tolerance)
    if not result["ok"]:
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level=result["severity"],
                source=f"chapter:{chapter_index}:length",
                message=result["advice"],
            )
        except Exception:
            pass
    elif result["actual"] > 0:
        # 通过时清掉同 source 旧告警
        try:
            from persistence.checkpoint import clear_progress_warnings
            clear_progress_warnings(source=f"chapter:{chapter_index}:length")
        except Exception:
            pass
    return result


# ═══════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════

def _count_words(text: str) -> int:
    """优先用项目内的中文字数统计；失败兜底用 len(text)。"""
    if not text:
        return 0
    try:
        from persistence.state import count_chapter_words
        return count_chapter_words(text)
    except Exception:
        return len(text)
