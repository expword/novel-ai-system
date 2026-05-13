"""
蓝图遵循度审计 —— P11 ActualChapterReport

输入：本章蓝图 + 实际正文
输出：
  · planned_scene_count / actual_scene_count
  · deviations 列表（哪些蓝图要求被改了）
  · blueprint_compliance（0-100，遵循度）

直接更新 state.completed_chapters 中本章的 summary 对应字段。
轻量——只在 director 写完一章后调用，不阻塞主流程。
"""
from __future__ import annotations
from typing import Optional


SYSTEM_TEMPLATE = """你是小说编辑助手。任务：对比"章节蓝图"和"实际正文"，找出执行时的偏差。

不评价文学好坏——只看：
1. 计划的几幕是否真的发生了
2. 蓝图里的"必发生事件"（key_events / dialogue_seeds / dramatic_beats）有没有兑现
3. 是否有蓝图未规划但实际写出的"超纲事件"

输出严格 JSON：
{{
  "actual_scene_count": 3,
  "deviations": ["第 2 幕规划反派揭面，实际只暗示未明面揭", "蓝图未规划但写了主角与小厮的私下对话"],
  "blueprint_compliance": 75,
  "compliance_notes": "整体执行 75% 蓝图——主线推进按计划，但缺少一个戏剧节拍"
}}

【尺度】
- 100 = 完全按蓝图，每条 dialogue_seed/dramatic_beat 都体现
- 80-99 = 主线和大部分细节都到位，少量小遗漏
- 50-79 = 主线在但多处偏离细节
- < 50 = 严重偏离蓝图（应该警示）

只列出"真的算偏差"的——蓝图里写得模糊的不强求精确执行。"""


def audit_compliance(
    blueprint,
    chapter_text: str,
    *,
    max_retries: int = 1,
) -> Optional[dict]:
    """对比蓝图和正文。失败返回 None。"""
    if not blueprint or not chapter_text:
        return None

    from json_utils import request_json

    # 拼蓝图摘要
    bp_lines = [f"章蓝图：{getattr(blueprint, 'chapter_delta', '')[:80]}"]
    if getattr(blueprint, "closing_hook", ""):
        bp_lines.append(f"计划钩子：{blueprint.closing_hook[:60]}")
    for i, beat in enumerate(getattr(blueprint, "scene_beats", []) or [], 1):
        line = f"幕{i}：{beat.scene_type}/{beat.location} - {beat.content[:80]}"
        ds = getattr(beat, "dialogue_seeds", []) or []
        if ds:
            line += f" | 计划对白点 {len(ds)} 条"
        sa = getattr(beat, "sensory_anchors", []) or []
        if sa:
            line += f" | 计划感官点 {len(sa)} 个"
        db = getattr(beat, "dramatic_beats", []) or []
        if db:
            line += f" | 戏剧节拍 {len(db)} 个"
        bp_lines.append(line)
    bp_text = "\n".join(bp_lines)

    user = (
        f"═══ 章节蓝图（写作前的计划）═══\n{bp_text}\n\n"
        f"═══ 实际正文 ═══\n{chapter_text[:8000]}\n\n"
        f"按 SYSTEM 要求输出 JSON。"
    )

    try:
        data = request_json(
            system=SYSTEM_TEMPLATE, user=user,
            required_keys=["actual_scene_count", "blueprint_compliance"],
            max_retries=max_retries,
            temperature=0.3,
            agent_name="BlueprintCompliance",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  [blueprint_compliance] 审计失败：{type(e).__name__}: {e}")
        return None

    if not data:
        return None

    try:
        compliance = int(data.get("blueprint_compliance", 100))
    except (TypeError, ValueError):
        compliance = 100
    compliance = max(0, min(100, compliance))

    deviations = []
    for d in (data.get("deviations") or []):
        if isinstance(d, str) and d.strip():
            deviations.append(d.strip()[:200])

    return {
        "actual_scene_count": int(data.get("actual_scene_count", 0) or 0),
        "deviations": deviations[:8],
        "blueprint_compliance": compliance,
        "compliance_notes": str(data.get("compliance_notes", ""))[:200],
    }


def update_chapter_summary(state, chapter_index: int, blueprint, chapter_text: str) -> Optional[dict]:
    """audit_compliance + 写回到 ChapterSummary。"""
    rep = audit_compliance(blueprint, chapter_text)
    if not rep:
        return None
    summary = next((c for c in (state.completed_chapters or []) if c.index == chapter_index), None)
    if summary:
        summary.planned_scene_count = len(getattr(blueprint, "scene_beats", []) or [])
        summary.actual_scene_count = rep["actual_scene_count"] or summary.planned_scene_count
        summary.deviations = rep["deviations"]
        summary.blueprint_compliance = rep["blueprint_compliance"]
    return rep
