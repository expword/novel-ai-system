"""Canon-KG —— 业务约束型设定知识图谱(KG-RAG 的 KG 侧)。

把跨章需强一致的设定沉淀为结构化、可查询的「确定性证据」：
  · 实体(角色/势力/物品/地点)及其属性
  · 实体间关系(师父/敌对/隶属…)
  · 伏笔状态(已埋/已收 + 触发条件)

检索时：匹配 query 里出现的实体名 → 吐出该实体的设定 + 关系 + 相关未回收伏笔，
作为「向量语义」之外的确定性一路，互补支撑多跳一致性。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


@dataclass
class Entity:
    name: str
    etype: str = "角色"          # 角色 / 势力 / 物品 / 地点
    attrs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Foreshadow:
    fid: str
    desc: str
    status: str = "planted"      # planted | resolved
    trigger: str = ""


class CanonKG:
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.relations: List[tuple] = []          # (a, rel, b)
        self.foreshadows: Dict[str, Foreshadow] = {}

    # ---- 构建 ----
    def add_entity(self, name: str, etype: str = "角色", **attrs) -> None:
        self.entities[name] = Entity(name, etype, dict(attrs))

    def add_relation(self, a: str, rel: str, b: str) -> None:
        self.relations.append((a, rel, b))

    def add_foreshadow(self, fid: str, desc: str, trigger: str = "") -> None:
        self.foreshadows[fid] = Foreshadow(fid, desc, "planted", trigger)

    def resolve_foreshadow(self, fid: str) -> None:
        if fid in self.foreshadows:
            self.foreshadows[fid].status = "resolved"

    # ---- 查询：返回确定性证据 ----
    def query(self, text: str, max_items: int = 5) -> List[str]:
        toks = set(_TOKEN_RE.findall(text))
        evidence: List[str] = []
        for name, ent in self.entities.items():
            # 实体名出现在 query 中(整名或单字命中)即视为相关
            if name in text or (set(_TOKEN_RE.findall(name)) & toks):
                parts = [f"{ent.name}({ent.etype})"]
                if ent.attrs:
                    parts.append("，".join(f"{k}:{v}" for k, v in ent.attrs.items()))
                rels = [f"{a}{rel}{b}" for (a, rel, b) in self.relations
                        if a == name or b == name]
                if rels:
                    parts.append("关系: " + "、".join(rels[:4]))
                evidence.append("【设定】" + "；".join(parts))
            if len(evidence) >= max_items:
                break
        # 附带未回收伏笔(提醒一致性)
        open_fs = [f for f in self.foreshadows.values() if f.status == "planted"]
        for f in open_fs[: max(0, max_items - len(evidence))]:
            evidence.append(f"【未回收伏笔】{f.fid}: {f.desc}"
                            + (f"(触发: {f.trigger})" if f.trigger else ""))
        return evidence

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return {
            "entities": {n: {"etype": e.etype, "attrs": e.attrs} for n, e in self.entities.items()},
            "relations": self.relations,
            "foreshadows": {k: vars(v) for k, v in self.foreshadows.items()},
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "CanonKG":
        kg = cls()
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        for n, e in d.get("entities", {}).items():
            kg.add_entity(n, e.get("etype", "角色"), **e.get("attrs", {}))
        kg.relations = [tuple(r) for r in d.get("relations", [])]
        for k, v in d.get("foreshadows", {}).items():
            kg.foreshadows[k] = Foreshadow(**v)
        return kg
