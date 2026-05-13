"""
ConflictLadderAgent — Phase 3-A2：每卷核心冲突的类型+层级+解决方式规划。

为什么独立：SatisfactionSystem 管爽点，但爽点需要冲突铺垫。
冲突如果每卷都是"人vs人"、都是打武力战，小说会单调。必须有：
- 类型多样（人vs人 / 人vs势力 / 人vs天道 / 人vs自己 / 人vs规则）
- 层级递进（对手越来越强，不能倒退）
- 解决方式多样（武力 / 智谋 / 情感 / 合作 / 运气 / 牺牲）
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, ConflictLadder, ConflictEntry
from agents.concept_pitch import format_concept_brief


SYSTEM = """你是小说冲突结构师。
好的长篇要让读者觉得"每一卷的挣扎都不一样"——这需要冲突类型、对手层级、解决方式都在变化。
- 类型：人vs人 / 人vs势力 / 人vs天道 / 人vs自己 / 人vs规则
- 层级（opponent_tier 1-5）：绝对递进，不倒退
- 解决方式：武力 / 智谋 / 情感 / 合作 / 运气 / 牺牲——尽量不重复用同一种
输出严格 JSON。"""


def design_conflict_ladder(state: NovelState) -> None:
    concept = format_concept_brief(state)
    volumes_brief = "\n".join(
        f"第{v.index}卷《{v.title}》[{v.structure_role or '?'}]：{v.theme}"
        f"｜对手：{v.volume_antagonist}｜弧线：{v.arc[:80]}"
        for v in state.volumes
    )

    prompt = f"""
为《{state.title}》规划【冲突阶梯】——每卷一条。

{concept}

各卷概况：
{volumes_brief}

═══ 要求 ═══
对每卷各给一条：
1. conflict_type：人vs人 | 人vs势力 | 人vs天道 | 人vs自己 | 人vs规则
   ——{len(state.volumes)} 卷加起来必须覆盖**至少 3 种不同类型**，不能全是"人vs人"
2. core_conflict：核心冲突的具体描述（40字）
3. opponent_tier：对手层级 1-5。**严格递进**——后一卷 >= 前一卷；高潮/转段跳一级
4. resolution_method：武力 | 智谋 | 情感 | 合作 | 运气 | 牺牲
   ——{len(state.volumes)} 卷不能全是同一种解决方式；至少 3 种不同
5. escalation_note：这卷比上卷升级在哪（30字；第1卷可写"建立基线"）
6. why_this_type：为什么这一卷选这种类型（20字）

输出 JSON：
{{
  "entries": [
    {{
      "volume": 1,
      "conflict_type": "...",
      "core_conflict": "...",
      "opponent_tier": 1,
      "resolution_method": "...",
      "escalation_note": "...",
      "why_this_type": "..."
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["entries", "items"],
        min_items=len(state.volumes),
        item_required_keys=["volume", "conflict_type", "opponent_tier"],
        max_retries=4, temperature=0.7, agent_name="ConflictLadder",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ ConflictLadder 跳过（LLM 重试失败）")
        return

    ladder = ConflictLadder(
        entries=[ConflictEntry(
            volume=int(e.get("volume", 1)),
            conflict_type=e.get("conflict_type", ""),
            core_conflict=e.get("core_conflict", ""),
            opponent_tier=int(e.get("opponent_tier", 1)),
            resolution_method=e.get("resolution_method", ""),
            escalation_note=e.get("escalation_note", ""),
            why_this_type=e.get("why_this_type", ""),
        ) for e in pick_list(data, "entries", "items")]
    )
    state.conflict_ladder = ladder

    print(f"  ✓ 冲突阶梯：{len(ladder.entries)} 卷")
    types_used = set(e.conflict_type for e in ladder.entries)
    methods_used = set(e.resolution_method for e in ladder.entries)
    print(f"    类型覆盖：{len(types_used)} 种（{' / '.join(types_used)}）")
    print(f"    解决方式：{len(methods_used)} 种（{' / '.join(methods_used)}）")
    for e in sorted(ladder.entries, key=lambda x: x.volume):
        print(f"    V{e.volume}[{e.conflict_type}·T{e.opponent_tier}·{e.resolution_method}]：{e.core_conflict[:40]}")
