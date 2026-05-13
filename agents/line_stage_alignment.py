"""
叙事线 × 叙事舞台 对齐审计 —— 解决两个正交维度生成时不对齐的问题。

为什么不合并：
  · NarrativeLine = "故事的经线"——感情线/势力线/成长线/悬念线，跨多卷
  · StoryStage    = "故事的纬线"——卷内的"主角在哪做什么"场景容器
  · 两个维度独立有意义：同一卷多条线可共享同一舞台；同一条线可跨多个舞台

为什么需要对齐审计：
  · line_planner（Phase 3-A/3-B）规划时还没有 stages
  · stage_architect（Phase 6-A）规划时虽看到 lines 但不强制对应
  · 结果可能：line.phase 章节区间落在一个完全不能推进它的 stage 里
    → chapter_planner 设计章节时收到矛盾信号（"本章感情线推进 + 主角在塞外军营"）
    → writer 强行融合 → 逻辑断裂

本模块做：
  1. analyze_alignment(state) → 完整报告（phase 状态 + stage 状态 + 警告）
  2. get_planning_hints(state, chapter_index) → 给 chapter_planner 注入校验提醒
  3. 不改 lines/stages 数据 —— 只读分析 + 报告
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class StageMatch:
    """phase 与 stage 重叠的描述。"""
    stage_id: str
    stage_name: str
    overlap_start: int
    overlap_end: int
    overlap_chapters: int            # 重叠章数
    overlap_ratio: float             # 重叠占 phase 总章数的比例


@dataclass
class PhaseAlignment:
    """一条 phase 的对齐分析结果。"""
    line_id: str
    line_name: str
    line_scope: str                  # "global" / "volume"
    phase_index: int
    phase_name: str
    chapter_start: int
    chapter_end: int
    volume: int
    matches: list[StageMatch] = field(default_factory=list)
    status: str = "ok"               # "ok"=完全在一个 stage 里
                                     # "crosses_stages"=横跨多个 stage（不一定是 bug）
                                     # "no_stage"=完全没匹配到 stage（**bug**）
                                     # "partial"=部分章节没 stage 覆盖
                                     # "invalid"=phase 章号无效


@dataclass
class StageCoverage:
    """一个 stage 的 line 覆盖情况。"""
    stage_id: str
    stage_name: str
    volume: int
    chapter_start: int
    chapter_end: int
    covered_phases: list[dict] = field(default_factory=list)  # [{line_id, line_name, phase_index, phase_name}]
    line_count: int = 0              # 覆盖了多少条不同的 line
    status: str = "covered"          # "covered"=有 lines 在推进
                                     # "underused"=lines 太少（< 2）
                                     # "no_lines"=没有任何 line phase 在这里推进（**bug**）


# ═══════════════════════════════════════════════════════════════
#  核心分析
# ═══════════════════════════════════════════════════════════════

def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> Optional[tuple]:
    """两段闭区间的重叠；不重叠返回 None。"""
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    if s > e:
        return None
    return (s, e)


def _all_lines(state) -> list[tuple]:
    """返回 [(line, scope_str)] —— scope 用于报告 + planning hints。"""
    out = []
    for ln in (getattr(state, "global_lines", []) or []):
        out.append((ln, "global"))
    for ln in (getattr(state, "volume_lines", []) or []):
        out.append((ln, "volume"))
    return out


def analyze_alignment(state, volume_index: Optional[int] = None) -> dict:
    """
    完整对齐分析。volume_index=None 分析全书；指定卷号则只看那一卷。
    返回 dict（直接 JSON 化给前端 / API 用）。
    """
    stages = list(state.story_stages or [])
    if volume_index is not None:
        stages = [s for s in stages if getattr(s, "volume", 0) == volume_index]

    # 1. 每个 phase 的对齐情况
    phase_aligns: list[PhaseAlignment] = []
    for line, scope in _all_lines(state):
        for phase in (line.phases or []):
            if volume_index is not None and getattr(phase, "volume", 0) != volume_index:
                continue
            ps = int(getattr(phase, "chapter_start", 0) or 0)
            pe = int(getattr(phase, "chapter_end", 0) or 0)
            pa = PhaseAlignment(
                line_id=line.line_id,
                line_name=line.name,
                line_scope=scope,
                phase_index=phase.phase_index,
                phase_name=phase.name or f"phase{phase.phase_index}",
                chapter_start=ps, chapter_end=pe,
                volume=int(getattr(phase, "volume", 0) or 0),
            )
            if ps <= 0 or pe <= 0 or ps > pe:
                pa.status = "invalid"
                phase_aligns.append(pa)
                continue
            phase_chapters = pe - ps + 1

            # 找所有重叠 stage
            for st in stages:
                ss = int(getattr(st, "chapter_start", 0) or 0)
                se = int(getattr(st, "chapter_end", 0) or 0)
                if ss <= 0 or se <= 0:
                    continue
                ov = _overlap(ps, pe, ss, se)
                if ov is None:
                    continue
                ov_chs = ov[1] - ov[0] + 1
                pa.matches.append(StageMatch(
                    stage_id=st.stage_id, stage_name=st.name,
                    overlap_start=ov[0], overlap_end=ov[1],
                    overlap_chapters=ov_chs,
                    overlap_ratio=round(ov_chs / phase_chapters, 2) if phase_chapters > 0 else 0,
                ))
            # 判 status
            if not pa.matches:
                pa.status = "no_stage"
            else:
                covered_chs = sum(m.overlap_chapters for m in pa.matches)
                if len(pa.matches) > 1:
                    pa.status = "crosses_stages"
                elif covered_chs >= phase_chapters:
                    pa.status = "ok"
                else:
                    pa.status = "partial"
            phase_aligns.append(pa)

    # 2. 每个 stage 的覆盖情况
    stage_covs: list[StageCoverage] = []
    for st in stages:
        sc = StageCoverage(
            stage_id=st.stage_id, stage_name=st.name,
            volume=int(getattr(st, "volume", 0) or 0),
            chapter_start=int(getattr(st, "chapter_start", 0) or 0),
            chapter_end=int(getattr(st, "chapter_end", 0) or 0),
        )
        seen_lines = set()
        for pa in phase_aligns:
            for m in pa.matches:
                if m.stage_id == st.stage_id:
                    sc.covered_phases.append({
                        "line_id": pa.line_id, "line_name": pa.line_name,
                        "phase_index": pa.phase_index, "phase_name": pa.phase_name,
                        "overlap_chapters": m.overlap_chapters,
                    })
                    seen_lines.add(pa.line_id)
        sc.line_count = len(seen_lines)
        if sc.line_count == 0:
            sc.status = "no_lines"
        elif sc.line_count == 1:
            sc.status = "underused"   # 一卷舞台只覆盖一条线偏少（不一定是 bug）
        else:
            sc.status = "covered"
        stage_covs.append(sc)

    # 3. 汇总分数
    total_phases = len(phase_aligns)
    ok_phases = sum(1 for p in phase_aligns if p.status == "ok")
    crosses = sum(1 for p in phase_aligns if p.status == "crosses_stages")
    no_stage = sum(1 for p in phase_aligns if p.status == "no_stage")
    partial = sum(1 for p in phase_aligns if p.status == "partial")
    invalid = sum(1 for p in phase_aligns if p.status == "invalid")

    total_stages = len(stage_covs)
    no_lines_stages = sum(1 for s in stage_covs if s.status == "no_lines")
    underused_stages = sum(1 for s in stage_covs if s.status == "underused")

    # 评分（0-100）：critical=no_stage 和 no_lines_stages 重罚；crosses 不罚（合理跨越也很多）
    score = 100
    if total_phases:
        score -= int(no_stage / max(total_phases, 1) * 60)
        score -= int(invalid / max(total_phases, 1) * 30)
        score -= int(partial / max(total_phases, 1) * 20)
    if total_stages:
        score -= int(no_lines_stages / max(total_stages, 1) * 30)
        score -= int(underused_stages / max(total_stages, 1) * 10)
    score = max(0, min(100, score))

    # 4. 警告列表
    warnings: list[str] = []
    for p in phase_aligns:
        if p.status == "no_stage":
            warnings.append(
                f"⚠ 叙事线《{p.line_name}》phase{p.phase_index}「{p.phase_name}」"
                f"（章 {p.chapter_start}-{p.chapter_end}）"
                f"完全找不到对应的叙事舞台——这段时间主角不在能推进它的场景里"
            )
        elif p.status == "partial":
            covered_chs = sum(m.overlap_chapters for m in p.matches)
            phase_chs = p.chapter_end - p.chapter_start + 1
            warnings.append(
                f"ℹ 叙事线《{p.line_name}》phase{p.phase_index}「{p.phase_name}」"
                f"的 {phase_chs} 章中只有 {covered_chs} 章在某个 stage 里"
                f"——剩余 {phase_chs - covered_chs} 章可能没有合适的场景容器"
            )
        elif p.status == "invalid":
            warnings.append(
                f"⚠ 叙事线《{p.line_name}》phase{p.phase_index}「{p.phase_name}」"
                f"的章节范围无效（start={p.chapter_start}, end={p.chapter_end}）"
            )
    for s in stage_covs:
        if s.status == "no_lines":
            warnings.append(
                f"⚠ 舞台《{s.stage_name}》（V{s.volume} 章 {s.chapter_start}-{s.chapter_end}）"
                f"没有任何叙事线 phase 在此推进——这段章节没有大的剧情线索"
            )

    return {
        "overall_score": score,
        "scope": "all" if volume_index is None else f"V{volume_index}",
        "summary": {
            "total_phases": total_phases,
            "ok_phases": ok_phases,
            "crosses_stages": crosses,
            "no_stage": no_stage,
            "partial": partial,
            "invalid_phase": invalid,
            "total_stages": total_stages,
            "stages_no_lines": no_lines_stages,
            "stages_underused": underused_stages,
        },
        "phase_alignments": [
            {
                "line_id": p.line_id, "line_name": p.line_name, "line_scope": p.line_scope,
                "phase_index": p.phase_index, "phase_name": p.phase_name,
                "volume": p.volume,
                "chapter_start": p.chapter_start, "chapter_end": p.chapter_end,
                "status": p.status,
                "matches": [
                    {
                        "stage_id": m.stage_id, "stage_name": m.stage_name,
                        "overlap_start": m.overlap_start, "overlap_end": m.overlap_end,
                        "overlap_chapters": m.overlap_chapters,
                        "overlap_ratio": m.overlap_ratio,
                    }
                    for m in p.matches
                ],
            }
            for p in phase_aligns
        ],
        "stage_coverages": [
            {
                "stage_id": s.stage_id, "stage_name": s.stage_name,
                "volume": s.volume,
                "chapter_start": s.chapter_start, "chapter_end": s.chapter_end,
                "line_count": s.line_count,
                "covered_phases": s.covered_phases,
                "status": s.status,
            }
            for s in stage_covs
        ],
        "warnings": warnings,
    }


# ═══════════════════════════════════════════════════════════════
#  供 chapter_planner 用：本章是否有线/舞台冲突
# ═══════════════════════════════════════════════════════════════

def get_planning_hints(state, chapter_index: int) -> list[str]:
    """
    在生成第 chapter_index 章的蓝图前，检查本章实际处于哪个 stage、
    哪些 line phase 应该在本章推进；如果两者冲突，提醒 chapter_planner。
    """
    hints: list[str] = []

    # 找本章活跃 stage
    active_stage = None
    for st in (state.story_stages or []):
        ss = int(getattr(st, "chapter_start", 0) or 0)
        se = int(getattr(st, "chapter_end", 0) or 0)
        if ss <= chapter_index <= se:
            active_stage = st
            break

    # 找本章活跃 phases
    active_phases = []
    for line, scope in _all_lines(state):
        for ph in (line.phases or []):
            ps = int(getattr(ph, "chapter_start", 0) or 0)
            pe = int(getattr(ph, "chapter_end", 0) or 0)
            if ps <= chapter_index <= pe:
                active_phases.append((line, ph, scope))

    # 1. 没有活跃舞台 → 无场景容器，让 planner 注意从上下文推断
    if not active_stage:
        hints.append(
            f"⚠ 本章（{chapter_index}）不在任何已规划的叙事舞台范围内——"
            f"check：是不是过渡章/舞台衔接处？需要 planner 自己安排合适的「行至何处」作为本章场景。"
        )

    # 2. 没有活跃 phase → 没有大剧情推进
    if not active_phases:
        hints.append(
            f"ℹ 本章（{chapter_index}）没有任何叙事线 phase 在此推进——"
            f"如果是过渡/喘息章可以接受；如果应该有 phase 推进，说明 line_planner 漏了。"
        )

    # 3. 活跃 phase 与活跃 stage 不兼容
    if active_stage and active_phases:
        ss = int(active_stage.chapter_start)
        se = int(active_stage.chapter_end)
        for line, ph, scope in active_phases:
            ps = int(ph.chapter_start)
            pe = int(ph.chapter_end)
            ov = _overlap(ps, pe, ss, se)
            if ov is None:
                # 这种情况不会发生（chapter 同时在两者范围）
                continue
            # 如果 phase 几乎不在这个 stage 的范围里（重叠 < phase 一半），警告
            ov_ratio = (ov[1] - ov[0] + 1) / max(pe - ps + 1, 1)
            if ov_ratio < 0.3:
                hints.append(
                    f"⚠ 本章活跃叙事线《{line.name}》phase{ph.phase_index}「{ph.name}」"
                    f"主要发生在章 {ps}-{pe}，但本章在舞台《{active_stage.name}》（章 {ss}-{se}）——"
                    f"两者只有 {round(ov_ratio*100)}% 重叠，这条 phase 在本舞台里是否真能推进？"
                )

    return hints
