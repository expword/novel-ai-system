"""
ProgressDashboard —— 作家仪表盘汇总（纯只读，零 LLM）。

═══ 解决的问题 ═══

critic 各维度评分 / 各 audit 状态 / 已埋未回收伏笔 / 反派 lifecycle / 配角戏份 /
主角已知事实 / 金句段 …… 这些信息散落在 state 各角落,作者**没有一个总览**
告诉他「这本书第 30 章状态: 节奏 6.2 / 角色 7.1 / 主角弧推进 23% / 已埋未回收
伏笔 8 个 / 反派搁置告警 1 处」。

ProgressDashboard 把这些信号聚合成单一 dict,前端 /api/projects/<id>/dashboard
端点直接返回。

═══ 7 大板块 ═══

· overall      整体进度(章号/卷号/完成比/总规划)
· quality      最近 critic 评分 / 平均分 / 各维度趋势
· plot_health  伏笔(已埋未回收/已回收/暴露过度)/ 爽点 / 反转 状态
· character_health 主角弧推进 / 配角戏份 top / 失踪 / 抢戏告警
· pacing       近 5 章张力分布 / 钩子类型分布 / 钩子多样性
· risks        progress_warnings 摘要 / lifecycle issues / canon issues
· quotables    最近章节的金句段

═══ 设计原则 ═══

· 纯只读,零 LLM,零 mutation
· 失败兜底:某板块抛错 → 该板块返回 {error: ...},不影响其他板块
· 字段命名英文 + 注释中文(前端可直接消费 JSON)
"""
from __future__ import annotations
from collections import Counter
from typing import Optional


def compute_dashboard(state) -> dict:
    """汇总作家仪表盘数据,返回 7 板块 dict。各板块独立 try/except。"""
    result: dict = {}
    for section_name, fn in [
        ("overall",          _compute_overall),
        ("quality",          _compute_quality),
        ("plot_health",      _compute_plot_health),
        ("character_health", _compute_character_health),
        ("pacing",           _compute_pacing),
        ("risks",            _compute_risks),
        ("quotables",        _compute_quotables),
    ]:
        try:
            result[section_name] = fn(state)
        except Exception as e:
            result[section_name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return result


# ═══════════════════════════════════════════════════════
#  各板块
# ═══════════════════════════════════════════════════════

def _compute_overall(state) -> dict:
    chapters = getattr(state, "completed_chapters", None) or []
    completed_count = len(chapters)
    last_ch = chapters[-1] if chapters else None
    current_chapter = getattr(last_ch, "index", 0) if last_ch else 0
    current_volume = getattr(last_ch, "volume_index", 0) if last_ch else 0

    # 总规划字数估算
    volumes = getattr(state, "volumes", None) or []
    total_planned_chapters = sum(int(getattr(v, "total_chapters", 0) or 0) for v in volumes)
    total_volumes = len(volumes)

    # 总字数
    total_words = sum(int(getattr(ch, "word_count", 0) or 0) for ch in chapters)

    return {
        "completed_chapters": completed_count,
        "current_chapter": current_chapter,
        "current_volume": current_volume,
        "total_volumes": total_volumes,
        "total_planned_chapters": total_planned_chapters,
        "total_words": total_words,
        "progress_pct": (
            int(100 * completed_count / total_planned_chapters)
            if total_planned_chapters > 0 else 0
        ),
    }


def _compute_quality(state) -> dict:
    chapters = getattr(state, "completed_chapters", None) or []
    scores: list[float] = []
    last_review = None
    dim_avgs: dict[str, list[float]] = {}

    for ch in chapters[-20:]:  # 取最近 20 章
        review = getattr(ch, "critic_review", None) or {}
        if not isinstance(review, dict):
            continue
        sc = review.get("score", 0)
        try:
            scores.append(float(sc))
        except Exception:
            continue
        last_review = review
        dims = review.get("dim_scores", {}) or {}
        if isinstance(dims, dict):
            for k, v in dims.items():
                try:
                    dim_avgs.setdefault(k, []).append(float(v))
                except Exception:
                    pass

    avg_last = sum(scores[-10:]) / max(len(scores[-10:]), 1) if scores else 0
    avg_all = sum(scores) / max(len(scores), 1) if scores else 0
    dim_summary = {k: round(sum(vs) / len(vs), 2) for k, vs in dim_avgs.items() if vs}

    return {
        "last_critic_score": (last_review or {}).get("score", 0),
        "last_critic_passed": (last_review or {}).get("passed", False),
        "avg_score_last_10": round(avg_last, 2),
        "avg_score_recent_20": round(avg_all, 2),
        "dim_scores_avg": dim_summary,
        "sample_count": len(scores),
    }


def _compute_plot_health(state) -> dict:
    fws = getattr(state, "foreshadow_items", None) or []
    sps = getattr(state, "satisfaction_points", None) or []
    rhs = getattr(state, "red_herrings", None) or []

    fw_planted = sum(1 for f in fws if int(getattr(f, "planted_chapter", -1) or -1) > 0)
    fw_resolved = sum(1 for f in fws if getattr(f, "resolved", False))
    fw_open = fw_planted - fw_resolved
    fw_overdue = sum(
        1 for f in fws
        if (not getattr(f, "resolved", False)
            and int(getattr(f, "planned_resolve_chapter", -1) or -1) > 0
            and int(getattr(f, "planned_resolve_chapter", -1)) < _current_chapter(state))
    )
    fw_over_exposed = sum(
        1 for f in fws
        if (not getattr(f, "resolved", False)
            and int(getattr(f, "exposure_count", 0) or 0) >= 4)
    )

    sp_triggered = sum(1 for s in sps if getattr(s, "triggered", False))
    sp_total = len(sps)

    rh_planted = sum(1 for r in rhs if getattr(r, "planted", False))
    rh_debunked = sum(1 for r in rhs if getattr(r, "debunked", False))

    return {
        "foreshadow_total": len(fws),
        "foreshadow_planted": fw_planted,
        "foreshadow_resolved": fw_resolved,
        "foreshadow_open": fw_open,
        "foreshadow_overdue": fw_overdue,
        "foreshadow_over_exposed": fw_over_exposed,
        "satisfaction_total": sp_total,
        "satisfaction_triggered": sp_triggered,
        "satisfaction_pct": (
            int(100 * sp_triggered / sp_total) if sp_total > 0 else 0
        ),
        "red_herring_planted": rh_planted,
        "red_herring_debunked": rh_debunked,
    }


def _compute_character_health(state) -> dict:
    chars = getattr(state, "characters", None) or []
    proto_arc_pct = _protagonist_arc_progress(state)
    stats_map = getattr(state, "supporting_cast_stats", None) or {}

    cast_top = []
    for entry in stats_map.values():
        if isinstance(entry, dict):
            cast_top.append({
                "name": entry.get("name", ""),
                "role": entry.get("role", ""),
                "appear_count": int(entry.get("appear_count", 0)),
                "last_seen_chapter": int(entry.get("last_seen_chapter", -1)),
            })
    cast_top.sort(key=lambda e: -e["appear_count"])
    cast_top = cast_top[:10]

    # 已 protagonist_known_facts 累积
    known_facts = getattr(state, "protagonist_known_facts", None) or []

    # 反派 lifecycle 健康度
    ant_lc = getattr(state, "antagonist_lifecycles", None) or {}
    ant_summary = []
    for name, lc in ant_lc.items():
        if not isinstance(lc, dict):
            continue
        nodes = lc.get("nodes") or []
        triggered = sum(1 for n in nodes if isinstance(n, dict) and n.get("triggered"))
        ant_summary.append({
            "name": name,
            "nodes_triggered": triggered,
            "nodes_total": len(nodes),
            "progress_pct": int(100 * triggered / len(nodes)) if nodes else 0,
        })

    return {
        "protagonist_arc_pct": proto_arc_pct,
        "protagonist_known_facts_count": len(known_facts),
        "supporting_cast_top": cast_top,
        "supporting_cast_total_tracked": len(stats_map),
        "characters_total": len(chars),
        "antagonist_lifecycles": ant_summary,
    }


def _compute_pacing(state) -> dict:
    chapters = getattr(state, "completed_chapters", None) or []
    last_5 = chapters[-5:]
    tensions = []
    hook_types = []
    word_counts = []
    for ch in last_5:
        t = getattr(ch, "tension", None)
        tensions.append(getattr(t, "value", str(t)) if t else "")
        hook_types.append((getattr(ch, "closing_hook_type", "") or ""))
        word_counts.append(int(getattr(ch, "word_count", 0) or 0))

    # 钩子多样性: 不同类型数 / 总章数
    non_empty_hooks = [h for h in hook_types if h]
    hook_diversity = (
        round(len(set(non_empty_hooks)) / len(non_empty_hooks), 2)
        if non_empty_hooks else 0
    )

    return {
        "last_5_tensions": tensions,
        "last_5_hook_types": hook_types,
        "last_5_word_counts": word_counts,
        "hook_diversity_ratio": hook_diversity,
        "avg_word_count_last_5": (
            int(sum(word_counts) / len(word_counts)) if word_counts else 0
        ),
    }


def _compute_risks(state) -> dict:
    """从 progress_status.json 读 warnings 摘要。"""
    summary = {"by_level": {"error": 0, "warn": 0, "info": 0}, "by_source_prefix": {}}
    warnings = _read_progress_warnings()
    for w in warnings:
        lvl = (w.get("level") or "info").lower()
        if lvl in summary["by_level"]:
            summary["by_level"][lvl] += 1
        src = (w.get("source") or "")
        prefix = src.split(":", 1)[0] if ":" in src else src
        summary["by_source_prefix"][prefix] = summary["by_source_prefix"].get(prefix, 0) + 1
    summary["total"] = len(warnings)
    # 最近 5 条
    summary["recent"] = [
        {"level": w.get("level", ""), "source": w.get("source", ""),
         "message": (w.get("message") or "")[:200]}
        for w in warnings[-5:]
    ]
    return summary


def _compute_quotables(state) -> dict:
    """最近 5 段金句。"""
    chapters = getattr(state, "completed_chapters", None) or []
    quotes: list[dict] = []
    for ch in reversed(chapters):
        ch_quotes = getattr(ch, "quotable_moments", None) or []
        if not isinstance(ch_quotes, list):
            continue
        ch_idx = getattr(ch, "index", -1)
        for q in ch_quotes:
            if not isinstance(q, dict):
                continue
            quotes.append({
                "chapter_index": ch_idx,
                "kind": q.get("kind", ""),
                "text": (q.get("text") or "")[:120],
                "impact_score": int(q.get("impact_score") or 0),
            })
            if len(quotes) >= 5:
                break
        if len(quotes) >= 5:
            break
    return {"recent": quotes, "count": len(quotes)}


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _current_chapter(state) -> int:
    chapters = getattr(state, "completed_chapters", None) or []
    if not chapters:
        return 0
    return int(getattr(chapters[-1], "index", 0) or 0)


def _protagonist_arc_progress(state) -> int:
    """主角弧推进百分比:已 triggered 的 milestone 数 / 总 milestone 数。"""
    pj = getattr(state, "protagonist_journey", None)
    if not pj:
        return 0
    milestones = getattr(pj, "milestones", None) or []
    if not milestones:
        return 0
    total = len(milestones)
    triggered = 0
    for m in milestones:
        # milestones 字段名可能是 actual_chapter / triggered / ...
        if getattr(m, "triggered", False) or int(getattr(m, "actual_chapter", -1) or -1) > 0:
            triggered += 1
    return int(100 * triggered / total) if total else 0


def _read_progress_warnings() -> list[dict]:
    """从当前项目的 progress_status.json 读 warnings。"""
    try:
        from project_mgmt import project_context
        import os, json
        path = os.path.join(project_context.project_dir(), "progress_status.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        warns = data.get("warnings", []) if isinstance(data, dict) else []
        return warns if isinstance(warns, list) else []
    except Exception:
        return []
