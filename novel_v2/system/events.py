"""EventLog —— 事件溯源:记每章已提交的变更，支持重放、按章回滚、快照。

状态不「覆盖式」修改:当前 state = 初始 state + 重放所有已提交变更。
好处:任何数值可追溯到「哪章给的」；修订某章可精确撤销该章变更，不污染后续。
"""
from __future__ import annotations

from typing import List

from .engine import SystemEngine
from .player_state import PlayerState
from .proposal import Change
from .spec import SystemSpec


class EventLog:
    def __init__(self):
        self.events: List[dict] = []     # 每条 {chapter, op, key, amount}

    def commit(self, events: List[dict]) -> None:
        self.events.extend(events)

    def rollback_after(self, chapter: int) -> int:
        """撤销第 chapter 章之后的所有变更(修订/回卷用)。返回撤销条数。"""
        keep = [e for e in self.events if e["chapter"] <= chapter]
        n = len(self.events) - len(keep)
        self.events = keep
        return n

    def snapshot(self) -> List[dict]:
        return list(self.events)

    def replay(self, spec: SystemSpec, up_to: int = None) -> PlayerState:
        """从初始状态重放到 up_to 章(含)，重建面板——用于回滚后恢复 / 全量对账。"""
        engine = SystemEngine(spec)
        st = PlayerState.initial(spec)
        for e in self.events:
            if up_to is not None and e["chapter"] > up_to:
                continue
            st.chapter = e["chapter"]
            engine.replay_change(st, Change(e["op"], e["key"], e["amount"]))
        return st
