"""narrative —— 叙事真相引擎(让小说「有灵魂」的那一层)。

它管理的不是数值，而是让读者「在乎、欲罢不能」的东西:
  TruthStack       多层真相(终点先定)，反转的反转的反转的骨架
  ReframableFact   可重释事实，回头处处是伏笔
  ForeshadowLedger 伏笔绑层，防凭空反转、防烂尾
  SoulCharacter    灵魂角色，秘密=反转层，让反转带情感
  NarrativeBible   统合 + 全量审计(创意靠人，一致性靠引擎兜底)
"""
from .truth_stack import TruthStack, TruthLayer
from .facts import FactBook, ReframableFact
from .foreshadow import ForeshadowLedger, Foreshadow
from .souls import SoulRegistry, SoulCharacter
from .bible import NarrativeBible

__all__ = [
    "TruthStack", "TruthLayer",
    "FactBook", "ReframableFact",
    "ForeshadowLedger", "Foreshadow",
    "SoulRegistry", "SoulCharacter",
    "NarrativeBible",
]
