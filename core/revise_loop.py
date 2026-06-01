"""
AuditReviseLoop —— audit→revise 多轮循环的统一框架。

设计动机：
  · 系统里有 5 处 revise 路径（canon / polisher / reader / dialogue / sensitive），
    每条自己手写 "audit → 看分数/issues → 拼 feedback → 调 revise → 长度兜底
    → 写盘 → 字数同步 → 重审 → critical 残留写 warning" 的完整循环
  · 共性的逻辑（长度兜底/写盘/字数同步/重审）已经被 `director._apply_revision_and_reaudit`
    抽出来——但只有 polisher/reader/dialogue 在用，canon-revise 和 sensitive-revise
    自己又写了一遍
  · 各路径细节差异让全部统一暴露过多参数。本框架走"声明式 config + 通用循环执行器"
    模式：每条路径只声明它的 audit_fn / needs_revise / feedback_builder / 阈值，
    循环骨架统一在 run_revise_loop 里

设计原则（按 [[feedback_generic_prompts]]）：
  · 不依赖任何项目术语——纯调度框架
  · 失败按 [[feedback_surface_errors]] 写 progress_warning，不静默
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Callable, Any, Optional


@dataclass
class ReviseConfig:
    """单条 revise 路径的声明式配置。

    audit_fn(state, chapter_index, text) → audit_result（任意类型，下游自己识别）
    needs_revise(audit_result) → bool 是否触发本轮 revise
    feedback_builder(audit_result, round_idx) → str 给 revise_chapter 的 feedback 文本
    revise_fn(state, directive, text, feedback) → str 新文本（director 注入 revise_chapter）

    可选回调（每个对应不同时机的副作用）：
    on_short(round_idx, new_len, original_len, short_streak)
    on_round_done(round_idx, audit_before, audit_after, new_text)
    on_residual_critical(remaining_audit) —— 跑满后仍 needs_revise，本回调收尾

    阈值：
    max_rounds 最多重试几轮（1 = 单轮一次性）
    min_length_ratio 单轮输出短于原稿 N 倍就丢弃此轮
    max_short_streak 连续多少轮过短就提前退出（防 LLM 异常空转）
    """
    label: str
    audit_fn: Callable[[Any, int, str], Any]
    needs_revise: Callable[[Any], bool]
    feedback_builder: Callable[[Any, int], str]
    revise_fn: Callable[[Any, Any, str, str], str]
    max_rounds: int = 1
    min_length_ratio: float = 0.7
    max_short_streak: int = 2

    # 回调（可选）
    on_short: Optional[Callable[[int, int, int, int], None]] = None
    on_round_done: Optional[Callable[[int, Any, Any, str], None]] = None
    on_residual_critical: Optional[Callable[[Any], None]] = None

    # 持久化：在每轮接受 new_text 时同步写盘 + word_count
    chapter_path: str = ""
    update_word_count: bool = True


# ═══════════════════════════════════════════════════════
#  P0-2: 跨 audit revise 总上限 (防 9 轮叠加把个性稀释)
# ═══════════════════════════════════════════════════════
# 单章跨 critic/setup/canon/reader/dialogue/polisher/sensitive 累计接受的 revise 轮数上限
# 超过此上限,后续 revise 路径 short-circuit (强制收稿,防 LLM 反复"修平")
MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER = 5


def get_total_rounds_used(directive: Any) -> int:
    """读 directive 已用的跨 audit revise 总轮数。"""
    return int(getattr(directive, "_total_revise_rounds", 0) or 0)


def add_total_rounds_used(directive: Any, n: int) -> None:
    """累加跨 audit revise 总轮数到 directive。

    directive 每章重生成,所以这个计数器天然是"每章独立"的。
    """
    if n <= 0:
        return
    try:
        cur = get_total_rounds_used(directive)
        directive._total_revise_rounds = cur + n
    except Exception:
        pass


def is_total_cap_exceeded(directive: Any) -> bool:
    """是否已达上限——其他 revise 路径(critic loop / 各类 audit)可调用此检查决定是否跳过。"""
    return get_total_rounds_used(directive) >= MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER


@dataclass
class ReviseResult:
    """循环结束后的状态摘要——调用方据此决定后续动作。"""
    final_text: str
    rounds_run: int = 0
    rounds_accepted: int = 0
    last_audit: Any = None
    residual_needs_revise: bool = False
    exit_reason: str = ""  # "clean" / "max_rounds" / "short_streak" / "no_initial_revise_needed" / "total_cap_exceeded"


def run_revise_loop(
    *,
    state: Any,
    chapter_index: int,
    directive: Any,
    config: ReviseConfig,
    initial_text: str,
    initial_audit: Any = None,
) -> ReviseResult:
    """跑一轮 audit→revise 循环。

    initial_audit 可选——如果调用方已经跑过初次 audit 就传入避免重跑。
    """
    text = initial_text
    audit = initial_audit

    # P0-2: 跨 audit 总上限保护——已达上限直接返回原稿,防 LLM 多 audit 叠加修平
    if is_total_cap_exceeded(directive):
        if audit is None:
            try:
                audit = config.audit_fn(state, chapter_index, text)
            except Exception:
                audit = None
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="info",
                source=f"chapter:{chapter_index}:revise_cap",
                message=(
                    f"{config.label} 跳过:本章 revise 总轮数已达上限 "
                    f"{MAX_TOTAL_REVISE_ROUNDS_PER_CHAPTER} (防个性稀释)"
                ),
            )
        except Exception:
            pass
        return ReviseResult(
            final_text=text, rounds_run=0, rounds_accepted=0,
            last_audit=audit, residual_needs_revise=False,
            exit_reason="total_cap_exceeded",
        )

    if audit is None:
        audit = config.audit_fn(state, chapter_index, text)

    if not config.needs_revise(audit):
        return ReviseResult(
            final_text=text, rounds_run=0, rounds_accepted=0,
            last_audit=audit, residual_needs_revise=False,
            exit_reason="no_initial_revise_needed",
        )

    rounds_run = 0
    rounds_accepted = 0
    short_streak = 0
    exit_reason = "max_rounds"

    for round_idx in range(1, config.max_rounds + 1):
        # P0-2: 每轮入口再检查一次总上限——同一 audit 内多轮也受限
        if is_total_cap_exceeded(directive):
            exit_reason = "total_cap_exceeded"
            break
        rounds_run = round_idx
        fb = config.feedback_builder(audit, round_idx)
        new_text = config.revise_fn(state, directive, text, fb)

        # 长度兜底
        new_len = len(new_text or "")
        original_len = len(text)
        if not (new_text and new_len >= int(config.min_length_ratio * original_len)):
            short_streak += 1
            if config.on_short:
                try:
                    config.on_short(round_idx, new_len, original_len, short_streak)
                except Exception:
                    pass
            if short_streak >= config.max_short_streak:
                exit_reason = "short_streak"
                break
            continue  # 丢这一轮、下一轮重试，不退出整套循环

        short_streak = 0
        rounds_accepted += 1

        # 接受新文本——写盘 + 字数同步
        if config.chapter_path:
            try:
                with open(config.chapter_path, "w", encoding="utf-8") as fp:
                    fp.write(new_text)
            except OSError as e:
                print(f"  ⚠ {config.label} 第 {round_idx} 轮写盘失败：{type(e).__name__}: {e}")
        if config.update_word_count:
            try:
                from persistence.state import count_chapter_words
                sm = next((c for c in state.completed_chapters if c.index == chapter_index), None)
                if sm:
                    sm.word_count = count_chapter_words(new_text)
            except Exception:
                pass

        old_audit = audit
        audit = config.audit_fn(state, chapter_index, new_text)
        text = new_text

        if config.on_round_done:
            try:
                config.on_round_done(round_idx, old_audit, audit, new_text)
            except Exception:
                pass

        if not config.needs_revise(audit):
            exit_reason = "clean"
            break

    residual_needs_revise = config.needs_revise(audit) if audit is not None else False
    if residual_needs_revise and config.on_residual_critical:
        try:
            config.on_residual_critical(audit)
        except Exception:
            pass

    # P0-2: 累加跨 audit 总轮数(只算接受的,丢弃过短的轮不算)
    add_total_rounds_used(directive, rounds_accepted)

    return ReviseResult(
        final_text=text, rounds_run=rounds_run, rounds_accepted=rounds_accepted,
        last_audit=audit, residual_needs_revise=residual_needs_revise,
        exit_reason=exit_reason,
    )
