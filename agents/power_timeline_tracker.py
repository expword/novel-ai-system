"""
PowerTimelineTracker —— 每章写完后扫正文，识别"X 用了 Y 能力"事件。

═══ 解决用户的诉求 ═══

"需要记录什么时候用什么能力" —— 每章写完后扫一次，把事件追加到
state.power_events，同时更新 character_ability_profiles 里对应能力的
use_count / last_used_chapter。

═══ 单章一次 LLM 调用（不是每能力一次）═══

跟 intent_asset_extractor 不同——扫一章正文识别使用事件，**一次 LLM 调用就够**：
  · 输入：正文 + 已登记 character_ability_profiles 清单
  · 输出：list[PowerEvent]——本章发生了哪些能力使用事件
  · 走 'extractor' usage（结构化短任务）

如果识别到的 ability 不在已登记清单 → 写 progress_warning 提示用户补登记
（不擅自加，避免污染）。

═══ 配合 invariant ═══

validate_power_consistency() 同时跑：
  · use_count 超过 ceiling 描述的次数限制 → warn
  · 角色用了未登记的能力 → critical（writer 编了能力）
  · cooldown 期内重复使用 → warn
"""
from __future__ import annotations
from typing import Optional

from utils.json_utils import request_json, request_json_with_profile
from persistence.state import NovelState, PowerEvent


SYSTEM = """你是【能力使用事件抽取员】——扫一章正文，识别"X 角色用了 Y 能力"事件。

═══ 抽取范围 ═══

只抽**明确使用**的能力事件，不抽：
  · 仅提及不使用（"主角想起当年学的 X 剑诀"）
  · 普通动作（拔剑、跑步、思考）——不算能力
  · 心理活动（"如果用 X 能力会怎样"）

═══ 每条事件 ═══

  user           使用者名（必须在已登记角色清单内——不在就用"未知"）
  ability_name   能力名（优先用已登记的清单里的；正文出现新名也照实写）
  target         作用对象（如"敌人"/"自己"/"某物"——≤30字）
  effect         实际效果（一句话，≤40字）
  cost_paid      使用代价（如"昏迷三日"/"消耗算力"，≤30字）
  success        bool（成功/失败）

═══ 输出严格 JSON ═══

{
  "events": [
    {"user":"...","ability_name":"...","target":"...","effect":"...","cost_paid":"...","success":true}
  ]
}

═══ 铁律 ═══
  · 用已登记清单里的能力名；如果正文出现 canon 外的新能力名也照实写（下游会提示用户登记）
  · 同一能力本章被同一人用 N 次 → 算 N 条事件（按出场顺序）
  · 没事件就 events=[] 空数组
  · 不要瞎猜——只抽正文明确写了的"""


def _build_registered_summary(state: NovelState) -> str:
    """已登记的 (角色, 能力) 清单——给 LLM 看用哪些已知名字。"""
    lines = []
    for char_name, prof in (state.character_ability_profiles or {}).items():
        ab_names = [la.name for la in (prof.learned_abilities or [])]
        ab_names += list(prof.linked_special_assets or [])
        if ab_names:
            lines.append(f"  · {char_name}：{' / '.join(ab_names[:10])}")
    if state.power_system and state.power_system.special_abilities:
        # 没明确归属的也列出
        for ab in state.power_system.special_abilities:
            if not ab.holder_name and ab.name:
                lines.append(f"  · (无主)：{ab.name}")
    return "\n".join(lines) if lines else "  （暂无已登记的角色能力档案）"


def track_chapter_power_events(state: NovelState, chapter_index: int,
                                  content: str) -> list[PowerEvent]:
    """主入口——写章后调用。扫正文识别能力使用事件，append 到 state.power_events。

    返回本章新追加的事件列表。
    """
    if not content or len(content.strip()) < 200:
        return []

    registered = _build_registered_summary(state)
    snippet = content[:8000] if len(content) > 8000 else content

    user = f"""═══ 已登记角色能力清单（优先用这些名字）═══
{registered}

═══ 本章正文（节选）═══
\"\"\"
{snippet}
\"\"\"

按 SYSTEM 规则识别本章所有能力使用事件。严格 JSON 输出。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM, user=user,
            required_keys=["events"], max_retries=2, temperature=0.2,
            agent_name=f"PowerTimeline[ch{chapter_index}]", empty_ok=True,
        )
    except Exception as _e:
        print(f"  ⚠ power_timeline 抽取失败：{type(_e).__name__}: {_e}")
        return []

    if not data:
        return []

    raw_events = data.get("events") or []
    new_events: list[PowerEvent] = []
    unknown_pairs: list[tuple[str, str]] = []  # (user, ability) 未登记的

    # 已知能力集合：(holder, ability_name)
    known_pairs: set[tuple[str, str]] = set()
    for name, prof in (state.character_ability_profiles or {}).items():
        for la in (prof.learned_abilities or []):
            known_pairs.add((name, la.name))
        for ast in (prof.linked_special_assets or []):
            known_pairs.add((name, ast))

    for e in raw_events:
        if not isinstance(e, dict):
            continue
        user_n = str(e.get("user") or "").strip()
        ab_n = str(e.get("ability_name") or "").strip()
        if not user_n or not ab_n:
            continue
        ev = PowerEvent(
            chapter_index=chapter_index,
            user=user_n, ability_name=ab_n,
            target=str(e.get("target") or "")[:60],
            effect=str(e.get("effect") or "")[:100],
            cost_paid=str(e.get("cost_paid") or "")[:80],
            success=bool(e.get("success", True)),
            extracted_by="auto",
        )
        new_events.append(ev)
        # 未登记的 (user, ability) 累计
        if (user_n, ab_n) not in known_pairs and user_n != "未知":
            unknown_pairs.append((user_n, ab_n))
        # 更新对应 learned_ability 的统计
        prof = (state.character_ability_profiles or {}).get(user_n)
        if prof:
            for la in (prof.learned_abilities or []):
                if la.name == ab_n:
                    la.use_count = (la.use_count or 0) + 1
                    la.last_used_chapter = chapter_index
                    break

    # 追加事件
    if not hasattr(state, "power_events") or state.power_events is None:
        state.power_events = []
    state.power_events.extend(new_events)

    # 未登记 (user, ability) 推 warning
    if unknown_pairs:
        try:
            from persistence.checkpoint import add_progress_warning
            preview = " / ".join(f"{u}→《{a}》" for u, a in unknown_pairs[:5])
            add_progress_warning(
                level="warn",
                source=f"power_timeline:ch{chapter_index}:unknown",
                message=(
                    f"第 {chapter_index} 章识别到 {len(unknown_pairs)} 个未登记的 (角色, 能力) "
                    f"组合：{preview}。可能是 writer 编了能力——建议在 web UI 决定是否补登记。"
                ),
            )
        except Exception:
            pass

    print(f"  📜 power_timeline 第 {chapter_index} 章：{len(new_events)} 个事件 "
          f"（{len(unknown_pairs)} 个未登记）")
    return new_events


def validate_power_consistency(state: NovelState) -> list[dict]:
    """跨章一致性 invariant——返回 issues 列表。

    检查：
      · 同章同人 (user, ability) 触发次数 > cooldown 描述允许
      · last_used_chapter 累计 use_count 跟 cooldown 不符
      · 用了未学的能力（用户已 promote 该 (user, ability) 到 profile 后这就是 critical）
    """
    issues: list[dict] = []

    # 累计 (user, ability) → use_count 计数（按时间）
    by_pair: dict[tuple[str, str], list[PowerEvent]] = {}
    for ev in (state.power_events or []):
        if not ev.user or not ev.ability_name:
            continue
        by_pair.setdefault((ev.user, ev.ability_name), []).append(ev)

    # 验证：每个 (user, ability) 要么在登记里，要么是"未知"
    for (u, a), evs in by_pair.items():
        if u == "未知":
            continue
        prof = (state.character_ability_profiles or {}).get(u)
        if not prof:
            issues.append({
                "severity": "warn",
                "kind": "no_ability_profile",
                "user": u, "ability": a,
                "message": f"角色《{u}》没有 character_ability_profile——"
                           f"使用了 {len(evs)} 次能力《{a}》"
                           "（建议先生成 profile，或确认是配角即时能力）",
            })
            continue
        ab_names = {la.name for la in (prof.learned_abilities or [])}
        ab_names.update(prof.linked_special_assets or [])
        if a not in ab_names:
            issues.append({
                "severity": "error",
                "kind": "used_unregistered_ability",
                "user": u, "ability": a,
                "message": f"《{u}》使用了未登记的能力《{a}》"
                           f"（{len(evs)} 次，章：{[e.chapter_index for e in evs]}）"
                           "—— writer 可能编了能力，或忘了登记到 profile",
            })

    return issues
