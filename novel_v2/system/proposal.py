"""Proposal —— LLM 从正文抽出的「本章系统变更」申请(意图，非真相)。

extractor 产出它，SystemEngine 校验并执行。LLM 永远只能「申请」，引擎是唯一裁判。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# 合法操作码(通用)：
#   gain_resource / spend_resource   资源增减
#   learn_skill                      习得技能(须在 spec.skills_pool)
#   gain_item / use_item             物品增减
#   advance_tier                     晋升阶层(须达 progress_resource 阈值且不超锚点)
#   complete_quest                   完成委托/任务(单元闭环)
#   reward                           系统发奖励(须在当前阶层奖励池)
#   lore_reveal                      推进系统来历揭晓
OPS = {"gain_resource", "spend_resource", "learn_skill", "gain_item",
       "use_item", "advance_tier", "complete_quest", "reward", "lore_reveal"}


@dataclass
class Change:
    op: str
    key: str = ""          # 资源名/技能名/物品名/奖励名/quest id
    amount: int = 1


@dataclass
class Proposal:
    chapter: int
    changes: List[Change] = field(default_factory=list)
    used_skills: List[str] = field(default_factory=list)   # 正文用到的技能(双向校验)
    used_items: List[str] = field(default_factory=list)    # 正文用到的物品(双向校验)
    cooldown_uses: List[str] = field(default_factory=list) # 本章触发的限频项

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        changes = [Change(c["op"], c.get("key", ""), int(c.get("amount", 1)))
                   for c in d.get("changes", [])]
        return cls(chapter=int(d["chapter"]), changes=changes,
                   used_skills=d.get("used_skills", []),
                   used_items=d.get("used_items", []),
                   cooldown_uses=d.get("cooldown_uses", []))
