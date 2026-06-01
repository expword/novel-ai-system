"""
intent_excerpt_extractor — 从用户原话里提取与某个具体生成步骤相关的段落。

# 何时被调用
当用户的 creative_intent.raw_description 超过 3000 字时，把完整原话塞进每个 phase
agent 的 prompt 既浪费 token 又稀释焦点。这时由 [utils/intent_helper.py] 调用本智能体，
按 agent_name 从原话里摘出与该 agent 相关的段落，只把相关切片塞进下游 prompt。

# 接口
extract_for_agent(raw_description, agent_name) -> str
  - 返回原文片段（保留用户原话不重写）
  - 原话里没明确相关内容时返回空字符串 ""
  - 调用方（intent_helper）会做 runtime cache，相同 (raw, agent) 不会重复跑

# 设计取舍
- 只摘原话片段，不总结、不重写——保留用户原话的语气和细节
- 已注册 agent 的"职责描述"维护在本文件 _AGENT_BRIEFS，新加 agent 时更新这里
- 输出软限制到 ~800 字，避免某些极长原话即使提取完仍把 prompt 撑爆
"""
from utils.json_utils import request_json


# 已注册 agent 的「职责简述」——告诉提取智能体该 agent 在干什么，
# 它才能判断从原话里摘哪些段落。新加 agent 走 intent_helper 时记得在这里加一条。
_AGENT_BRIEFS: dict[str, str] = {
    "character_designer":  "设计本书所有角色（主角/反派/主要配角/次要配角）的姓名、身份、性格、动机、关系网、致命弱点、成长弧光",
    "faction_architect":   "设计本书的势力/组织层级结构（每层 label、各层具体势力名、势力间对立与博弈、最大反派势力）",
    "world_builder":       "构建本书世界观设定（世界规则、力量/能力体系、地理、文化、氛围基调、特殊设定）",
    "satisfaction_system": "规划本书爽点（每卷 3-5 个爽点：升级/打脸/装逼/逆袭/智力碾压/技术降维 等具体场景）",
    "volume_planner":      "规划本书卷结构（每卷主题、对手、起承转合定位、章节范围、卷首钩子、卷尾钩子、关键事件）",
    "line_planner":        "规划本书叙事线（主线/感情线/悬疑线/人物线 在全书与每卷中的推进节奏）",
    "twist_designer":      "设计本书反转链（多层反转、信息差布局、伏笔与揭露节奏、最终翻盘）",
    "fortune_planner":     "规划主角机缘（每卷 3-5 个机缘：获得功法/物品/血脉/传承/师承/盟友 等）",
}


_SYSTEM = """你是"用户原意提取器"——从用户对小说的原始描述里，摘出与某个具体设计步骤相关的段落。

【铁律】
1. 只摘原文片段，不要总结、不要解读、不要重写——保留用户的措辞、语气、细节
2. 如果原话里有明确相关内容，按原话顺序摘出（不同段落可用 \\n 分隔）
3. 如果原话里没明确相关内容，excerpt 返回空字符串 ""——绝不硬凑、绝不替用户脑补
4. 摘出的内容应当让设计师能直接看懂"用户对这一步具体想要什么"
5. 输出严格 JSON
"""


def extract_for_agent(raw_description: str, agent_name: str) -> str:
    """从 raw_description 摘出与 agent_name 相关的原话段落。

    未注册的 agent_name 返回 ""（不浪费 LLM 调用）。
    提取失败时由调用方处理回退。
    """
    brief = _AGENT_BRIEFS.get(agent_name, "")
    if not brief:
        # agent 没注册职责简述——智能体不知道该摘什么，返回空让调用方走回退
        return ""

    user_prompt = f"""═══ 用户对本小说的完整描述（用户在「用一段自然语言描述你想写什么」表单里写的原话）═══
{raw_description}
═══════════════════════════════════════════════════════════

═══ 本次提取目标 ═══
设计步骤：{agent_name}
该步骤负责：{brief}

请从上面的用户原话里摘出与本步骤相关的段落（控制在 800 字以内）。
- 有相关内容就按原话顺序摘出
- 没相关内容就 excerpt 返回 ""

输出 JSON：
{{"excerpt": "原话片段或空字符串"}}
"""

    data = request_json(
        system=_SYSTEM,
        user=user_prompt,
        required_keys=["excerpt"],
        max_retries=2,
        temperature=0.1,    # 提取任务温度低，避免发散
        agent_name=f"IntentExcerpt[{agent_name}]",
        empty_ok=True,
    )

    return (data.get("excerpt") if data else "" or "").strip()


def list_registered_agents() -> list[str]:
    """便于其他模块查询哪些 agent 已注册职责描述。"""
    return list(_AGENT_BRIEFS.keys())
