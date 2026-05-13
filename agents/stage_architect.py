"""
StageArchitectAgent — 为每卷设计叙事舞台（Narrative Stages）。

叙事舞台 = 【大情节】的承载容器，每卷2-5个，是章节发生的"环境框架"。
子场景（SubScene） = 【小情节】的承载容器，每个舞台2-4个。

分形起承转合：
- 每一个舞台（大情节）在所属卷的起承转合中承担"起/承/转/合"之一；
- 每一个舞台自身也是一次完整的起承转合，由其子场景承担；
- 每一个子场景（小情节）在所属舞台的起承转合中承担一个角色，自身又由章节承担起承转合。

设计原则：
- 舞台类型多样（宗门/秘境/竞技/市井/战场/旅途...）
- 每个舞台有独特的"规则"和"氛围"（影响写作风格）
- 子场景可以同时活跃（主角在两件事之间穿梭）
- 舞台与机缘挂钩（某些机缘只能在特定舞台获得）
- 舞台有自然的进入/退出方式（保证故事流动感）
- 所有设计都以【唯一主角】为中心——每个舞台/子场景都要明确"主角在这里要经历什么/变成什么"。
"""
from utils.json_utils import repair_json, pick_list, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, StoryStage, SubScene
from agents.fortune_planner import get_fortunes_for_volume_brief
from agents.concept_pitch import format_world_context_brief


SYSTEM = """你是小说叙事结构设计师，负责把一卷拆成几个有血有肉的大情节（叙事舞台），再把每个大情节切成几个小情节（子场景）。

每一个舞台是一个"大情节"：它在所属卷的起承转合中通常扮演一个角色（但不强求每个舞台都内部完整起承转合——该精悍的情节就让它精悍）。
每一个子场景是一个"小情节"：它服务于所属舞台，给舞台的走向注入具体的戏剧性。

【舞台类型按题材选择】不要给现代文塞"宗门"，不要给修真文写"职场会议"：
  · 修真/玄幻：宗门/秘境/大比/坊市/历练/战场/洞府
  · 武侠：江湖/客栈/比武/帮派内/镖局/山寨/京城
  · 都市/职场：办公室/会议室/客户公司/酒会/出差/商战谈判
  · 校园：教室/操场/社团/食堂/宿舍/比赛/社会实践
  · 末世：避难所/废墟探索/物资争夺/势力交锋/逃亡路
  · 星际：星舰内/星球登陆/殖民地/星港/太空战
  · 古代/宫廷：朝堂/后宫/宴会/边疆/府邸/驿馆
  · 言情：约会场所/工作场合/家庭聚会/旅行/重大场合
  · 系统流：任务副本/世界穿梭/系统空间
  · 克苏鲁：诡异事件现场/非凡协会/古迹遗址/调查小镇

好故事需要跌宕起伏，舞台和子场景可以设计反转——不是每个都要反转，而是关键节点要有：
  · 表面走向 A，实际通往 B（读者以为主角要这样，结果……）
  · 开局平静，暗流涌动；或开局惊险，发现虚惊一场，背后更大的陷阱
  · 多个小情节叠加，让读者的预期被层层打乱
别被"起承转合"的标签束缚——该反转就反转，该平静就平静，骨架是为故事服务的。

所有设计围绕【唯一主角】——每个舞台/子场景要能具体说清"主角在这里经历什么、变化什么、感受到什么"，不能是配角自成一体的支线。

输出严格 JSON。"""

# 通用舞台类型（题材中性的标签——LLM 在 prompt 里会按题材具体化命名）
STAGE_TYPES = [
    "势力内部", "探索/调查", "对抗/竞争", "市井/日常", "外出/旅程",
    "战斗/冲突", "幕后阴谋", "特殊事件", "被困/险境", "追逐/逃亡"
]




def design_volume_stages(state: NovelState, volume_index: int) -> None:
    """为指定卷设计叙事舞台，写入 state.story_stages。"""
    vol = state.get_volume(volume_index)
    if not vol:
        return

    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"

    # 本卷机缘列表
    fortune_brief = get_fortunes_for_volume_brief(state, volume_index)

    # 本卷活跃势力
    active_factions = [
        f for f in state.factions
        if not f.is_hidden or f.reveal_volume <= volume_index
        and f.volume_role.get(volume_index)
    ]
    faction_brief = "\n".join(
        f"  [{f.tier_name()}] {f.name}：{f.volume_role.get(volume_index, '')[:30]}"
        for f in active_factions[:5]
    )

    # 上一卷结尾状态
    prev_end = ""
    if state.story_thread.scene_end_state:
        prev_end = f"接续上卷末尾状态：{state.story_thread.scene_end_state[:80]}"

    # 卷级分形上下文
    vol_role_tag = f"[{vol.structure_role}]" if vol.structure_role else ""
    vol_purpose = vol.purpose or vol.theme
    vol_expression = vol.expression or ""

    world_ctx = format_world_context_brief(state)

    # 主角金手指/能力的具体名字——禁止 LLM 用 "AI/系统/算法" 泛词
    abilities_block = ""
    if state.power_system and state.power_system.special_abilities:
        proto_signs = [
            ab for ab in state.power_system.special_abilities
            if ab.holder_name == prot_name or ab.is_protagonist_signature
        ]
        if proto_signs:
            names_only = [ab.name for ab in proto_signs]
            abs_lines = "\n".join(
                f"  · 《{ab.name}》（{ab.source}）：{ab.description}"
                for ab in proto_signs
            )
            abilities_block = (
                "【主角金手指——命名硬约束】\n"
                f"{abs_lines}\n\n"
                f"⚠️ 严禁在 stage 描述里用 'AI / 系统 / 算法 / 数据 / 工具 / 引擎' 等泛通用词指代主角能力——"
                f"必须用具体名字（{' / '.join(names_only)}）。错 ✗ 'AI 计算' / 对 ✓ '《{names_only[0]}》计算'。"
            )

    prompt = f"""
为第{volume_index}卷《{vol.title}》{vol_role_tag}设计叙事舞台（大情节）和子场景（小情节）。

{world_ctx}

{abilities_block}

═══ 本卷上下文（所处分形层级） ═══
本卷在整本书起承转合中的角色：{vol.structure_role or '待定'}
本卷的 purpose（为什么要有这一卷）：{vol_purpose}
本卷想表达的：{vol_expression}
卷主题：{vol.theme}
卷内弧线（说明了本卷自身的起-承-转-合）：{vol.arc[:150]}
章节范围：第{vol.chapter_start}-{vol.chapter_end}章（共{vol.total_chapters}章）
卷首钩子：{vol.opening_hook}
卷尾钩子：{vol.closing_hook}

主角{prot_name}的起始状态：
{prev_end or "全书开篇"}

本卷活跃势力：
{faction_brief or "无特定势力"}

本卷机缘：
{fortune_brief or "无预定机缘"}

═══ 设计要求 ═══
1. 设计 3-5 个叙事舞台，让它们集体承担本卷的起承转合。分工你根据故事定：
   · 3 个舞台可以让一个同时承担两段（比如"转+合"）
   · 5 个舞台可以让两个分担同一段（比如两个推进的"承"）
   · 不强求每个舞台自己内部都完整起承转合——短促有力的情节段也很好
2. 每个舞台 2-4 个子场景，让子场景把舞台的大情节切成可感知的节奏。
3. 戏剧性设计：关键舞台/子场景可以有反转（表面 A 实际 B），但别硬塞——该反转就反转，该平铺就平铺。反转的位置可以写在 purpose 里或 description 里。
4. 舞台类型要多样，不能全是同类；舞台可以重叠（主角同时在两个舞台穿梭）。
5. 机缘分配到对应舞台/子场景；舞台切换要有自然的进入/退出理由。
6. 每个舞台/子场景的 purpose 和 expression 都要具体可感——说"让主角第一次对这个世界产生怀疑"胜过"情感深化"。

【开篇节奏铁律——本卷如果是第 1 卷必须遵守】
  · 第 1 卷的【第 1 个舞台】必须是【铺垫型】，structure_role 必填"起"，**不能是冲突高潮也不能是大反转**
  · 这个铺垫舞台要写的是：主角"当前的处境/状态/日常困境/未爆发的渴望"——让读者代入主角是谁、他被什么压迫、他想要什么
  · 章节占比要够：第 1 个舞台至少占本卷前 1/4-1/5 的篇幅（不能 5 章就匆匆过完直接跳到大事件）
  · 第 1 个舞台的 atmosphere 应该是"压抑的日常 / 暗流涌动的平静 / 看似平凡的不安"，而不是"惊天动地的开局"
  · 关键事件（inciting incident）应该出现在【第 1 个舞台的尾声 → 第 2 个舞台的开头】之间，让读者在熟悉了主角处境之后才被剧情拽走
  · 反派 / 危机 / 大反转 / 觉醒能力 / 越级冲突 等"高强度"事件最早从第 2 个舞台才能登场（除非主角的 trauma 就是开篇被动卷入）

舞台类型按题材自由命名（修真用"宗门/秘境/大比"，都市用"办公室/客户公司/酒会"，末世用"避难所/物资争夺地"，校园用"教室/操场/社团"，言情用"约会场所/家庭聚会"……）。
通用类型仅供参考：{' / '.join(STAGE_TYPES)}

输出JSON：
{{
  "stages": [
    {{
      "stage_id": "s{volume_index}_1",
      "name": "舞台名称（按题材具体化，如'青云宗试炼月'/'X 集团第三季冲刺会议'/'第七避难所内争'）",
      "stage_type": "舞台类型（题材合适即可）",
      "chapter_start": 章节起始,
      "chapter_end": 章节结束,
      "structure_role": "起|承|转|合（本舞台在本卷起承转合中的角色）",
      "purpose": "为什么要安排这个大情节，对主角意味着什么（40字）",
      "expression": "想让读者从这段故事感受到的（30字）",
      "setting_desc": "环境描述（60字，供写作参考）",
      "atmosphere": "整体氛围（20字，如'暗流涌动的表面平静'）",
      "protagonist_role": "主角在此的身份/处境（30字）",
      "key_activities": ["活动1", "活动2", "活动3"],
      "fortune_ids": ["关联的机缘ID"],
      "transition_in": "如何进入此舞台（20字）",
      "transition_out": "如何退出（20字）",
      "parallel_stage_ids": ["同期并行的其他舞台ID，可为空"],
      "sub_scenes": [
        {{
          "sub_id": "sub_{volume_index}_1_1",
          "name": "子场景名",
          "sub_type": "题材合适的子场景类型（修真:修炼/历练/比斗 ／ 都市:谈判/会议/相亲 ／ 末世:搜索/防御/物资抢夺 ／ 校园:考试/比赛/社团活动 ……）",
          "structure_role": "起|承|转|合（本小情节在所属大情节起承转合中的角色）",
          "purpose": "为什么要有这个小情节，对主角意味着什么（30字）",
          "expression": "想让读者感受到什么（25字）",
          "description": "子场景描述（40字）",
          "chapter_start": 起始章,
          "chapter_end": 结束章,
          "key_events": ["关键事件1", "事件2"],
          "fortune_ids": ["在此可获得的机缘ID，可为空"]
        }}
      ]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["stages", "story_stages", "items"],
        min_items=2,
        max_retries=4, temperature=0.72, agent_name=f"StageArchitect[V{volume_index}]",
        empty_ok=True,
    )
    stages_list = pick_list(data, "stages", "story_stages", "items") if data else []
    if not stages_list:
        print(f"  ⚠ 第{volume_index}卷舞台设计跳过（LLM 重试失败），后续章节将在无舞台上下文下规划")
        return

    count = 0
    for sd in stages_list:
        sub_scenes = []
        for ss in sd.get("sub_scenes", []):
            sub_scenes.append(SubScene(
                sub_id=ss.get("sub_id", f"sub_{volume_index}_{count}_{len(sub_scenes)}"),
                name=ss.get("name", "子场景"),
                sub_type=ss.get("sub_type", "推进"),
                description=ss.get("description", ""),
                chapter_start=ss.get("chapter_start", sd.get("chapter_start", vol.chapter_start)),
                chapter_end=ss.get("chapter_end", sd.get("chapter_end", vol.chapter_end)),
                key_events=ss.get("key_events", []),
                fortune_ids=ss.get("fortune_ids", []),
                structure_role=ss.get("structure_role", ""),
                purpose=ss.get("purpose", ""),
                expression=ss.get("expression", ""),
            ))
        stage = StoryStage(
            stage_id=sd.get("stage_id", f"s{volume_index}_{count+1}"),
            name=sd.get("name", f"第{volume_index}卷舞台{count+1}"),
            stage_type=sd.get("stage_type", "旅途/外出历练"),
            volume=volume_index,
            chapter_start=max(sd.get("chapter_start", vol.chapter_start), vol.chapter_start),
            chapter_end=min(sd.get("chapter_end", vol.chapter_end), vol.chapter_end),
            setting_desc=sd.get("setting_desc", ""),
            atmosphere=sd.get("atmosphere", ""),
            protagonist_role=sd.get("protagonist_role", ""),
            key_activities=sd.get("key_activities", []),
            sub_scenes=sub_scenes,
            fortune_ids=sd.get("fortune_ids", []),
            transition_in=sd.get("transition_in", ""),
            transition_out=sd.get("transition_out", ""),
            parallel_stage_ids=sd.get("parallel_stage_ids", []),
            structure_role=sd.get("structure_role", ""),
            purpose=sd.get("purpose", ""),
            expression=sd.get("expression", ""),
        )
        state.story_stages.append(stage)
        count += 1

    print(f"  ✓ 第{volume_index}卷叙事舞台：{count} 个")
    for s in state.story_stages[-count:]:
        sub_names = " / ".join(
            f"{ss.name}[{ss.structure_role}]" if ss.structure_role else ss.name
            for ss in s.sub_scenes
        )
        role_tag = f"[{s.structure_role}]" if s.structure_role else ""
        print(f"    [{s.stage_type}] {s.name}{role_tag} [{s.chapter_start}-{s.chapter_end}章]"
              f" 子场景：{sub_names[:80] or '无'}")
        if s.purpose:
            print(f"       为何：{s.purpose[:50]}")
