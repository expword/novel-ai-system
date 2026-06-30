"""NovelState —— LangGraph 风格的中心化状态(单一 SSoT)。

采用 TypedDict + reducer：节点不 mutate state，只 **返回增量 dict**，由 reducer 合并。
好处：
  · 并行节点写同一字段不会互相覆盖(reducer 决定如何合并)
  · 每个超步的 state 可被 Checkpointer 整体快照 → 断点续跑
  · 节点之间解耦，谁也不用知道下一个节点是谁(黑板模式)
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def keep_last(_old: Any, new: Any) -> Any:
    """标量 reducer：后写覆盖前写(用于 draft / 当前章号等单值字段)。"""
    return new


class ChapterSummary(TypedDict, total=False):
    """已完成章节的摘要 —— RAG 长期记忆的最小单元。"""
    index: int          # 全书章号
    volume_index: int   # 所属卷
    title: str
    summary: str        # 供「剧情走向」语义召回
    key_events: list[str]
    characters: list[str]
    word_count: int


class CanonIssue(TypedDict, total=False):
    level: str          # "critical" | "major" | "minor"
    code: str
    message: str


class NovelState(TypedDict, total=False):
    # —— 静态元信息 ——
    project_id: str
    title: str
    genre: str
    premise: str                # 小说设定/一句话梗概(写正文的总纲)
    output_dir: str             # 正文落盘目录(空则不落盘)
    volume_size: int            # 每卷多少章(用于触发卷级 HITL 关卡)
    target_chapters: int        # 本次要写到第几章
    debug_inject_flaw: bool      # 仅 mock 演示:第2章故意注入违规以触发自愈环

    # —— 写作游标 ——
    current_chapter_index: int
    revise_round: int

    # —— 单章流水态(每章覆盖) ——
    directive: dict             # 本章指令
    retrieved_context: str      # RAG 召回拼装的前情上下文
    draft: str                  # 当前章正文
    canon_issues: list[CanonIssue]
    critic_report: dict

    # —— 长期累积态(reducer 追加) ——
    completed_chapters: Annotated[list[ChapterSummary], operator.add]
    event_log: Annotated[list[str], operator.add]   # 可观测：每步发生了什么

    # —— HITL ——
    awaiting_human: bool
    human_decision: str         # "approve" | "revise" | ...

    # —— 系统流(可选；空则不启用系统引擎，退化为普通续写) ——
    system_spec: dict                 # SystemSpec.to_dict()
    player_state: dict                # PlayerState.to_dict() 面板真相
    unit_plan: dict                   # {章号str: settle} 单元规划
    system_violations: list           # 本章引擎违规
    pending_player_state: dict        # system_node 试算结果，finalize 提交
    pending_events: list
    system_events: Annotated[list, operator.add]   # 系统事件日志(累积)


def initial_state(
    *,
    project_id: str = "demo",
    title: str = "未命名",
    genre: str = "xianxia",
    premise: str = "",
    output_dir: str = "",
    volume_size: int = 3,
    target_chapters: int = 5,
    debug_inject_flaw: bool = False,
    system_spec: dict = None,
    player_state: dict = None,
    unit_plan: dict = None,
) -> NovelState:
    """构造一个最小可运行的初始 state。"""
    return NovelState(
        project_id=project_id,
        title=title,
        genre=genre,
        premise=premise,
        output_dir=output_dir,
        volume_size=volume_size,
        target_chapters=target_chapters,
        debug_inject_flaw=debug_inject_flaw,
        current_chapter_index=1,
        revise_round=0,
        directive={},
        retrieved_context="",
        draft="",
        canon_issues=[],
        critic_report={},
        completed_chapters=[],
        event_log=[],
        awaiting_human=False,
        human_decision="",
        system_spec=system_spec or {},
        player_state=player_state or {},
        unit_plan=unit_plan or {},
        system_violations=[],
        pending_player_state={},
        pending_events=[],
        system_events=[],
    )
