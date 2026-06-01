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


# 前 N 章判定为"开篇章"——窗口 10 章，3 阶段路由
# 阶段:1-3 钩人 / 4-7 立住 / 8-10 入主线 (取代旧"黄金 3 章"教条)
OPENING_CHAPTER_THRESHOLD = 10


def _build_world_primer(state) -> str:
    """读者必须在开篇 10 章接收的硬事实清单——开篇章硬注入 must_include。

    不要一次塞光——分散到 3 阶段:钩人期偏主角处境/世界基调,
    立住期偏体系/势力,入主线期偏故事核心/反派初始。
    """
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
    # 故事核心（master_outline.thematic_core + 主角致命弱点）
    mo = getattr(state, "master_outline", None)
    core_str = (getattr(mo, "thematic_core", "") or "").strip() if mo else ""
    if core_str:
        flaw = (proto.fatal_flaw or "").strip() if proto else ""
        flaw_part = f"（致命弱点：{flaw[:40]}）" if flaw else ""
        parts.append(f"故事核心：{core_str[:60]}{flaw_part}")
    if not parts:
        return ""
    return (
        "【读者必须在前 10 章接收的硬事实——本章正文中至少自然带出 1-2 条】\n"
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
        # 开篇 10 章 = 卷 1 ch_idx ∈ [1, 10]:走 3 阶段路由
        #   ch 1-3 钩人 / ch 4-7 立住 / ch 8-10 入主线
        # writer_variant 由阶段决定;详细硬指引在 prompt_variants.WRITER_SYSTEM_<阶段>
        # 此处只做 archetype 标记 + 注入"硬事实清单"primer + 最小化阶段提示
        plan.planner_variant = "opening"  # planner 仍用通用 opening 模板
        plan.context_block_ids.append("INTRO_STAKES")
        plan.writer_temperature = 0.75
        vol_idx = getattr(directive, "volume_index", 0)
        if vol_idx == 1 and 1 <= ch_idx <= 10:
            if 1 <= ch_idx <= 3:
                phase = "opening_kick_off"
                phase_label = "钩人期 (1-3 章)"
            elif 4 <= ch_idx <= 7:
                phase = "opening_establish"
                phase_label = "立住期 (4-7 章)"
            else:  # 8-10
                phase = "opening_main_line"
                phase_label = "入主线期 (8-10 章)"
            plan.writer_variant = phase
            plan.archetype = f"{phase}:ch{ch_idx}"
            plan.signals["opening_phase"] = phase
            # 阶段最小提示:只补一句章在阶段中的位置,具体硬指引由 writer_variant 的
            # SYSTEM 模板承担 (prompt_variants.WRITER_SYSTEM_<phase>)。
            plan.must_include_hints.append(
                f"本章是开篇{phase_label}的第 {ch_idx} 章——按本期任务执行,不要复述前/后阶段任务。"
            )
        else:
            # 卷 1 之外的开篇章 (不应该出现,容错走通用 opening)
            plan.writer_variant = "opening"
    else:
        # 非全书开篇,但可能是某卷的卷首/卷末——P1-4 专项 prompt
        vol_idx = getattr(directive, "volume_index", 0)
        # 取本卷起讫章号(从 state)
        try:
            vol = state.get_volume(vol_idx) if hasattr(state, "get_volume") else None
        except Exception:
            vol = None
        if vol is not None and vol_idx >= 2:
            if ch_idx == vol.chapter_start:
                plan.writer_variant = "volume_opener"
                plan.archetype = f"volume_opener:V{vol_idx}"
                plan.signals["is_volume_opener"] = True
            elif ch_idx == vol.chapter_end:
                plan.writer_variant = "volume_finale"
                plan.archetype = f"volume_finale:V{vol_idx}"
                plan.signals["is_volume_finale"] = True
        # 所有开篇 10 章硬注入"读者必须接收的硬事实清单"
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
