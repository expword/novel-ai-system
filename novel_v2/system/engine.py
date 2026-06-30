"""SystemEngine —— 提案-校验-执行 闭环(系统真相的唯一裁判)。

事务语义：在副本上试算所有变更 → 跑合规校验 + 不变量 → 全过才提交。
任一不过 → 原状态不变，返回违规原因 → 交给修订重写。
引擎只认 SystemSpec，与任何具体题材解耦。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional

from .invariants import check_invariants
from .player_state import PlayerState
from .proposal import Change, Proposal
from .spec import SystemSpec


@dataclass
class ApplyResult:
    ok: bool
    state: PlayerState
    events: List[dict] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)


class SystemEngine:
    def __init__(self, spec: SystemSpec):
        self.spec = spec

    def apply(self, state: PlayerState, proposal: Proposal) -> ApplyResult:
        new = copy.deepcopy(state)
        new.chapter = proposal.chapter
        events: List[dict] = []
        violations: List[str] = []

        # --- 双向校验：正文用到的技能/物品必须已拥有(防正文偷改系统) ---
        for sk in proposal.used_skills:
            if sk not in state.skills:
                violations.append(f"正文使用了未习得的技能: {sk}")
        for it in proposal.used_items:
            if state.items.get(it, 0) <= 0:
                violations.append(f"正文使用了未持有的物品: {it}")

        # --- 限频校验 ---
        for cd in proposal.cooldown_uses:
            nxt = state.cooldowns.get(cd, 0)
            if proposal.chapter < nxt:
                violations.append(f"限频项「{cd}」冷却中(下次可用第 {nxt} 章)")

        # --- 逐条试算变更 ---
        for ch in proposal.changes:
            err = self._apply_change(new, ch, events)
            if err:
                violations.append(err)

        # --- 不变量 ---
        violations += check_invariants(self.spec, new)

        if violations:
            return ApplyResult(False, state, [], violations)

        # 提交：更新限频冷却
        for cd in proposal.cooldown_uses:
            new.cooldowns[cd] = proposal.chapter + self.spec.cooldowns.get(cd, 1)
        return ApplyResult(True, new, events, [])

    # 直接应用已提交的变更(供事件重放，跳过校验) ----
    def replay_change(self, state: PlayerState, ch: Change) -> None:
        self._apply_change(state, ch, [])

    def _apply_change(self, st: PlayerState, ch: Change, events: List[dict]) -> Optional[str]:
        op, key, amt = ch.op, ch.key, ch.amount
        if op == "gain_resource":
            st.resources[key] = st.resources.get(key, 0) + amt
        elif op == "spend_resource":
            have = st.resources.get(key, 0)
            if have < amt:
                return f"资源不足: {key} 需 {amt} 仅有 {have}"
            st.resources[key] = have - amt
        elif op == "learn_skill":
            if key not in self.spec.skills_pool:
                return f"技能不在系统技能池: {key}"
            if key in st.skills:
                return f"重复习得技能: {key}"
            st.skills.append(key)
        elif op == "gain_item":
            st.items[key] = st.items.get(key, 0) + amt
        elif op == "use_item":
            have = st.items.get(key, 0)
            if have < amt:
                return f"物品不足: {key} 需 {amt} 仅有 {have}"
            st.items[key] = have - amt
        elif op == "reward":
            if key not in self.spec.allowed_rewards(st.tier):
                return f"奖励「{key}」不在当前阶层「{st.tier}」的奖励池(超发)"
            st.items[key] = st.items.get(key, 0) + amt
        elif op == "advance_tier":
            idx = self.spec.tier_index(st.tier)
            if idx + 1 >= len(self.spec.tiers):
                return "已是最高阶层，无法晋升"
            nxt = self.spec.tiers[idx + 1]
            need = self.spec.tier_advance.get(nxt, 0)
            if st.resources.get(self.spec.progress_resource, 0) < need:
                return f"晋升「{nxt}」需 {self.spec.progress_resource} ≥ {need}"
            st.tier = nxt
        elif op == "complete_quest":
            st.quests_done.append(key)
        elif op == "lore_reveal":
            st.lore_progress += amt
        else:
            return f"未知操作码: {op}"
        events.append({"chapter": st.chapter, "op": op, "key": key, "amount": amt})
        return None
