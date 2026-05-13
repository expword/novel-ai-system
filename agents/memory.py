"""
MemoryAgent — 专注于章节记忆提取：线索进度/角色状态/世界事实。
伏笔操作已由 ForeshadowManagerAgent 接管，此处不再处理。
"""
import json
from utils.json_utils import repair_json, safe_parse, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, MemoryEntry, ChapterSummary, TensionLevel, count_chapter_words
from agents.foreshadow_manager import update_after_chapter
from utils.context_manager import build_memory_context


SYSTEM = """你是小说记忆管理员，从章节正文中精确提取结构化信息。
记录需要准确、精炼，是后续写作的核心依据。
【单主角视角】摘要和事件提取以主角为中心——本章对主角而言发生了什么？主角的处境/认知/情感/关系发生了什么改变？
- summary 必须回答"本章对主角意味着什么"
- key_events 优先记录直接影响主角的事件；配角独立事件只在"会对主角后续产生影响"时才记录。
- 若本章主角缺席/戏份很少，在 summary 里显式说明"主角在本章的角色/缺席原因"。
输出严格JSON。"""


def process_chapter(state: NovelState, chapter_index: int, content: str) -> ChapterSummary:
    """提取章节记忆，更新角色状态，推进叙事线阶段，返回 ChapterSummary。"""

    vol = state.current_volume()
    volume_index = vol.index if vol else state.current_volume_index

    # ContextManager 提供精简的提取所需上下文
    ctx = build_memory_context(state, chapter_index, volume_index)

    # 正文截断：memory只需要全文来提取信息，但超长正文截到5000字
    content_input = content[:5000] if len(content) > 5000 else content

    planted_fws = [fw for fw in state.foreshadow_items if fw.planted_chapter == chapter_index]
    resolved_fws = [fw for fw in state.foreshadow_items if fw.planned_resolve_chapter == chapter_index]

    prompt = f"""请分析以下章节，提取结构化信息。

{ctx}

【章节正文】
{content_input}

输出JSON：
{{
  "title": "章节标题（从正文标题行提取）",
  "summary": "情节摘要（200字以内，含关键事件/情绪变化/线索推进）",
  "closing_hook": "本章最后留下的悬念/未解问题/情绪钩子（50字，供下章承接用）",
  "tension": "平静|上升|高潮|下落|反转",
  "key_events": ["关键事件1（30字）", "事件2"],
  "memory_entries": [
    {{
      "line_ids": ["相关line_id"],
      "event_type": "推进|转折|情感变化|成长|揭秘|死亡|登场",
      "content": "事件描述（80字）",
      "tension": "平静|上升|高潮|下落|反转",
      "tags": ["标签"]
    }}
  ],
  "new_facts": ["新确立的世界规则/重要设定（50字）"],
  "character_state_updates": {{
    "角色名": "当前状态简述（30字）"
  }},
  "lines_advanced": ["本章推进了的叙事线line_id"],
  "sp_triggered": ["本章触发的爽点sp_id（如有）"],
  "fw_planted_confirmed": ["确认已植入的伏笔fw_id"],
  "fw_resolved_confirmed": ["确认已兑现的伏笔fw_id"]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["summary"],
        max_retries=3, temperature=0.3,
        agent_name=f"Memory[Ch{chapter_index}]",
        empty_ok=True,
    )
    if not data:
        data = {}

    tension_map = {t.value: t for t in TensionLevel}
    chapter_tension = tension_map.get(data.get("tension", "上升"), TensionLevel.RISING)

    # 写入记忆条目
    for me in data.get("memory_entries", []):
        if not isinstance(me, dict):
            continue
        # 缺 content 的记忆条目无意义——跳过
        content = (me.get("content") or "").strip()
        if not content:
            continue
        # event_type 缺失给个默认值，不阻塞
        event_type = (me.get("event_type") or me.get("type") or "事件").strip() or "事件"
        line_ids = me.get("line_ids") or []
        if not isinstance(line_ids, list):
            line_ids = []
        tags = me.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        state.memory.add(MemoryEntry(
            chapter_index=chapter_index,
            volume_index=volume_index,
            line_ids=line_ids,
            event_type=event_type,
            content=content,
            tension=tension_map.get(me.get("tension", "上升"), TensionLevel.RISING),
            tags=tags,
        ))

    # 更新世界事实
    state.memory.facts.extend(data.get("new_facts", []))

    # 更新角色状态
    for name, status in data.get("character_state_updates", {}).items():
        state.memory.character_states[name] = f"[第{chapter_index}章] {status}"

    # 推进叙事线阶段
    for lid in data.get("lines_advanced", []):
        line = state.get_line(lid)
        if line:
            phase = line.get_phase_for_chapter(chapter_index)
            if phase and chapter_index >= phase.chapter_end:
                phase.completed = True
                line.advance_phase()

    # 更新爽点状态
    from agents.satisfaction_system import mark_sp_triggered
    for sp_id in data.get("sp_triggered", []):
        mark_sp_triggered(state, sp_id, chapter_index)

    # 更新伏笔状态
    update_after_chapter(
        state, chapter_index,
        planted_ids=data.get("fw_planted_confirmed", []),
        resolved_ids=data.get("fw_resolved_confirmed", []),
    )

    state.tension_history.append(chapter_tension)

    summary = ChapterSummary(
        index=chapter_index,
        volume_index=volume_index,
        title=data.get("title", f"第{chapter_index}章"),
        summary=data.get("summary", "") or "（LLM未返回摘要）",
        word_count=count_chapter_words(content),
        tension=chapter_tension,
        key_events=data.get("key_events", []),
        lines_advanced=data.get("lines_advanced", []),
        sp_triggered=data.get("sp_triggered", []),
        closing_hook=data.get("closing_hook", ""),
    )
    state.completed_chapters.append(summary)
    return summary


def format_writing_context(state: NovelState, active_line_ids: list[str], chapter_index: int) -> str:
    """为写作智能体组装完整上下文记忆。"""
    vol = state.current_volume()
    sections = []

    sections.append("【近期情节（最近3章）】")
    sections.append(state.last_n_summaries(3))

    sections.append("\n【各线最新进展】")
    for lid in active_line_ids:
        line = state.get_line(lid)
        if not line:
            continue
        entries = state.memory.get_by_line(lid, last_n=3)
        phase = line.get_phase_for_chapter(chapter_index)
        phase_str = f"当前阶段：{phase.name}（{phase.tension.value}）" if phase else "线索待激活"
        sections.append(f"  [{line.scope.value}]{line.name} — {phase_str}")
        for e in entries:
            sections.append(f"    [第{e.chapter_index}章/{e.event_type}] {e.content}")

    # 角色当前状态
    if vol:
        active = state.active_characters_in_volume(vol.index)
        char_lines = []
        for c in active[:6]:
            status = state.memory.character_states.get(c.name, "状态未知")
            char_lines.append(f"  {c.name}（{c.realm}）：{status}")
        if char_lines:
            sections.append("\n【角色当前状态】")
            sections.extend(char_lines)

    return "\n".join(sections)
