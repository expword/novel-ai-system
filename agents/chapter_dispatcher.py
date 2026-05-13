"""
章节 prompt dispatcher —— 根据章节情境路由到对应的 prompt 变体和 context blocks。

三个输入维度：
  A) 小说子类型（穿越/重生/系统/升级流/反套路/无特殊）—— state.creative_intent.suggested_subgenre
  B) 章节位置与时序（开篇/卷首/卷尾/卷中/高潮/反转 ...）—— directive + state 推导
  C) 章节功能原型（cold_open / identity_shift / reveal / climax / ...）

输出一个 ChapterPromptPlan：
  - writer_variant / planner_variant：选哪个 SYSTEM 变体
  - context_blocks：注入到 prompt 顶部的小段指导
  - must_include_hints：额外塞到 directive.must_include 的硬约束
  - model_profile_id / temperature：模型路由（当前未使用，留扩展位）

新增原型或规则 → 改这里，不改 writer/planner 代码。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# 前 N 章判定为"开篇章"——窗口扩大到 5 章，分阶段路由
OPENING_CHAPTER_THRESHOLD = 5


def _build_world_primer(state) -> str:
    """读者必须在前 5 章接收的硬事实清单——开篇章硬注入 must_include。"""
    parts = []
    # 世界基础（power_system）
    if state.power_system:
        parts.append(f"力量/阶层体系：{(state.power_system.system_name or '')}—— {(state.power_system.flow_brief()[:120] if hasattr(state.power_system, 'flow_brief') else '')}")
    # 世界观一句话
    ws = (getattr(state, "world_setting", "") or "").strip()
    if ws:
        parts.append(f"世界基调：{ws[:160]}")
    # 主角硬事实
    proto = next((c for c in state.characters if c.role.value == "主角"), None)
    if proto:
        parts.append(
            f"主角：{proto.name}（{proto.realm if proto.realm else '无层级'}）"
            f"——出身/处境：{proto.background[:80]}；当前缺失：{proto.desire[:50]}"
        )
    # 主要对手骨架
    if state.factions:
        top_factions = [f for f in state.factions if not f.is_hidden][:3]
        if top_factions:
            parts.append("主要势力：" + " / ".join(f"{f.name}({f.tier_name()})" for f in top_factions))
    # 主角弧线（已由 0.6 阶段确定）
    j = state.protagonist_journey
    if j and j.overall_theme:
        parts.append(f"故事核心：{j.overall_theme[:60]}（致命弱点：{j.fatal_flaw[:40]}）")
    if not parts:
        return ""
    return (
        "【读者必须在前 5 章接收的硬事实——本章正文中至少自然带出 1-2 条】\n"
        + "\n".join(f"  · {p}" for p in parts)
    )


# ═══════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class ChapterPromptPlan:
    """dispatcher 的输出——告诉 writer/planner 这一章该用什么 prompt 资源。"""
    writer_variant: str = "default"          # 对应 prompt_variants.WRITER_SYSTEM_{UPPER}
    planner_variant: str = "default"          # 对应 prompt_variants.PLANNER_SYSTEM_{UPPER}
    context_block_ids: list[str] = field(default_factory=list)  # 对应 prompt_variants.BLOCK_{UPPER}
    must_include_hints: list[str] = field(default_factory=list)  # 追加到 must_include 的硬约束
    model_profile_id: Optional[str] = None   # None=用全局默认 profile
    writer_temperature: Optional[float] = None  # None=用代码默认
    # 便于日志 & 调试
    archetype: str = "default"
    signals: dict = field(default_factory=dict)  # 哪些判定信号命中了


# ═══════════════════════════════════════════════════════════
#  子类型启发式检测
# ═══════════════════════════════════════════════════════════

_SUBGENRE_KEYWORDS = {
    "穿越":   ["穿越", "穿到", "魂穿", "系统附身穿越", "transmigrat", "isekai"],
    "重生":   ["重生", "重来一世", "重回", "回到过去", "回到从前"],
    "系统":   ["系统", "金手指系统", "签到系统", "任务系统", "面板"],
    "升级流": ["升级流", "等级制", "打怪升级", "数值修炼"],
    "反套路": ["反套路", "反转", "打脸套路"],
}


def detect_subgenre(state) -> str:
    """
    从 creative_intent 推断子类型。优先用用户明确填的字段，没有就扫 theme/description。
    返回：子类型字符串（如"穿越"），或空串表示无特殊。
    """
    ci = getattr(state, "creative_intent", None)
    if not ci:
        return ""
    # 1. 用户明确填的
    explicit = (getattr(ci, "suggested_subgenre", "") or "").strip()
    if explicit:
        return explicit
    # 2. 启发式扫描 theme + genre + raw_description
    haystack = " ".join([
        getattr(ci, "suggested_theme", "") or "",
        getattr(ci, "suggested_genre", "") or "",
        getattr(ci, "raw_description", "") or "",
    ]).lower()
    for subtype, keywords in _SUBGENRE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in haystack:
                return subtype
    return ""


# ═══════════════════════════════════════════════════════════
#  主函数
# ═══════════════════════════════════════════════════════════

def dispatch(state, directive) -> ChapterPromptPlan:
    """
    输入当前 state 和本章 directive，输出 ChapterPromptPlan。
    永远返回一个 plan（包括默认路径），上游按 plan 拼 prompt 即可。
    """
    plan = ChapterPromptPlan()

    # ── 信号：章节类型（已有系统）──
    ch_type = (getattr(directive, "chapter_type", "") or "").strip()
    if ch_type == "感情章":
        plan.writer_variant = "romance"
    elif ch_type == "战斗章":
        plan.writer_variant = "combat"

    # ── 信号：是否开篇章（前 N 章）──
    ch_idx = getattr(directive, "chapter_index", 0)
    is_book_opening = 1 <= ch_idx <= OPENING_CHAPTER_THRESHOLD
    plan.signals["chapter_index"] = ch_idx
    plan.signals["is_book_opening"] = is_book_opening

    if is_book_opening:
        plan.writer_variant = "opening"       # 覆盖 romance/combat——开篇优先级更高
        plan.planner_variant = "opening"
        plan.context_block_ids.append("INTRO_STAKES")
        # 开篇温度稍降，稳一点
        plan.writer_temperature = 0.75
        # 按 ch_idx 分阶段路由——每章侧重不同
        if ch_idx == 1:
            plan.archetype = "stage1_cold_open_hook"
            plan.must_include_hints.append(
                "本章是全书第 1 章——首要任务是【钩住读者】：第一幕就建立读者对主角的强情绪锚点"
                "（困境/渴望/孤独/不甘/不服/秘密之一），章末必须留一个让读者非翻下一章不可的悬念。"
                "不要急于全盘铺设定，画面感和情绪锚点优先。"
            )
        elif ch_idx == 2:
            plan.archetype = "stage2_world_establish"
            plan.must_include_hints.append(
                "本章是第 2 章——任务是【把世界讲清楚】：通过场景/对话/冲突自然带出力量体系/势力格局/世界规则的核心 2-3 条。"
                "不要无脑信息倾倒（不要长段落讲设定），要用主角的眼睛和处境让读者读懂世界的运转方式。"
            )
        elif ch_idx == 3:
            plan.archetype = "stage3_inciting_incident"
            plan.must_include_hints.append(
                "本章是第 3 章——任务是【点燃故事】：必须发生一个推动主角离开现状的关键事件（遭遇/打击/邀请/发现/失去之一），"
                "让前 2 章建立的现状被打破，让读者明白本书的'故事'真正开始了。"
            )
        elif ch_idx in (4, 5):
            plan.archetype = "stage45_consolidate"
            plan.must_include_hints.append(
                f"本章是第 {ch_idx} 章——任务是【巩固 + 推进】：让主角对第 3 章的事件做出真实反应/抉择，"
                f"开始进入主线。前几章已建立的世界事实/角色关系应该回响一次（让读者觉得'这书没忘'）。"
            )
        # 所有开篇 5 章硬注入"读者必须接收的硬事实清单"
        primer = _build_world_primer(state)
        if primer:
            plan.must_include_hints.append(primer)

    # ── 信号：子类型 ──
    subtype = detect_subgenre(state)
    plan.signals["subgenre"] = subtype

    # 开篇章 + 身份切换类子类型 → 额外注入 identity_shift block
    if is_book_opening and subtype:
        if "穿越" in subtype:
            plan.context_block_ids.append("IDENTITY_SHIFT_TRANSMIGRATION")
            plan.must_include_hints.append(
                "本章必须至少一整幕展开「从原世界到新身份」的切换过程——身体不适、记忆错乱、"
                "本能动作冲突、意识到自己变了的心理过程。绝不允许主角瞬间无缝进入新身份执行任务。"
            )
            plan.archetype = "cold_open_hook+identity_shift"
        elif "重生" in subtype:
            plan.context_block_ids.append("IDENTITY_SHIFT_REBIRTH")
            plan.must_include_hints.append(
                "本章必须处理「前世记忆重回当下」的心理重压——熟悉场景触发前世创伤/遗憾，"
                "主角情绪不合时宜地波动（旁人觉得奇怪，读者心领神会）。"
            )
            plan.archetype = "cold_open_hook+identity_shift"
        elif "系统" in subtype:
            plan.context_block_ids.append("IDENTITY_SHIFT_SYSTEM")
            plan.must_include_hints.append(
                "本章必须先铺垫主角的具体困境，再让系统/金手指出场——首次出现要有冲击感，"
                "能力不全盘托出，主角态度有层次（怀疑→试探→谨慎使用）。"
            )
            plan.archetype = "cold_open_hook+identity_shift"

    # 后续可加：reveal_chapter / climax_chapter / satisfaction_peak / emotional_resonance ...

    return plan


# ═══════════════════════════════════════════════════════════
#  变体/块查询 —— 上游用这两个函数取实际 prompt 文本
# ═══════════════════════════════════════════════════════════

def get_writer_system(variant: str, *, genre: str = "") -> Optional[str]:
    """
    取 writer SYSTEM 变体模板。
    variant='default' → 返回 None（调用方回退到 writer.py 内置 SYSTEM_TEMPLATE）
    variant='opening' 等 → 返回模块级常量 WRITER_SYSTEM_OPENING（已替换 {genre}）
    """
    if not variant or variant == "default":
        return None
    from agents import prompt_variants as pv
    attr = f"WRITER_SYSTEM_{variant.upper()}"
    tmpl = getattr(pv, attr, None)
    if not tmpl:
        return None
    try:
        return tmpl.format(genre=genre or "")
    except (KeyError, IndexError):
        return tmpl


def get_planner_system(variant: str, *, genre: str = "") -> Optional[str]:
    if not variant or variant == "default":
        return None
    from agents import prompt_variants as pv
    attr = f"PLANNER_SYSTEM_{variant.upper()}"
    tmpl = getattr(pv, attr, None)
    if not tmpl:
        return None
    try:
        return tmpl.format(genre=genre or "")
    except (KeyError, IndexError):
        return tmpl


def get_block(block_id: str) -> str:
    """取 context block 文本（block_id 如 'INTRO_STAKES'）。

    未知 id 会打一个警告，避免 dispatcher 里拼错 id 时静默吞掉导致 prompt 缺段。
    """
    if not block_id:
        return ""
    from agents import prompt_variants as pv
    attr = f"BLOCK_{block_id.upper()}"
    val = getattr(pv, attr, None)
    if val is None:
        print(f"  ⚠ [dispatcher] 未知 block id：{block_id}（attr={attr} 不存在）——注入空串")
        return ""
    return val or ""


def compose_blocks(plan: ChapterPromptPlan) -> str:
    """把 plan.context_block_ids 拼成一段文字，供 writer/planner 塞到 prompt 顶部。"""
    parts = [get_block(bid) for bid in plan.context_block_ids]
    return "\n".join(p for p in parts if p.strip())
