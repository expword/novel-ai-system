"""不变量 —— 系统「永远必须成立」的硬约束。每次提案执行后自动跑。

任一被破坏 → 拦截 → 交给修订重写。这是「系统永远是对的」的底线保证。
"""
from __future__ import annotations

from typing import List

from .player_state import PlayerState
from .spec import SystemSpec


def check_invariants(spec: SystemSpec, st: PlayerState) -> List[str]:
    bad: List[str] = []

    # 1. 资源不为负(守恒)
    for k, v in st.resources.items():
        if v < 0:
            bad.append(f"资源为负: {k}={v}")
    for k, v in st.items.items():
        if v < 0:
            bad.append(f"物品为负: {k}={v}")

    # 2. 技能合法且不重复
    if len(st.skills) != len(set(st.skills)):
        bad.append("存在重复习得的技能")
    for sk in st.skills:
        if sk not in spec.skills_pool:
            bad.append(f"技能不在系统技能池: {sk}")

    # 3. 阶层在阶梯内
    if st.tier not in spec.tiers:
        bad.append(f"未知阶层: {st.tier}")

    # 4. 防通胀：当前阶层不得超过该章成长锚点
    anchor = spec.anchor_tier_at(st.chapter)
    if spec.tier_index(st.tier) > spec.tier_index(anchor):
        bad.append(f"阶层超前(通胀): 第{st.chapter}章为 {st.tier}，锚点上限 {anchor}")

    return bad
