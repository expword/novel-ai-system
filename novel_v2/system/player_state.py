"""PlayerState —— 系统面板的单一真相源(结构化，正文是它的渲染)。

要知道主角现在多强 → 查它，绝不问 LLM。字段由 SystemSpec 驱动，本身通用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .spec import SystemSpec


@dataclass
class PlayerState:
    chapter: int = 0
    tier: str = ""                                   # 当前阶层
    resources: Dict[str, int] = field(default_factory=dict)   # {"信誉":0,"灵石":0}
    skills: List[str] = field(default_factory=list)           # 已习得
    items: Dict[str, int] = field(default_factory=dict)       # {"物品":数量}
    quests_done: List[str] = field(default_factory=list)
    cooldowns: Dict[str, int] = field(default_factory=dict)   # 限频项 -> 下次可用章
    lore_progress: int = 0                                     # 系统来历揭晓进度

    @classmethod
    def initial(cls, spec: SystemSpec) -> "PlayerState":
        return cls(chapter=0, tier=spec.tiers[0],
                   resources={r: 0 for r in spec.resources})

    def render(self) -> str:
        """渲染成可注入 prompt 的面板文本(让 LLM 在真相约束下写)。"""
        res = "，".join(f"{k}:{v}" for k, v in self.resources.items())
        return (f"【面板】阶层:{self.tier} | {res} | "
                f"技能:{('、'.join(self.skills)) or '无'} | "
                f"物品:{('、'.join(f'{k}x{v}' for k, v in self.items.items())) or '无'}")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "chapter", "tier", "resources", "skills", "items",
            "quests_done", "cooldowns", "lore_progress")}

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerState":
        keys = ("chapter", "tier", "resources", "skills", "items",
                "quests_done", "cooldowns", "lore_progress")
        return cls(**{k: d[k] for k in keys if k in d})
