"""
FortunePlannerAgent — 规划主角的所有机缘。

机缘是主角成长的燃料，分布在各卷的关键节点。
每个机缘有：位置/获取条件/对成长的影响/引发的后续剧情。

设计原则：
- 机缘与主角的境界突破节点对齐
- 机缘的获取要有代价（不是天上掉馅饼）
- 机缘引发后续剧情（不是获得就结束）
- 高价值机缘要有竞争者（增加张力）
"""
from utils.json_utils import repair_json, pick_list, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, Fortune
from config import NUM_VOLUMES


SYSTEM = """你是小说"契机/机缘"设计师——负责设计主角成长的关键节点。
根据体系类型（system_type），"契机"的含义不同：
- realms/skill_tiers（修炼/超能）→ 传统机缘：传承 / 宝物 / 奇遇 / 功法 / 秘境 / 领悟 / 血脉觉醒 / 贵人
- social_rank（社会向）→ 职场机遇：大项目 / 行业风口 / 贵人相助 / 关键客户 / 重要饭局 / 内部情报 / 创业时机 / 舆论事件
- progression_arc（人生向）→ 生命契机：高考/求职/恋情/婚姻 / 至亲离世 / 子女诞生 / 重大疾病 / 一次远行 / 一本书 / 一次顿悟
- none（无体系）→ 只需设计少量决定性的"命运时刻"

【单主角】所有契机都属于唯一主角——是主角人生轨迹的转折点。配角/对手不参与契机分配。

契机设计原则（所有题材通用）：
- 【分阶段融入】大契机应该有"预兆→接近→获得/错过→消化→激活"的过程，不是瞬间完事
- 【与剧情自然共振】契机的出现时机必须契合主角当前处境/内心困境
- 【要有代价或考验】获取过程有戏剧性——不是天上掉馅饼
- 【引发后续剧情】契机获得后要打开新的故事空间，不是获得就结束
- 【类型多样】各种契机混合，不要千篇一律
- 【数量适中】宁可精不求多
输出严格JSON。"""


# 按体系类型提供不同的"契机类型"候选
FORTUNE_TYPES_BY_SYSTEM = {
    "realms": ["传承", "宝物", "奇遇", "贵人", "功法", "天材地宝", "秘境", "领悟", "血脉觉醒"],
    "skill_tiers": ["奇遇", "贵人", "契机", "突破灵感", "关键比赛", "秘密项目", "极限情境", "行业洞察"],
    "social_rank": ["大项目", "行业风口", "贵人相助", "关键客户", "重要饭局", "内部情报", "创业时机", "舆论事件"],
    "progression_arc": ["高考", "求职", "恋情", "婚姻", "至亲离世", "子女诞生", "重大疾病", "远行", "一本书", "一次顿悟"],
    "none": ["关键相遇", "命运时刻", "意外事件", "重要决定", "失去", "获得"],
}

# 保留旧名以防有其他地方引用
FORTUNE_TYPES = FORTUNE_TYPES_BY_SYSTEM["realms"]


def _fortune_meaning_for(sys_type: str) -> str:
    return {
        "realms": "修仙机缘——传承/宝物/奇遇/秘境等，推动主角境界突破",
        "skill_tiers": "技能契机——关键比赛/导师相遇/突破灵感，推动主角段位跃升",
        "social_rank": "职场机遇——大项目/贵人/关键客户/行业风口，推动主角地位晋升",
        "progression_arc": "生命契机——人生关键时刻，推动主角内心成熟和阶段跨越",
        "none": "命运时刻——改变主角轨迹的关键相遇/事件/决定",
    }.get(sys_type, "主角成长的关键节点")


def plan_all_fortunes(state: NovelState) -> None:
    """规划主角全书的机缘，写入 state.fortunes。按卷分批——每卷一次 LLM 调用，避免单次负担过重。"""
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"
    realm_plan = state.power_system.protagonist_realm_plan if state.power_system else {}

    for v in state.volumes:
        _plan_volume_fortunes(state, v, prot_name, realm_plan)

    print(f"  ✓ 机缘规划总计：{len(state.fortunes)} 个（按卷分批生成）")
    for v_idx in range(1, NUM_VOLUMES + 1):
        vf = [f for f in state.fortunes if f.volume == v_idx]
        if vf:
            names = " / ".join(f"[{f.fortune_type}]{f.name}" for f in vf)
            print(f"    第{v_idx}卷({len(vf)}个)：{names[:80]}")


def _plan_volume_fortunes(state: NovelState, vol, prot_name: str, realm_plan: dict) -> None:
    """为单个卷规划契机——一次 LLM 调用只专注这一卷。"""
    # 前一卷末"阶段" → 本卷末"阶段"（对所有体系类型通用）
    prev_realm = realm_plan.get(vol.index - 1, "起始")
    cur_realm = realm_plan.get(vol.index, "未定")

    # 根据体系类型决定契机名词
    sys_type = state.power_system.system_type if state.power_system else "realms"
    fortune_types = FORTUNE_TYPES_BY_SYSTEM.get(sys_type, FORTUNE_TYPES_BY_SYSTEM["realms"])
    fortune_term = {
        "realms": "机缘",
        "skill_tiers": "契机",
        "social_rank": "机遇",
        "progression_arc": "生命契机",
        "none": "命运时刻",
    }.get(sys_type, "契机")

    # 新增：流派 + 特殊机制上下文，帮 LLM 贴合流派设计契机
    flow_context = ""
    if state.power_system:
        ps = state.power_system
        flow_lines = []
        if ps.power_flow:
            flow_lines.append(f"流派：{ps.power_flow}")
        if ps.special_mechanics:
            mech_summary = " / ".join(f"{m.name}" for m in ps.special_mechanics[:4])
            flow_lines.append(f"本书特殊机制：{mech_summary}")
            flow_lines.append("——若契机与这些机制联动（例：签到系统触发奇遇、副本奖励、序列仪式），请写进 narrative_hook")
        if flow_lines:
            flow_context = "\n".join(flow_lines) + "\n"

    # 已设计的伏笔/爽点——机缘要与之协同
    existing = [f for f in state.fortunes if f.volume <= vol.index][-6:]
    existing_brief = "\n".join(
        f"  · {f.name}（第{f.volume}卷/{f.fortune_type}）：{f.description[:40]}"
        for f in existing
    ) or "（本卷之前无机缘）"

    # 本卷特殊能力觉醒阶段——机缘要与之呼应
    ability_awakenings = []
    if state.power_system:
        for ab in state.power_system.special_abilities:
            if not ab.is_protagonist_signature:
                continue
            for st in ab.awakening_stages:
                if st.target_volume == vol.index:
                    ability_awakenings.append(
                        f"  · {ab.name} 本卷觉醒 {st.stage_name}，触发：{st.triggering_event[:40]}"
                    )
    ability_brief = "\n".join(ability_awakenings) or "（本卷无特殊能力新觉醒）"

    # Phase 2.2:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    # 用户创作意图（preferred_sp_types_hints / avoid_tropes_hints / world_tone_hint）
    from utils.intent_helper import build_intent_brief
    intent_brief = build_intent_brief(state, "fortune_planner")
    prompt = f"""{feedback_prefix}{intent_brief}
为主角【{prot_name}】在第{vol.index}卷《{vol.title}》规划【{fortune_term}】——3-5 个即可。

【体系类型】{sys_type}
{flow_context}【本书"{fortune_term}"含义】{_fortune_meaning_for(sys_type)}

卷信息：
  章节范围：第{vol.chapter_start}-{vol.chapter_end}章
  主题：{vol.theme}
  卷弧线：{vol.arc[:120]}
  主角阶段推进：{prev_realm} → {cur_realm}

前面卷已有的主要{fortune_term}（参考，不要重复）：
{existing_brief}

本卷主角特殊能力觉醒计划（{fortune_term}应配合这些时机）：
{ability_brief}

═══ 要求 ═══
- 3-5 个{fortune_term}，配合主角阶段推进/能力觉醒的节点
- 类型多样（从以下择选）：{' / '.join(fortune_types)}
- 高价值{fortune_term}要有竞争者/障碍（这本身就是剧情）
- 有代价或考验——不是天上掉馅饼
- 引发后续剧情（获得后打开新的故事空间）
- 前期小而精，后期大而罕见——本卷是第 {vol.index} 卷（共 {NUM_VOLUMES} 卷）
- narrative_hook 必须说明它服务于主角弧线的哪个节点

输出 JSON：
{{
  "fortunes": [
    {{
      "fortune_id": "v{vol.index}_f1",
      "fortune_type": "（从上列候选中选）",
      "name": "（贴合题材的具体名称）",
      "description": "内容描述（40字）",
      "location_desc": "在哪里/什么场景（30字）",
      "acquisition_method": "如何获得（竞争/考验/偶然/代价，30字）",
      "prerequisite": "需要什么前提（实力/情节前置，20字）",
      "volume": {vol.index},
      "target_chapter": 章节编号,
      "effect_on_growth": "对主角成长的具体影响（30字）",
      "narrative_hook": "获得后引发什么新剧情（30字）"
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["fortunes", "items"],
        min_items=2, max_retries=3, temperature=0.75,
        agent_name=f"FortunePlanner[V{vol.index}]",
        empty_ok=True,
    )
    count_before = len([f for f in state.fortunes if f.volume == vol.index])
    for fd in (pick_list(data, "fortunes", "items") if data else []):
        fortune = Fortune(
            fortune_id=fd.get("fortune_id", f"v{vol.index}_f{len(state.fortunes)+1:03d}"),
            fortune_type=fd.get("fortune_type", "宝物"),
            name=fd.get("name", "未命名机缘"),
            description=fd.get("description", ""),
            location_desc=fd.get("location_desc", ""),
            acquisition_method=fd.get("acquisition_method", ""),
            prerequisite=fd.get("prerequisite", ""),
            volume=fd.get("volume", vol.index),
            target_chapter=fd.get("target_chapter", -1),
            effect_on_growth=fd.get("effect_on_growth", ""),
            narrative_hook=fd.get("narrative_hook", ""),
        )
        state.fortunes.append(fortune)
    count_after = len([f for f in state.fortunes if f.volume == vol.index])
    print(f"    第{vol.index}卷机缘：+{count_after - count_before} 个")


def get_fortunes_for_volume_brief(state: NovelState, volume_index: int) -> str:
    """获取指定卷的机缘概览（供写作参考）。"""
    vf = [f for f in state.fortunes if f.volume == volume_index]
    if not vf:
        return ""
    lines = []
    for f in vf:
        status = "✓已获得" if f.obtained else f"→第{f.target_chapter}章"
        lines.append(f"[{f.fortune_type}]{f.name}（{status}）：{f.acquisition_method[:25]}")
    return "\n".join(lines)


def mark_fortune_obtained(state: NovelState, fortune_id: str, chapter_index: int) -> None:
    f = state.get_fortune(fortune_id)
    if f:
        f.obtained = True
        f.actual_chapter = chapter_index
