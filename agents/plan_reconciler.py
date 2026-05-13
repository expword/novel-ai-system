"""
规划-执行反馈闭环 —— 在写了 N 章后自动对比"计划 vs 实际"，
更新 state.tension_debt / novelty_budget，并产出"后续章节规划调整建议"。

触发时机：director 写完每一章后（成本低，纯规则 + 短 LLM 调用）。
不改写 rhythm_plan / emotion_curve 本身（避免破坏性），而是：
  · 更新 state.tension_debt / novelty_budget / last_reconcile_report
  · 产出的"建议调整"通过 directive.must_include 注入未来的 2-3 章

设计理念：
  · 本 agent 是**轻量且确定性**的——主要靠规则计算，LLM 只用来生成人话建议
  · state.tension_debt：读者"情绪账户"
      - 苦章（tension=FALLING/TURNING 等低谷）累积负债 -1 到 -3
      - 爽章（tension=PEAK/RISING_HIGH 等高点）释放债务 +1 到 +3
      - 理想区间 [-6, +3]；超出就该调整节奏
  · state.novelty_budget：
      - 初值 100（起始余额）
      - 每章引入新要素扣分（reader_audit.new_info_density 低说明新信息多 → 扣更多）
      - 每章每月自动恢复 + 5（跨卷有喘息）
      - 低于 20 说明本书"新意快用完了"，warn 下游
"""
from __future__ import annotations
from dataclasses import asdict
from typing import Optional


# ═══════════════════════════════════════════════════════════════
#  规则层：根据本章的 summary/audit 更新两个债务指标
# ═══════════════════════════════════════════════════════════════

# tension value → 债务增减映射
_TENSION_DELTA = {
    # 苦/低谷：读者承受压力 → 负债累加
    "压抑":   -3, "低谷": -3, "下落": -2, "沉重": -2,
    "松弛":   -1,   # 小歇
    # 平稳
    "平静": 0, "稳定": 0, "积累": 0, "过渡": 0,
    # 爽/高点：债务释放
    "上升": 1, "紧张": 1,
    "高潮": 3, "爆发": 3, "突破": 3, "巅峰": 3,
    "反转": 2, "揭露": 2,
}


def _tension_value(t) -> str:
    """TensionLevel enum → 字符串（宽容各种形态）。"""
    if t is None:
        return ""
    return str(getattr(t, "value", t))


def _delta_from_tension(t) -> int:
    v = _tension_value(t)
    # 部分匹配
    for key, d in _TENSION_DELTA.items():
        if key in v:
            return d
    return 0


def reconcile_after_chapter(
    state,
    chapter_index: int,
) -> dict:
    """
    一章写完后调用。返回本次 reconcile 的 report（也写回 state.last_reconcile_report）。

    流程：
      1. 从 state.completed_chapters 找本章 summary
      2. 用本章 tension → _delta_from_tension → 更新 tension_debt
      3. 用本章 reader_audit（若有）新信息密度 → 扣 novelty_budget
      4. 每 10 章自然恢复 novelty_budget（+5）
      5. 判定"是否偏离规划"：
         - rhythm_plan 期望本章是快节奏，但实际 tension=低谷 → 偏差
         - emotion_curve 期望高点，但实际 tension=平静 → 偏差
      6. 按 debt 和 budget 产出"未来 2 章建议"
      7. 回填到 state，返回 report
    """
    import json

    ch_sum = next((c for c in (state.completed_chapters or []) if c.index == chapter_index), None)
    if not ch_sum:
        return {"error": f"chapter {chapter_index} 未找到 summary"}

    report: dict = {
        "chapter_index": chapter_index,
        "deltas": {},
        "deviations": [],
        "advice": [],
        "before": {
            "tension_debt": int(getattr(state, "tension_debt", 0)),
            "novelty_budget": int(getattr(state, "novelty_budget", 100)),
        },
    }

    # 1. tension_debt 更新
    t_delta = _delta_from_tension(ch_sum.tension)
    state.tension_debt = max(-15, min(15, int(getattr(state, "tension_debt", 0)) + t_delta))
    report["deltas"]["tension_debt"] = t_delta

    # 2. novelty_budget 更新
    # 从 reader_audit 取 new_info_density（10=好 → 扣 1；5=中 → 扣 3；1=灾 → 扣 8）
    audit = (state.reader_audits or {}).get(chapter_index)
    if audit:
        info_density = int(getattr(audit, "new_info_density", 8))
        cost = max(1, 10 - info_density)   # 1..9
    else:
        cost = 2    # 无审计时保守扣 2
    state.novelty_budget = max(0, int(getattr(state, "novelty_budget", 100)) - cost)
    report["deltas"]["novelty_budget"] = -cost

    # 3. 每 10 章自然恢复（喘息）
    if chapter_index % 10 == 0:
        state.novelty_budget = min(100, state.novelty_budget + 5)
        report["deltas"]["novelty_budget_recovery"] = 5

    # 4. 偏差检测：rhythm_plan vs 实际 tension
    try:
        rseg = state.get_rhythm_for_chapter(chapter_index) if hasattr(state, "get_rhythm_for_chapter") else None
    except Exception:
        rseg = None
    if rseg:
        expected_rhythm = getattr(rseg.rhythm_type, "value", str(rseg.rhythm_type))
        actual_tension = _tension_value(ch_sum.tension)
        # 简易匹配：快节奏 vs 低谷？慢热 vs 爆发？
        if any(k in expected_rhythm for k in ("快", "高")) and any(k in actual_tension for k in ("低谷", "压抑")):
            report["deviations"].append(
                f"规划rhythm='{expected_rhythm}'，实际tension='{actual_tension}'——节奏和张力不匹配"
            )
        elif any(k in expected_rhythm for k in ("慢", "铺")) and any(k in actual_tension for k in ("高潮", "爆发", "突破")):
            report["deviations"].append(
                f"规划rhythm='{expected_rhythm}'，实际tension='{actual_tension}'——本该铺垫却炸了"
            )

    # 5. 按 debt / budget 产出未来建议
    debt = state.tension_debt
    budget = state.novelty_budget
    advice: list[str] = []

    if debt <= -6:
        advice.append(f"tension_debt={debt}（读者苦太久）→ 未来 1-2 章应安排明确爽点或情感释放")
    elif debt >= 5:
        advice.append(f"tension_debt={debt}（连爽过多 → 审美疲劳）→ 未来 1-2 章放入低谷/代价章")
    elif -3 <= debt <= 2:
        pass  # 理想区间

    if budget < 20:
        advice.append(f"novelty_budget={budget}（新意余额告急）→ 不要引入新角色/新设定，多用已有元素做变化")
    elif budget < 50:
        advice.append(f"novelty_budget={budget}（中等）→ 新要素应节制，优先深化已有")

    if report["deviations"]:
        advice.append("规划和实际偏离——建议后续章节回到规划节奏，或接受新走向并重建 rhythm_plan")

    report["advice"] = advice
    report["after"] = {
        "tension_debt": state.tension_debt,
        "novelty_budget": state.novelty_budget,
    }
    state.last_reconcile_report = report
    return report


# ═══════════════════════════════════════════════════════════════
#  供 chapter_planner 查询的"本章规划调整建议"
#  —— 在生成下一章蓝图时，把这些建议以 must_include 式 hint 塞进 prompt
# ═══════════════════════════════════════════════════════════════

def get_planning_hints(state, chapter_index: int) -> list[str]:
    """
    返回当下 tension_debt / novelty_budget 对 *本章* 的规划建议。
    chapter_planner 在生成蓝图时读取并注入到 prompt 顶部。
    """
    hints: list[str] = []
    debt = int(getattr(state, "tension_debt", 0))
    budget = int(getattr(state, "novelty_budget", 100))

    if debt <= -6:
        hints.append(
            f"⚠ 读者情绪账户：已累积 {abs(debt)} 章的苦/压抑没被释放——"
            f"如果本章结构角色允许，强烈建议设计一个明确的「兑现/爽点/情感释放」时刻，"
            f"不要再继续压抑。"
        )
    elif debt >= 5:
        hints.append(
            f"⚠ 读者情绪账户：最近连爽过多（+{debt}）——"
            f"本章应该有代价/低谷/压力，避免连续高点导致审美疲劳。"
        )

    if budget < 20:
        hints.append(
            f"⚠ 新意余额告急（{budget}/100）——本章不要引入新角色/新地名/新设定。"
            f"用已有角色做深化，用已有设定做新组合。"
        )
    elif budget < 50:
        hints.append(
            f"新意余额中等（{budget}/100）——节制新要素，优先深化已有元素。"
        )

    return hints
