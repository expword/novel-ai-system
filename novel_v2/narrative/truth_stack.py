"""真相栈 —— 多层真相(终点先定)，「反转的反转的反转」的骨架。

表象 L0 → L1 → … → Ln(最终真相，先定死)。每层是对上一层的颠覆，标注何时揭晓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TruthLayer:
    level: int           # 0=表象(读者初始认知)，1..n 逐层深入
    name: str
    statement: str       # 这一层的真相是什么
    reveal_at: int       # 第几章揭晓(0=表象，从头即读者认知)
    overturns: str = ""  # 它推翻了上一层的什么


@dataclass
class TruthStack:
    layers: List[TruthLayer] = field(default_factory=list)

    def sorted_layers(self) -> List[TruthLayer]:
        return sorted(self.layers, key=lambda x: x.level)

    def levels(self) -> set:
        return {x.level for x in self.layers}

    def final(self) -> Optional[TruthLayer]:
        ls = self.sorted_layers()
        return ls[-1] if ls else None

    def layer_at_level(self, level: int) -> Optional[TruthLayer]:
        for x in self.layers:
            if x.level == level:
                return x
        return None

    def revealed_at(self, chapter: int) -> List[TruthLayer]:
        return [x for x in self.sorted_layers() if x.reveal_at <= chapter]

    def reader_layer_at(self, chapter: int) -> int:
        return max((x.level for x in self.revealed_at(chapter)), default=0)

    def structure_issues(self) -> List[str]:
        bad: List[str] = []
        if len(self.layers) < 2:
            bad.append("真相栈至少 2 层(表象+1层反转)，否则没有反转")
        lv = sorted(self.levels())
        if lv and lv != list(range(len(lv))):
            bad.append(f"真相层级不连续: {lv}(应从 0 连续)")
        ls = self.sorted_layers()
        for a, b in zip(ls, ls[1:]):
            if b.reveal_at < a.reveal_at:
                bad.append(f"第{b.level}层揭晓({b.reveal_at})早于第{a.level}层({a.reveal_at})")
        return bad
