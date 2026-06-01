"""G2 节点 MOCK 版本——只验证 LangGraph 编排，不调 v1 agent。"""
from __future__ import annotations
import time

from state_v2 import NovelStateV2


def _patch(state: NovelStateV2, phase_id: str, label: str, payload: dict) -> dict:
    if phase_id in state.phases_done:
        print(f"  ✓ [MOCK·跳过] {phase_id} 已完成")
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成）"}
    print(f"  ▶ [MOCK·{phase_id}] {label} ...")
    time.sleep(0.2)
    out = dict(payload)
    out["phases_done"] = state.phases_done + [phase_id]
    out["current_phase"] = phase_id
    out["current_phase_label"] = f"{label} 完成（mock）"
    print(f"  ✓ [MOCK·{phase_id}] 完成")
    return out


def mock_1A(s):  return _patch(s, "1A",  "力量体系",       {"power_system": {"_mocked": True, "system_type": "fake_realms"}})
def mock_1A2(s): return _patch(s, "1A2", "力量刻度",       {"power_system": {"_mocked": True, "system_type": "fake_realms", "scaling": "fake_scaling"}})
def mock_1B(s):  return _patch(s, "1B",  "卷结构",         {"volumes": [{"_mocked": True, "index": i+1, "title": f"卷{i+1}"} for i in range(6)]})
def mock_1C(s):  return _patch(s, "1C",  "势力架构",       {"factions": [{"_mocked": True, "name": "fake_faction_1"}]})
def mock_1D(s):  return _patch(s, "1D",  "世界观",         {"world_setting": {"_mocked": True, "intro": "fake world"}})
def mock_1E(s):  return _patch(s, "1E",  "世界观校验",     {"world_checklist_gaps": []})
def mock_1F(s):  return _patch(s, "1F",  "地理",           {"geography": {"_mocked": True, "regions": []}})
def mock_1G(s):  return _patch(s, "1G",  "时间线",         {"timeline": {"_mocked": True, "events": []}})
def mock_1H(s):  return _patch(s, "1H",  "经济",           {"economy": {"_mocked": True, "currencies": []}})
