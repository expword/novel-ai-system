"""
All state dataclasses: power system, factions, satisfaction, foreshadowing, rhythm, characters, volumes, memory, lines.
"""
from __future__ import annotations
import re as _re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ═══════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════

_CN_RE = _re.compile(r"[一-鿿㐀-䶿]")
_EN_RE = _re.compile(r"[A-Za-z]+")
_NUM_RE = _re.compile(r"\d+(?:\.\d+)?")


def count_chapter_words(text: str) -> int:
    """中文小说字数（行业标准）：每个汉字算 1，英文 word 算 1，数字串算 1，
    标点/空格/换行不算。和 len(text)（字符数）有显著差距。"""
    if not text:
        return 0
    return (
        len(_CN_RE.findall(text))
        + len(_EN_RE.findall(text))
        + len(_NUM_RE.findall(text))
    )


# ═══════════════════════════════════════════════════════
#  Enums
# ═══════════════════════════════════════════════════════

class LineType(str, Enum):
    STORY = "故事线"
    EMOTION = "情感线"
    CHARACTER = "人物线"
    MYSTERY = "悬疑线"


class LineScope(str, Enum):
    GLOBAL = "全局"
    VOLUME = "卷内"


class TensionLevel(str, Enum):
    CALM = "平静"
    RISING = "上升"
    PEAK = "高潮"
    FALLING = "下落"
    TWIST = "反转"


class CharacterRole(str, Enum):
    PROTAGONIST = "主角"
    MAJOR = "主要配角"
    MINOR = "次要配角"
    ANTAGONIST = "反派"
    VOLUME_ONLY = "卷内角色"


class SatisfactionType(str, Enum):
    SLAP_FACE = "打脸"          # 被看不起后的反打脸
    BREAKTHROUGH = "突破"       # 境界突破/实力爆发
    REVENGE = "复仇"            # 仇恨的兑现
    REVERSAL = "逆袭"           # 绝境逆转
    EMOTIONAL = "情感爆发"      # 情感的决堤
    REVELATION = "真相揭露"     # 震撼的秘密揭开
    SHOW_STRENGTH = "实力展示"  # 碾压性实力展示
    REUNION = "羁绊达成"        # 重要羁绊/誓言兑现
    ASSET_LIFECYCLE = "金手指节点"  # 能力/物品/法宝生命周期节点（获得/首用/解锁/牺牲...）


class ForeshadowImportance(str, Enum):
    MAJOR = "主线伏笔"    # 关乎主线走向
    MINOR = "支线伏笔"    # 支线情节
    DETAIL = "细节伏笔"   # 世界细节/彩蛋


class SetupKind(str, Enum):
    """SetupEntry 的事件类型——爽点 callback 锚点的来源分类。

    SetupLedger 从正文里识别这些事件,触发爽点章前供 writer 作为具体回响候选。
    """
    HUMILIATION = "humiliation"             # 被嘲讽/侮辱(有具体台词)
    LOSS = "loss"                            # 被夺走/失去重要人事物
    REJECTION = "rejection"                  # 被拒绝/被推开
    UNDERESTIMATION = "underestimation"      # 被小看/被无视
    FAILED_ATTEMPT = "failed_attempt"        # 主角自己尝试失败
    VOW = "vow"                              # 主角立誓/承诺
    DEBT = "debt"                            # 欠下人情/仇恨


class HookType(str, Enum):
    """章末钩子类型——网文中决定下章追读的关键。

    chapter_planner 必须给每章选一个 HookType,避免向"悬念钩"单一类型收敛。
    critic 检查 hook_type_compliance(本卷最近 5 章同类型 ≥4 个扣分)。
    """
    SUSPENSE = "suspense"            # 悬念钩:话音未落/门外传来/某个角色突然现身
    REVERSAL = "reversal"            # 反转钩:全章被压制,末句主角微笑/反派惊愕
    INFO_REVEAL = "info_reveal"      # 信息钩:揭露翻盘信息后停笔(身份/秘密/真相)
    EMOTIONAL = "emotional"          # 情感钩:主角做出决断/感情转折,后果留下章
    PHYSICAL = "physical"            # 物理钩:看到不该出现的人/物/场景(惊鸿一瞥)
    DEATH = "death"                  # 死亡钩:重要角色突然出事(伤亡/失踪)
    CLIFF = "cliff"                  # 悬崖钩:字面危险情境(被追/中毒/坠落)


class RhythmType(str, Enum):
    SLOW_BUILD = "慢热铺垫"
    FAST_ACTION = "快节奏战斗"
    EMOTIONAL = "情感沉淀"
    REVEAL = "信息揭示"
    TRANSITION = "过渡转场"


# ═══════════════════════════════════════════════════════
#  I. 境界 / 力量体系
# ═══════════════════════════════════════════════════════

@dataclass
class Realm:
    index: int
    name: str
    sub_realms: list[str]           # 小境界划分，如["初期","中期","后期","圆满"]
    power_description: str          # 此境界能做什么
    breakthrough_condition: str     # 突破条件
    resource_requirement: str       # 所需资源/机缘
    average_time: str               # 普通修炼者所需时间
    rarity: str                     # 此境界修炼者在世界中的稀有程度
    # ── PowerScaling 扩展字段（由 design_power_scaling 填充）─────
    combat_capability: str = ""     # 具体战斗表现（如"一掌击碎三丈石壁 / 御剑飞行百里"）
    lifespan: str = ""              # 寿命（如"约200岁"）
    consciousness_range: str = ""   # 神识范围（如"方圆十里"）
    mana_capacity: str = ""         # 法力储备（如"可御剑战斗半个时辰"）
    overleap_rule: str = ""         # 越级战斗规则（如"正常情况只能越小境界；天骄可越一大境界"）
    specific_examples: list[str] = field(default_factory=list)  # 具体例子："能打过xx""能抗住yy一击"


@dataclass
class AbilityAwakeningStage:
    """特殊能力的一个觉醒阶段——能力随剧情逐步变强。"""
    stage_index: int
    stage_name: str              # 如"初显/小成/大成/圆满"
    target_volume: int           # 预计第几卷达到
    triggering_event: str        # 触发这一阶段的情节事件（40字，要与主角弧关联）
    new_power: str               # 这一阶段获得的新力量（30字）
    cost_or_risk: str = ""       # 觉醒的代价或风险（20字，让力量有重量）


@dataclass
class LifecycleNode:
    """金手指/物品/技能在剧情里的一个生命周期节点。
    由 ability_roadmap_planner 生成；不固定枚举，让 LLM 按情节自由组合：
      · "setup"     —— 铺垫线索（让 asset 的存在有伏笔）
      · "acquired"  —— 主角真正获得 asset
      · "first_use" —— 第一次实战使用
      · "locked"    —— 获得后被封印/限制无法使用
      · "unlocked"  —— 限制解除（条件达成/敌方破解/觉醒）
      · "constraint_lifted" —— 使用条件放宽
      · "escalation"—— 升级/进阶/新能力解锁
      · "sacrificed"—— 牺牲/失去 asset（反噬/耗尽）
      · ……开放（LLM 可造新 type，下游按 type 字符串处理）
    """
    node_type: str                  # 节点类型（开放字符串，见上）
    target_volume: int              # 预计第几卷
    target_chapter: int = 0         # 预计第几章（0 = 粗粒度只到卷，由 chapter_planner 临近时细化）
    prerequisites: str = ""         # 这一步发生的前置情节条件（40字，自然语言）
    narrative_purpose: str = ""     # 在故事里的作用（30字，给 chapter_planner 写 must_include 用）
    is_dramatic: bool = False       # 是否戏剧性强（True → 反向生成 SatisfactionPoint 挂钩爽点系统）
    linked_sp_id: str = ""          # 反向绑定的 sp_id（is_dramatic=True 时填）
    triggered: bool = False         # 是否已落章（chapter_planner 细化后置 True）
    actual_chapter: int = -1        # 实际落到的章号（-1=未落）


@dataclass
class PowerMechanic:
    """
    流派专属机制——不同流派有不同的"特殊规则"：
    - 系统流：签到系统/任务系统/商城
    - 无限流：主神评分/副本难度/死亡惩罚
    - 克苏鲁流：序列跃升仪式/非凡特性/神名封印
    - 游戏流：副本/装备品级/转职条件
    - 异能流：觉醒机制/失控代价
    - 料理流：料理人评级/食材品级
    - 基建/种田流：领地等级/产业规模
    - 驯兽流：宠物契约/觉醒等级
    等等。

    这个 dataclass 记录这些流派的规则，让后续 agent 能利用。
    """
    name: str                       # 机制名（如"签到系统"/"副本评级"/"序列跃升"）
    description: str                # 机制描述（50字）
    protagonist_usage: str          # 主角如何用这个机制（40字）
    narrative_impact: str = ""      # 本机制如何驱动剧情（30字，可选）


@dataclass
class SpecialAbility:
    """金手指/能力/物品/技能/法宝——统称 SpecialAsset（保留 SpecialAbility 类名兼容旧代码，
    见文件底部 `SpecialAsset = SpecialAbility` 别名）。entry_kind 字段区分实际类型。"""
    name: str
    source: str                     # 来源（传承/天赋/功法/机缘/血脉/穿越随身/师承/拾获...）
    description: str                # 整体描述（50字）
    unlock_condition: str           # 最初解锁条件（对应第1阶段，或"获得即可用"）
    usage_rule: str = ""            # 什么时候允许使用（场景/触发/前置条件）
    effect_scope: str = ""          # 能做到什么（效果范围/强度/对象）
    hard_limits: str = ""           # 明确不能做到什么（硬边界）
    cost_rule: str = ""             # 每次使用或关键使用必须付出的代价/冷却/风险
    # ── 持有者与对主角的关系 ─────────────────────────
    holder_role: str = ""           # "主角自身" / "伙伴" / "对手" / "中立" / "隐藏"
    holder_name: str = ""           # 具体角色名（由 character_designer 在角色设计后回填）
    is_protagonist_signature: bool = False   # 是否为主角"逆天机遇"核心能力
    # ── 资产类型（区分能力/物品/技能/法宝）─────────────
    entry_kind: str = "ability"     # "ability"/"item"/"skill"/"treasure"/"system" 等开放标签
    # ── 渐进觉醒（保留旧字段，新规划走 lifecycle_nodes）─
    awakening_stages: list[AbilityAwakeningStage] = field(default_factory=list)
    # ── 生命周期节点序列（ability_roadmap_planner 产出）─
    # 由 LLM 按情节自由编排：可能是 [acquired+first_use 同章]，可能是 [setup → acquired → 隔多章 → unlocked → first_use]，
    # 也可能是 [acquired → escalation×N → sacrificed]。chapter_planner 在临近时把粗粒度卷锚细化到章。
    lifecycle_nodes: list[LifecycleNode] = field(default_factory=list)
    # ── 融入情节（保留为人类可读摘要；细节走 lifecycle_nodes）─
    plot_integration: str = ""      # 该 asset 如何自然融入剧情（40字，首次出场/成长桥段）
    narrative_hook: str = ""        # 觉醒/获取引发的后续剧情（30字）
    # ── 绑定外部 LLM（真 AI 接入）──────────────────────
    # 当主角在小说里"使用"这个能力（比如向豆包/系统问问题），可以指定一个 user_models.json
    # 里的 profile id，writer 写到该场景时会用 [[ASK_AI:能力名|问题]] 占位，章节定稿前
    # 后处理 agent 真的把"问题"发给这个 LLM，用真实回答替换占位——不是 writer 凭空编。
    # 留空 = 关闭真 AI 接入（默认行为：writer 自己写"AI 回答"）。
    external_llm_profile: str = ""


# 类型别名：让新代码用 SpecialAsset 这个更通用的名字；老代码 import SpecialAbility 不破
SpecialAsset = SpecialAbility


@dataclass
class PowerSystem:
    """
    自适应的"成长体系"容器。

    不同流派语义不同：
    - 修真：realms=境界链，resources=灵石/丹药
    - 武侠：realms=武学境界，resources=秘笈/内力
    - 异能：realms=能力等级(SS/S/A)，abilities=各种超能力
    - 系统：realms=系统赋予的等级（1-99），mechanics=签到/任务/商城
    - 无限：realms=轮回者评级，mechanics=副本/主神评分/死亡惩罚
    - 克苏鲁序列：realms=序列 0-9，mechanics=序列跃升/非凡特性
    - 游戏：realms=职业等级+段位，mechanics=副本/装备/PVP
    - 魔法：realms=魔法环阶(1-9环)，resources=法杖/卷轴/魔晶
    - 科技：realms=技术等级(T1-T9)，resources=能源/材料/蓝图
    - 诡异/SCP：realms=收容等级，mechanics=异常事件/规则
    - 驯兽/宠物：realms=宠物品阶，mechanics=契约/觉醒
    - 料理/职业：realms=职业段位，mechanics=评级/传承
    - 商战/财富：realms=财富段（小摊→富豪），mechanics=行业规则
    - 宫斗/官场：realms=官阶/位份，mechanics=人情/党争
    - 职场：realms=职级（已有），mechanics=KPI/人脉
    - 国运：realms=文明阶段，mechanics=科技树/外交
    - 人生：realms=人生阶段，mechanics=无
    - 纯情感/推理：realms=[]，mechanics=[]
    """
    system_name: str                # 体系名称（贴合流派，如"九天玄道"/"全球觉醒榜"/"迷雾序列"）
    system_description: str         # 整体描述
    realms: list[Realm]             # 阶梯列表（可以为空）
    special_abilities: list[SpecialAbility] = field(default_factory=list)
    cultivation_resources: list[dict] = field(default_factory=list)
    protagonist_realm_plan: dict[int, str] = field(default_factory=dict)

    # ── 体系大类（传统五种；LLM 可以用其他字符串）────
    system_type: str = "realms"
    # ── 具体流派标签（更细，LLM 按网文惯例填）────────
    power_flow: str = ""            # 如"修真流"/"武侠流"/"异能觉醒流"/"系统流"/"无限流"/
                                    #    "克苏鲁序列流"/"游戏异界流"/"科技赛博流"/"魔法学院流"/
                                    #    "诡异怪谈流"/"驯兽流"/"料理流"/"基建种田流"/"商战流"/
                                    #    "宫斗宅斗流"/"职场流"/"国运流"/"人生现实流"/"纯情感流"
    # ── 体系性质一句话 ──────────────────────────────
    system_nature: str = ""         # 如"九境修真"/"序列仪式"/"系统打卡签到"

    # ── 等级单位名 ──────────────────────────────────
    rank_unit: str = ""             # "境"/"级"/"段"/"品"/"序列"/"阶"/"星"/"环"/"层"/"点"——空=无层级

    # ── 流派专属机制 ────────────────────────────────
    special_mechanics: list[PowerMechanic] = field(default_factory=list)

    # ── 是否有明确层级（系统流可能有数值无层级）─────
    has_hierarchy: bool = True

    # ── P11 战力比值表（量化"越级"含义）──────────────────
    # power_ratio_table: { "高境界名": { "低境界名": "战力比 N 倍 + 描述" } }
    # 例：{ "金丹": { "筑基": "约 5-8 倍战力，加远距离斗法+元神操控" } }
    # writer 战斗描写时参考——避免越级反差忽强忽弱
    power_ratio_table: dict[str, dict[str, str]] = field(default_factory=dict)

    def realm_by_name(self, name: str) -> Optional[Realm]:
        for r in self.realms:
            if r.name == name:
                return r
        return None

    def realm_list_str(self) -> str:
        if not self.realms:
            return "（无等级阶梯）"
        return " → ".join(f"{r.name}" for r in self.realms)

    def has_ladder(self) -> bool:
        return bool(self.realms) and self.system_type != "none"

    def flow_brief(self) -> str:
        """给下游 agent 看的一行体系摘要。"""
        parts = [self.power_flow or self.system_type]
        if self.system_nature:
            parts.append(self.system_nature)
        if self.rank_unit:
            parts.append(f"单位:{self.rank_unit}")
        if self.special_mechanics:
            parts.append("机制:" + "/".join(m.name for m in self.special_mechanics[:3]))
        return " ｜ ".join(parts)


# ═══════════════════════════════════════════════════════
#  II. 世界势力架构
# ═══════════════════════════════════════════════════════

@dataclass
class FactionRelation:
    target: str
    relation_type: str              # 敌对/友好/中立/附属/暗中对立
    description: str


@dataclass
class FactionInfiltration:
    """跨层渗透：本势力在其他势力中安插的眼线/影响。"""
    target_faction: str             # 被渗透势力名
    method: str                     # 渗透方式（安插眼线/金钱收买/血脉控制）
    depth: str                      # 渗透深度（表层/核心/完全控制）
    reveal_volume: int              # 哪卷揭露此渗透


@dataclass
class Faction:
    name: str
    faction_type: str               # 宗门/帝国/魔族/商会/神秘组织/散修联盟/情报组织/医馆
    power_level: int                # 1-10（相对实力）
    territory: str

    # ── 层级系统（核心新增）──────────────────────────
    tier: int                       # 1=底层(村/城镇) 2=地方(州/小宗门) 3=国家/大宗门
                                    # 4=大陆/圣地  5=幕后黑手
    tier_label: str                 # 层级说明（如"偏远县城的小帮派"）
    is_neutral: bool = False        # 中立势力（商会/医馆/情报组织，不站队）
    is_hidden: bool = False         # 是否对读者/主角隐藏（幕后势力）
    reveal_volume: int = 1          # 第几卷才对读者揭示（is_hidden=True时有效）
    protagonist_start: bool = False # 主角初始所在/最弱起点的势力

    surface_goal: str = ""          # 表面目标
    hidden_goal: str = ""           # 隐藏目标/秘密
    core_strength: str = ""         # 核心实力底牌
    weakness: str = ""              # 弱点/可被利用之处
    key_members: list[str] = field(default_factory=list)

    # ── 内部矛盾（主角可利用）──────────────────────
    internal_conflicts: list[str] = field(default_factory=list)

    # ── 跨层渗透 ────────────────────────────────────
    infiltrations: list[FactionInfiltration] = field(default_factory=list)

    # ── 消亡后的权力真空 ────────────────────────────
    power_vacuum_desc: str = ""     # 该势力被消灭后，空出的位置引发什么争夺

    # ── 动态状态（随剧情变化）──────────────────────
    status: str = "active"          # active / destroyed / weakened / merged
    status_changed_volume: int = -1 # 状态在第几卷改变

    relations: list[FactionRelation] = field(default_factory=list)
    volume_role: dict[int, str] = field(default_factory=dict)  # {卷号: 本卷扮演的角色}

    def tier_name(self) -> str:
        names = {1: "底层", 2: "地方", 3: "国家/大宗门", 4: "大陆/圣地", 5: "幕后黑手"}
        return names.get(self.tier, f"第{self.tier}层")

    def to_dict(self) -> dict:
        """
        统一的 dict 化（director 写 plans/factions.json + web /api 都共用），
        是两边消费者所需字段的超集——既保留 director 历史的别名（power/type/relations.type/
        infiltrations.target），也包含 web 需要的 tier_name/volume_role 与原字段。
        """
        return {
            # ── 基础（两边共用）──
            "name": self.name,
            "type": self.faction_type,            # director 历史 key
            "faction_type": self.faction_type,    # 原字段名
            "power": self.power_level,            # director 历史 key
            "power_level": self.power_level,      # 原字段名（web 用）
            "territory": self.territory,
            "tier": self.tier,
            "tier_label": self.tier_label,
            "tier_name": self.tier_name(),        # 计算字段（web 用）
            "is_neutral": self.is_neutral,
            "is_hidden": self.is_hidden,
            "reveal_volume": self.reveal_volume,
            "protagonist_start": self.protagonist_start,
            "surface_goal": self.surface_goal,
            "hidden_goal": self.hidden_goal,
            "core_strength": self.core_strength,
            "weakness": self.weakness,
            "key_members": list(self.key_members),
            "internal_conflicts": list(self.internal_conflicts),
            "power_vacuum_desc": self.power_vacuum_desc,
            "status": self.status,
            "status_changed_volume": self.status_changed_volume,
            # ── 关系/渗透（保留 director 的别名 type/target，同时补全 web 的全字段）──
            "relations": [
                {"target": r.target, "type": r.relation_type,
                 "relation_type": r.relation_type, "description": r.description}
                for r in self.relations
            ],
            "infiltrations": [
                {"target": i.target_faction, "target_faction": i.target_faction,
                 "method": i.method, "depth": i.depth, "reveal_volume": i.reveal_volume}
                for i in self.infiltrations
            ],
            "volume_role": dict(self.volume_role),
        }


# ═══════════════════════════════════════════════════════
#  III. 爽点系统
# ═══════════════════════════════════════════════════════

@dataclass
class SatisfactionSetup:
    chapter: int
    content: str                    # 铺垫内容（埋下什么）


@dataclass
class SatisfactionPoint:
    sp_id: str                      # 唯一id
    sp_type: SatisfactionType
    title: str                      # 爽点标题
    description: str                # 具体场景描述
    intensity: int                  # 爽感强度 1-10
    volume: int
    target_chapter: int             # 预计在哪章爆发
    setup_chain: list[SatisfactionSetup]  # 铺垫链（从哪几章开始铺垫）
    payoff_description: str         # 爆发时的具体呈现方式
    triggered: bool = False
    actual_chapter: int = -1


# ═══════════════════════════════════════════════════════
#  IV. 伏笔管理系统
# ═══════════════════════════════════════════════════════

@dataclass
class ForeshadowItem:
    fw_id: str
    content: str                    # 伏笔内容（读者看到了什么）
    hidden_meaning: str             # 真实含义（作者视角）
    importance: ForeshadowImportance
    planted_chapter: int
    planned_resolve_volume: int     # 计划在第几卷兑现
    planned_resolve_chapter: int    # 计划在第几章兑现，-1=未定
    resolution_description: str     # 兑现时的场景
    related_sp_id: str = ""         # 关联的爽点id
    resolved: bool = False
    actual_resolve_chapter: int = -1
    # ── ChekhovTracker 三状态 ────────────────────────
    activation_chapter: int = -1    # 读者开始注意到的章（第一次明显提醒）
    activation_sign: str = ""       # 激活时的具体表现（30字）
    resolution_quality: str = ""    # 回收效果评价（回收后填，20字：震撼/意料之中/生硬等）


@dataclass
class RedHerring:
    """
    红鲱鱼（假线索）——故意误导读者的假伏笔。
    读者以为是伏笔 A 会在第 N 章兑现，实际是个误导；真相另在。
    """
    rh_id: str
    content: str                    # 假线索呈现给读者的样子（50字）
    misdirection_purpose: str       # 误导的目的（40字，如"让读者以为反派是X，实际是Y"）
    planted_chapter: int            # 计划植入章
    debunk_chapter: int             # 计划揭穿/被证伪的章（-1=读者自己回过味来）
    actual_truth: str               # 真相（作者视角，60字）
    planted: bool = False
    debunked: bool = False


@dataclass
class SetupEntry:
    """爽点 callback 账本条目——从正文里提取的"被埋下/待回响"事件。

    与 SatisfactionPoint.setup_chain 的关系:
      · setup_chain 是规划期预定的"我打算在第 N 章铺垫什么"(抽象 content)
      · SetupEntry 是章后从实际正文里提取的"我已经埋下了什么"(具体 quote/scene)
      · 触发爽点章前,find_callback_seeds() 拉出相关 pending entry 给 writer 当回响锚点
    """
    entry_id: str                          # 唯一 id (e.g. "setup_0001")
    chapter: int                           # 发生章
    kind: SetupKind
    actor: str                             # 主体(通常是主角)
    counterpart: str                       # 对手方(嘲讽者/夺走者/...)
    quote: str                             # 具体台词(20-50字,可空)
    scene_summary: str                     # 具体场景(50字)
    suggested_sp_id: str = ""              # LLM 建议关联的爽点 sp_id(可空)
    payoff_status: str = "pending"         # pending / partial / paid
    callback_chapter: int = -1             # 实际兑现章 (-1=未兑现)
    callback_quote: str = ""               # 兑现时的具体表达(可空)


@dataclass
class SimulatedComment:
    """模拟读者评论——comment_simulator 章后生成,挂在 ChapterSummary 上。

    4 类身份(reader_type):
      · 追读派 — 主线党,关心剧情推进/情感投入(positive 居多)
      · 挑刺派 — 逻辑党,挑设定漏洞/文笔毛病(critical)
      · 路过派 — 吐槽党,玩梗调侃(neutral)
      · 章评党 — 金句党,截图段落/夸或骂(mixed)
    """
    reader_type: str            # 追读派 / 挑刺派 / 路过派 / 章评党
    nickname: str               # 读者昵称(LLM 生成,匿名风格)
    text: str                   # 评论内容(40-100字)
    sentiment: str = "neutral"  # positive / neutral / negative / critical


@dataclass
class ReaderExpectation:
    """读者预期——expectation_manager 在写章前预测,挂在 ChapterDirective 上。

    chapter_planner 必须对每条预期标 decision:
      · satisfy — 满足预期(常规推进,读者爽)
      · reverse — 反转预期(出意料,读者震惊)
      · stack   — 加料(预期内 + 额外惊喜,双向)

    decision 由 chapter_planner LLM 在生成蓝图时填(可能修改)。
    """
    expectation: str            # 读者读完前一章会预期什么(30字)
    based_on: str               # 基于哪个线索(如"第 5 章末:门外传来咳嗽声"——20字)
    decision: str = ""          # satisfy / reverse / stack(空=未决策)


@dataclass
class FlavorAdvice:
    """老作者直觉调味建议——flavor_advisor 每 N 章扫一次产出。

    输入:最近 N 章的 critic 评分 / reader_audit / 模拟评论。
    输出:接下来 1-3 章应当加什么"调味料"(新反派出场/感情线推进/吃个小亏 等)。

    chapter_planner 读最近一条 advice 注入 prompt,作为可选灵感。
    """
    generated_at_chapter: int    # 生成本条建议的章号
    target_range: str            # 建议作用范围(如"第 12-15 章")
    advice: list[str]            # 具体建议条目(3-5 条,每条 ≤ 50 字)
    reasoning: str = ""          # 给的理由(50 字内,可空)


@dataclass
class PhaseDraft:
    """Stepwise 审核 modal 用:某 phase 的一个候选版本(LLM 跑 N 次产出多版本).

    payload 存该 phase 改动的字段(从 PHASE_FIELDS_MAP[phase_id] 读出的字段名 → JSON-able 值).
    用户在 modal 里对比选定后,apply_draft 把 payload 写回 state 顶层字段;其余候选 discard.
    """
    phase_id: str                # 如 "1D" / "1F" / "0" 等
    version_index: int           # 1 / 2 / 3 (在该 phase 的候选列表中的序号)
    payload: dict                # {field_name: value} — 该候选写回 state 时用
    created_at: str              # 时间戳
    notes: str = ""              # 可选备注(如"用户反馈:..."或"LLM 重试 3 次后产出")


# ═══════════════════════════════════════════════════════
#  IV-C. 反转系统（TwistDesigner）—— 层层反转让读者猜不到
# ═══════════════════════════════════════════════════════

@dataclass
class TwistLayer:
    """
    反转的一层——读者以为 X，揭露后实际是 Y。
    Layer 越深，颠覆越大：Layer 1 是局部反转，Layer 4 是世界观崩塌。
    """
    layer: int                       # 第几层（1=第一次反转，向上递进到 4）
    surface_belief: str              # 这层揭露前读者/主角相信的内容（40字）
    reveal: str                      # 揭露的真相（50字）
    clues_planted: list[str] = field(default_factory=list)  # 要提前埋下的伏笔（各 30 字，2-3 条）
    reveal_anchor: str = ""          # 何时揭露（如"第3卷中段"/"第5卷高潮"）
    emotional_impact: str = ""       # 对主角/读者的冲击（25字）
    twist_mechanism: str = ""        # 反转手法（"信息缺失补全"/"视角欺骗"/"因果颠倒"/"身份替换"）


@dataclass
class TwistChain:
    """
    一条反转链——从初始认知开始，2-4 次层层反转到最终真相。
    目标：让读者每次以为"这次真相揭露了"，结果下一层又颠覆一次。

    【大/小反转的跨度规则】
    · 大反转链（difficulty = brain_burning / mind_bending）必须跨越多卷——
      每层的 reveal_anchor 分散在不同卷号，营造长期悬念
    · 小反转链（difficulty = moderate）可以在一卷之内完成——
      所有层的 reveal_anchor 都落在同一卷的不同章节（章级推进）
    """
    chain_id: str
    title: str                       # 反转链命名（如"主角身世之谜"/"师父真面目"）
    category: str                    # 身世 / 阵营 / 目的 / 因果 / 身份 / 设定
    initial_setup: str               # 全书开头时读者相信的设定（50字）
    target_layers: int = 2           # 计划的反转层数（2-4）
    layers: list[TwistLayer] = field(default_factory=list)
    involved_characters: list[str] = field(default_factory=list)
    involved_factions: list[str] = field(default_factory=list)
    difficulty: str = "moderate"     # moderate（2 层）/ brain_burning（3 层）/ mind_bending（4 层）
    design_rationale: str = ""       # 这条反转链的设计理念（60字，为什么这样反）
    linked_foreshadow_ids: list[str] = field(default_factory=list)  # 挂钩的伏笔 id
    # ── 跨度 ────────────────────────────────────────────
    scope: str = "cross_volume"      # "within_volume"（小反转，单卷内）/ "cross_volume"（大反转，跨卷）
    volume_span: list[int] = field(default_factory=list)  # 本链覆盖的卷号（按顺序，如 [2,3,5]）
    anchor_volume: int = 0           # 本链的主锚卷（小反转）或起始卷（大反转）


@dataclass
class TwistSystem:
    chains: list[TwistChain] = field(default_factory=list)
    design_principle: str = ""       # 整套反转的核心设计理念（80字）
    reader_experience_curve: str = ""  # 读者体验曲线（100字，描述从怀疑→笃定→崩溃→重建的过程）


# ═══════════════════════════════════════════════════════
#  IV-B. 冲突阶梯（ConflictLadder）
# ═══════════════════════════════════════════════════════

@dataclass
class ConflictEntry:
    """每卷的核心冲突规划——冲突类型+对手层级+解决方式。"""
    volume: int
    conflict_type: str              # 人vs人 | 人vs势力 | 人vs天道 | 人vs自己 | 人vs规则
    core_conflict: str              # 本卷核心冲突的具体描述（40字）
    opponent_tier: int              # 对手层级 1-5（递进不能倒退）
    resolution_method: str          # 武力 | 智谋 | 情感 | 合作 | 运气 | 牺牲
    escalation_note: str            # 这卷比上卷升级在哪（30字）
    why_this_type: str = ""         # 为什么这一卷选这种类型（20字）


@dataclass
class ConflictLadder:
    entries: list[ConflictEntry] = field(default_factory=list)

    def get(self, volume: int) -> Optional["ConflictEntry"]:
        for e in self.entries:
            if e.volume == volume:
                return e
        return None

    def brief(self) -> str:
        if not self.entries:
            return ""
        return "\n".join(
            f"  V{e.volume}[{e.conflict_type}·T{e.opponent_tier}]：{e.core_conflict[:35]}"
            f"（解决方式：{e.resolution_method}）"
            for e in sorted(self.entries, key=lambda x: x.volume)
        )


# ═══════════════════════════════════════════════════════
#  IV-C. 情绪曲线（EmotionCurve）
# ═══════════════════════════════════════════════════════

@dataclass
class EmotionNote:
    volume: int
    base_tone: str                  # 热血 | 悲情 | 轻松 | 压抑 | 温暖 | 黑暗 | 希望
    low_point_chapter: int          # 本卷情绪最低谷的大致章节
    low_point_desc: str             # 低谷描述（30字，让读者失望/心疼/绝望的瞬间）
    high_point_chapter: int         # 本卷情绪最高点的大致章节
    high_point_desc: str            # 高点描述（30字）
    contrast_with_prev: str         # 与上卷情绪基调的对冲（25字）


@dataclass
class EmotionCurve:
    notes: list[EmotionNote] = field(default_factory=list)

    def get(self, volume: int) -> Optional["EmotionNote"]:
        for n in self.notes:
            if n.volume == volume:
                return n
        return None

    def brief(self) -> str:
        if not self.notes:
            return ""
        return "\n".join(
            f"  V{n.volume}[{n.base_tone}]：低谷{n.low_point_chapter}→高点{n.high_point_chapter}"
            for n in sorted(self.notes, key=lambda x: x.volume)
        )


# ═══════════════════════════════════════════════════════
#  V. 情节节奏设计
# ═══════════════════════════════════════════════════════

@dataclass
class RhythmSegment:
    chapter_start: int
    chapter_end: int
    rhythm_type: RhythmType
    description: str                # 这段节奏要达到的效果
    word_pace: str                  # "紧凑"/"舒缓"/"中等"


@dataclass
class VolumeRhythmPlan:
    volume_index: int
    overall_pattern: str            # 整卷节奏模式描述
    segments: list[RhythmSegment]
    breathing_chapters: list[int]   # 喘息章（轻松/日常，让读者休息）
    climax_chapters: list[int]      # 高潮章


# ═══════════════════════════════════════════════════════
#  VI. 人物（增强版）
# ═══════════════════════════════════════════════════════

@dataclass
class Relationship:
    target_name: str
    relation: str
    evolution: str


@dataclass
class Character:
    name: str
    role: CharacterRole
    gender: str
    age_desc: str
    appearance: str
    personality: str                # 性格关键词
    personality_detail: str        # 性格深度描述（含矛盾面）
    background: str
    trauma: str                     # 心理创伤/阴影
    desire: str                     # 内心真正渴望
    fear: str                       # 最深的恐惧
    speech_pattern: str             # 说话风格特点
    ability: str
    realm: str                      # 当前境界
    arc: str                        # 整体成长轨迹
    motivation: str
    fatal_flaw: str
    first_volume: int
    last_volume: int                # -1=全程
    relationships: list[Relationship] = field(default_factory=list)
    volume_arcs: dict[int, str] = field(default_factory=dict)
    volume_realm: dict[int, str] = field(default_factory=dict)  # {卷: 本卷末境界}
    # ── 细腻刻画字段（主角/主要配角必填；次要配角可空）────
    signature_mannerisms: list[str] = field(default_factory=list)  # 习惯性小动作（2-3个，如"紧张时拨头发""说谎时看左上方"）
    verbal_tics: list[str] = field(default_factory=list)           # 口头禅/说话习惯（1-3条，如"总是以反问结尾""爱引经据典"）
    sensory_signature: str = ""                                     # 标志性感官特征（如"身上有淡淡檀香""声音带沙哑的颗粒感"）
    default_stress_response: str = ""                               # 压力下的第一反应（如"会突然沉默""会用冷笑掩饰"）
    defining_memory: str = ""                                       # 塑造其人的一段关键记忆（40字）
    secret_desire: str = ""                                         # 从不承认的渴望（30字）
    contrast_with_protagonist: str = ""                             # 和主角的世界观/做事方式有什么对比或张力（30字，主角填"—"）
    # ── VoiceProfile 语言指纹（writer 对话一致性的硬依据）──────
    high_freq_vocab: list[str] = field(default_factory=list)       # 高频词汇（3-5个，配合 verbal_tics）
    speech_taboo: list[str] = field(default_factory=list)          # 这个角色绝对不会说的话类型（如"粗口""文言""自谦之词"，2-4条）
    speech_under_anger: str = ""                                    # 愤怒时的语言变化（20字，如"句式变短，冷嘲为主"）
    speech_under_fear: str = ""                                     # 恐惧时的语言变化（20字，如"结巴或沉默"）
    speech_under_joy: str = ""                                      # 喜悦时的语言变化（20字）
    sentence_length_preference: str = ""                            # 句式偏好（如"短句为主""长短交织""排比成串"）
    # ── 叙事功能（新增：从 MasterDispatcher slot 继承 + Designer 扩展）──
    narrative_function: str = ""                                    # NarrativeFunction 枚举值
    support_role: str = ""                                          # 功能内细分（如 情感支撑者 > 伴侣/家人/挚友）
    function_detail: str = ""                                       # 该功能在故事里的具体发挥（50字）
    source_slot_id: str = ""                                        # 来源 MasterOutline slot id（便于追踪）

    # ── P8 反派深度字段（仅当本角色是反派时才需要填）────────
    belief_system: str = ""              # 反派的"信仰系统"（80字）：他相信什么？这种相信对他来说有多重要？
    despair_moments: list[str] = field(default_factory=list)
                                         # 反派让主角/读者"绝望"的具体时刻规划（每条 30-60 字）
    charisma_signature: str = ""         # 反派的"魅力点"（60字）——读者明知他坏却忍不住欣赏
    pov_insertion_volumes: list[int] = field(default_factory=list)
                                         # 哪几卷应该有"反派 POV 章节"
    inner_wound: str = ""                # 反派自己也曾受过的伤（50字），让他立体而非单纯邪恶

    def brief(self) -> str:
        return (f"【{self.role.value}】{self.name}（{self.gender}/{self.age_desc}）"
                f"境界:{self.realm} | {self.personality} | 动机:{self.motivation}")

    def voice_card(self, max_chars: int = 360) -> str:
        """
        汇总本角色的**声音指纹**为一张紧凑的卡片——供 writer 在"本幕出场角色"段落直接消费。
        把零散字段（speech_pattern/verbal_tics/high_freq_vocab/speech_taboo/
        signature_mannerisms/sensory_signature/sentence_length_preference/各情绪语言变化）
        整理成"开口就该是他/她"的一段参照。

        和 full_sheet 的区别：
        · full_sheet 是人物档案——用于规划/审计
        · voice_card 是"说话卡"——writer 正文生成时直接塞 prompt，必须短、必须具体、必须可直接影响下一句对白
        """
        parts: list[str] = [f"◇ {self.name}"]
        # 说话风格（主干）
        sp = (self.speech_pattern or "").strip()
        if sp:
            parts.append(f"· 风格：{sp[:80]}")
        # 句式偏好
        slp = (self.sentence_length_preference or "").strip()
        if slp:
            parts.append(f"· 句式：{slp[:50]}")
        # 口癖 / 高频词
        tics = [t for t in (self.verbal_tics or []) if t][:3]
        vocab = [v for v in (self.high_freq_vocab or []) if v][:4]
        if tics:
            parts.append(f"· 口癖：{' / '.join(tics)}")
        if vocab:
            parts.append(f"· 高频词：{' / '.join(vocab)}")
        # 禁忌（绝不说）
        taboo = [t for t in (self.speech_taboo or []) if t][:3]
        if taboo:
            parts.append(f"· 禁说：{' / '.join(taboo)}")
        # 情绪下的语言变化
        mood_changes: list[str] = []
        if (self.speech_under_anger or "").strip():
            mood_changes.append(f"怒→{self.speech_under_anger[:30]}")
        if (self.speech_under_fear or "").strip():
            mood_changes.append(f"惧→{self.speech_under_fear[:30]}")
        if (self.speech_under_joy or "").strip():
            mood_changes.append(f"喜→{self.speech_under_joy[:30]}")
        if mood_changes:
            parts.append("· 情绪语言：" + " / ".join(mood_changes))
        # 压力反应（行动层面，对话里可借用）
        if (self.default_stress_response or "").strip():
            parts.append(f"· 压力下：{self.default_stress_response[:40]}")
        # 小动作
        sigs = [s for s in (self.signature_mannerisms or []) if s][:2]
        if sigs:
            parts.append(f"· 小动作：{' / '.join(sigs)}")
        # 感官标记
        if (self.sensory_signature or "").strip():
            parts.append(f"· 感官标记：{self.sensory_signature[:40]}")

        # 如果啥都没有，至少输出 brief
        if len(parts) == 1:
            parts.append(f"· {self.personality[:40] or '—'}")

        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars - 1] + "…"
        return text

    def full_sheet(self) -> str:
        return "\n".join([
            f"姓名：{self.name}　角色：{self.role.value}",
            f"性别/年龄：{self.gender}/{self.age_desc}",
            f"外貌：{self.appearance}",
            f"性格：{self.personality_detail}",
            f"背景：{self.background}",
            f"创伤：{self.trauma}",
            f"渴望：{self.desire}　恐惧：{self.fear}",
            f"说话风格：{self.speech_pattern}",
            f"能力/境界：{self.ability} / {self.realm}",
            f"动机：{self.motivation}　致命弱点：{self.fatal_flaw}",
            f"整体弧线：{self.arc}",
        ])


# ═══════════════════════════════════════════════════════
#  VII. 叙事线
# ═══════════════════════════════════════════════════════

@dataclass
class LinePhase:
    phase_index: int
    name: str
    description: str
    volume: int
    chapter_start: int
    chapter_end: int
    tension: TensionLevel
    completed: bool = False


@dataclass
class NarrativeLine:
    line_id: str
    line_type: LineType
    scope: LineScope
    name: str
    description: str
    characters: list[str]
    volume_range: tuple[int, int]
    phases: list[LinePhase] = field(default_factory=list)
    current_phase: int = 1
    resolved: bool = False

    def get_phase_for_chapter(self, chapter_index: int) -> Optional[LinePhase]:
        for p in self.phases:
            if p.chapter_start <= chapter_index <= p.chapter_end:
                return p
        return None

    def get_current_phase(self) -> Optional[LinePhase]:
        for p in self.phases:
            if p.phase_index == self.current_phase:
                return p
        return None

    def advance_phase(self):
        if self.current_phase < len(self.phases):
            self.current_phase += 1
        else:
            self.resolved = True


# ═══════════════════════════════════════════════════════
#  VIII. 卷
# ═══════════════════════════════════════════════════════

@dataclass
class Volume:
    index: int
    title: str
    theme: str
    arc: str
    chapter_start: int
    chapter_end: int
    opening_hook: str
    closing_hook: str
    volume_antagonist: str
    key_events: list[str] = field(default_factory=list)
    chapter_outlines: list[dict] = field(default_factory=list)
    # ↑ 每条字段约定（由 volume_planner.plan_volume_chapters 生成）：
    #   index           int       章号
    #   title           str       章标题
    #   goal            str       本章主线该推进什么（60 字内）
    #   position        str       卷首 / 普通 / 卷中高潮 / 卷尾
    #   stage_id        str       关联到 4_stage 的舞台 id（可空）
    #   chapter_focus   str       本章一件最重要的事（一句话，30 字）— NEW
    #   reader_hook     str       让读者翻下一页的具体钩子（具体到画面/对话/悬念，40 字）— NEW
    # （chapter_focus / reader_hook 是写章前作者可审、writer 必须命中的硬约束）
    # ── 分形起承转合 ──────────────────────────────────
    structure_role: str = ""   # 本卷在整本书起承转合中的角色："起"/"承"/"转"/"合"
                               # （若两卷合并承担一个角色，可为"起后半"/"承前半"等）
    purpose: str = ""          # 为什么要写这一卷（在全书中的作用，40字）
    expression: str = ""       # 本卷想表达什么（主题/情绪/信息增量，30字）

    @property
    def total_chapters(self) -> int:
        return self.chapter_end - self.chapter_start + 1


# ═══════════════════════════════════════════════════════
#  IX. 记忆系统
# ═══════════════════════════════════════════════════════

@dataclass
class MemoryEntry:
    chapter_index: int
    volume_index: int
    line_ids: list[str]
    event_type: str
    content: str
    tension: TensionLevel
    tags: list[str] = field(default_factory=list)


@dataclass
class MemoryBank:
    entries: list[MemoryEntry] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    character_states: dict[str, str] = field(default_factory=dict)

    def add(self, entry: MemoryEntry):
        self.entries.append(entry)

    def get_by_line(self, line_id: str, last_n: int = 5) -> list[MemoryEntry]:
        return [e for e in self.entries if line_id in e.line_ids][-last_n:]

    def get_recent(self, n: int = 5) -> list[MemoryEntry]:
        return self.entries[-n:]

    def format_line_memory(self, line_ids: list[str], last_n: int = 4) -> str:
        entries = []
        for lid in line_ids:
            entries.extend(self.get_by_line(lid, last_n))
        seen = {}
        for e in entries:
            seen[e.chapter_index] = e
        entries = sorted(seen.values(), key=lambda e: e.chapter_index)[-last_n:]
        if not entries:
            return "暂无相关记忆。"
        return "\n".join(
            f"[第{e.chapter_index}章/{e.event_type}/{e.tension.value}] {e.content}"
            for e in entries
        )


# ═══════════════════════════════════════════════════════
#  X. 章节结构
# ═══════════════════════════════════════════════════════

@dataclass
class SceneBeat:
    """章节内一个场景的蓝图。"""
    scene_index: int
    scene_type: str          # 场景自由描述（对峙/审讯/逃跑/密谋/突破/重逢...）
    location: str            # 场景发生地点
    characters: list[str]    # 出场角色
    content: str             # 这个场景发生什么（300字以内，尽量详尽——writer 据此写 2500-4000 字一幕）
    emotional_shift: str     # 情绪/局势在这个场景后的变化（20字）
    word_quota: int          # 建议字数
    # ── 分形起承转合 ──────────────────────────────────
    structure_role: str = ""   # 本场景在本章起承转合中的角色："起"/"承"/"转"/"合"
    purpose: str = ""          # 本场景的作用（20字，为什么要有这一幕）
    expression: str = ""       # 想表达的核心（15字）
    # ── 场景与上一幕的衔接方式（连续性控制）──────────────
    transition_type: str = "continuous"  # "continuous"（无缝，继续同一时刻/地点）
                                          # "soft_cut"（几分钟/小时过去，同一场景线）
                                          # "hard_cut"（真正切换时空/视角，须显著必要）
    transition_note: str = ""              # 衔接提示（20字，如"同一房间；他刚合上门"）
    # ── 细节锚点（P4：让 writer 不再"自由编剧"）──────────
    # 这两个字段是核心升级：content 只给骨架，锚点给血肉。
    # writer prompt 会要求"至少融入其中 2-3 条，不必全用，但不得 100% 忽略"
    dialogue_seeds: list[str] = field(default_factory=list)
    # 3-5 条示范对白/关键台词锚点（含角色身份，如 "师父（压低声）：此剑一出，便再无回头"）
    sensory_anchors: list[str] = field(default_factory=list)
    # 5-8 个感官细节候选（视/听/嗅/触/内感），写在 20-35 字内（如"门开缝漏出半寸烛光，带着松烟的焦味"）
    dramatic_beats: list[str] = field(default_factory=list)
    # 0-3 个戏剧节拍标记（15-25 字，如"主角抬手按住她的肩——但手停在半寸外"）


@dataclass
class HookSpec:
    """章末钩子规格——类型 + 50 字描述。

    chapter_planner 输出时填充;writer 按 hook_type 调整章末写法;
    critic 检查本卷同类型分布。
    """
    type: HookType                  # 钩子类型(7 种)
    text: str = ""                  # 具体钩子描述(50 字,如"门外传来师父的咳嗽声")


@dataclass
class ChapterBlueprint:
    """章节蓝图：场景级写作指令，确保每章有明确进展。"""
    chapter_index: int
    opening_state: str       # 承接上章——此刻的局面/悬念（40字）
    chapter_delta: str       # 本章核心进展：什么东西不可逆地改变了（30字）
    scene_beats: list[SceneBeat]
    closing_hook: str        # 结尾方向：最后留下什么悬念/情绪（40字）
    pacing_note: str         # 节奏备注（如"前紧后松"/"一直紧绷"）
    # ── 分形起承转合（章自身内部由 scene_beats 分担）──
    structure_role: str = ""   # 本章在所属小情节（SubScene）起承转合中的角色："起"/"承"/"转"/"合"
    purpose: str = ""          # 为什么必须写这一章（40字，不能是"推进剧情"这种空话）
    expression: str = ""       # 本章想表达什么（30字，主题/情绪/信息）
    # ── 钩子类型（Batch 3：防钩子单一化收敛）────────────────
    closing_hook_spec: Optional[HookSpec] = None  # 钩子类型 + 描述,空时降级到 closing_hook 字符串

@dataclass
class ChapterDirective:
    chapter_index: int
    volume_index: int
    tension: TensionLevel
    rhythm: RhythmType
    active_lines: list[str]
    primary_line: str
    must_include: list[str]
    satisfaction_points: list[str]
    foreshadow_plant: list[str]
    foreshadow_resolve: list[str]
    emotional_note: str
    chapter_position: str
    word_pace: str
    blueprint: Optional["ChapterBlueprint"] = None   # 场景蓝图（由ChapterPlannerAgent填充）
    # ── 分形起承转合（从 director 预填，chapter_planner 可补全/修正）──
    structure_role: str = ""   # 本章在所属小情节中的起承转合角色
    purpose: str = ""          # 为什么必须写这一章（40字）
    expression: str = ""       # 本章想表达什么（30字）
    # 完整结构定位链（供 writer/critic 参考）
    # 形如："整本[起] → 卷[承] → 大情节·青云试炼[转] → 小情节·内门考核[承] → 章[转]"
    structure_chain: str = ""
    # ── PreChapterBrief 扩展（Writer 的唯一输入，不让 writer 读全库）──
    chapter_type: str = ""                              # 本章类型：打脸章/升级章/铺垫章/感情章/战斗章/日常章/真相章/转折章
    character_states: dict[str, dict] = field(default_factory=dict)  # {角色名: {location, injury, emotion, items, realm}}
    forbidden_content: list[str] = field(default_factory=list)  # 本章禁止出现的内容（防剧透/设定冲突）
    red_herring_plant: list[str] = field(default_factory=list)  # 本章植入的红鲱鱼 rh_id
    red_herring_debunk: list[str] = field(default_factory=list)  # 本章揭穿的红鲱鱼 rh_id
    # ── 反转系统 ──────────────────────────────────────
    twist_reveals: list[str] = field(default_factory=list)  # 本章要揭露的反转层 ["chain_id:layer_num", ...]
    twist_clues_plant: list[str] = field(default_factory=list)  # 本章要埋的反转伏笔 ["chain_id:layer_num", ...]
    # ── 写作前的作者灵感（来自 Web UI "章节灵感"面板）──
    user_inspiration: str = ""                          # 作者想让本章包含的元素/桥段/情感/画面
    # ── 重写时的作者反馈（来自 Web UI 的"不满意重写"）──
    user_feedback: str = ""                             # 作者对上一版本不满意的具体反馈
    # ── 爽点 callback 锚点(由 director 在触发爽点的章填充, writer 必须精确引用)──
    callback_seeds: list[str] = field(default_factory=list)  # 格式: "[kind·第N章·counterpart] 「quote」 — summary"
    # ── 读者预期(Batch 5:expectation_manager 写章前预测,chapter_planner 标 decision)──
    reader_expectations: list[ReaderExpectation] = field(default_factory=list)


@dataclass
class ChapterPacingStats:
    """章节节奏统计——PacingAnalyzer 产出，用于和本卷同期章节对比。"""
    chapter_index: int
    dialogue_ratio: float = 0.0         # 对话占比（0-1）
    action_ratio: float = 0.0           # 动作占比
    description_ratio: float = 0.0      # 描写占比
    inner_monologue_ratio: float = 0.0  # 心理描写占比
    turns_per_1000_words: int = 0       # 每千字出现的转折/情绪位移次数
    deviation_note: str = ""            # 与本卷同期偏离情况（20字，"与第N章相比偏慢"等）


@dataclass
class ChapterSummary:
    index: int
    volume_index: int
    title: str
    summary: str
    word_count: int
    tension: TensionLevel
    key_events: list[str] = field(default_factory=list)
    lines_advanced: list[str] = field(default_factory=list)
    sp_triggered: list[str] = field(default_factory=list)
    closing_hook: str = ""   # 本章结尾悬念/钩子，供下章开头承接
    pacing_stats: Optional["ChapterPacingStats"] = None  # 由 PacingAnalyzer 填充
    # ── P11 actual vs planned 回传 ─────────────────────
    # writer 写完后存"实际发生"vs"蓝图计划"的差异；后续审计/分析可用
    planned_scene_count: int = 0       # 蓝图原计划幕数
    actual_scene_count: int = 0        # 实际写出的幕数（按段落估算或 LLM 回填）
    deviations: list[str] = field(default_factory=list)
                                       # 偏差描述：哪些蓝图要求被改动了
                                       # 例："蓝图要求第 2 幕反派揭面，实际未发生"
    blueprint_compliance: int = 100    # 0-100 蓝图遵循度（100=完全按蓝图）
    # ── SetupLedger 章后扫稿提取(本章兑现了哪些 entry_id)─────
    setup_callbacks_invoked: list[str] = field(default_factory=list)
    # ── HookType 历史(Batch 3:critic 检查同类型连发扣分)─────
    closing_hook_type: str = ""        # HookType.value 或空字符串
    # ── 模拟读者评论(Batch 5:comment_simulator 章后生成)──────
    simulated_comments: list[SimulatedComment] = field(default_factory=list)
    # ── critic 最后一轮评分快照(Batch P2:UI 可视化用,不参与逻辑)─────
    # 含 score / passed / dim_scores(10+ 维) / sp_check / fw_check / feedback / highlights
    critic_review: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
#  XI-B. 实时故事线索（StoryThread）
#  每章写完后由 ThreadTrackerAgent 更新，作为下章精确起点
# ═══════════════════════════════════════════════════════

@dataclass
class OpenLoop:
    """
    开放循环——一个悬而未决的情况，每章必须推进或解决至少一个。
    例："主角被通缉，3天内必须离开青云城""师父生死未卜""某人知道了秘密"
    """
    loop_id: str
    description: str         # 具体情况（50字内）
    urgency: str             # 紧急/持续/潜伏
    opened_chapter: int
    target_close_chapter: int  # 预计哪章解决，-1=未定
    current_progress: str    # 当前进展（30字，每章更新）
    closed: bool = False


@dataclass
class StoryThread:
    """
    实时故事状态——每章写完后更新，作为下章写作的精确起点。
    解决章节割裂和情节雷同的核心机制。
    """
    # 当前物理状态
    current_location: str = ""
    current_time_context: str = ""

    # 主角当前目标与阻碍（场景级）
    protagonist_immediate_goal: str = ""
    protagonist_immediate_obstacle: str = ""
    protagonist_emotional_state: str = ""

    # 开放循环（悬而未决的情况，每章必须推进至少一个）
    open_loops: list[OpenLoop] = field(default_factory=list)

    # 章节末尾精确状态
    scene_end_state: str = ""
    next_chapter_opening: str = ""

    # 多线并行状态（编织多条线索的核心）
    parallel_events: list[str] = field(default_factory=list)          # 其他角色正在做什么
    background_developments: list[str] = field(default_factory=list)  # 主角未察觉的暗中发展

    # 当前关系张力
    active_tensions: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
#  XI-C. 机缘系统（Fortune）
# ═══════════════════════════════════════════════════════

@dataclass
class Fortune:
    """
    主角的机缘——成长燃料。
    每个机缘有具体的地点、获取条件、对成长的影响。
    """
    fortune_id: str
    fortune_type: str          # 传承/宝物/奇遇/贵人/功法/天材地宝/秘境/领悟
    name: str                  # 机缘名称（如"太古剑意残影"）
    description: str           # 机缘内容描述
    location_desc: str         # 在哪里（场景描述）
    stage_id: str = ""         # 关联的叙事舞台ID
    acquisition_method: str = ""   # 获取方式（争夺/偶然发现/师传/任务奖励/以命换取）
    prerequisite: str = ""         # 前提条件（力量门槛/情节前提）
    volume: int = 1
    target_chapter: int = -1       # 预计获得章节
    effect_on_growth: str = ""     # 对主角成长的具体影响（解锁什么能力/境界）
    narrative_hook: str = ""       # 这个机缘如何引发后续剧情
    obtained: bool = False
    actual_chapter: int = -1


# ═══════════════════════════════════════════════════════
#  XI-D. 叙事舞台系统（Story Stage）
#  每卷预先设计2-5个叙事容器，章节在这些舞台中发生
# ═══════════════════════════════════════════════════════

@dataclass
class SubScene:
    """
    子场景——大舞台内的具体活动容器，相当于【小情节】层级。
    例：宗门大舞台下 → 内门考核/资源争夺/秘法阁查阅 等子场景。
    子场景可以交叉活跃（同时进行多个）。
    """
    sub_id: str
    name: str
    sub_type: str              # 竞技/探索/社交/战斗/交易/修炼/追查/谈判
    description: str
    chapter_start: int
    chapter_end: int
    key_events: list[str] = field(default_factory=list)   # 这里会发生的关键事件
    fortune_ids: list[str] = field(default_factory=list)  # 可在此获得的机缘ID
    # ── 分形起承转合 ──────────────────────────────────
    structure_role: str = ""   # 本小情节在所属大情节（StoryStage）中的角色："起"/"承"/"转"/"合"
    purpose: str = ""          # 为什么要有这个小情节（30字）
    expression: str = ""       # 想让读者感受到什么（25字）


@dataclass
class StoryStage:
    """
    叙事舞台——一卷内的故事容器，相当于【大情节】层级。
    主角在2-5个舞台中穿梭，舞台可以重叠、交叉（如"在城市舞台中参加拍卖会子场景"）。
    """
    stage_id: str
    name: str
    stage_type: str            # 宗门/秘境/战场/市井/旅途/竞技场/特殊事件/幕后阴谋
    volume: int
    chapter_start: int
    chapter_end: int
    setting_desc: str          # 场景环境/氛围（供写作参考，100字内）
    atmosphere: str            # 整体氛围基调（如"暗流涌动的表面平静""生死相搏的极限压力"）
    protagonist_role: str      # 主角在此舞台的身份/处境
    key_activities: list[str] = field(default_factory=list)   # 主要活动类型
    sub_scenes: list[SubScene] = field(default_factory=list)
    fortune_ids: list[str] = field(default_factory=list)      # 本舞台可获得的机缘
    transition_in: str = ""    # 进入这个舞台的方式
    transition_out: str = ""   # 离开时的过渡
    parallel_stage_ids: list[str] = field(default_factory=list)  # 同期并行的其他舞台
    active: bool = True
    # ── 分形起承转合 ──────────────────────────────────
    structure_role: str = ""   # 本大情节在所属卷的起承转合中的角色："起"/"承"/"转"/"合"
    purpose: str = ""          # 为什么要安排这个大情节（它承担卷中什么使命，40字）
    expression: str = ""       # 想让读者从这段故事感受到什么（30字）




# ═══════════════════════════════════════════════════════
#  XI-E. 关系网络（RelationshipWeb）
#  完整的人物关系图谱，包含明暗双层关系、秘密、关系演变
# ═══════════════════════════════════════════════════════

@dataclass
class CharacterBond:
    """
    两个角色之间的立体关系。
    表层关系（读者/主角最初认知） vs 真实关系（作者视角）。
    """
    bond_id: str
    char_a: str
    char_b: str
    surface_relation: str       # 表面关系（如"师徒"/"陌生人"）
    true_relation: str          # 真实关系（可以与表面相同，也可完全不同）
    hidden_secret: str          # 一方或双方不知道的秘密（如"char_a是char_b失散多年的儿子"）
    tension_source: str         # 关系张力来源（什么会让他们冲突）
    # 关系在各卷如何演变 {卷号: "这卷关系变化描述"}
    volume_evolution: dict[int, str] = field(default_factory=dict)
    # 哪卷揭露真实关系/秘密（-1=从不揭露）
    reveal_volume: int = -1
    # 关系是否对主角有直接影响
    affects_protagonist: bool = True
    # ── RelationshipMatrix 增强：未来走向 ──────────
    future_trajectory: str = ""                                       # 未来预期走向（50字，如"第3卷反目→第5卷和解"）
    projected_changes: dict[int, str] = field(default_factory=dict)   # {卷号: 预计变化的具体描述}——比 volume_evolution 更具体


@dataclass
class RelationshipWeb:
    """
    完整关系网络——全书人物关系图谱。
    """
    bonds: list[CharacterBond] = field(default_factory=list)
    # 权力链条：谁暗中控制谁 ["A→B（通过XX方式）"]
    power_chains: list[str] = field(default_factory=list)
    # 隐藏同盟：读者前期不知道的联盟
    hidden_alliances: list[str] = field(default_factory=list)
    # 人物归属：{角色名: [势力名1, 势力名2]}（可属多个势力）
    faction_affiliations: dict[str, list[str]] = field(default_factory=dict)

    def get_bonds_for_char(self, name: str) -> list[CharacterBond]:
        return [b for b in self.bonds if b.char_a == name or b.char_b == name]

    def get_bond(self, a: str, b: str) -> Optional["CharacterBond"]:
        for bond in self.bonds:
            if (bond.char_a == a and bond.char_b == b) or \
               (bond.char_a == b and bond.char_b == a):
                return bond
        return None

    def matrix_view_for_char(self, name: str) -> str:
        """
        返回某角色的关系矩阵式文本——看清他与谁有什么关系、当前状态、未来走向。
        供 chapter_planner/writer 在角色出场时参考。
        """
        lines = [f"【{name} 的关系矩阵】"]
        my_bonds = self.get_bonds_for_char(name)
        if not my_bonds:
            return lines[0] + "（无已设计关系）"
        for b in my_bonds:
            other = b.char_b if b.char_a == name else b.char_a
            lines.append(
                f"  ↔ {other}：表面[{b.surface_relation}] 实际[{b.true_relation}]"
                f" 张力：{b.tension_source[:25]}"
            )
            if b.future_trajectory:
                lines.append(f"      走向：{b.future_trajectory[:50]}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════
#  XI-F. 主角历程（ProtagonistJourney）
#  三层规划：整体弧线 → 卷级里程碑 → 舞台级节拍
#  不直接到章节，让章节规划有更深的根基
# ═══════════════════════════════════════════════════════

@dataclass
class ProtagonistMilestone:
    """
    主角在某一卷的核心里程碑。
    描述卷首→卷尾的状态变化，不规定具体章节。
    """
    volume: int
    # 卷首状态（情感/力量/处境）
    entry_state: str
    # 卷尾状态
    exit_state: str
    # 这一卷主角最重要的内心成长
    inner_growth: str
    # 这一卷外部最重要的改变
    outer_change: str
    # 这一卷主角与哪些人产生了关键化学反应（可以是正向或负向）
    key_relationships: list[str] = field(default_factory=list)
    # 这一卷主角面临的核心内心冲突
    inner_conflict: str = ""
    # 这一卷主角被迫做出的最艰难选择（塑造人物的时刻）
    hardest_choice: str = ""
    # 主角卷内最低谷（让读者揪心的时刻）
    darkest_moment: str = ""
    # 主角卷内最高光（让读者爽的时刻）
    triumph_moment: str = ""


@dataclass
class ProtagonistStageBeat:
    """
    主角在某个叙事舞台中的经历节拍。
    承接卷级里程碑，比卷级更细，比章级更粗。
    """
    beat_id: str
    stage_id: str               # 对应叙事舞台ID
    volume: int
    # 进入舞台时的状态
    entry_state: str
    # 离开舞台时的状态
    exit_state: str
    # 在这个舞台里的核心行动（3-5件事）
    key_actions: list[str] = field(default_factory=list)
    # 在这个舞台里关系的变化
    relationship_shifts: list[str] = field(default_factory=list)
    # 在这个舞台里获得/失去了什么
    gained: str = ""
    lost: str = ""
    # 这个舞台对应哪个卷级里程碑的哪个阶段
    milestone_phase: str = ""   # "起"/"承"/"转"/"合"


@dataclass
class ProtagonistJourney:
    """
    主角完整历程规划。
    三层结构：整体弧线 → 卷级里程碑 → 舞台级节拍
    """
    # 整体弧线
    overall_theme: str = ""         # 主角的故事主题（如"从孤儿到天下第一，寻找存在意义"）
    core_wound: str = ""            # 驱动主角前进的根源创伤
    true_goal: str = ""             # 主角真正追求的（可能与表面目标不同）
    fatal_flaw: str = ""            # 主角会反复被这个弱点阻碍
    # 主角与命运/反派的核心矛盾
    central_conflict: str = ""
    # 全书主角的成长轨迹（一段话）
    growth_arc: str = ""

    # 卷级里程碑（每卷一个）
    milestones: list[ProtagonistMilestone] = field(default_factory=list)

    # 舞台级节拍（每个叙事舞台一个）
    stage_beats: list[ProtagonistStageBeat] = field(default_factory=list)

    def get_milestone(self, volume: int) -> Optional["ProtagonistMilestone"]:
        for m in self.milestones:
            if m.volume == volume:
                return m
        return None

    def get_stage_beat(self, stage_id: str) -> Optional["ProtagonistStageBeat"]:
        for b in self.stage_beats:
            if b.stage_id == stage_id:
                return b
        return None

    def milestone_brief(self, volume: int) -> str:
        m = self.get_milestone(volume)
        if not m:
            return ""
        return (f"[第{volume}卷] 入：{m.entry_state} → 出：{m.exit_state}\n"
                f"  内心成长：{m.inner_growth} | 最艰难选择：{m.hardest_choice}")


# ═══════════════════════════════════════════════════════
#  XII. 地理系统（GeographyDesigner）
# ═══════════════════════════════════════════════════════

@dataclass
class GeoRegion:
    """地理区划——大陆/国家/州郡/城镇/村落，层级结构。"""
    region_id: str
    name: str
    level: str                      # "大陆"|"国家"|"州郡"|"城镇"|"村落"|"秘境"
    parent_id: str = ""             # 上级区划id（空=顶级）
    description: str = ""           # 概况（按 detail_level 决定长度）
    climate: str = ""               # 气候
    products: str = ""              # 物产
    culture_notes: str = ""         # 风土/文化
    notable_spots: list[str] = field(default_factory=list)  # 本区划内的著名地点
    # ── 按"主角活跃度"分级描写 ────────────────────────
    importance: str = "background"  # "protagonist_active"（主角深入） / "occasional"（途经）/ "background"（仅提及）
    detail_level: int = 1           # 1=brief轮廓 / 2=常规 / 3=精细（含街巷/氛围/人物）
    protagonist_arc_note: str = ""  # "主角第 1-2 卷在这"——说明本区在主角旅程中的定位
    atmosphere: str = ""            # 氛围基调（冷峻/繁华/肃杀/市井/仙气 等，20字）
    key_scenes: list[str] = field(default_factory=list)  # 本区内会发生的关键场景锚点（给下游 Stage/Writer 用）


@dataclass
class TransportMode:
    """交通方式——影响"从A到B要多久"的核心依据。"""
    name: str                       # 步行/骑马/御剑/法船/传送阵 等
    speed_description: str          # 人话描述速度（如"日行百里""瞬息千里"）
    realm_required: str = ""        # 最低境界要求（空=任何人可用）
    cost: str = ""                  # 消耗（如"大量灵石""法力消耗"）


@dataclass
class TravelDistance:
    """两地距离条目——建成距离矩阵。"""
    from_region: str                # 起点 region_id 或地名
    to_region: str                  # 终点
    distance_desc: str              # "三千里"/"一个月脚程" 等
    travel_time_by_mode: dict[str, str] = field(default_factory=dict)  # {"骑马":"7天","御剑":"半日"}


@dataclass
class RouteStage:
    """主角路线的一站——按卷展开。"""
    volume: int                     # 哪一卷
    primary_region_id: str = ""     # 本卷主要活动区
    visited_region_ids: list[str] = field(default_factory=list)  # 本卷内会去的所有区（按访问顺序）
    arc_note: str = ""              # 本卷地理弧线（如"从家乡出走→抵达剑派"，40字）


@dataclass
class Geography:
    regions: list[GeoRegion] = field(default_factory=list)
    transport_modes: list[TransportMode] = field(default_factory=list)
    distances: list[TravelDistance] = field(default_factory=list)
    world_map_desc: str = ""        # 综合性地图描述（100字，向后兼容字段）
    # ── 新：天下布局 + 主角路线 ──────────────────────
    world_layout: str = ""          # 天下布局（200-300字）——整个世界格局俯瞰
    protagonist_route: list[RouteStage] = field(default_factory=list)  # 按卷的路线

    def get_region(self, region_id: str) -> Optional[GeoRegion]:
        for r in self.regions:
            if r.region_id == region_id:
                return r
        return None

    def brief_for_volume(self, max_chars: int = 300) -> str:
        """给 writer/chapter_planner 的精简地理上下文。"""
        if not self.regions:
            return ""
        lines = []
        for level in ("大陆", "国家", "州郡"):
            items = [r for r in self.regions if r.level == level]
            if items:
                lines.append(f"[{level}] " + " / ".join(r.name for r in items[:6]))
        if self.transport_modes:
            tm = " / ".join(f"{m.name}({m.speed_description})" for m in self.transport_modes[:4])
            lines.append(f"[交通] {tm}")
        result = "\n".join(lines)
        return result[:max_chars]


# ═══════════════════════════════════════════════════════
#  XIII. 时间锚点（TimelineAnchor）
# ═══════════════════════════════════════════════════════

@dataclass
class TimelineEvent:
    """历史事件——伏笔的温床。"""
    event_id: str
    era: str                        # "上古"/"中古"/"近代"/"当代"
    years_ago: int                  # 距离当前剧情的年数（负数=未来预言）
    name: str
    description: str                # 事件描述（60字）
    consequences: str               # 对当前世界的影响（40字）
    related_factions: list[str] = field(default_factory=list)  # 涉及的势力
    foreshadow_potential: str = ""  # 可作为伏笔的角度（30字）


@dataclass
class Timeline:
    events: list[TimelineEvent] = field(default_factory=list)
    current_era: str = ""           # 当前剧情所处的纪元名
    current_year_desc: str = ""     # 当前年份描述（如"大夏第三千二百零七年"）

    def events_sorted(self) -> list[TimelineEvent]:
        return sorted(self.events, key=lambda e: -e.years_ago)  # 最久远的在前

    def brief(self, max_events: int = 6) -> str:
        if not self.events:
            return ""
        lines = [f"【当前纪元】{self.current_era}（{self.current_year_desc}）" if self.current_era else ""]
        for e in self.events_sorted()[:max_events]:
            lines.append(f"· [{e.years_ago}年前·{e.era}]《{e.name}》：{e.description[:40]}")
        return "\n".join(filter(None, lines))


# ═══════════════════════════════════════════════════════
#  XIV. 经济系统（EconomySystem）
# ═══════════════════════════════════════════════════════

@dataclass
class Currency:
    """货币种类——比如"下品灵石/中品灵石/金币/贡献点"。"""
    name: str
    rank: int                       # 1=最基础；越大越珍贵
    exchange_to_base: int = 1       # 相对 rank=1 的基础货币的兑换比率
    notes: str = ""                 # 用途说明


@dataclass
class PriceAnchor:
    """物价锚点——让 LLM 写"一千两银子"时知道这是巨款还是零花。"""
    item: str                       # 物品/服务（如"一顿便饭""一件凡品法器""一颗回春丹"）
    price: str                      # 价格（如"1 两银子"或"10 下品灵石"）
    tier: str                       # "平民日常"/"修炼者入门"/"珍稀"/"逆天"


@dataclass
class WealthTierPoint:
    """主角在某卷末的财富状态。"""
    volume: int
    tier: str                       # "赤贫"/"温饱"/"小康"/"富足"/"巨富"/"富可敌国"
    description: str                # 本卷末主角的财富具体情况（40字）


@dataclass
class Economy:
    currencies: list[Currency] = field(default_factory=list)
    price_anchors: list[PriceAnchor] = field(default_factory=list)
    protagonist_wealth_curve: list[WealthTierPoint] = field(default_factory=list)
    trade_notes: str = ""           # 特殊经济现象（如"灵石产量稀少导致通货紧缩""某国独占某资源"）

    def brief(self, max_chars: int = 300) -> str:
        lines = []
        if self.currencies:
            cs = " / ".join(f"{c.name}(1={c.exchange_to_base}x)" for c in self.currencies[:5])
            lines.append(f"[货币] {cs}")
        if self.price_anchors:
            # 选几个跨档次的锚点
            tiers_seen = set()
            picks = []
            for a in self.price_anchors:
                if a.tier not in tiers_seen:
                    picks.append(a)
                    tiers_seen.add(a.tier)
                if len(picks) >= 4:
                    break
            if picks:
                lines.append("[物价锚] " + " / ".join(f"{a.item}={a.price}" for a in picks))
        result = "\n".join(lines)
        return result[:max_chars]

    def wealth_at_volume(self, volume_index: int) -> str:
        for w in self.protagonist_wealth_curve:
            if w.volume == volume_index:
                return f"{w.tier}：{w.description}"
        return ""


# ═══════════════════════════════════════════════════════
#  XV. 人物弧光（CharacterArc）—— 每个主要角色的心理弧
# ═══════════════════════════════════════════════════════

@dataclass
class ArcTransition:
    """人物弧的一个转折点。"""
    volume: int
    chapter_approx: int             # 大致章节（-1=未定）
    trigger_event: str              # 触发事件（50字）
    state_before: str               # 变化前（30字）
    state_after: str                # 变化后（30字）
    inner_change: str               # 内心发生了什么（40字）
    # 如果这次心理转折由某个 SpecialAsset 的生命周期节点驱动（如"获得豆包后的恍然 / 失去
    # 神剑后的崩溃"），填 asset 名；空 = 与 asset 无关。由 ability_roadmap_planner 反向标。
    ability_trigger: str = ""


@dataclass
class CharacterArc:
    """
    人物心理弧——与 LinePlanner 正交：LinePlanner 管剧情线，CharacterArc 管内心线。
    每个主要人物一条，详细规划心理转变的触发点。
    """
    character_name: str
    theme: str                      # 弧线主题（20字，如"从懦弱到担当"/"从天才傲气到谦卑"）
    start_state: str                # 起点（50字：性格缺陷+认知局限）
    end_state: str                  # 终点（50字：蜕变后）
    transitions: list[ArcTransition] = field(default_factory=list)

    def brief(self) -> str:
        return f"【{self.character_name}·弧线】{self.theme}：{self.start_state[:30]} → {self.end_state[:30]}"


@dataclass
class IntentRevision:
    """单次意图追加记录——作者可以在已有意图上多次补充。"""
    timestamp: str = ""                 # ISO 时间戳
    addition: str = ""                  # 本次追加的文本
    round_index: int = 0                # 第几轮（1 是首次分析，之后递增）
    summary: str = ""                   # 本轮分析后 AI 的一句话概括（变化点）


@dataclass
class PlotSupplement:
    """
    plot_enhancer（Phase -0.7）产出的"补充情节建议"——让系统主动反问
    "作者意图够不够吸引读者"，补 3-5 个钩子供作者审。

    采纳后下游 agent 必须落地：
      · satisfaction_system 把它转成具体爽点（payoff/setup）
      · foreshadow_manager 把它转成伏笔
      · twist_designer 把它转成反转层
      · volume_planner 引用到具体卷/章 outline

    intensity: low / mid / high —— 决定下游落地的强度（low=暗线伏笔；high=主线钩子）
    adopted: 作者是否采纳；None = 待审，True = 采纳，False = 拒绝
    """
    name: str = ""                  # 短名（10-15 字，给作者看的标题）
    what: str = ""                  # 具体补什么（60 字）
    why_engaging: str = ""          # 为什么这能让读者留下（40 字）
    where_to_inject: str = ""       # 建议注入到哪——卷/章范围（如"第 1 卷中段"）
    intensity: str = "mid"          # low / mid / high
    adopted: Optional[bool] = None  # None 待审 / True 采纳 / False 拒绝
    notes: str = ""                 # 作者审核时加的备注


@dataclass
class CreativeIntent:
    """
    创作意图层（Phase -1）—— 作者用自然语言描述心里想写什么。
    intent_analyzer 把它解析成结构化信号，作为 Phase 0 三件套的"硬约束"。
    优先级：creative_intent.* > config.py seeds > LLM 自由发挥。

    支持多次追加：raw_description 是当前完整意图（首轮 + 所有后续追加拼接）；
    revisions 留历史。每次 refine_intent 调用时会把 addition 追加到 raw_description
    并重跑 intent_analyzer。
    """
    raw_description: str = ""           # 当前完整意图（首次输入 + 所有追加拼接）
    analyzed: bool = False              # 是否已经 LLM 分析过
    revisions: list[IntentRevision] = field(default_factory=list)  # 历次追加记录

    # ── 从描述里提取的题材类信号 ────────────────────
    suggested_title: str = ""           # 从描述推荐的书名（可选，作者可不采纳）
    suggested_genre: str = ""           # 玄幻/都市/科幻/言情/悬疑 等
    suggested_subgenre: str = ""        # 子类型：穿越 / 重生 / 系统 / 升级流 / 反套路 / 无特殊
                                        # 驱动 chapter_dispatcher 对开篇章的路由
    suggested_theme: str = ""           # 一段话主题（80字）

    # ── 立项类硬约束（喂给 ConceptPitch）──────────────
    audience_hint: str = ""             # 男频|女频|混合
    age_group_hint: str = ""            # 年龄段
    platform_hint: str = ""             # 起点/晋江/番茄/书旗/QQ阅读/飞卢
    selling_points_hints: list[str] = field(default_factory=list)
    benchmark_hints: list[str] = field(default_factory=list)
    differentiation_hint: str = ""      # 与对标的差异化（30字）

    # ── 套路类硬约束（喂给 TropeLibrary）──────────────
    embrace_tropes_hints: list[str] = field(default_factory=list)
    avoid_tropes_hints: list[str] = field(default_factory=list)
    preferred_sp_types_hints: list[str] = field(default_factory=list)
    villain_policy_hint: str = ""
    romance_policy_hint: str = ""
    harem_policy_hint: str = ""
    protagonist_archetype_hint: str = ""
    world_tone_hint: str = ""

    # ── 文风类硬约束（喂给 ToneManual）────────────────
    narrative_voice_hint: str = ""
    style_reference_hint: str = ""
    dialogue_style_hint: str = ""

    # ── 综合性基调摘要（一段话，所有下游 prompt 都引用）
    tone_summary: str = ""              # 100字，描述这本书的整体气质
    analyzer_notes: str = ""            # LLM 分析时的额外说明（给作者看）

    # ── 故事根基（真实 vs 虚构）─────────────────────
    # 由"⓪ 故事根基"问答 / IntentAnalyzer 自动推断 / 用户在面板上手动改 三种途径写入。
    # 决定下游 character_designer / world_builder / writer / canon_checker 是否
    # 把"真实历史人物言行"作为硬约束。
    #   "real_history"   严格基于真实历史——朝代/事件/人物言行须符合史料
    #   "real_adapted"   基于真实人物或事件改编——大方向尊重，细节可演绎
    #   "fictional"      完全虚构——人物事件均可自由编撰
    #   ""               未指定（兜底按 fictional 处理）
    reality_basis: str = ""
    respect_real_figures: bool = False  # 是否强制尊重 real_persons 名单的言行（仅 real_history/real_adapted）
    real_persons: list[str] = field(default_factory=list)  # 要尊重史实的真实人物名单（如 "李世民"/"诸葛亮"）
    historical_setting: str = ""        # 历史背景描述（朝代/时期/区域，仅 real_* 模式下有意义）

    # ── 补充情节（plot_enhancer Phase -0.7 产物）─────
    # 让系统主动反问"只看作者写的会不会无聊"——补 3-5 个能吸引读者的情节钩子。
    # 由 LLM 生成 + 作者在 web UI 审（采纳/拒绝）；采纳的会被 satisfaction_system /
    # foreshadow_manager / twist_designer / volume_planner 引用并落地为具体设计。
    plot_supplements: list["PlotSupplement"] = field(default_factory=list)


@dataclass
class ConceptPitch:
    """
    Phase 0：创作立项——一句话梗概 + 卖点 + 读者画像 + 对标 + 预期规模。
    所有下游 agent 的创作取向基准。
    """
    one_line_pitch: str = ""                 # 一句话梗概（30字内）
    core_selling_points: list[str] = field(default_factory=list)  # 3-5 个核心卖点
    target_audience: str = ""                # 男频|女频|混合
    target_age_group: str = ""               # 目标年龄段（如"18-30"）
    target_platform: str = ""                # 起点|晋江|番茄|书旗|QQ阅读|飞卢 等
    reader_profile: str = ""                 # 综合读者画像（60字：在什么心境下会读这本书）
    benchmark_works: list[str] = field(default_factory=list)  # 2-3 本对标作品
    differentiation: str = ""                # 与对标的差异化点（60字，讲清"为什么读我不读他们"）
    expected_total_words: int = 0            # 预期总字数
    expected_volumes: int = 0
    expected_completion_weeks: int = 0       # 预期完本周期


@dataclass
class TropeLibrary:
    """
    Phase 0：套路库——哪些梗要用，哪些要规避，爽点类型偏好，反派/感情处理原则。
    直接喂给 SatisfactionSystem / CharacterDesigner / Writer。
    """
    embrace_tropes: list[str] = field(default_factory=list)  # 要拥抱的经典套路（如"扮猪吃虎""扫地僧""反派洗白"）
    avoid_tropes: list[str] = field(default_factory=list)   # 要规避的烂梗（如"师门叛徒""女主圣母"）
    preferred_sp_types: list[str] = field(default_factory=list)  # 爽点偏好（权力爽/情感爽/升级爽/打脸爽/真相爽）
    villain_policy: str = ""                 # 反派处理（洗白型/彻底黑化型/灰色模糊型/人格魅力型）
    romance_policy: str = ""                 # 感情线处理（甜宠/虐恋/轻感情/发糖+撒糖/无感情线）
    harem_policy: str = ""                   # 后宫（单恋专一/双女主/多女主/不涉及）
    protagonist_archetype: str = ""          # 主角原型（逆袭型/天才型/苟道型/腹黑型/热血型/成熟型/萝莉化）
    world_tone: str = ""                     # 世界基调（热血/沉郁/轻松/黑暗/治愈/古典）


@dataclass
class ToneManual:
    """
    Phase 0：文风手册——Writer 和 Critic 的共同基准。
    决定句子的质感。
    """
    narrative_voice: str = ""                # 第一人称|第三人称限知|上帝视角|多视角切换
    style_reference: str = ""                # 笔触参考（如"天蚕土豆的热血感 + 烽火戏诸侯的诗意"）
    prose_rhythm: str = ""                   # 节奏倾向（如"长短句交织""短句密集""骈散结合"）
    dialogue_style: str = ""                 # 古风|现代|半文半白|诗化|口语化
    sensory_weight: str = ""                 # 感官侧重（视觉/听觉/触觉/嗅觉——偏向哪一两种）
    banned_words: list[str] = field(default_factory=list)   # 禁用词（如"仿佛""似乎""突然""然而"）
    careful_words: list[str] = field(default_factory=list)  # 慎用词（如"笑了笑""点了点头"这种万能动作）
    metaphor_preference: str = ""            # 比喻/意象偏好（如"自然物为主""避免现代词汇比喻"）
    opening_habit: str = ""                  # 段落/章节开头习惯（如"从一个具体动作或声音切入"）


# ═══════════════════════════════════════════════════════
#  章节类型规划（ChapterTypePlan）
# ═══════════════════════════════════════════════════════

CHAPTER_TYPES = [
    "铺垫章", "推进章", "日常章", "感情章", "战斗章",
    "打脸章", "升级章", "真相章", "转折章", "余韵章",
]


@dataclass
class ChapterTypeAssignment:
    chapter_index: int
    chapter_type: str               # 上列 CHAPTER_TYPES 之一
    reason: str                     # 为什么这一章是这个类型（30字）


@dataclass
class VolumeChapterTypeDistribution:
    """单卷的章节类型分布 + 具体章节分派。"""
    volume: int
    type_distribution: dict[str, int] = field(default_factory=dict)  # {"铺垫章": 10, "战斗章": 5, ...}
    per_chapter: list[ChapterTypeAssignment] = field(default_factory=list)

    def type_for_chapter(self, chapter_index: int) -> str:
        for a in self.per_chapter:
            if a.chapter_index == chapter_index:
                return a.chapter_type
        return ""


# ═══════════════════════════════════════════════════════
#  状态快照（StateUpdater 产出）
# ═══════════════════════════════════════════════════════

@dataclass
class CharacterStateSnapshot:
    """每章写完后，主要角色的精确状态快照——供下章 PreChapterBrief 使用。"""
    chapter_index: int
    location: str = ""              # 当前地点
    injury: str = ""                # 伤势（空=无伤）
    emotion: str = ""               # 当前情绪
    items_on_hand: list[str] = field(default_factory=list)  # 手头重要物品
    realm: str = ""                 # 当前境界
    relationship_changes: list[str] = field(default_factory=list)  # 本章与谁的关系变化


@dataclass
class WorldEvent:
    """世界层面的重大事件日历——StateUpdater 维护。"""
    chapter_index: int
    event_desc: str                 # 事件描述（60字）
    affected_factions: list[str] = field(default_factory=list)
    affected_regions: list[str] = field(default_factory=list)
    importance: str = "普通"        # 普通 | 重大 | 里程碑


# ═══════════════════════════════════════════════════════
#  术语表（Glossary）—— 写作过程中涌现的专有名词统一管理
# ═══════════════════════════════════════════════════════

@dataclass
class GlossaryEntry:
    """
    一个专有名词条目——地名/人名/功法/境界/法器/组织/阵法 等。
    防止同一事物后续章节换名字的事故（术语漂移）。
    """
    term: str                       # 规范名称
    category: str                   # 地名 / 人名 / 功法 / 法器 / 组织 / 阵法 / 境界 / 其他
    definition: str                 # 定义（40字）
    first_appeared_chapter: int     # 首次出现的章节
    aliases: list[str] = field(default_factory=list)  # 同义别名（含曾用名）


# ═══════════════════════════════════════════════════════
#  版本快照索引（VersionControl）
# ═══════════════════════════════════════════════════════

@dataclass
class VersionSnapshot:
    """标记一次 state 快照——state 正文存在 history/state_<timestamp>.json 里。"""
    timestamp: str                  # YYYYMMDD_HHMMSS
    label: str                      # 触发快照的事件（如"phase_1A_complete"/"chapter_42_done"）
    phase: str = ""
    chapter_index: int = -1
    notes: str = ""


# ═══════════════════════════════════════════════════════
#  人工审核队列（HumanInTheLoop）
# ═══════════════════════════════════════════════════════

@dataclass
class PendingApproval:
    """等待人工审核的节点——director 碰到就暂停并写入 approval 文件。"""
    approval_id: str
    reason: str                     # 为什么要人审（"卷2开始前"/"主角跨大境界"/"主线伏笔回收"）
    trigger_chapter: int = -1
    trigger_phase: str = ""
    created_at: str = ""
    approved: bool = False
    approver_note: str = ""


@dataclass
class ChatMessage:
    """章节对话调整（chapter chat）里的一条消息。
    assistant 消息的 content 就是 AI 那一轮返回的完整新版章节正文。"""
    role: str                       # "user" | "assistant"
    content: str
    ts: str = ""                    # ISO 时间戳


@dataclass
class AbilityUse:
    """章节中一次金手指/技能使用记录（auditor 提取）。"""
    ability_name: str               # 能力/金手指名称（如"豆包AI"/"剑意"）
    how_used: str                   # 怎么用的（50字）
    cost_paid: str                  # 付出的代价（30字，"无"=没付）
    setting_match: bool             # 是否符合设定边界
    notes: str = ""                 # auditor 备注（如"轻微超纲"）


@dataclass
class AbilityIssue:
    """auditor 发现的一个问题。"""
    type: str                       # overuse | overreach | no_cost | scale_mismatch | underuse | other
    severity: str                   # minor | major | critical
    description: str                # 问题描述（60字）
    suggested_fix: str = ""         # 建议的修改方向（40字）


@dataclass
class AbilityAudit:
    """章节后对金手指/技能使用合理性的审计结果。"""
    chapter_index: int
    ability_uses: list[AbilityUse] = field(default_factory=list)
    issues: list[AbilityIssue] = field(default_factory=list)
    overall_score: int = 10         # 1-10，越低问题越多
    summary: str = ""               # 一句话总结（30字）
    ts: str = ""                    # ISO 时间戳
    # 可选：审计模型/prompt 版本，便于后续追溯
    auditor_model: str = ""


# ═══════════════════════════════════════════════════════
#  读者视角审计（ReaderExperience）
# ═══════════════════════════════════════════════════════

@dataclass
class ReaderExperienceIssue:
    """从读者视角看的问题——会让读者想弃书的点。"""
    type: str                       # info_overload | character_dump | premature_power
                                    # | hook_weak | novelty_repeat | satisfaction_fatigue
                                    # | suspense_debt | pacing_drag | empathy_missing | other
    severity: str                   # minor | major | critical
    description: str                # 读者视角的描述（60字）
    suggested_fix: str = ""         # 改进方向（40字）


@dataclass
class ReaderExperienceAudit:
    """
    读者视角章后审计——模拟"一个挑剔的新/老读者会不会看完并看下一章"。
    区别于 critic（文学质量/作者视角）和 ability_auditor（设定合规/规则视角）。
    """
    chapter_index: int
    # 评分维度（1-10，10 最好）
    new_info_density: int = 8        # 新名词/概念/人物密度是否合理（太多→过载）
    emotional_anchor: int = 8        # 读者能否共情主角（有痛点/渴望/挣扎）
    hook_strength: int = 8           # 章末是否让人想翻下一章
    novelty: int = 8                 # 桥段是否新鲜（不是本书第 N 次同样套路）
    satisfaction_balance: int = 8    # 爽苦平衡（不是连续爽/连续虐）
    fluency: int = 8                 # 阅读流畅度（无说教/无冗长铺陈/无跳戏）
    empathy_depth: int = 8           # 情感深度（读者能跟着心跳）
    # 预估指标
    retention_estimate: int = 80     # 0-100 新读者看完本章后继续读的概率估计
    dropout_risk_points: list[str] = field(default_factory=list)  # 本章哪些位置读者容易弃（如"开头 300 字太冷"）
    # 问题清单 + 总评
    issues: list[ReaderExperienceIssue] = field(default_factory=list)
    overall_score: int = 8           # 综合打分（对应订阅/追更意愿）
    summary: str = ""                # 一句话总结（40字）
    ts: str = ""
    auditor_model: str = ""


# ═══════════════════════════════════════════════════════
#  对话质量审计（DialogueAudit）
# ═══════════════════════════════════════════════════════

@dataclass
class DialogueIssue:
    """对话层面的问题。"""
    type: str                         # on_the_nose | infodump_speech | voice_mismatch
                                      # | wrong_address | tone_flat | emotional_beat_missing
                                      # | repetitive | too_explicit | pacing_broken | other
    severity: str                     # minor | major | critical
    location: str = ""                # 本章哪一段/哪一幕（定位 30 字）
    excerpt: str = ""                 # 问题对话的片段（≤80 字）
    character: str = ""               # 涉及的角色名（如适用）
    description: str = ""             # 问题描述（60字）
    suggested_fix: str = ""           # 改进方向（40字）


@dataclass
class RomanceEvent:
    """感情线上的一个具体事件。"""
    chapter_index: int
    event_type: str             # "first_meet" | "tension" | "moment" | "kiss" | "fight" | "reconcile" | "confession" | "betrayal" | "crisis" | "resolution"
    description: str            # 30-80 字
    emotion_after: str = ""     # 事件后双方关系状态


@dataclass
class RomanceArc:
    """两个角色之间的感情线（多女主/CP 各一条）。"""
    relationship_id: str        # 自动生成
    char_a: str                 # 主角名
    char_b: str                 # 对象名
    relationship_label: str = ""   # 标签：青梅/师姐/敌方/红颜知己/政治联姻 等
    # 阶段（按典型网文感情曲线 0-100 累积）
    progress_score: int = 0     # 0=陌生  20=好感  40=心动  60=互通心意  80=确立  100=圆满
    current_stage: str = "stranger"  # stranger | acquaintance | tension | attraction | confession | conflict | reconciled | committed
    target_progress: int = 80   # 计划本书结束时该达到的程度
    # 节拍规划
    planned_beats: list[str] = field(default_factory=list)  # 大致顺序的事件清单（陌生→...→确立）
    actual_events: list[RomanceEvent] = field(default_factory=list)
    # 给主角增加的"心理债务"——配合 plan_reconciler 用
    last_interaction_chapter: int = 0
    notes: str = ""


@dataclass
class Promise:
    """主角在某章对自己/他人许下的承诺——长篇里容易遗忘。"""
    promise_id: str             # 自动生成
    chapter_made: int           # 在哪章许下的
    content: str                # 承诺内容（30-80 字）
    target_character: str = ""  # 对谁许下的（"自己"也算）
    expected_fulfill_chapter: int = -1   # 预期兑现章（可空）
    fulfilled: bool = False
    fulfilled_chapter: int = -1
    notes: str = ""


@dataclass
class AssetUsage:
    """主角持有的某个重要物品/能力的最近使用记录。"""
    asset_name: str
    asset_type: str = "item"    # "item" / "ability" / "fortune" / "title"
    obtained_chapter: int = 0   # 获得章
    last_used_chapter: int = 0  # 最近一次使用章（0=从未使用）
    use_count: int = 0          # 使用次数
    notes: str = ""


@dataclass
class AtmosphereFragment:
    """一条可以塞进章节正文的"感官/细节"碎片。"""
    fragment: str                    # 文本（30-60 字，具体可感）
    sense: str = "mixed"             # "visual" | "audio" | "smell" | "taste" | "touch" | "internal" | "mixed"
    occasion: str = ""               # 适用情境（"早市/夜路/打斗中/饮宴/独处" 等，简短标签）
    notes: str = ""                  # 备注


@dataclass
class CulturalCustom:
    """本世界独有的礼仪/俗语/禁忌等小文化片段。"""
    type: str                        # "礼仪" | "俗语" | "禁忌" | "称谓" | "童谣" | "节庆" | "服饰小节"
    content: str                     # 30-100 字
    used_by: str = ""                # 谁会用（哪类身份/势力/地区）
    avoid_by: str = ""               # 谁绝对不会用


@dataclass
class AtmosphereScope:
    """
    一个氛围"作用域"——可以是 region_id / faction_name / volume_index。
    把所有相关的氛围碎片和文化条目集中存放。
    """
    scope_type: str                  # "region" | "faction" | "volume" | "general"
    scope_key: str                   # 对应的 id/name/卷号
    label: str = ""                  # 展示用名（如"江州府/赵家/V1"）
    fragments: list[AtmosphereFragment] = field(default_factory=list)
    customs: list[CulturalCustom] = field(default_factory=list)


@dataclass
class AtmosphereLibrary:
    """全书氛围库——所有 scope 的集合。"""
    scopes: list[AtmosphereScope] = field(default_factory=list)

    def get(self, scope_type: str, scope_key: str) -> Optional["AtmosphereScope"]:
        for s in self.scopes:
            if s.scope_type == scope_type and str(s.scope_key) == str(scope_key):
                return s
        return None

    def upsert(self, scope: "AtmosphereScope") -> None:
        existing = self.get(scope.scope_type, scope.scope_key)
        if existing:
            # 合并而不是覆盖
            existing.label = scope.label or existing.label
            existing.fragments.extend(scope.fragments)
            existing.customs.extend(scope.customs)
        else:
            self.scopes.append(scope)


@dataclass
class DialogueAudit:
    """
    对话质量章后审计——网文里"角色能不能立住"的关键层。
    区别于 voice_consistency_checker（只查单角色说话像不像他自己），
    本 auditor 看的是整章对话的综合质量：潜台词/说教/节奏/称谓/角色间的差异化。
    """
    chapter_index: int
    # 统计
    total_dialogue_count: int = 0            # 本章对话行数（约）
    speaking_characters: list[str] = field(default_factory=list)
    dialogue_ratio_percent: int = 0          # 对话字数占章节字数 %
    # 评分（1-10，10 最好）
    subtext_density: int = 7                 # 潜台词密度（1=全直说，10=层次丰富）
    voice_distinctiveness: int = 7           # 角色之间说话的差异化程度
    action_beats_integration: int = 7        # 对话之间有没有动作/表情/沉默的穿插
    emotional_pacing: int = 7                # 对话里的情感节拍（吵架→缓和→爆发等）
    address_accuracy: int = 8                # 称谓/身份用词准确度（对上位/下位正确）
    infodump_level: int = 8                  # 信息灌输严重度（10=没有说教；1=全是赘述）
    dialogue_purpose: int = 7                # 每轮对话是否都推进情节/关系（而非凑数）
    # 问题清单
    issues: list[DialogueIssue] = field(default_factory=list)
    # 综合
    overall_score: int = 7
    summary: str = ""
    ts: str = ""
    auditor_model: str = ""


class NarrativeFunction(str, Enum):
    """
    角色的"叙事功能"——与 role_tag（组织身份）正交的第二重标签。
    role_tag 回答"这人在小说里是什么位置"（主角/配角/反派）；
    narrative_function 回答"这人在故事里起什么作用"——推动情节还是给主角情感支撑。

    每个角色同时有 role_tag 和 narrative_function。
    """
    # 核心配角（每本都应该有）
    EMOTIONAL_SUPPORT = "情感支撑者"    # 恋人/家人/挚友——给主角情感归属
    GROWTH_GUIDE = "成长引导者"          # 导师/师父/前辈——传授知识技能
    CONFLICT_CREATOR = "对立冲突者"      # 反派/宿敌/竞争对手——制造矛盾
    # 情节推动角色
    MESSENGER = "信使引路者"             # 带关键信息/任务/线索
    TESTER = "考验者"                    # 设置障碍考验主角
    DISRUPTOR = "搅局者"                 # 打破格局制造意外转折
    # 关系网络
    ALLY = "盟友伙伴"                    # 并肩作战的伙伴
    BETRAYER = "背叛者"                  # 曾亲密后背离
    NEUTRAL = "中立者"                   # 中立观察/客观调和
    # 特殊功能
    SYMBOLIC = "象征性人物"              # 代表某理念/价值观
    NARRATOR = "叙述观察者"              # 提供独特叙事视角
    MIRROR = "镜像角色"                  # 与主角对比突显主角
    # 其他
    PROTAGONIST_SELF = "主角本人"        # 主角自己


@dataclass
class CharacterSlot:
    """MasterDispatcher 产出的角色槽位——轻量级人物蓝图，供 CharacterDesigner 并发填充具体档案。"""
    slot_id: str                         # 唯一 id，如 "mc_01" / "ally_01" / "antag_01" / "mentor_01"
    role_tag: str                        # 组织身份：主角 / 主要配角 / 反派 / 卷内角色 / 次要配角
    function: str                        # 在全书叙事中的作用（30字）
    brief_hint: str                      # 一句话人设提示（40字，给 CharacterDesigner 的种子）
    relationship_hint: str = ""          # 与谁关联 + 什么关系（20字）
    narrative_arc_hint: str = ""         # 内在弧光方向（如"从高傲到谦卑"，20字）
    first_volume: int = 1
    last_volume: int = -1                # -1 = 全程
    # ── 新增：叙事功能（12 类）+ 细分角色 ──
    narrative_function: str = ""         # NarrativeFunction 枚举值（情感支撑者/成长引导者/...）
    support_role: str = ""               # 功能内的细分，如 情感支撑者 下可再分 "伴侣/家人/挚友"；成长引导者 下可分 "师父/前辈/上司"
    # ── 功能专属字段（按 narrative_function 填不同内容）──
    function_detail: str = ""            # 此槽位在其叙事功能里的具体发挥方式（50字）
                                         # 例：情感支撑者 → "主角最低谷时唯一不会离开的人"；
                                         #     成长引导者 → "教 A 技但在第 3 卷因保护主角死亡"；
                                         #     背叛者 → "第 4 卷因理念分歧背叛，第 7 卷可和解"


@dataclass
class FactionSkeletonItem:
    """MasterDispatcher 产出的势力层骨架——FactionArchitect 并发填每层具体势力。"""
    tier: int                            # 层号 1..N
    tier_label: str                      # 按题材的层名
    tier_function: str                   # 本层对主角的功能（起点/对手/天花板/幕后）
    faction_count_hint: int = 3          # 本层建议势力数
    style_hint: str = ""                 # 风格提示（20字）


@dataclass
class PlotSetpiece:
    """MasterDispatcher 产出的关键剧情节点——供 VolumePlanner/ForeshadowManager 对齐时机。"""
    anchor: str                          # 时间锚点，如"第1卷末"/"第3卷中"/"第5卷高潮"
    kind: str                            # 反转/揭露/牺牲/对决/重逢/堕落/觉醒
    gist: str                            # 一句话概括（50字）
    involved_slot_ids: list[str] = field(default_factory=list)  # 涉及哪些角色槽


@dataclass
class MasterOutline:
    """
    Phase 0.5：MasterDispatcher 产出的全书蓝图——下游所有 agent 的"种子文件"。

    核心理念：
      - 先用一次 LLM 把全书的骨架定下来（3-5 句故事 + 10-20 个角色槽 + 势力骨架 + 关键节点）
      - 下游 agent（VolumePlanner/CharacterDesigner/FactionArchitect）**读这个骨架**作为输入，
        只做各自的局部填充——每个 LLM 调用 prompt 更短、任务更单一、可并发

    不生成则下游 agent 退化到自己从 intent 推断——保持向后兼容。
    """
    generated: bool = False              # 是否已生成（用于下游判断是否有蓝图可用）
    story_premise: str = ""              # 3-5 句讲清整本书故事
    central_conflict: str = ""           # 核心矛盾（主角 vs 谁/什么）一句话
    thematic_core: str = ""              # 主题内核（"关于X的故事"，30字）
    character_slots: list[CharacterSlot] = field(default_factory=list)
    faction_skeleton: list[FactionSkeletonItem] = field(default_factory=list)
    plot_setpieces: list[PlotSetpiece] = field(default_factory=list)
    world_seed: str = ""                 # 世界观一句话（150字）
    tone_anchors: list[str] = field(default_factory=list)  # 3-5 个文风锚点（具体可操作）


@dataclass
class BookStructurePlan:
    """
    整本书的起承转合分段规划——顶层分形根。
    规划"哪几卷承担起/承/转/合"，每段的使命、情绪、核心矛盾递进。
    """
    # 整本书的核心命题（一句话，不超过30字）
    book_proposition: str = ""
    # 整本书最终想表达的（30字）
    book_expression: str = ""
    # 各段（起/承/转/合）的卷号分配：{"起": [1], "承": [2,3], "转": [4,5], "合": [6]}
    phase_volumes: dict[str, list[int]] = field(default_factory=dict)
    # 各段的使命说明：{"起": "建立世界+主角初始处境+第一推动力", ...}
    phase_purposes: dict[str, str] = field(default_factory=dict)
    # 各段想表达的核心：{"起": "...", "承": "...", "转": "...", "合": "..."}
    phase_expressions: dict[str, str] = field(default_factory=dict)

    def role_for_volume(self, volume_index: int) -> str:
        """查询某卷在整本书起承转合中担任哪个角色。"""
        for role, volumes in self.phase_volumes.items():
            if volume_index in volumes:
                return role
        return ""


# ═══════════════════════════════════════════════════════
#  Stage / Volume 级审查报告
# ═══════════════════════════════════════════════════════

@dataclass
class ReviewIssue:
    """
    Stage 或 Volume 级审查发现的一个问题。
    critical → 必须修订才能进入下一 stage/卷
    major    → 建议修订（默认触发一次重写）
    minor    → 仅记录，不阻塞
    """
    level: str = "minor"                                # critical / major / minor
    issue: str = ""                                     # 问题描述
    affected_chapters: list[int] = field(default_factory=list)  # 涉及哪几章
    suggestion: str = ""                                # 修订建议（用于 user_feedback 注入）
    iteration: int = 0                                  # 第几轮审查（修订循环计数）


@dataclass
class LearnedAbility:
    """角色掌握的一项能力——比 Character.ability 一行字详细 100 倍。

    用于跨章追踪：每次使用 use_count +1，last_used_chapter 自动更新。
    learned_at_chapter 为 -1 = 出生/起手就会（无需"习得"事件）。
    """
    name: str
    learned_at_chapter: int = -1            # -1 = 与生俱来
    source: str = ""                         # 怎么学到的（30字）
    ceiling: str = ""                        # 当前能做到的极限（如"只能瞬移 10 米"）
    cost: str = ""                           # 使用代价（如"消耗 1 年寿元"）
    cooldown: str = ""                       # 冷却描述（如"每月只能一次"）
    last_used_chapter: int = -1
    use_count: int = 0
    notes: str = ""                          # 杂项备注


@dataclass
class PowerEvent:
    """一次能力使用事件——按章/按角色记录。

    power_timeline_tracker 写章后从正文识别并 append。
    跟 ability_audits 不同：ability_audits 只追主角金手指 + LLM 评分；
    PowerEvent 是所有角色的所有能力使用流水。
    """
    chapter_index: int
    user: str                                # 使用者
    ability_name: str
    target: str = ""                         # 目标（"敌人/自己/某物"，30字）
    effect: str = ""                         # 实际效果（40字）
    cost_paid: str = ""                      # 实际付出的代价（30字）
    success: bool = True
    extracted_by: str = "auto"               # "auto" = LLM/规则抽 / "manual" = 用户手工添


@dataclass
class CharacterAbilityProfile:
    """角色能力档案——结构化记录"X 角色能做什么 / 不能做什么 / 怎么成长"。

    跟 Character.ability (一行字) 互补：profile 是详细版，跨章追踪用。
    主角的 special_abilities (金手指) 通过 holder_name 关联——
    profile.linked_special_assets 列出该角色持有的 asset 名。

    解决用户的实际需求：
      "主角的能力，还有其他人的能力都是需要长时间记录的，刚刚生成小说的时候就需要生成，
       防止后面矛盾，还需要记录什么时候用什么能力"
    """
    holder_name: str
    innate_talents: list[str] = field(default_factory=list)        # 天赋（出生就有的能力倾向）
    learned_abilities: list[LearnedAbility] = field(default_factory=list)
    linked_special_assets: list[str] = field(default_factory=list) # 持有的金手指 asset 名
    ceiling_now: str = ""                                          # 当前总体能力上限（卷级，会随成长更新）
    weakness: str = ""                                              # 弱点 / 克星 / 不能做的事
    signature_moves: list[str] = field(default_factory=list)       # 招牌动作（让读者一眼认出该角色）
    forbidden_combos: list[str] = field(default_factory=list)      # 不能做的能力组合（如"X 与 Y 不能同时用"）
    growth_arc_by_volume: dict[int, str] = field(default_factory=dict)  # {卷号: 本卷末能力状态}
    updated_at_chapter: int = -1                                   # 上次更新所处章节
    notes: str = ""


@dataclass
class WorldCanon:
    """world_setting 大段自然语言里的**机器可读关键锚点**。

    设计动机：state.world_setting 是几千字的自然语言（包含 [geography] /
    [history] / [society] / [economy] / [culture] / [taboos] 等标签段落），
    下游 agent 和 canon_checker 无法机器比对——LLM 自己抽取容易漂移
    （如把"大雍王朝"写成"白鹿朝"）。

    本字段由独立 agent（world_canon_extractor）在 Phase 1D 后从 world_setting
    抽出关键锚点，存为结构化字段。后续：
      · canon_checker.validate_text 把这些锚点加入 known 集合，能机器抓 canon 冲突
      · volume_planner 等上游引用 anchor 时不用每次都从大段文本里 grep
      · web 编辑 world_setting 时自动重新抽取

    所有字段都允许为空——非穿越类小说不一定有"朝代/年号"概念。
    """
    dynasty_name: str = ""              # 朝代/国号（如"大雍王朝"）—— canon_checker 用于抓"白鹿朝"等违规
    era_name: str = ""                  # 当前年号（如"景和十七年"）
    region_root: str = ""               # 主角根地理（如"江州府青石县"）
    epoch_summary: str = ""             # 时代一句话定性（如"皇权衰落，门阀垄断"）
    canonical_aliases: list[str] = field(default_factory=list)  # 朝代的别称/简称（"大雍"是"大雍王朝"的简称）
    forbidden_anchors: list[str] = field(default_factory=list)  # 不可改写的关键设定锚点（来自 [taboos]）
    extracted_at_phase: str = ""        # 抽取时所处 phase（便于检测 stale）
    source_hash: str = ""               # world_setting 抽取时的 md5 前 12 位（变了就 stale）


@dataclass
class NovelState:
    title: str
    genre: str
    theme: str

    # 世界观
    world_setting: str = ""
    world_factions_desc: str = ""
    overall_arc: str = ""

    # 世界观结构化锚点——从 world_setting 大段自然语言抽出的机器可读 canon
    # 由 agents/world_canon_extractor.py 在 Phase 1D 后自动抽取
    world_canon: WorldCanon = field(default_factory=WorldCanon)

    # 角色能力档案 + 能力使用时间线——跨章一致性追踪
    # 规划期由 character_ability_designer 生成；写作期 power_timeline_tracker 更新
    character_ability_profiles: dict[str, CharacterAbilityProfile] = field(default_factory=dict)
    power_events: list[PowerEvent] = field(default_factory=list)

    # 整本书起承转合分段规划（顶层分形根）
    book_structure: BookStructurePlan = field(default_factory=BookStructurePlan)

    # Phase -1：创作意图（作者用自然语言描述想写什么）
    creative_intent: CreativeIntent = field(default_factory=CreativeIntent)

    # Phase 0：创作立项（三件套）——所有下游 agent 的创作取向基准
    concept_pitch: ConceptPitch = field(default_factory=ConceptPitch)
    trope_library: TropeLibrary = field(default_factory=TropeLibrary)
    tone_manual: ToneManual = field(default_factory=ToneManual)

    # Phase 0.5：MasterOutline——中央调度器产出的全书蓝图，下游按槽位并发填充
    master_outline: MasterOutline = field(default_factory=MasterOutline)

    # Phase 1 扩展：地理/时间线/经济
    geography: "Geography" = field(default_factory=lambda: Geography())
    timeline: "Timeline" = field(default_factory=lambda: Timeline())
    economy: "Economy" = field(default_factory=lambda: Economy())

    # Phase 2 扩展：每个主要角色的心理弧
    character_arcs: list["CharacterArc"] = field(default_factory=list)

    # Phase 3 扩展：冲突阶梯 / 情绪曲线 / 红鲱鱼 / 章节类型规划
    conflict_ladder: "ConflictLadder" = field(default_factory=lambda: ConflictLadder())
    emotion_curve: "EmotionCurve" = field(default_factory=lambda: EmotionCurve())
    red_herrings: list["RedHerring"] = field(default_factory=list)
    chapter_type_plans: list["VolumeChapterTypeDistribution"] = field(default_factory=list)

    # Phase 3 扩展：反转系统（多层反转，烧脑）
    twist_system: TwistSystem = field(default_factory=TwistSystem)

    # Phase 5 扩展：状态快照历史 + 世界事件日历
    character_state_history: dict[str, list["CharacterStateSnapshot"]] = field(default_factory=dict)
    world_events: list["WorldEvent"] = field(default_factory=list)

    # 作者章节灵感（web 写作指引）：{chapter_index: inspiration_text}
    # 在每章写作前，director 把对应章的灵感注入 directive.user_inspiration
    chapter_inspirations: dict[int, str] = field(default_factory=dict)

    # ── Stage / Volume 级审查与批次进度 ──────────────────
    # Stage 写完后跑 stage_reviewer，整卷写完后跑 volume_reviewer。
    # 报告以 ReviewIssue 列表形式持久化，每条带 level/issue/affected_chapters/suggestion/iteration。
    stage_review_reports: dict[str, list[ReviewIssue]] = field(default_factory=dict)    # stage_id → issues
    volume_review_reports: dict[int, list[ReviewIssue]] = field(default_factory=dict)    # volume_index → issues
    # 已通过审查的 stage/volume——用于断点续跑时跳过已审过的批次
    done_stage_ids: list[str] = field(default_factory=list)
    done_volume_review_indices: list[int] = field(default_factory=list)

    # 章节对话调整历史：{chapter_index: [ChatMessage, ...]}
    # 用户通过对话要求 AI 修饰章节正文（不改蓝图/事件），采纳后覆盖章节 txt
    chapter_chats: dict[int, list["ChatMessage"]] = field(default_factory=dict)

    # 章节金手指/技能使用审计：{chapter_index: AbilityAudit}
    # 每章写完自动跑一次；用户也可手动重审
    ability_audits: dict[int, "AbilityAudit"] = field(default_factory=dict)

    # 章节读者视角审计：{chapter_index: ReaderExperienceAudit}
    reader_audits: dict[int, "ReaderExperienceAudit"] = field(default_factory=dict)

    # 章节对话质量审计：{chapter_index: DialogueAudit}
    dialogue_audits: dict[int, "DialogueAudit"] = field(default_factory=dict)

    # 笔触多样性追踪——避免反复用同一比喻/同一开头/同一钩子模式：
    # {chapter_index: {"opening": str, "closing": str, "metaphors": [str], "transitions": [str]}}
    style_signature_history: dict[int, dict] = field(default_factory=dict)
    # 章节标题去重指纹：{chapter_index: signature_str}（用前 2 字 + 关键词组合）
    used_titles_signature: dict[int, str] = field(default_factory=dict)

    # 主角实力章级日志：{chapter_index: {"realm": str, "key_means": [str], "recent_breakthrough": str}}
    # 每章后由 state_updater 回写——下章 writer 知道主角"此刻"能调用什么、近期是否升级
    protagonist_power_log: dict[int, dict] = field(default_factory=dict)

    # 元系统：术语表 / 版本索引 / 人工审核队列
    glossary: list["GlossaryEntry"] = field(default_factory=list)
    version_snapshots: list["VersionSnapshot"] = field(default_factory=list)
    pending_approvals: list["PendingApproval"] = field(default_factory=list)

    # 专项系统
    power_system: Optional[PowerSystem] = None
    factions: list[Faction] = field(default_factory=list)
    satisfaction_points: list[SatisfactionPoint] = field(default_factory=list)
    foreshadow_items: list[ForeshadowItem] = field(default_factory=list)
    setup_ledger: list[SetupEntry] = field(default_factory=list)
    # Batch 6:调味建议滚动队列(只保留最近 5 条)
    flavor_advices: list[FlavorAdvice] = field(default_factory=list)
    # Batch 6:平台 rulebook 缓存(立项时按 target_platform 加载,空=未匹配/无规则)
    platform_rules: str = ""
    # Phase 2 审核:某 phase 的候选版本暂存 — 用户选定后清空,未选定时持久化
    # 结构:{phase_id: [PhaseDraft(v1), PhaseDraft(v2), PhaseDraft(v3)]}
    phase_drafts: dict = field(default_factory=dict)
    rhythm_plans: list[VolumeRhythmPlan] = field(default_factory=list)

    # 关系网络
    relationship_web: RelationshipWeb = field(default_factory=RelationshipWeb)

    # 主角历程（三层规划）
    protagonist_journey: ProtagonistJourney = field(default_factory=ProtagonistJourney)

    # 机缘系统
    fortunes: list["Fortune"] = field(default_factory=list)

    # 叙事舞台系统（每卷的场景容器）
    story_stages: list["StoryStage"] = field(default_factory=list)

    # 实时故事状态
    story_thread: StoryThread = field(default_factory=StoryThread)

    # 人物
    characters: list[Character] = field(default_factory=list)

    # 卷
    volumes: list[Volume] = field(default_factory=list)
    current_volume_index: int = 1
    current_chapter_index: int = 0

    # 叙事线
    global_lines: list[NarrativeLine] = field(default_factory=list)
    volume_lines: list[NarrativeLine] = field(default_factory=list)

    # 记忆
    memory: MemoryBank = field(default_factory=MemoryBank)
    tension_history: list[TensionLevel] = field(default_factory=list)

    # 氛围库（P6 AtmosphereLibrary）
    # 每个 region/faction/volume 一组"感官细节碎片"，让 writer 不用现编世界感
    atmosphere_library: "AtmosphereLibrary" = field(default_factory=lambda: AtmosphereLibrary())

    # ── P5 动态反馈闭环（PlanReconciler 消费/产出）────────
    # tension_debt：主角承受的"苦/低谷"还没被"爽/高点"兑现的累积量（负数=欠读者一个释放；正数=连爽多了需要铺垫）
    #               每次 reconcile 由 PlanReconciler 更新。writer 看不到这个值，
    #               但 chapter_planner 在规划时可参考，调整本章倾向"兑现"还是"铺垫"
    tension_debt: int = 0
    # novelty_budget：还剩多少"新东西"（新角色/新设定/新套路）可以抛给读者（跨卷跟踪）
    #                 太低说明本书最近重复套路太多，需要休止"引入新东西"
    novelty_budget: int = 100
    # 最近一次 reconcile 的记录（便于 UI 展示和审计）
    last_reconcile_report: dict = field(default_factory=dict)

    # ── P9 长篇连贯性跟踪 ────────────────────────────────
    # 主角"承诺"账：第 N 章主角说要做某事，没做就一直挂着
    promises: list["Promise"] = field(default_factory=list)
    # 重要物品/能力的最近使用记录：哪些拿了很久没用
    asset_usage: dict[str, "AssetUsage"] = field(default_factory=dict)
    # 最近一次跨卷连贯性检查报告
    last_cohesion_report: dict = field(default_factory=dict)

    # ── P10 感情线跟踪 ───────────────────────────────────
    # 每条感情线一条 RomanceArc，多女主/CP 多条
    romance_arcs: list["RomanceArc"] = field(default_factory=list)
    completed_chapters: list[ChapterSummary] = field(default_factory=list)

    # ── 查询辅助 ──────────────────────────────────

    @property
    def all_lines(self) -> list[NarrativeLine]:
        return self.global_lines + self.volume_lines

    def get_line(self, line_id: str) -> Optional[NarrativeLine]:
        for ln in self.all_lines:
            if ln.line_id == line_id:
                return ln
        return None

    def get_volume(self, index: int) -> Optional[Volume]:
        for v in self.volumes:
            if v.index == index:
                return v
        return None

    def current_volume(self) -> Optional[Volume]:
        return self.get_volume(self.current_volume_index)

    def get_character(self, name: str) -> Optional[Character]:
        for c in self.characters:
            if c.name == name:
                return c
        return None

    def get_faction(self, name: str) -> Optional[Faction]:
        for f in self.factions:
            if f.name == name:
                return f
        return None

    def active_characters_in_volume(self, volume_index: int) -> list[Character]:
        return [
            c for c in self.characters
            if c.first_volume <= volume_index and (c.last_volume == -1 or c.last_volume >= volume_index)
        ]

    def lines_active_in_chapter(self, chapter_index: int) -> list[NarrativeLine]:
        return [ln for ln in self.all_lines if ln.get_phase_for_chapter(chapter_index)]

    def character_brief_list(self, volume_index: int = None) -> str:
        chars = self.active_characters_in_volume(volume_index) if volume_index else self.characters
        return "\n".join(c.brief() for c in chars)

    def lines_status_for_chapter(self, chapter_index: int) -> str:
        lines = []
        for ln in self.lines_active_in_chapter(chapter_index):
            phase = ln.get_phase_for_chapter(chapter_index)
            if phase:
                lines.append(
                    f"[{ln.scope.value}/{ln.line_type.value}] {ln.name} "
                    f"→ 阶段{phase.phase_index}《{phase.name}》[{phase.tension.value}]：{phase.description}"
                )
        return "\n".join(lines) if lines else "无活跃线索。"

    def last_n_summaries(self, n: int = 3) -> str:
        recent = self.completed_chapters[-n:]
        if not recent:
            return "暂无已完成章节。"
        return "\n".join(
            f"第{c.index}章《{c.title}》[{c.tension.value}]：{c.summary}"
            for c in recent
        )

    def get_rhythm_for_chapter(self, chapter_index: int) -> Optional[RhythmSegment]:
        for plan in self.rhythm_plans:
            for seg in plan.segments:
                if seg.chapter_start <= chapter_index <= seg.chapter_end:
                    return seg
        return None

    def get_pending_sp_for_chapter(self, chapter_index: int) -> list[SatisfactionPoint]:
        return [
            sp for sp in self.satisfaction_points
            if not sp.triggered and abs(sp.target_chapter - chapter_index) <= 2
        ]

    def get_plantable_foreshadows(self, chapter_index: int) -> list[ForeshadowItem]:
        return [
            fw for fw in self.foreshadow_items
            if not fw.resolved and fw.planted_chapter == 0
               and fw.planned_resolve_chapter > chapter_index
        ]

    def get_resolvable_foreshadows(self, chapter_index: int) -> list[ForeshadowItem]:
        return [
            fw for fw in self.foreshadow_items
            if not fw.resolved and fw.planted_chapter > 0
               and abs(fw.planned_resolve_chapter - chapter_index) <= 3
        ]

    def get_foreshadow(self, fw_id: str) -> Optional[ForeshadowItem]:
        for fw in self.foreshadow_items:
            if fw.fw_id == fw_id:
                return fw
        return None

    def power_system_brief(self) -> str:
        if not self.power_system:
            return "未设定"
        return f"{self.power_system.system_name}：{self.power_system.realm_list_str()}"

    def volume_progress_str(self) -> str:
        lines = []
        for v in self.volumes:
            done = len([c for c in self.completed_chapters if c.volume_index == v.index])
            total = v.total_chapters
            filled = done * 10 // max(total, 1)
            bar = "█" * filled + "░" * (10 - filled)
            lines.append(f"第{v.index}卷《{v.title}》[{bar}] {done}/{total}章")
        return "\n".join(lines)

    # ── 叙事舞台 / 机缘 查询 ──────────────────────────

    def get_active_stages(self, chapter_index: int) -> list[StoryStage]:
        """返回chapter_index所在的所有活跃舞台（可能同时有多个）。"""
        return [
            s for s in self.story_stages
            if s.active and s.chapter_start <= chapter_index <= s.chapter_end
        ]

    def get_active_sub_scenes(self, chapter_index: int) -> list[SubScene]:
        """返回chapter_index活跃的所有子场景。"""
        result = []
        for stage in self.get_active_stages(chapter_index):
            for sub in stage.sub_scenes:
                if sub.chapter_start <= chapter_index <= sub.chapter_end:
                    result.append(sub)
        return result

    def get_fortunes_for_chapter(self, chapter_index: int, window: int = 3) -> list[Fortune]:
        """返回预计在chapter_index附近获得的机缘。"""
        return [
            f for f in self.fortunes
            if not f.obtained and abs(f.target_chapter - chapter_index) <= window
        ]

    def get_fortune(self, fortune_id: str) -> Optional[Fortune]:
        for f in self.fortunes:
            if f.fortune_id == fortune_id:
                return f
        return None

    # ── 特殊能力查询 ──────────────────────────────────

    def abilities_for_volume(self, volume_index: int) -> list["SpecialAbility"]:
        """返回在第 volume_index 卷已经出现或可能觉醒阶段落在本卷的能力。"""
        if not self.power_system:
            return []
        result = []
        for ab in self.power_system.special_abilities:
            # 有觉醒阶段命中本卷
            if any(st.target_volume == volume_index for st in ab.awakening_stages):
                result.append(ab)
                continue
            # 无觉醒阶段但已在本卷前首次出现（通过第1阶段 target_volume 判断）
            if ab.awakening_stages:
                first_stage_vol = ab.awakening_stages[0].target_volume
                if first_stage_vol <= volume_index:
                    result.append(ab)
            else:
                # 老能力（无觉醒阶段）默认全程可见
                result.append(ab)
        return result

    def ability_stage_for_volume(self, ability_name: str, volume_index: int) -> Optional[AbilityAwakeningStage]:
        """返回某能力在第 volume_index 卷将要达到/已经达到的最新阶段。"""
        if not self.power_system:
            return None
        for ab in self.power_system.special_abilities:
            if ab.name != ability_name:
                continue
            # 找 target_volume <= volume_index 的最大阶段
            hit = [st for st in ab.awakening_stages if st.target_volume <= volume_index]
            if hit:
                return max(hit, key=lambda s: s.stage_index)
            return None
        return None

    # ── 术语表查询 ────────────────────────────────────

    def get_glossary_term(self, term: str) -> Optional["GlossaryEntry"]:
        """支持规范名或别名查询。"""
        for g in self.glossary:
            if g.term == term or term in g.aliases:
                return g
        return None

    def glossary_brief(self, categories: list[str] = None, max_items: int = 12) -> str:
        """精简术语摘要——供 writer 上下文使用，避免重新生成已有词。"""
        items = self.glossary
        if categories:
            items = [g for g in items if g.category in categories]
        if not items:
            return ""
        lines = []
        for g in items[-max_items:]:
            aliases = f"(别名:{','.join(g.aliases[:2])})" if g.aliases else ""
            lines.append(f"· [{g.category}]《{g.term}》{aliases}")
        return "\n".join(lines)

    # ── 章节类型查询 ──────────────────────────────────

    def chapter_type_for(self, chapter_index: int) -> str:
        """返回某章的预定类型（如果有规划）。"""
        for vol_plan in self.chapter_type_plans:
            t = vol_plan.type_for_chapter(chapter_index)
            if t:
                return t
        return ""

    # ── 红鲱鱼查询 ────────────────────────────────────

    def red_herrings_for_chapter(self, chapter_index: int) -> dict:
        """返回本章需要操作的红鲱鱼：植入/揭穿。"""
        return {
            "plant": [rh for rh in self.red_herrings
                      if rh.planted_chapter == chapter_index and not rh.planted],
            "debunk": [rh for rh in self.red_herrings
                       if rh.debunk_chapter == chapter_index and not rh.debunked],
        }

    def get_red_herring(self, rh_id: str) -> Optional["RedHerring"]:
        for rh in self.red_herrings:
            if rh.rh_id == rh_id:
                return rh
        return None

    # ── 反转系统：按章/卷查询 ──────────────────────────

    def twist_reveals_for_volume(self, volume_index: int) -> list[tuple["TwistChain", "TwistLayer"]]:
        """返回 reveal_anchor 落在本卷的反转层。"""
        ts = getattr(self, "twist_system", None)
        if not ts:
            return []
        result = []
        anchor_prefix = f"第{volume_index}卷"
        for chain in ts.chains:
            for layer in chain.layers:
                if anchor_prefix in (layer.reveal_anchor or ""):
                    result.append((chain, layer))
                # cross_volume 链：如果 volume_span 包含本卷也算相关
                if chain.scope == "cross_volume" and volume_index in (chain.volume_span or []):
                    if (chain, layer) not in result:
                        result.append((chain, layer))
        return result

    def twist_reveals_for_chapter(self, volume_index: int, chapter_index: int) -> list[tuple["TwistChain", "TwistLayer"]]:
        """
        返回 reveal_anchor 精确匹配本章的反转层。
        reveal_anchor 可能是"第3卷第12章"/"第3卷中段"/"第3卷末"等，用粗粒度匹配。
        """
        ts = getattr(self, "twist_system", None)
        if not ts:
            return []
        # 先拿本卷所有反转层，再按锚点粗粒度判定是否在本章触发
        vol = self.get_volume(volume_index)
        if not vol:
            return []
        total_chapters = vol.total_chapters if vol.chapter_end >= vol.chapter_start else 0
        result = []
        import re
        for chain in ts.chains:
            for layer in chain.layers:
                anchor = layer.reveal_anchor or ""
                if f"第{volume_index}卷" not in anchor:
                    continue
                # 精确章号（"第3卷第12章"）
                m = re.search(r"第\d+卷第(\d+)章", anchor)
                if m and int(m.group(1)) == chapter_index:
                    result.append((chain, layer))
                    continue
                # 模糊锚点："开头"/"初"/"中段"/"末"/"高潮"
                if total_chapters > 0 and vol.chapter_start > 0:
                    offset = chapter_index - vol.chapter_start
                    pos = offset / max(1, total_chapters - 1)
                    if ("开头" in anchor or "初" in anchor) and pos <= 0.25:
                        result.append((chain, layer))
                    elif "中段" in anchor and 0.35 <= pos <= 0.65:
                        result.append((chain, layer))
                    elif ("末" in anchor or "高潮" in anchor or "收尾" in anchor) and pos >= 0.75:
                        result.append((chain, layer))
        return result

    def find_twist_layer(self, chain_id: str, layer_num: int) -> Optional[tuple["TwistChain", "TwistLayer"]]:
        ts = getattr(self, "twist_system", None)
        if not ts:
            return None
        for chain in ts.chains:
            if chain.chain_id == chain_id:
                for layer in chain.layers:
                    if layer.layer == layer_num:
                        return chain, layer
        return None

    # ── MasterOutline：按卷查询关键节点 ────────────────

    def plot_setpieces_for_volume(self, volume_index: int) -> list["PlotSetpiece"]:
        """返回 MasterOutline 中 anchor 落在本卷的关键节点。"""
        mo = getattr(self, "master_outline", None)
        if not mo or not getattr(mo, "generated", False):
            return []
        anchor_prefix = f"第{volume_index}卷"
        return [p for p in (mo.plot_setpieces or []) if anchor_prefix in (p.anchor or "")]

    # ── 角色最近状态快照 ──────────────────────────────

    def latest_state_snapshot(self, char_name: str) -> Optional["CharacterStateSnapshot"]:
        history = self.character_state_history.get(char_name, [])
        return history[-1] if history else None

    def get_character_arc(self, name: str) -> Optional["CharacterArc"]:
        for a in self.character_arcs:
            if a.character_name == name:
                return a
        return None

    def arc_transitions_near_chapter(self, chapter_index: int, window: int = 3) -> list[tuple[str, "ArcTransition"]]:
        """返回在本章附近预计发生的人物弧转折点，格式 [(角色名, transition)]。"""
        result = []
        for arc in self.character_arcs:
            for tr in arc.transitions:
                if tr.chapter_approx > 0 and abs(tr.chapter_approx - chapter_index) <= window:
                    result.append((arc.character_name, tr))
        return result

    def abilities_context_for_volume(self, volume_index: int, max_chars: int = 400) -> str:
        """为写作/规划提供本卷特殊能力上下文（含本卷觉醒阶段）。"""
        abs_ = self.abilities_for_volume(volume_index)
        if not abs_:
            return ""
        lines = []
        for ab in abs_:
            holder = ab.holder_name or ab.holder_role or "未知持有者"
            sig = "★" if ab.is_protagonist_signature else " "
            # 本卷阶段
            cur_stage = self.ability_stage_for_volume(ab.name, volume_index)
            # 本卷是否有新阶段觉醒
            new_stage = next((st for st in ab.awakening_stages if st.target_volume == volume_index), None)
            parts = [f"{sig}《{ab.name}》[{holder}]"]
            if cur_stage:
                parts.append(f"当前阶段：{cur_stage.stage_name}")
            if new_stage:
                parts.append(f"本卷觉醒→{new_stage.stage_name}（触发：{new_stage.triggering_event[:30]}）")
            lines.append(" | ".join(parts))
        result = "\n".join(lines)
        return result[:max_chars] if len(result) > max_chars else result

    def stage_context_for_chapter(self, chapter_index: int) -> str:
        """为写作智能体生成当前舞台上下文（精简版）。"""
        stages = self.get_active_stages(chapter_index)
        if not stages:
            return ""
        parts = []
        for stage in stages:
            subs = [s for s in stage.sub_scenes
                    if s.chapter_start <= chapter_index <= s.chapter_end]
            sub_str = ""
            if subs:
                sub_str = " → 活跃子场景：" + " / ".join(s.name for s in subs)
            parts.append(
                f"[{stage.stage_type}舞台] {stage.name}（{stage.atmosphere}）"
                f"\n  主角处境：{stage.protagonist_role}{sub_str}"
            )
        return "\n".join(parts)

    # ── 分形起承转合：结构链查询 ──────────────────────

    def primary_stage_for_chapter(self, chapter_index: int) -> Optional[StoryStage]:
        """返回本章所在的主大情节（取起止范围包含本章、最靠前的活跃舞台）。"""
        stages = self.get_active_stages(chapter_index)
        if not stages:
            return None
        # 用 chapter_start 排序，优先取最贴合的（章范围最小的）
        stages_sorted = sorted(
            stages,
            key=lambda s: (s.chapter_end - s.chapter_start, s.chapter_start)
        )
        return stages_sorted[0]

    def stages_in_volume(self, volume_index: int) -> list[StoryStage]:
        """返回某卷内所有大情节，按 chapter_start 升序。"""
        stages = [s for s in self.story_stages if s.volume == volume_index]
        return sorted(stages, key=lambda s: (s.chapter_start, s.chapter_end))

    def chapters_in_stage(self, volume_index: int, stage_id: str) -> list[int]:
        """
        返回属于本 stage 的章号列表（升序、去重）。
        - 优先从 outlines_by_stage 拿（每章只归属一个 stage_id，不会和 parallel stage 范围重叠）。
        - 无 outline 时 fallback 到 stage.chapter_start..chapter_end。
        """
        grouped = self.outlines_by_stage(volume_index)
        own = grouped.get(stage_id, [])
        if own:
            indices = sorted({o.get("index") for o in own if isinstance(o.get("index"), int)})
            if indices:
                return indices
        # Fallback：用 stage 自身章节范围
        st = next((s for s in self.story_stages if s.stage_id == stage_id), None)
        if st:
            return list(range(st.chapter_start, st.chapter_end + 1))
        return []

    def outlines_by_stage(self, volume_index: int):
        """
        按 stage 分组返回某卷的 chapter_outlines。
        - 旧 outline 缺 stage_id 字段时，用 primary_stage_for_chapter 回填到 dict 上（in-place）。
        - 没有 stage 覆盖的章节归到 "_unstaged" key（一般是边界 / 设计阶段未完整覆盖）。
        - 返回 OrderedDict[stage_id, list[outline_dict]]，stage 顺序与 stages_in_volume 一致。
        """
        from collections import OrderedDict
        vol = self.get_volume(volume_index)
        result = OrderedDict()
        if not vol:
            return result
        # 预填 stage 顺序，保证遍历顺序稳定
        for st in self.stages_in_volume(volume_index):
            result[st.stage_id] = []
        result["_unstaged"] = []
        for o in (vol.chapter_outlines or []):
            sid = (o.get("stage_id") or "").strip()
            if not sid:
                ci = o.get("index", 0)
                if ci:
                    st = self.primary_stage_for_chapter(ci)
                    if st and st.volume == volume_index:
                        sid = st.stage_id
                        o["stage_id"] = sid  # 回填，下次直接命中
            if sid in result:
                result[sid].append(o)
            else:
                result["_unstaged"].append(o)
        # 移除空分组
        return OrderedDict((k, v) for k, v in result.items() if v)

    def primary_sub_scene_for_chapter(self, chapter_index: int) -> Optional[SubScene]:
        """返回本章所在的主小情节（范围最贴合的子场景）。"""
        candidates = self.get_active_sub_scenes(chapter_index)
        if not candidates:
            return None
        candidates_sorted = sorted(
            candidates,
            key=lambda s: (s.chapter_end - s.chapter_start, s.chapter_start)
        )
        return candidates_sorted[0]

    def structure_chain_for_chapter(self, chapter_index: int, chapter_role: str = "") -> str:
        """
        构造本章完整的分形结构链：
        "整本[起] → 卷[承] → 大情节·xxx[转] → 小情节·xxx[承] → 章[转]"
        供 writer/critic prompt 使用。chapter_role 由 chapter_planner 填入。
        """
        vol = None
        for v in self.volumes:
            if v.chapter_start <= chapter_index <= v.chapter_end:
                vol = v
                break

        parts = []
        # 整本（无 structure_role，但展示命题）
        if self.book_structure.book_proposition:
            parts.append(f"整本《{self.title}》")
        else:
            parts.append(f"整本《{self.title}》")

        # 卷
        if vol:
            role = vol.structure_role or self.book_structure.role_for_volume(vol.index)
            role_tag = f"[{role}]" if role else ""
            parts.append(f"第{vol.index}卷《{vol.title}》{role_tag}")

        # 大情节（StoryStage）
        stage = self.primary_stage_for_chapter(chapter_index)
        if stage:
            role_tag = f"[{stage.structure_role}]" if stage.structure_role else ""
            parts.append(f"大情节·{stage.name}{role_tag}")

        # 小情节（SubScene）
        sub = self.primary_sub_scene_for_chapter(chapter_index)
        if sub:
            role_tag = f"[{sub.structure_role}]" if sub.structure_role else ""
            parts.append(f"小情节·{sub.name}{role_tag}")

        # 章
        ch_tag = f"[{chapter_role}]" if chapter_role else ""
        parts.append(f"第{chapter_index}章{ch_tag}")

        return " → ".join(parts)
