"""
氛围库设计 —— CustomsDesigner

为每个 region/faction/volume 生成"感官细节碎片 + 文化小条目"，
让 writer 在写作时可以直接取用，避免每次现编"早市味道""见礼姿势""街市童谣"。

输入：
  · 一个 scope（region/faction/volume）的基本信息
  · 全书 tone_summary（基调一致性）

输出：
  · 30-80 条 AtmosphereFragment（感官碎片）
  · 10-30 条 CulturalCustom（礼仪/俗语/禁忌/称谓/童谣/节庆/服饰小节）
"""
from __future__ import annotations
from typing import Optional

from persistence.state import (
    NovelState, AtmosphereLibrary, AtmosphereScope,
    AtmosphereFragment, CulturalCustom,
)


SYSTEM_TEMPLATE = """你是{genre}小说的世界感设计师。你的任务**不是**做大设定（境界/势力/地图），而是做**小细节**——
那些让读者读到一句话就觉得"对，这个世界活着"的具体感官和文化碎片。

差别：
- ✗ 大设定："江州地处南方，盛产盐铁，赵氏家族控制大部分商道"
- ✓ 小细节："江州早市的盐商铺前挂着褪色的'赵记'幡子，铜钱串在湿漉漉的麻绳上一串就是一两"

你输出两类东西：

## 一、AtmosphereFragment（感官细节碎片）—— 30-50 条
每条 30-60 字，五感之一：
- **视觉**："锈红色的铁衙门牌坊在烟雨里像沉默的兽"
- **听觉**："更夫敲三下，第三下总比前两下慢半拍——老更夫的左手不灵了"
- **嗅觉**："茶铺后巷飘出腌咸鱼的腥味，混着巷口杏花的甜腻"
- **触觉**："铜钱穿在湿绳上，攥在手心一阵凉黏"
- **内感**："胃里那块凉豆腐还没消化，像团石头压着"

每条要：
- **具体可感**——不能是"很美""很压抑"这种抽象词
- **带本世界的特征**——铜钱/麻绳/烟雨/早市……要符合这个 scope 的题材和氛围
- **可以单独出现在小说里**而不显得突兀

涵盖情境（occasion）：早市/夜路/独处/打斗/饮宴/赶路/睡眠/雨天/争吵/送别 等

## 二、CulturalCustom（文化小条目）—— 10-20 条
本世界独有的细节，每条 30-100 字：
- **礼仪**：见上司怎么行礼？同辈怎么打招呼？女眷见外男的避讳？
- **称谓**：王爷的下人怎么称呼他？普通百姓对官的称呼？江湖人之间？
- **俗语**：本地百姓的口头禅、骂人的话、夸人的话（"这小子真是块榆木疙瘩")
- **禁忌**：不能在某些场合做的事（"这地方夜里不能吹口哨——招邪")
- **童谣**：本地小孩唱的歌、小调（最多 4 句）
- **节庆**：本地特有的节日及其仪式（30-60 字）
- **服饰小节**：身份的细节标志（如"举人帽要插一支柳枝")

each 必须标 used_by（谁会用）和（如适用）avoid_by（谁绝对不用）

---

## 输出严格 JSON

{{
  "fragments": [
    {{"fragment": "...30-60 字...", "sense": "visual|audio|smell|taste|touch|internal|mixed", "occasion": "早市|夜路|独处|...", "notes": ""}}
  ],
  "customs": [
    {{"type": "礼仪|俗语|禁忌|称谓|童谣|节庆|服饰小节", "content": "...30-100 字...", "used_by": "...", "avoid_by": ""}}
  ]
}}

【硬约束】
- fragments 至少 30 条，customs 至少 10 条
- 每条都要新鲜——不要套话/老梗/任何能在 100 部网文里见到的描写
- 五感分布要均衡：视觉/听觉/嗅觉/触觉/内感各占至少 5 条
- customs 要覆盖 5+ 类（礼仪/俗语/禁忌/称谓/童谣 至少各 1-2 条）"""


def _scope_context(scope_type: str, scope_key: str, label: str, state: NovelState) -> str:
    """根据 scope 给 LLM 拼一段描述——它要知道是为谁/哪里/什么卷设计氛围。"""
    parts = [f"【scope】{scope_type} = {scope_key}"]
    if label:
        parts.append(f"【展示名】{label}")

    # region：取地理描述
    if scope_type == "region":
        geo = getattr(state, "geography", None)
        if geo:
            for r in (getattr(geo, "regions", []) or []):
                if getattr(r, "region_id", "") == scope_key or getattr(r, "name", "") == scope_key:
                    parts.append(f"【描述】{getattr(r, 'description', '')[:300]}")
                    parts.append(f"【人口/规模】{getattr(r, 'population_desc', '')}")
                    break
    # faction：取势力描述
    elif scope_type == "faction":
        for f in (state.factions or []):
            if getattr(f, "name", "") == scope_key:
                parts.append(f"【势力描述】{getattr(f, 'description', '')[:300]}")
                parts.append(f"【表面目标】{getattr(f, 'surface_goal', '')[:100]}")
                parts.append(f"【风格】{getattr(f, 'style', '')[:80]}")
                break
    # volume：取卷主题/地理
    elif scope_type == "volume":
        try:
            vi = int(scope_key)
            v = state.get_volume(vi)
            if v:
                parts.append(f"【卷主题】{v.theme}")
                parts.append(f"【对手】{getattr(v, 'volume_antagonist', '')}")
                parts.append(f"【关键事件】{' / '.join((v.key_events or [])[:3])}")
        except (ValueError, TypeError):
            pass

    # 全书基调
    ci = getattr(state, "creative_intent", None)
    if ci:
        if getattr(ci, "tone_summary", ""):
            parts.append(f"【全书基调】{ci.tone_summary[:200]}")
        if getattr(ci, "world_tone_hint", ""):
            parts.append(f"【世界基调】{ci.world_tone_hint}")
    parts.append(f"【题材】{state.genre}")
    return "\n".join(parts)


def design_atmosphere(
    state: NovelState,
    scope_type: str,
    scope_key: str,
    label: str = "",
    *,
    max_retries: int = 2,
) -> Optional[AtmosphereScope]:
    """为单个 scope 生成氛围库。失败返回 None。"""
    from utils.json_utils import request_json

    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "")
    user = (
        f"为以下 scope 设计氛围库：\n\n"
        f"{_scope_context(scope_type, scope_key, label, state)}\n\n"
        f"严格按 SYSTEM 的 JSON schema 输出。"
    )

    try:
        data = request_json(
            system=system, user=user,
            required_keys=["fragments", "customs"],
            list_candidates=["fragments"],
            min_items=20,   # 起码 20 条 fragment
            max_retries=max_retries,
            temperature=0.85,
            agent_name=f"AtmosphereDesigner[{scope_type}:{scope_key}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  [atmosphere] {scope_type}:{scope_key} 设计失败：{type(e).__name__}: {e}")
        return None

    if not data:
        return None

    fragments = []
    for f in (data.get("fragments") or []):
        if not isinstance(f, dict):
            continue
        text = str(f.get("fragment", "")).strip()
        if not text:
            continue
        fragments.append(AtmosphereFragment(
            fragment=text[:200],
            sense=str(f.get("sense", "mixed"))[:20],
            occasion=str(f.get("occasion", ""))[:60],
            notes=str(f.get("notes", ""))[:120],
        ))

    customs = []
    for c in (data.get("customs") or []):
        if not isinstance(c, dict):
            continue
        content = str(c.get("content", "")).strip()
        if not content:
            continue
        customs.append(CulturalCustom(
            type=str(c.get("type", "其他"))[:20],
            content=content[:300],
            used_by=str(c.get("used_by", ""))[:120],
            avoid_by=str(c.get("avoid_by", ""))[:120],
        ))

    if not fragments and not customs:
        return None

    return AtmosphereScope(
        scope_type=scope_type,
        scope_key=str(scope_key),
        label=label or scope_key,
        fragments=fragments,
        customs=customs,
    )


def design_for_volume(state: NovelState, volume_index: int) -> Optional[AtmosphereScope]:
    """为某一卷设计氛围库——在写本卷前调用，结果存到 state.atmosphere_library。"""
    v = state.get_volume(volume_index)
    if not v:
        return None
    sc = design_atmosphere(state, "volume", str(volume_index), label=f"V{volume_index}《{v.title}》")
    if sc and state.atmosphere_library:
        state.atmosphere_library.upsert(sc)
    return sc


def design_for_region(state: NovelState, region_id: str, label: str = "") -> Optional[AtmosphereScope]:
    sc = design_atmosphere(state, "region", region_id, label=label or region_id)
    if sc and state.atmosphere_library:
        state.atmosphere_library.upsert(sc)
    return sc


def design_for_faction(state: NovelState, faction_name: str) -> Optional[AtmosphereScope]:
    sc = design_atmosphere(state, "faction", faction_name, label=faction_name)
    if sc and state.atmosphere_library:
        state.atmosphere_library.upsert(sc)
    return sc


# ═══════════════════════════════════════════════════════════════
#  消费侧：给 writer 取用
# ═══════════════════════════════════════════════════════════════

def format_atmosphere_for_scene(
    state: NovelState,
    *,
    volume_index: Optional[int] = None,
    location_name: str = "",
    factions: Optional[list[str]] = None,
    max_fragments: int = 8,
    max_customs: int = 4,
) -> str:
    """
    根据本幕的卷号/地点/出场势力，从氛围库挑出适用的碎片，拼成 writer prompt 的一段。
    """
    lib = getattr(state, "atmosphere_library", None)
    if not lib or not lib.scopes:
        return ""

    selected_fragments: list[AtmosphereFragment] = []
    selected_customs: list[CulturalCustom] = []

    # 收集相关 scope（卷 → 地区 → 势力）
    relevant_scopes: list[AtmosphereScope] = []
    if volume_index:
        sc = lib.get("volume", str(volume_index))
        if sc: relevant_scopes.append(sc)
    if location_name:
        sc = lib.get("region", location_name)
        if sc: relevant_scopes.append(sc)
    for fac in (factions or []):
        sc = lib.get("faction", fac)
        if sc: relevant_scopes.append(sc)
    # 通用 scope
    sc = lib.get("general", "_")
    if sc: relevant_scopes.append(sc)

    if not relevant_scopes:
        return ""

    seen_frag = set()
    for sc in relevant_scopes:
        for fr in sc.fragments:
            if fr.fragment in seen_frag:
                continue
            seen_frag.add(fr.fragment)
            selected_fragments.append(fr)
            if len(selected_fragments) >= max_fragments:
                break
        if len(selected_fragments) >= max_fragments:
            break

    seen_cust = set()
    for sc in relevant_scopes:
        for cu in sc.customs:
            if cu.content in seen_cust:
                continue
            seen_cust.add(cu.content)
            selected_customs.append(cu)
            if len(selected_customs) >= max_customs:
                break
        if len(selected_customs) >= max_customs:
            break

    if not (selected_fragments or selected_customs):
        return ""

    parts = ["═══ 本幕氛围库（可选取用——融入 1-3 条让世界活起来）═══"]
    if selected_fragments:
        parts.append("【感官细节】")
        for fr in selected_fragments:
            tag = f"({fr.sense})" if fr.sense and fr.sense != "mixed" else ""
            occ = f" [{fr.occasion}]" if fr.occasion else ""
            parts.append(f"  · {fr.fragment}{tag}{occ}")
    if selected_customs:
        parts.append("【文化小节】")
        for cu in selected_customs:
            ub = f"（{cu.used_by}）" if cu.used_by else ""
            parts.append(f"  · [{cu.type}]{ub} {cu.content}")
    parts.append("规则：上面是参考库——不必照抄，融入你觉得合适的 1-3 条让画面有质感。完全不用也行，但要保证本幕有自己的氛围细节。")
    return "\n".join(parts)
