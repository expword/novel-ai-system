"""SystemSpec —— 描述「这本书的系统长什么样」的通用配置。

规划期由 LLM 生成(针对不同题材生成不同字段)，写作期由 SystemEngine 读取执行。
引擎只认 SystemSpec，不认识任何具体题材——换一本书只换这份配置，工作流不动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SystemSpec:
    name: str                                   # 系统名(仅展示)，如「委托系统」
    resources: List[str]                        # 可累积资源名，如 ["信誉", "灵石"]
    progress_resource: str                      # 决定晋升阶层的主资源
    tiers: List[str]                            # 阶层阶梯(低→高)
    tier_advance: Dict[str, int]               # 进入某阶层所需 progress_resource 阈值
    skills_pool: List[str]                     # 可习得技能全集
    reward_pool: Dict[str, List[str]]          # 阶层 -> 该阶层「新解锁」的奖励名
    growth_anchors: Dict[int, str]             # 章号 -> 该章「最高允许」阶层(防通胀)
    cooldowns: Dict[str, int] = field(default_factory=dict)   # 限频项 -> 周期(章)
    lore_hook: str = ""                        # 系统来历主悬念(唯一长线)

    # ---- 派生查询 ----
    def tier_index(self, tier: str) -> int:
        return self.tiers.index(tier) if tier in self.tiers else -1

    def allowed_rewards(self, tier: str) -> set:
        """当前阶层可发的奖励 = 本阶层及以下所有阶层的奖励池之并。"""
        idx = self.tier_index(tier)
        out = set()
        for t in self.tiers[: idx + 1]:
            out.update(self.reward_pool.get(t, []))
        return out

    def anchor_tier_at(self, chapter: int) -> str:
        """该章允许达到的最高阶层(取 <=chapter 的最大锚点)。无锚点则放开到最高。"""
        applicable = [c for c in self.growth_anchors if c <= chapter]
        if not applicable:
            return self.tiers[-1]
        return self.growth_anchors[max(applicable)]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "name", "resources", "progress_resource", "tiers", "tier_advance",
            "skills_pool", "reward_pool", "growth_anchors", "cooldowns", "lore_hook")}

    @classmethod
    def from_dict(cls, d: dict) -> "SystemSpec":
        # JSON 的 dict key 是字符串，growth_anchors 章号转回 int
        anchors = {int(k): v for k, v in d.get("growth_anchors", {}).items()}
        return cls(
            name=d["name"], resources=d["resources"],
            progress_resource=d["progress_resource"], tiers=d["tiers"],
            tier_advance=d.get("tier_advance", {}), skills_pool=d.get("skills_pool", []),
            reward_pool=d.get("reward_pool", {}), growth_anchors=anchors,
            cooldowns=d.get("cooldowns", {}), lore_hook=d.get("lore_hook", ""))
