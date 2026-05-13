"""
ForeshadowManagerAgent — 专项管理伏笔的规划/追踪/植入/兑现。
与 memory.py 分离，专注于伏笔的全生命周期。
"""
import json
from json_utils import repair_json, safe_parse, pick_list, request_json
from agents.concept_pitch import format_concept_brief
from llm import system_user
from state import NovelState, ForeshadowItem, ForeshadowImportance, RedHerring
from config import NUM_VOLUMES


SYSTEM = """你是伏笔系统架构师，负责给长篇小说铺设"等待收割"的网络。

伏笔最终都要落到主角身上——兑现时要冲击主角的处境/认知/情感。哪怕埋在配角或反派身上，兑现时主角得承受后果或得到启示。不要设计纯装饰不触碰主角的伏笔。

伏笔设计要有跨度梯度——像小说自然的呼吸节奏：
  · 近端伏笔（1-3章跨度）：本章埋下一个细节，下一两章就兑现，用来托住短程悬念、制造"咦？"的读者互动
  · 中段伏笔（几章到十几章跨度）：在某个情节段早期埋下，等到该情节段尾部兑现，让整段情节有回环
  · 远程伏笔（几十章甚至跨卷）：全书级别的大谜团，前期不动声色埋一笔，后期兑现时让读者倒吸一口气——"原来第 3 章那个眼神就是答案"
一部好小说这三种伏笔都要有，比例你根据故事自己权衡。但"远程伏笔"必须有，它们是全书史诗感的来源。

其他要记住的：
- 伏笔=读者看到 A，作者知道 A 背后是 B；兑现时要有"原来如此"的落点
- 同一章不要埋超过 2 个伏笔（读者消化不了）
- 大伏笔兑现前要有小伏笔铺路，让"原来如此"是渐进的而不是突兀的
- 兑现时机配合其所在卷的起承转合角色：转段宜兑现认知颠覆型伏笔，合段宜兑现情感羁绊型伏笔
- 主线伏笔的兑现要契合主角的重大节点（按题材而异：境界突破/关键抉择/职场翻盘/异能觉醒/最黑暗时刻……）

输出严格 JSON。"""


def plan_all_foreshadowing(state: NovelState) -> None:
    """规划全书伏笔体系——按重要性分三批生成（主线/支线/细节），每批一次 LLM 调用。

    主线批次会优先为 3E3 反转链的每个 clues_planted 生成对应伏笔——这是
    反转章不"作弊"的保证：每层 reveal 都有前期具体伏笔可引。
    """
    from agents import require_upstream
    if not require_upstream(state, "ForeshadowManager",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    ):
        return
    from config import (
        MAJOR_FORESHADOWS_MIN, MAJOR_FORESHADOWS_MAX,
        MINOR_FORESHADOWS_MIN, MINOR_FORESHADOWS_MAX,
        DETAIL_FORESHADOWS_MIN, DETAIL_FORESHADOWS_MAX,
    )
    # 收集反转链需要铺的 clues——主线批次必须给它们各生成一个具体伏笔
    twist_clues = _collect_twist_clues(state)
    _plan_foreshadow_batch(state, "主线伏笔",
                           count_range=(MAJOR_FORESHADOWS_MIN, MAJOR_FORESHADOWS_MAX),
                           desc="关乎主线走向，多数是远程伏笔（跨 30+ 章甚至多卷），兑现时要震撼",
                           required_clues=twist_clues)
    _plan_foreshadow_batch(state, "支线伏笔",
                           count_range=(MINOR_FORESHADOWS_MIN, MINOR_FORESHADOWS_MAX),
                           desc="支线情节/角色背景揭露，多数是中段跨度（10-25 章）")
    _plan_foreshadow_batch(state, "细节伏笔",
                           count_range=(DETAIL_FORESHADOWS_MIN, DETAIL_FORESHADOWS_MAX),
                           desc="世界细节/彩蛋/短程互动，跨度可短可长（1-10 章为主）")


def _collect_twist_clues(state: NovelState) -> list[dict]:
    """从 twist_system 收集每个反转层的 clues_planted——返回 [{chain_id, layer, clue, anchor}...]"""
    out = []
    ts = getattr(state, "twist_system", None)
    if not ts:
        return out
    for chain in (ts.chains or []):
        for layer in (chain.layers or []):
            anchor = (layer.reveal_anchor or "").strip()
            for clue in (layer.clues_planted or []):
                clue = (clue or "").strip()
                if clue:
                    out.append({
                        "chain_id": chain.chain_id,
                        "layer": layer.layer,
                        "clue": clue,
                        "reveal_anchor": anchor,
                    })
    return out

    if not state.foreshadow_items:
        print(f"  ⚠ ForeshadowManager 三批全部失败——本书将无预埋伏笔")
        return
    _print_fw_summary(state)


def _plan_foreshadow_batch(state: NovelState, importance_label: str,
                            count_range: tuple, desc: str,
                            required_clues: list[dict] = None) -> None:
    """为单一重要性档次生成一批伏笔——一次 LLM 调用只聚焦这一档。

    required_clues：反转链强制要求铺的 clues（仅主线批次会传入），LLM 必须为
    每条 clue 单独生成一个伏笔条目，再补足至 count_range。
    """
    low, high = count_range
    required_clues = required_clues or []
    chars_desc = "\n".join(
        f"- {c.name}（{c.role.value}）背景：{c.background[:50]}，创伤：{c.trauma[:30]}"
        for c in state.characters[:12]
    )
    volumes_desc = "\n".join(
        f"第{v.index}卷 [第{v.chapter_start}-{v.chapter_end}章]：关键事件：{', '.join(v.key_events[:2])}"
        for v in state.volumes
    )
    world_secrets = [f for f in state.memory.facts if "世界秘密" in f]
    # 已有伏笔（供参考，避免重复）
    existing = "\n".join(
        f"- [{fw.importance.value}] {fw.content[:40]}"
        for fw in state.foreshadow_items[-8:]
    ) or "（尚无已规划伏笔）"
    # 本档次的跨度引导
    if "主线" in importance_label:
        span_hint = "跨度以远程为主（30+ 章、甚至跨卷）"
    elif "支线" in importance_label:
        span_hint = "跨度以中段为主（10-25 章）"
    else:
        span_hint = "跨度可短可长（1-10 章为主，可以有少量彩蛋跨全书）"

    # 全书章节范围
    total_chapters = sum(v.total_chapters for v in state.volumes)

    concept_block = format_concept_brief(state)

    twist_block = ""
    if required_clues:
        clue_lines = "\n".join(
            f"  · [反转链 {c['chain_id']} L{c['layer']}] {c['clue'][:80]}"
            f"（揭露于：{c['reveal_anchor'][:30] or '待对齐'}）"
            for c in required_clues[:20]
        )
        twist_block = f"""
═══ 反转链需要的 clues（必须为它们各生成一个具体伏笔——不能少）═══
{clue_lines}

要求：
- 上面 {len(required_clues[:20])} 条 clue 必须**每条对应一个伏笔条目**（hidden_meaning 写明它属于哪条反转链）
- 这些 clue 对应的伏笔的 planned_resolve_chapter 必须早于或等于反转层的揭露锚点章
- 余下名额（共 {high} 个上限）再生成与反转无关的独立主线伏笔
"""
    prompt = f"""
为《{state.title}》规划【{importance_label}】——{low}-{high} 个。

{concept_block}

本批定义：{desc}
跨度要求：{span_hint}

全书共 {total_chapters} 章，卷结构：
{volumes_desc}

主要人物（设计角色相关伏笔时参考）：
{chars_desc}

世界秘密（需要作为伏笔逐步揭露）：
{chr(10).join(world_secrets) if world_secrets else '（待设计）'}

已规划的其他伏笔（仅供参考，本批不重复）：
{existing}
{twist_block}
═══ 要求 ═══
- 生成 {low}-{high} 个【{importance_label}】
- 每个伏笔明确：植入章 / 计划兑现章（卷内或跨卷） / 兑现时的具体场景
- 兑现的落点必须冲击主角（让主角承受/顿悟/付出代价）
- 同一章不要埋超过 2 个伏笔

输出 JSON：
{{
  "foreshadow_items": [
    {{
      "fw_id": "fw_{importance_label[:2]}_1",
      "content": "读者看到的内容（50字）",
      "hidden_meaning": "真实含义（50字，作者视角）",
      "importance": "{importance_label}",
      "planned_plant_chapter": 植入章编号,
      "planned_resolve_volume": 兑现卷号,
      "planned_resolve_chapter": 兑现章编号（-1未定）,
      "resolution_description": "兑现时的具体场景（60字，震撼落点在主角身上）",
      "related_sp_id": "关联的爽点id（无则空字符串）"
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["foreshadow_items", "foreshadows", "items"],
        min_items=low, max_retries=3, temperature=0.75,
        agent_name=f"ForeshadowManager[{importance_label}]",
        empty_ok=True,
    )
    fw_data = pick_list(data, "foreshadow_items", "foreshadows", "items") if data else []
    if not fw_data:
        print(f"    {importance_label} 批次跳过（LLM 重试失败）")
        return

    imp_map = {i.value: i for i in ForeshadowImportance}
    count_before = len(state.foreshadow_items)
    for fwd in fw_data:
        fw = ForeshadowItem(
            fw_id=fwd.get("fw_id", f"fw_{len(state.foreshadow_items)+1:03d}"),
            content=fwd.get("content", ""),
            hidden_meaning=fwd.get("hidden_meaning", ""),
            importance=imp_map.get(fwd.get("importance", importance_label),
                                    ForeshadowImportance.MINOR),
            planted_chapter=int(fwd.get("planned_plant_chapter", 0)),
            planned_resolve_volume=int(fwd.get("planned_resolve_volume", 1)),
            planned_resolve_chapter=int(fwd.get("planned_resolve_chapter", -1)),
            resolution_description=fwd.get("resolution_description", ""),
            related_sp_id=fwd.get("related_sp_id", ""),
        )
        state.foreshadow_items.append(fw)
    print(f"    {importance_label}：+{len(state.foreshadow_items) - count_before} 个")


def plan_red_herrings(state: NovelState) -> None:
    """
    规划红鲱鱼（假线索）——故意误导读者的假伏笔。
    数量精简：3-6 个即可。读者以为是伏笔 A，实际是个障眼法，真相另在。
    """
    from agents import require_upstream
    if not require_upstream(state, "RedHerringPlanner",
        volumes=lambda s: bool(s.volumes),
        foreshadow_items=lambda s: bool(s.foreshadow_items),
    ):
        return
    concept = format_concept_brief(state)
    volumes_desc = "\n".join(
        f"第{v.index}卷 [第{v.chapter_start}-{v.chapter_end}章]：{v.theme}"
        for v in state.volumes
    )
    # 已有伏笔列表——红鲱鱼不能与真伏笔混淆
    real_fw_brief = "\n".join(
        f"- [真伏笔·{fw.importance.value}] {fw.content[:30]}"
        for fw in state.foreshadow_items[:10]
    ) or "（暂无真伏笔）"

    prompt = f"""
为《{state.title}》规划【红鲱鱼（假线索）】——3-6 个故意误导读者的假伏笔。

{concept}

卷结构：
{volumes_desc}

已有真伏笔（红鲱鱼不能和这些撞车）：
{real_fw_brief}

═══ 要求 ═══
红鲱鱼是读者以为是伏笔但其实不是的线索。它的目的：
- 让读者在某段剧情里往错方向猜（"这人肯定是反派卧底"）
- 后来揭穿时读者恍然大悟（"原来真凶是另一个从没怀疑过的人"）

给出 3-6 个：
- content：假线索呈现给读者的样子（50字）
- misdirection_purpose：误导的目的（40字，"让读者以为X，实际是Y"）
- planted_chapter：计划植入章（全书章节编号）
- debunk_chapter：计划揭穿/被证伪的章（-1=让读者自行回味）
- actual_truth：真相（60字，作者视角，为什么这是假的）

输出 JSON：
{{
  "red_herrings": [
    {{
      "rh_id": "rh_1",
      "content": "...",
      "misdirection_purpose": "...",
      "planted_chapter": 章号,
      "debunk_chapter": 章号或-1,
      "actual_truth": "..."
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["red_herrings", "items"],
        min_items=2, max_retries=3, temperature=0.75,
        agent_name="ChekhovTracker[红鲱鱼]",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ 红鲱鱼规划跳过——本书将无假线索误导层")
        return

    for r in pick_list(data, "red_herrings", "items"):
        rh = RedHerring(
            rh_id=r.get("rh_id", f"rh_{len(state.red_herrings)+1:03d}"),
            content=r.get("content", ""),
            misdirection_purpose=r.get("misdirection_purpose", ""),
            planted_chapter=int(r.get("planted_chapter", 0)),
            debunk_chapter=int(r.get("debunk_chapter", -1)),
            actual_truth=r.get("actual_truth", ""),
        )
        state.red_herrings.append(rh)

    print(f"  ✓ 红鲱鱼：{len(state.red_herrings)} 个假线索")
    for rh in state.red_herrings:
        dk = f"第{rh.debunk_chapter}章揭穿" if rh.debunk_chapter > 0 else "读者自悟"
        print(f"    [rh_{rh.rh_id[-3:]}] 第{rh.planted_chapter}章植入→{dk}：{rh.content[:40]}")


def get_chapter_foreshadow_directive(state: NovelState, chapter_index: int) -> dict:
    """
    返回本章的伏笔操作指令：
    - plant: 需要植入的伏笔（附完整内容）
    - resolve: 需要兑现的伏笔（附兑现描述）
    """
    to_plant = [fw for fw in state.foreshadow_items if fw.planted_chapter == chapter_index and not fw.resolved]
    to_resolve = [
        fw for fw in state.foreshadow_items
        if not fw.resolved
        and fw.planted_chapter > 0
        and abs(fw.planned_resolve_chapter - chapter_index) <= 2
    ]
    return {"plant": to_plant, "resolve": to_resolve}


def update_after_chapter(state: NovelState, chapter_index: int, planted_ids: list[str], resolved_ids: list[str]):
    """章节写完后更新伏笔状态。"""
    for fw_id in planted_ids:
        fw = state.get_foreshadow(fw_id)
        if fw and fw.planted_chapter == 0:
            fw.planted_chapter = chapter_index

    for fw_id in resolved_ids:
        fw = state.get_foreshadow(fw_id)
        if fw:
            fw.resolved = True
            fw.actual_resolve_chapter = chapter_index


def get_foreshadow_status_report(state: NovelState) -> str:
    """生成伏笔状态报告，供 Director 决策参考。"""
    unplanted = [fw for fw in state.foreshadow_items if fw.planted_chapter == 0]
    planted = [fw for fw in state.foreshadow_items if fw.planted_chapter > 0 and not fw.resolved]
    resolved = [fw for fw in state.foreshadow_items if fw.resolved]

    lines = [
        f"伏笔状态：未植入 {len(unplanted)} / 已植入待兑现 {len(planted)} / 已兑现 {len(resolved)}",
    ]
    if planted:
        lines.append("待兑现（主线）：")
        for fw in [f for f in planted if f.importance == ForeshadowImportance.MAJOR]:
            lines.append(f"  [{fw.fw_id}] 第{fw.planted_chapter}章埋 → 计划第{fw.planned_resolve_chapter}章兑：{fw.content[:40]}")
    return "\n".join(lines)


def _print_fw_summary(state: NovelState):
    total = len(state.foreshadow_items)
    major = len([f for f in state.foreshadow_items if f.importance == ForeshadowImportance.MAJOR])
    print(f"  ✓ 伏笔体系：共 {total} 个伏笔（{major} 个主线伏笔）")
    for fw in state.foreshadow_items[:5]:
        print(f"    [{fw.importance.value}] {fw.content[:35]}... → 第{fw.planned_resolve_chapter}章兑现")

