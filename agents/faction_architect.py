"""
FactionArchitectAgent — 自适应的势力/组织体系设计。

按题材不同，"势力"长什么样差别巨大：
  · 修真/玄幻：宗门/帝国/圣地/幕后
  · 武侠：门派/帮会/朝廷/江湖
  · 都市/职场：公司/部门/行业协会/政府机构
  · 商战：集团/行业联盟/竞争对手/政商关系
  · 末世/废土：避难所/军团/掠夺者/神秘组织
  · 星际/科幻：星际联邦/公司/海盗/古文明遗族
  · 校园：学校/社团/学生会/家长圈
  · 宫斗/古代：后宫势力/朝堂党派/外戚/世家
  · 推理/悬疑：警方/罪案组织/私家侦探所/媒体
  · 异能/超能：基金会/觉醒者协会/反叛组织/政府特殊部门
  · 克苏鲁：教团/基金会/非凡组织/古神信徒
  · 游戏异界：公会/王国/商会/玩家组织
  · 纯情感：可能就是朋友圈/家庭/同事圈（甚至势力=无）

这个 agent 根据 power_flow / genre 判断势力结构的"颗粒度"和"类型"，不硬套五层修真体系。
"""
import json
from utils.json_utils import repair_json, request_json, pick_list
from llm_layer.llm import system_user
from persistence.state import NovelState, Faction, FactionRelation, FactionInfiltration
from config import (
    NUM_VOLUMES,
    FACTION_TIERS_MIN, FACTION_TIERS_MAX,
    FACTIONS_PER_TIER_MIN, FACTIONS_PER_TIER_MAX,
    NEUTRAL_FACTIONS, HIDDEN_FACTIONS,
    KEY_MEMBERS_PER_FACTION_MIN, KEY_MEMBERS_PER_FACTION_MAX,
    FACTION_RELATIONS_MIN, FACTION_RELATIONS_MAX,
    FACTION_INTERNAL_CONFLICTS_MIN, FACTION_INTERNAL_CONFLICTS_MAX,
)
from agents.concept_pitch import format_world_context_brief


SYSTEM = """你是小说"势力/组织"架构师——根据题材设计主角会遭遇的各种团体。

【核心原则】
1. 势力形态完全看题材：
   · 修真文的"势力"=宗门/圣地；都市文的"势力"=公司/政府/行业协会；校园文=学校/社团/家长圈；
     末世=避难所/军团；言情=朋友圈/家庭；宫斗=后宫派系/朝堂党派。
   · 不要给都市职场文写"宗门/圣地"，不要给言情文写"大陆势力"。
2. 分层不是修真专属——所有题材都有"层级感"，但名字要贴题材：
   · 修真：底层帮派→地方宗门→国家势力→大陆圣地→幕后
   · 都市职场：小公司→中型企业→行业巨头→跨国集团→资本家族
   · 末世：散兵游勇→城市据点→军阀→区域政权→神秘组织
   · 言情：家庭→朋友圈→社会圈→职业圈（如果故事需要延展）
   · 宫斗：妃嫔→派系→外戚→朝堂→帝王
3. 【单主角铁律】每个势力必须对主角有意义（敌/友/资源/跳板/威胁/归属）。严禁纯背景势力。
4. 同层势力有矛盾（主角可借势打势）；高层对低层有渗透；势力消亡有权力真空。
5. 势力揭露/崩塌时机配合卷的起承转合角色。

输出严格 JSON。"""


# ── 分两步构建：先设计层级框架，再填充每个势力细节 ──

def design_factions(state: NovelState) -> None:
    """分两步设计完整分层势力体系，写入 state.factions。"""

    realm_brief = state.power_system_brief() if state.power_system else "未知体系"
    volumes_brief = "\n".join(
        f"第{v.index}卷《{v.title}》主题：{v.theme}"
        for v in state.volumes
    )
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_name = protagonist.name if protagonist else "主角"

    # Step 1: 设计层级框架和势力清单
    tier_map = _design_tier_framework(state, realm_brief, volumes_brief, prot_name)

    if not tier_map:
        print("  ✗ FactionArchitect: tier_map 为空，势力体系未生成（state.factions 保持为空）")
        print("    建议排查：上方 [FactionArchitect/骨架] 的校验失败日志")
        return

    # Step 2: 分批填充每个势力的详细设定
    # 兼容LLM可能返回的不同key名（factions/faction_list等）
    for tier in tier_map:
        if "factions" not in tier:
            for key in ("faction_list", "势力", "items", "list"):
                if key in tier:
                    tier["factions"] = tier[key]
                    break
            else:
                tier["factions"] = []

    # 逐层报告 Step B 成果
    print("  FactionArchitect: Step A/B 后各层势力清单：")
    empty_tiers = []
    for tier in tier_map:
        cnt = len([f for f in tier.get("factions", []) if isinstance(f, dict) and f.get("name")])
        tag = "⚠ 空" if cnt == 0 else f"{cnt} 个"
        print(f"    · 第{tier.get('tier','?')}层 [{tier.get('tier_label','?')}]：{tag}")
        if cnt == 0:
            empty_tiers.append(tier.get("tier", "?"))
    if empty_tiers:
        print(f"  ⚠ 以下层未生成任何势力：{empty_tiers}——排查上方 [FactionArchitect/第{empty_tiers[0]}层] 日志")

    all_faction_names = [f["name"] for tier in tier_map for f in tier.get("factions", []) if "name" in f]
    if not all_faction_names:
        print("  ✗ FactionArchitect: Step A 骨架就绪但所有层的 factions 填充全部失败——state.factions 保持为空")
        return

    _fill_faction_details(state, tier_map, all_faction_names, realm_brief, prot_name)

    if not state.factions:
        print(f"  ⚠ FactionArchitect: 已汇集 {len(all_faction_names)} 个势力名，但 Step 2 细节填充后 state.factions 仍为空")
        print(f"    可能原因：所有批次的细节 JSON 都不合规——排查上方 [FactionArchitect/细节批次N] 日志")

    # 生成供写作用的势力概述（按层级）
    state.world_factions_desc = _build_factions_desc(state.factions)

    print(f"  ✓ 分层势力体系：{len(state.factions)} 个势力")
    tier_labels = {1: "底层", 2: "地方", 3: "国家", 4: "大陆", 5: "幕后"}
    for tier_num in range(1, 6):
        tier_factions = [f for f in state.factions if f.tier == tier_num]
        if tier_factions:
            names = " / ".join(f.name + ("[中立]" if f.is_neutral else "") + ("[隐]" if f.is_hidden else "") for f in tier_factions)
            print(f"    [{tier_labels.get(tier_num, tier_num)}层] {names}")


def _design_tier_framework(
    state: NovelState, realm_brief: str, volumes_brief: str, prot_name: str
) -> list[dict]:
    """
    Step 1：设计势力层级框架。

    两阶段：
      A. 一次 LLM：只要"层级骨架"——tier_label + tier_conflicts（每层一两句）
      B. 并发 N 次 LLM：每层单独生成该层的 4-6 个势力（名字+类型+power_level+brief）
    这样避免一次要 LLM 吐 30 个势力导致 JSON 截断。
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS

    world_ctx = format_world_context_brief(state)
    genre_hints = (
        "- 修真/玄幻：底层小帮派 → 地方宗门 → 国家宗门 → 大陆圣地 → 幕后古神\n"
        "- 武侠：江湖小帮会 → 地方门派 → 五大门派 → 朝廷/皇室 → 魔教/外族\n"
        "- 都市/职场：小团队/小公司 → 中型企业 → 行业巨头 → 资本家族 → 政商背景\n"
        "- 末世/废土：散居者 → 城市据点 → 军阀联盟 → 区域政权 → 神秘组织\n"
        "- 宫斗/古代：妃嫔圈子 → 派系党羽 → 外戚世家 → 朝堂重臣 → 幕后大黑手\n"
        "- 校园：小圈子 → 社团班级 → 学生会/校方 → 家长圈 → 背景家族\n"
        "- 克苏鲁：凡人团体 → 非凡协会 → 教团/基金会 → 组织领袖 → 古神"
    )

    # Phase 2.1:thread-local user_feedback 注入(带反馈重生成走这条路径)
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()

    # ── A. 层级骨架（只要 tier labels，不要 factions）────────────
    skeleton_prompt = f"""{feedback_prefix}
为《{state.title}》设计势力/组织的【层级骨架】——只给每层的 label 和内部矛盾，**不要填具体势力**。

{world_ctx}

世界观：{state.world_setting[:200]}
力量体系：{realm_brief}
各卷主题：
{volumes_brief}

═══ 要求 ═══
按本书题材设计 {FACTION_TIERS_MIN}-{FACTION_TIERS_MAX} 层。不要硬套"宗门/圣地"。

参考（按题材）：
{genre_hints}

输出 JSON（注意：factions 留空数组，Step B 会填）：
{{
  "tiers": [
    {{
      "tier": 1,
      "tier_label": "（按题材命名，一句话如'{prot_name} 初涉的校园社团层' 或 '底层修真帮派'）",
      "tier_conflicts": "本层内部主要矛盾（30字）",
      "tier_hint": "本层势力类型的风格参考（给 Step B 做填充提示，20字，如'小宗门/独立山头/野修团体'）"
    }}
  ]
}}
"""
    skeleton_data = request_json(
        system=SYSTEM, user=skeleton_prompt,
        required_keys=["tiers"],
        list_candidates=["tiers", "items"],
        min_items=max(2, FACTION_TIERS_MIN - 1),   # 宽松点，少一层也接受
        max_retries=4, temperature=0.7,
        agent_name="FactionArchitect/骨架",
        empty_ok=True,
    )
    tiers = pick_list(skeleton_data, "tiers", "items") if skeleton_data else []
    if not tiers:
        print("  ✗ FactionArchitect/骨架 生成失败——tier 列表为空")
        print("    可能原因：LLM 返回无 'tiers' key 或不是数组；排查上方 [FactionArchitect/骨架] 的每轮错误")
        return []

    # ── B. 每层并发生成 factions ───────────────────────────────
    tier_count = len(tiers)
    print(f"  骨架就绪：{tier_count} 层；并发为每层生成 {FACTIONS_PER_TIER_MIN}-{FACTIONS_PER_TIER_MAX} 个势力...")

    def _gen_factions_for_tier(tier_spec: dict) -> dict:
        """单层势力填充——返回 tier_spec 的 copy，factions 填好。线程安全：只读 state。"""
        t = tier_spec.copy()
        t_num = t.get("tier", 1)
        t_label = t.get("tier_label", f"第{t_num}层")
        hint = t.get("tier_hint", "")
        # 保留 Step A 如果意外返回了 factions 的数据——Step B 失败时可作为降级
        existing_factions = [f for f in (t.get("factions") or []) if isinstance(f, dict) and f.get("name")]
        prompt = f"""
为《{state.title}》的【第 {t_num} 层 · {t_label}】设计 {FACTIONS_PER_TIER_MIN}-{FACTIONS_PER_TIER_MAX} 个具体势力。

{world_ctx}
世界观：{state.world_setting[:150]}
本层定位：{t_label}
本层风格提示：{hint}
本层内部矛盾：{t.get('tier_conflicts', '')}

═══ 要求 ═══
- 生成 {FACTIONS_PER_TIER_MIN}-{FACTIONS_PER_TIER_MAX} 个并列势力，互相有竞争/对立
- 名字/类型贴题材——避免都市文出"宗门"、修真文出"公司"
- 若本层含"中立势力"：is_neutral=true；含"隐藏势力"：is_hidden=true + reveal_volume
- 第 1 层（最底层）里挑 1 个作为主角起点 protagonist_start=true
- 全书共有 {NEUTRAL_FACTIONS} 个中立 + {HIDDEN_FACTIONS} 个隐藏势力，按层级合理分布

输出 JSON：
{{
  "factions": [
    {{
      "name": "势力名",
      "faction_type": "（帮派/宗门/公司/社团/家族/教团/军阀...）",
      "is_neutral": false,
      "is_hidden": false,
      "reveal_volume": 1,
      "protagonist_start": false,
      "power_level": 1到10,
      "brief": "一句话描述（20字）"
    }}
  ]
}}
"""
        data = request_json(
            system=SYSTEM, user=prompt,
            list_candidates=["factions", "items"],
            min_items=2,
            max_retries=3, temperature=0.7,
            agent_name=f"FactionArchitect/第{t_num}层",
            empty_ok=True,
        )
        factions_list = pick_list(data, "factions", "items") if data else []
        if not factions_list and existing_factions:
            print(f"    · 第{t_num}层 [{t_label}] Step B 失败，沿用 Step A 残留的 {len(existing_factions)} 个势力")
            t["factions"] = existing_factions
        else:
            t["factions"] = factions_list
            if not factions_list:
                print(f"    · 第{t_num}层 [{t_label}] Step B 生成势力为空")
        return t

    enriched = parallel_map(
        fn=_gen_factions_for_tier,
        items=tiers,
        max_workers=PARALLEL_WORKERS,
        label="TierFactions",
    )
    # 过滤失败的（None）并按 tier 号排序
    result = [t for t in enriched if t]
    result.sort(key=lambda t: t.get("tier", 999))
    return result


def _fill_faction_details(
    state: NovelState,
    tier_map: list[dict],
    all_names: list[str],
    realm_brief: str,
    prot_name: str,
) -> None:
    """Step 2：为每个势力填充完整细节（分批，每批最多6个势力）。"""

    # 展平所有势力条目，附上层级信息
    entries = []
    for tier in tier_map:
        for f in tier.get("factions", []):
            if "name" not in f:
                continue
            entries.append({**f, "tier": tier["tier"], "tier_label": tier.get("tier_label", f"第{tier['tier']}层"),
                             "tier_conflicts": tier.get("tier_conflicts", "")})

    # 分批（每批6个，避免JSON过长）
    BATCH = 6
    batches = [entries[i:i+BATCH] for i in range(0, len(entries), BATCH)]

    for batch_num, batch in enumerate(batches):
        names_in_batch = [e["name"] for e in batch]
        print(f"    势力细节批次 {batch_num+1}/{len(batches)}：{' / '.join(names_in_batch)}")

        batch_desc = "\n".join(
            f"- [{e['name']}] Tier{e['tier']}({e['tier_label']}) "
            f"类型:{e['faction_type']} 实力:{e['power_level']} 简述:{e.get('brief','')}"
            for e in batch
        )

        prompt = f"""
为以下 {len(batch)} 个势力填写完整细节。

{format_world_context_brief(state)}

全部势力名单（设计关系时参考）：{', '.join(all_names)}
主角：{prot_name}
力量体系：{realm_brief}

需要填充的势力：
{batch_desc}

每个势力输出：
- surface_goal：表面目标（40字）
- hidden_goal：隐藏目标/不为人知的秘密（40字）
- core_strength：核心实力底牌（30字）
- weakness：致命弱点/可被利用之处（25字）
- key_members：重要成员名字（{KEY_MEMBERS_PER_FACTION_MIN}-{KEY_MEMBERS_PER_FACTION_MAX} 个，名字尽量和已有角色清单挂钩，能对应上则 double duty）
- internal_conflicts：内部矛盾列表（{FACTION_INTERNAL_CONFLICTS_MIN}-{FACTION_INTERNAL_CONFLICTS_MAX} 条，各20字，主角可以利用的）
- power_vacuum_desc：若该势力被消灭，会引发什么权力争夺（30字）
- relations：与其他势力的关系（{FACTION_RELATIONS_MIN}-{FACTION_RELATIONS_MAX} 条，优先连向不同层级的势力，避免只跟同层互动）
- infiltrations：该势力渗透了哪些其他势力（可为空，高层势力填 1-2 条）
- volume_role：各卷中扮演的角色（只填有实质交集的卷）

输出JSON：
{{
  "factions": [
    {{
      "name": "势力名（与输入完全一致）",
      "surface_goal": "...",
      "hidden_goal": "...",
      "core_strength": "...",
      "weakness": "...",
      "key_members": ["成员1", "成员2"],
      "internal_conflicts": ["内部矛盾1", "矛盾2"],
      "power_vacuum_desc": "...",
      "relations": [
        {{"target": "其他势力名", "relation_type": "敌对|友好|中立|附属|暗中对立", "description": "（25字）"}}
      ],
      "infiltrations": [
        {{"target_faction": "被渗透势力", "method": "安插眼线|金钱收买|血脉控制", "depth": "表层|核心|完全控制", "reveal_volume": 卷号}}
      ],
      "volume_role": {{"1": "第1卷角色（20字）", "3": "第3卷角色"}}
    }}
  ]
}}
"""
        details_data = request_json(
            system=SYSTEM, user=prompt,
            list_candidates=["factions", "items"],
            min_items=1,
            max_retries=3, temperature=0.7,
            agent_name=f"FactionArchitect[细节批次{batch_num+1}]",
            empty_ok=True,
        )

        # 合并 tier_map 信息 + 详细信息
        factions_list = pick_list(details_data, "factions", "items") if details_data else []
        details_by_name = {d.get("name", ""): d for d in factions_list if d.get("name")}

        for entry in batch:
            name = entry["name"]
            det = details_by_name.get(name, {})

            rels = []
            for r in det.get("relations", []) or []:
                if not isinstance(r, dict):
                    continue
                target = r.get("target") or r.get("target_faction") or r.get("to")
                rel_type = r.get("relation_type") or r.get("type") or r.get("kind")
                if not target or not rel_type:
                    continue
                rels.append(FactionRelation(
                    target=target,
                    relation_type=rel_type,
                    description=r.get("description", ""),
                ))
            infils = []
            for i in det.get("infiltrations", []) or []:
                if not isinstance(i, dict):
                    continue
                tgt = i.get("target_faction") or i.get("target") or i.get("to")
                if not tgt:
                    continue
                infils.append(FactionInfiltration(
                    target_faction=tgt,
                    method=i.get("method", "安插眼线"),
                    depth=i.get("depth", "表层"),
                    reveal_volume=int(i.get("reveal_volume", NUM_VOLUMES) or NUM_VOLUMES),
                ))
            faction = Faction(
                name=name,
                faction_type=entry.get("faction_type", "势力"),
                power_level=entry.get("power_level", 5),
                territory="",
                tier=entry["tier"],
                tier_label=entry["tier_label"],
                is_neutral=entry.get("is_neutral", False),
                is_hidden=entry.get("is_hidden", False),
                reveal_volume=entry.get("reveal_volume", 1),
                protagonist_start=entry.get("protagonist_start", False),
                surface_goal=det.get("surface_goal", ""),
                hidden_goal=det.get("hidden_goal", ""),
                core_strength=det.get("core_strength", ""),
                weakness=det.get("weakness", ""),
                key_members=det.get("key_members", []),
                internal_conflicts=det.get("internal_conflicts", []),
                infiltrations=infils,
                power_vacuum_desc=det.get("power_vacuum_desc", ""),
                relations=rels,
                volume_role={int(k): v for k, v in det.get("volume_role", {}).items()},
            )
            state.factions.append(faction)


def get_factions_for_volume(state: NovelState, volume_index: int) -> str:
    """
    获取指定卷活跃且已揭露的势力简述，供写作参考。
    隐藏势力只在 reveal_volume <= volume_index 后才出现。
    """
    lines = []
    for f in state.factions:
        # 未到揭露卷的隐藏势力不展示
        if f.is_hidden and f.reveal_volume > volume_index:
            continue
        role = f.volume_role.get(volume_index, "")
        if not role:
            continue
        neutral_tag = "[中立]" if f.is_neutral else ""
        tier_tag = f"T{f.tier}"
        lines.append(f"【{f.name}】{neutral_tag}{tier_tag} {role}（弱点：{f.weakness[:20]}）")
    return "\n".join(lines) if lines else "本卷无特定势力重点。"


def get_faction_context_for_writer(state: NovelState, volume_index: int, max_chars: int = 400) -> str:
    """
    为写作智能体提供分层势力上下文：
    - 已揭露的势力（按层级）
    - 中立势力（始终可见）
    - 本卷活跃势力的内部矛盾提示
    """
    sections = []

    # 中立势力（始终显示）
    neutral = [f for f in state.factions if f.is_neutral and f.status == "active"]
    if neutral:
        n_str = " / ".join(f"{f.name}（{f.faction_type}，可合作）" for f in neutral)
        sections.append(f"[中立可利用] {n_str}")

    # 按层级显示已揭露势力（活跃且有本卷角色）
    for tier in range(1, 6):
        tier_factions = [
            f for f in state.factions
            if f.tier == tier
            and not f.is_neutral
            and f.status == "active"
            and (not f.is_hidden or f.reveal_volume <= volume_index)
            and f.volume_role.get(volume_index)
        ]
        if not tier_factions:
            continue
        tier_name = {1: "底层", 2: "地方", 3: "国家", 4: "大陆", 5: "幕后"}.get(tier, f"T{tier}")
        for f in tier_factions:
            role = f.volume_role.get(volume_index, "")
            conflict_hint = f.internal_conflicts[0] if f.internal_conflicts else ""
            conflict_str = f" | 内部矛盾：{conflict_hint}" if conflict_hint else ""
            sections.append(f"[{tier_name}层/{f.name}] {role}{conflict_str}")

    result = "\n".join(sections)
    return result[:max_chars] if len(result) > max_chars else result


def _build_factions_desc(factions: list[Faction]) -> str:
    tier_name = {1: "底层", 2: "地方", 3: "国家", 4: "大陆", 5: "幕后"}
    lines = []
    for tier in range(1, 6):
        tier_list = [f for f in factions if f.tier == tier]
        if not tier_list:
            continue
        lines.append(f"=== {tier_name.get(tier, tier)}层势力 ===")
        for f in tier_list:
            tags = []
            if f.is_neutral:
                tags.append("中立")
            if f.is_hidden:
                tags.append(f"第{f.reveal_volume}卷揭露")
            if f.protagonist_start:
                tags.append("主角起点")
            tag_str = f"[{'/'.join(tags)}]" if tags else ""
            lines.append(
                f"  {f.name}{tag_str}（{f.faction_type}/实力{f.power_level}）"
                f" 表面:{f.surface_goal[:25]} | 隐藏:{f.hidden_goal[:25]}"
            )
    return "\n".join(lines)


def _fallback_tier_framework() -> list[dict]:
    """LLM完全失败时的最小占位框架，保证流程不中断。"""
    return [
        {"tier": 1, "tier_label": "底层", "tier_conflicts": "地方势力争夺地盘",
         "factions": [{"name": "青云帮", "faction_type": "帮派", "power_level": 1,
                       "is_neutral": False, "is_hidden": False, "reveal_volume": 1,
                       "protagonist_start": True, "brief": "主角起点小帮派"}]},
        {"tier": 2, "tier_label": "地方", "tier_conflicts": "两大宗门争夺资源",
         "factions": [{"name": "铁剑门", "faction_type": "宗门", "power_level": 3,
                       "is_neutral": False, "is_hidden": False, "reveal_volume": 1,
                       "protagonist_start": False, "brief": "地方强宗"},
                      {"name": "天云商会", "faction_type": "商会", "power_level": 2,
                       "is_neutral": True, "is_hidden": False, "reveal_volume": 1,
                       "protagonist_start": False, "brief": "中立商业组织"}]},
        {"tier": 3, "tier_label": "国家级", "tier_conflicts": "王国与大宗门的权力博弈",
         "factions": [{"name": "玄武王朝", "faction_type": "帝国", "power_level": 6,
                       "is_neutral": False, "is_hidden": False, "reveal_volume": 2,
                       "protagonist_start": False, "brief": "统治王国"}]},
        {"tier": 4, "tier_label": "大陆级", "tier_conflicts": "圣地之间的暗中角力",
         "factions": [{"name": "苍穹圣地", "faction_type": "神秘组织", "power_level": 9,
                       "is_neutral": False, "is_hidden": True, "reveal_volume": 3,
                       "protagonist_start": False, "brief": "大陆顶级势力"}]},
        {"tier": 5, "tier_label": "幕后黑手", "tier_conflicts": "操控一切的真正力量",
         "factions": [{"name": "虚空议会", "faction_type": "神秘组织", "power_level": 10,
                       "is_neutral": False, "is_hidden": True, "reveal_volume": 5,
                       "protagonist_start": False, "brief": "真正的幕后黑手"}]},
    ]
