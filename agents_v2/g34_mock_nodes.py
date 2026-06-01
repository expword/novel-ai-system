"""G3 + G4 共 17 节点的 MOCK 版本——只验证编排，不调 v1 agent。"""
from __future__ import annotations
import time

from state_v2 import NovelStateV2


def _patch(state: NovelStateV2, phase_id: str, label: str, payload: dict) -> dict:
    if phase_id in state.phases_done:
        print(f"  ✓ [MOCK·跳过] {phase_id} 已完成")
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成）"}
    print(f"  ▶ [MOCK·{phase_id}] {label} ...")
    time.sleep(0.1)
    out = dict(payload)
    out["phases_done"] = state.phases_done + [phase_id]
    out["current_phase"] = phase_id
    out["current_phase_label"] = f"{label} 完成（mock）"
    print(f"  ✓ [MOCK·{phase_id}] 完成")
    return out


# ── G3 人物组 ──
def mock_2(s):    return _patch(s, "2",   "人物档案",    {"characters": [{"_mocked": True, "name": "主角", "role": "主角"}]})
def mock_2A2(s):  return _patch(s, "2A2", "人物深化",    {"characters": [{"_mocked": True, "name": "主角", "depth": "refined"}]})
def mock_2B(s):   return _patch(s, "2B",  "关系网络",    {"relationship_web": {"_mocked": True, "bonds": []}})
def mock_2C(s):   return _patch(s, "2C",  "特殊能力",    {
    "power_system": {**s.power_system, "_mocked_2C": True, "special_abilities": [{"name": "豆包"}]} if isinstance(s.power_system, dict) else {"_mocked_2C": True},
    "characters": s.characters or [],
})
def mock_2D(s):   return _patch(s, "2D",  "心理弧光",    {"character_arcs": [{"_mocked": True, "character_name": "主角"}]})
def mock_2C2(s):  return _patch(s, "2C2", "能力路线图",  {
    "power_system": {**s.power_system, "_mocked_2C2": True} if isinstance(s.power_system, dict) else {"_mocked_2C2": True},
    "satisfaction_points": [{"_mocked": True, "sp_id": "sp_asset_豆包_0"}],
    "character_arcs": s.character_arcs or [],
})

# ── G4 情节组 ──
def mock_3A(s):   return _patch(s, "3A",  "全局叙事线",  {"global_lines": [{"_mocked": True, "line_id": "L_main"}]})
def mock_3B(s):   return _patch(s, "3B",  "卷内叙事线",  {"volume_lines": [{"_mocked": True, "volume": 1}]})
def mock_3B2(s):  return _patch(s, "3B2", "冲突阶梯",    {"conflict_ladder": {"_mocked": True, "entries": []}})
def mock_3C(s):   return _patch(s, "3C",  "爽点系统",    {
    # 3C 在 2C2 的爽点基础上追加
    "satisfaction_points": (s.satisfaction_points or []) + [{"_mocked": True, "sp_id": "sp_main_1"}],
})
def mock_3D(s):   return _patch(s, "3D",  "节奏",        {"rhythm_plans": [{"_mocked": True, "volume": 1}]})
def mock_3D2(s):  return _patch(s, "3D2", "情绪曲线",    {"emotion_curve": {"_mocked": True, "notes": []}})
def mock_3E3(s):  return _patch(s, "3E3", "反转系统",    {"twist_system": {"_mocked": True, "chains": []}})
def mock_3E(s):   return _patch(s, "3E",  "伏笔",        {"foreshadow_items": [{"_mocked": True, "fw_id": "fw_1"}]})
def mock_3E2(s):  return _patch(s, "3E2", "红鲱鱼",      {"red_herrings": [{"_mocked": True, "rh_id": "rh_1"}]})
def mock_3F(s):   return _patch(s, "3F",  "机缘",        {"fortunes": [{"_mocked": True, "ft_id": "ft_1"}]})
def mock_3G(s):   return _patch(s, "3G",  "主角历程",    {
    "protagonist_journey": {**s.protagonist_journey, "_mocked_3G": True, "milestones": [{"volume": 1}]} if isinstance(s.protagonist_journey, dict) else {"_mocked_3G": True, "milestones": []},
})
