"""
LinePlannerAgent — 规划全局叙事线（贯穿全书）+ 每卷专属叙事线。
全局线一次性全部规划；卷内线在该卷开始前规划。
"""
import json
from json_utils import repair_json, safe_parse, pick_list, request_json
from llm import system_user
from state import (
    NovelState, NarrativeLine, LinePhase, LineType, LineScope, TensionLevel
)
from config import (
    GLOBAL_STORY_LINES, GLOBAL_EMOTION_LINES, GLOBAL_CHARACTER_LINES,
    VOLUME_STORY_LINES, VOLUME_EMOTION_LINES, VOLUME_CHARACTER_LINES,
)


SYSTEM = """你是顶级叙事结构设计师，专注多线并行长篇小说。
【单主角铁律】
整部小说只有一个主角。所有叙事线——无论是"故事线""情感线""人物线"还是"悬疑线"——最终都必须投射到主角身上。
- 可以有不直接以主角为主视角的线（比如反派阴谋线），但其展开方向必须会在某一阶段收拢到主角的处境/抉择/成长上。
- 严禁存在"与主角毫无交集，纯粹背景板"的叙事线。
- 线索多可以，但每一条都要说清"这条线最终如何作用于主角"。

【分形起承转合】
每一条叙事线自身也是一次完整的起承转合——由若干 phase 承担起/承/转/合。
每一条线的所有 phase 加起来必须能组成完整的起→承→转→合，缺段即视为设计失败。
每个 phase 的 name 应该能看出它在线里的结构角色（如"初识/积累/破裂/和解"对应起承转合）。

【其他要求】
- 线与线之间有交叉点（冲突/配合/对比），交叉点最好落在主角身上
- 张力节奏高低错落，不能全程高亢
张力级别只能用：平静/上升/高潮/下落/反转
输出严格JSON。"""


# ─────────────────────────────────────────────────
#  全局线（一次性规划）
# ─────────────────────────────────────────────────

def plan_global_lines(state: NovelState) -> None:
    """规划贯穿全书的全局叙事线——按线类型分三批生成（故事线/情感线/人物线），每批一次 LLM 调用。"""
    from agents import require_upstream
    if not require_upstream(state, "LinePlanner",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    ):
        return
    state.global_lines = []
    _plan_global_by_type(state, "故事线", count=GLOBAL_STORY_LINES,
                          desc_hint="主线必须围绕主角的核心追求/核心矛盾；支线在某节点作用于主角")
    _plan_global_by_type(state, "情感线", count=GLOBAL_EMOTION_LINES,
                          desc_hint="主角的核心感情线——不是独立故事，是主角心路的外化")
    _plan_global_by_type(state, "人物线", count=GLOBAL_CHARACTER_LINES,
                          desc_hint="主角的成长蜕变线——从第1卷到最后一卷的内在变化")
    print(f"  ✓ 全局叙事线总计：{len(state.global_lines)} 条（分三批生成）")
    for ln in state.global_lines:
        _print_line_summary(ln)


def _plan_global_by_type(state: NovelState, line_type: str, count: int, desc_hint: str) -> None:
    """为单一类型的全局线做一次 LLM 调用。"""
    if count <= 0:
        return
    total_chapters = sum(v.total_chapters for v in state.volumes)
    volumes_desc = "\n".join(
        f"第{v.index}卷《{v.title}》[第{v.chapter_start}-{v.chapter_end}章]：{v.theme} — {v.arc[:60]}"
        for v in state.volumes
    )
    existing_brief = "\n".join(
        f"- [{ln.line_type.value}] {ln.name}：{ln.description[:50]}"
        for ln in state.global_lines
    ) or "（尚无已规划的全局线）"

    prompt = f"""
为《{state.title}》规划贯穿全书的【{line_type}】（{count} 条）。

全书共 {len(state.volumes)} 卷，{total_chapters} 章。

各卷概况：
{volumes_desc}

人物：
{state.character_brief_list()}

已设计的其他全局线（避免重复、寻找交叉点）：
{existing_brief}

═══ 要求 ═══
- 本批只设计【{line_type}】，共 {count} 条
- 设计提示：{desc_hint}
- 每条线必须在 description 里说清"对主角意味着什么"
- 每条线分 {len(state.volumes)*2}~{len(state.volumes)*3} 个阶段，阶段覆盖卷结构
- 每条线的 phases 合起来要形成完整起承转合，phase name 要能看出结构角色（如"初识→试探→破裂→和解"）
- 张力节奏高低错落，不能全程高亢

输出 JSON：
{{
  "global_lines": [
    {{
      "line_id": "唯一英文id（如 main_quest / romance_main / hero_growth）",
      "line_type": "{line_type}",
      "name": "线名称",
      "description": "整条线的叙事目标 + 对主角的意义（100字）",
      "characters": ["相关角色名"],
      "volume_range": [起始卷号, 结束卷号],
      "phases": [
        {{
          "phase_index": 1,
          "name": "阶段名称（能看出起承转合角色）",
          "description": "本阶段叙事目标（80字）",
          "volume": 所在卷号,
          "chapter_start": 全书章节起始编号,
          "chapter_end": 全书章节结束编号,
          "tension": "平静|上升|高潮|下落|反转"
        }}
      ]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["global_lines", "lines", "items", "narrative_lines"],
        min_items=1, max_retries=3, temperature=0.7,
        agent_name=f"LinePlanner[全局·{line_type}]",
        empty_ok=True,
    )
    lines_data = pick_list(data, "global_lines", "lines", "items", "narrative_lines") if data else []
    if not lines_data:
        print(f"    {line_type} 批次跳过（LLM 重试失败）")
        return
    new_lines = _parse_lines(lines_data, LineScope.GLOBAL)
    state.global_lines.extend(new_lines)
    print(f"    {line_type}：+{len(new_lines)} 条")


# ─────────────────────────────────────────────────
#  卷内线（每卷开始前规划）
# ─────────────────────────────────────────────────

def plan_volume_lines(state: NovelState, volume_index: int) -> None:
    """规划指定卷的专属叙事线，追加到 state.volume_lines。"""
    vol = state.get_volume(volume_index)
    if not vol:
        return

    active_chars = state.active_characters_in_volume(volume_index)
    chars_str = "\n".join(c.brief() for c in active_chars)

    global_lines_in_vol = "\n".join(
        f"- [{ln.line_type.value}] {ln.name}：" + (
            phase.description if (phase := ln.get_phase_for_chapter(vol.chapter_start)) else "本卷无活跃阶段"
        )
        for ln in state.global_lines
    )

    char_volume_arcs = "\n".join(
        f"- {c.name}：{c.volume_arcs.get(volume_index, '保持前卷状态')}"
        for c in active_chars
    )

    prompt = f"""
请为第{volume_index}卷《{vol.title}》规划卷内专属叙事线。

卷章节范围：第{vol.chapter_start}-{vol.chapter_end}章（共{vol.total_chapters}章）
卷主题：{vol.theme}
卷弧线：{vol.arc}
本卷主要对手：{vol.volume_antagonist}
本卷关键事件：{', '.join(vol.key_events[:3])}

本卷活跃角色：
{chars_str}

本卷角色弧线：
{char_volume_arcs}

全局线在本卷的状态（本卷写作需配合这些）：
{global_lines_in_vol}

前序情节（近期）：
{state.last_n_summaries(3)}

卷内线要求：
- 故事线：{VOLUME_STORY_LINES}条（卷内主要冲突线，主角必须深度介入，卷尾前必须解决一个）
- 情感线：{VOLUME_EMOTION_LINES}条（本卷对主角影响最深的人物关系，必须作用于主角）
- 人物线：{VOLUME_CHARACTER_LINES}条（本卷重要角色的成长——若非主角，必须说明该角色本卷变化如何影响主角）
- 每条线的 description 必须显式说明"对主角意味着什么"
- 每条线的所有 phase 合起来要形成完整起承转合（本卷内完成）
- 各线阶段需覆盖整卷，合理分布张力

输出JSON：
{{
  "volume_lines": [
    {{
      "line_id": "vol{volume_index}_唯一英文id",
      "line_type": "故事线|情感线|人物线|悬疑线",
      "name": "线名称",
      "description": "本线在本卷的叙事目标（80字）",
      "characters": ["角色名"],
      "phases": [
        {{
          "phase_index": 1,
          "name": "阶段名称",
          "description": "阶段目标（60字）",
          "volume": {volume_index},
          "chapter_start": 全书章节编号,
          "chapter_end": 全书章节编号,
          "tension": "平静|上升|高潮|下落|反转"
        }}
      ]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["volume_lines", "lines", "items", "narrative_lines"],
        min_items=1,
        max_retries=4, temperature=0.7, agent_name=f"LinePlanner[V{volume_index}]",
        empty_ok=True,
    )
    lines_data = pick_list(data, "volume_lines", "lines", "items", "narrative_lines") if data else []
    if not lines_data:
        print(f"  ⚠ LinePlanner 第{volume_index}卷跳过（LLM 重试失败）")
        return
    new_lines = _parse_lines(lines_data, LineScope.VOLUME)

    # 设置 volume_range
    for ln in new_lines:
        ln.volume_range = (volume_index, volume_index)

    state.volume_lines.extend(new_lines)
    print(f"  ✓ 第{volume_index}卷专属线：{len(new_lines)} 条")
    for ln in new_lines:
        _print_line_summary(ln)


def plan_all_volume_lines_parallel(state: NovelState) -> None:
    """
    并行版本：一次性把 NUM_VOLUMES 个 plan_volume_lines 并发跑。
    每个卷的 LLM 调用是独立的（只依赖已固定的 global_lines 和 state 元数据），
    因此安全可并发。结果按卷号顺序追加到 state.volume_lines。
    """
    from agents import require_upstream
    if not require_upstream(state, "LinePlanner[卷内]",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
        global_lines=lambda s: bool(s.global_lines),
    ):
        return
    from config import NUM_VOLUMES, PARALLEL_WORKERS
    from concurrency import parallel_map

    vol_indexes = [vi for vi in range(1, NUM_VOLUMES + 1) if state.get_volume(vi)]
    if not vol_indexes:
        return

    print(f"  并发为 {len(vol_indexes)} 卷规划卷内叙事线（max_workers={PARALLEL_WORKERS}）...")

    def _one_volume(vi: int) -> tuple[int, list]:
        """只跑 LLM + parse，不写 state——返回 (vi, parsed_lines)。"""
        vol = state.get_volume(vi)
        if not vol:
            return (vi, [])
        active_chars = state.active_characters_in_volume(vi)
        chars_str = "\n".join(c.brief() for c in active_chars)
        global_lines_in_vol = "\n".join(
            f"- [{ln.line_type.value}] {ln.name}：" + (
                phase.description if (phase := ln.get_phase_for_chapter(vol.chapter_start)) else "本卷无活跃阶段"
            )
            for ln in state.global_lines
        )
        char_volume_arcs = "\n".join(
            f"- {c.name}：{c.volume_arcs.get(vi, '保持前卷状态')}"
            for c in active_chars
        )
        prompt = f"""
请为第{vi}卷《{vol.title}》规划卷内专属叙事线。

卷章节范围：第{vol.chapter_start}-{vol.chapter_end}章（共{vol.total_chapters}章）
卷主题：{vol.theme}
卷弧线：{vol.arc}
本卷主要对手：{vol.volume_antagonist}
本卷关键事件：{', '.join(vol.key_events[:3])}

本卷活跃角色：
{chars_str}

本卷角色弧线：
{char_volume_arcs}

全局线在本卷的状态（本卷写作需配合这些）：
{global_lines_in_vol}

前序情节（近期）：
{state.last_n_summaries(3)}

卷内线要求：
- 故事线：{VOLUME_STORY_LINES}条（卷内主要冲突线，主角必须深度介入，卷尾前必须解决一个）
- 情感线：{VOLUME_EMOTION_LINES}条（本卷对主角影响最深的人物关系）
- 人物线：{VOLUME_CHARACTER_LINES}条（本卷重要角色的成长）
- 每条线的 description 必须显式说明"对主角意味着什么"
- 每条线的所有 phase 合起来要形成完整起承转合（本卷内完成）

输出JSON：
{{
  "volume_lines": [
    {{
      "line_id": "vol{vi}_唯一英文id",
      "line_type": "故事线|情感线|人物线|悬疑线",
      "name": "线名称",
      "description": "本线在本卷的叙事目标（80字）",
      "characters": ["角色名"],
      "phases": [
        {{"phase_index": 1, "name": "阶段名称", "description": "阶段目标（60字）",
          "volume": {vi}, "chapter_start": 全书章节编号, "chapter_end": 全书章节编号,
          "tension": "平静|上升|高潮|下落|反转"}}
      ]
    }}
  ]
}}
"""
        data = request_json(
            system=SYSTEM, user=prompt,
            list_candidates=["volume_lines", "lines", "items", "narrative_lines"],
            min_items=1, max_retries=4, temperature=0.7,
            agent_name=f"LinePlanner[V{vi}]",
            empty_ok=True,
        )
        lines_data = pick_list(data, "volume_lines", "lines", "items", "narrative_lines") if data else []
        if not lines_data:
            return (vi, [])
        new_lines = _parse_lines(lines_data, LineScope.VOLUME)
        for ln in new_lines:
            ln.volume_range = (vi, vi)
        return (vi, new_lines)

    results = parallel_map(
        fn=_one_volume,
        items=vol_indexes,
        max_workers=PARALLEL_WORKERS,
        label="VolumeLines",
    )

    # 按卷号顺序 append，避免 state.volume_lines 里的线打乱顺序
    results = [r for r in results if r]
    results.sort(key=lambda x: x[0])
    for vi, new_lines in results:
        state.volume_lines.extend(new_lines)
        print(f"  ✓ 第{vi}卷专属线：{len(new_lines)} 条")
        for ln in new_lines:
            _print_line_summary(ln)


# ─────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────

def _parse_lines(lines_data: list[dict], scope: LineScope) -> list[NarrativeLine]:
    """把 LLM 返回的 raw dict 列表解析成 NarrativeLine。
    所有字段读取都用 .get()——LLM 漏字段时跳过该 line/phase 而不是崩。
    """
    tension_map = {t.value: t for t in TensionLevel}
    type_map = {t.value: t for t in LineType}
    result = []
    for idx, ld in enumerate(lines_data):
        if not isinstance(ld, dict):
            continue
        phases = []
        for pi, p in enumerate(ld.get("phases", []) or []):
            if not isinstance(p, dict):
                continue
            try:
                phases.append(LinePhase(
                    phase_index=int(p.get("phase_index", pi + 1)),
                    name=p.get("name", "") or f"阶段{pi+1}",
                    description=p.get("description", "") or "",
                    volume=int(p.get("volume", 1) or 1),
                    chapter_start=int(p.get("chapter_start", 0) or 0),
                    chapter_end=int(p.get("chapter_end", 0) or 0),
                    tension=tension_map.get(p.get("tension", ""), TensionLevel.RISING),
                ))
            except (ValueError, TypeError) as _e:
                # 单个 phase 有问题就跳，不要把整条线毁掉
                print(f"  ⚠ _parse_lines: phase {pi} 解析失败（跳过）：{type(_e).__name__}: {_e}")
        vr = ld.get("volume_range", [1, 1]) or [1, 1]
        if not isinstance(vr, (list, tuple)) or len(vr) < 2:
            vr = [1, 1]
        line_id = ld.get("line_id") or f"line_{idx+1:02d}"
        line_type_str = ld.get("line_type") or ld.get("type") or "故事线"
        try:
            line = NarrativeLine(
                line_id=line_id,
                line_type=type_map.get(line_type_str, LineType.STORY),
                scope=scope,
                name=ld.get("name", "") or line_id,
                description=ld.get("description", "") or "",
                characters=ld.get("characters", []) or [],
                volume_range=(int(vr[0]), int(vr[1])),
                phases=phases,
            )
            result.append(line)
        except Exception as _e:
            print(f"  ⚠ _parse_lines: line {line_id} 解析失败（跳过）：{type(_e).__name__}: {_e}")
    return result


def _print_line_summary(ln: NarrativeLine):
    phases_str = " → ".join(
        f"[{p.chapter_start}-{p.chapter_end}]{p.name}({p.tension.value})"
        for p in ln.phases
    )
    print(f"     [{ln.line_type.value}] {ln.name}")
    print(f"       {phases_str}")

