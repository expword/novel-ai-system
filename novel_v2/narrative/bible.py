"""NarrativeBible —— 叙事真相引擎:统合 真相栈/可重释事实/伏笔/灵魂角色，做全量审计。

把「反转的反转的反转」「处处是伏笔」「不烂尾」「角色秘密」变成可校验的硬约束。
分工:真相栈怎么设计(创意)靠人审 + 规划期 LLM;一致性(不崩/有铺垫/坑全填)靠这里兜。
"""
from __future__ import annotations

from typing import List

from .facts import FactBook
from .foreshadow import ForeshadowLedger
from .souls import SoulRegistry
from .truth_stack import TruthStack

_BIG = 10 ** 9


class NarrativeBible:
    def __init__(self, truth: TruthStack, facts: FactBook = None,
                 foreshadow: ForeshadowLedger = None, souls: SoulRegistry = None):
        self.truth = truth
        self.facts = facts or FactBook()
        self.foreshadow = foreshadow or ForeshadowLedger()
        self.souls = souls or SoulRegistry()

    # 写作期:截至某章，整套叙事自不自洽
    def audit(self, chapter: int) -> List[str]:
        issues: List[str] = []
        issues += self.truth.structure_issues()
        issues += self.facts.issues(self.truth)
        issues += self.foreshadow.issues(self.truth, chapter)
        issues += self.souls.issues(self.truth)
        return issues

    # 规划期:假设全书写完，检查整套设计自洽(每层反转有铺垫、伏笔都会回收、角色秘密对齐)
    def plan_design_audit(self) -> List[str]:
        return self.audit(_BIG)

    # 写作期:本章该揭哪层真相、回收哪些伏笔、揭哪个角色秘密、reframe 哪些事实 → 喂给写作
    def reveal_plan_at(self, chapter: int) -> dict:
        plan = {"reveal_layers": [], "pay_foreshadows": [],
                "reveal_souls": [], "reframe_facts": []}
        for layer in [x for x in self.truth.layers if x.reveal_at == chapter]:
            plan["reveal_layers"].append(
                {"level": layer.level, "name": layer.name, "statement": layer.statement})
            plan["pay_foreshadows"] += [f.fid for f in self.foreshadow.for_layer(layer.level)]
            plan["reveal_souls"] += [s.name for s in self.souls.for_layer(layer.level)]
            plan["reframe_facts"] += [f.fid for f in self.facts.reframed_at_level(layer.level)]
        return plan
