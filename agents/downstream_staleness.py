"""
DownstreamStaleness —— 检测用户编辑 canon 字段后的下游失效。

设计动机：
  · 用户在 web UI 改 power_system / world_setting / factions / characters 时，
    已经生成的 outline / chapter 可能与新数据不一致——例如：
      - 改朝代名 → 老 outline 还说"白鹿朝" → 与新 canon 矛盾
      - 改 asset 名字 → 老章节里 asset 名变成未定义术语
      - 删势力 → 老 outline 引用了不存在的势力名
  · 系统默认不告诉用户哪些产物失效——静默继续后果就是污染累积
  · 本模块复用 canon_checker.validate_text 跑一遍现有产物，把违规聚合写到
    progress_status warnings，让用户在 ⚠ 徽章里看到"以下 N 章 outline 失效"

设计原则：
  · 不预先建依赖图——直接用 validator 跑结果就是最准的"是否兼容"判定
  · 不阻塞、不重生、不删除——只告知，由用户决定
  · 按 changed_section 粒度聚合，每次编辑写一条 warning（同 source 自动去重）
"""
from __future__ import annotations
from persistence.state import NovelState


def scan_outlines_for_violations(state: NovelState) -> list[dict]:
    """跑所有 outlines 的 goal 过 validate_text，返回 [(volume, chapter, issues)]。"""
    from agents.canon_checker import validate_text
    out = []
    for vol in state.volumes:
        for o in (vol.chapter_outlines or []):
            ch_idx = o.get("index", 0)
            goal = (o.get("goal", "") or "").strip()
            if not goal:
                continue
            report = validate_text(state, f"outline:V{vol.index}Ch{ch_idx}.goal", goal)
            critical = [i for i in report["issues"] if i.get("severity") == "error"]
            if critical:
                out.append({
                    "volume": vol.index, "chapter": ch_idx,
                    "goal_preview": goal[:50],
                    "critical_count": len(critical),
                    "kinds": list({i["kind"] for i in critical}),
                })
    return out


def scan_chapters_for_violations(state: NovelState, project_dir: str = None) -> list[dict]:
    """跑所有已写章节正文过 validate_text，返回 [(volume, chapter, issues)]。

    chapter 文件较大，可选项目慎用。project_dir 不传时从 project_context 取。
    """
    import os
    from agents.canon_checker import validate_text
    if not project_dir:
        from project_mgmt import project_context
        project_dir = project_context.project_dir()
    out = []
    for sm in (state.completed_chapters or []):
        vol = state.get_volume(sm.volume_index)
        if not vol:
            continue
        path = os.path.join(project_dir, f"vol{vol.index:02d}", f"chapter_{sm.index:04d}.txt")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        report = validate_text(state, f"chapter:{sm.index}", content)
        critical = [i for i in report["issues"] if i.get("severity") == "error"]
        if critical:
            out.append({
                "volume": vol.index, "chapter": sm.index,
                "critical_count": len(critical),
                "kinds": list({i["kind"] for i in critical}),
                "terms": list({i["term"] for i in critical})[:5],
            })
    return out


# canon 类 section ——这些字段变化会影响下游 outline / chapter 合规性
# （其他 section 如 lines/satisfaction_points 不直接影响 canon 校验，跳过扫描以节省时间）
_CANON_SECTIONS = {
    "power_system", "world", "world_canon", "factions", "characters",
    "geography", "glossary",
}


def report_downstream_staleness(state: NovelState, changed_section: str,
                                  scan_chapters: bool = False) -> dict:
    """主入口——用户编辑某 section 后调用，扫描下游产物把违规聚合写 progress_warning。

    scan_chapters=False 时只扫 outline（快，秒级返回）；True 时也扫已写章节（慢）。
    返回 {"outline_violations": [...], "chapter_violations": [...]}.
    """
    if changed_section not in _CANON_SECTIONS:
        return {"outline_violations": [], "chapter_violations": []}

    from persistence.checkpoint import add_progress_warning, clear_progress_warnings

    outline_v = scan_outlines_for_violations(state)
    chapter_v = scan_chapters_for_violations(state) if scan_chapters else []

    # 同 source 去重——本 section 上一次扫描留的 warning 先清，再写新版
    clear_progress_warnings(source=f"staleness:{changed_section}:outlines")
    clear_progress_warnings(source=f"staleness:{changed_section}:chapters")

    if outline_v:
        ch_list = sorted({(v["volume"], v["chapter"]) for v in outline_v})
        # 按卷分组显示
        by_vol: dict[int, list[int]] = {}
        for v, c in ch_list:
            by_vol.setdefault(v, []).append(c)
        per_vol = "；".join(
            f"V{v}: 第 {','.join(str(c) for c in chs[:8])}"
            + (f" 等 {len(chs)} 章" if len(chs) > 8 else " 章")
            for v, chs in by_vol.items()
        )
        kinds = sorted({k for v in outline_v for k in v["kinds"]})
        add_progress_warning(
            level="warn",
            source=f"staleness:{changed_section}:outlines",
            message=(
                f"编辑 {changed_section} 后，{len(outline_v)} 条 outline.goal "
                f"与新 canon 不一致（{per_vol}；类型={kinds}）。"
                "建议在 web UI 触发对应卷的 outline 重生；或手改个别章节 outline。"
            ),
        )

    if chapter_v:
        ch_list = sorted({(v["volume"], v["chapter"]) for v in chapter_v})
        by_vol: dict[int, list[int]] = {}
        for v, c in ch_list:
            by_vol.setdefault(v, []).append(c)
        per_vol = "；".join(
            f"V{v}: 第 {','.join(str(c) for c in chs[:8])}"
            + (f" 等 {len(chs)} 章" if len(chs) > 8 else " 章")
            for v, chs in by_vol.items()
        )
        add_progress_warning(
            level="error",
            source=f"staleness:{changed_section}:chapters",
            message=(
                f"编辑 {changed_section} 后，{len(chapter_v)} 章已写正文 "
                f"与新 canon 冲突（{per_vol}）。建议重写这些章节，或忽略（已写的不会自动失效）。"
            ),
        )

    return {
        "outline_violations": outline_v,
        "chapter_violations": chapter_v,
    }
