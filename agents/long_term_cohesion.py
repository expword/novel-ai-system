"""
长篇连贯性跟踪 —— LongTermCohesionTracker

解决长篇网文常见的：
  1. 角色销号：第 1 卷重要配角，到第 6 卷已经 30 章没出现，读者忘了他
  2. 物品空挂：主角第 2 卷得了灵器，到第 10 卷一次没用过 → 要么用要么解释丢了
  3. 承诺挂账：第 3 卷主角说"等我有空就去救他"，挂了 50 章无下文
  4. 前情提要：第 5 卷开头需要回忆几卷前的关键信息

设计：
  · 每章写完后调用 update_after_chapter(state, chapter_index, content)
  · 周期性（每 N 章 / 每卷开头）调用 generate_cohesion_report(state, chapter_index)
  · 报告内容会以 must_include_hint 形式注入下一章 chapter_planner prompt

不是阻塞性的——只是产出建议，作者/写手决定要不要采纳。
"""
from __future__ import annotations
from typing import Optional


# 阈值
DORMANT_CHAPTERS_THRESHOLD = 30   # 角色多少章没出现算"销号"
ASSET_UNUSED_THRESHOLD = 25       # 物品/能力多少章没用算"空挂"
PROMISE_OVERDUE_THRESHOLD = 40    # 承诺挂多少章算"逾期"


def _make_promise_id(state, chapter: int) -> str:
    n = len([p for p in (state.promises or []) if p.chapter_made == chapter])
    return f"promise_ch{chapter}_{n+1}"


def register_promise(
    state,
    chapter: int,
    content: str,
    target: str = "",
    expected_fulfill_chapter: int = -1,
) -> str:
    """手动登记主角的承诺。返回 promise_id。"""
    from persistence.state import Promise
    pid = _make_promise_id(state, chapter)
    state.promises.append(Promise(
        promise_id=pid,
        chapter_made=chapter,
        content=content[:200],
        target_character=target,
        expected_fulfill_chapter=expected_fulfill_chapter,
    ))
    return pid


def mark_promise_fulfilled(state, promise_id: str, chapter: int) -> bool:
    for p in (state.promises or []):
        if p.promise_id == promise_id:
            p.fulfilled = True
            p.fulfilled_chapter = chapter
            return True
    return False


def register_asset(state, asset_name: str, asset_type: str, chapter: int, notes: str = "") -> None:
    """登记一个重要物品/能力。"""
    from persistence.state import AssetUsage
    if state.asset_usage is None:
        state.asset_usage = {}
    if asset_name not in state.asset_usage:
        state.asset_usage[asset_name] = AssetUsage(
            asset_name=asset_name,
            asset_type=asset_type,
            obtained_chapter=chapter,
            notes=notes[:200],
        )


def note_asset_used(state, asset_name: str, chapter: int) -> None:
    """记录一次使用（用在写完章后扫描内容触发）。"""
    if state.asset_usage and asset_name in state.asset_usage:
        a = state.asset_usage[asset_name]
        a.last_used_chapter = chapter
        a.use_count += 1


# ═══════════════════════════════════════════════════════════════
#  自动扫描（章后调用）—— 检测角色出现频率 + 物品使用
# ═══════════════════════════════════════════════════════════════

def update_after_chapter(state, chapter_index: int, content: str) -> None:
    """
    章节写完后自动扫描更新：
      1. 主角持有的 fortunes（机缘）若名字出现在正文 → 记一次使用
      2. （未来可扩展：扫到承诺关键词如"我会回来"等自动登记 promise）
    """
    if not content:
        return
    # 1. 已有的 asset 在正文出现 → 标记使用
    if state.asset_usage:
        for name in list(state.asset_usage.keys()):
            if name and name in content:
                note_asset_used(state, name, chapter_index)

    # 2. 已获得的 fortune 但还没在 asset_usage 里登记 → 自动登记
    for f in (state.fortunes or []):
        if not getattr(f, "obtained", False):
            continue
        ac = int(getattr(f, "actual_chapter", -1) or -1)
        if ac < 0 or ac > chapter_index:
            continue
        name = getattr(f, "name", "")
        if name and name not in (state.asset_usage or {}):
            register_asset(state, name, "fortune", ac, getattr(f, "effect_on_growth", "")[:120])
        # 在本章出现就记一次使用
        if name and name in content:
            note_asset_used(state, name, chapter_index)


# ═══════════════════════════════════════════════════════════════
#  报告生成
# ═══════════════════════════════════════════════════════════════

def _character_last_appearance(state, char_name: str) -> int:
    """从 character_state_history 找该角色最近一次快照的章。"""
    history = (state.character_state_history or {}).get(char_name, [])
    if not history:
        return 0
    return max(getattr(s, "chapter_index", 0) for s in history)


def generate_cohesion_report(state, current_chapter: int) -> dict:
    """生成跨卷连贯性报告。"""
    report: dict = {
        "current_chapter": current_chapter,
        "dormant_characters": [],      # 长期没出现的重要配角
        "unused_assets": [],            # 长期没用的物品/能力
        "overdue_promises": [],         # 超期未兑现的承诺
        "recap_suggestions": [],        # 建议加前情提要的关键事件
    }

    # 1. 销号检测——重要角色（first_volume 已开始 + last_volume 未结束）
    for c in (state.characters or []):
        first_v = int(getattr(c, "first_volume", 0) or 0)
        last_v = int(getattr(c, "last_volume", -1) or -1)
        # 主角不检测
        role_v = getattr(getattr(c, "role", None), "value", "")
        if role_v == "主角":
            continue
        # 取该角色当前所在卷
        cur_vol = state.current_volume_index or 1
        if first_v > cur_vol:
            continue
        if last_v != -1 and cur_vol > last_v:
            continue
        last_chap = _character_last_appearance(state, c.name)
        gap = current_chapter - last_chap if last_chap > 0 else 0
        if gap >= DORMANT_CHAPTERS_THRESHOLD:
            report["dormant_characters"].append({
                "name": c.name,
                "role": role_v,
                "last_appearance": last_chap,
                "gap": gap,
                "action": "建议在未来 1-3 章合理引入或显式解释他/她的去向"
            })

    # 2. 空挂物品/能力
    for name, asset in (state.asset_usage or {}).items():
        gap = current_chapter - asset.last_used_chapter if asset.last_used_chapter else current_chapter - asset.obtained_chapter
        if gap >= ASSET_UNUSED_THRESHOLD:
            report["unused_assets"].append({
                "name": name, "type": asset.asset_type,
                "obtained": asset.obtained_chapter,
                "last_used": asset.last_used_chapter,
                "gap": gap, "use_count": asset.use_count,
                "action": "建议在合理情境用一次，或明确「丢失/封印/被换」等下落"
            })

    # 3. 承诺挂账
    for p in (state.promises or []):
        if p.fulfilled:
            continue
        if p.expected_fulfill_chapter > 0 and current_chapter > p.expected_fulfill_chapter:
            overdue = current_chapter - p.expected_fulfill_chapter
            report["overdue_promises"].append({
                "id": p.promise_id, "content": p.content,
                "made_at": p.chapter_made,
                "expected": p.expected_fulfill_chapter,
                "overdue_by": overdue,
                "action": "建议在本卷处理：兑现/明确推迟原因/转化为新的剧情线"
            })
        elif p.expected_fulfill_chapter <= 0:
            # 没有预期日期但挂的太久也提示
            gap = current_chapter - p.chapter_made
            if gap >= PROMISE_OVERDUE_THRESHOLD:
                report["overdue_promises"].append({
                    "id": p.promise_id, "content": p.content,
                    "made_at": p.chapter_made, "expected": -1, "overdue_by": gap,
                    "action": "建议在合理时机回应这条承诺"
                })

    # 4. 前情提要：每卷开头建议回顾上一卷高强度爽点/反转
    vol = state.current_volume_index or 1
    if vol > 1:
        # 找上一卷的高 SP（intensity >=7）
        prev_vol_high_sp = []
        for sp in (state.satisfaction_points or []):
            if getattr(sp, "volume", 0) == vol - 1 and getattr(sp, "intensity", 0) >= 7 and getattr(sp, "triggered", False):
                prev_vol_high_sp.append({
                    "title": getattr(sp, "title", ""),
                    "intensity": sp.intensity,
                    "ch": sp.actual_chapter,
                })
        if prev_vol_high_sp:
            report["recap_suggestions"].append({
                "type": "previous_volume_climaxes",
                "items": prev_vol_high_sp[:3],
                "action": "本卷开篇适当呼应这些高点，唤醒读者记忆"
            })

    state.last_cohesion_report = report
    return report


def get_planning_hints(state, chapter_index: int) -> list[str]:
    """供 chapter_planner 调用——把当前最紧急的连贯性问题转成 hints。"""
    hints: list[str] = []
    rep = state.last_cohesion_report or {}

    if rep.get("dormant_characters"):
        names = [d["name"] for d in rep["dormant_characters"][:3]]
        hints.append(
            f"⚠ 角色销号风险：{' / '.join(names)} 已 {rep['dormant_characters'][0]['gap']}+ 章未出现——"
            f"如果本章合理可以让他/她登场，或显式提及。"
        )
    if rep.get("unused_assets"):
        names = [a["name"] for a in rep["unused_assets"][:3]]
        hints.append(
            f"⚠ 物品/能力空挂：{' / '.join(names)} 长期未用——本章若有机会请用一次或显式解释下落。"
        )
    if rep.get("overdue_promises"):
        hints.append(
            f"⚠ 承诺挂账：{rep['overdue_promises'][0]['content'][:50]}（已挂 {rep['overdue_promises'][0]['overdue_by']} 章）——"
            f"考虑本章/本卷处理。"
        )
    if rep.get("recap_suggestions"):
        hints.append(
            "ℹ 跨卷提醒：本卷开篇建议简短回顾上一卷的高潮/反转，唤醒读者记忆。"
        )
    return hints
