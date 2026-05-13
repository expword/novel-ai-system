"""
RhythmDesignerAgent — 设计情节节奏：每卷节奏蓝图、喘息章、快慢交替模式。
"""
import json
from utils.json_utils import repair_json, safe_parse, pick_list, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, VolumeRhythmPlan, RhythmSegment, RhythmType
from config import NUM_VOLUMES


SYSTEM = """你是顶级小说节奏师，专注于长篇小说的阅读体验设计。
节奏设计原则：
- 【单主角】节奏服务于主角的情绪曲线——让读者跟着主角呼吸。喘息章也要让读者对"主角此刻的日常/反思"有兴趣，不是纯写配角日常。
- 【分形对齐】每卷的节奏段落要与该卷的起承转合对齐：
  * 卷的"起"段：慢热铺垫为主，夹杂钩子
  * 卷的"承"段：快慢交替，矛盾积累
  * 卷的"转"段：快节奏+反转+情感沉淀三件套
  * 卷的"合"段：先快（高潮）后慢（余波）再钩子
- "快慢快"交替，不能一直高速或一直平淡
- 每4-8章要有1章"喘息章"（让读者和角色都喘口气）
- 战斗/冲突之后必须有沉淀（情感/思考/转折）
- 信息揭示章要慢下来，让读者消化
- 卷首需要快速钩子（3章内必须有吸引力）
- 卷尾节奏要先快（高潮）后慢（余波）再反转（钩子）
输出严格JSON。"""


def design_all_rhythms(state: NovelState) -> None:
    """为所有卷设计节奏蓝图，写入 state.rhythm_plans。"""

    sp_desc = "\n".join(
        f"第{sp.volume}卷/第{sp.target_chapter}章：{sp.title}（{sp.intensity}分/{sp.sp_type.value}）"
        for sp in state.satisfaction_points[:15]
    )

    volumes_desc = "\n".join(
        f"第{v.index}卷《{v.title}》[{v.structure_role or '?'}]：第{v.chapter_start}-{v.chapter_end}章，{v.total_chapters}章"
        f"，主题：{v.theme}，卷内弧线：{v.arc[:80]}"
        for v in state.volumes
    )

    prompt = f"""
请为《{state.title}》每一卷设计详细的情节节奏蓝图。

各卷概况：
{volumes_desc}

已规划的爽点分布：
{sp_desc}

节奏分段要求：
- 每卷划分5-8个节奏段
- 节奏类型：慢热铺垫/快节奏战斗/情感沉淀/信息揭示/过渡转场
- 标记喘息章（具体章节编号）
- 标记高潮章（具体章节编号）

输出JSON：
{{
  "volume_rhythms": [
    {{
      "volume_index": 1,
      "overall_pattern": "整卷节奏模式描述（30字，如'慢热铺垫→双线加速→中段反转→冲刺收束'）",
      "segments": [
        {{
          "chapter_start": 全书章节起始号,
          "chapter_end": 全书章节结束号,
          "rhythm_type": "慢热铺垫|快节奏战斗|情感沉淀|信息揭示|过渡转场",
          "description": "这段节奏的目的（40字）",
          "word_pace": "紧凑|舒缓|中等"
        }}
      ],
      "breathing_chapters": [喘息章的全书章节编号列表],
      "climax_chapters": [高潮章的全书章节编号列表]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["volume_rhythms", "rhythms", "items"],
        min_items=1,
        max_retries=4, temperature=0.68, agent_name="RhythmDesigner",
        empty_ok=True,
    )
    rhythms_data = pick_list(data, "volume_rhythms", "rhythms", "items") if data else []
    if not rhythms_data:
        print(f"  ⚠ RhythmDesigner 跳过（LLM 重试失败）——各章将用默认节奏")
        return

    rhythm_map = {r.value: r for r in RhythmType}
    for vrd in rhythms_data:
        segs = [
            RhythmSegment(
                chapter_start=s["chapter_start"],
                chapter_end=s["chapter_end"],
                rhythm_type=rhythm_map.get(s["rhythm_type"], RhythmType.SLOW_BUILD),
                description=s["description"],
                word_pace=s.get("word_pace", "中等"),
            )
            for s in vrd["segments"]
        ]
        plan = VolumeRhythmPlan(
            volume_index=vrd["volume_index"],
            overall_pattern=vrd["overall_pattern"],
            segments=segs,
            breathing_chapters=vrd.get("breathing_chapters", []),
            climax_chapters=vrd.get("climax_chapters", []),
        )
        state.rhythm_plans.append(plan)

    print(f"  ✓ 节奏设计：{len(state.rhythm_plans)} 卷节奏蓝图")
    for plan in state.rhythm_plans:
        breathing = len(plan.breathing_chapters)
        climax = len(plan.climax_chapters)
        print(f"    第{plan.volume_index}卷：{plan.overall_pattern}（喘息{breathing}章/高潮{climax}章）")


def get_rhythm_instruction(state: NovelState, chapter_index: int) -> str:
    """获取当前章节的节奏指令，供写作参考。"""
    seg = state.get_rhythm_for_chapter(chapter_index)
    if not seg:
        return "节奏：中等"

    # 检查是否是喘息章或高潮章
    for plan in state.rhythm_plans:
        if chapter_index in plan.breathing_chapters:
            return f"【喘息章】节奏：舒缓 | {seg.description} | 让读者休息，角色日常/情感互动"
        if chapter_index in plan.climax_chapters:
            return f"【高潮章】节奏：极度紧凑 | {seg.description} | 短句密集，冲突最大化"

    return f"节奏：{seg.rhythm_type.value}（{seg.word_pace}）| {seg.description}"
