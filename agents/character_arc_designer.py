"""
CharacterArcDesignerAgent — Phase 2-A4：人物弧光。

每个主要角色（主角 + 主要配角 + 反派）都有一条心理成长弧——
起点的性格缺陷 → 通过关键事件触发转变 → 终点的蜕变状态。

与 LinePlanner 正交：LinePlanner 管"发生了什么事"（剧情线），
CharacterArc 管"这些事让他内心怎么变了"（人物心理线）。

每人一次 LLM 调用（5-9 人），生成后写入 state.character_arcs。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, CharacterArc, ArcTransition, CharacterRole
from utils.concurrency import parallel_map
from config import PARALLEL_WORKERS


SYSTEM = """你是人物心理弧光设计师。
你的工作是给一个角色画一条"内心成长曲线"——不是剧情发生了什么，而是他的内心因此怎么变了。

好的人物弧要有：
- 清晰的起点：性格缺陷 + 认知局限（他看世界的方式哪里错了？）
- 清晰的终点：他蜕变成什么样了（具体可见，不是"变强了"这种空话）
- 3-5 个转折点：什么事件让他从 A 跨到 B 的中间状态；每个转折都要有"触发事件 → 内心震动 → 新状态"
- 转折必须有痛感：人不会轻易改变，真正的成长都伴随代价

注意：这条弧**独立于**剧情线——即使某卷什么都没发生，他的内心也应该在积累下次转折所需的压力。

输出严格 JSON。"""


def design_character_arcs(state: NovelState) -> None:
    """为主角、主要配角、反派各设计一条人物弧光。"""
    from agents import require_upstream
    if not require_upstream(state, "CharacterArcDesigner",
        characters=lambda s: bool(s.characters),
        volumes=lambda s: bool(s.volumes),
    ):
        return
    targets = [
        c for c in state.characters
        if c.role in (CharacterRole.PROTAGONIST, CharacterRole.MAJOR, CharacterRole.ANTAGONIST)
    ]
    if not targets:
        print("  ⚠ 没有需要做心理弧的角色")
        return

    existing_arc_names = {a.character_name for a in state.character_arcs}
    volumes_brief = "\n".join(
        f"第{v.index}卷《{v.title}》[第{v.chapter_start}-{v.chapter_end}章]：{v.theme}"
        for v in state.volumes
    )

    pending = [c for c in targets if c.name not in existing_arc_names]
    skipped = len(targets) - len(pending)
    if skipped:
        print(f"  跳过已有弧线的 {skipped} 人")
    if not pending:
        return

    print(f"  为 {len(pending)} 个核心角色并发设计心理弧（每人一次 LLM）...")

    # 并发跑——每个 worker 只读 state，不写
    arcs = parallel_map(
        fn=lambda c: _design_one_arc(state, c, volumes_brief),
        items=pending,
        max_workers=PARALLEL_WORKERS,
        label="CharacterArc",
    )

    # 主线程串行 append 到 state.character_arcs（列表 append 在 CPython 有 GIL 保护，但显式主线程写更稳）
    for arc in arcs:
        if not arc:
            continue
        state.character_arcs.append(arc)
        print(f"  ✓ {arc.character_name} · {arc.theme}")
        print(f"      起：{arc.start_state[:45]}")
        print(f"      ↓ {len(arc.transitions)} 个转折点")
        print(f"      终：{arc.end_state[:45]}")


def _design_one_arc(state: NovelState, char, volumes_brief: str) -> CharacterArc:
    char_sheet = (
        f"姓名：{char.name}（{char.role.value}）\n"
        f"性格：{char.personality_detail[:100]}\n"
        f"背景：{char.background[:80]}\n"
        f"创伤：{char.trauma[:60]}\n"
        f"渴望：{char.desire[:40]}\n"
        f"恐惧：{char.fear[:40]}\n"
        f"致命弱点：{char.fatal_flaw}\n"
        f"整体成长轨迹（基础描述）：{char.arc[:100]}"
    )

    prompt = f"""为以下角色设计心理成长弧。

【角色档案】
{char_sheet}

【全书卷结构（把转折点落在合适的卷/章）】
{volumes_brief}

═══ 要求 ═══
1. theme：这条弧线的主题（20字，如"从懦弱到担当"、"从天才傲气到谦卑"、"从复仇执念到和解"）
2. start_state：起点状态（50字）——性格缺陷 + 认知局限。要具体，不要"性格不好"这种抽象词
3. end_state：终点状态（50字）——蜕变后的样子。要和起点形成对照
4. transitions：3-5 个关键转折点。每个：
   - volume / chapter_approx：大致在哪一卷哪一章（触发事件的时间锚点）
   - trigger_event：触发事件（50字，具体，要是一次冲击性经历）
   - state_before：转折前内心（30字）
   - state_after：转折后内心（30字）
   - inner_change：内心到底发生了什么（40字，不是外在事件，是认知/情感层面的位移）
5. 转折要有痛感——人不会无痛成长，每次转折应当伴随失去/觉醒/抉择/代价

输出 JSON：
{{
  "character_name": "{char.name}",
  "theme": "...",
  "start_state": "...",
  "end_state": "...",
  "transitions": [
    {{
      "volume": 卷号,
      "chapter_approx": 大致章节,
      "trigger_event": "...",
      "state_before": "...",
      "state_after": "...",
      "inner_change": "..."
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["theme", "start_state", "end_state"],
        list_candidates=["transitions"],
        min_items=2,
        max_retries=4, temperature=0.75,
        agent_name=f"CharacterArc[{char.name}]",
        empty_ok=True,
    )
    if not data:
        return None

    return CharacterArc(
        character_name=char.name,
        theme=data.get("theme", ""),
        start_state=data.get("start_state", ""),
        end_state=data.get("end_state", ""),
        transitions=[ArcTransition(
            volume=int(t.get("volume", 1)),
            chapter_approx=int(t.get("chapter_approx", -1)),
            trigger_event=t.get("trigger_event", ""),
            state_before=t.get("state_before", ""),
            state_after=t.get("state_after", ""),
            inner_change=t.get("inner_change", ""),
        ) for t in pick_list(data, "transitions", "items")],
    )
