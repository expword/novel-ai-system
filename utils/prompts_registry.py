"""
PromptRegistry —— 统一的系统提示词管理。

职责：
  · 扫描所有 agents 模块，读出各自的 SYSTEM 常量（agent 的提示词）
  · 按功能分类（写作/章节规划/世界构建/人物/情节/审核/记忆/立项）
  · 允许用户通过 web UI 覆盖任一 agent 的 SYSTEM（持久化到 prompts/overrides.json）
  · 启动时把 overrides 热打补丁到各 agent 模块（setattr）——agent 代码无需改动

工作原理：
  · 每个 agent 文件顶层一个 SYSTEM 变量，如 agents/writer.py 里 SYSTEM_TEMPLATE 或 SYSTEM
  · Python 对模块级变量的查找在函数调用时发生——我们改 module.SYSTEM 后，
    下一次 agent 函数调用读取 SYSTEM 就是新值
  · overrides.json 持久化；启动时 apply_overrides() 把所有覆盖值应用到对应模块
"""
from __future__ import annotations
import importlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

_OVERRIDES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "overrides.json"
)

# 缓存每个 prompt 的"代码默认值"——必须在应用 override 之前抓取
_DEFAULTS_CACHE: dict[str, str] = {}


# ═══════════════════════════════════════════════════════
#  注册表：每个可管理的 prompt
# ═══════════════════════════════════════════════════════

@dataclass
class PromptEntry:
    id: str                          # 唯一标识（module:var，如 "agents.writer:SYSTEM_TEMPLATE"）
    module: str                      # Python 模块路径（import 用）
    attr: str                        # 变量名
    label: str                       # 展示名
    category: str                    # 分组
    description: str = ""            # 人话描述：这条 prompt 控制什么
    # 运行时填充
    current: str = field(default="", repr=False)
    default: str = field(default="", repr=False)  # 代码里原始值
    overridden: bool = False


# 人类分类 → agents
# 每条：(id, module, attr, label, description)
_REGISTRY_SPEC: list[tuple[str, tuple[str, str, str, str, str]]] = [
    # ── 写作 ──
    ("写作", ("agents.writer:SYSTEM_TEMPLATE", "agents.writer", "SYSTEM_TEMPLATE",
              "Writer 写作 System",
              "Writer 写正文时的核心人格与铁律；细腻/节奏/对话/内心独白等。")),
    ("写作", ("agents.critic:SYSTEM", "agents.critic", "SYSTEM",
              "Critic 评审 System",
              "Critic 评价初稿的文学质量与结构性问题。")),

    # ── 章节规划 ──
    ("章节规划", ("agents.chapter_planner:SYSTEM", "agents.chapter_planner", "SYSTEM",
                  "ChapterPlanner System",
                  "生成章节场景蓝图；控制每章场景数、连续性、起承转合分形。")),
    ("章节规划", ("agents.chapter_planner:ENHANCER_SYSTEM", "agents.chapter_planner", "ENHANCER_SYSTEM",
                  "ChapterPlanner 创意增强 System",
                  "对已有章节骨架追加创意/意外角度/反差的增强器。")),
    ("章节规划", ("agents.chapter_type_planner:SYSTEM", "agents.chapter_type_planner", "SYSTEM",
                  "ChapterTypePlanner System",
                  "每卷章节类型配比（打脸/升级/感情/战斗/真相/转折等）。")),

    # ── 世界构建 ──
    ("世界构建", ("agents.master_dispatcher:SYSTEM", "agents.master_dispatcher", "SYSTEM",
                  "MasterDispatcher System",
                  "全书骨架蓝图——故事前提/角色槽位/势力骨架/关键节点。")),
    ("世界构建", ("agents.faction_architect:SYSTEM", "agents.faction_architect", "SYSTEM",
                  "FactionArchitect System",
                  "多题材自适应势力/组织体系设计。")),
    ("世界构建", ("agents.realm_designer:SYSTEM", "agents.realm_designer", "SYSTEM",
                  "RealmDesigner System",
                  "力量体系设计（境界/异能/职业级别等）。")),
    ("世界构建", ("agents.geography_designer:SYSTEM", "agents.geography_designer", "SYSTEM",
                  "GeographyDesigner System",
                  "地理/交通/区划系统。")),
    ("世界构建", ("agents.timeline_anchor:SYSTEM", "agents.timeline_anchor", "SYSTEM",
                  "TimelineAnchor System",
                  "世界历史时间线设计。")),
    ("世界构建", ("agents.economy_designer:SYSTEM", "agents.economy_designer", "SYSTEM",
                  "EconomyDesigner System",
                  "货币/物价/财富曲线。")),

    # ── 人物 ──
    ("人物", ("agents.character_designer:SYSTEM", "agents.character_designer", "SYSTEM",
              "CharacterDesigner System",
              "人物档案设计——按角色槽位展开具体人设。")),
    ("人物", ("agents.major_supporting_refiner:SYSTEM", "agents.major_supporting_refiner", "SYSTEM",
              "MajorSupportingRefiner System",
              "主要配角的细腻刻画深化。")),
    ("人物", ("agents.character_web:SYSTEM", "agents.character_web", "SYSTEM",
              "CharacterWeb System",
              "人物关系网设计——谁和谁什么关系、秘密、演变。")),
    ("人物", ("agents.protagonist_journey:SYSTEM", "agents.protagonist_journey", "SYSTEM",
              "ProtagonistJourney System",
              "主角历程三层规划（整体弧/卷里程碑/舞台节拍）。")),

    # ── 情节架构 ──
    ("情节架构", ("agents.volume_planner:SYSTEM", "agents.volume_planner", "SYSTEM",
                  "VolumePlanner System",
                  "整本起承转合+每卷详情。")),
    ("情节架构", ("agents.line_planner:SYSTEM", "agents.line_planner", "SYSTEM",
                  "LinePlanner System",
                  "叙事线规划（故事/情感/人物/悬疑线）。")),
    ("情节架构", ("agents.conflict_ladder:SYSTEM", "agents.conflict_ladder", "SYSTEM",
                  "ConflictLadder System",
                  "冲突阶梯——每卷冲突类型与升级。")),
    ("情节架构", ("agents.emotion_curve:SYSTEM", "agents.emotion_curve", "SYSTEM",
                  "EmotionCurve System",
                  "情绪曲线——每卷基调/低谷/高点。")),
    ("情节架构", ("agents.satisfaction_system:SYSTEM", "agents.satisfaction_system", "SYSTEM",
                  "SatisfactionSystem System",
                  "爽点系统——打脸/升级/真相等节点规划。")),
    ("情节架构", ("agents.foreshadow_manager:SYSTEM", "agents.foreshadow_manager", "SYSTEM",
                  "ForeshadowManager System",
                  "伏笔三档分批规划。")),
    ("情节架构", ("agents.fortune_planner:SYSTEM", "agents.fortune_planner", "SYSTEM",
                  "FortunePlanner System",
                  "每卷机缘规划。")),
    ("情节架构", ("agents.stage_architect:SYSTEM", "agents.stage_architect", "SYSTEM",
                  "StageArchitect System",
                  "叙事舞台（每卷的场景容器）设计。")),
    ("情节架构", ("agents.rhythm_designer:SYSTEM", "agents.rhythm_designer", "SYSTEM",
                  "RhythmDesigner System",
                  "每卷节奏段（慢热/快节奏/反转）。")),
    ("情节架构", ("agents.twist_designer:SYSTEM", "agents.twist_designer", "SYSTEM",
                  "TwistDesigner System",
                  "多层反转链设计——2-4 层层层反转。")),

    # ── 审核与校验 ──
    ("审核与校验", ("agents.setup_reviewer:SYSTEM", "agents.setup_reviewer", "SYSTEM",
                    "SetupReviewer System",
                    "章节定稿前对世界设定/力量规则/人格的合规校验。")),
    ("审核与校验", ("agents.continuity_checker:SYSTEM", "agents.continuity_checker", "SYSTEM",
                    "ContinuityChecker System",
                    "硬事实/因果链连续性校验。")),
    ("审核与校验", ("agents.voice_consistency_checker:SYSTEM", "agents.voice_consistency_checker", "SYSTEM",
                    "VoiceConsistencyChecker System",
                    "角色说话风格一致性校验。")),
    # ── 记忆与状态 ──
    ("记忆与状态", ("agents.state_updater:SYSTEM", "agents.state_updater", "SYSTEM",
                    "StateUpdater System",
                    "章节后状态集中回写（人物快照/关系变化/伏笔激活等）。")),
    ("记忆与状态", ("agents.memory:SYSTEM", "agents.memory", "SYSTEM",
                    "Memory System",
                    "从正文提取记忆条目/事实/角色状态。")),
    ("记忆与状态", ("agents.thread_tracker:SYSTEM", "agents.thread_tracker", "SYSTEM",
                    "ThreadTracker System",
                    "实时故事线索跟踪（开放循环/余波/暗线）。")),
    ("记忆与状态", ("agents.glossary_manager:SYSTEM", "agents.glossary_manager", "SYSTEM",
                    "GlossaryManager System",
                    "从正文提取专有名词入术语表。")),

    # ── 立项与意图 ──
    ("立项与意图", ("agents.intent_analyzer:SYSTEM", "agents.intent_analyzer", "SYSTEM",
                    "IntentAnalyzer System",
                    "把作者自然语言意图解析为立项信号。")),

    # ── 章节调整（chapter chat）──
    ("章节调整", ("agents.chat_editor:SYSTEM_TEMPLATE", "agents.chat_editor", "SYSTEM_TEMPLATE",
                  "ChatEditor System（章节对话调整）",
                  "章节「对话」功能的 system 提示词，控制 AI 在不动骨架的前提下如何修改笔触。"
                  "可用变量：{chapter_index}/{volume_index}/{volume_title}/{summary}/{word_count}"
                  "/{prior_requests_block}/{chapter_text}——不要去掉大括号。")),

    # ── 章节原型变体（chapter dispatcher）──
    # writer/planner SYSTEM 按章节情境选用的变体；变量 {genre} 会被填入
    ("章节原型·开篇", ("agents.prompt_variants:WRITER_SYSTEM_OPENING",
                       "agents.prompt_variants", "WRITER_SYSTEM_OPENING",
                       "Writer System（开篇章变体）",
                       "前 3 章写作时使用的变体——强调代入感/情绪锚点，延迟能力展示。"
                       "dispatcher 判定为 cold_open_hook 原型时启用。可用变量：{genre}")),
    ("章节原型·开篇", ("agents.prompt_variants:PLANNER_SYSTEM_OPENING",
                       "agents.prompt_variants", "PLANNER_SYSTEM_OPENING",
                       "ChapterPlanner System（开篇章变体）",
                       "前 3 章设计蓝图时使用的变体——强调情绪入口/信息稀疏/身份切换整幕。"
                       "可用变量：{genre}")),

    # 开篇 10 章 3 阶段独立 system —— 卷 1 ch 1-10,取代旧"黄金 3 章"教条
    ("章节原型·开篇 3 阶段", ("agents.prompt_variants:WRITER_SYSTEM_OPENING_KICK_OFF",
                                "agents.prompt_variants", "WRITER_SYSTEM_OPENING_KICK_OFF",
                                "Writer System(开篇·钩人期 1-3 章)",
                                "卷 1 ch 1-3 钩人期——立主角处境/情绪入口/反常细节;不强制金手指出场。"
                                "可用变量：{genre}")),
    ("章节原型·开篇 3 阶段", ("agents.prompt_variants:WRITER_SYSTEM_OPENING_ESTABLISH",
                                "agents.prompt_variants", "WRITER_SYSTEM_OPENING_ESTABLISH",
                                "Writer System(开篇·立住期 4-7 章)",
                                "卷 1 ch 4-7 立住期——主角驱动力浮出/世界规则展开/长线钩子。"
                                "可用变量：{genre}")),
    ("章节原型·开篇 3 阶段", ("agents.prompt_variants:WRITER_SYSTEM_OPENING_MAIN_LINE",
                                "agents.prompt_variants", "WRITER_SYSTEM_OPENING_MAIN_LINE",
                                "Writer System(开篇·入主线期 8-10 章)",
                                "卷 1 ch 8-10 入主线期——主线方向感/第一次不可逆选择/阶段性胜利或代价。"
                                "可用变量：{genre}")),
    # P1-4: 卷首/卷末专项 prompt
    ("章节原型·卷过渡", ("agents.prompt_variants:WRITER_SYSTEM_VOLUME_OPENER",
                            "agents.prompt_variants", "WRITER_SYSTEM_VOLUME_OPENER",
                            "Writer System(新卷开篇章)",
                            "卷 ≥2 的卷首章——上卷余波承接 + 新阶段定位 + 新钩子。"
                            "可用变量：{genre}")),
    ("章节原型·卷过渡", ("agents.prompt_variants:WRITER_SYSTEM_VOLUME_FINALE",
                            "agents.prompt_variants", "WRITER_SYSTEM_VOLUME_FINALE",
                            "Writer System(卷末章)",
                            "卷末章——情感收束 + 冲突阶段性解决 + 下卷悬念铺设。"
                            "可用变量：{genre}")),

    # 章后审计 —— 能力/金手指使用合理性
    ("章后审计", ("agents.ability_auditor:SYSTEM_TEMPLATE",
                  "agents.ability_auditor", "SYSTEM_TEMPLATE",
                  "AbilityAuditor System（金手指/技能使用审计）",
                  "每章写完后的审计员人格——核对金手指/技能使用是否符合设定/是否合理。"
                  "可用变量：{genre}。输出结构化 JSON 记录到 state.ability_audits。")),

    # 章后审计 —— 读者视角
    ("章后审计", ("agents.reader_experience_auditor:SYSTEM_TEMPLATE",
                  "agents.reader_experience_auditor", "SYSTEM_TEMPLATE",
                  "ReaderExperienceAuditor System（读者视角）",
                  "从挑剔的老读者视角审一章："
                  "信息密度/代入深度/钩子强度/新奇度/爽苦平衡/流畅度/情感深度；预估留存率。"
                  "可用变量：{genre}。输出 JSON 记录到 state.reader_audits。")),

    # 章后审计 —— 对话质量
    ("章后审计", ("agents.dialogue_auditor:SYSTEM_TEMPLATE",
                  "agents.dialogue_auditor", "SYSTEM_TEMPLATE",
                  "DialogueAuditor System（对话质量）",
                  "审整章对话的戏剧质量——潜台词/说教/角色差异化/情感节拍/称谓。"
                  "跟 voice_consistency（单角色）互补，这个看的是整章对话作为戏剧结构的质量。"
                  "可用变量：{genre}。输出 JSON 记录到 state.dialogue_audits。")),

    # 氛围设计 —— 让世界活起来的细节碎片
    ("氛围库", ("agents.customs_designer:SYSTEM_TEMPLATE",
                "agents.customs_designer", "SYSTEM_TEMPLATE",
                "CustomsDesigner System（氛围库设计）",
                "为 region/faction/volume 生成感官细节碎片 + 文化小条目。"
                "writer 写作时直接取用，避免每次现编世界感。"
                "可用变量：{genre}。输出 JSON 写入 state.atmosphere_library。")),

    # 反派深度
    ("反派塑造", ("agents.antagonist_depth_designer:SYSTEM_TEMPLATE",
                "agents.antagonist_depth_designer", "SYSTEM_TEMPLATE",
                "AntagonistDepthDesigner System（反派深度）",
                "为反派补 5 个深度字段：belief_system / despair_moments / "
                "charisma_signature / pov_insertion_volumes / inner_wound。"
                "可用变量：{genre}。直接更新 state.characters 中反派的字段。")),

    # 章后审计 —— 爽点 callback 账本(扫稿提取被嘲讽/被夺/被拒/失败/立誓/欠债事件)
    ("章后审计", ("agents.setup_ledger:SYSTEM",
                  "agents.setup_ledger", "SYSTEM",
                  "SetupLedger System(爽点 callback 账本)",
                  "章后从正文识别被嘲讽/被夺/被拒/失败/立誓事件,触发爽点章前找回响候选给 writer。无变量。")),

    # 章后审计 —— 模拟读者评论(Batch 5)
    ("章后审计", ("agents.comment_simulator:SYSTEM",
                  "agents.comment_simulator", "SYSTEM",
                  "CommentSimulator System(模拟读者评论)",
                  "章后模拟 5-10 条网文读者评论(追读派/挑刺派/路过派/章评党),挂到 ChapterSummary。无变量。")),

    # 写章前 —— 读者预期管理(Batch 5)
    ("章节规划", ("agents.expectation_manager:SYSTEM",
                  "agents.expectation_manager", "SYSTEM",
                  "ExpectationManager System(读者预期预测)",
                  "写章前预测 3-5 条读者下意识预期,chapter_planner 必须对每条标 satisfy/reverse/stack 决策。无变量。")),

    # 章后审计 —— 老作者调味直觉(Batch 6)
    ("章后审计", ("agents.flavor_advisor:SYSTEM",
                  "agents.flavor_advisor", "SYSTEM",
                  "FlavorAdvisor System(调味直觉)",
                  "每 N 章扫一次最近章节,输出'下章应当加什么调味料'。"
                  "结果加到 state.flavor_advices(滚动 5 条),chapter_planner 注入下章 prompt。无变量。")),

    # 章后润色 —— 按审计结果定向修正
    ("章后审计", ("agents.chapter_polisher:SYSTEM_TEMPLATE",
                  "agents.chapter_polisher", "SYSTEM_TEMPLATE",
                  "ChapterPolisher System（按审计润色）",
                  "审计完看到问题后，按 issue 清单定向修正章节正文——只动对应 issue 的地方，"
                  "不动骨架/情节/钩子，字数变动 ±10% 内。可用变量：{genre}。")),

    # 可组合 context blocks——按"章节情境"注入到 prompt 顶部
    ("章节原型·Block", ("agents.prompt_variants:BLOCK_INTRO_STAKES",
                        "agents.prompt_variants", "BLOCK_INTRO_STAKES",
                        "Block · 开篇代入提醒",
                        "开篇章通用注入块，提醒 AI 先建立情绪锚点再展开情节。")),
    ("章节原型·Block", ("agents.prompt_variants:BLOCK_IDENTITY_SHIFT_TRANSMIGRATION",
                        "agents.prompt_variants", "BLOCK_IDENTITY_SHIFT_TRANSMIGRATION",
                        "Block · 穿越身份错位",
                        "subgenre 含「穿越」时注入，要求展开身份切换过程。")),
    ("章节原型·Block", ("agents.prompt_variants:BLOCK_IDENTITY_SHIFT_REBIRTH",
                        "agents.prompt_variants", "BLOCK_IDENTITY_SHIFT_REBIRTH",
                        "Block · 重生记忆双重性",
                        "subgenre 含「重生」时注入，处理前世记忆重压。")),
    ("章节原型·Block", ("agents.prompt_variants:BLOCK_IDENTITY_SHIFT_SYSTEM",
                        "agents.prompt_variants", "BLOCK_IDENTITY_SHIFT_SYSTEM",
                        "Block · 系统金手指首现",
                        "subgenre 含「系统」时注入，处理金手指首次出现。")),
]


def _capture_default(pid: str, mod_name: str, attr: str) -> str:
    """首次读取某模块 attr 时缓存为"代码默认值"；之后始终返回缓存。"""
    if pid in _DEFAULTS_CACHE:
        return _DEFAULTS_CACHE[pid]
    try:
        mod = importlib.import_module(mod_name)
        val = getattr(mod, attr, "")
    except Exception as e:
        val = f"[加载失败：{type(e).__name__}: {e}]"
    # 注意：如果 override 已经被应用过，这里可能抓到的是 override——
    # 因此必须保证 _capture_default 在任何 _apply_one 之前被调用。
    # apply_all_overrides() 会先 seed 再 apply；手动改动也会调用 all_entries()
    # 导致在 apply 之前 seed 默认，所以总体安全。
    _DEFAULTS_CACHE[pid] = val
    return val


def _seed_all_defaults() -> None:
    """把所有注册 prompt 的默认值读一遍，缓存起来。"""
    for _category, (pid, mod_name, attr, _label, _desc) in _REGISTRY_SPEC:
        _capture_default(pid, mod_name, attr)


def all_entries() -> list[PromptEntry]:
    """返回所有已注册 prompt 的当前状态（已加载 + 覆盖）。"""
    # 确保所有默认值都已 seed（只执行一次）
    if len(_DEFAULTS_CACHE) < len(_REGISTRY_SPEC):
        _seed_all_defaults()

    entries: list[PromptEntry] = []
    overrides = _load_overrides()
    for category, (pid, mod_name, attr, label, description) in _REGISTRY_SPEC:
        default_val = _capture_default(pid, mod_name, attr)
        current = overrides.get(pid, default_val)
        entries.append(PromptEntry(
            id=pid, module=mod_name, attr=attr,
            label=label, category=category, description=description,
            current=current, default=default_val,
            overridden=(pid in overrides and overrides[pid] != default_val),
        ))
    return entries


def get_entry(prompt_id: str) -> Optional[PromptEntry]:
    for e in all_entries():
        if e.id == prompt_id:
            return e
    return None


def save_override(prompt_id: str, body: str) -> PromptEntry:
    """保存并立即应用 override。空字符串/完全等于 default 则删除 override。"""
    entry = get_entry(prompt_id)
    if not entry:
        raise ValueError(f"未注册的 prompt_id：{prompt_id}")
    overrides = _load_overrides()
    body = body or ""
    if body.strip() == "" or body == entry.default:
        overrides.pop(prompt_id, None)
    else:
        overrides[prompt_id] = body
    _save_overrides(overrides)
    # 应用到模块
    _apply_one(prompt_id, overrides.get(prompt_id, entry.default))
    return get_entry(prompt_id)  # 返回最新状态


def delete_override(prompt_id: str) -> PromptEntry:
    entry = get_entry(prompt_id)
    if not entry:
        raise ValueError(f"未注册的 prompt_id：{prompt_id}")
    overrides = _load_overrides()
    overrides.pop(prompt_id, None)
    _save_overrides(overrides)
    # 恢复默认
    _apply_one(prompt_id, entry.default)
    return get_entry(prompt_id)


def apply_all_overrides() -> int:
    """
    启动时调用一次——先缓存所有默认值，再把 overrides.json 里的覆盖值 setattr 到对应模块。
    返回应用的条目数。
    """
    # 1. 先读一遍所有默认值（重要：在任何 override 应用前）
    _seed_all_defaults()
    # 2. 应用 override
    overrides = _load_overrides()
    count = 0
    for pid, body in overrides.items():
        try:
            _apply_one(pid, body)
            count += 1
        except Exception as e:
            print(f"[prompts_registry] 应用 {pid} 失败：{e}")
    if count:
        print(f"[prompts_registry] 已应用 {count} 条用户提示词覆盖")
    return count


# ═══════════════════════════════════════════════════════
#  内部辅助
# ═══════════════════════════════════════════════════════

def _apply_one(prompt_id: str, body: str) -> None:
    mod_name, attr = prompt_id.split(":", 1)
    mod = importlib.import_module(mod_name)
    setattr(mod, attr, body)


def _load_overrides() -> dict:
    if not os.path.exists(_OVERRIDES_FILE):
        return {}
    try:
        with open(_OVERRIDES_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception as e:
        print(f"[prompts_registry] 读 overrides 失败：{e}")
        return {}


def _save_overrides(overrides: dict) -> None:
    os.makedirs(os.path.dirname(_OVERRIDES_FILE), exist_ok=True)
    with open(_OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)


def categories() -> list[str]:
    """返回分类顺序（按注册表顺序去重）。"""
    seen = []
    for cat, _ in _REGISTRY_SPEC:
        if cat not in seen:
            seen.append(cat)
    return seen
