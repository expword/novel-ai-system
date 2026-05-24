"""
GeographyDesignerAgent — Phase 1-F：自适应地理设计。

【先总后并发分架构】解决单次 LLM 调用输出量过大（~3000字）导致截断的问题：
  Step A（串行）：骨架 —— world_layout + protagonist_route + transport_modes + region_plan（只含 id/name/level/importance/parent_id）
  Step B1/B2/B3（并发）：按 importance 分档把各 region 填充详情
     · protagonist_active（3-6 个，每个 150-200 字）
     · occasional（4-8 个，每个 60-80 字）
     · background（4-8 个，每个 30 字）
  Step C（串行）：distances + world_map_desc

每一步输出 <2000 字，截断风险大幅下降。

不同题材的"地理"长得不一样：
  · 修真/玄幻：大陆 → 国家 → 州郡 → 城镇 → 秘境
  · 都市/现代：国家 → 省 → 市 → 区 → 街道/小区
  · 校园：学校 → 教学楼 → 班级 → 食堂宿舍
  · 星际/科幻：星系 → 星球 → 大陆/殖民地 → 城市/基地 → 空间站
  · 末世/废土：联邦废墟 → 隔离区 → 避难所 → 街区
  · 古代/宫斗：帝国 → 皇宫 → 宫殿 → 院落
  · 武侠：江湖 → 门派所在 → 州府 → 城池
  · 克苏鲁：现代国家 → 诡异事件地 → 非凡事件现场 → 隐秘古迹
  · 言情/纯情感：主角的生活圈（学校/公司/家/旅行目的地，可以极简 2-3 个节点）

交通方式也完全看题材：
  · 修真：步行/骑马/御剑/法船/传送阵
  · 现代：步行/自行车/汽车/高铁/飞机/邮轮
  · 末世：徒步/自行车/改装车/装甲车/能源稀缺的飞行器
  · 星际：飞船/跃迁引擎/虫洞/传送阵
  · 武侠：步行/骑马/轻功/快马/船只
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, Geography, GeoRegion, TransportMode, TravelDistance, RouteStage
from agents.concept_pitch import format_concept_brief, format_world_context_brief
from config import NUM_VOLUMES


SYSTEM = """你是适应多题材的地理/场景架构师。设计地理不是写散文，是填表格：让 writer 能精确查询"两地多远、怎么走、多久到"。

【核心原则】
1. 地理层级按题材选：
   · 修真玄幻：大陆→国家→州郡→城镇→秘境
   · 现代都市：国家→省→市→区→街道/小区/景点
   · 星际科幻：星系→星球→大陆/殖民地→城市→基地/空间站
   · 末世废土：联邦废墟→隔离区/军区→避难所/据点→街区
   · 古代宫廷：帝国→皇城→宫殿→院落
   · 校园：学校→楼宇→班级/社团/食堂
   · 武侠江湖：地区→门派所在地/江湖集散→城镇
   · 克苏鲁：现代地理 + 诡异事件地点
   · 言情/极简：只需主角生活圈的几个节点（家/学校或公司/咖啡馆/某次旅行地）
2. 交通方式按题材自由组合（步行/骑马/轻功/御剑/法船/汽车/高铁/飞机/跃迁飞船/传送门/传送阵...）。
   不要给都市文强塞"御剑"，不要给修真文写"高铁"。
3. 距离矩阵只覆盖**主角前几卷真正会去的地方**——不必面面俱到。
4. 言情/短篇/纯情感可以把地理设计得极简（就是主角的生活动线）。

输出严格 JSON。"""


def design_geography(state: NovelState) -> None:
    """
    三段式拆分（先总→并发分→总汇）：
      Step A: skeleton   —— 骨架：world_layout/protagonist_route/transport_modes/region_plan
      Step B1/B2/B3 并发 —— 按 importance 分档填充 region 详情
      Step C: distances  —— 基于骨架 region_id 生成距离矩阵 + world_map_desc
    """
    from utils.concurrency import parallel_map

    context = _build_geo_context(state)

    # ═══ Step A：骨架 ═══
    print("  [Geography] Step A: 生成骨架（world_layout / protagonist_route / transport_modes / region_plan）")
    skeleton = _dispatch_skeleton(state, context)
    if not skeleton or not skeleton.get("region_plan"):
        print("  [Geography] ✗ Step A 骨架生成失败——Geography 不可用")
        return

    region_plan = skeleton.get("region_plan", [])
    active_plan = [r for r in region_plan if r.get("importance") == "protagonist_active"]
    occ_plan    = [r for r in region_plan if r.get("importance") == "occasional"]
    bg_plan     = [r for r in region_plan if r.get("importance") == "background"]
    print(f"    骨架 region_plan：活跃 {len(active_plan)} / 途经 {len(occ_plan)} / 背景 {len(bg_plan)}")

    # ═══ Step B1/B2/B3 并发：按 importance 分档填充详情 ═══
    print("  [Geography] Step B 并发填充三档区划详情")

    def _run_active():
        if not active_plan:
            return []
        return _flesh_out_regions(state, context, skeleton, active_plan, importance="protagonist_active")

    def _run_occasional():
        if not occ_plan:
            return []
        return _flesh_out_regions(state, context, skeleton, occ_plan, importance="occasional")

    def _run_background():
        if not bg_plan:
            return []
        return _flesh_out_regions(state, context, skeleton, bg_plan, importance="background")

    bucket_results = parallel_map(
        fn=lambda f: f(),
        items=[_run_active, _run_occasional, _run_background],
        max_workers=3,
        label="Geography-Regions",
    )
    filled_active = bucket_results[0] or []
    filled_occ    = bucket_results[1] or []
    filled_bg     = bucket_results[2] or []

    # 合并 region detail 回 region_plan（以 region_id 为主键匹配，填不到的用 plan 占位）
    detail_by_id = {}
    for r in (filled_active + filled_occ + filled_bg):
        if isinstance(r, dict) and r.get("region_id"):
            detail_by_id[r["region_id"]] = r

    merged_regions = []
    missing_regions = []
    for plan_item in region_plan:
        rid = plan_item.get("region_id", "")
        detail = detail_by_id.get(rid)
        if detail:
            # plan 字段（level/parent_id/importance）作为保底，detail 覆盖
            merged = {**plan_item, **detail}
            merged_regions.append(merged)
        else:
            # detail 没填成功——仅保留骨架 plan 作为轻量占位
            missing_regions.append(rid)
            merged_regions.append(plan_item)
    if missing_regions:
        print(f"    ⚠ {len(missing_regions)} 个 region 详情填充失败（将使用骨架占位）：{missing_regions[:5]}")

    # ═══ Step C：distances + world_map_desc ═══
    print("  [Geography] Step C: 生成距离矩阵")
    distances_data = _dispatch_distances(state, context, skeleton, merged_regions)
    distances = distances_data.get("distances", []) if distances_data else []
    world_map_desc = (distances_data or {}).get("world_map_desc", "") or skeleton.get("world_map_desc", "")
    if not distances:
        print("    ⚠ 距离矩阵未生成——writer 跨区移动时只能粗估时间")

    # ═══ 写回 state ═══
    geo = Geography(
        regions=[GeoRegion(
            region_id=r.get("region_id", f"r_{i}"),
            name=r.get("name", ""),
            level=r.get("level", "城镇"),
            parent_id=r.get("parent_id", ""),
            description=r.get("description", ""),
            climate=r.get("climate", ""),
            products=r.get("products", ""),
            culture_notes=r.get("culture_notes", ""),
            notable_spots=r.get("notable_spots", []),
            importance=r.get("importance", "background"),
            detail_level=int(r.get("detail_level", 1)),
            protagonist_arc_note=r.get("protagonist_arc_note", ""),
            atmosphere=r.get("atmosphere", ""),
            key_scenes=r.get("key_scenes", []),
        ) for i, r in enumerate(merged_regions)],
        transport_modes=[TransportMode(
            name=m.get("name", ""),
            speed_description=m.get("speed_description", ""),
            realm_required=m.get("realm_required", ""),
            cost=m.get("cost", ""),
        ) for m in skeleton.get("transport_modes", []) if isinstance(m, dict)],
        distances=[TravelDistance(
            from_region=td.get("from_region", ""),
            to_region=td.get("to_region", ""),
            distance_desc=td.get("distance_desc", ""),
            travel_time_by_mode=td.get("travel_time_by_mode", {}),
        ) for td in distances if isinstance(td, dict)],
        world_map_desc=world_map_desc,
        world_layout=skeleton.get("world_layout", ""),
        protagonist_route=[RouteStage(
            volume=int(rs.get("volume", i + 1)),
            primary_region_id=rs.get("primary_region_id", ""),
            visited_region_ids=rs.get("visited_region_ids", []),
            arc_note=rs.get("arc_note", ""),
        ) for i, rs in enumerate(skeleton.get("protagonist_route", [])) if isinstance(rs, dict)],
    )
    state.geography = geo

    # 统计 + 打印
    active_count = sum(1 for r in geo.regions if r.importance == "protagonist_active")
    occ_count = sum(1 for r in geo.regions if r.importance == "occasional")
    bg_count = sum(1 for r in geo.regions if r.importance == "background")
    print(f"  ✓ 地理：{len(geo.regions)} 个区划（主角活跃:{active_count} / 途经:{occ_count} / 背景:{bg_count}）"
          f"，{len(geo.transport_modes)} 种交通，{len(geo.distances)} 条距离")
    if geo.world_layout:
        print(f"    天下布局：{geo.world_layout[:120]}...")
    if geo.protagonist_route:
        for rs in geo.protagonist_route[:3]:
            region_name = next((r.name for r in geo.regions if r.region_id == rs.primary_region_id), rs.primary_region_id)
            print(f"    路线·第{rs.volume}卷：→ {region_name} ({rs.arc_note[:40]})")
    for level in ("大陆", "国家", "州郡", "城镇"):
        items = [r for r in geo.regions if r.level == level]
        if items:
            print(f"    [{level}] {' / '.join(r.name for r in items[:6])}")
    if geo.transport_modes:
        print(f"    交通：{' / '.join(f'{m.name}({m.speed_description})' for m in geo.transport_modes[:4])}")


# ═══════════════════════════════════════════════════════
#  公共上下文
# ═══════════════════════════════════════════════════════

def _build_geo_context(state: NovelState) -> str:
    """四个子步骤共用的上下文。"""
    concept = format_concept_brief(state)
    realm_list = state.power_system.realm_list_str() if state.power_system else ""
    vol_brief = "\n".join(
        f"第{v.index}卷《{v.title}》：{v.theme}"
        + (f"｜对手：{v.volume_antagonist}" if v.volume_antagonist else "")
        + (f"｜关键事件：{' / '.join(v.key_events[:2])}" if v.key_events else "")
        for v in state.volumes
    )
    world_ctx = format_world_context_brief(state)
    return f"""为《{state.title}》（题材：{state.genre}）设计地理系统。

{world_ctx}

{concept}

世界观摘要：{state.world_setting[:300]}
力量/体系参考：{realm_list}

卷结构（判断主角路线的关键依据，共 {NUM_VOLUMES} 卷）：
{vol_brief}"""


# ═══════════════════════════════════════════════════════
#  Step A：骨架
# ═══════════════════════════════════════════════════════

def _dispatch_skeleton(state: NovelState, context: str) -> dict:
    """
    Step A 只生成骨架——world_layout + protagonist_route + transport_modes + region_plan。
    region_plan 里每个 region 只含 id/name/level/parent_id/importance（不填具体描述）。
    输出量约 1000-1500 字，留足空间给下游并发填充。
    """
    # Phase 2.1:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    prompt = f"""{feedback_prefix}{context}

═══ 任务：只生成骨架（不要在本步填写 region 的详细描述）═══

你要产出 4 样东西：world_layout / protagonist_route / transport_modes / region_plan。
**region_plan 里每个 region 只列 region_id/name/level/parent_id/importance**——具体描写下一步再并发生成。

【1. world_layout（200-300字）】
天下布局：俯瞰整个世界的格局，让读者脑海里有张地图。
  · 修真/玄幻：中土几国 + 边陲蛮荒 + 海外仙山 + 域外异族
  · 都市：华东/华北/华南 + 海外（主角主要去的区域）+ 次要区域
  · 末世：联邦主城群 + 中间缓冲带 + 变异爆发源
  · 宫斗：皇城内 + 边疆 + 民间三方
让读者感到世界"大"——主角舞台只是一隅，天下还有未到达的远方。

【2. protagonist_route（{NUM_VOLUMES} 条，每卷一条）】
根据卷结构规划主角每卷在哪：
  - primary_region_id（本卷主要活跃区）
  - visited_region_ids（本卷访问顺序，2-4 个 region_id）
  - arc_note（地理弧线 40 字）

【3. transport_modes（2-6 种，按题材选）】
speed_description 用题材合适的表述（如"日行百里"/"时速 80 公里"/"光年跃迁"）。

【4. region_plan（15-25 个 region）——只含标识信息，不描述】
必须严格按 importance 分档：
  · protagonist_active：3-6 个（主角会深度描写的地方：起点、主要舞台、决战地）
  · occasional：4-8 个（主角路过或短暂停留）
  · background：4-8 个（天下布局里提及但主角不会去）
每个 region 只填：
  - region_id（如 "r_01"、"r_mc_start"——必须唯一，protagonist_route 要用）
  - name（贴题材的地名，不要占位"起点城"）
  - level（按题材：大陆/国家/省/市/区/学校/星球/避难所/皇城...）
  - parent_id（上级 region_id，顶级填空）
  - importance（"protagonist_active" / "occasional" / "background"）

【5. world_map_desc（可选，100字综合地图描述——向后兼容字段）】

输出 JSON：
{{
  "world_layout": "天下布局（200-300字）",
  "protagonist_route": [
    {{"volume": 1, "primary_region_id": "r_xx", "visited_region_ids": ["r_aa", "r_bb"], "arc_note": "40字"}}
  ],
  "transport_modes": [
    {{"name": "...", "speed_description": "...", "realm_required": "", "cost": "..."}}
  ],
  "region_plan": [
    {{"region_id": "r_01", "name": "...", "level": "...", "parent_id": "", "importance": "protagonist_active"}}
  ],
  "world_map_desc": "100字"
}}
"""
    example = (
        '{"world_layout":"...","protagonist_route":[{"volume":1,"primary_region_id":"r_01",'
        '"visited_region_ids":["r_01"],"arc_note":"..."}],'
        '"transport_modes":[{"name":"...","speed_description":"...","realm_required":"","cost":""}],'
        '"region_plan":[{"region_id":"r_01","name":"...","level":"...","parent_id":"",'
        '"importance":"protagonist_active"}],"world_map_desc":"..."}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["region_plan"],
        list_candidates=["region_plan"],
        min_items=10,  # 至少 10 个 region
        max_retries=4, temperature=0.72,
        agent_name="GeographyDesigner/Skeleton",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  Step B：按 importance 分档填充 region 详情
# ═══════════════════════════════════════════════════════

_DETAIL_GUIDE = {
    "protagonist_active": {
        "detail_level": 3,
        "desc_len": "150-200 字",
        "spots_count": "5-8 个",
        "scenes_count": "3-5 个",
        "emphasis": (
            "必须包含：街巷氛围 / 地标建筑 / 居民特征 / 日常画面。"
            "atmosphere 明确（如'市井烟火气中藏暗流'/'肃杀冷峻'）。"
            "protagonist_arc_note 要说清主角在此的时间段。"
        ),
    },
    "occasional": {
        "detail_level": 2,
        "desc_len": "60-80 字",
        "spots_count": "2-3 个",
        "scenes_count": "1-2 个",
        "emphasis": (
            "基本画面 + 一个记忆点即可。"
            "atmosphere 可短。"
            "protagonist_arc_note 简短说明（如'主角第 2 卷途经'）。"
        ),
    },
    "background": {
        "detail_level": 1,
        "desc_len": "30 字左右",
        "spots_count": "0-1 个",
        "scenes_count": "0 个",
        "emphasis": (
            "只画轮廓——叫什么/在哪/什么风格。"
            "notable_spots/key_scenes/atmosphere/protagonist_arc_note 可留空。"
        ),
    },
}


def _flesh_out_regions(
    state: NovelState,
    context: str,
    skeleton: dict,
    plan_items: list[dict],
    importance: str,
) -> list[dict]:
    """
    给一档 importance 的 regions 批量填充详情。
    输入 plan_items 只含 id/name/level/parent_id/importance。
    输出完整 GeoRegion dict 列表（含 description/climate/products/culture_notes/notable_spots/atmosphere/key_scenes/protagonist_arc_note/detail_level）。
    """
    if not plan_items:
        return []

    guide = _DETAIL_GUIDE[importance]
    plan_brief = "\n".join(
        f"  · region_id={p.get('region_id','')}｜name={p.get('name','')}｜level={p.get('level','')}｜parent_id={p.get('parent_id','')}"
        for p in plan_items
    )
    world_layout_hint = (skeleton.get("world_layout") or "")[:300]
    route_brief = ""
    if importance == "protagonist_active":
        lines = []
        for rs in (skeleton.get("protagonist_route") or [])[:8]:
            if isinstance(rs, dict):
                lines.append(
                    f"  第{rs.get('volume','?')}卷 primary={rs.get('primary_region_id','')} "
                    f"visited={rs.get('visited_region_ids', [])} arc={rs.get('arc_note','')[:40]}"
                )
        route_brief = "\n【主角路线（用于判断活跃区在哪个卷登场）】\n" + "\n".join(lines) if lines else ""

    prompt = f"""{context}

═══ 天下布局（Step A 已生成）═══
{world_layout_hint}
{route_brief}

═══ 任务：为以下 {len(plan_items)} 个 `{importance}` 级别 region 填充详情 ═══

**只处理这些 region_id，不要新增/删减**。每个 region_id 必须一对一输出完整对象，保持顺序。

Region 列表（骨架，只有 id/name/level/parent_id）：
{plan_brief}

【本档详写要求（importance = {importance}，detail_level = {guide['detail_level']}）】
  · description：{guide['desc_len']}
  · notable_spots：{guide['spots_count']}
  · key_scenes：{guide['scenes_count']}
  · {guide['emphasis']}

输出 JSON：
{{
  "regions": [
    {{
      "region_id": "必须和输入 plan 中一致",
      "name": "和 plan 一致",
      "level": "和 plan 一致",
      "parent_id": "和 plan 一致",
      "importance": "{importance}",
      "detail_level": {guide['detail_level']},
      "description": "（按本档长度要求）",
      "climate": "气候（短）",
      "products": "物产/产业（短）",
      "culture_notes": "风土/文化（短）",
      "atmosphere": "氛围（20字）",
      "notable_spots": ["..."],
      "key_scenes": ["..."],
      "protagonist_arc_note": "主角在此的时间段"
    }}
  ]
}}
"""
    example = (
        '{"regions":[{"region_id":"r_01","name":"...","level":"...","parent_id":"",'
        f'"importance":"{importance}","detail_level":{guide["detail_level"]},'
        '"description":"...","climate":"...","products":"...","culture_notes":"...",'
        '"atmosphere":"...","notable_spots":[],"key_scenes":[],"protagonist_arc_note":"..."}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["regions"],
        list_candidates=["regions"],
        min_items=max(1, len(plan_items) - 1),  # 允许丢一个
        max_retries=3, temperature=0.7,
        agent_name=f"GeographyDesigner/Regions-{importance}",
        example_schema=example,
        empty_ok=True,
    )
    if not data:
        return []
    return [r for r in pick_list(data, "regions", "items") if isinstance(r, dict)]


# ═══════════════════════════════════════════════════════
#  Step C：距离矩阵
# ═══════════════════════════════════════════════════════

def _dispatch_distances(
    state: NovelState,
    context: str,
    skeleton: dict,
    regions: list[dict],
) -> dict:
    """
    Step C：基于已定的 region 列表 + transport_modes 生成距离矩阵。
    优先覆盖主角路线上相邻区，不必面面俱到。
    """
    active_regions = [r for r in regions if r.get("importance") == "protagonist_active"]
    occ_regions    = [r for r in regions if r.get("importance") == "occasional"]
    region_brief_active = "\n".join(
        f"  · {r.get('region_id','')}｜{r.get('name','')}（{r.get('level','')}）"
        for r in active_regions
    )
    region_brief_occ = "\n".join(
        f"  · {r.get('region_id','')}｜{r.get('name','')}（{r.get('level','')}）"
        for r in occ_regions[:8]
    )
    tm_names = [m.get("name", "") for m in skeleton.get("transport_modes", []) if isinstance(m, dict)]
    route_region_ids = []
    for rs in skeleton.get("protagonist_route", []) or []:
        if isinstance(rs, dict):
            if rs.get("primary_region_id"):
                route_region_ids.append(rs["primary_region_id"])
            for rid in (rs.get("visited_region_ids") or []):
                route_region_ids.append(rid)

    prompt = f"""{context}

═══ 任务：基于已确定的 region + transport_modes 生成距离矩阵 ═══

已有交通方式：{' / '.join(tm_names) if tm_names else '（无）'}

主角路线经过的 region_id（按顺序）：
{route_region_ids[:20]}

主角活跃区：
{region_brief_active}

主要途经区：
{region_brief_occ}

生成 5-12 条 distances，**优先覆盖主角路线上相邻两区的距离**（如第1卷起点→第1卷终点→第2卷起点）。
每条 distances.travel_time_by_mode 的 key 必须是上面列出的交通方式之一。
额外再给一段 world_map_desc（100字综合地图描述，可选，和 world_layout 错开视角）。

输出 JSON：
{{
  "distances": [
    {{
      "from_region": "region_id 或名字",
      "to_region": "region_id 或名字",
      "distance_desc": "三千里 / 一个月脚程 / 300公里 ...",
      "travel_time_by_mode": {{"骑马": "7天", "御剑": "半日"}}
    }}
  ],
  "world_map_desc": "100字综合地图描述"
}}
"""
    example = (
        '{"distances":[{"from_region":"r_01","to_region":"r_02","distance_desc":"...",'
        '"travel_time_by_mode":{"...":"..."}}],"world_map_desc":"..."}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["distances"],
        list_candidates=["distances"],
        min_items=3,
        max_retries=3, temperature=0.68,
        agent_name="GeographyDesigner/Distances",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}
