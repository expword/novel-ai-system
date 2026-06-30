"""伏笔账本(绑真相层) —— 防「凭空反转」(揭晓前没埋) + 防「烂尾」(揭晓后没收)。

每条伏笔绑定它服务的真相层。某层揭晓时，引擎校验该层伏笔都已埋、且已回收。
这把三部作品最被称道的「首尾呼应、坑全填」变成硬约束。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .truth_stack import TruthStack


@dataclass
class Foreshadow:
    fid: str
    desc: str
    serves_layer: int      # 为哪一层反转埋的
    planted_at: int = 0    # 埋在哪章(0=未埋)
    paid_at: int = 0       # 回收在哪章(0=未收)


class ForeshadowLedger:
    def __init__(self):
        self.items: Dict[str, Foreshadow] = {}

    def add(self, f: Foreshadow) -> None:
        self.items[f.fid] = f

    def plant(self, fid: str, chapter: int) -> None:
        self.items[fid].planted_at = chapter

    def pay(self, fid: str, chapter: int) -> None:
        self.items[fid].paid_at = chapter

    def for_layer(self, layer: int) -> List[Foreshadow]:
        return [f for f in self.items.values() if f.serves_layer == layer]

    def open_debts(self) -> List[Foreshadow]:
        return [f for f in self.items.values() if not f.paid_at]

    def issues(self, stack: TruthStack, chapter: int) -> List[str]:
        bad: List[str] = []
        for layer in stack.revealed_at(chapter):
            if layer.level == 0:
                continue
            for f in self.for_layer(layer.level):
                if not f.planted_at or f.planted_at > layer.reveal_at:
                    bad.append(f"第{layer.level}层反转「{layer.name}」凭空："
                               f"伏笔{f.fid}({f.desc})在揭晓前(第{layer.reveal_at}章)未埋")
                if not f.paid_at:
                    bad.append(f"伏笔{f.fid}({f.desc})服务的第{layer.level}层已揭晓，"
                               f"却未回收(烂尾风险)")
        return bad
