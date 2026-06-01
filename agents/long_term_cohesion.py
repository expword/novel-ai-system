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
FORESHADOW_OVERDUE_GRACE = 5      # 伏笔超过计划兑现章 N 章未回收算"挂账"
LIFECYCLE_OVERDUE_GRACE = 2       # lifecycle 节点过 target_chapter N 章未触发算"过期"
SP_OVERDUE_GRACE = 2              # 爽点过 target_chapter N 章未触发算"过期"


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
      3. P0-4: 读者疑问追踪——LLM 提取本章产生但未回答的疑问,挂 state.reader_questions_pending
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

    # 3. P0-4: 读者疑问追踪
    try:
        _track_reader_questions(state, chapter_index, content)
    except Exception as _e:
        # 不阻塞主流程
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="warn",
                source=f"chapter:{chapter_index}:reader_questions",
                message=f"读者疑问追踪失败: {type(_e).__name__}: {str(_e)[:120]}",
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  P0-4: 读者疑问追踪
# ═══════════════════════════════════════════════════════════════

# 单个疑问连续 N 章未被回应 → 强制下章回应
_QUESTION_PENDING_THRESHOLD = 3

_QUESTION_SYSTEM = """你是网文读者代言人——读完一章后,列出**普通读者最可能问出来但本章没回答**的疑问。

读者问什么:
· "为什么主角不直接 X?"(决策合理性)
· "反派为什么不杀他/不追下去?"(对手智商)
· "前面那个 X 怎么没了?"(线索失踪)
· "这事这么大,旁人怎么不反应?"(世界响应)
· "主角怎么突然知道/会 Y?"(信息来源)

只列**普通读者会问出来**的疑问,不列作者视角的设定疑问。每条 ≤40 字。

输出 JSON: {"questions":[{"q":"30 字疑问","kind":"决策|对手|线索|世界|信息"}]}
没有就给空数组。最多 5 条。"""


def _track_reader_questions(state, chapter_index: int, content: str) -> None:
    """每章扫稿提取读者可能的疑问,累积到 state.reader_questions_pending。

    格式: list[{q, kind, raised_chapter, age}]
    age = 当前章 - raised_chapter,age ≥ _QUESTION_PENDING_THRESHOLD → 下章必须回应
    """
    if not content or len(content) < 200:
        return

    from utils.json_utils import request_json_with_profile
    try:
        result = request_json_with_profile(
            system_prompt=_QUESTION_SYSTEM,
            user_prompt=(
                f"以下是第 {chapter_index} 章正文(节选),列出读者可能问的疑问:\n\n"
                + content[:5000]
            ),
            required_keys=["questions"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception:
        return  # 失败兜底

    if not isinstance(result, dict):
        return
    new_qs = result.get("questions") or []
    if not isinstance(new_qs, list):
        return

    if not hasattr(state, "reader_questions_pending") or state.reader_questions_pending is None:
        state.reader_questions_pending = []

    # 老化已有疑问 + 移除已解答的(粗启发:本章正文是否含老疑问的关键词命中即视为已答)
    aged: list = []
    for q in state.reader_questions_pending:
        if not isinstance(q, dict):
            continue
        # age 累加
        q_age = chapter_index - int(q.get("raised_chapter", chapter_index))
        # 老疑问的关键词在本章是否出现(粗启发:取 q 中前 3 个 ≥ 2 字汉字串作匹配)
        q_text = (q.get("q") or "").strip()
        if q_age >= 1 and _question_is_answered_in_content(q_text, content):
            continue  # 视为已答,移除
        q["age"] = q_age
        aged.append(q)

    # 加入新疑问
    for nq in new_qs[:5]:
        if not isinstance(nq, dict):
            continue
        qstr = (nq.get("q") or "").strip()
        if not qstr or len(qstr) < 5:
            continue
        # 同问去重(简单子串匹配)
        if any(qstr[:15] in (a.get("q") or "") for a in aged):
            continue
        aged.append({
            "q": qstr[:80],
            "kind": (nq.get("kind") or "").strip()[:10],
            "raised_chapter": chapter_index,
            "age": 0,
        })

    # 控容(最多保留最近 20 条)
    state.reader_questions_pending = aged[-20:]

    # 超阈值未回应 → progress_warning
    overdue = [q for q in state.reader_questions_pending
                if int(q.get("age", 0) or 0) >= _QUESTION_PENDING_THRESHOLD]
    if overdue:
        try:
            from persistence.checkpoint import add_progress_warning
            preview = " | ".join(f"「{q['q'][:30]}」" for q in overdue[:3])
            add_progress_warning(
                level="warn",
                source=f"chapter:{chapter_index}:reader_questions_overdue",
                message=(
                    f"{len(overdue)} 个读者疑问积压 ≥{_QUESTION_PENDING_THRESHOLD} 章未回应: "
                    f"{preview} —— 下章必须正面回应(让主角自己问出来 / 让旁人质疑 / 走剧情自然展开)"
                ),
            )
        except Exception:
            pass


def _question_is_answered_in_content(question: str, content: str) -> bool:
    """粗启发:从疑问中抽 2-3 个关键 token,在 content 找 ≥1 命中即视为已答。"""
    import re
    if not question or not content:
        return False
    # 抽 ≥ 2 字的连续汉字串(忽略疑问词/虚词)
    tokens = re.findall(r"[一-龥]{2,}", question)
    skip = {"为什么", "为何", "怎么", "什么", "怎样", "如何", "主角", "他们", "这个", "那个"}
    tokens = [t for t in tokens if t not in skip]
    if not tokens:
        return False
    hits = sum(1 for t in tokens[:5] if t in content)
    return hits >= 2  # ≥2 命中视为已答


def get_overdue_reader_questions(state) -> list[dict]:
    """供下章 directive 注入:返回积压超阈值的疑问列表。"""
    qs = getattr(state, "reader_questions_pending", None) or []
    return [q for q in qs if int(q.get("age", 0) or 0) >= _QUESTION_PENDING_THRESHOLD]


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
        "overdue_foreshadows": [],     # 伏笔超期未回收（埋了 N 章不出回收）
        "missed_lifecycle_nodes": [],  # lifecycle 节点过 target_chapter 未触发
        "missed_satisfaction_points": [],  # 爽点过 target_chapter 未触发
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

    # 5. 伏笔挂账——埋了但过 planned_resolve_chapter + grace 章仍未 resolved
    for fw in (state.foreshadow_items or []):
        if getattr(fw, "resolved", False):
            continue
        planned_resolve = int(getattr(fw, "planned_resolve_chapter", -1) or -1)
        if planned_resolve <= 0:
            continue  # 没设计兑现章号——不能机器对账（用户自由埋）
        overdue = current_chapter - planned_resolve
        if overdue < FORESHADOW_OVERDUE_GRACE:
            continue
        importance_v = getattr(getattr(fw, "importance", None), "value", "")
        report["overdue_foreshadows"].append({
            "fw_id": getattr(fw, "fw_id", ""),
            "content": (getattr(fw, "content", "") or "")[:60],
            "importance": importance_v,
            "planted_at": getattr(fw, "planted_chapter", -1),
            "planned_resolve": planned_resolve,
            "overdue_by": overdue,
            "action": "建议在合理情境兑现伏笔，或写入「延迟原因」显式 punt 到后续卷",
        })

    # 6. lifecycle 节点过期——target_chapter 已过 + grace 章但 triggered=False
    if state.power_system and state.power_system.special_abilities:
        for ab in state.power_system.special_abilities:
            for n in (getattr(ab, "lifecycle_nodes", None) or []):
                if getattr(n, "triggered", False):
                    continue
                tgt = int(getattr(n, "target_chapter", 0) or 0)
                if tgt <= 0:
                    continue  # 粗粒度只到卷的节点（未细化到章）跳过
                overdue = current_chapter - tgt
                if overdue < LIFECYCLE_OVERDUE_GRACE:
                    continue
                report["missed_lifecycle_nodes"].append({
                    "asset_name": ab.name,
                    "node_type": getattr(n, "node_type", ""),
                    "target_chapter": tgt,
                    "overdue_by": overdue,
                    "narrative_purpose": (getattr(n, "narrative_purpose", "") or "")[:50],
                    "action": (
                        f"应在第 {tgt} 章落地《{ab.name}》[{getattr(n, 'node_type', '')}] 节点，"
                        "但已过期未触发——建议立即在近章补落地，或在 ability_roadmap_planner 重排"
                    ),
                })

    # 7. 爽点过期——target_chapter 已过 + grace 但 triggered=False
    for sp in (state.satisfaction_points or []):
        if getattr(sp, "triggered", False):
            continue
        tgt = int(getattr(sp, "target_chapter", 0) or 0)
        if tgt <= 0:
            continue
        overdue = current_chapter - tgt
        if overdue < SP_OVERDUE_GRACE:
            continue
        report["missed_satisfaction_points"].append({
            "sp_id": getattr(sp, "sp_id", ""),
            "title": (getattr(sp, "title", "") or "")[:50],
            "intensity": getattr(sp, "intensity", 0),
            "target_chapter": tgt,
            "overdue_by": overdue,
            "action": (
                f"应在第 {tgt} 章爆发的爽点已过期——建议在近章补触发或推后到合理章节"
            ),
        })

    state.last_cohesion_report = report
    # P1-5: 每 20 章一次,检查主角内核漂移
    if current_chapter > 0 and current_chapter % 20 == 0:
        try:
            drift = _check_protagonist_core_drift(state, current_chapter)
            if drift:
                report["protagonist_core_drift"] = drift
        except Exception as _e:
            try:
                from persistence.checkpoint import add_progress_warning
                add_progress_warning(
                    level="warn",
                    source=f"chapter:{current_chapter}:core_drift",
                    message=f"主角内核漂移检查失败: {type(_e).__name__}: {str(_e)[:120]}",
                )
            except Exception:
                pass

    return report


# ═══════════════════════════════════════════════════════════════
#  P1-5: 主角内核漂移检查 (每 20 章)
# ═══════════════════════════════════════════════════════════════

_CORE_DRIFT_SYSTEM = """你是网文长期连贯性审计员——专项检查"主角内核漂移"。

═══ 内核三件套 ═══

每个主角档案声明了 3 个根基:
· fatal_flaw  致命弱点(如"逃避责任""不肯求人""易动恻隐之心")
· desire      内心真正渴望
· fear        最深恐惧

═══ 你的任务 ═══

读最近 20 章 summary,判定主角行为/选择/对白是否仍能 trace 到这 3 项:

· 仍贴合 = 主角立体度保持
· 漂移 = 这 3 项在最近 20 章几乎不可见(没体现弱点/没体现渴望/没体现恐惧)
· 翻转 = 主角行为与设定根基**相反**(如 fatal_flaw=逃避责任,但近 20 章他全是主动担责)

★ 漂移本身不一定坏——可能是成长。但需要作者**主动确认**:
  · 是规划的成长(已通过 character_arc 设计)→ 应该
  · 是 LLM 凭空把主角"开光成全能"→ 不应该

═══ 输出 JSON ═══

{
  "fatal_flaw_traceable": true/false,
  "fatal_flaw_evidence": "近 20 章某段证据 / 或 '未见'",
  "desire_traceable": true/false,
  "desire_evidence": "...",
  "fear_traceable": true/false,
  "fear_evidence": "...",
  "drift_severity": "none|minor|major",
  "drift_description": "30 字总结(none 时空串)"
}"""


def _check_protagonist_core_drift(state, current_chapter: int):
    """返回 None=未跑/无漂移 / dict=漂移报告。"""
    proto = next(
        (c for c in (state.characters or []) if getattr(c.role, "value", "") == "主角"),
        None
    )
    if not proto:
        return None
    fatal_flaw = (getattr(proto, "fatal_flaw", "") or "").strip()
    desire = (getattr(proto, "desire", "") or "").strip()
    fear = (getattr(proto, "fear", "") or "").strip()
    if not (fatal_flaw or desire or fear):
        return None  # 主角根基未声明 → 无法检查

    chapters = (state.completed_chapters or [])[-20:]
    if len(chapters) < 5:
        return None

    summaries = []
    for ch in chapters:
        idx = getattr(ch, "index", "?")
        s = (getattr(ch, "summary", "") or "")[:300]
        if s:
            summaries.append(f"[第{idx}章] {s}")
    if not summaries:
        return None

    user = (
        f"主角姓名: {proto.name}\n"
        f"fatal_flaw: {fatal_flaw or '(未声明)'}\n"
        f"desire: {desire or '(未声明)'}\n"
        f"fear: {fear or '(未声明)'}\n\n"
        f"最近 20 章 summary:\n" + "\n".join(summaries)
        + "\n\n按 schema 输出 JSON,判定 fatal_flaw/desire/fear 三项在近 20 章是否仍 trace 到。"
    )

    from utils.json_utils import request_json_with_profile
    try:
        result = request_json_with_profile(
            system_prompt=_CORE_DRIFT_SYSTEM,
            user_prompt=user,
            required_keys=["drift_severity"],
            usage="extractor",
            max_attempts=2,
            empty_ok=False,
        )
    except Exception:
        return None

    if not isinstance(result, dict):
        return None
    severity = (result.get("drift_severity") or "none").strip()
    if severity in ("major", "minor"):
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="warn" if severity == "major" else "info",
                source=f"chapter:{current_chapter}:protagonist_core_drift",
                message=(
                    f"主角内核漂移({severity}): {result.get('drift_description', '')[:100]} "
                    f"—— fatal_flaw 可追溯={result.get('fatal_flaw_traceable')} "
                    f"desire 可追溯={result.get('desire_traceable')} "
                    f"fear 可追溯={result.get('fear_traceable')}"
                ),
            )
        except Exception:
            pass
    return result


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
    if rep.get("overdue_foreshadows"):
        top = rep["overdue_foreshadows"][0]
        n = len(rep["overdue_foreshadows"])
        hints.append(
            f"⚠ 伏笔挂账（{n} 条）：例「{top['content']}」"
            f"（计划兑现章 {top['planned_resolve']}，已挂 {top['overdue_by']} 章）"
            "——建议在本章/本卷合理情境兑现。"
        )
    if rep.get("missed_lifecycle_nodes"):
        top = rep["missed_lifecycle_nodes"][0]
        n = len(rep["missed_lifecycle_nodes"])
        hints.append(
            f"⚠ lifecycle 节点过期（{n} 条）：例《{top['asset_name']}》"
            f"[{top['node_type']}] 应在第 {top['target_chapter']} 章落地、已过 {top['overdue_by']} 章。"
            "——若本章合理可补落地，或在 web UI 重排 ability_roadmap。"
        )
    if rep.get("missed_satisfaction_points"):
        top = rep["missed_satisfaction_points"][0]
        n = len(rep["missed_satisfaction_points"])
        hints.append(
            f"⚠ 爽点过期（{n} 条）：例「{top['title']}」（应在第 {top['target_chapter']} 章爆，"
            f"已过 {top['overdue_by']} 章）——建议本章/近章补触发。"
        )
    if rep.get("recap_suggestions"):
        hints.append(
            "ℹ 跨卷提醒：本卷开篇建议简短回顾上一卷的高潮/反转，唤醒读者记忆。"
        )
    return hints
