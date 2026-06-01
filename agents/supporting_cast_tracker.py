"""
SupportingCastTracker —— 配角戏份监控（章后纯规则审计）。

═══ 解决的问题 ═══

major_supporting_refiner 设计配角细腻档案,但**配角写完会消失/抢戏/降智**没人管。
最常见病:
  · 第 5 章重点铺垫的师妹 → 第 30 章读者已经忘了
  · 反派从第 8 章后就再没出现
  · 某配角戏份突然暴增,抢主角风头

SupportingCastTracker 每章后扫 ChapterSummary 累积配角戏份统计:
  · appear_count       本卷累计出场章数
  · last_seen_chapter  最后出场章
  · chapter_appearances 出场章列表(供前端可视化)

每 N 章检查阈值:
  · 「重要配角(major_supporting)失踪 ≥10 章」 → progress_warning
  · 「单卷某配角出场比例 >30%」 → 抢戏 warning(主角戏份被稀释)

═══ 单章成本 ≈ 0（纯字符串匹配,无 LLM）═══

· 扫 ChapterSummary.summary + key_events 字符串
· 角色名命中即记 +1 appear_count
· 失败兜底:不更新统计 + 不报警

═══ 设计原则 ═══

· 不依赖 LLM(避免成本/速度问题)
· 数据挂 state.supporting_cast_stats(新加字段)
· 按 [[feedback_generic_prompts]] — 不硬编码具体项目角色名
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="supporting_cast_tracker.update_after_chapter",
    inputs=[
        "characters[*].name",
        "characters[*].role",
        "completed_chapters[*].summary",
        "completed_chapters[*].key_events",
    ],
    outputs=[
        "supporting_cast_stats",
        # + progress_warning(volume:N:cast_balance) on threshold breach
    ],
    invariants=[],
    notes=(
        "章后纯规则扫描配角出场。累积 state.supporting_cast_stats。"
        "失踪/抢戏超阈值 → progress_warning。零 LLM 成本。"
    ),
))


# ═══════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════

# 重要配角失踪超过此章数 → warning
MISSING_THRESHOLD_MAJOR = 10
MISSING_THRESHOLD_NORMAL = 20

# 单卷某配角出场比例超过此值 → 抢戏 warning
HOG_RATIO_THRESHOLD = 0.30  # 30%

# 每 N 章触发一次阈值检查(避免每章告警过多)
CHECK_EVERY = 5


@dataclass
class CastStats:
    """单个角色的累积统计。"""
    name: str
    role: str = ""                    # 主角/配角/反派/...
    appear_count: int = 0             # 全书累计出场章数
    chapter_appearances: list = field(default_factory=list)  # [章号]
    last_seen_chapter: int = -1
    first_seen_chapter: int = -1
    by_volume: dict = field(default_factory=dict)  # {volume_index: count}

    def to_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role,
            "appear_count": self.appear_count,
            "chapter_appearances": list(self.chapter_appearances),
            "last_seen_chapter": self.last_seen_chapter,
            "first_seen_chapter": self.first_seen_chapter,
            "by_volume": dict(self.by_volume),
        }


def update_after_chapter(state, chapter_index: int, volume_index: int = 0) -> dict:
    """
    章后调一次:扫本章 summary + key_events,更新 supporting_cast_stats。
    超阈值时(每 CHECK_EVERY 章一次)推 progress_warning。

    返回 {updated_count, missing_majors, hog_warnings} 统计。
    """
    try:
        return _update_impl(state, chapter_index, volume_index)
    except Exception as e:
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="warn",
                source=f"chapter:{chapter_index}:cast",
                message=f"配角戏份追踪失败: {type(e).__name__}: {str(e)[:120]}",
            )
        except Exception:
            pass
        return {"updated_count": 0, "missing_majors": [], "hog_warnings": []}


def _update_impl(state, chapter_index: int, volume_index: int) -> dict:
    chapters = list(getattr(state, "completed_chapters", None) or [])
    if not chapters:
        return {"updated_count": 0, "missing_majors": [], "hog_warnings": []}

    # 找到本章 summary
    current = None
    for ch in chapters:
        if getattr(ch, "index", -1) == chapter_index:
            current = ch
            break
    if not current:
        return {"updated_count": 0, "missing_majors": [], "hog_warnings": []}

    haystack = _extract_text_haystack(current)
    if not haystack:
        return {"updated_count": 0, "missing_majors": [], "hog_warnings": []}

    # 确保 state.supporting_cast_stats 存在
    if not hasattr(state, "supporting_cast_stats") or state.supporting_cast_stats is None:
        state.supporting_cast_stats = {}

    stats_map: dict = state.supporting_cast_stats

    # 扫所有角色(主角也算,但抢戏检查时排除)
    updated = 0
    chars = getattr(state, "characters", None) or []
    for c in chars:
        name = getattr(c, "name", "")
        if not name or len(name) < 2:
            continue  # 太短的名字易误命中
        if name not in haystack:
            continue
        # 命中 → 更新 stats
        role_val = getattr(getattr(c, "role", None), "value", str(getattr(c, "role", "")))
        entry = stats_map.get(name)
        if not isinstance(entry, dict):
            entry = {
                "name": name, "role": role_val,
                "appear_count": 0, "chapter_appearances": [],
                "last_seen_chapter": -1, "first_seen_chapter": chapter_index,
                "by_volume": {},
            }
            stats_map[name] = entry
        if chapter_index not in entry["chapter_appearances"]:
            entry["chapter_appearances"].append(chapter_index)
            entry["appear_count"] = len(entry["chapter_appearances"])
            entry["last_seen_chapter"] = chapter_index
            if entry["first_seen_chapter"] < 0:
                entry["first_seen_chapter"] = chapter_index
            vol_key = str(volume_index)
            entry["by_volume"][vol_key] = int(entry["by_volume"].get(vol_key, 0)) + 1
            updated += 1
        # role 同步(以防 character.role 后期变化)
        if role_val and entry.get("role") != role_val:
            entry["role"] = role_val

    # 每 CHECK_EVERY 章 进行阈值检查
    missing_majors: list[dict] = []
    hog_warnings: list[dict] = []
    if chapter_index % CHECK_EVERY == 0:
        missing_majors, hog_warnings = _check_thresholds(
            state, chapter_index, volume_index, chapters
        )
        _surface_warnings(chapter_index, missing_majors, hog_warnings)

    return {
        "updated_count": updated,
        "missing_majors": missing_majors,
        "hog_warnings": hog_warnings,
    }


def _check_thresholds(
    state, chapter_index: int, volume_index: int, chapters: list
) -> tuple[list[dict], list[dict]]:
    """检查 "重要配角失踪" + "配角抢戏" 两类阈值。"""
    missing_majors: list[dict] = []
    hog_warnings: list[dict] = []

    stats_map = getattr(state, "supporting_cast_stats", None) or {}
    chars = getattr(state, "characters", None) or []

    # ── 1. 重要配角失踪 ─────────────────────────
    # 重要配角定义:role 含"主要"/"配角"且 appear_count >= 3(早期出现过) 但近 N 章没出现
    for c in chars:
        name = getattr(c, "name", "")
        role_val = getattr(getattr(c, "role", None), "value", str(getattr(c, "role", "")))
        if role_val in ("主角", ""):
            continue
        entry = stats_map.get(name)
        if not entry or entry.get("appear_count", 0) < 3:
            continue
        last_seen = entry.get("last_seen_chapter", -1)
        gap = chapter_index - last_seen
        threshold = (MISSING_THRESHOLD_MAJOR
                      if ("主要" in role_val or "重要" in role_val or "配角" == role_val)
                      else MISSING_THRESHOLD_NORMAL)
        if gap > threshold:
            missing_majors.append({
                "name": name, "role": role_val,
                "last_seen_chapter": last_seen,
                "gap_chapters": gap,
                "threshold": threshold,
            })

    # ── 2. 单卷某配角抢戏 ───────────────────────
    # 本卷所有 completed chapter 中,某配角出现比例 > HOG_RATIO_THRESHOLD
    vol_chapters_total = 0
    for ch in chapters:
        ch_vol = getattr(ch, "volume_index", None)
        if ch_vol == volume_index:
            vol_chapters_total += 1
    if vol_chapters_total >= 5:  # 本卷至少 5 章再判
        for name, entry in stats_map.items():
            if not isinstance(entry, dict):
                continue
            role_val = entry.get("role", "")
            if role_val == "主角":
                continue
            vol_count = int(entry.get("by_volume", {}).get(str(volume_index), 0))
            if vol_count == 0:
                continue
            ratio = vol_count / vol_chapters_total
            if ratio >= HOG_RATIO_THRESHOLD:
                hog_warnings.append({
                    "name": name, "role": role_val,
                    "volume_index": volume_index,
                    "vol_appearance_count": vol_count,
                    "vol_total_chapters": vol_chapters_total,
                    "ratio_pct": int(ratio * 100),
                })

    return missing_majors, hog_warnings


def _surface_warnings(chapter_index: int, missing: list[dict], hogs: list[dict]) -> None:
    if not missing and not hogs:
        return
    try:
        from persistence.checkpoint import add_progress_warning
        if missing:
            msg = (
                "重要配角长期失踪(下章 chapter_planner 应考虑安排回归戏): "
                + " | ".join(
                    f"{m['name']}({m['role']}) 已 {m['gap_chapters']} 章未出场"
                    for m in missing[:5]
                )
            )
            add_progress_warning(level="warn", source=f"chapter:{chapter_index}:cast_missing", message=msg)
        if hogs:
            msg = (
                "配角戏份比例告警(可能抢主角风头): "
                + " | ".join(
                    f"{h['name']}({h['role']}) V{h['volume_index']} 出场 {h['ratio_pct']}%"
                    for h in hogs[:5]
                )
            )
            add_progress_warning(level="warn", source=f"chapter:{chapter_index}:cast_hog", message=msg)
    except Exception:
        pass


def _extract_text_haystack(summary) -> str:
    """从 ChapterSummary 提取角色匹配用的文本。"""
    parts: list[str] = []
    s = getattr(summary, "summary", "") or ""
    if s:
        parts.append(s)
    ke = getattr(summary, "key_events", None) or []
    if isinstance(ke, list):
        for e in ke:
            parts.append(str(e))
    return "\n".join(parts)


def get_cast_stats_summary(state, top_n: int = 10) -> list[dict]:
    """供前端/仪表盘读:返回出场最多的 top_n 配角。"""
    stats_map = getattr(state, "supporting_cast_stats", None) or {}
    items = []
    for name, entry in stats_map.items():
        if not isinstance(entry, dict):
            continue
        items.append(entry)
    items.sort(key=lambda e: -int(e.get("appear_count", 0)))
    return items[:top_n]


# ═══════════════════════════════════════════════════════
#  P1-2: 配角"标志性动作"复现率扫描
# ═══════════════════════════════════════════════════════

# 配角出场 ≥ MIN_APPEAR 次,但 signature_mannerisms 复现率 < MIN_RECURRENCE_RATE
# 视为"标志失效",写 progress_warning 提示下章 writer 复用
MIN_APPEAR_FOR_RECURRENCE_CHECK = 3
MIN_RECURRENCE_RATE = 0.50  # 至少 50% 出场章应复现一次标志动作


def scan_mannerism_recurrence(state, chapter_index: int, lookback: int = 10) -> list[dict]:
    """扫描最近 N 章的角色 signature_mannerisms 复现频率。

    返回 list[{name, role, mannerism_count, appearance_count, recurrence_rate, mannerisms}]
    复现率 < MIN_RECURRENCE_RATE 的会触发 progress_warning。
    """
    chapters = list(getattr(state, "completed_chapters", None) or [])[-lookback:]
    if not chapters:
        return []

    weak: list[dict] = []
    for c in (getattr(state, "characters", None) or []):
        role_val = getattr(getattr(c, "role", None), "value", str(getattr(c, "role", "")))
        if role_val == "主角":
            continue
        name = getattr(c, "name", "")
        if not name or len(name) < 2:
            continue
        mannerisms = list(getattr(c, "signature_mannerisms", None) or [])
        if not mannerisms:
            continue

        # 统计该角色在 lookback 章节中的出场次数 + 标志动作出现次数
        appear = 0
        manner_hit_chapters = 0
        for ch in chapters:
            haystack = (
                (getattr(ch, "summary", "") or "")
                + "\n" + "\n".join(str(e) for e in (getattr(ch, "key_events", None) or []))
            )
            if name not in haystack:
                continue
            appear += 1
            # 任一标志动作的关键 token(取前 6 字)出现 = 算命中
            for m in mannerisms:
                token = (m or "").strip()[:6]
                if token and token in haystack:
                    manner_hit_chapters += 1
                    break

        if appear < MIN_APPEAR_FOR_RECURRENCE_CHECK:
            continue
        rate = manner_hit_chapters / appear if appear else 0
        if rate < MIN_RECURRENCE_RATE:
            weak.append({
                "name": name,
                "role": role_val,
                "appearance_count": appear,
                "mannerism_hit_count": manner_hit_chapters,
                "recurrence_rate": round(rate, 2),
                "mannerisms": mannerisms[:3],
            })

    if weak:
        try:
            from persistence.checkpoint import add_progress_warning
            msg = (
                f"配角标志动作复现率低(标志失效): "
                + " | ".join(
                    f"{w['name']}({w['role']}) 出场 {w['appearance_count']} 章"
                    f"但仅 {w['mannerism_hit_count']} 章用了标志动作({int(w['recurrence_rate']*100)}%)"
                    for w in weak[:3]
                )
                + " —— 下章 writer 应主动复用 signature_mannerisms"
            )
            add_progress_warning(
                level="info", source=f"chapter:{chapter_index}:mannerism_recurrence",
                message=msg,
            )
        except Exception:
            pass
    return weak
