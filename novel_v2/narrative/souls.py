"""灵魂角色 —— 秘密 = 一层反转，先扁平后丰满，绑真相层让反转带情感。

先用标签立印象、用创伤/牵挂让读者在乎，再在某层揭开秘密 —— 反转就有了情感暴击。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .truth_stack import TruthStack


@dataclass
class SoulCharacter:
    name: str
    tag: str                    # 开场扁平标签(先扁平立印象)
    surface: str                # 读者初见的印象
    wound: str = ""             # 创伤(让人在乎)
    longing: str = ""           # 牵挂 / 渴望
    secret: str = ""            # 秘密(=一层反转)
    secret_layer: int = -1      # 秘密属于真相栈哪层(-1=无秘密)
    bond_with_mc: str = ""      # 与主角的羁绊
    introduced_at: int = 0
    secret_revealed_at: int = 0


class SoulRegistry:
    def __init__(self):
        self.souls: Dict[str, SoulCharacter] = {}

    def add(self, s: SoulCharacter) -> None:
        self.souls[s.name] = s

    def revealed_at(self, chapter: int) -> List[SoulCharacter]:
        return [s for s in self.souls.values()
                if s.secret_revealed_at and s.secret_revealed_at <= chapter]

    def for_layer(self, layer: int) -> List[SoulCharacter]:
        return [s for s in self.souls.values() if s.secret_layer == layer]

    def issues(self, stack: TruthStack) -> List[str]:
        bad: List[str] = []
        for s in self.souls.values():
            if s.secret_layer >= 0 and s.secret_layer not in stack.levels():
                bad.append(f"角色{s.name}的秘密绑定第{s.secret_layer}层，但真相栈无此层")
            if s.secret_revealed_at and s.secret_layer >= 0:
                layer = stack.layer_at_level(s.secret_layer)
                if layer and s.secret_revealed_at != layer.reveal_at:
                    bad.append(f"角色{s.name}秘密揭晓({s.secret_revealed_at})"
                               f"与第{s.secret_layer}层揭晓({layer.reveal_at})不一致")
        return bad
