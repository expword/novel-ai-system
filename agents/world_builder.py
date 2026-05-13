"""
WorldBuilderAgent — 构建世界观文本（地理/历史/文化/经济/禁忌）+ 世界观完整性校验清单。
仅负责世界设定，人物由 CharacterDesignerAgent 负责。
"""
import json
from utils.json_utils import repair_json, safe_parse, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState
from config import NUM_VOLUMES
from agents.concept_pitch import format_world_context_brief


SYSTEM_BUILD = """你是多题材世界观构建师。按本书题材设计合适维度和深度的世界观。

【构建原则】
- 不同题材世界观的维度不同：
  · 幻想向（修真/玄幻/武侠/奇幻）：地理+历史+文化+经济+禁忌+世界秘密——全套
  · 都市/现代/职场：主要是行业生态+城市描写+社会氛围，可省略"种族/大陆"等幻想维度
  · 末世/科幻：灾变起源+生存规则+区域划分+势力格局
  · 校园/言情：学校/城市/家庭氛围即可，不必编大世界
  · 历史/古代：朝代背景+阶级+礼制+风俗
- 每个世界要有**符合题材的**秘密/谜团（幻想的远古/现代的行业黑幕/末世的灾变起源/言情的家族秘密）
- 【单主角】每个世界元素都要能回到主角身上，不要宏大空写
输出严格 JSON。"""

SYSTEM_CHECK = """你是世界观完整性审核员。
按题材的合理性检查——不要对都市文要求"种族/大陆"，也不要对修真文漏掉"修炼体系"。
输出结构化的审核报告 JSON。"""

CHECKLIST_ITEMS = [
    "地理：主要区域/重要地点是否清晰",
    "历史：重大事件/时间背景是否有交代",
    "社会：阶级结构/人群关系是否明确",
    "经济：流通货币/资源分配是否合理",
    "文化：习俗/信仰/语言氛围",
    "禁忌：世界层面的禁区/红线",
    "秘密：核心隐藏真相（读者最终会知道）",
    "力量体系/特殊机制与世界规则的自洽性",
]


def build_world(state: NovelState) -> None:
    """构建世界观并写入 state.world_setting。"""

    realm_brief = state.power_system_brief() if state.power_system else "待设计"
    factions_brief = "\n".join(
        f"- {f.name}（{f.faction_type}）" for f in state.factions[:6]
    ) if state.factions else "待设计"

    world_ctx = format_world_context_brief(state)
    prompt = f"""
请为《{state.title}》构建详细的世界观设定。

{world_ctx}

题材：{state.genre}
主题：{state.theme}
力量/体系：{realm_brief}
主要势力：
{factions_brief}
总卷数：{NUM_VOLUMES}

世界观需涵盖（每项50-100字）——按题材选择合适的描写方向：
- geography（地理：架空可写大陆/国家划分；都市写城市/街区/行业聚集地；末世写区域废墟；星际写星系/殖民地）
- history（历史：架空可写上古/近代；都市可用真实年份；末世从灾变 Day 0 起；言情可写主角家族往事）
- society（社会结构：阶级/人群关系——架空写修炼者与凡人/贵族与平民；都市写阶层/行业鄙视链；末世写幸存者派系；言情写家族/圈层）
- economy（经济：货币与资源流通——按题材选择灵石/银两/人民币/物资票/信用点等）
- culture（文化：架空写种族/习俗/信仰；现代写亚文化/价值观；末世写幸存者道德；星际写多元文明）
- taboos（禁忌：世界层面不可逾越之事——按题材自定，可以是天道红线、法律、行业潜规则、灾变后规矩等）
- world_secrets（世界最大的隐藏真相，1-2个，贯穿全书悬疑——架空可写上古秘辛；都市可写行业黑幕/家族秘密；末世可写灾变真相）

输出JSON：
{{
  "geography": "...",
  "history": "...",
  "society": "...",
  "economy": "...",
  "culture": "...",
  "taboos": "...",
  "world_secrets": ["秘密1", "秘密2"],
  "world_summary": "综合性世界观描述（200字，供写作直接参考）"
}}
"""
    example = (
        '{"geography":"...","history":"...","society":"...","economy":"...","culture":"...",'
        '"taboos":"...","world_secrets":["..."],"world_summary":"（200字综合性世界观）"}'
    )
    data = request_json(
        system=SYSTEM_BUILD, user=prompt,
        custom_validator=lambda d: (
            (True, "") if (isinstance(d, dict) and (d.get("world_summary") or d.get("summary")))
            else (False, "缺少 world_summary 字段（或 summary）")
        ),
        max_retries=5, temperature=0.72, agent_name="WorldBuilder",
        example_schema=example,
    )

    # 世界秘密作为重要伏笔素材存入facts
    for secret in data.get("world_secrets", []):
        state.memory.facts.append(f"[世界秘密-待揭露] {secret}")

    state.world_setting = data.get("world_summary", "") or data.get("summary", "") or ""
    # 附加详细信息
    detail_parts = []
    for key in ["geography", "history", "society", "economy", "culture", "taboos"]:
        if key in data:
            detail_parts.append(f"[{key}] {data[key]}")
    state.world_setting += "\n\n" + "\n".join(detail_parts)

    print(f"  ✓ 世界观构建完成（{len(state.world_setting)} 字）")


def run_world_checklist(state: NovelState) -> list[str]:
    """审核世界观完整性，返回需要补充的条目列表。"""

    prompt = f"""
请审核以下世界观设定的完整性：

世界设定：
{state.world_setting[:2000]}

力量体系：{state.power_system_brief()}
势力数量：{len(state.factions)}个

审核清单（每项判断是否已充分涵盖）：
{chr(10).join(f'- {item}' for item in CHECKLIST_ITEMS)}

输出JSON：
{{
  "checklist": [
    {{"item": "清单项", "status": "完整|不足|缺失", "issue": "问题描述（如有）"}}
  ],
  "critical_gaps": ["必须补充的关键缺失（若有）"],
  "supplements": ["补充建议（每条30字）"]
}}
"""
    data = request_json(
        system=SYSTEM_CHECK, user=prompt,
        max_retries=3, temperature=0.3, agent_name="WorldChecklist",
        empty_ok=True,
    )

    gaps = data.get("critical_gaps", [])
    if gaps:
        print(f"  ⚠ 世界观存在 {len(gaps)} 个关键缺失：")
        for g in gaps:
            print(f"    - {g}")
        # 将补充建议存入facts
        for s in data.get("supplements", []):
            state.memory.facts.append(f"[世界补充设定] {s}")
    else:
        print("  ✓ 世界观完整性校验通过")

    return gaps

