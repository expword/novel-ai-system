"""
EmotionCurveAgent — Phase 3-D2：每卷情绪曲线规划。

和 RhythmDesigner 互补：
- RhythmDesigner 管"节奏"——快慢密度（句法层）
- EmotionCurve 管"情绪"——基调走向（心理层）

产出每卷：
- 基调（热血/悲情/轻松/压抑/温暖/黑暗/希望）
- 低谷位置与描述（观众必须先失望才能真正感受到爽）
- 高点位置与描述
- 与上卷的情绪对冲（不要连续两卷同一基调）
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, EmotionCurve, EmotionNote
from agents.concept_pitch import format_concept_brief


SYSTEM = """你是情绪节奏设计师。
整本小说的阅读体验，不只是剧情密度，更是情绪起伏。
你要让读者在每一卷都经历一次"先失望再爽"或者"先温暖再破碎"的情绪旅程——
高潮需要低谷铺垫，爽需要苦衬托。
而且卷与卷之间，情绪基调要对冲——别让读者连着读两卷压抑，会弃书。
输出严格 JSON。"""


TONES = ["热血", "悲情", "轻松", "压抑", "温暖", "黑暗", "希望", "苍凉", "诡谲"]


def design_emotion_curve(state: NovelState) -> None:
    concept = format_concept_brief(state)

    # 提取立项层的世界基调给提示词
    world_tone = state.trope_library.world_tone if state.trope_library else ""

    volumes_brief = "\n".join(
        f"第{v.index}卷《{v.title}》[{v.chapter_start}-{v.chapter_end}章]"
        f"｜结构[{v.structure_role}]｜主题：{v.theme}｜弧：{v.arc[:80]}"
        for v in state.volumes
    )

    # 冲突阶梯可以参考——冲突 tier 高的卷情绪容易更重
    conflict_brief = state.conflict_ladder.brief() if state.conflict_ladder else ""

    # Phase 2.2:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    prompt = f"""{feedback_prefix}
为《{state.title}》规划【情绪曲线】——每卷一条。

{concept}

世界基调（立项层已定）：{world_tone or '（未定）'}

各卷概况：
{volumes_brief}

冲突阶梯（供参考，情绪可与冲突层级共振）：
{conflict_brief}

═══ 要求 ═══
对每卷各给一条：
1. base_tone：本卷情绪基调（从 {' / '.join(TONES)} 中选）
2. low_point_chapter：低谷大致章节（卷内，不能是卷首第一章）
3. low_point_desc：低谷描述（30字，主角失望/心疼/绝望的瞬间；是什么让读者替主角难过）
4. high_point_chapter：高点大致章节
5. high_point_desc：高点描述（30字，是什么让读者燃起来/释然）
6. contrast_with_prev：与上卷情绪基调的对冲（25字；第1卷写"建立基线"）

【卷间对冲铁律】连续两卷不得是同一个 base_tone——必须对冲。如果实在要连续，在 contrast_with_prev 里说明强度/角度上的反差。

输出 JSON：
{{
  "notes": [
    {{
      "volume": 1,
      "base_tone": "...",
      "low_point_chapter": 章节编号,
      "low_point_desc": "...",
      "high_point_chapter": 章节编号,
      "high_point_desc": "...",
      "contrast_with_prev": "..."
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["notes", "items"],
        min_items=len(state.volumes),
        item_required_keys=["volume", "base_tone"],
        max_retries=4, temperature=0.7, agent_name="EmotionCurve",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ EmotionCurve 跳过（LLM 重试失败）")
        return

    curve = EmotionCurve(
        notes=[EmotionNote(
            volume=int(n.get("volume", 1)),
            base_tone=n.get("base_tone", ""),
            low_point_chapter=int(n.get("low_point_chapter", 0)),
            low_point_desc=n.get("low_point_desc", ""),
            high_point_chapter=int(n.get("high_point_chapter", 0)),
            high_point_desc=n.get("high_point_desc", ""),
            contrast_with_prev=n.get("contrast_with_prev", ""),
        ) for n in pick_list(data, "notes", "items")]
    )
    state.emotion_curve = curve

    print(f"  ✓ 情绪曲线：{len(curve.notes)} 卷")
    for n in sorted(curve.notes, key=lambda x: x.volume):
        print(f"    V{n.volume}[{n.base_tone}]：低谷{n.low_point_chapter}({n.low_point_desc[:30]}) → 高点{n.high_point_chapter}({n.high_point_desc[:30]})")
