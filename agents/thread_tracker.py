"""
ThreadTrackerAgent — 每章写完后提取精确的故事状态，作为下章的接力棒。

核心理念：
  真实小说里，任何时刻都有多件事同时在发展：
  - 主线正面交锋（前景）
  - 其他角色有自己的行动（并行）
  - 幕后势力在布局（背景暗流）
  - 之前埋的线索在等待激活（潜伏）

  ThreadTracker 的任务是追踪所有这些层次，
  让下章的 ChapterPlanner 知道该把哪些线索交叉编织进来。
"""
from json_utils import repair_json, request_json
from llm import system_user
from state import NovelState, StoryThread, OpenLoop


SYSTEM = """你是小说连续性分析师，专门追踪多线并行的故事状态。
你的任务：从章节正文提取精确的"此刻多线状态"——不是概括，是实时快照。
【单主角视角】快照以主角为中心：
- protagonist_* 字段是快照的核心，必须精确反映主角此刻的处境/目标/阻碍/情绪。
- parallel_events（配角并行事件）只记录"最终会影响主角"的事件，不记录纯背景。
- background_developments（暗中发展）应当是"主角未察觉但终将波及他"的事，不是与主角无关的彩蛋。
输出严格JSON。"""


def update_story_thread(
    state: NovelState,
    chapter_index: int,
    content: str,
) -> None:
    """
    从章节正文提取实时故事状态，更新 state.story_thread。
    在 memory.process_chapter 之后调用。
    """
    thread = state.story_thread
    content_tail = content[-1500:]
    content_head = content[:800]

    open_loops_desc = _format_open_loops(thread)
    known_chars = [c.name for c in state.characters]

    prompt = f"""分析第{chapter_index}章，提取多线并行的故事状态快照。

【当前已知开放循环（悬而未决的情况）】
{open_loops_desc}

【已知正式角色】
{', '.join(known_chars)}

【章节开头（800字）】
{content_head}

【章节结尾（1500字）】
{content_tail}

提取以下内容：

1. scene_end_state：章节最后的精确画面（100字，具体到"谁在哪里，处于什么状态"）
2. protagonist_immediate_goal：主角此刻最紧迫的具体目标（30字，场景级）
3. protagonist_immediate_obstacle：具体阻碍（30字）
4. protagonist_emotional_state：主角情绪（具体，含原因）
5. current_location：主角当前地点
6. current_time_context：时间背景

7. parallel_events：其他重要角色此刻正在做什么（与主角无关的并行发展）
   每条格式："【角色名】正在做什么/处于什么状态"（30字内）
   最多4条，只写有意义的发展，不要凑数

8. background_developments：主角尚不知晓的暗中发展（势力/反派/阴谋）
   每条格式："【谁/什么势力】在暗中做什么"（30字内）
   最多3条，前期可以为空

9. next_chapter_opening：下章应该怎么开始（具体到场景、时间、切入点，50字）

10. open_loops_update：更新开放循环
    - 本章新开启的情况：新增（urgency=紧急/持续/潜伏）
    - 有进展的情况：更新current_progress
    - 已解决的情况：closed=true
    注意：不要遗漏已有循环，要逐一检查更新

11. emergent_characters：本章新出现的角色（不在已知正式角色列表中的）
    每条：name/brief_role/first_impression/potential_future_role
    可为空列表

12. active_tensions：当前活跃的人际/势力紧张关系（20字内，最多4条）

输出JSON：
{{
  "scene_end_state": "...",
  "protagonist_immediate_goal": "...",
  "protagonist_immediate_obstacle": "...",
  "protagonist_emotional_state": "...",
  "current_location": "...",
  "current_time_context": "...",
  "parallel_events": ["【角色名】...", "..."],
  "background_developments": ["【势力/角色】...", "..."],
  "next_chapter_opening": "...",
  "active_tensions": ["...", "..."],
  "emergent_characters": [
    {{
      "name": "角色名",
      "brief_role": "简短角色定位（20字）",
      "first_impression": "读者对他的第一印象（20字）",
      "potential_future_role": "可能在后续发挥的作用（30字）"
    }}
  ],
  "open_loops_update": [
    {{
      "loop_id": "已有ID或新ID如loop_N",
      "description": "情况描述（50字）",
      "urgency": "紧急|持续|潜伏",
      "target_close_chapter": 预计解决章节或-1,
      "current_progress": "当前进展（30字）",
      "closed": false
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["scene_end_state"],
        max_retries=3, temperature=0.3,
        agent_name=f"ThreadTracker[Ch{chapter_index}]",
        empty_ok=True,
    )
    if not data:
        print(f"    ⚠ 第{chapter_index}章 ThreadTracker 重试失败——保留上一章故事状态")
        return

    thread.scene_end_state = data.get("scene_end_state", "")
    thread.protagonist_immediate_goal = data.get("protagonist_immediate_goal", "")
    thread.protagonist_immediate_obstacle = data.get("protagonist_immediate_obstacle", "")
    thread.protagonist_emotional_state = data.get("protagonist_emotional_state", "")
    thread.current_location = data.get("current_location", "")
    thread.current_time_context = data.get("current_time_context", "")
    thread.parallel_events = data.get("parallel_events", [])
    thread.background_developments = data.get("background_developments", [])
    thread.next_chapter_opening = data.get("next_chapter_opening", "")
    thread.active_tensions = data.get("active_tensions", [])

    _update_open_loops(thread, data.get("open_loops_update", []), chapter_index)
    _register_emergent_characters(state, data.get("emergent_characters", []), chapter_index)

    print(f"    线索快照：开放循环×{len([l for l in thread.open_loops if not l.closed])} "
          f"| 并行事件×{len(thread.parallel_events)} "
          f"| 新角色×{len(data.get('emergent_characters', []))}")


def format_thread_for_writer(thread: StoryThread, chapter_index: int) -> str:
    """
    将 StoryThread 格式化为写作/规划智能体的 CRITICAL 上下文。
    包含多线并行状态，让写作时自然编织多条线索。
    """
    if not thread.scene_end_state:
        return "故事开篇，一切从零开始。"

    lines = [
        "【承接上章——此刻精确状态】",
        f"画面：{thread.scene_end_state}",
    ]
    if thread.current_location:
        lines.append(f"地点：{thread.current_location}　时间：{thread.current_time_context}")
    if thread.protagonist_emotional_state:
        lines.append(f"主角情绪：{thread.protagonist_emotional_state}")

    lines += ["", "【主角当前处境】"]
    if thread.protagonist_immediate_goal:
        lines.append(f"目标：{thread.protagonist_immediate_goal}")
    if thread.protagonist_immediate_obstacle:
        lines.append(f"阻碍：{thread.protagonist_immediate_obstacle}")

    # 开放循环（未解情况，写作时不能无视）
    active_loops = [l for l in thread.open_loops if not l.closed]
    urgent = [l for l in active_loops if l.urgency == "紧急"]
    ongoing = [l for l in active_loops if l.urgency == "持续"]
    latent = [l for l in active_loops if l.urgency == "潜伏"]

    if urgent or ongoing:
        lines += ["", "【悬而未决的情况（必须推进）】"]
        for l in urgent[:3]:
            lines.append(f"⚠ [紧急] {l.description}　进展：{l.current_progress}")
        for l in ongoing[:3]:
            lines.append(f"· [持续] {l.description}　进展：{l.current_progress}")
    if latent:
        lines += ["", "【潜伏中的线索（可适时激活）】"]
        for l in latent[:2]:
            lines.append(f"  [潜伏] {l.description}")

    # 并行事件（其他角色同时在做什么）
    if thread.parallel_events:
        lines += ["", "【同时发生的并行事件（可以交叉编织）】"]
        for e in thread.parallel_events[:4]:
            lines.append(f"  {e}")

    # 背景暗流
    if thread.background_developments:
        lines += ["", "【幕后暗中发展（主角暂未察觉）】"]
        for b in thread.background_developments[:3]:
            lines.append(f"  {b}")

    if thread.active_tensions:
        lines += ["", "【当前活跃张力】"]
        for t in thread.active_tensions[:3]:
            lines.append(f"  · {t}")

    # 待融入的新角色
    emergent = [c for c in getattr(thread, '_emergent_pending', [])]
    if emergent:
        lines += ["", "【待融入的新角色】"]
        for c in emergent[:2]:
            lines.append(f"  {c['name']}：{c.get('potential_future_role', '')}（{c.get('first_impression', '')}）")

    if thread.next_chapter_opening:
        lines += ["", f"【建议开篇切入】{thread.next_chapter_opening}"]

    return "\n".join(lines)


# ── 内部辅助 ──────────────────────────────────────────

def _format_open_loops(thread: StoryThread) -> str:
    active = [l for l in thread.open_loops if not l.closed]
    if not active:
        return "暂无已知开放循环（故事刚开始）"
    return "\n".join(
        f"- [{l.loop_id}/{l.urgency}] {l.description}（进展：{l.current_progress}）"
        for l in active[:10]
    )


def _update_open_loops(thread: StoryThread, updates: list[dict], chapter_index: int) -> None:
    existing = {l.loop_id: l for l in thread.open_loops}
    for u in updates:
        loop_id = u.get("loop_id", "")
        if not loop_id:
            continue
        if loop_id in existing:
            loop = existing[loop_id]
            loop.current_progress = u.get("current_progress", loop.current_progress)
            loop.closed = u.get("closed", loop.closed)
            if "target_close_chapter" in u and u["target_close_chapter"] != -1:
                loop.target_close_chapter = u["target_close_chapter"]
            if "urgency" in u:
                loop.urgency = u["urgency"]
        else:
            thread.open_loops.append(OpenLoop(
                loop_id=loop_id,
                description=u.get("description", ""),
                urgency=u.get("urgency", "持续"),
                opened_chapter=chapter_index,
                target_close_chapter=u.get("target_close_chapter", -1),
                current_progress=u.get("current_progress", "刚开启"),
                closed=u.get("closed", False),
            ))
    # 清理已关闭超过15章的循环
    thread.open_loops = [
        l for l in thread.open_loops
        if not l.closed or (chapter_index - l.opened_chapter < 15)
    ]


def _register_emergent_characters(state: NovelState, emergent: list[dict], chapter_index: int) -> None:
    """将新出现的角色记录到 state，以便后续章节计划融入。"""
    if not emergent:
        return
    known_names = {c.name for c in state.characters}
    for ec in emergent:
        name = ec.get("name", "")
        if not name or name in known_names:
            continue
        # 写入 memory facts，让后续记忆系统可以查到
        state.memory.facts.append(
            f"[新出现角色/{chapter_index}章] {name}：{ec.get('brief_role', '')}。"
            f"第一印象：{ec.get('first_impression', '')}。"
            f"潜在作用：{ec.get('potential_future_role', '')}"
        )
        # 标记在 story_thread 上（临时挂载，供下几章的 ChapterPlanner 知道）
        if not hasattr(state.story_thread, '_emergent_pending'):
            state.story_thread._emergent_pending = []
        # 去重
        existing_names = [e['name'] for e in state.story_thread._emergent_pending]
        if name not in existing_names:
            state.story_thread._emergent_pending.append({
                **ec, "first_appeared": chapter_index
            })
    # 超过10章未被正式加入的，清除掉
    if hasattr(state.story_thread, '_emergent_pending'):
        state.story_thread._emergent_pending = [
            e for e in state.story_thread._emergent_pending
            if chapter_index - e.get("first_appeared", chapter_index) < 10
        ]
