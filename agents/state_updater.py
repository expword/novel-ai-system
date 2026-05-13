"""
StateUpdaterAgent — Phase 5：状态集中回写。

memory.process_chapter 之后跑，统一做五件事：
1. 人物状态快照更新（location/injury/emotion/items/realm）——写入 character_state_history
2. 关系矩阵更新（本章因事件产生的关系变化）——追加到 CharacterBond.volume_evolution
3. 伏笔追踪器更新：
   · 激活：本章让某个已埋伏笔开始被读者注意到（激活≠兑现）
   · 兑现：本章真的把某个伏笔的真相揭晓了（置 fw.resolved=True, fw.actual_resolve_chapter）
4. 世界事件日历更新（本章发生的重大事件）——写入 world_events
5. 反转揭露追踪：按 directive.twist_reveals 校验本章是否真的写了反转层揭露（LLM 判断）

让 Writer 下一章开始时能从 state 精确读取"此刻的硬事实"，不靠猜。
"""
from json_utils import request_json, pick_list
from state import NovelState, CharacterStateSnapshot, WorldEvent, CharacterRole


SYSTEM = """你是小说状态管理员。你不评价文学质量，你只干一件事：
读完本章正文，精确提取"本章结束时"的状态变化，以便下一章有依据可查。
- 角色状态：每个核心角色此刻在哪、受伤了吗、情绪怎样、手上有什么关键物品、级别/身份
  （level 字段叫 realm 是历史命名——按本书题材填合适的内容：境界/职位/异能等级/学历/官阶/家世……如本书无层级体系，留空即可）
- 关系变化：本章哪两个人的关系发生了可感知的改变
- 伏笔激活：本章是否让某个已埋伏笔开始被读者注意到？（不是回收，是"激活"）
- 世界事件：本章是否发生了影响势力/区域/行业/学校/避难所等的重大事件
输出严格 JSON。不要编造未写出的事情。"""


def update_state_after_chapter(
    state: NovelState,
    chapter_index: int,
    volume_index: int,
    content: str,
    directive=None,
) -> None:
    """统一回写：快照/关系/伏笔激活+兑现/世界事件/反转执行确认。"""
    # 准备已知角色和已有伏笔列表
    active_chars = [
        c for c in state.active_characters_in_volume(volume_index)
        if c.role in (CharacterRole.PROTAGONIST, CharacterRole.MAJOR, CharacterRole.ANTAGONIST)
    ]
    char_names = ", ".join(c.name for c in active_chars[:8])
    # 已植入但未激活、未兑现的伏笔
    pending_fws = [
        fw for fw in state.foreshadow_items
        if fw.planted_chapter > 0 and not fw.resolved and fw.activation_chapter == -1
    ][:8]
    fw_desc = "\n".join(f"  - [{fw.fw_id}] {fw.content[:40]}" for fw in pending_fws) or "（无待激活伏笔）"

    # directive 里指定本章应兑现的伏笔 + 本章应揭露的反转层
    to_resolve_fws = []
    to_reveal_twists = []
    if directive:
        for fw_id in getattr(directive, "foreshadow_resolve", []) or []:
            fw = state.get_foreshadow(fw_id)
            if fw and not fw.resolved:
                to_resolve_fws.append(fw)
        for token in getattr(directive, "twist_reveals", []) or []:
            try:
                cid, ln = token.split(":")
                hit = state.find_twist_layer(cid, int(ln))
                if hit:
                    to_reveal_twists.append(hit)
            except (ValueError, AttributeError):
                pass
    resolve_desc = "\n".join(
        f"  - [{fw.fw_id}] 需兑现：{fw.resolution_description[:50]}"
        for fw in to_resolve_fws
    ) or "（无）"
    twist_check_desc = "\n".join(
        f"  - [{c.chain_id}:L{l.layer}] 需揭露：{l.reveal[:50]}"
        for c, l in to_reveal_twists
    ) or "（无）"

    # 正文——为节省 token，取开头+结尾
    content_sample = content[:2000] + ("\n[...]\n" + content[-2000:] if len(content) > 4000 else "")

    prompt = f"""分析第 {chapter_index} 章的"状态变化"——只提取事实，不评价。

【核心角色候选】
{char_names}

【已埋未激活的伏笔】
{fw_desc}

【本章 directive 指定要兑现的伏笔】
{resolve_desc}

【本章 directive 指定要揭露的反转层】
{twist_check_desc}

【章节正文节选】
{content_sample}

═══ 提取要求 ═══
1. character_snapshots：每个在本章有戏份的核心角色，给出章末快照
2. relationship_changes：本章发生的关系变化（20字/条，格式"X↔Y：XX"）
3. foreshadow_activations：本章是否让哪个待激活伏笔被读者注意到（激活不等于兑现）
4. foreshadow_resolutions：directive 里要求兑现的伏笔，本章真的兑现了吗？只报 yes 的
5. twist_executions：directive 里要求揭露的反转层，本章真的写到揭露了吗？只报 yes 的
6. world_events：本章是否发生重大世界事件（势力、区域层面）

输出 JSON：
{{
  "character_snapshots": [
    {{
      "name": "角色名",
      "location": "...",
      "injury": "伤势（无则空）",
      "emotion": "情绪",
      "items_on_hand": ["物品1"],
      "realm": "级别/身份（按题材：境界/职位/异能等级/学历/官阶/家世……本书无此概念则留空）",
      "relationship_changes": ["与X：...（可选）"]
    }}
  ],
  "foreshadow_activations": [
    {{"fw_id": "...", "activation_sign": "本章让读者注意到它的具体描写（30字）"}}
  ],
  "foreshadow_resolutions": [
    {{"fw_id": "...", "resolved_in_text": "本章真正兑现它的句子/情节（30字）"}}
  ],
  "twist_executions": [
    {{"chain_id": "...", "layer": 1, "reveal_moment": "本章真正揭露它的描写（30字）"}}
  ],
  "world_events": [
    {{"event_desc": "事件描述（50字）",
      "affected_factions": ["势力名"],
      "affected_regions": ["区域名"],
      "importance": "普通|重大|里程碑"}}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        max_retries=2, temperature=0.3,
        agent_name=f"StateUpdater[Ch{chapter_index}]",
        empty_ok=True,
    )
    if not data:
        return

    # 1. 写入角色状态快照
    for snap in data.get("character_snapshots", []):
        name = snap.get("name", "")
        if not name:
            continue
        snapshot = CharacterStateSnapshot(
            chapter_index=chapter_index,
            location=snap.get("location", ""),
            injury=snap.get("injury", ""),
            emotion=snap.get("emotion", ""),
            items_on_hand=snap.get("items_on_hand", []),
            realm=snap.get("realm", ""),
            relationship_changes=snap.get("relationship_changes", []),
        )
        state.character_state_history.setdefault(name, []).append(snapshot)
        # 同步旧接口：更新 memory.character_states 保持向后兼容
        summary_parts = []
        if snapshot.location:
            summary_parts.append(f"在{snapshot.location}")
        if snapshot.injury:
            summary_parts.append(f"伤:{snapshot.injury}")
        if snapshot.emotion:
            summary_parts.append(snapshot.emotion)
        if summary_parts:
            state.memory.character_states[name] = f"[第{chapter_index}章] " + "，".join(summary_parts)

    # 2. 关系变化：追加到 CharacterBond.projected_changes / volume_evolution
    for snap in data.get("character_snapshots", []):
        name = snap.get("name", "")
        for change in snap.get("relationship_changes", []):
            # 尝试 parse "X↔Y：..." 格式找到对应 bond
            if "↔" not in change and ":" not in change and "：" not in change:
                continue
            for bond in state.relationship_web.bonds:
                if name in (bond.char_a, bond.char_b):
                    other = bond.char_b if bond.char_a == name else bond.char_a
                    if other in change:
                        # 追加到本卷演变
                        existing = bond.volume_evolution.get(volume_index, "")
                        if change[:60] not in existing:
                            bond.volume_evolution[volume_index] = (existing + " | " + change[:60]).strip(" |")
                        break

    # 3a. 伏笔激活
    activation_count = 0
    for act in data.get("foreshadow_activations", []):
        fw_id = act.get("fw_id", "")
        if not fw_id:
            continue
        fw = state.get_foreshadow(fw_id)
        if fw and fw.activation_chapter == -1:
            fw.activation_chapter = chapter_index
            fw.activation_sign = act.get("activation_sign", "")
            activation_count += 1

    # 3b. 伏笔兑现——LLM 确认本章真的兑现的，标 resolved=True
    resolution_count = 0
    resolved_ids: set[str] = set()
    for res in data.get("foreshadow_resolutions", []):
        fw_id = res.get("fw_id", "")
        if not fw_id:
            continue
        fw = state.get_foreshadow(fw_id)
        if fw and not fw.resolved:
            fw.resolved = True
            fw.actual_resolve_chapter = chapter_index
            fw.resolution_quality = "兑现"
            resolution_count += 1
            resolved_ids.add(fw_id)
    # 安全兜底：directive 里要求兑现但 LLM 没确认的，打警告（不自动标 resolved）
    unfulfilled = []
    if directive:
        for fw_id in getattr(directive, "foreshadow_resolve", []) or []:
            if fw_id not in resolved_ids:
                fw = state.get_foreshadow(fw_id)
                if fw and not fw.resolved:
                    unfulfilled.append(fw_id)

    # 3c. 反转执行——LLM 确认本章真的写了反转揭露
    twist_executed = 0
    twist_missed: list[str] = []
    executed_tokens: set[str] = set()
    for ex in data.get("twist_executions", []):
        cid = ex.get("chain_id", "")
        layer_num = ex.get("layer", 0)
        if not cid or not layer_num:
            continue
        executed_tokens.add(f"{cid}:{layer_num}")
        twist_executed += 1
    if directive:
        for token in getattr(directive, "twist_reveals", []) or []:
            if token not in executed_tokens:
                twist_missed.append(token)

    # 4. 世界事件日历
    event_count = 0
    for ev in data.get("world_events", []):
        desc = ev.get("event_desc", "")
        if not desc:
            continue
        state.world_events.append(WorldEvent(
            chapter_index=chapter_index,
            event_desc=desc,
            affected_factions=ev.get("affected_factions", []),
            affected_regions=ev.get("affected_regions", []),
            importance=ev.get("importance", "普通"),
        ))
        event_count += 1

    # 简要报告
    snap_count = len(data.get("character_snapshots", []))
    if snap_count or activation_count or resolution_count or twist_executed or event_count:
        print(
            f"  ✓ 状态回写：快照×{snap_count}"
            f"｜伏笔激活×{activation_count}｜伏笔兑现×{resolution_count}"
            f"｜反转揭露×{twist_executed}｜世界事件×{event_count}"
        )
    if unfulfilled:
        print(f"  ⚠ 以下伏笔本章未真正兑现（directive 要求但文本中未落地）：{unfulfilled}")
    if twist_missed:
        print(f"  ⚠ 以下反转层本章未真正揭露：{twist_missed}")
