"""从「单元结算 + 正文」抽出系统变更提案(Proposal)。

数值不靠 LLM:本章该结算什么由 directive['system_settle'](来自单元规划)确定;
extractor 只做两件事 ——
  ① 把 settle 转成结构化 changes;
  ② 从正文扫「用到的已有技能/物品」供引擎做双向校验(防正文偷用系统)。
"""
from __future__ import annotations

from typing import List

from .proposal import Change, Proposal


def extract_proposal(chapter: int, settle: dict, draft: str,
                     known_skills: List[str], known_items: List[str]) -> Proposal:
    settle = settle or {}
    changes: List[Change] = []
    for k, v in (settle.get("gain") or {}).items():
        changes.append(Change("gain_resource", k, int(v)))
    for k, v in (settle.get("spend") or {}).items():
        changes.append(Change("spend_resource", k, int(v)))
    for sk in (settle.get("learn") or []):
        changes.append(Change("learn_skill", sk))
    for rw in (settle.get("reward") or []):
        changes.append(Change("reward", rw, 1))
    for it in (settle.get("gain_item") or []):
        changes.append(Change("gain_item", it, 1))
    if settle.get("quest"):
        changes.append(Change("complete_quest", settle["quest"]))
    if settle.get("advance"):
        changes.append(Change("advance_tier"))
    if settle.get("lore"):
        changes.append(Change("lore_reveal", "", int(settle["lore"])))

    used_skills = [sk for sk in known_skills if sk and sk in (draft or "")]
    used_items = [it for it in known_items if it and it in (draft or "")]
    return Proposal(chapter=chapter, changes=changes,
                    used_skills=used_skills, used_items=used_items,
                    cooldown_uses=(settle.get("cooldown") or []))
