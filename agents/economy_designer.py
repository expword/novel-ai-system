"""
EconomyDesignerAgent — Phase 1-H：自适应经济系统。

经济在不同题材里的形式：
  · 修真：灵石（下/中/上品）+ 丹药 + 法器 + 功法
  · 武侠：白银/黄金/银票 + 兵器
  · 都市/职场：人民币/美元/股票/虚拟货币
  · 末世：物资票/罐头/弹药/燃料
  · 末日/废土：以物易物 + 神秘货币
  · 星际：信用点/合金/能源块
  · 古代/宫斗：白银/黄金/月例银子
  · 校园：人民币/校园卡/积分
  · 系统流：系统积分 + 任务奖励
  · 游戏异界：金币/水晶/神器
  · 言情/纯情感：极简——可能完全不需要

这个 agent 根据 power_flow / genre 决定货币类型和物价语境。
"""
from json_utils import request_json, pick_list
from state import NovelState, Economy, Currency, PriceAnchor, WealthTierPoint
from agents.concept_pitch import format_concept_brief, format_world_context_brief
from config import NUM_VOLUMES


SYSTEM = """你是各类小说世界的经济设计师——给作家一把"价格尺"，让"一千两""一万灵石""一万信用点""5000 块"在读者脑里有具体感受。

【核心原则】
1. 货币按题材选：
   · 修真：下/中/上品灵石 + 丹药 + 法器
   · 武侠：白银/黄金/银票
   · 都市：人民币/美元/股票
   · 末世：物资票/罐头/弹药
   · 星际：信用点/合金/能源
   · 古代：铜钱/白银/黄金/银票
   · 校园：人民币/校园卡
   · 系统流：积分/任务奖励
   · 游戏异界：金币/水晶
2. 物价锚点跨档次：平民日常 / 入门 / 珍稀 / 逆天 ——按题材填具体物品
3. 主角财富曲线随阶段推进，不要第一卷就富可敌国
4. 言情/纯情感/校园短篇可以极简（甚至 currencies=[] 只填物价锚点）

输出严格 JSON。"""


def design_economy(state: NovelState) -> None:
    concept = format_concept_brief(state)
    realm_plan = state.power_system.protagonist_realm_plan if state.power_system else {}
    realm_plan_brief = " → ".join(
        f"第{v}卷末:{r}" for v, r in sorted(realm_plan.items())
    ) if realm_plan else "（无境界规划）"

    world_ctx = format_world_context_brief(state)
    prompt = f"""
为《{state.title}》设计经济系统。

{world_ctx}

{concept}

世界观：{state.world_setting[:200]}
力量/体系：{state.power_system_brief() if state.power_system else '（无）'}
主角阶段推进：{realm_plan_brief}

═══ 要求 ═══
1. **currencies**（2-6 种，贴题材）：
   · 修真：下/中/上品灵石等
   · 武侠：铜钱/白银/黄金/银票
   · 都市：人民币/美元/股票
   · 末世：物资票/罐头/弹药
   · 星际：信用点/合金/能源
   · 系统流：系统积分/任务点
   · 言情短篇：可能只需 1 种（现代货币）甚至留空
   rank 从小到大表示越稀贵；exchange_to_base 是相对 rank=1 的兑换比率。

2. **price_anchors**（4-10 个）——跨档次覆盖，举例按题材：
   · 修真：饭/布衣/法器/丹药/传承
   · 武侠：饭/客栈/兵器/秘籍
   · 都市：一顿快餐/房租/车/房/名表
   · 末世：一块面包/一把刀/一枪/一辆车/避难所床位
   · 星际：一餐/能源棒/武器/飞船/战舰
   · 校园：食堂饭/教材/电子产品/品牌包/留学费
   tier 字段用"平民日常/入门/珍稀/逆天"或你认为贴切的分级。

3. **protagonist_wealth_curve**：主角每卷末的财富状态。{NUM_VOLUMES} 卷都要有。
   tier 推荐："赤贫"|"温饱"|"小康"|"富足"|"巨富"|"富可敌国"（题材不同具体含义不同）

4. **trade_notes**：特殊经济现象（30字，按题材写——
   修真可写灵石通缩；都市可写房价泡沫；末世可写物资稀缺；星际可写星际贸易战；
   言情可空）

输出 JSON：
{{
  "currencies": [
    {{"name":"（贴题材）","rank":1,"exchange_to_base":1,"notes":"用途说明"}}
  ],
  "price_anchors": [
    {{"item":"一顿便饭","price":"10文铜钱","tier":"平民日常"}}
  ],
  "protagonist_wealth_curve": [
    {{"volume":1,"tier":"赤贫","description":"身无分文，靠给人跑腿糊口"}}
  ],
  "trade_notes": "..."
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["currencies"],
        min_items=2,
        max_retries=4, temperature=0.68, agent_name="EconomyDesigner",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ EconomyDesigner 跳过（LLM 重试失败）——后续描写金钱无锚点参考")
        return

    eco = Economy(
        currencies=[Currency(
            name=c.get("name", ""),
            rank=int(c.get("rank", 1)),
            exchange_to_base=int(c.get("exchange_to_base", 1)),
            notes=c.get("notes", ""),
        ) for c in pick_list(data, "currencies")],
        price_anchors=[PriceAnchor(
            item=a.get("item", ""),
            price=a.get("price", ""),
            tier=a.get("tier", ""),
        ) for a in data.get("price_anchors", [])],
        protagonist_wealth_curve=[WealthTierPoint(
            volume=int(w.get("volume", 1)),
            tier=w.get("tier", ""),
            description=w.get("description", ""),
        ) for w in data.get("protagonist_wealth_curve", [])],
        trade_notes=data.get("trade_notes", ""),
    )
    state.economy = eco

    print(f"  ✓ 经济系统：{len(eco.currencies)} 种货币｜{len(eco.price_anchors)} 个物价锚点｜主角财富曲线 {len(eco.protagonist_wealth_curve)} 卷")
    if eco.currencies:
        cs = " / ".join(f"{c.name}(1={c.exchange_to_base})" for c in eco.currencies)
        print(f"    货币：{cs}")
    if eco.protagonist_wealth_curve:
        curve = " → ".join(
            f"V{w.volume}:{w.tier}" for w in sorted(eco.protagonist_wealth_curve, key=lambda x: x.volume)
        )
        print(f"    主角财富：{curve}")
