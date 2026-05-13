"""
伏笔/爽点/反转的统一视图（轻量整合，不重构数据结构）。

之前问题：foreshadow_items / satisfaction_points / twist_chains.layers[*].clues_planted
三套独立系统，互相不知道对方存在 → 同一章可能重复埋两条相似的线索，
或反转 layer 依赖的某条 clue 写手以为已埋实际没埋。

本模块做轻量解决：
  · 提供"全书所有可见信息点"的统一只读视图
  · 重复检测（同一章计划埋的多条 clue 内容相似）
  · 兑现节奏分布（按章统计）
  · 不动数据结构 —— 三套系统继续各自存储，本模块只是 read-aggregate
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClueEntry:
    """统一视图中的一条线索/信息点条目（来自 foreshadow / sp / twist）。"""
    source: str              # "foreshadow" | "sp_setup" | "twist_clue" | "red_herring"
    source_id: str           # fw_id / sp_id / chain_id:layer / rh_id
    content: str             # 给读者看的内容
    plant_chapter: int = 0   # 计划/实际埋下的章
    payoff_chapter: int = 0  # 计划兑现的章（-1=未定）
    importance: str = ""     # 重要度（依源系统而异）
    extra: dict = field(default_factory=dict)


def build_registry(state) -> list[ClueEntry]:
    """收集所有可见信息点。"""
    out: list[ClueEntry] = []

    # 1. foreshadow_items
    for fw in (getattr(state, "foreshadow_items", []) or []):
        out.append(ClueEntry(
            source="foreshadow",
            source_id=getattr(fw, "fw_id", ""),
            content=getattr(fw, "content", "")[:200],
            plant_chapter=int(getattr(fw, "planted_chapter", 0) or 0),
            payoff_chapter=int(getattr(fw, "planned_resolve_chapter", -1) or -1),
            importance=getattr(getattr(fw, "importance", None), "value", str(getattr(fw, "importance", ""))),
            extra={
                "planted": getattr(fw, "planted_chapter", 0) > 0 or False,
                "resolved": bool(getattr(fw, "resolved", False)),
                "hidden_meaning": getattr(fw, "hidden_meaning", "")[:120],
            },
        ))

    # 2. satisfaction_points 的 setup_chain
    for sp in (getattr(state, "satisfaction_points", []) or []):
        for setup in (getattr(sp, "setup_chain", []) or []):
            out.append(ClueEntry(
                source="sp_setup",
                source_id=f"{getattr(sp, 'sp_id', '')}:setup",
                content=str(getattr(setup, "setup_content", ""))[:200] or str(setup)[:200],
                plant_chapter=int(getattr(setup, "chapter", 0) or 0),
                payoff_chapter=int(getattr(sp, "target_chapter", -1) or -1),
                importance=str(getattr(sp, "intensity", "")),
                extra={"sp_title": getattr(sp, "title", ""), "triggered": bool(getattr(sp, "triggered", False))},
            ))

    # 3. twist_chains.layers[*].clues_planted
    twist_sys = getattr(state, "twist_system", None)
    if twist_sys:
        for chain in (getattr(twist_sys, "chains", []) or []):
            for layer in (getattr(chain, "layers", []) or []):
                # reveal_anchor 解析章号（"第1卷第15章"）
                payoff_ch = -1
                anchor = getattr(layer, "reveal_anchor", "") or ""
                import re as _re
                m = _re.search(r"第\d+卷第(\d+)章", anchor)
                if m:
                    payoff_ch = int(m.group(1))
                for clue in (getattr(layer, "clues_planted", []) or []):
                    out.append(ClueEntry(
                        source="twist_clue",
                        source_id=f"{getattr(chain, 'chain_id', '')}:L{getattr(layer, 'layer', '')}",
                        content=str(clue)[:200],
                        plant_chapter=0,  # twist clues 没有具体章号
                        payoff_chapter=payoff_ch,
                        importance=str(getattr(layer, "layer", "")),
                        extra={"chain_title": getattr(chain, "title", "")},
                    ))

    # 4. red_herrings
    for rh in (getattr(state, "red_herrings", []) or []):
        out.append(ClueEntry(
            source="red_herring",
            source_id=getattr(rh, "rh_id", ""),
            content=getattr(rh, "content", "")[:200],
            plant_chapter=int(getattr(rh, "planted_chapter", 0) or 0),
            payoff_chapter=int(getattr(rh, "debunk_chapter", -1) or -1),
            importance="假线索",
            extra={"planted": bool(getattr(rh, "planted", False)),
                   "debunked": bool(getattr(rh, "debunked", False))},
        ))

    return out


def detect_duplicates(entries: list[ClueEntry], threshold: float = 0.6) -> list[dict]:
    """
    简易重复检测——同一章前后埋两条字面/语义相似的线索。
    用字符重叠率（不是真语义比对，但能抓到大部分重复）。
    """
    def _overlap(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        sa = set(a.replace(" ", ""))
        sb = set(b.replace(" ", ""))
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / max(len(sa), len(sb))

    by_chapter: dict[int, list[ClueEntry]] = {}
    for e in entries:
        if not e.plant_chapter or e.plant_chapter <= 0:
            continue
        by_chapter.setdefault(e.plant_chapter, []).append(e)

    out: list[dict] = []
    for ch, lst in by_chapter.items():
        for i, a in enumerate(lst):
            for b in lst[i+1:]:
                if a.source == b.source and a.source_id == b.source_id:
                    continue
                ov = _overlap(a.content, b.content)
                if ov >= threshold:
                    out.append({
                        "chapter": ch,
                        "overlap": round(ov, 2),
                        "a": {"source": a.source, "id": a.source_id, "content": a.content[:60]},
                        "b": {"source": b.source, "id": b.source_id, "content": b.content[:60]},
                    })
    return out


def chapter_distribution(entries: list[ClueEntry]) -> dict:
    """按章统计 plant 和 payoff 的密度——找出过载/空白章。"""
    plant_counts: dict[int, int] = {}
    payoff_counts: dict[int, int] = {}
    for e in entries:
        if e.plant_chapter and e.plant_chapter > 0:
            plant_counts[e.plant_chapter] = plant_counts.get(e.plant_chapter, 0) + 1
        if e.payoff_chapter and e.payoff_chapter > 0:
            payoff_counts[e.payoff_chapter] = payoff_counts.get(e.payoff_chapter, 0) + 1
    return {"plant": plant_counts, "payoff": payoff_counts}


def overview(state) -> dict:
    """汇总——给前端展示用。"""
    entries = build_registry(state)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    return {
        "total": len(entries),
        "by_source": by_source,
        "duplicates": detect_duplicates(entries),
        "distribution": chapter_distribution(entries),
        "entries": [
            {
                "source": e.source, "source_id": e.source_id,
                "content": e.content,
                "plant_chapter": e.plant_chapter, "payoff_chapter": e.payoff_chapter,
                "importance": e.importance, "extra": e.extra,
            }
            for e in entries
        ],
    }
