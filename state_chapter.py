"""章级子图的 state schema（独立于主 NovelStateV2，subgraph 模式）。

为什么独立 state：
  · 章级写作有大量临时字段（draft / review_score / revision_round / issues...）
    污染主 state 不合适——主图只关心"第 N 章写完了"，章内审改循环细节不必持久化进主图
  · LangGraph 子图支持自己的 state schema；子图跑完只把"最终产物"汇总到主图

Cycle 字段（critic 审校循环用）：
  · revision_round：当前是第几轮 critic（0 = 还没审过）
  · max_rounds：最大轮数硬上限（对应 v1 MAX_REVISION_ROUNDS）
  · review_passed：本轮 critic 是否通过
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class ChapterState(BaseModel):
    # ── 输入：写本章前的上下文 ──
    chapter_index: int = 0           # 全书第几章
    volume_index: int = 1            # 第几卷
    chapter_directive: dict = Field(default_factory=dict)  # 张力/伏笔/爽点等 directive
    outline_goal: str = ""           # 本章 outline goal
    word_quota: int = 3000

    # ── 写作产物 ──
    draft: str = ""                  # 当前正文（每轮 revise 后覆盖）
    word_count: int = 0

    # ── critic 审校循环状态（LangGraph cycle 核心）──
    revision_round: int = 0          # 当前轮次（0 = 还没审过）
    max_rounds: int = 3              # 硬上限——防止无限循环
    review_score: int = 0            # 上轮 critic 打分
    review_passed: bool = False      # 上轮是否通过
    review_issues: list[str] = Field(default_factory=list)
    review_feedback: str = ""        # 给下轮 revise 的反馈

    # ── 章后审计循环状态（后续阶段扩展用）──
    setup_review_round: int = 0
    setup_critical_count: int = 0

    # ── 最终产物 + 元数据 ──
    finalized: bool = False
    finalize_reason: str = ""        # "passed" / "max_rounds_exceeded" / "early_break"
