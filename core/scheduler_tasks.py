"""
任务注册表 —— 所有 Phase 的任务定义 + 依赖关系。

这是从手写 director.run() 链条迁移过来的结构化声明。
每个 Task 声明自己需要哪些前置任务完成，调度器自动算并发。

关键依赖图（简化版）：

    -1 意图 → 0 立项 → 0.5 Master
                        │
                        ├→ 1A 体系 ──┬→ 1A2 刻度
                        │            ├→ 1B 卷结构 ──┬→ 1C 势力 ──┬→ 1D 世界 → 1E 校验
                        │            │              │             ├→ 1G 时间线
                        │            │              │             └→ 1F 地理（需 1B）
                        │            │              └→ 1H 经济
                        │            │
                        │            └→ 2A 人物 ──┬→ 2A2 深化  （与下面并行）
                        │                         ├→ 2B 关系网
                        │                         ├→ 2C 特殊能力
                        │                         └→ 2D 心理弧
                        │
                        ... Phase 3 和 Phase 4 类似，大量可并发

这个文件只做"声明"——具体执行逻辑还在各个 agent 里。
"""
from __future__ import annotations
from core.scheduler import Task
from persistence.state import NovelState

# ─── Phase 逻辑 import（延迟 import 可减少初次加载成本，但这里简单起见一次性导入）
from agents.intent_analyzer import analyze_intent
from agents.concept_pitch import design_concept_phase
from agents.master_dispatcher import dispatch_master_outline
from agents.realm_designer import (
    design_realm_system, design_power_scaling, design_special_abilities,
    bind_abilities_to_characters,
)
from agents.volume_planner import plan_all_volumes_dispatched
from agents.faction_architect import design_factions
from agents.world_builder import build_world, run_world_checklist
from agents.geography_designer import design_geography
from agents.timeline_anchor import design_timeline
from agents.economy_designer import design_economy
from agents.character_designer import design_all_characters
from agents.major_supporting_refiner import refine_major_characters
from agents.character_web import design_relationship_web
from agents.line_planner import plan_global_lines, plan_all_volume_lines_parallel
from agents.conflict_ladder import design_conflict_ladder
from agents.satisfaction_system import plan_all_satisfaction_points
from agents.rhythm_designer import design_all_rhythms
from agents.emotion_curve import design_emotion_curve
from agents.foreshadow_manager import plan_all_foreshadowing
from agents.twist_designer import design_twists
from agents.fortune_planner import plan_all_fortunes
from agents.stage_architect import design_volume_stages
from agents.chapter_type_planner import plan_all_chapter_types

from config import NUM_VOLUMES, INTENT_DESCRIPTION


# ═══════════════════════════════════════════════════════
#  包装器 —— 处理任务内部的条件跳过 / 分批循环
# ═══════════════════════════════════════════════════════

def _phase_minus1_intent(state: NovelState):
    """Phase -1：只在有 raw_description 且未分析过时跑"""
    intent = state.creative_intent
    desc = intent.raw_description or INTENT_DESCRIPTION
    if desc and not intent.analyzed:
        intent.raw_description = desc
        analyze_intent(state, desc)


def _phase_all_stages(state: NovelState):
    """Phase 4：逐卷设计叙事舞台（后续 3-G 已并发，这里保持逐卷调用不变）"""
    for vi in range(1, NUM_VOLUMES + 1):
        design_volume_stages(state, vi)


def _phase_2c_abilities(state: NovelState):
    """Phase 2-C：特殊能力 + 绑定"""
    design_special_abilities(state)


def _phase_2c2_ability_roadmap(state: NovelState):
    """Phase 2-C2：能力路线图——补 2C 缺的金手指（system_type=progression_arc 等被 2C 跳过的）
    + 为每个 asset 设计 lifecycle_nodes（铺垫/获得/首用/解锁/升级/牺牲...）
    + 反向产 ASSET_LIFECYCLE 爽点 + 标 ArcTransition.ability_trigger。"""
    from agents.ability_roadmap_planner import run_phase_2c2
    run_phase_2c2(state)
    bind_abilities_to_characters(state)


def _phase_1e_world_check(state: NovelState):
    """Phase 1-E：世界观完整性校验（只读 + 产 facts）"""
    gaps = run_world_checklist(state)
    if gaps:
        print(f"  ! 世界观校验发现 {len(gaps)} 处缺失，已记入 facts")


# ═══════════════════════════════════════════════════════
#  关键任务 —— 失败时启用模型轮换
# ═══════════════════════════════════════════════════════

def _critical_realm_system(state: NovelState):
    """力量体系——失败则轮换模型重试 + 事后合规验证。"""
    from llm_layer.fallback_runner import run_with_model_fallback
    from utils.validators import validate_and_regen, SECTION_VALIDATORS

    def _run_once():
        design_realm_system(state)
        ps = state.power_system
        if ps is None:
            return False
        if getattr(ps, "has_hierarchy", True) and not ps.realms:
            return False
        return True

    ok = run_with_model_fallback(
        fn=_run_once,
        agent_name="RealmDesigner",
        check_ok=lambda r: bool(r),
        retries_per_model=5,
    )
    if not ok:
        raise RuntimeError("RealmDesigner 模型轮换后仍失败——无法继续（下游依赖力量体系）")

    # 事后合规验证——不通过最多重生 2 次
    validate_and_regen(
        state, "power_system",
        generator_fn=lambda s: design_realm_system(s),
        validators=SECTION_VALIDATORS["power_system"],
        max_retries=2,
    )


def _critical_volume_planner(state: NovelState):
    """卷结构——失败则轮换模型重试 + 事后合规验证。"""
    from llm_layer.fallback_runner import run_with_model_fallback
    from utils.validators import validate_and_regen, SECTION_VALIDATORS
    from config import NUM_VOLUMES

    def _run_once():
        plan_all_volumes_dispatched(state)
        if len(state.volumes) < max(2, (NUM_VOLUMES * 7) // 10):
            return False
        return True

    ok = run_with_model_fallback(
        fn=_run_once,
        agent_name="VolumePlanner",
        check_ok=lambda r: bool(r),
        retries_per_model=5,
    )
    if not ok:
        raise RuntimeError("VolumePlanner 模型轮换后仍失败")

    # 事后合规验证
    def _clear_and_regen(s):
        s.volumes = []
        s.book_structure = type(s.book_structure)()
        plan_all_volumes_dispatched(s)

    validate_and_regen(
        state, "volumes",
        generator_fn=_clear_and_regen,
        validators=SECTION_VALIDATORS["volumes"],
        max_retries=2,
    )


def _critical_character_designer(state: NovelState):
    """人物设计——失败则轮换模型重试 + 事后合规验证。"""
    from llm_layer.fallback_runner import run_with_model_fallback
    from utils.validators import validate_and_regen, SECTION_VALIDATORS
    from persistence.state import CharacterRole

    def _run_once():
        design_all_characters(state)
        has_protagonist = any(c.role == CharacterRole.PROTAGONIST for c in state.characters)
        return has_protagonist and len(state.characters) >= 3

    ok = run_with_model_fallback(
        fn=_run_once,
        agent_name="CharacterDesigner",
        check_ok=lambda r: bool(r),
        retries_per_model=5,
    )
    if not ok:
        raise RuntimeError("CharacterDesigner 模型轮换后仍失败——无主角/角色太少")

    # 事后合规验证——如果角色不达标（数量 / 叙事功能覆盖 / 关系）就再跑一轮
    validate_and_regen(
        state, "characters",
        generator_fn=lambda s: design_all_characters(s),
        validators=SECTION_VALIDATORS["characters"],
        max_retries=1,  # 人物重生成本高，1 次就够
    )


def _critical_factions(state: NovelState):
    """势力格局——非 critical 但常出问题（层级跳跃/数量不足），加验证"""
    from utils.validators import validate_and_regen, SECTION_VALIDATORS
    from agents.faction_architect import design_factions as _design_factions

    _design_factions(state)

    def _clear_and_regen(s):
        s.factions = []
        _design_factions(s)

    validate_and_regen(
        state, "factions",
        generator_fn=_clear_and_regen,
        validators=SECTION_VALIDATORS["factions"],
        max_retries=2,
    )


# ═══════════════════════════════════════════════════════
#  任务注册（完整 DAG）
# ═══════════════════════════════════════════════════════

ALL_TASKS: list[Task] = [

    # 1 · 起点：创作意图（唯一依赖链起点）
    Task(id="-1", phase="1 · 起点", agent_name="意图分析",
         detail="解析作者创作意图", fn=_phase_minus1_intent, depends_on=[]),

    # 2 · 市场定位：立项三件套
    Task(id="0", phase="2 · 市场定位", agent_name="立项",
         detail="卖点 + 套路 + 文风",
         fn=design_concept_phase, depends_on=["-1"]),

    # 3 · 全书蓝图：一次性产出骨架，下游按骨架并发
    Task(id="0.5", phase="3 · 全书蓝图", agent_name="MasterDispatcher",
         detail="故事骨架 + 角色槽位 + 势力骨架 + 关键节点",
         fn=dispatch_master_outline, depends_on=["0"]),

    # 4 · 世界 · 力量体系（下游世界模块的基础）——模型轮换兜底
    Task(id="1A", phase="4 · 世界", agent_name="力量体系",
         detail="境界/能力体系设计（带模型轮换）",
         fn=_critical_realm_system, depends_on=["0.5"], critical=True),

    Task(id="1A2", phase="4 · 世界", agent_name="力量刻度",
         detail="战力/寿命/越级规则",
         fn=design_power_scaling, depends_on=["1A"]),

    # 5 · 情节 · 卷结构（虽跨步骤但依赖 1A，需尽早出）——模型轮换兜底
    Task(id="1B", phase="5 · 情节 · 卷结构", agent_name="卷结构",
         detail="整本起承转合 + 每卷详情（并发，带模型轮换）",
         fn=_critical_volume_planner, depends_on=["1A"], critical=True),

    # 4 · 世界 · 势力格局——带合规验证（防止层级跳跃/数量不足）
    Task(id="1C", phase="4 · 世界", agent_name="势力格局",
         detail="层级骨架 + 每层势力（并发，带事后验证）",
         fn=_critical_factions, depends_on=["1A", "1B"]),

    # 4 · 世界 · 世界观 / 校验
    Task(id="1D", phase="4 · 世界", agent_name="世界观",
         detail="地理/历史/文化/禁忌/世界秘密",
         fn=build_world, depends_on=["1A", "1C"]),

    Task(id="1E", phase="4 · 世界", agent_name="世界观校验",
         detail="完整性检查",
         fn=_phase_1e_world_check, depends_on=["1D"]),

    # 4 · 世界 · 地理 / 时间 / 经济（三者可并发）
    Task(id="1F", phase="4 · 世界", agent_name="地理",
         detail="区划/交通/距离矩阵",
         fn=design_geography, depends_on=["1D"]),

    Task(id="1G", phase="4 · 世界", agent_name="时间线",
         detail="历史事件时间轴",
         fn=design_timeline, depends_on=["1D"]),

    Task(id="1H", phase="4 · 世界", agent_name="经济",
         detail="货币/物价/财富曲线",
         fn=design_economy, depends_on=["1D"]),

    # 6 · 人物 · 档案——模型轮换兜底（主角必须有）
    Task(id="2A", phase="6 · 人物", agent_name="人物档案",
         detail="按 MasterOutline slots 并发生成（带模型轮换）",
         fn=_critical_character_designer, depends_on=["1A", "1B", "1C", "1D", "0.5"], critical=True),

    Task(id="2A2", phase="6 · 人物", agent_name="人物深化",
         detail="主角+主要配角+反派 细节刻画（并发）",
         fn=refine_major_characters, depends_on=["2A"]),

    Task(id="2B", phase="6 · 人物", agent_name="关系网络",
         detail="语义分批并发（主角圈/配角间/隐藏反转）",
         fn=design_relationship_web, depends_on=["2A"]),

    Task(id="2C", phase="6 · 人物", agent_name="特殊能力",
         detail="能力设定 + 持有者绑定",
         fn=_phase_2c_abilities, depends_on=["2A", "1A"]),

    Task(id="2C2", phase="6 · 人物", agent_name="能力路线图",
         detail="金手指/物品 lifecycle（铺垫/获得/首用/升级…）+ 反向 SP + 标 arc",
         fn=_phase_2c2_ability_roadmap, depends_on=["2C", "2A", "1B", "3C"]),

    # 5 · 情节架构：多数子模块可并发
    Task(id="3A", phase="5 · 情节架构", agent_name="全局叙事线",
         detail="故事/情感/人物/悬疑 跨卷主线",
         fn=plan_global_lines, depends_on=["2A", "1B"]),

    Task(id="3B", phase="5 · 情节架构", agent_name="卷内叙事线",
         detail="每卷专属线（并发）",
         fn=plan_all_volume_lines_parallel, depends_on=["3A"]),

    Task(id="3B2", phase="5 · 情节架构", agent_name="冲突阶梯",
         detail="每卷冲突类型+层级",
         fn=design_conflict_ladder, depends_on=["2A", "1B"]),

    Task(id="3C", phase="5 · 情节架构", agent_name="爽点系统",
         detail="打脸/升级/真相 等节点规划",
         fn=plan_all_satisfaction_points, depends_on=["2A", "1B", "3A"]),

    Task(id="3D", phase="5 · 情节架构", agent_name="节奏",
         detail="每卷节奏段（慢热/快节奏/反转）",
         fn=design_all_rhythms, depends_on=["1B"]),

    Task(id="3D2", phase="5 · 情节架构", agent_name="情绪曲线",
         detail="每卷基调+低谷+高点",
         fn=design_emotion_curve, depends_on=["1B"]),

    # 反转先于伏笔——反转链声明 clues_planted，伏笔阶段优先满足这些 clues
    Task(id="3E3", phase="5 · 情节架构", agent_name="反转设计",
         detail="多层反转链（2-4 层）—— 先于伏笔，让伏笔阶段为反转铺路",
         fn=design_twists, depends_on=["2A", "1B", "1C", "3A"]),

    Task(id="3E", phase="5 · 情节架构", agent_name="伏笔",
         detail="主线/支线/细节 三档分批 + 为反转铺必要 clues",
         fn=plan_all_foreshadowing, depends_on=["2A", "1B", "1D", "3E3"]),

    Task(id="3F", phase="5 · 情节架构", agent_name="机缘",
         detail="每卷机缘规划",
         fn=plan_all_fortunes, depends_on=["2A", "1A", "1B"]),

    # Phase 3G "主角历程" 已删除（2026-05-25）—— overall_theme/milestones 等字段
    # 全部审计为零下游消费；stage_beats 由 _beats_for_volume 在卷级写作时独立生成。

    # 7 · 章节：舞台 + 章节类型
    Task(id="4", phase="7 · 章节", agent_name="叙事舞台",
         detail="逐卷大情节/小情节划分",
         fn=_phase_all_stages, depends_on=["3A", "3B", "3F"]),

    Task(id="4C", phase="7 · 章节", agent_name="章节类型",
         detail="每卷章节类型配比",
         fn=plan_all_chapter_types, depends_on=["1B", "3B2", "3D"]),
]


def task_id_list() -> list[str]:
    return [t.id for t in ALL_TASKS]
