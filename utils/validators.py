"""
数据合规验证器 —— agent 生成完 state 后跑一次检查，不合规就重新生成。

与 request_json 里 custom_validator 的区别：
  · request_json 在"LLM 单次调用"层面校验（返回的 JSON 对不对）
  · 本模块在"agent 完整产出"层面校验（整条数据链合理吗）
    例：势力层级是否严格递增？每层势力数是否达标？人物角色分布是否合理？

一次不合规就触发重新生成（含模型轮换）；多次不合规就打印警告接受当前稿。

用法：
    validate_and_regen(
        state,
        section_name="factions",
        generator_fn=lambda s: design_factions(s),
        validators=[faction_tier_valid, faction_count_valid],
        max_retries=2,
    )
"""
from __future__ import annotations
from typing import Callable, Optional

from persistence.state import NovelState


# ═══════════════════════════════════════════════════════
#  Validator 协议
# ═══════════════════════════════════════════════════════
# 一个 validator 是函数 (state) -> (ok: bool, reason: str)
# ok=True: 通过
# ok=False: reason 说明问题（用于下次重生成时的 prompt 提示）

Validator = Callable[[NovelState], "tuple[bool, str]"]


# ═══════════════════════════════════════════════════════
#  势力格局校验器
# ═══════════════════════════════════════════════════════

def faction_tier_hierarchy(state: NovelState) -> tuple[bool, str]:
    """势力分层：每层都要有势力，没有空层跳跃"""
    from config import FACTION_TIERS_MIN
    if not state.factions:
        return False, "factions 为空"
    tiers = sorted(set(f.tier for f in state.factions))
    if len(tiers) < FACTION_TIERS_MIN:
        return False, f"仅 {len(tiers)} 层势力，低于配置的 {FACTION_TIERS_MIN} 层"
    # 检查层号连续
    for i, t in enumerate(tiers):
        if i > 0 and t - tiers[i - 1] > 1:
            return False, f"层号不连续：{tiers[i-1]} → {t}（跳层）"
    # 最底层应该有 protagonist_start=True 的势力
    tier1_factions = [f for f in state.factions if f.tier == tiers[0]]
    if not any(f.protagonist_start for f in tier1_factions):
        return False, f"最底层（第 {tiers[0]} 层）没有标记主角起点的势力"
    return True, ""


def faction_count_per_tier(state: NovelState) -> tuple[bool, str]:
    """每层势力数要达到 config.FACTIONS_PER_TIER_MIN"""
    from config import FACTIONS_PER_TIER_MIN
    tier_counts = {}
    for f in state.factions:
        tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
    for tier, count in tier_counts.items():
        if count < FACTIONS_PER_TIER_MIN:
            return False, f"第 {tier} 层只有 {count} 个势力，低于最少 {FACTIONS_PER_TIER_MIN}"
    return True, ""


def faction_has_hidden_and_neutral(state: NovelState) -> tuple[bool, str]:
    """必须有至少 1 个隐藏势力 + 至少 1 个中立势力"""
    has_hidden = any(f.is_hidden for f in state.factions)
    has_neutral = any(f.is_neutral for f in state.factions)
    missing = []
    if not has_hidden:
        missing.append("隐藏势力（is_hidden=True）")
    if not has_neutral:
        missing.append("中立势力（is_neutral=True）")
    if missing:
        return False, f"缺少：{' + '.join(missing)}"
    return True, ""


# ═══════════════════════════════════════════════════════
#  人物校验器
# ═══════════════════════════════════════════════════════

def characters_have_protagonist(state: NovelState) -> tuple[bool, str]:
    from persistence.state import CharacterRole
    pros = [c for c in state.characters if c.role == CharacterRole.PROTAGONIST]
    if not pros:
        return False, "缺少主角"
    if len(pros) > 1:
        return False, f"有 {len(pros)} 个主角，只能 1 个"
    return True, ""


def characters_meet_count_targets(state: NovelState) -> tuple[bool, str]:
    """按 config 数量目标检查各角色类型"""
    from persistence.state import CharacterRole
    from config import MAJOR_ALLIES_MIN, ANTAGONISTS_MIN
    counts = {r: 0 for r in CharacterRole}
    for c in state.characters:
        counts[c.role] = counts.get(c.role, 0) + 1
    issues = []
    if counts.get(CharacterRole.MAJOR, 0) < MAJOR_ALLIES_MIN:
        issues.append(f"主要配角仅 {counts[CharacterRole.MAJOR]} 人，目标 ≥ {MAJOR_ALLIES_MIN}")
    if counts.get(CharacterRole.ANTAGONIST, 0) < ANTAGONISTS_MIN:
        issues.append(f"反派仅 {counts[CharacterRole.ANTAGONIST]} 人，目标 ≥ {ANTAGONISTS_MIN}")
    if issues:
        return False, " + ".join(issues)
    return True, ""


def characters_cover_narrative_functions(state: NovelState) -> tuple[bool, str]:
    """必须覆盖核心 3 类叙事功能：情感支撑 + 成长引导 + 对立冲突"""
    required = {"情感支撑者", "成长引导者", "对立冲突者"}
    present = {c.narrative_function for c in state.characters if c.narrative_function}
    missing = required - present
    if missing:
        return False, f"未覆盖叙事功能：{' / '.join(missing)}"
    return True, ""


def characters_have_relationships(state: NovelState) -> tuple[bool, str]:
    """非主角角色的 relationships 不能为空（每个配角至少 1 条关系）"""
    from persistence.state import CharacterRole
    missing = [
        c.name for c in state.characters
        if c.role != CharacterRole.PROTAGONIST
        and len(c.relationships) == 0
    ]
    if len(missing) > len(state.characters) * 0.3:  # 容忍 30% 缺失
        return False, f"{len(missing)} 个角色 relationships 为空：{', '.join(missing[:5])}..."
    return True, ""


# ═══════════════════════════════════════════════════════
#  卷结构校验器
# ═══════════════════════════════════════════════════════

def volumes_complete(state: NovelState) -> tuple[bool, str]:
    """卷数达到 config.NUM_VOLUMES"""
    from config import NUM_VOLUMES
    if len(state.volumes) < NUM_VOLUMES:
        return False, f"仅 {len(state.volumes)}/{NUM_VOLUMES} 卷"
    # 章节起止连续不空
    for i, v in enumerate(state.volumes):
        if not v.title.strip() or not v.theme.strip():
            return False, f"第 {v.index} 卷 title/theme 为空"
        if v.chapter_end <= v.chapter_start:
            return False, f"第 {v.index} 卷 chapter 起止非法（{v.chapter_start}-{v.chapter_end}）"
    return True, ""


def volumes_have_structure_role(state: NovelState) -> tuple[bool, str]:
    """每卷必须有 structure_role"""
    missing = [v.index for v in state.volumes if not v.structure_role]
    if missing:
        return False, f"卷 {missing} 缺 structure_role"
    return True, ""


# ═══════════════════════════════════════════════════════
#  力量体系校验器
# ═══════════════════════════════════════════════════════

def power_system_valid(state: NovelState) -> tuple[bool, str]:
    """力量体系非空（若 has_hierarchy=True 则 realms 也要有）"""
    ps = state.power_system
    if ps is None:
        return False, "power_system 为 None"
    if not ps.system_name.strip():
        return False, "system_name 为空"
    if getattr(ps, "has_hierarchy", True) and not ps.realms:
        return False, "has_hierarchy=True 但 realms 为空"
    return True, ""


# ═══════════════════════════════════════════════════════
#  主入口：验证 + 自动重生成
# ═══════════════════════════════════════════════════════

def validate_and_regen(
    state: NovelState,
    section_name: str,
    generator_fn: Callable[[NovelState], None],
    validators: list[Validator],
    max_retries: int = 2,
    use_model_fallback: bool = False,
) -> bool:
    """
    跑 validators 检查 state；不合规就重跑 generator_fn。
    返回 True = 最终合规；False = 重试耗尽仍不合规。

    - generator_fn: 无参数函数（已闭包好 state）
    - validators: validator 列表（按顺序检查）
    - max_retries: 最多重生成次数（0 = 只验证不重生）
    - use_model_fallback: True = 每次重生成走 fallback_runner（轮换模型）
    """
    for attempt in range(max_retries + 1):
        # 跑所有 validator
        all_ok = True
        reasons: list[str] = []
        for v in validators:
            try:
                ok, reason = v(state)
                if not ok:
                    all_ok = False
                    reasons.append(f"[{v.__name__}] {reason}")
            except Exception as e:
                all_ok = False
                reasons.append(f"[{v.__name__}] 校验器自身异常：{e}")

        if all_ok:
            if attempt > 0:
                print(f"  [验证·{section_name}] 第 {attempt} 次重生后合规 ✓")
            return True

        if attempt >= max_retries:
            print(f"  [验证·{section_name}] 重试耗尽（{max_retries} 次），接受当前稿。遗留问题：")
            for r in reasons[:5]:
                print(f"      · {r}")
            return False

        print(f"  [验证·{section_name}] 第 {attempt+1} 次不合规，重新生成")
        for r in reasons[:3]:
            print(f"      · {r}")

        # 触发重生
        try:
            if use_model_fallback:
                from llm_layer.fallback_runner import run_with_model_fallback
                run_with_model_fallback(
                    fn=lambda: (generator_fn(state), True)[1],
                    agent_name=f"regen-{section_name}",
                    retries_per_model=1,  # 外层已在循环了
                )
            else:
                generator_fn(state)
        except Exception as e:
            print(f"  [验证·{section_name}] 重生异常：{e}")
            return False

    return False


# ═══════════════════════════════════════════════════════
#  Section → validator 映射（便于统一调用）
# ═══════════════════════════════════════════════════════

SECTION_VALIDATORS: dict[str, list[Validator]] = {
    "factions": [
        faction_tier_hierarchy,
        faction_count_per_tier,
        faction_has_hidden_and_neutral,
    ],
    "characters": [
        characters_have_protagonist,
        characters_meet_count_targets,
        characters_cover_narrative_functions,
        characters_have_relationships,
    ],
    "volumes": [
        volumes_complete,
        volumes_have_structure_role,
    ],
    "power_system": [
        power_system_valid,
    ],
}


def validate_section(state: NovelState, section_name: str) -> list[str]:
    """只验不重生——返回问题清单。"""
    validators = SECTION_VALIDATORS.get(section_name, [])
    issues = []
    for v in validators:
        try:
            ok, reason = v(state)
            if not ok:
                issues.append(f"[{v.__name__}] {reason}")
        except Exception as e:
            issues.append(f"[{v.__name__}] 异常：{e}")
    return issues
