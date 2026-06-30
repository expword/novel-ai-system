"""可重释事实 —— 同一个细节，对每一层真相各有含义，实现「回头处处是伏笔」。

写正文呈现当前层的解释；揭晓深层时把旧事实重新解读(reframe)。
引擎强制:同一事实对各层不能矛盾(声明的层必须都在真相栈内)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .truth_stack import TruthStack


@dataclass
class ReframableFact:
    fid: str
    surface: str                                  # 表面呈现(读者初见的解读)
    meanings: Dict[int, str] = field(default_factory=dict)  # {真相层level: 该层下的真实含义}
    planted_at: int = 0                           # 这个细节出现在哪章

    def meaning_at(self, level: int) -> str:
        ks = [k for k in self.meanings if k <= level]
        return self.meanings[max(ks)] if ks else self.surface


class FactBook:
    def __init__(self):
        self.facts: Dict[str, ReframableFact] = {}

    def add(self, fact: ReframableFact) -> None:
        self.facts[fact.fid] = fact

    def reframed_at_level(self, level: int) -> List[ReframableFact]:
        return [f for f in self.facts.values() if level in f.meanings]

    def issues(self, stack: TruthStack) -> List[str]:
        bad: List[str] = []
        levels = stack.levels()
        for f in self.facts.values():
            for lv in f.meanings:
                if lv not in levels:
                    bad.append(f"事实{f.fid}声明了第{lv}层含义，但真相栈无此层")
        return bad
