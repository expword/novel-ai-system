"""
AbilityRoadmapPlanner —— 金手指/能力/物品/技能/法宝的"生命周期路线图"规划。

设计动机：
  · 现有 design_special_abilities (Phase 2C) 只对 realms/skill_tiers 类型小说生效，
    其他类型（progression_arc/social/life…）整本书 special_abilities 为空 —— writer 自然
    不会写 asset 的获取/使用 —— setup_reviewer 在每章都报 critical。
  · 即便 2C 跑了，它产出的只是"能力 + 觉醒阶段"，缺乏"铺垫/获得/首用/解锁/升级/牺牲"
    这种**情节级生命周期锚点**。chapter_planner 没东西可读 → 章节 must_include 不带 asset。

本 agent 做三件事（任何 system_type 都跑）：
  1. 让 LLM 按 genre+creative_intent 决定要规划多少个 SpecialAsset（可 0/1/N，类型可 ability/
     item/skill/treasure/system）；每个 asset 的基础信息 + external_llm_profile（豆包类
     真 AI 接入用）。
  2. 对每个 asset，让 LLM 设计开放型 lifecycle_nodes 序列 —— 不固定模板，让 LLM 按情节
     自由组合：可"获得即用"，可"获得→隔几卷→解锁→首用"，可"获得→升级×N→牺牲"。
  3. 戏剧性强的节点 (is_dramatic=True) 反向生成 SatisfactionPoint 挂钩爽点系统；
     涉及主角心理转折的节点回标到 character_arcs.transitions[i].ability_trigger。

公开 API：
  plan_asset_roadmap(state)          —— 主入口，写回 state.power_system.special_abilities
  materialize_sps_from_lifecycle(state) —— 反向产 SP，写回 state.satisfaction_points
  tag_arc_transitions_with_abilities(state) —— 标 ArcTransition.ability_trigger
"""
from __future__ import annotations
from typing import Optional

from utils.json_utils import request_json
from persistence.state import (
    NovelState,
    SpecialAbility,
    LifecycleNode,
    SatisfactionPoint,
    SatisfactionType,
    SatisfactionSetup,
)


SYSTEM_ASSETS = """你是【金手指/能力体系架构师】。你的任务是看一本书的题材与作者意图后，
决定这本书需要多少个"特殊资产"（asset：金手指/能力/物品/技能/法宝/系统等），以及每个的基本设定。

核心原则：
  1. **数量按题材+意图灵活定**：可能 0 个（纯写实/言情/职场剧），可能 1 个（典型都市重生/穿越带系统），
     可能 N 个（仙侠/玄幻/克苏鲁，常 3-6 个，由主角/伙伴/对手分持）。
  2. **作者意图里出现的具体名词必须沿用**：如果作者写"主角带豆包穿越"，asset 名就叫"豆包"，
     不要包装成"智能助手/超级 AI/信息引擎"之类的泛词。
  3. **不强行加 asset**：如果题材不需要（如纯爱/职场/写实），就返回空数组。
  4. **kind 字段区分类型**：ability(能力/血脉/天赋) / item(物品) / skill(技艺) / treasure(法宝/灵物) / system(系统/外挂)。
  5. **真 AI 接入用 external_llm_profile**：当 asset 是"会回答问题的智能体"（豆包/系统/AI 助手），
     用 user_models.json 里的 profile id 绑住，让 writer 用 [[ASK_AI:名|问题]] 占位真发问询。
     普通能力/物品留空。

输出严格 JSON，不要废话。"""


SYSTEM_LIFECYCLE = """你是【金手指节奏设计师】。给你一个 asset 的基本信息和这本书的卷布局，
你为它设计**生命周期节点序列** —— 这个 asset 从被引入到（可能）退场的完整剧情链。

**节点完全开放**，不要套固定模板。可能的 node_type 举例（你也可以发明新的）：
  · setup           —— 铺垫线索（让 asset 的存在/获得有伏笔）
  · acquired        —— 主角真正获得（穿越带来/拾获/赠予/解封/觉醒）
  · first_use       —— 第一次实战使用
  · locked          —— 获得后被封印/限制无法用
  · unlocked        —— 限制解除（条件达成/敌方破解/觉醒）
  · constraint_lifted —— 使用条件放宽（代价变小/冷却变短）
  · escalation      —— 升级/进阶/新功能解锁
  · sacrificed      —— 牺牲/失去/耗尽（反噬/换条件/转赠）

**模式自由组合**，按戏剧需要：
  · 即用型：acquired 和 first_use 同一节点（或紧邻同章）
  · 延迟型：acquired (V1) → first_use (V1 末或 V2 初)
  · 锁住型：acquired (V1) → locked → unlocked (V3) → first_use (V3)
  · 条件型：acquired (V1) → constraint_lifted (V2) → first_use (V2)
  · 渐进型：acquired → escalation × N
  · 反噬型：acquired → first_use → sacrificed (V4 末)

每个节点必填：
  · node_type：节点类型（上面或自创）
  · target_volume：在第几卷
  · target_chapter：粗粒度填 0（由 chapter_planner 临近时细化到章）；如果该节点必须在卷的
    特定位置才符合节奏，可以填具体章号（如卷的第 4 章）
  · prerequisites：发生的前置情节条件（40 字内）—— 这一节点为什么"现在"发生
  · narrative_purpose：在故事里的作用（30 字内）—— chapter_planner 会把它写进章 must_include
  · is_dramatic：是否戏剧性强 —— True 会反向生成爽点，挂钩 SatisfactionPoint 系统。
    一般 acquired / first_use / unlocked / escalation 戏剧性强；setup / locked 多为静态铺垫不算。

输出严格 JSON，不要废话。"""


# ═══════════════════════════════════════════════════════
#  Step 1：决定 asset 清单
# ═══════════════════════════════════════════════════════

def _design_asset_list(state: NovelState) -> list[dict]:
    """让 LLM 按 genre+intent 决定本书的 asset 清单（数量+基本设定）。"""
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    allies = [c for c in state.characters if c.role.value == "主要配角"]
    enemies = [c for c in state.characters if c.role.value == "反派"]

    prot_name = protagonist.name if protagonist else "（主角未定）"
    allies_brief = "、".join(c.name for c in allies[:6]) or "（无）"
    enemies_brief = "、".join(c.name for c in enemies[:6]) or "（无）"

    ci = state.creative_intent
    intent_raw = (ci.raw_description if ci else "")[:800]

    # 列出 user_models.json 里可用的 LLM profile id 给 LLM 看
    profiles_hint = _available_llm_profiles_hint()

    user_msg = f"""为《{state.title}》（题材：{state.genre or '未填'}｜主题：{state.theme or '未填'}）规划特殊资产（asset）清单。

【作者创作意图原话】
{intent_raw or '（未填——按题材判断即可）'}

【主角】{prot_name}
【主要配角】{allies_brief}
【反派】{enemies_brief}

【可绑定的真 AI profile（user_models.json）】
{profiles_hint}

═══ 任务 ═══
按题材+创意意图，决定本书需要多少个 asset：
  · 可以 0 个（纯写实/职场/言情等不需要）
  · 可以 1 个（典型都市重生带系统/AI/外挂）
  · 可以 N 个（仙侠/玄幻多能力多法宝，主角/配角/反派分持）

如果作者意图里点名了具体 asset（如"带豆包穿越"），**asset 名必须用作者原话的词**——
不要包装、不要改名。description 也沿用作者原话用词。

如果题材是会"回答问题的智能体"（AI 助手/系统/数据库），**必须**设置 external_llm_profile
绑一个 user_models 里的 id；这样 writer 写到主角问它问题时会用 [[ASK_AI:名|问题]] 占位
真发问询，不靠 writer 凭空编。

输出严格 JSON：
{{
  "assets": [
    {{
      "name": "如：豆包 / 真元剑 / 万象镜 / 都市重生系统",
      "entry_kind": "ability|item|skill|treasure|system",
      "source": "穿越随身|师承|拾获|血脉觉醒|系统绑定|...",
      "description": "整体描述（50字内）",
      "unlock_condition": "首次获得/解锁的条件（30字内，'获得即可用'也行）",
      "usage_rule": "什么时候允许使用；必须写触发条件/场景，不许写随时可用",
      "effect_scope": "能做到什么；写清对象、范围、强度",
      "hard_limits": "明确不能做到什么；至少 2 条硬边界",
      "cost_rule": "每次或关键使用的代价/冷却/风险；不能写无代价",
      "holder_role": "主角自身|伙伴|对手|中立|隐藏",
      "holder_name": "持有者具体名字（主角填 '{prot_name}'）",
      "is_protagonist_signature": true/false,
      "external_llm_profile": "user_models 里的 profile id；普通能力留空",
      "plot_integration": "如何自然首次出场/融入剧情（40字内）",
      "narrative_hook": "获得后引发什么后续剧情（30字内）"
    }}
  ]
}}

如果本书完全不需要 asset，输出 {{"assets": []}}。
"""
    example = (
        '{"assets":[{"name":"豆包","entry_kind":"ability","source":"穿越随身",'
        '"description":"主角穿越时随身带的 AI 助手","unlock_condition":"获得即可用",'
        '"usage_rule":"主角主动提问且问题属于现代通用知识时可用",'
        '"effect_scope":"提供原理、公式、流程、图纸思路和策略建议",'
        '"hard_limits":"不能变出物品；不能预知古代具体人事；不能判断本地律法真伪",'
        '"cost_rule":"消耗注意力和时间，复杂问题会导致头痛疲惫",'
        '"holder_role":"主角自身","holder_name":"' + prot_name + '","is_protagonist_signature":true,'
        '"external_llm_profile":"doubao","plot_integration":"穿越苏醒后发现脑内有 AI","narrative_hook":"借豆包反推商业/历史"}]}'
    )
    data = request_json(
        system=SYSTEM_ASSETS, user=user_msg,
        list_candidates=["assets", "abilities", "items"],
        min_items=0, item_required_keys=["name", "entry_kind"],
        max_retries=3, temperature=0.7,
        agent_name="AbilityRoadmapPlanner.assets",
        example_schema=example,
        empty_ok=True,
    )
    if not data:
        return []
    raw = data.get("assets") or data.get("abilities") or data.get("items") or []
    return raw if isinstance(raw, list) else []


def _available_llm_profiles_hint() -> str:
    """列出 user_models.json 里所有 profile 的 id + display_name，给 LLM 选哪个绑。"""
    try:
        from llm_layer import user_models as um
        items = um.list_all() or []
    except Exception:
        return "（user_models 未加载，留 external_llm_profile 为空即可）"
    if not items:
        return "（用户未配 user_models，留 external_llm_profile 为空）"
    lines = []
    for it in items[:15]:
        pid = it.get("id") or ""
        dn = it.get("display_name") or it.get("name") or ""
        usage = it.get("usage") or ""
        if isinstance(usage, list):
            usage = ",".join(usage)
        if pid:
            lines.append(f"  · {pid}（{dn}{'｜用途:'+usage if usage else ''}）")
    return "\n".join(lines) or "（无可用 profile）"


# ═══════════════════════════════════════════════════════
#  Step 2：为单个 asset 设计 lifecycle 节点序列
# ═══════════════════════════════════════════════════════

def _design_lifecycle(state: NovelState, asset_meta: dict,
                       other_assets: list[dict]) -> list[dict]:
    """给定一个 asset 的基本信息，让 LLM 设计 lifecycle_nodes 序列。"""
    total_vols = len(state.volumes) if state.volumes else 6
    vol_themes = []
    for v in (state.volumes or []):
        title = getattr(v, "title", "") or ""
        theme = getattr(v, "theme", "") or getattr(v, "central_conflict", "") or ""
        vol_themes.append(f"  V{v.index}：{title}｜{theme[:50]}")
    vol_block = "\n".join(vol_themes) or f"  （共 {total_vols} 卷，卷主题未规划）"

    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_arc = ""
    if protagonist:
        prot_arc = (
            f"主角 {protagonist.name}：性格 {protagonist.personality[:30]}｜"
            f"创伤 {(protagonist.trauma or '')[:30]}｜"
            f"整体弧 {(protagonist.arc or '')[:50]}"
        )

    others_brief = "、".join(a.get("name", "") for a in other_assets if a.get("name") != asset_meta.get("name"))

    user_msg = f"""为下面这个 asset 设计【生命周期节点序列】。

【本书卷布局】（共 {total_vols} 卷）
{vol_block}

【主角弧】{prot_arc or '（未规划）'}

【本 asset】
  · 名字：{asset_meta.get('name', '')}
  · 类型：{asset_meta.get('entry_kind', 'ability')}
  · 来源：{asset_meta.get('source', '')}
  · 描述：{asset_meta.get('description', '')}
  · 解锁：{asset_meta.get('unlock_condition', '')}
  · 持有：{asset_meta.get('holder_role', '')}（{asset_meta.get('holder_name', '')}）
  · 真 AI 绑定：{asset_meta.get('external_llm_profile') or '（无）'}

【其他已规划 asset】{others_brief or '（无）'}（避免节奏撞车）

═══ 任务 ═══
为它设计 lifecycle_nodes 序列。**节点数量和类型完全由你按情节决定**：
  · 简单的 asset 可能只需要 1 个节点（如 acquired+first_use 合一）；
  · 复杂的核心金手指可能需要 5+ 个节点（铺垫 → 获得 → 锁住 → 解锁 → 首用 → 升级 ×N → 牺牲）。

**重点**：节点间要有戏剧落差。"获得即一直顺利用"是最无聊的；"获得→暂时不能用→解锁时机戏剧性强"
最有张力。把"获得"和"首用"分开放在合适的情节位置（哪怕只是隔几章），让获得有期待感、首用有爆发感。

每个节点严格输出：
  · node_type：节点类型（setup/acquired/first_use/locked/unlocked/constraint_lifted/escalation/sacrificed 或你新造）
  · target_volume：第几卷
  · target_chapter：粗时填 0（由 chapter_planner 临近时细化）；如果节奏要求必须在卷里特定位置，填具体章号
  · prerequisites：发生的前置情节条件（40字内）
  · narrative_purpose：在故事里的作用（30字内，chapter_planner 会写进章 must_include）
  · is_dramatic：true/false。true 会反向生成爽点 —— 一般 acquired/first_use/unlocked/escalation 是 true，
    setup/locked 这种静态铺垫一般 false。

输出严格 JSON：
{{
  "lifecycle_nodes": [
    {{"node_type":"...","target_volume":1,"target_chapter":0,
      "prerequisites":"...","narrative_purpose":"...","is_dramatic":true}},
    ...
  ]
}}
"""
    example = (
        '{"lifecycle_nodes":[{"node_type":"acquired","target_volume":1,"target_chapter":1,'
        '"prerequisites":"主角穿越苏醒","narrative_purpose":"主角发现脑内豆包随之同来","is_dramatic":true},'
        '{"node_type":"first_use","target_volume":1,"target_chapter":0,'
        '"prerequisites":"债主上门走投无路","narrative_purpose":"主角第一次向豆包问商业策略","is_dramatic":true}]}'
    )
    data = request_json(
        system=SYSTEM_LIFECYCLE, user=user_msg,
        list_candidates=["lifecycle_nodes", "nodes", "lifecycle"],
        min_items=1, item_required_keys=["node_type", "target_volume"],
        max_retries=3, temperature=0.75,
        agent_name=f"AbilityRoadmapPlanner.lifecycle[{asset_meta.get('name','?')}]",
        example_schema=example,
        empty_ok=True,
    )
    if not data:
        return _fallback_lifecycle_nodes(asset_meta)
    nodes = data.get("lifecycle_nodes") or data.get("nodes") or data.get("lifecycle") or []
    return nodes if isinstance(nodes, list) else _fallback_lifecycle_nodes(asset_meta)


def _fallback_lifecycle_nodes(asset_meta: dict) -> list[dict]:
    """LLM 失败时的保守 lifecycle。

    核心原则：获得、理解、第一次实用分开，避免第一章就把金手指写成万能问答机。
    """
    name = (asset_meta.get("name") or "asset").strip()
    is_external_ai = bool((asset_meta.get("external_llm_profile") or "").strip())
    if is_external_ai:
        return [
            {
                "node_type": "setup",
                "target_volume": 1,
                "target_chapter": 1,
                "prerequisites": "主角苏醒后察觉意识中有异样回声",
                "narrative_purpose": f"铺垫《{name}》存在但不解决问题",
                "is_dramatic": False,
            },
            {
                "node_type": "acquired",
                "target_volume": 1,
                "target_chapter": 2,
                "prerequisites": "主角独处并尝试确认脑内工具边界",
                "narrative_purpose": f"发现《{name}》只能给现代原则",
                "is_dramatic": True,
            },
            {
                "node_type": "first_use",
                "target_volume": 1,
                "target_chapter": 6,
                "prerequisites": "主角已亲手整理出足够本地账册材料",
                "narrative_purpose": f"首次用《{name}》查询现代会计原则",
                "is_dramatic": True,
            },
            {
                "node_type": "constraint_lifted",
                "target_volume": 1,
                "target_chapter": 18,
                "prerequisites": "主角录入一批本地债务与物价信息",
                "narrative_purpose": f"《{name}》可辅助整理本地数据",
                "is_dramatic": True,
            },
        ]
    return [
        {
            "node_type": "acquired",
            "target_volume": 1,
            "target_chapter": 0,
            "prerequisites": "主角进入第一卷核心困境",
            "narrative_purpose": f"获得《{name}》但先建立限制",
            "is_dramatic": True,
        },
        {
            "node_type": "first_use",
            "target_volume": 1,
            "target_chapter": 0,
            "prerequisites": "主角付出代价并找到合适场景",
            "narrative_purpose": f"首次有代价地使用《{name}》",
            "is_dramatic": True,
        },
    ]


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════

def plan_asset_roadmap(state: NovelState, force: bool = False) -> list[SpecialAbility]:
    """规划本书的 SpecialAsset 清单及每个的 lifecycle_nodes，写回 state.power_system.special_abilities。

    幂等：如果 state.power_system.special_abilities 里已有 asset 且都有非空 lifecycle_nodes，
    返回现有列表不重做；用 force=True 强制重跑。
    """
    if not state.power_system:
        print("  ⚠ power_system 未初始化，跳过 ability_roadmap_planner")
        return []
    existing = state.power_system.special_abilities or []
    if not force and existing and all(getattr(a, "lifecycle_nodes", None) for a in existing):
        print(f"  ✓ 已有 {len(existing)} 个 asset 且 lifecycle 完整，跳过（force=True 可重跑）")
        return existing

    # Step 1: asset 清单
    print("  ── AbilityRoadmapPlanner Step 1：决定 asset 清单 ──")
    asset_dicts = _design_asset_list(state)
    if not asset_dicts:
        if existing:
            print("  ⚠ asset 清单生成为空——保留已有 special_abilities，并补齐缺失 lifecycle")
            final_assets = []
            for asset in existing:
                if not getattr(asset, "lifecycle_nodes", None):
                    fallback_meta = {
                        "name": asset.name,
                        "external_llm_profile": asset.external_llm_profile,
                    }
                    asset.lifecycle_nodes = [
                        LifecycleNode(
                            node_type=str(nd.get("node_type") or "acquired"),
                            target_volume=int(nd.get("target_volume", 1) or 1),
                            target_chapter=int(nd.get("target_chapter", 0) or 0),
                            prerequisites=str(nd.get("prerequisites") or "")[:200],
                            narrative_purpose=str(nd.get("narrative_purpose") or "")[:200],
                            is_dramatic=bool(nd.get("is_dramatic", False)),
                        )
                        for nd in _fallback_lifecycle_nodes(fallback_meta)
                    ]
                final_assets.append(asset)
            state.power_system.special_abilities = final_assets
            return final_assets
        print("  ✓ LLM 判定本书无需特殊 asset（题材使然）")
        state.power_system.special_abilities = []
        return []
    print(f"  ✓ 规划出 {len(asset_dicts)} 个 asset：" +
          "、".join(f"《{a.get('name','?')}》({a.get('entry_kind','?')})" for a in asset_dicts))

    # 角色名白名单
    valid_holders = {c.name for c in state.characters}

    # Step 2: 为每个 asset 设计 lifecycle
    print(f"\n  ── AbilityRoadmapPlanner Step 2：为每个 asset 设计 lifecycle ──")
    final_assets: list[SpecialAbility] = []
    for ad in asset_dicts:
        name = (ad.get("name") or "").strip()
        if not name:
            continue
        # holder_name 白名单兜底
        holder = (ad.get("holder_name") or "").strip()
        if holder and valid_holders and holder not in valid_holders:
            holder = next((c.name for c in state.characters if c.role.value == "主角"), holder)

        nodes_data = _design_lifecycle(state, ad, asset_dicts)
        lifecycle: list[LifecycleNode] = []
        for nd in nodes_data:
            try:
                lifecycle.append(LifecycleNode(
                    node_type=str(nd.get("node_type") or "").strip() or "acquired",
                    target_volume=int(nd.get("target_volume", 1) or 1),
                    target_chapter=int(nd.get("target_chapter", 0) or 0),
                    prerequisites=str(nd.get("prerequisites") or "")[:200],
                    narrative_purpose=str(nd.get("narrative_purpose") or "")[:200],
                    is_dramatic=bool(nd.get("is_dramatic", False)),
                ))
            except (ValueError, TypeError) as e:
                print(f"    ⚠ 节点解析失败（跳过）：{e}")

        asset = SpecialAbility(
            name=name,
            source=str(ad.get("source") or "")[:50],
            description=str(ad.get("description") or "")[:200],
            unlock_condition=str(ad.get("unlock_condition") or "")[:100],
            usage_rule=str(ad.get("usage_rule") or "")[:200],
            effect_scope=str(ad.get("effect_scope") or "")[:200],
            hard_limits=str(ad.get("hard_limits") or "")[:240],
            cost_rule=str(ad.get("cost_rule") or "")[:200],
            holder_role=str(ad.get("holder_role") or "主角自身"),
            holder_name=holder,
            is_protagonist_signature=bool(ad.get("is_protagonist_signature", False)),
            entry_kind=str(ad.get("entry_kind") or "ability"),
            lifecycle_nodes=lifecycle,
            plot_integration=str(ad.get("plot_integration") or "")[:120],
            narrative_hook=str(ad.get("narrative_hook") or "")[:100],
            external_llm_profile=str(ad.get("external_llm_profile") or "").strip(),
        )
        final_assets.append(asset)
        nodes_brief = " → ".join(f"V{n.target_volume}{'C'+str(n.target_chapter) if n.target_chapter else ''}:{n.node_type}"
                                  for n in lifecycle)
        print(f"  ✓ 《{name}》（{asset.entry_kind}）{len(lifecycle)} 节点：{nodes_brief}")

    state.power_system.special_abilities = final_assets
    return final_assets


# ═══════════════════════════════════════════════════════
#  反向生成 SP
# ═══════════════════════════════════════════════════════

def materialize_sps_from_lifecycle(state: NovelState) -> list[SatisfactionPoint]:
    """遍历所有 asset 的 dramatic 节点 → 每个生成一条 SatisfactionPoint，linked_sp_id 双向回绑。
    幂等：如果节点已有 linked_sp_id 且对应 SP 仍存在，跳过。返回新增的 SP 列表。
    """
    if not state.power_system or not state.power_system.special_abilities:
        return []
    existing_sp_ids = {sp.sp_id for sp in (state.satisfaction_points or [])}
    new_sps: list[SatisfactionPoint] = []
    for asset in state.power_system.special_abilities:
        for idx, node in enumerate(asset.lifecycle_nodes or []):
            if not node.is_dramatic:
                continue
            if node.linked_sp_id and node.linked_sp_id in existing_sp_ids:
                continue
            # 生成 sp_id：sp_asset_<安全名>_<index>
            safe_name = "".join(ch if ch.isalnum() else "_" for ch in asset.name)[:20]
            sp_id = f"sp_asset_{safe_name}_{idx}"
            # 避免重名
            n = 1
            base = sp_id
            while sp_id in existing_sp_ids:
                n += 1
                sp_id = f"{base}_{n}"
            title = f"《{asset.name}》·{node.node_type}"
            description = node.narrative_purpose or node.prerequisites or f"{asset.name} 的 {node.node_type} 节点"
            sp = SatisfactionPoint(
                sp_id=sp_id,
                sp_type=SatisfactionType.ASSET_LIFECYCLE,
                title=title,
                description=description[:200],
                intensity=7 if node.node_type in ("acquired", "first_use", "unlocked", "escalation") else 5,
                volume=node.target_volume,
                target_chapter=node.target_chapter if node.target_chapter > 0 else (node.target_volume - 1) * 10 + 5,
                setup_chain=[SatisfactionSetup(
                    chapter=max(1, (node.target_chapter or (node.target_volume - 1) * 10 + 5) - 2),
                    content=node.prerequisites or "前置情节铺垫",
                )] if node.prerequisites else [],
                payoff_description=node.narrative_purpose or "",
                triggered=False,
            )
            new_sps.append(sp)
            existing_sp_ids.add(sp_id)
            node.linked_sp_id = sp_id  # 反向回绑

    if new_sps:
        if state.satisfaction_points is None:
            state.satisfaction_points = []
        state.satisfaction_points.extend(new_sps)
        print(f"  ✓ 从 lifecycle 反向生成 {len(new_sps)} 条爽点（SatisfactionType.ASSET_LIFECYCLE）")
    return new_sps


# ═══════════════════════════════════════════════════════
#  标 ArcTransition.ability_trigger
# ═══════════════════════════════════════════════════════

def tag_arc_transitions_with_abilities(state: NovelState) -> int:
    """对每个 CharacterArc.transitions[i]，如果在同一卷有匹配 holder 的 asset lifecycle 节点
    （acquired / unlocked / sacrificed 这种心理冲击大的），标 ability_trigger=asset_name。
    返回标记的 transition 数量。
    """
    if not getattr(state, "character_arcs", None) or not state.power_system:
        return 0
    arcs = state.character_arcs or []
    if not arcs:
        return 0
    impactful_node_types = {"acquired", "unlocked", "sacrificed", "first_use", "escalation"}
    tagged = 0
    for arc in arcs:
        for trans in (arc.transitions or []):
            if trans.ability_trigger:
                continue
            # 找在同一卷、持有者 == arc 的人物，且类型有冲击的 lifecycle 节点
            for asset in state.power_system.special_abilities or []:
                if asset.holder_name != arc.character_name:
                    continue
                for node in asset.lifecycle_nodes or []:
                    if node.target_volume != trans.volume:
                        continue
                    if node.node_type not in impactful_node_types:
                        continue
                    trans.ability_trigger = asset.name
                    tagged += 1
                    break
                if trans.ability_trigger:
                    break
    if tagged:
        print(f"  ✓ 给 {tagged} 个 ArcTransition 标了 ability_trigger")
    return tagged


# ═══════════════════════════════════════════════════════
#  卷级落章：把粗粒度 lifecycle 节点细化到具体章号
# ═══════════════════════════════════════════════════════

# 节点类型 → 卷内相对位置范围（按戏剧节奏经验放置；LLM 给了具体章号则不会进这里）
_NODE_POSITION_RATIO = {
    "setup":             (0.0,  0.10),
    "acquired":          (0.0,  0.15),
    "first_use":         (0.15, 0.30),
    "constraint_lifted": (0.30, 0.50),
    "escalation":        (0.40, 0.70),
    "locked":            (0.50, 0.75),
    "unlocked":          (0.75, 0.90),
    "sacrificed":        (0.90, 1.00),
}
_DEFAULT_RATIO = (0.30, 0.70)


def assign_chapter_to_lifecycle_nodes(state: NovelState, volume_index: int,
                                       written_chapters: Optional[set] = None) -> int:
    """把本卷所有 target_chapter=0 的 lifecycle 节点按 node_type 启发式分到具体章。

    · 同步更新对应 SP 的 target_chapter（lifecycle 与 SP 章号保持一致）
    · 已写章不会被分到（避免分到无法改的章）
    · 多个节点冲突时按 node_type 卷内位置先后顺序错开

    返回成功分配的节点数。
    """
    if not state.power_system or not state.power_system.special_abilities:
        return 0
    vol = state.get_volume(volume_index)
    if not vol:
        return 0
    written = set(written_chapters or [])
    cs, ce = vol.chapter_start, vol.chapter_end
    length = ce - cs + 1
    if length <= 0:
        return 0

    # 收集本卷所有 target_chapter == 0 的待落章节点
    pending = []
    for asset in state.power_system.special_abilities:
        for node in asset.lifecycle_nodes or []:
            if node.target_volume != volume_index:
                continue
            if node.target_chapter and node.target_chapter > 0:
                continue
            pending.append((asset, node))
    if not pending:
        return 0

    # 按 node_type 卷内中心位置排序（早→晚分配，确保 acquired 先于 first_use 等）
    def _center(node_type: str) -> float:
        lo, hi = _NODE_POSITION_RATIO.get(node_type, _DEFAULT_RATIO)
        return (lo + hi) / 2
    pending.sort(key=lambda x: _center(x[1].node_type))

    assigned_chs: set = set()
    success = 0
    for asset, node in pending:
        lo, hi = _NODE_POSITION_RATIO.get(node.node_type, _DEFAULT_RATIO)
        ch_lo = cs + int(length * lo)
        ch_hi = min(ce, cs + max(1, int(length * hi)) - 1)
        if ch_hi < ch_lo:
            ch_hi = ch_lo
        candidate = None
        # 第一轮：在偏好区间内找未占用未已写
        for ch in range(ch_lo, ch_hi + 1):
            if ch in assigned_chs or ch in written:
                continue
            candidate = ch
            break
        # 第二轮：扩展到全卷找
        if candidate is None:
            for ch in range(cs, ce + 1):
                if ch in assigned_chs or ch in written:
                    continue
                candidate = ch
                break
        if candidate is None:
            # 本卷已写满，把节点强制落到卷末（即便已写，触发"已写章但缺该节点"由 setup_reviewer 报）
            candidate = ce
        node.target_chapter = candidate
        assigned_chs.add(candidate)
        success += 1
        if node.linked_sp_id:
            sp = next((s for s in (state.satisfaction_points or [])
                       if s.sp_id == node.linked_sp_id), None)
            if sp:
                sp.target_chapter = candidate
        print(f"  📌 《{asset.name}》·{node.node_type} 落章 V{volume_index}C{candidate}"
              + ("（强制·该章已写）" if candidate in written else ""))
    return success


# ═══════════════════════════════════════════════════════
#  按章查询命中的 lifecycle 节点（供 chapter_planner / ability_planner / setup_reviewer 用）
# ═══════════════════════════════════════════════════════

def find_nodes_hitting_chapter(state: NovelState, chapter_index: int,
                                 holder_name: Optional[str] = None) -> list[dict]:
    """返回本章命中的 lifecycle 节点列表。可选 holder_name 过滤（如只看主角的）。

    每项 dict:
      {asset_name, asset_kind, node_type, narrative_purpose, prerequisites,
       holder_name, external_llm_profile, is_dramatic, linked_sp_id}
    """
    out = []
    if not state.power_system or not state.power_system.special_abilities:
        return out
    for asset in state.power_system.special_abilities:
        if holder_name and asset.holder_name != holder_name and not asset.is_protagonist_signature:
            continue
        for node in asset.lifecycle_nodes or []:
            if node.target_chapter == chapter_index:
                out.append({
                    "asset_name": asset.name,
                    "asset_kind": asset.entry_kind,
                    "node_type": node.node_type,
                    "narrative_purpose": node.narrative_purpose,
                    "prerequisites": node.prerequisites,
                    "holder_name": asset.holder_name,
                    "external_llm_profile": asset.external_llm_profile,
                    "is_dramatic": node.is_dramatic,
                    "linked_sp_id": node.linked_sp_id,
                })
    return out


# ═══════════════════════════════════════════════════════
#  Phase 2C2 入口（被 scheduler_tasks / director 调）
# ═══════════════════════════════════════════════════════

def run_phase_2c2(state: NovelState, force: bool = False) -> None:
    """Phase 2C2 全流程：规划 asset roadmap → 反向产 SP → 标 character_arcs。"""
    plan_asset_roadmap(state, force=force)
    materialize_sps_from_lifecycle(state)
    tag_arc_transitions_with_abilities(state)
