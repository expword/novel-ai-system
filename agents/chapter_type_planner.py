"""
ChapterTypePlannerAgent — Phase 4-C：每卷章节类型配比规划。

为什么需要：网文章节有固定类型（打脸/升级/铺垫/感情/战斗/日常/真相/转折/余韵/推进）。
如果让 Director 临场决定每章类型，会出现"连续 5 章打脸"或"连续 8 章日常"这种崩节奏。

这个 agent 按卷规划每章的类型——让节奏从一开始就是被设计好的，不是临场发挥的。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import (
    NovelState, CHAPTER_TYPES,
    VolumeChapterTypeDistribution, ChapterTypeAssignment,
)
from agents.concept_pitch import format_concept_brief


SYSTEM = """你是网文章节类型节奏师。
章节类型决定了读者的阅读情绪走向。过多铺垫会弃书，过多战斗会麻木，过多日常会嫌慢。
你的工作：给一卷章节配类型，让类型分布有波浪——铺垫间或感情、感情间或升级、升级间或战斗，
每 3-5 章有一个类型切换，每 7-10 章有一个爽点章（打脸/升级/真相），
每 5-8 章有一个喘息章（日常/感情）。
输出严格 JSON。"""


def plan_chapter_types(state: NovelState, volume_index: int) -> None:
    """为单卷生成章节类型规划——每章分派一个类型，并说明配比逻辑。"""
    vol = state.get_volume(volume_index)
    if not vol:
        return

    concept = format_concept_brief(state)
    emotion_note = state.emotion_curve.get(volume_index) if state.emotion_curve else None
    conflict = state.conflict_ladder.get(volume_index) if state.conflict_ladder else None

    emo_hint = ""
    if emotion_note:
        emo_hint = (
            f"情绪曲线：基调[{emotion_note.base_tone}]｜"
            f"低谷第{emotion_note.low_point_chapter}章｜高点第{emotion_note.high_point_chapter}章"
        )
    conflict_hint = ""
    if conflict:
        conflict_hint = (
            f"冲突：[{conflict.conflict_type}·T{conflict.opponent_tier}·{conflict.resolution_method}]"
            f"：{conflict.core_conflict[:40]}"
        )

    # 卷内爽点分布——类型规划要配合爽点落点
    vol_sps = [sp for sp in state.satisfaction_points if sp.volume == volume_index]
    sp_brief = "\n".join(
        f"  第{sp.target_chapter}章 [{sp.sp_type.value}·强度{sp.intensity}]：{sp.title}"
        for sp in vol_sps
    ) or "（本卷无已规划爽点）"

    prompt = f"""
为第 {volume_index} 卷《{vol.title}》规划章节类型分布。

{concept}

卷信息：
  章节范围：第{vol.chapter_start}-{vol.chapter_end}章（共{vol.total_chapters}章）
  主题：{vol.theme}
  结构角色：{vol.structure_role}
  弧线：{vol.arc[:120]}

{emo_hint}
{conflict_hint}

本卷爽点分布（类型规划要配合这些落点）：
{sp_brief}

═══ 可选类型 ═══
{' / '.join(CHAPTER_TYPES)}

═══ 要求 ═══
1. type_distribution：给出每种类型在本卷占多少章（加起来 = {vol.total_chapters}）。
   约束：
   - 铺垫章+推进章+日常章 合计不超过 60%
   - 战斗章+打脸章+升级章 合计不少于 20%，不超过 50%
   - 真相章/转折章通常只有 1-3 章（一卷里大转折点）
   - 感情章 3-8 章（根据立项 romance_policy 调整）
   - 余韵章（重大事件后的情绪沉淀）2-4 章

2. per_chapter：给出本卷每一章的类型分派：
   - 每 3-5 章切换类型，不连续同类型超过 3 章
   - 爽点落点章（target_chapter）对应类型必须是：打脸章/升级章/真相章/转折章之一
   - 低谷前面几章可以安排感情章/铺垫章
   - 高点章最好是打脸/升级/真相
   - reason 写"为什么这一章是这个类型"（25字）

输出 JSON：
{{
  "type_distribution": {{"铺垫章": 10, "推进章": 8, ...}},
  "per_chapter": [
    {{"chapter_index": 章节编号, "chapter_type": "...", "reason": "..."}}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["type_distribution", "per_chapter"],
        max_retries=3, temperature=0.7,
        agent_name=f"ChapterTypePlanner[V{volume_index}]",
        empty_ok=True,
    )
    if not data:
        print(f"  ⚠ 第{volume_index}卷章节类型规划跳过")
        return

    dist = {k: int(v) for k, v in data.get("type_distribution", {}).items()}
    per_ch_raw = pick_list(data, "per_chapter", "chapters", "items")
    per_ch = [ChapterTypeAssignment(
        chapter_index=int(a.get("chapter_index", 0)),
        chapter_type=a.get("chapter_type", ""),
        reason=a.get("reason", ""),
    ) for a in per_ch_raw]

    ctp = VolumeChapterTypeDistribution(
        volume=volume_index,
        type_distribution=dist,
        per_chapter=per_ch,
    )
    # 去重后追加
    state.chapter_type_plans = [p for p in state.chapter_type_plans if p.volume != volume_index]
    state.chapter_type_plans.append(ctp)

    print(f"  ✓ 第{volume_index}卷章节类型规划：{len(per_ch)} 章分派类型")
    if dist:
        summary = " / ".join(f"{k}×{v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])[:6])
        print(f"    配比：{summary}")


def plan_all_chapter_types(state: NovelState) -> None:
    """为所有卷规划章节类型分布。"""
    for v in state.volumes:
        plan_chapter_types(state, v.index)
