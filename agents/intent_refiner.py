"""
IntentRefiner —— 追加意图时的"增量精炼"级联。

当作者在 creative_intent 上追加新想法后，可选让这个新意图"回灌"到已生成的
Phase 1-3 各模块里。和重建不同：
  - 保留：与新意图不冲突的内容原样留
  - 修正：与新意图有轻微出入的字段做小改（不删整条）
  - 新增：新意图引入的新元素补充进来
  - 删除：仅在严重冲突且无法调和时

顺序按依赖拓扑：世界 → 体系/地理/时间/经济 → 势力 → 卷/书结构 → 人物 → 关系网
下游产物（爽点/伏笔/机缘/舞台/节奏/章节蓝图）目前不自动精炼——它们高度耦合，
用户可在侧边栏按模块的"🔄 重建"按钮手动刷。

每个模块的精炼都是"现有数据 + 新意图 → LLM → 修订版"的增量 merge。
每个精炼都是独立一次 LLM 调用；失败某个模块不影响其他模块。
"""
from __future__ import annotations
import json
import dataclasses
from dataclasses import asdict
from typing import Callable, Optional

from utils.json_utils import request_json, pick_list
from persistence.state import (
    NovelState, Character, CharacterRole, Relationship,
    Faction, FactionRelation, FactionInfiltration,
    CharacterBond, RelationshipWeb,
)
from agents.concept_pitch import format_concept_brief, format_world_context_brief


REFINE_SYSTEM = """你是小说设定增量修订员。
作者追加了新的创作意图，现在需要把已生成的某个设定模块按新意图做**增量修改**——不是推翻重写。

【修订原则】
1. 保留：已有内容凡是与新意图不冲突的，原样保留（字段值不要无故变动）
2. 修正：与新意图有轻微出入的字段做小改（换措辞、调整取向，但不删整条）
3. 新增：新意图引入的新元素补充进来（比如"我想加一个主角的双胞胎妹妹"→ characters 里多一条）
4. 删除：**仅在严重冲突且无法调和时**才删——默认不要删已有条目
5. 保字段：输出的 JSON 字段结构必须与输入完全一致（key 名不能变，数组条目的 schema 不能变）
6. 保 ID：已有条目的 id/编号字段保持不变，只追加新条目用新 id

输出严格 JSON，结构与输入样例一致。"""


# ═══════════════════════════════════════════════════════
#  通用单模块精炼
# ═══════════════════════════════════════════════════════

def _refine_section(
    state: NovelState,
    section_name: str,
    existing_data,
    section_hint: str,
    addition: str,
    max_retries: int = 3,
    temperature: float = 0.55,
) -> Optional[dict]:
    """
    通用精炼：把 existing_data 按 addition（新意图）做增量修改。
    返回 LLM 输出的 dict（与 existing_data 同结构），或 None（失败）。
    """
    intent = state.creative_intent
    merged_intent = (intent.raw_description or "")[:1500]
    tone_summary = intent.tone_summary or "（未知）"

    # 把 existing_data 序列化——太长就截断
    if dataclasses.is_dataclass(existing_data):
        existing_json = json.dumps(asdict(existing_data), ensure_ascii=False, indent=2)
    elif isinstance(existing_data, list):
        existing_json = json.dumps(
            [asdict(x) if dataclasses.is_dataclass(x) else x for x in existing_data],
            ensure_ascii=False, indent=2,
        )
    else:
        existing_json = json.dumps(existing_data, ensure_ascii=False, indent=2)

    # 长度截断——避免 prompt 爆预算
    if len(existing_json) > 6000:
        existing_json = existing_json[:5800] + "\n... (已截断，请基于结构做增量修订)"

    prompt = f"""对《{state.title}》的【{section_name}】做增量精炼——不是重建。

{section_hint}

═══ 作者的新追加意图（本次要回灌到本模块）═══
{addition}

═══ 完整意图（新追加 + 旧意图拼接后）═══
{merged_intent}

整体气质：{tone_summary}

═══ 现有【{section_name}】数据（不要推翻，在此基础上改）═══
```json
{existing_json}
```

═══ 修订要求 ═══
1. **保留** 与新意图不冲突的条目——字段不要无故变动
2. **修正** 与新意图有轻微冲突的字段（微调措辞/取向，不删条目）
3. **新增** 新意图引入的新元素（追加条目，给新 id）
4. **删除** 谨慎——只在严重冲突时，宁可修正也不要删
5. **字段结构** 与输入保持一致，id 字段保持原值

输出 JSON，结构与输入完全一致：
```json
<修订后的完整数据>
```
"""
    try:
        data = request_json(
            system=REFINE_SYSTEM, user=prompt,
            max_retries=max_retries, temperature=temperature,
            agent_name=f"IntentRefiner[{section_name}]",
            empty_ok=True,
        )
        return data if data else None
    except Exception as e:
        print(f"    ⚠ {section_name} 精炼失败：{e}")
        return None


# ═══════════════════════════════════════════════════════
#  各模块专用精炼
# ═══════════════════════════════════════════════════════

def refine_world(state: NovelState, addition: str) -> bool:
    """精炼 world_setting（字符串）。"""
    if not state.world_setting:
        return False
    hint = "世界观是综合性文本——在原文基础上按新意图补充/调整段落，不要整段重写。"
    # world_setting 是 str，不走通用路径
    prompt = f"""为《{state.title}》修订世界观描述——不是重写。

{hint}

═══ 作者新追加意图 ═══
{addition}

═══ 现有世界观（基础）═══
{state.world_setting[:3000]}

═══ 要求 ═══
- 原文与新意图不冲突的段落原样保留
- 有轻微冲突的段落做小改
- 新意图引入的新元素补充进来（新增段落在合适位置插入）
- 保持整体结构不变

直接输出修订后的完整世界观文本（纯文本，不要 JSON 包裹）：
"""
    try:
        from llm_layer.llm import system_user
        out = system_user(REFINE_SYSTEM, prompt, temperature=0.5, max_tokens=3000).strip()
        if out and len(out) > 100:
            state.world_setting = out
            print(f"  ✓ world_setting 精炼（{len(out)} 字）")
            return True
    except Exception as e:
        print(f"    ⚠ world_setting 精炼失败：{e}")
    return False


def refine_power_system(state: NovelState, addition: str) -> bool:
    """精炼 power_system。"""
    if not state.power_system:
        return False
    data = _refine_section(
        state, "power_system",
        state.power_system,
        "体系类型/境界链/流派/特殊机制——按新意图补充或微调，别换体系名字和基础结构。",
        addition,
    )
    if not data:
        return False
    # 只改几个安全字段，避免破坏结构
    ps = state.power_system
    for field in ("system_description", "power_flow", "rank_unit", "system_nature"):
        if data.get(field):
            setattr(ps, field, data[field])
    # special_mechanics 允许追加
    new_mech = data.get("special_mechanics", [])
    if isinstance(new_mech, list) and new_mech:
        existing_names = {m.name for m in ps.special_mechanics}
        from persistence.state import PowerMechanic
        for m in new_mech:
            if isinstance(m, dict) and m.get("name") and m["name"] not in existing_names:
                ps.special_mechanics.append(PowerMechanic(
                    name=m.get("name", ""),
                    description=m.get("description", ""),
                    protagonist_usage=m.get("protagonist_usage", ""),
                    narrative_impact=m.get("narrative_impact", ""),
                ))
    print(f"  ✓ power_system 精炼（特殊机制：{len(ps.special_mechanics)} 条）")
    return True


def refine_geography(state: NovelState, addition: str) -> bool:
    """精炼 geography——允许追加新区域/交通方式/距离。"""
    if not state.geography or not state.geography.regions:
        return False
    data = _refine_section(
        state, "geography",
        state.geography,
        "regions/transport_modes/distances——可以追加条目，已有 region_id 保持不变。",
        addition,
    )
    if not data:
        return False
    geo = state.geography
    # 合并 regions
    from persistence.state import GeoRegion, TransportMode, TravelDistance
    existing_region_ids = {r.region_id for r in geo.regions}
    for r in data.get("regions", []):
        rid = r.get("region_id", "")
        if not rid or rid in existing_region_ids:
            # 修正已有：只改可安全字段
            if rid in existing_region_ids:
                for cur in geo.regions:
                    if cur.region_id == rid:
                        for f in ("description", "climate", "products", "culture_notes"):
                            if r.get(f):
                                setattr(cur, f, r[f])
            continue
        geo.regions.append(GeoRegion(
            region_id=rid, name=r.get("name", ""), level=r.get("level", "城镇"),
            parent_id=r.get("parent_id", ""), description=r.get("description", ""),
            climate=r.get("climate", ""), products=r.get("products", ""),
            culture_notes=r.get("culture_notes", ""), notable_spots=r.get("notable_spots", []),
        ))
    # 合并 transport_modes
    existing_mode_names = {m.name for m in geo.transport_modes}
    for m in data.get("transport_modes", []):
        if m.get("name") and m["name"] not in existing_mode_names:
            geo.transport_modes.append(TransportMode(
                name=m.get("name", ""), speed_description=m.get("speed_description", ""),
                realm_required=m.get("realm_required", ""), cost=m.get("cost", ""),
            ))
    # world_map_desc 允许改
    if data.get("world_map_desc"):
        geo.world_map_desc = data["world_map_desc"]
    print(f"  ✓ geography 精炼（regions={len(geo.regions)}, modes={len(geo.transport_modes)}）")
    return True


def refine_timeline(state: NovelState, addition: str) -> bool:
    """精炼 timeline——允许追加新历史事件。"""
    if not state.timeline or not state.timeline.events:
        return False
    data = _refine_section(
        state, "timeline",
        state.timeline,
        "events 历史事件——可追加新事件（用新 event_id），已有事件不要删。",
        addition,
    )
    if not data:
        return False
    from persistence.state import TimelineEvent
    existing_ids = {e.event_id for e in state.timeline.events}
    for e in data.get("events", []):
        eid = e.get("event_id", "")
        if not eid or eid in existing_ids:
            continue
        state.timeline.events.append(TimelineEvent(
            event_id=eid, era=e.get("era", "近代"),
            years_ago=int(e.get("years_ago", 0)),
            name=e.get("name", ""), description=e.get("description", ""),
            consequences=e.get("consequences", ""),
            related_factions=e.get("related_factions", []),
            foreshadow_potential=e.get("foreshadow_potential", ""),
        ))
    print(f"  ✓ timeline 精炼（events={len(state.timeline.events)}）")
    return True


def refine_economy(state: NovelState, addition: str) -> bool:
    if not state.economy or not state.economy.currencies:
        return False
    data = _refine_section(
        state, "economy",
        state.economy,
        "currencies/price_anchors/wealth_curve——按新意图可调整物价分层或加新货币。",
        addition,
    )
    if not data:
        return False
    eco = state.economy
    if data.get("trade_notes"):
        eco.trade_notes = data["trade_notes"]
    from persistence.state import Currency, PriceAnchor
    existing_ccy = {c.name for c in eco.currencies}
    for c in data.get("currencies", []):
        if c.get("name") and c["name"] not in existing_ccy:
            eco.currencies.append(Currency(
                name=c["name"], rank=int(c.get("rank", 1)),
                exchange_to_base=int(c.get("exchange_to_base", 1)),
                notes=c.get("notes", ""),
            ))
    existing_items = {a.item for a in eco.price_anchors}
    for a in data.get("price_anchors", []):
        if a.get("item") and a["item"] not in existing_items:
            eco.price_anchors.append(PriceAnchor(
                item=a["item"], price=a.get("price", ""), tier=a.get("tier", "")
            ))
    print(f"  ✓ economy 精炼（货币={len(eco.currencies)}｜锚点={len(eco.price_anchors)}）")
    return True


def refine_factions(state: NovelState, addition: str) -> bool:
    """精炼 factions——允许追加新势力，修正已有势力的 surface/hidden_goal。"""
    if not state.factions:
        return False
    data = _refine_section(
        state, "factions",
        state.factions,
        "势力列表——可追加新势力，已有势力可微调 surface_goal/hidden_goal/relations 字段，别删。",
        addition,
    )
    if not data:
        return False
    # data 可能是 list 或 {factions:[...]}
    factions_list = data if isinstance(data, list) else pick_list({"factions": data.get("factions", [])}, "factions", "items")
    existing_by_name = {f.name: f for f in state.factions}
    added = 0
    edited = 0
    for fd in factions_list:
        if not isinstance(fd, dict) or not fd.get("name"):
            continue
        name = fd["name"]
        if name in existing_by_name:
            # 微调已有
            cur = existing_by_name[name]
            for fld in ("surface_goal", "hidden_goal", "core_strength", "weakness", "power_vacuum_desc"):
                if fd.get(fld):
                    setattr(cur, fld, fd[fld])
            # 追加关系（不覆盖）
            existing_rels = {(r.target, r.relation_type) for r in cur.relations}
            for r in fd.get("relations", []):
                key = (r.get("target", ""), r.get("relation_type", ""))
                if key[0] and key not in existing_rels:
                    cur.relations.append(FactionRelation(
                        target=key[0], relation_type=key[1], description=r.get("description", "")
                    ))
            edited += 1
        else:
            # 新势力——territory/tier_label 是必填字段，LLM 没给就兜底
            tier = int(fd.get("tier", 3))
            state.factions.append(Faction(
                name=name,
                faction_type=fd.get("faction_type", ""),
                power_level=int(fd.get("power_level", 5)),
                territory=fd.get("territory", "（待补充）"),
                tier=tier,
                tier_label=fd.get("tier_label", f"第{tier}层"),
                is_neutral=bool(fd.get("is_neutral", False)),
                is_hidden=bool(fd.get("is_hidden", False)),
                reveal_volume=int(fd.get("reveal_volume", 1)),
                protagonist_start=bool(fd.get("protagonist_start", False)),
                surface_goal=fd.get("surface_goal", ""),
                hidden_goal=fd.get("hidden_goal", ""),
                core_strength=fd.get("core_strength", ""),
                weakness=fd.get("weakness", ""),
                key_members=fd.get("key_members", []),
                internal_conflicts=fd.get("internal_conflicts", []),
                relations=[
                    FactionRelation(
                        target=r.get("target", ""),
                        relation_type=r.get("relation_type", ""),
                        description=r.get("description", ""),
                    )
                    for r in fd.get("relations", [])
                    if isinstance(r, dict) and r.get("target")
                ],
            ))
            added += 1
    print(f"  ✓ factions 精炼（新增 {added}｜微调 {edited}｜总计 {len(state.factions)}）")
    return added or edited


def refine_characters(state: NovelState, addition: str) -> bool:
    """
    精炼 characters——关键功能：
    - 允许新增角色（新意图引入的新人物）
    - 允许微调现有角色的 motivation/arc/fatal_flaw/desire 等软字段
    - 不删除已有角色
    - 名字重复的以已有角色为准（做字段合并）
    """
    if not state.characters:
        return False
    # 只传关键字段，避免 prompt 爆炸
    brief_list = [
        {
            "name": c.name, "role": c.role.value, "gender": c.gender,
            "personality": c.personality[:60], "motivation": c.motivation[:60],
            "arc": c.arc[:80], "fatal_flaw": c.fatal_flaw[:40],
            "desire": c.desire[:40], "fear": c.fear[:40],
            "first_volume": c.first_volume, "last_volume": c.last_volume,
        }
        for c in state.characters
    ]
    data = _refine_section(
        state, "characters",
        brief_list,
        "角色列表——可追加新角色（用新名字），已有角色名字不要改，只微调 motivation/arc/fatal_flaw/desire/fear/personality。新增角色必须包含 role (主角/主要配角/次要配角/反派/卷内角色)。",
        addition,
        temperature=0.6,
    )
    if not data:
        return False
    chars_list = data if isinstance(data, list) else pick_list({"x": data.get("characters", data.get("items", []))}, "x")
    existing_by_name = {c.name: c for c in state.characters}
    role_map = {r.value: r for r in CharacterRole}
    added = 0
    edited = 0
    for cd in chars_list:
        if not isinstance(cd, dict) or not cd.get("name"):
            continue
        name = cd["name"]
        if name in existing_by_name:
            cur = existing_by_name[name]
            for fld in ("motivation", "arc", "fatal_flaw", "desire", "fear", "personality", "personality_detail"):
                v = cd.get(fld)
                if v and v != getattr(cur, fld, None):
                    setattr(cur, fld, v)
            edited += 1
        else:
            # 新角色——字段不全就填默认
            state.characters.append(Character(
                name=name,
                role=role_map.get(cd.get("role", "次要配角"), CharacterRole.MINOR),
                gender=cd.get("gender", "男"),
                age_desc=cd.get("age_desc", ""),
                appearance=cd.get("appearance", ""),
                personality=cd.get("personality", ""),
                personality_detail=cd.get("personality_detail", cd.get("personality", "")),
                background=cd.get("background", "（追加意图引入，待补）"),
                trauma=cd.get("trauma", ""),
                desire=cd.get("desire", ""),
                fear=cd.get("fear", ""),
                speech_pattern=cd.get("speech_pattern", "普通"),
                ability=cd.get("ability", ""),
                realm=cd.get("realm", "普通人"),
                arc=cd.get("arc", ""),
                motivation=cd.get("motivation", ""),
                fatal_flaw=cd.get("fatal_flaw", ""),
                first_volume=int(cd.get("first_volume", 1)),
                last_volume=int(cd.get("last_volume", -1)),
                relationships=[],
                volume_arcs={},
                volume_realm={},
            ))
            added += 1
    print(f"  ✓ characters 精炼（新增 {added}｜微调 {edited}｜总计 {len(state.characters)}）")
    return added or edited


def refine_relationship_web(state: NovelState, addition: str) -> bool:
    """精炼关系网——主要是追加新 bond，不删除已有。"""
    if not state.relationship_web or not state.relationship_web.bonds:
        return False
    brief = {
        "bonds": [
            {"bond_id": b.bond_id, "char_a": b.char_a, "char_b": b.char_b,
             "surface_relation": b.surface_relation, "true_relation": b.true_relation}
            for b in state.relationship_web.bonds[:30]
        ],
        "char_names": [c.name for c in state.characters],
    }
    data = _refine_section(
        state, "relationship_web",
        brief,
        "关系网 bonds——可追加新 bond（用新 bond_id），已有不要删。char_a/char_b 必须是 char_names 里存在的名字。",
        addition,
    )
    if not data:
        return False
    bonds_list = pick_list(data, "bonds", "items")
    existing_ids = {b.bond_id for b in state.relationship_web.bonds}
    valid_names = {c.name for c in state.characters}
    added = 0
    for bd in bonds_list:
        if not isinstance(bd, dict):
            continue
        bid = bd.get("bond_id", "")
        if not bid or bid in existing_ids:
            continue
        a, b = bd.get("char_a", ""), bd.get("char_b", "")
        if a not in valid_names or b not in valid_names:
            continue
        state.relationship_web.bonds.append(CharacterBond(
            bond_id=bid, char_a=a, char_b=b,
            surface_relation=bd.get("surface_relation", ""),
            true_relation=bd.get("true_relation", ""),
            hidden_secret=bd.get("hidden_secret", ""),
            tension_source=bd.get("tension_source", ""),
            volume_evolution={int(k): v for k, v in bd.get("volume_evolution", {}).items()},
            reveal_volume=int(bd.get("reveal_volume", -1)),
            affects_protagonist=bool(bd.get("affects_protagonist", True)),
            future_trajectory=bd.get("future_trajectory", ""),
            projected_changes={int(k): v for k, v in bd.get("projected_changes", {}).items()},
        ))
        added += 1
    print(f"  ✓ relationship_web 精炼（新增 bonds={added}｜总计 {len(state.relationship_web.bonds)}）")
    return added > 0


def refine_volumes(state: NovelState, addition: str) -> bool:
    """精炼卷——只微调每卷的 theme/arc/opening_hook/closing_hook，不改卷数和章节分配。"""
    if not state.volumes:
        return False
    brief = [
        {"index": v.index, "title": v.title, "theme": v.theme, "arc": v.arc[:100],
         "purpose": v.purpose, "expression": v.expression,
         "opening_hook": v.opening_hook, "closing_hook": v.closing_hook,
         "volume_antagonist": v.volume_antagonist, "structure_role": v.structure_role}
        for v in state.volumes
    ]
    data = _refine_section(
        state, "volumes",
        brief,
        "卷主题/弧线——只能微调 theme/arc/opening_hook/closing_hook/purpose/expression/volume_antagonist。不要改 index、title、structure_role、章节起止。按新意图调整每卷的方向。",
        addition,
    )
    if not data:
        return False
    volumes_list = data if isinstance(data, list) else pick_list({"v": data.get("volumes", data.get("items", []))}, "v")
    edits = 0
    for vd in volumes_list:
        if not isinstance(vd, dict):
            continue
        idx = vd.get("index")
        if idx is None:
            continue
        v = state.get_volume(int(idx))
        if not v:
            continue
        for fld in ("theme", "arc", "purpose", "expression", "opening_hook", "closing_hook", "volume_antagonist"):
            if vd.get(fld) and vd[fld] != getattr(v, fld, ""):
                setattr(v, fld, vd[fld])
                edits += 1
    print(f"  ✓ volumes 精炼（字段微调 {edits} 处）")
    return edits > 0


# ═══════════════════════════════════════════════════════
#  顶层编排——按依赖顺序逐模块精炼
# ═══════════════════════════════════════════════════════

# 顺序：世界观 → 体系/地理/时间/经济 → 势力 → 人物 → 关系 → 卷
REFINEMENT_PIPELINE: list[tuple[str, Callable]] = [
    ("world_setting", refine_world),
    ("power_system", refine_power_system),
    ("geography", refine_geography),
    ("timeline", refine_timeline),
    ("economy", refine_economy),
    ("factions", refine_factions),
    ("characters", refine_characters),
    ("relationship_web", refine_relationship_web),
    ("volumes", refine_volumes),
]


def cascade_refine_all(
    state: NovelState,
    addition: str,
    only_sections: Optional[list[str]] = None,
    progress_hook: Optional[Callable[[str, int, int], None]] = None,
) -> dict:
    """
    按新意图逐模块增量精炼。返回每个模块的结果 dict：
      {section_name: True/False/"skipped"}

    only_sections：可选，限定只精炼这些模块（用于前端细粒度勾选）。
    progress_hook(section_name, current_index, total)：可选回调——每个模块开始前调用。
    """
    addition = (addition or "").strip()
    if not addition:
        return {}
    print(f"\n═══ 增量精炼级联：基于新追加意图（{len(addition)} 字）═══")

    pipeline = [
        (name, fn) for name, fn in REFINEMENT_PIPELINE
        if not only_sections or name in only_sections
    ]
    total = len(pipeline)

    results: dict = {}
    # 先把 skipped 的填入
    for name, _ in REFINEMENT_PIPELINE:
        if only_sections and name not in only_sections:
            results[name] = "skipped"

    for i, (name, fn) in enumerate(pipeline, 1):
        if progress_hook:
            try:
                progress_hook(name, i, total)
            except Exception:
                pass
        try:
            ok = fn(state, addition)
            results[name] = bool(ok)
        except Exception as e:
            print(f"  ✗ {name} 精炼异常：{e}")
            results[name] = False

    print(f"═══ 精炼完成 ═══\n")
    return results
