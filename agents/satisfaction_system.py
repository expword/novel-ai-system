"""
SatisfactionSystemAgent — 规划全书爽点：类型/强度/铺垫链/兑现方式。
确保每卷都有足够密度的爽感，且有递进关系。
"""
import json
from utils.json_utils import repair_json, safe_parse, pick_list, request_json
from agents.concept_pitch import format_concept_brief
from agents.plot_enhancer import format_adopted_supplements
from llm_layer.llm import system_user
from persistence.state import NovelState, SatisfactionPoint, SatisfactionSetup, SatisfactionType
from config import NUM_VOLUMES


SYSTEM = """你是爽文节奏大师，专注设计"爽点"——那些让读者血压飙升、拍案叫绝的时刻。
【单主角铁律】所有爽点必须是"主角的爽点"——读者爽是因为主角爽/主角扬眉吐气/主角兑现誓言。
- 爽点的主语永远是主角，哪怕触发条件来自配角或反派。
- 严禁设计"主角不在场，读者却很爽"的爽点。
- 严禁配角替主角"扮演"爽点主角（比如盟友把反派打脸了但主角没参与）。

爽点设计原则：
1. 铺垫越长，爆发越爽（委屈积累 → 痛快反击）——铺垫的委屈必须是主角承受的
2. 爽点需要"见证者"（必须有在场的人看到主角的瞬间，才能最大化戏剧效果）
3. 类型要多样，不能全是战斗，情感爆发/真相揭露同样震撼
4. 递进原则：后面的爽点要比前面的更强烈
5. 每卷至少3个爽点，其中1个大爽点作为卷高潮
6. 爽点分布要与各卷的起承转合对齐——"转"段和"合"段是爽点密集期，"起""承"以铺垫为主
输出严格JSON。"""


def plan_all_satisfaction_points(state: NovelState) -> None:
    """规划全书爽点，按卷分批生成——每卷一次 LLM 调用。"""
    from agents import require_upstream
    if not require_upstream(state, "SatisfactionSystem",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
    ):
        return
    for v in state.volumes:
        _plan_volume_sps(state, v)

    total = len(state.satisfaction_points)
    if total == 0:
        print(f"  ⚠ SatisfactionSystem 没有生成任何爽点")
        return
    print(f"  ✓ 爽点规划总计：{total} 个（按卷分批）")
    _print_sp_summary(state)


def _plan_volume_sps(state: NovelState, vol) -> None:
    """为单个卷规划爽点——一次 LLM 调用只专注本卷。"""
    # 强度区间随卷数递进：早卷 4-6 / 中卷 6-8 / 晚卷 8-10
    third = max(1, NUM_VOLUMES // 3)
    if vol.index <= third:
        intensity_band = "4-6"
    elif vol.index <= third * 2:
        intensity_band = "6-8"
    else:
        intensity_band = "8-10"

    # 本卷反派 + 主要角色
    antag = vol.volume_antagonist or "（未定）"
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"
    ps = state.power_system
    sys_type = ps.system_type if ps else "realms"
    stage_label = {
        "realms": "境界",
        "skill_tiers": "段位",
        "social_rank": "地位",
        "progression_arc": "人生阶段",
        "none": "主角所处阶段",
    }.get(sys_type, "阶段")
    realm_before = ps.protagonist_realm_plan.get(vol.index - 1, "起始") if ps else ""
    realm_after = ps.protagonist_realm_plan.get(vol.index, "未定") if ps else ""

    # 流派 + 特殊机制——让爽点设计贴合流派风格
    flow_hint = ""
    if ps:
        ex = []
        if ps.power_flow:
            ex.append(f"流派：{ps.power_flow}")
        if ps.special_mechanics:
            mech = " / ".join(m.name for m in ps.special_mechanics[:4])
            ex.append(f"特殊机制：{mech}——爽点若与这些机制联动更吸睛（如系统流的签到奖励爆表、无限流的主神评分登顶、诡秘流的仪式成功）")
        if ex:
            flow_hint = "\n" + "\n".join(ex)

    # 前卷已有爽点（供参考，避免重复）
    prev_sps = [sp for sp in state.satisfaction_points if sp.volume < vol.index][-5:]
    prev_brief = "\n".join(
        f"  · 第{sp.volume}卷《{sp.title}》（{sp.sp_type.value}｜强度{sp.intensity}）"
        for sp in prev_sps
    ) or "（尚无已规划爽点）"

    concept_block = format_concept_brief(state)

    # 作者已采纳的补充情节建议——本卷范围内的优先转成爽点 / 铺垫
    supplements_block = format_adopted_supplements(state.creative_intent)
    if supplements_block:
        supplements_block = (
            supplements_block
            + "\n  ⚠ 若上面的「建议注入」匹配本卷范围，必须把它转成本卷的具体爽点"
            "（按 setup_chain 铺垫 + 兑现的形式）"
        )

    # 爽点偏好——立项层定的
    sp_pref = state.trope_library.preferred_sp_types
    sp_pref_hint = ""
    if sp_pref:
        sp_pref_hint = f"\n【爽点类型偏好（立项层决定，按此排序权重）】\n  {' > '.join(sp_pref)}"

    # Phase 2.2:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    prompt = f"""{feedback_prefix}
为《{state.title}》第{vol.index}卷《{vol.title}》规划爽点——3-5 个即可。

{concept_block}{sp_pref_hint}

{supplements_block}

卷信息：
  章节范围：第{vol.chapter_start}-{vol.chapter_end}章
  主题：{vol.theme}
  卷弧线：{vol.arc[:120]}
  主要对手：{antag}
  主角{stage_label}：{realm_before} → {realm_after}{flow_hint}

前卷已有的近期爽点（用于避免类型重复、强度递进）：
{prev_brief}

═══ 本卷要求 ═══
- 3-5 个爽点，其中 **1 个大爽点作为卷高潮**（通常放在卷中或卷尾附近）
- 强度区间：{intensity_band}（本卷位于全书第 {vol.index}/{NUM_VOLUMES} 卷）
- 类型覆盖（本卷不必全部都有，但整体要多样）：打脸 / 突破 / 复仇 / 逆袭 / 情感爆发 / 真相揭露 / 实力展示 / 羁绊达成
  ★ 提示：本书体系类型为 [{sys_type}]——"突破""实力展示"在非修炼/超能题材里意思要相应变化：
    - realms/skill_tiers：修炼境界突破、能力觉醒、碾压性战斗
    - social_rank：关键项目拿下、行业地位跃升、商战胜负
    - progression_arc：内心蜕变、人生抉择兑现、认知升级
    - none：情感顿悟、关系突破、命运扭转
  不要给职场文写"一拳崩石"这种跳戏的爽点。
- 每个大爽点要有 3-5 章的铺垫链（setup_chain）——委屈先埋，后面才爽
- 【主角爽铁律】爽的主语永远是主角{prot_name}——主角扬眉吐气/主角兑现誓言。严禁主角缺席的爽点

输出 JSON：
{{
  "satisfaction_points": [
    {{
      "sp_id": "sp_v{vol.index}_1",
      "sp_type": "打脸|突破|复仇|逆袭|情感爆发|真相揭露|实力展示|羁绊达成",
      "title": "爽点标题（10字）",
      "description": "具体场景（80字）",
      "intensity": {intensity_band.split('-')[0]}到{intensity_band.split('-')[1]}之间的整数,
      "volume": {vol.index},
      "target_chapter": 预计爆发的章节编号（{vol.chapter_start}-{vol.chapter_end}）,
      "setup_chain": [
        {{"chapter": 铺垫章节编号, "content": "铺垫内容（30字）"}}
      ],
      "payoff_description": "爆发时的具体呈现（50字）"
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["satisfaction_points", "sps", "points", "items"],
        min_items=2, max_retries=3, temperature=0.8,
        agent_name=f"SatisfactionSystem[V{vol.index}]",
        empty_ok=True,
    )
    sp_data = pick_list(data, "satisfaction_points", "sps", "points", "items") if data else []
    if not sp_data:
        print(f"    第{vol.index}卷爽点跳过（LLM 重试失败）")
        return

    sp_type_map = {t.value: t for t in SatisfactionType}
    count_before = len([sp for sp in state.satisfaction_points if sp.volume == vol.index])
    for spd in sp_data:
        setups = [
            SatisfactionSetup(chapter=int(s.get("chapter", vol.chapter_start)),
                              content=s.get("content", ""))
            for s in spd.get("setup_chain", [])
        ]
        sp = SatisfactionPoint(
            sp_id=spd.get("sp_id", f"sp_v{vol.index}_{len(state.satisfaction_points)+1}"),
            sp_type=sp_type_map.get(spd.get("sp_type", ""), SatisfactionType.REVERSAL),
            title=spd.get("title", "（未命名爽点）"),
            description=spd.get("description", ""),
            intensity=int(spd.get("intensity", 5)),
            volume=int(spd.get("volume", vol.index)),
            target_chapter=int(spd.get("target_chapter", vol.chapter_end)),
            setup_chain=setups,
            payoff_description=spd.get("payoff_description", ""),
        )
        state.satisfaction_points.append(sp)
    count_after = len([sp for sp in state.satisfaction_points if sp.volume == vol.index])
    print(f"    第{vol.index}卷爽点：+{count_after - count_before} 个")


def get_sp_for_chapter(state: NovelState, chapter_index: int) -> dict:
    """返回本章需要执行的爽点操作：触发/铺垫/无。"""
    result = {"trigger": [], "setup": []}

    for sp in state.satisfaction_points:
        if sp.triggered:
            continue
        # 需要触发的爽点
        if abs(sp.target_chapter - chapter_index) <= 1:
            result["trigger"].append(sp)
        # 需要铺垫的爽点
        for setup in sp.setup_chain:
            if setup.chapter == chapter_index:
                result["setup"].append({"sp": sp, "setup_content": setup.content})

    return result


def mark_sp_triggered(state: NovelState, sp_id: str, chapter_index: int):
    sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
    if sp:
        sp.triggered = True
        sp.actual_chapter = chapter_index


def _print_sp_summary(state: NovelState):
    total = len(state.satisfaction_points)
    high = len([s for s in state.satisfaction_points if s.intensity >= 8])
    print(f"  ✓ 爽点系统：共 {total} 个爽点（{high} 个高强度8+）")
    for v_idx in range(1, NUM_VOLUMES + 1):
        vol_sps = [s for s in state.satisfaction_points if s.volume == v_idx]
        if vol_sps:
            titles = " / ".join(f"{s.title}({s.intensity})" for s in vol_sps)
            print(f"    第{v_idx}卷：{titles}")
