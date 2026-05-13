"""
RealmDesignerAgent — 自适应的"成长阶梯"设计器。

不同题材需要不同类型的阶梯：
  · realms 修炼境界     —— 玄幻/仙侠/武侠
  · skill_tiers 技能段位 —— 科幻/灵异/超能/职业文
  · social_rank 社会地位 —— 都市/职场/商战/宫斗
  · progression_arc 人生阶段 —— 现实/文艺/成长类
  · none 无体系          —— 纯情感/短篇/特殊题材

本 agent 会根据题材 + 立项意图自动判断用哪一种，然后填 realms 列表
（realms 字段复用，但对应内容按 system_type 调整语义）。

核心原则：
1. 【单主角铁律】所有设计围绕主角——主角必须有"机遇型"核心特质（signature），他人的能力/地位是对比/陪衬。
2. 【成长阶梯与剧情对齐】主角的跨阶时机配合卷级高潮；每次跨阶有具体触发事件。
3. 【特殊能力/机遇】仅当 system_type 支持超自然元素时才生成（realms/skill_tiers）；社会向题材可能根本没有"能力"概念。
"""
import json
from json_utils import repair_json, safe_parse, pick_list, request_json
from llm import system_user
from state import NovelState, PowerSystem, Realm, SpecialAbility, AbilityAwakeningStage, PowerMechanic
from config import NUM_VOLUMES


SYSTEM = """你是网文力量体系/成长体系的架构师，熟悉市面上各种流派的标准玩法。

【你掌握的流派 & 典型机制】（power_flow 字段填其中之一，或自创贴合的）：

一、【传统修炼流】（修真/仙侠/玄幻）
  · realms：炼气→筑基→金丹→元婴→化神→炼虚→合体→大乘→渡劫 等
  · rank_unit：境、层、重、品
  · mechanics：闭关/渡劫/吞噬/传承/飞升
  · resources：灵石/丹药/法器/功法/天材地宝

二、【武侠流】
  · realms：外家→内家→先天→宗师→大宗师→陆地神仙
  · rank_unit：境、重
  · mechanics：武学秘籍/内力转化/门派传承/江湖切磋
  · resources：秘籍/神兵/内丹/奇药

三、【异能觉醒流】（都市异能/进化/全球觉醒）
  · realms：D→C→B→A→S→SS→SSS 级 / 凡人→觉醒者→异能者→超凡者
  · rank_unit：级
  · mechanics：觉醒仪式/基因进化/能力突变/失控代价
  · resources：进化结晶/能量矿/强化剂

四、【系统流】（签到/金手指）
  · realms：系统赋予的等级 1→10 或 "学徒→宗师"
  · rank_unit：级/阶/点
  · mechanics：【签到】每日登录奖励、【任务系统】主线+支线、【商城】积分兑换、【抽奖】、【属性面板】
  · resources：系统积分/签到点/任务奖励
  · 特殊：系统本身就是外挂，机制重于境界

五、【无限流】（无限恐怖式）
  · realms：轮回者等级 1-10 号 / 青铜→白银→黄金→钻石→王者
  · rank_unit：级/号
  · mechanics：【副本】恐怖片场/历史战场/神话世界、【主神评分】S/A/B/C/D、【死亡惩罚】扣分/清零、【奖励商城】、【队伍系统】
  · resources：主神点数/副本奖励
  · 特殊：副本间休整 + 跨副本携带

六、【克苏鲁/诡秘之主序列流】
  · realms：序列 9→0（数字越小越强）
  · rank_unit：序列
  · mechanics：【途径】23 条 / 【序列跃升仪式】、【非凡特性】扮演通过、【神名封印】、【失控癫狂】
  · resources：魔药配方/非凡物品/灵性之物
  · 特殊：每个序列有独特名字，不是简单数字递增

七、【游戏异界流】
  · realms：等级 1-100 + 职业（战士/法师/盗贼）+ 段位
  · rank_unit：级、阶
  · mechanics：【职业转职】、【副本】、【装备品质】白/绿/蓝/紫/橙/红/神、【技能树】、【PVP/PVE】
  · resources：金币/经验/装备/材料

八、【科技赛博流】
  · realms：T1→T9 技术等级 / 探索级→工业级→行星级→恒星级→星系级
  · rank_unit：级、代
  · mechanics：【技术树】、【纳米改造】、【机甲】、【AI 意识】、【量子突破】
  · resources：能源晶体/稀有金属/蓝图/AI芯片

九、【魔法奇幻流】
  · realms：魔法环阶 1→9 环 / 学徒→法师→大法师→贤者
  · rank_unit：环、阶
  · mechanics：【魔法回路】、【咒文研究】、【魔法学院】、【元素亲和】
  · resources：魔杖/法袍/卷轴/魔晶

十、【诡异怪谈/SCP流】
  · realms：收容等级 安全/欧几里得/基特尔/塔玛斯加 或 异常级别
  · rank_unit：级
  · mechanics：【异常规则】、【收容突破】、【基金会权限】、【记忆删除】
  · resources：记忆药剂/抗性物品/档案

十一、【驯兽/宠物流】
  · realms：宠物品阶 凡兽→灵兽→妖兽→神兽→仙兽
  · rank_unit：阶、品
  · mechanics：【契约】、【进化】、【亲密度】、【宠物技能】、【变异】
  · resources：驯兽珠/宠物丹/血脉之物

十二、【料理/职业向流】（美食/医道/制器）
  · realms：职业段位 学徒→师傅→大师→宗师→神匠
  · rank_unit：段、品
  · mechanics：【评级考试】、【传承秘方】、【作品等级】、【行业大会】
  · resources：稀有食材/传承手札/工匠工具

十三、【商战/财富流】
  · realms：财富段位 摆摊→小老板→千万→亿万→富可敌国
  · rank_unit：档
  · mechanics：【行业规则】、【人脉资源】、【并购】、【舆论操控】、【政商关系】
  · resources：资金/信息/人脉/品牌

十四、【宫斗/宅斗流】
  · realms：位份 选侍→答应→常在→贵人→嫔→妃→贵妃→皇后
  · rank_unit：位/品
  · mechanics：【恩宠】、【孕育】、【党羽】、【算计】、【告密】
  · resources：圣宠/母家/位份/积蓄

十五、【职场/都市流】
  · realms：职员→组长→经理→总监→VP→CEO
  · rank_unit：级
  · mechanics：【KPI】、【人脉】、【项目】、【行业风口】、【办公室政治】
  · resources：资源/客户/信息/资历

十六、【国运/文明流】
  · realms：文明阶段 部落→城邦→王朝→帝国→文明/工业→信息→星际
  · rank_unit：代
  · mechanics：【科技树】、【外交】、【战争】、【资源开采】、【民心】
  · resources：人口/领土/科技/资源

十七、【人生现实流】
  · realms：人生阶段 少年→求学→初入社会→而立→不惑→知天命
  · rank_unit：（通常无单位）
  · mechanics：无特殊机制，靠心智成熟和关系演变
  · resources：无

十八、【纯情感/推理/短篇流】
  · realms：[]
  · rank_unit：无
  · mechanics：[]
  · 靠人物心路/解谜节奏/情感曲线驱动

═══ 补充：常与上述流派叠加/正交的"金手指/叙事角度" ═══
以下是"叙事切入角度"，不是完整的力量体系——但它们常常是主角的核心差异点，经常和上面的流派叠加出现：
  · 穿越流：主角从现代/异世界穿越到故事世界。可以"纯穿越"（没有任何金手指，靠见识和努力），也可以穿越+X
  · 重生流：同一世界重来一次，带前世记忆
  · 带系统流：主角随身系统（常与任意流派结合——修真+系统、都市+系统、种田+系统）
  · 带空间流：主角有个随身异能空间（灵田/仓库/位面）
  · 带印章/法宝流：主角有神秘法器/印章/传承残魂相伴
  · 附身流：附身到古人/原著角色/某个特定人物
  · 同人/综漫/综武流：进入某个既存 IP 的世界
  · 快穿流：不断穿越多个世界执行任务（每个任务世界可能用不同力量体系）
  · 末日/末世流：灾变后的生存游戏（丧尸/辐射/外星入侵）
  · 废土流：文明崩塌后的荒凉世界
  · 救世流：主角天命所归拯救世界
  · 反派流：主角就是反派
  · 带崽/养成流：带着孩子或小动物一起冒险
  · 年代文：写 70/80/90 年代生活
  · 直播/综艺流：靠直播/综艺节目影响世界

这些并不都是"力量体系"流派——但它们决定了体系该怎么塞。比如"纯穿越到古代种田"可能完全不需要 realms（全靠农学知识+人生阅历），"带系统修真"要 realms+system_mechanics 两手抓。

═══ 关键认知 ═══
- ★ 以上 18+14 种流派是**参考清单**，不是全部——实际作品经常是**多种流派混合**，或**传统流派的变体**
- ★ power_flow 字段填**作者实际想写的流派描述**，可以是"穿越+纯种田+无金手指" 或 "修真+系统+快穿" 这种组合式
- ★ 如果作品核心不是"升级"而是"经历/情感/探索"，realms **可以完全为空**——不要为了填而填
- ★ 有些作品主角只有一个金手指（比如"随身空间"或"签到系统"或"读心术"），这时 realms 可能很短或留空，但 special_mechanics 要详细写清金手指规则
- ★ 常见作品就是一种简化：主角穿越过来，没有大外挂，就是普通人靠努力——这种 power_flow="穿越+普通人成长"，realms 可能就是人生的几个关键阶段

═══ 你的工作 ═══
读懂作者题材 + 立项意图 → 判断这本书属于（哪几种流派的组合）→ 按那个组合的核心特征填字段。

流程：
1. power_flow 字段：用**自然语言描述**作者想写的东西，可以是单一流派（"修真流"）或混合（"穿越+修真+系统"）或自创（"古代种田+权谋"）
2. system_type 字段：归一化成 realms/skill_tiers/social_rank/progression_arc/none 之一（作为下游代码分流用；若实在不符可自由取名）
3. system_name：书内体系名（贴合流派，如"九天玄道"/"诡秘序列"/"无尽签到系统"/"家族小说的宅斗等级"）
4. system_nature：一句话定性
5. rank_unit：等级单位名——境/级/段/阶/序列/环/层/品/星等，**没有层级就填空字符串**
6. has_hierarchy：true=有明确等级，false=无明确等级（纯穿越无外挂/纯情感/仅有金手指但无境界）
7. realms：阶梯列表——按流派惯例命名，**核心不在升级的作品可以为空或只给 2-3 个人生节点**
8. special_mechanics：流派专属机制 0-5 条（系统流/无限流/诡秘流必须写清；纯现实类可为空）
   若主角有"金手指"（系统/空间/印章/读心等），金手指机制必须写在这里，写得具体
9. cultivation_resources：资源 0-8 条（对所有流派通用；纯情感类可为空）
10. special_abilities：先留空 []，后续 Phase 2C 再单独设计

★ 避免的陷阱：
- 不要给所有书都硬塞"炼气→筑基..."境界链
- 不要把系统流的核心"签到/任务/商城"漏掉去写境界
- 不要给纯穿越/种田/言情/推理文强塞"战斗力"字段
- 不要把"金手指"和"境界体系"混为一谈——金手指是独立的机制，进 special_mechanics
- 诡秘序列流的数字是反向的（9→0），不要搞错
- 混合流派时，哪个主哪个辅要分清楚

输出严格 JSON。"""


def design_realm_system(state: NovelState) -> None:
    """设计完整境界体系，写入 state.power_system。"""

    total_chapters = sum(v.total_chapters for v in state.volumes) if state.volumes else 400
    volumes_desc = "\n".join(
        f"第{v.index}卷《{v.title}》主题：{v.theme}" for v in state.volumes
    ) if state.volumes else f"共{NUM_VOLUMES}卷"

    # 立项意图——让 system_type 判断有依据
    intent = state.creative_intent
    intent_block = ""
    if intent.analyzed and intent.tone_summary:
        intent_block = f"\n创作意图整体气质：{intent.tone_summary}\n"

    prompt = f"""
为《{state.title}》设计"力量/成长体系"。

题材：{state.genre}
主题：{state.theme}
总卷数：{NUM_VOLUMES}
总章节：约{total_chapters}章
{intent_block}
各卷主题：
{volumes_desc}

═══ 第一步：读懂作者要写什么 ═══
先判断这本书属于（可多选/混合）：
  · 主流派（修真/武侠/异能/系统流/无限流/诡秘序列/游戏/科技/魔法/诡异/驯兽/料理/商战/宫斗/职场/国运/人生/纯情感）
  · 叙事切入（穿越/重生/带系统/带空间/带印章/附身/同人/快穿/末日/救世/反派/带崽/年代文/直播...）
  · 是否有明确"金手指"（空间/系统/印章/读心/签到/任务面板等随身外挂）
  · 是否真的需要"等级阶梯"（有些作品核心是经历/情感，不需要）

然后决定：
  · power_flow：用自然语言描述（可混合，如 "穿越+修真+系统" 或 "纯穿越+古代种田+无金手指" 或 "都市异能觉醒"）
  · system_type：归一化到 realms / skill_tiers / social_rank / progression_arc / none 之一（给下游代码用）
  · has_hierarchy：本书是否有明确的等级/层级

═══ 第二步：填 realms 和 special_mechanics ═══
realms 列表按流派惯例命名：
  · 修真/玄幻：6-9 个大境界，每个带小境界
  · 武侠：4-7 个武学境界
  · 异能：D→C→B→A→S→SS 之类 4-7 级
  · 系统流：若系统给了等级就填（1-10 级等），否则可为空
  · 无限流：轮回者评级 4-7 档
  · 诡秘序列流：**序列 9→0**（反向编号，9 最弱）
  · 游戏流：职业等级+段位
  · 科技：T1→T9 技术级
  · 魔法：1-9 环 或 学徒→大法师
  · 诡异/SCP：安全→欧几里得→基特尔 之类收容级
  · 驯兽：宠物品阶 凡→灵→妖→神 之类
  · 料理/职业：学徒→宗师
  · 商战：财富段
  · 宫斗：位份
  · 职场：职级
  · 国运：文明阶段
  · 人生：3-5 个人生阶段
  · 纯情感/推理/极简穿越：realms = []
  · 带金手指无境界：realms = [] 或只给 2-3 节点
  · 混合流派：按主流派命名

special_mechanics 按流派填（这个比 realms 更能体现流派特征）：
  · 系统流：必填签到/任务/商城/属性面板
  · 无限流：必填副本/主神评分/死亡惩罚
  · 诡秘流：必填序列跃升仪式/非凡特性
  · 游戏流：必填副本/装备品质/转职
  · 驯兽流：必填契约/进化/亲密度
  · 金手指：必须详细写清楚金手指规则（空间/印章/读心/签到/回溯等）
  · 传统修真：可选闭关/渡劫/吞噬/传承
  · 纯现实/情感：可为空 []

═══ 第三步：填其他字段 ═══
- system_name：书内体系名，贴合流派
- system_nature：一句话定性
- rank_unit：等级单位（境/级/段/阶/序列/环/层/品等）；无层级就空
- cultivation_resources：资源 0-8 条
- protagonist_realm_plan：主角每卷末到达的阶段/位置（对所有类型都尽量填）
- special_abilities：先留空 []（Phase 2C 再设计）

输出 JSON：
{{
  "power_flow": "（自然语言描述作者要写的流派组合）",
  "system_type": "realms|skill_tiers|social_rank|progression_arc|none",
  "has_hierarchy": true或false,
  "system_name": "（书内体系名）",
  "system_nature": "（一句话定性）",
  "system_description": "（100字）",
  "rank_unit": "（等级单位；无则空）",
  "realms": [
    {{
      "index": 1,
      "name": "...",
      "sub_realms": ["..."],
      "power_description": "此阶段主要能力/担当/生活状态（50字）",
      "breakthrough_condition": "进阶条件（40字）",
      "resource_requirement": "所需资源（40字）",
      "average_time": "普通人跨越此阶段所需时间",
      "rarity": "此阶段稀有度/人群占比"
    }}
  ],
  "special_mechanics": [
    {{
      "name": "机制名（如'签到系统'/'主神副本'/'序列跃升'/'随身空间'）",
      "description": "机制是什么（50字）",
      "protagonist_usage": "主角怎么用（40字）",
      "narrative_impact": "如何驱动剧情（30字，可选）"
    }}
  ],
  "cultivation_resources": [
    {{"name": "...", "rarity": "...", "effect": "..."}}
  ],
  "protagonist_realm_plan": {{
    "1": "第1卷末主角所在阶段",
    "2": "第2卷末..."
  }}
}}
"""
    example = (
        '{"power_flow":"修真+系统流","system_type":"realms","has_hierarchy":true,'
        '"system_name":"...","system_description":"...","system_nature":"...","rank_unit":"境",'
        '"realms":[{"index":1,"name":"...","sub_realms":[],"power_description":"...",'
        '"breakthrough_condition":"...","resource_requirement":"...","average_time":"...","rarity":"..."}],'
        '"special_mechanics":[{"name":"签到系统","description":"...","protagonist_usage":"...","narrative_impact":""}],'
        '"cultivation_resources":[],"protagonist_realm_plan":{"1":"..."}}'
    )

    def _validator(d):
        if not isinstance(d, dict):
            return False, "不是对象"
        if not d.get("system_name"):
            return False, "缺 system_name"
        stype = d.get("system_type", "realms")
        has_hier = d.get("has_hierarchy", True)
        realms = d.get("realms", [])
        mechanics = d.get("special_mechanics", [])
        # 校验逻辑：
        # - 如果 has_hierarchy=true 且 system_type != none，realms 至少 3
        # - 如果 has_hierarchy=false，realms 可空（但最好有 mechanics）
        # - none → 啥都可空
        if stype == "none":
            return True, ""
        if not has_hier:
            # 纯金手指/纯情感型允许无 realms，但应有一些 mechanics 或资源
            return True, ""
        if not isinstance(realms, list) or len(realms) < 3:
            return False, f"system_type={stype}, has_hierarchy=true 时 realms 至少 3 项"
        return True, ""

    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["system_name", "system_type"],
        custom_validator=_validator,
        max_retries=5, temperature=0.75, agent_name="RealmDesigner",
        example_schema=example,
        empty_ok=True,
    )
    if not data:
        raise RuntimeError("RealmDesigner 彻底失败——力量体系是下游 Phase 1-A2/1-F 的基础，必须重试。请检查 LLM 连接或简化 intent")

    realms_data = pick_list(data, "realms", "realm_list", "items")

    realms = [
        Realm(
            index=r.get("index", i + 1),
            name=r.get("name", f"境界{i+1}"),
            sub_realms=r.get("sub_realms", []),
            power_description=r.get("power_description", ""),
            breakthrough_condition=r.get("breakthrough_condition", ""),
            resource_requirement=r.get("resource_requirement", ""),
            average_time=r.get("average_time", ""),
            rarity=r.get("rarity", ""),
        )
        for i, r in enumerate(realms_data)
    ]
    # 解析 special_mechanics
    mechanics = [
        PowerMechanic(
            name=m.get("name", ""),
            description=m.get("description", ""),
            protagonist_usage=m.get("protagonist_usage", ""),
            narrative_impact=m.get("narrative_impact", ""),
        )
        for m in data.get("special_mechanics", []) if m.get("name")
    ]

    state.power_system = PowerSystem(
        system_name=data.get("system_name", "未命名体系"),
        system_description=data.get("system_description", ""),
        realms=realms,
        special_abilities=[],  # 特殊能力在 Phase 2C 单独设计
        cultivation_resources=data.get("cultivation_resources", []),
        protagonist_realm_plan={int(k): v for k, v in data.get("protagonist_realm_plan", {}).items()},
        system_type=data.get("system_type", "realms"),
        system_nature=data.get("system_nature", ""),
        power_flow=data.get("power_flow", ""),
        rank_unit=data.get("rank_unit", ""),
        special_mechanics=mechanics,
        has_hierarchy=bool(data.get("has_hierarchy", True)),
    )

    _print_realm_summary(state.power_system)


def design_power_scaling(state: NovelState) -> None:
    """
    Phase 1-A2：力量刻度——给每个境界补上战斗力表现/寿命/神识/越级规则。
    只对 realms / skill_tiers 类型有意义；社会地位/人生阶段类跳过。
    """
    ps = state.power_system
    if not ps or not ps.realms:
        print("  ⚠ 无成长阶梯，跳过力量刻度设计")
        return
    if ps.system_type not in ("realms", "skill_tiers"):
        print(f"  ⚠ 体系类型 [{ps.system_type}] 不需要战力刻度——跳过")
        return

    realms_brief = "\n".join(
        f"- [{r.index}] {r.name}（小境界：{' / '.join(r.sub_realms)}）{r.power_description}"
        for r in state.power_system.realms
    )

    prompt = f"""
为【{state.power_system.system_name}】体系补上战力刻度——让每个境界有**具体可量化的**战斗力表现。

现有境界（你要逐一给它们补战力刻度）：
{realms_brief}

═══ 要求 ═══
对**每一个**境界给出：
1. combat_capability：具体战斗表现（50字，要具体到"几掌击碎什么""多远距离放招"）
2. lifespan：寿命（如"约200岁"、"千年不朽"）
3. consciousness_range：神识范围（如"方圆十里"、"可扫描整座城池"）
4. mana_capacity：法力储备（如"可御剑战斗半个时辰"）
5. overleap_rule：越级战斗规则（如"正常情况只能越一小境；天才可越一大境；不可越两大境"）
6. specific_examples：2-3 个具体战斗示例，如"可一拳崩石柱""能硬抗筑基后期三成力"——**关键是给后续战斗描写提供对标**

输出 JSON：
{{
  "realm_scalings": [
    {{
      "index": 境界index,
      "name": "境界名（必须与输入一致）",
      "combat_capability": "...",
      "lifespan": "...",
      "consciousness_range": "...",
      "mana_capacity": "...",
      "overleap_rule": "...",
      "specific_examples": ["示例1", "示例2"]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["realm_scalings", "scalings", "items"],
        min_items=len(state.power_system.realms) // 2,  # 至少半数填上
        max_retries=4, temperature=0.65, agent_name="PowerScaling",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ PowerScaling 跳过（LLM 重试失败）——各境界无量化战力刻度")
        return

    scalings = pick_list(data, "realm_scalings", "scalings", "items")
    by_name = {s.get("name"): s for s in scalings if s.get("name")}
    by_index = {int(s.get("index", 0)): s for s in scalings if s.get("index")}

    for r in state.power_system.realms:
        s = by_name.get(r.name) or by_index.get(r.index) or {}
        r.combat_capability = s.get("combat_capability", r.combat_capability)
        r.lifespan = s.get("lifespan", r.lifespan)
        r.consciousness_range = s.get("consciousness_range", r.consciousness_range)
        r.mana_capacity = s.get("mana_capacity", r.mana_capacity)
        r.overleap_rule = s.get("overleap_rule", r.overleap_rule)
        if s.get("specific_examples"):
            r.specific_examples = s.get("specific_examples", [])

    print(f"  ✓ 力量刻度：{sum(1 for r in state.power_system.realms if r.combat_capability)} 个境界已补战力表")
    for r in state.power_system.realms[:4]:
        if r.combat_capability:
            print(f"    {r.name}：战力={r.combat_capability[:40]}｜寿命={r.lifespan[:15]}｜越级={r.overleap_rule[:20]}")


def design_special_abilities(state: NovelState) -> None:
    """
    Phase 2C：人物设计完成后，单独设计特殊能力（3-5 个）。
    仅对 realms / skill_tiers 类型有意义；社会/人生/无体系 跳过。
    """
    ps = state.power_system
    if not ps:
        print("  ⚠ 力量体系未设计，跳过特殊能力")
        return
    if ps.system_type not in ("realms", "skill_tiers"):
        print(f"  ⚠ 体系类型 [{ps.system_type}] 不需要特殊能力——跳过")
        return

    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    allies = [c for c in state.characters if c.role.value == "主要配角"]
    enemies = [c for c in state.characters if c.role.value == "反派"]

    if not protagonist:
        print("  ⚠ 未找到主角，跳过特殊能力设计")
        return

    prot_name = protagonist.name
    allies_desc = "\n".join(
        f"- {c.name}：{c.personality[:25]}｜动机：{c.motivation[:30]}｜能力倾向：{c.ability[:40]}"
        for c in allies[:6]
    ) or "（无主要配角）"
    enemies_desc = "\n".join(
        f"- {c.name}：{c.personality[:25]}｜动机：{c.motivation[:30]}｜能力倾向：{c.ability[:40]}"
        for c in enemies[:6]
    ) or "（无反派）"

    realm_list = state.power_system.realm_list_str()
    realm_plan = state.power_system.protagonist_realm_plan

    # 把作者意图原文塞进 prompt——让 LLM 用作者已经命名好的具体术语，而不是凭空起泛名
    ci = state.creative_intent
    intent_block = ""
    if ci:
        raw = (ci.raw_description or "")[:600]
        proto_archetype = ci.protagonist_archetype_hint or ""
        if raw or proto_archetype:
            intent_block = f"""
【作者创作意图原话（命名能力时必须沿用其中具体术语）】
{raw}
{('主角原型：' + proto_archetype) if proto_archetype else ''}

【命名硬约束】
  · 作者意图原话里如果出现过任何具体名词（金手指/系统/工具/能力/物件 的具体称呼），
    能力名**必须原封不动用作者那个词**——不要包装、不要叠加修饰。
  · **严禁**把作者原词包装成"大数据 X / 智能 X / 信息处理 X / X 引擎 / X 装置 / X 助手"
    之类的泛通用词组合，这等于偷换概念。
  · 如果作者意图里没明确指定具体名字，能力名要贴合本书题材风格（题材自适应——
    LLM 自己选合适词），但同样不能用泛通用词。
  · 名称要短、有记忆点（2-5 字最好），符合中文网文读者的命名习惯。
  · description 字段也要贴合作者原话用词——作者怎么写就怎么沿用，不要替换。
"""
    prompt = f"""
为《{state.title}》设计特殊能力体系——主角和关键角色的标志性能力。

【力量体系】{state.power_system.system_name}：{realm_list}
【主角境界推进】{realm_plan}
{intent_block}
【主角】{prot_name}
  性格：{protagonist.personality_detail[:80]}
  创伤：{protagonist.trauma[:40]}
  致命弱点：{protagonist.fatal_flaw[:25]}
  整体弧线：{protagonist.arc[:80]}

【可选主要配角（作为"伙伴"持有者）】
{allies_desc}

【可选反派（作为"对手"持有者）】
{enemies_desc}

═══ 设计要求 ═══
总共 3-5 个特殊能力：
- 1-2 个属于主角（is_protagonist_signature=true，holder_name="{prot_name}"），源自机遇/血脉/奇遇/传承
- 1-2 个属于主要配角（holder_role="伙伴"，holder_name 从上列选，与主角能力互补或协同）
- 1-2 个属于反派（holder_role="对手"，holder_name 从上列选，对主角构成针对性威胁）

每个能力必须有渐进觉醒（3-5 个阶段）：
- 每阶段绑定一个具体触发事件（主角的能力必须绑到主角弧的关键时刻：绝境/机缘/艰难抉择/目睹创伤等）
- 每阶段有新力量 + 对应卷号 + 觉醒代价
- 主角能力的最终阶段要走得最远（主角永远最强）

每个能力要说明 plot_integration（如何自然首次登场）和 narrative_hook（觉醒后引发什么后续剧情）。

输出 JSON：
{{
  "special_abilities": [
    {{
      "name": "...",
      "source": "天赋|传承|功法|机缘|血脉",
      "description": "能力整体描述（50字）",
      "unlock_condition": "最初解锁条件（30字）",
      "holder_role": "主角自身|伙伴|对手|中立|隐藏",
      "holder_name": "具体角色名（主角填'{prot_name}'，配角/反派从上列选）",
      "is_protagonist_signature": true或false,
      "awakening_stages": [
        {{"stage_index":1,"stage_name":"...","target_volume":卷号,
          "triggering_event":"...","new_power":"...","cost_or_risk":"..."}}
      ],
      "plot_integration": "...",
      "narrative_hook": "..."
    }}
  ]
}}
"""
    example = (
        '{"special_abilities":[{"name":"...","source":"机缘","description":"...","unlock_condition":"...",'
        '"holder_role":"主角自身","holder_name":"' + prot_name + '","is_protagonist_signature":true,'
        '"awakening_stages":[{"stage_index":1,"stage_name":"初显","target_volume":1,'
        '"triggering_event":"...","new_power":"...","cost_or_risk":"..."}],'
        '"plot_integration":"...","narrative_hook":"..."}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["special_abilities", "abilities", "items"],
        min_items=1, item_required_keys=["name", "source", "holder_role"],
        max_retries=4, temperature=0.75,
        agent_name="SpecialAbilityDesigner",
        example_schema=example,
        empty_ok=True,
    )
    abilities_data = pick_list(data, "special_abilities", "abilities", "items") if data else []
    if not abilities_data:
        print("  ⚠ SpecialAbilityDesigner 跳过（LLM 重试失败），本书将无特殊能力——可在前端手动添加")
        return

    # 角色名白名单：主角 + 所有配角/反派（允许 holder_name 为空或白名单内）
    valid_names = {prot_name} | {c.name for c in allies} | {c.name for c in enemies}

    # ── 重跑保护：先按 name 暂存旧 asset 的下游字段，避免被 2C 覆盖 ──
    # 2C2 / external_ai_query 等下游会给 SpecialAbility 加：
    #   · lifecycle_nodes  (Phase 2C2 规划的金手指 lifecycle 节点序列)
    #   · entry_kind       (Phase 2C2 设置的 ability/item/skill/treasure)
    #   · external_llm_profile (用户在 web UI 手动绑的真 AI profile id)
    # 如果 2C 重跑时无脑覆盖整个列表，这些字段会全丢——所以这里按 name 暂存后回填。
    _preserved = {}
    for old_ab in (state.power_system.special_abilities or []):
        _preserved[old_ab.name] = {
            "lifecycle_nodes": old_ab.lifecycle_nodes or [],
            "entry_kind": old_ab.entry_kind or "ability",
            "external_llm_profile": old_ab.external_llm_profile or "",
        }

    abilities = []
    for a in abilities_data:
        stages = [
            AbilityAwakeningStage(
                stage_index=int(st.get("stage_index", i + 1)),
                stage_name=st.get("stage_name", ""),
                target_volume=int(st.get("target_volume", 1)),
                triggering_event=st.get("triggering_event", ""),
                new_power=st.get("new_power", ""),
                cost_or_risk=st.get("cost_or_risk", ""),
            )
            for i, st in enumerate(a.get("awakening_stages", []))
        ]
        holder_name = a.get("holder_name", "")
        if holder_name and holder_name not in valid_names:
            # LLM 胡编了不在名单的角色——清空，让后续 bind_abilities_to_characters 兜底匹配
            holder_name = ""
        new_ab = SpecialAbility(
            name=a["name"], source=a["source"],
            description=a.get("description", ""),
            unlock_condition=a.get("unlock_condition", ""),
            holder_role=a.get("holder_role", ""),
            holder_name=holder_name,
            is_protagonist_signature=bool(a.get("is_protagonist_signature", False)),
            awakening_stages=stages,
            plot_integration=a.get("plot_integration", ""),
            narrative_hook=a.get("narrative_hook", ""),
        )
        # 回填下游字段（按同名 asset 暂存的）—— 防止 2C 重跑把 2C2 等下游产物冲掉
        if new_ab.name in _preserved:
            saved = _preserved[new_ab.name]
            new_ab.lifecycle_nodes = saved["lifecycle_nodes"]
            new_ab.entry_kind = saved["entry_kind"]
            # external_llm_profile：只在 LLM 这次没显式给的时候才回填（让 LLM 能主动改绑）
            if not new_ab.external_llm_profile and saved["external_llm_profile"]:
                new_ab.external_llm_profile = saved["external_llm_profile"]
        abilities.append(new_ab)
    state.power_system.special_abilities = abilities
    if _preserved:
        recovered = sum(1 for ab in abilities
                         if ab.name in _preserved and ab.lifecycle_nodes)
        if recovered:
            print(f"  ↻ 2C 重跑保护：{recovered} 个 asset 的下游字段（lifecycle_nodes 等）已回填")

    print(f"  ✓ 特殊能力（{len(abilities)}个）：")
    for ab in abilities:
        sig = "★" if ab.is_protagonist_signature else " "
        print(f"      {sig} 《{ab.name}》← {ab.holder_name or '(未绑定)'}（{ab.holder_role}｜{len(ab.awakening_stages)}阶段）")


def bind_abilities_to_characters(state: NovelState) -> None:
    """
    在人物设计完成后调用——把 SpecialAbility 的 holder_name 绑定到具体角色。
    规则：
      - is_protagonist_signature=True → 绑定到唯一主角
      - holder_role="伙伴" → 绑定到一个"主要配角"（让 LLM 选最匹配的）
      - holder_role="对手" → 绑定到一个"反派"
      - holder_role="中立"或"隐藏" → 可以暂时留空
    """
    if not state.power_system or not state.power_system.special_abilities:
        return
    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    allies = [c for c in state.characters if c.role.value == "主要配角"]
    enemies = [c for c in state.characters if c.role.value == "反派"]

    # 快速绑定主角能力
    for ab in state.power_system.special_abilities:
        if ab.holder_name:
            continue
        if ab.is_protagonist_signature and protagonist:
            ab.holder_name = protagonist.name
            ab.holder_role = "主角自身"

    # 需要 LLM 匹配的能力
    to_bind = [
        ab for ab in state.power_system.special_abilities
        if not ab.holder_name and ab.holder_role in ("伙伴", "对手")
    ]
    if not to_bind or (not allies and not enemies):
        # 没有可绑的，直接返回
        return

    chars_desc = "\n".join(
        f"- {c.name}（{c.role.value}）性格：{c.personality[:30]} | 能力倾向：{c.ability[:40]}"
        for c in allies + enemies
    )
    abs_desc = "\n".join(
        f"- 《{ab.name}》holder_role={ab.holder_role} 来源={ab.source} 描述：{ab.description[:60]}"
        for ab in to_bind
    )

    prompt = f"""
为以下特殊能力绑定最匹配的角色作为持有者。

【可选角色（只能在这里面选）】
{chars_desc}

【需要绑定的能力】
{abs_desc}

绑定规则：
- holder_role="伙伴" 的能力只能绑给"主要配角"
- holder_role="对手" 的能力只能绑给"反派"
- 选最匹配的：考虑能力来源、描述与角色性格/背景的契合度
- 每个角色可以持有多个能力（但尽量均匀分布）

输出JSON：
{{
  "bindings": [
    {{"ability_name": "能力名", "holder_name": "角色名"}}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["bindings", "items"],
        min_items=1,
        max_retries=2, temperature=0.5, agent_name="AbilityBinder",
        empty_ok=True,
    )
    if not data:
        print(f"  ⚠ 能力绑定失败——部分能力的 holder_name 将留空")
        return
    name_to_char = {c.name: c for c in allies + enemies}

    for b in pick_list(data, "bindings", "items"):
        ab_name = b.get("ability_name", "")
        holder = b.get("holder_name", "")
        if not ab_name or not holder or holder not in name_to_char:
            continue
        for ab in state.power_system.special_abilities:
            if ab.name == ab_name:
                ab.holder_name = holder
                break

    # 打印结果
    print(f"  ✓ 特殊能力持有者绑定：")
    for ab in state.power_system.special_abilities:
        if ab.holder_name:
            sig = "★" if ab.is_protagonist_signature else " "
            print(f"      {sig} 《{ab.name}》→ {ab.holder_name}（{ab.holder_role}）")


def _print_realm_summary(ps: PowerSystem):
    tag = {
        "realms": "修炼境界",
        "skill_tiers": "能力段位",
        "social_rank": "社会地位",
        "progression_arc": "人生阶段",
        "none": "无阶梯",
    }.get(ps.system_type, ps.system_type)
    print(f"  ✓ 体系 [{tag}]：{ps.system_name}")
    if ps.power_flow:
        print(f"    流派：{ps.power_flow}")
    if ps.system_nature:
        print(f"    性质：{ps.system_nature}")
    if ps.realms:
        unit = f"（单位：{ps.rank_unit}）" if ps.rank_unit else ""
        print(f"    阶梯{unit}：{ps.realm_list_str()}")
    else:
        print(f"    （无阶梯，主角成长靠心智/关系/处境/金手指驱动）")
    if ps.special_mechanics:
        print(f"    特殊机制（{len(ps.special_mechanics)}条）：")
        for m in ps.special_mechanics[:5]:
            print(f"      · {m.name}：{m.description[:40]}")
    print(f"    特殊能力（{len(ps.special_abilities)}个）：")
    for a in ps.special_abilities:
        sig = "★" if a.is_protagonist_signature else " "
        holder = a.holder_role or "未指定"
        stages = len(a.awakening_stages)
        print(f"      {sig} {a.name}（{holder}｜{stages}阶段觉醒｜{a.source}）")
        if a.awakening_stages:
            stage_str = " → ".join(
                f"[第{st.target_volume}卷·{st.stage_name}]" for st in a.awakening_stages
            )
            print(f"         {stage_str}")
        if a.plot_integration:
            print(f"         融入：{a.plot_integration[:50]}")
    print(f"    主角进度：{dict(list(ps.protagonist_realm_plan.items())[:3])}...")
