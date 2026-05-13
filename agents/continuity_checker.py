"""
ContinuityCheckerAgent — Phase 5：连续性校验。

Writer 写完后、Critic 之前跑一次。检查三类连续性问题：
1. 硬事实：时间/地点/人物在场/物品持有/境界数值 是否与前文/directive 一致
2. 设定一致性：是否违反世界观规则、力量体系规则、势力规则
3. 因果链：本章结果是否由前文原因导致（不能凭空出现解决方案）

产出 issues 列表；如果有 critical 问题，会被 director 拿去触发 revise。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, ChapterDirective


SYSTEM = """你是小说连续性审核员。你的工作不是评文学，是抓漏洞。
专门抓三种漏洞——用本书题材合适的语言判断：
1. 硬事实漏洞：时间错（"昨天还在A城，今天就到了千里外B城"）；物品凭空消失/出现；
   人物身份/级别突然跃级（修真:境界跃级 ／ 都市:职位空降 ／ 校园:成绩飞跃 ／ 末世:异能等级跳跃）；
   死人复活；伤势突然痊愈；记忆/技能凭空获得
2. 设定漏洞：违反世界观规则——按本书题材判断（修真:违反功法限制 ／ 都市:违反公司流程或法律 ／
   科幻:违反技术原理 ／ 末世:违反生存规则 ／ 言情:与人物背景矛盾）
3. 因果漏洞：主角突然会一个没铺垫过的招/技能/知识；敌人突然知道了主角隐瞒的事；
   解决方案凭空出现（deus ex machina）；情感转变缺乏铺垫

【重要】不要把题材当成修真——本书可能是都市、校园、末世、星际、言情……判断时按 prompt 提供的世界观语境。
找到漏洞就列出；没漏洞就明说"无问题"。不要评价写得好不好——那是 Critic 的事。
输出严格 JSON。"""


def check_continuity(state: NovelState, directive: ChapterDirective, content: str) -> dict:
    """对章节做连续性校验，返回 issues + severity。"""
    # 构造硬事实参照：前一章状态快照 + directive 的 character_states + 世界规则摘要
    state_block = _build_state_reference(state, directive)
    world_rules_block = _build_world_rules_reference(state)
    # 正文（截取开头+结尾，避免太长）
    content_sample = content[:1500] + ("\n\n[...]\n\n" + content[-1500:] if len(content) > 3500 else "")

    prompt = f"""连续性校验：第 {directive.chapter_index} 章。

【角色此刻状态（directive 中的硬事实，本章不得违反）】
{state_block}

【世界规则摘要】
{world_rules_block}

【本章正文节选】
{content_sample}

═══ 审查要求 ═══
逐一检查三类漏洞（按本书题材判断，"境界/职位/级别/学历"这些字段名按本书实际叫法理解）：
1. 硬事实漏洞：章内描写是否与 directive 给出的角色状态/级别/物品/位置/情绪相冲突？时间推进是否合理？
2. 设定漏洞：是否违反世界规则、能力体系、势力规则、社会逻辑？
3. 因果漏洞：本章关键事件、主角突破/突破口/转变、敌人/对手行动，是否由前文合理推出？有无凭空出现的解决方案？

输出 JSON：
{{
  "has_issues": true 或 false,
  "severity": "none" | "minor" | "major" | "critical",
  "issues": [
    {{
      "type": "硬事实" | "设定" | "因果",
      "severity": "minor|major|critical",
      "description": "问题描述（50字，具体到哪一段）",
      "suggested_fix": "建议修改方向（40字）"
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["has_issues"],
        max_retries=3, temperature=0.3,
        agent_name=f"ContinuityChecker[Ch{directive.chapter_index}]",
        empty_ok=True,
    )
    if not data:
        return {"has_issues": False, "severity": "none", "issues": []}
    return data


def _build_state_reference(state: NovelState, directive: ChapterDirective) -> str:
    """构造 directive 里给 writer 的角色状态摘要，用于与正文对照。"""
    if not directive.character_states:
        return "（无预设状态）"
    lines = []
    for name, st in list(directive.character_states.items())[:8]:
        parts = [name]
        for k in ("realm", "location", "emotion", "injury"):
            if st.get(k):
                parts.append(f"{k}={st[k]}")
        if st.get("items"):
            parts.append(f"items={st['items']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _build_world_rules_reference(state: NovelState) -> str:
    lines = []
    if state.power_system:
        lines.append(f"能力/层级体系：{state.power_system.system_name}")
        if getattr(state.power_system, "power_flow", ""):
            lines.append(f"流派：{state.power_system.power_flow}")
        # 主角当前卷的目标级别（境界/职位/学历/异能等级，因题材而异）
        realm_plan = state.power_system.protagonist_realm_plan
        cur_realm = realm_plan.get(directive_vol := directive_get_volume(state), "")
        if cur_realm:
            lines.append(f"主角本卷目标级别：{cur_realm}")
        # 越级/越阶规则（任取一条代表）
        for r in state.power_system.realms:
            if r.overleap_rule:
                lines.append(f"越级/越阶规则（以{r.name}为例）：{r.overleap_rule[:50]}")
                break
        # 流派特殊机制
        for sm in getattr(state.power_system, "special_mechanics", [])[:2]:
            lines.append(f"特殊机制·{sm.name}：{sm.description[:50]}")
    # 世界规则
    rules = [f for f in state.memory.facts if f.startswith("[世界规则]")][:4]
    if rules:
        lines.extend(rules)
    return "\n".join(lines) if lines else "（无额外规则）"


def directive_get_volume(state: NovelState) -> int:
    return state.current_volume_index
