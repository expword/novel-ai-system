"""
AbilityPlanner —— 写章前的能力使用规划 + 自审。

设计动机：之前 writer 写章时是否用主角能力、用什么能力、付什么代价，全靠 LLM 现场临场发挥；
然后章后 ability_auditor 才发现"用得太多""没付代价""能力越界"等问题——再去 polisher 修。
更好的做法：**写之前就规划好**——本章是否需要能力、用哪些、各自付什么代价、戏剧效果是什么。
然后**自审**：这个规划合不合理（节奏/代价相称/不滥用）。通过才交给 writer 落地。

输出 AbilityPlan 挂到 directive.ability_plan，writer 在 prompt 顶部读到"本章必须按此规划使用能力"。

不需要落 state——是章级临时计划，写完章节后该信息已固化在正文里。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.json_utils import request_json
from persistence.state import NovelState


@dataclass
class AbilityUseItem:
    ability_name: str               # 能力名（必须在 state.power_system.special_abilities 里）
    when_to_use: str = ""           # 章内何时使用（开头/冲突中/危机后/章末等）
    purpose: str = ""               # 用来解决什么具体问题（不能"为用而用"）
    cost_to_pay: str = ""           # 代价（消耗/反噬/时间冷却/精神负担/副作用）—— 必填
    drama_value: str = ""           # 这次使用如何强化戏剧性（铺垫/反转/反差）
    restraint_note: str = ""        # 为什么这次该用——不是滥用
    external_llm_profile: str = ""  # 该能力绑定的真 LLM profile id（writer 写时要用占位）


@dataclass
class AbilityPlan:
    should_use: bool = False        # 本章是否使用能力（False = 主角靠自己/智计/临场解决）
    reasoning: str = ""             # 决策理由（30-60 字）
    items: list[AbilityUseItem] = field(default_factory=list)
    # 自审结果
    review_score: int = 8           # 1-10，自审打分
    review_passed: bool = True
    review_issues: list[str] = field(default_factory=list)
    summary: str = ""               # 给 stdout 看的一句话总结


SYSTEM_PLANNER = """你是小说【能力使用规划师】。一本好书的金手指/特殊能力使用必须有节制——
读者讨厌"无脑开挂"和"为爽而爽"。你要在章节写作前先规划：本章主角是否使用能力、用哪些、付什么代价。

核心原则：
  1. 不是每章都该用能力——日常章 / 铺垫章 / 情感章 / 心理章 一般不该用
  2. 用就要"付代价"——消耗/反噬/冷却/精神负担/副作用之一，让能力有重量
  3. 优先让主角"先想到自己解决"，能力是补位，不是主导
  4. 同一能力短期内反复使用要降爽感——本章如何避免重复
  5. 该用就要用足戏剧性——不是"顺手解决问题"，而是"使用过程本身就是戏"

输出严格 JSON。"""

SYSTEM_REVIEWER = """你是【能力规划审核员】。审核作者刚拟的"本章能力使用计划"是否合理。

审核维度（1-10 打分，整数）：
  1. 必要性：本章真的非用能力不可吗（如果靠主角智计/对话/拼搏能解决，应该 should_use=false）
  2. 节制度：使用次数是否过度（一章 ≤ 1-2 次为宜，除非高潮章）
  3. 代价相称：每次使用都有具体代价吗（不能空挂"消耗精神"这种通用词）
  4. 重复性：跟近 5 章用过的能力是否雷同（要变换使用方式）
  5. 戏剧性：使用是不是"戏的高潮"，而不是"顺手开挂"

打分严苛——开挂/无代价/重复/凑场，直接 ≤5 分。
输出严格 JSON。"""


def _format_protagonist_abilities(state: NovelState) -> tuple[list[dict], str]:
    """提取主角能持有的能力清单 + 文本摘要。"""
    abilities = []
    if not state.power_system or not state.power_system.special_abilities:
        return [], ""
    proto_name = next((c.name for c in state.characters if c.role.value == "主角"), None)
    if not proto_name:
        return [], ""
    for ab in state.power_system.special_abilities:
        if ab.holder_name == proto_name or ab.is_protagonist_signature:
            stages_brief = " → ".join(
                f"V{s.target_volume}:{s.stage_name}({s.new_power[:25]})"
                for s in (ab.awakening_stages or [])
            )
            abilities.append({
                "name": ab.name,
                "description": ab.description,
                "source": ab.source,
                "stages": stages_brief,
                "external_llm_profile": ab.external_llm_profile or "",
            })
    if not abilities:
        return [], ""
    parts = []
    for a in abilities:
        line = f"  · 《{a['name']}》（{a['source']}）：{a['description']}\n"
        if a.get("external_llm_profile"):
            line += (f"    🔌 真 AI 接入：本能力绑了真 LLM（profile={a['external_llm_profile']}），"
                     f"主角问它问题时**用占位 [[ASK_AI:{a['name']}|具体问题]]**——\n"
                     f"    后处理会真发给 LLM 拿回答替换；不要自己编"
                     f"《{a['name']}》"
                     f"的回答。\n")
        line += f"    觉醒阶段：{a['stages']}"
        parts.append(line)
    text = "\n".join(parts)
    return abilities, text


def _recent_ability_uses(state: NovelState, current_chapter: int, n: int = 5) -> list[str]:
    """近 N 章用过的能力名（来自 ability_audits）——避免本章重复。"""
    used = []
    for ch in range(max(1, current_chapter - n), current_chapter):
        audit = state.ability_audits.get(ch)
        if not audit:
            continue
        for u in (audit.ability_uses or []):
            if u.ability_name:
                used.append(f"第{ch}章·{u.ability_name}（{(u.how_used or '')[:25]}）")
    return used[-10:]


def plan_chapter_abilities(state: NovelState, directive) -> AbilityPlan:
    """
    写章前的能力使用规划。返回 AbilityPlan（挂到 directive.ability_plan）。

    流程：
      0. 检测本章是否命中 lifecycle 节点（acquired/first_use/escalation/...）—— 命中则强制
         should_use=True，并把节点信息注入 prompt 让 LLM 必须安排该使用。
      1. Step A：根据章节类型 / 张力 / 主角能力 决定是否用 + 用哪些 + 代价
      2. Step B：自审这个规划（合理性 / 节制 / 代价相称 / 重复 / 戏剧性）
      3. 不通过 → 重生 1 次
    """
    # 主角无能力——直接返回不用
    abilities_data, abilities_text = _format_protagonist_abilities(state)
    if not abilities_data:
        return AbilityPlan(
            should_use=False,
            reasoning="主角无特殊能力——本章靠智计/对话/拼搏推进",
            summary="本章不涉及能力使用（主角无特殊能力）",
        )

    # 检测本章命中的 lifecycle 节点（强制使用——这一章必须把该 asset 的该节点落地）
    proto_name = next((c.name for c in state.characters if c.role.value == "主角"), None)
    forced_nodes = []
    try:
        from agents.ability_roadmap_planner import find_nodes_hitting_chapter
        forced_nodes = find_nodes_hitting_chapter(state, directive.chapter_index, holder_name=proto_name)
    except Exception as _e:
        # roadmap planner 缺失或异常不阻塞主流程
        pass

    ch_type = (getattr(directive, "chapter_type", "") or "").strip()
    tension = directive.tension.value if hasattr(directive, "tension") else ""
    structure_role = getattr(directive, "structure_role", "") or ""
    purpose = getattr(directive, "purpose", "") or ""
    primary_line = getattr(directive, "primary_line", "") or ""

    blueprint = getattr(directive, "blueprint", None)
    chapter_delta = getattr(blueprint, "chapter_delta", "") if blueprint else ""

    # 近 5 章用过的能力——避免重复
    recent_uses = _recent_ability_uses(state, directive.chapter_index, n=5)

    # 主角实力日志（章级）
    log = state.protagonist_power_log.get(directive.chapter_index - 1, {})
    cur_realm = log.get("realm", "")
    breakthrough = log.get("recent_breakthrough", "")

    # 构造强制节点段（lifecycle 命中本章 → must_use）
    forced_block = ""
    if forced_nodes:
        lines = ["═══ 【本章必须落地的金手指节点（lifecycle 规划，强制 should_use=true）】═══"]
        for fn in forced_nodes:
            llm_tag = f"（绑真 AI: {fn['external_llm_profile']} → 必须用 [[ASK_AI:{fn['asset_name']}|具体问题]] 占位）" if fn['external_llm_profile'] else ""
            lines.append(
                f"  · 《{fn['asset_name']}》（{fn['asset_kind']}）·节点类型 [{fn['node_type']}]\n"
                f"      作用：{fn['narrative_purpose']}\n"
                f"      前置：{fn['prerequisites']}{llm_tag}"
            )
        lines.append("注意：本章必须在 items 里至少包含一个使用该 asset 的条目；should_use 必须 true。")
        forced_block = "\n".join(lines) + "\n"

    user_prompt = f"""为第 {directive.chapter_index} 章规划主角的能力使用。
{forced_block}
═══ 本章背景 ═══
章节类型：{ch_type or '普通章'}
张力等级：{tension}
结构角色：{structure_role}
purpose：{purpose[:80]}
主推线：{primary_line[:60]}
本章变化：{chapter_delta[:80]}

═══ 主角当前实力 ═══
当前境界：{cur_realm or '(未明)'}
最近突破：{breakthrough or '(无)'}

═══ 主角持有能力 ═══
{abilities_text}

═══ 近 5 章已用过的能力（避免重复使用方式）═══
{chr(10).join('  · ' + u for u in recent_uses) if recent_uses else '  （前几章未用能力）'}

═══ 决策标准 ═══
看本章类型 / 张力 / 矛盾性质，先判断【本章是否需要用能力】：
  · 日常章 / 铺垫章 / 心理章 / 情感章——一般 should_use=false（让主角靠智计/对话/拼搏解决）
  · 战斗章 / 升级章 / 反转章 / 危机章——可以 should_use=true，但必须节制
  · 即便高潮章，能不开挂解决就不开挂——主角的"人"才是主角，能力只是工具
  · **但如果上面"必须落地的金手指节点"非空，本章必须 should_use=true 并落地该节点**——
    lifecycle 规划已锚定这一章是该 asset 的关键剧情点，不能跳过。

如果决定用，每次使用必须有具体代价（消耗/反噬/冷却/精神负担/副作用）+ 戏剧性。

输出 JSON：
{{
  "should_use": true|false,
  "reasoning": "为什么用/不用（30-60字，具体到本章场景）",
  "items": [   // should_use=false 时为空数组
    {{
      "ability_name": "（必须从主角持有列表里选）",
      "when_to_use": "章内何时（开头/冲突初/危机最深/反转点/章末）",
      "purpose": "用来解决什么（具体到本章某个困境）",
      "cost_to_pay": "代价（消耗XX/反噬XX/冷却N章/副作用XX）—— 必须具体",
      "drama_value": "这次使用如何制造戏剧性（铺垫够长/付出够大/反差够强）",
      "restraint_note": "为什么这次该用——不是滥用（与近期使用区分开）"
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM_PLANNER, user=user_prompt,
        required_keys=["should_use", "reasoning"],
        max_retries=2, temperature=0.6,
        agent_name="AbilityPlanner",
        empty_ok=True,
    )
    if not data:
        return AbilityPlan(
            should_use=False,
            reasoning="规划 LLM 失败——本章默认不用能力（保守）",
            summary="规划失败，默认不用能力",
        )

    plan = _parse_plan(data, abilities_data)

    # 兜底：lifecycle 命中节点必须出现在 items 里（即便 LLM 没写）
    if forced_nodes:
        plan = _ensure_forced_nodes_in_plan(plan, forced_nodes, abilities_data)

    # ── Step B：自审 ──
    review_data = _review_plan(state, directive, plan, abilities_text, recent_uses)
    plan.review_score = int(review_data.get("score", 8))
    plan.review_passed = plan.review_score >= 7
    plan.review_issues = list(review_data.get("issues", []) or [])

    # 如果自审不过，重生一次（带 issues 反馈）
    if not plan.review_passed:
        print(f"  ⚠ [AbilityPlan] 自审 {plan.review_score}/10——重新规划")
        for iss in plan.review_issues[:3]:
            print(f"      · {iss[:80]}")
        # 加 issues 到 prompt 重生
        retry_prompt = user_prompt + (
            "\n\n═══ 上次规划的问题（必须修正）═══\n"
            + "\n".join(f"  · {i}" for i in plan.review_issues[:5])
            + "\n请重新规划，避免上述问题。"
        )
        retry_data = request_json(
            system=SYSTEM_PLANNER, user=retry_prompt,
            required_keys=["should_use", "reasoning"],
            max_retries=2, temperature=0.55,
            agent_name="AbilityPlanner[重生]",
            empty_ok=True,
        )
        if retry_data:
            new_plan = _parse_plan(retry_data, abilities_data)
            # 重生分支同样兜底 forced_nodes
            if forced_nodes:
                new_plan = _ensure_forced_nodes_in_plan(new_plan, forced_nodes, abilities_data)
            new_review = _review_plan(state, directive, new_plan, abilities_text, recent_uses)
            new_plan.review_score = int(new_review.get("score", 8))
            new_plan.review_passed = new_plan.review_score >= 7
            new_plan.review_issues = list(new_review.get("issues", []) or [])
            if new_plan.review_score >= plan.review_score:
                plan = new_plan
                print(f"  ✓ [AbilityPlan] 重生后 {plan.review_score}/10")

    # 摘要文本
    if not plan.should_use:
        plan.summary = f"不用能力（{plan.reasoning[:35]}）"
    else:
        names = "/".join(it.ability_name for it in plan.items[:3])
        plan.summary = f"用 {names}（{len(plan.items)} 处，自审 {plan.review_score}/10）"
    return plan


def _ensure_forced_nodes_in_plan(plan: AbilityPlan, forced_nodes: list[dict],
                                   allowed_abilities: list[dict]) -> AbilityPlan:
    """lifecycle 命中节点必须出现在 plan.items 里——LLM 若漏，本函数兜底强行加。
    任何命中节点都把 should_use 翻成 True；否则 writer 拿不到能力 prompt。
    """
    name_to_meta = {a["name"]: a for a in allowed_abilities}
    existing = {it.ability_name for it in plan.items}
    for fn in forced_nodes:
        name = fn.get("asset_name")
        if not name or name not in name_to_meta:
            continue  # 不在主角持有列表（理论上不应该，find_nodes_hitting_chapter 已过滤）
        if name in existing:
            continue
        node_type = fn.get("node_type", "")
        cost_note = "按节点性质——acquired/first_use 一般无额外代价；escalation/locked/unlocked/sacrificed 必须有具体代价"
        plan.items.append(AbilityUseItem(
            ability_name=name,
            when_to_use=f"本章关键场景（lifecycle 节点 [{node_type}]）",
            purpose=(fn.get("narrative_purpose") or "")[:120],
            cost_to_pay=cost_note,
            drama_value=f"lifecycle [{node_type}] 锚定章——剧情高光",
            restraint_note="lifecycle 规划锚定本章必须落地，不可跳过",
            external_llm_profile=name_to_meta[name].get("external_llm_profile", "") or "",
        ))
        existing.add(name)
    if plan.items and not plan.should_use:
        plan.should_use = True
        plan.reasoning = (plan.reasoning or "") + "｜强制：lifecycle 节点必须本章落地"
    return plan


def _parse_plan(data: dict, allowed_abilities: list[dict]) -> AbilityPlan:
    """把 LLM 的 raw dict 解析成 AbilityPlan，过滤不存在的 ability_name。"""
    name_to_meta = {a["name"]: a for a in allowed_abilities}
    items = []
    for raw in (data.get("items", []) or []):
        if not isinstance(raw, dict):
            continue
        name = (raw.get("ability_name", "") or "").strip()
        if not name or name not in name_to_meta:
            continue  # 防 LLM 凭空发明能力
        items.append(AbilityUseItem(
            ability_name=name,
            when_to_use=raw.get("when_to_use", "") or "",
            purpose=raw.get("purpose", "") or "",
            cost_to_pay=raw.get("cost_to_pay", "") or "",
            drama_value=raw.get("drama_value", "") or "",
            restraint_note=raw.get("restraint_note", "") or "",
            external_llm_profile=name_to_meta[name].get("external_llm_profile", "") or "",
        ))
    should_use = bool(data.get("should_use"))
    if should_use and not items:
        # LLM 说要用但没填具体——降级为不用
        should_use = False
    return AbilityPlan(
        should_use=should_use,
        reasoning=data.get("reasoning", "") or "",
        items=items,
    )


def _review_plan(state: NovelState, directive, plan: AbilityPlan,
                 abilities_text: str, recent_uses: list[str]) -> dict:
    """自审：评估 plan 是否合理 + 列出问题。"""
    if not plan.should_use:
        # 不用能力的规划默认通过（除非 reasoning 太空）
        if not plan.reasoning.strip():
            return {"score": 5, "issues": ["should_use=false 但没说为什么——理由必填"]}
        return {"score": 9, "issues": []}

    items_dump = []
    for it in plan.items:
        items_dump.append(
            f"  · 《{it.ability_name}》：在 {it.when_to_use} 用——{it.purpose}\n"
            f"    代价：{it.cost_to_pay} | 戏剧性：{it.drama_value} | 节制：{it.restraint_note}"
        )

    user_prompt = f"""审核本章能力使用规划。

═══ 本章上下文 ═══
章节类型：{getattr(directive, 'chapter_type', '') or '普通章'}
张力：{directive.tension.value if hasattr(directive, 'tension') else ''}

═══ 主角持有能力 ═══
{abilities_text[:600]}

═══ 近 5 章用过的能力 ═══
{chr(10).join('  · ' + u for u in recent_uses) if recent_uses else '  （无）'}

═══ 当前规划 ═══
should_use: {plan.should_use}
reasoning: {plan.reasoning}
items（{len(plan.items)} 项）：
{chr(10).join(items_dump) if items_dump else '  （无）'}

═══ 审核任务 ═══
按 5 维度打分（必要性/节制/代价相称/重复性/戏剧性），每维度 1-10，取最低值作 score。
输出 JSON：
{{
  "score": 1-10 整数（取最低维度分）,
  "issues": ["具体问题 1", "具体问题 2", ...]（最多 5 条），
  "summary": "一句话总评（25 字内）"
}}
"""
    data = request_json(
        system=SYSTEM_REVIEWER, user=user_prompt,
        required_keys=["score"],
        max_retries=2, temperature=0.4,
        agent_name="AbilityPlanner[自审]",
        empty_ok=True,
    )
    return data or {"score": 8, "issues": []}


def format_ability_plan_brief(plan: AbilityPlan, max_chars: int = 500) -> str:
    """给 writer prompt 用的简报——告诉 writer 本章必须按此使用能力。"""
    if not plan.should_use:
        return (
            "【本章能力使用规划】\n"
            f"  · 决策：本章不使用主角能力——{plan.reasoning[:80]}\n"
            "  · 写作时让主角靠【智计/对话/拼搏/团队配合/临场判断】解决——不要让能力出场救场。"
        )
    lines = [
        "【本章能力使用规划——必须严格按此执行】",
        f"  · 决策：使用 {len(plan.items)} 处能力（{plan.reasoning[:60]}）",
    ]
    has_external = False
    for i, it in enumerate(plan.items, 1):
        line = (
            f"  {i}. 《{it.ability_name}》在 {it.when_to_use}\n"
            f"     用途：{it.purpose}\n"
            f"     代价（必须具体描写出来！）：{it.cost_to_pay}\n"
            f"     戏剧性要求：{it.drama_value}"
        )
        if it.external_llm_profile:
            has_external = True
            line += (
                f"\n     🔌 真 AI 接入：本能力绑了真 LLM。**主角向《{it.ability_name}》提问的内容**"
                f"必须用占位写——\n"
                f"        [[ASK_AI:{it.ability_name}|主角的具体问题文字]]\n"
                f"     占位之后正常写主角的反应即可——后处理会真把问题发给 LLM，把占位整体替换为真实回答。\n"
                f"     **绝对不要自己虚构《{it.ability_name}》的回答内容**——回答让真 LLM 给。"
            )
        lines.append(line)
    lines.append("  · 不在本规划里的能力本章一律不得出场；规划里的代价必须具体描写不能跳过。")
    if has_external:
        lines.append("  · ⚠ 占位格式严格匹配：双方括号、ASK_AI、能力名、|、问题——**任何字符多了少了都会失败**。")
    text = "\n".join(lines)
    return text[:max_chars]
