"""system —— 配置驱动的通用系统引擎(系统流写作工作流的数值真相层)。

设计原则:
  · 数值真相脱离 LLM —— LLM 只产「提案」，引擎用代码裁决并记账
  · 完全题材无关 —— 引擎只认 SystemSpec，换一本书只换配置，工作流不动
  · 永远算得对 —— 提案-校验-执行 + 不变量 + 事件溯源 三重保证

  SystemSpec    一本书的系统配置(规划期生成)
  PlayerState   面板真相源
  Proposal      LLM 申请的本章变更
  SystemEngine  提案-校验-执行 闭环
  EventLog      事件溯源(重放/回滚/对账)
"""
from .spec import SystemSpec
from .player_state import PlayerState
from .proposal import Proposal, Change, OPS
from .engine import SystemEngine, ApplyResult
from .invariants import check_invariants
from .events import EventLog
from .extractor import extract_proposal

__all__ = [
    "SystemSpec", "PlayerState", "Proposal", "Change", "OPS",
    "SystemEngine", "ApplyResult", "check_invariants", "EventLog",
    "extract_proposal",
]
