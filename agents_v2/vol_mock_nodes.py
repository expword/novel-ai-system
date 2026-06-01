"""卷级 5 节点 MOCK 版本。每卷重跑一次（LangGraph cycle）。

  vol_stage     卷叙事舞台
  vol_beats     主角舞台节拍
  vol_outline   逐章大纲
  vol_ctp       章节类型
  vol_lifecycle lifecycle 节点落章（同时推进 current_volume_index → 下一卷）

phase id 命名：`4_<short>_<volume_index>`（与 v1 progress.json 对齐）。
"""
from __future__ import annotations
import time

from state_v2 import NovelStateV2


def _vol_patch(state: NovelStateV2, short: str, label: str,
                advance_volume: bool = False, extra: dict | None = None) -> dict:
    vi = state.current_volume_index
    phase_id = f"4_{short}_{vi}"
    if phase_id in state.phases_done:
        patch = {"current_phase": phase_id,
                 "current_phase_label": f"V{vi} {label}（已完成，跳过）"}
        if advance_volume:
            patch["current_volume_index"] = vi + 1
        return patch
    print(f"  ▶ [MOCK·V{vi}·{short}] {label} ...")
    time.sleep(0.05)
    patch = {
        "phases_done": state.phases_done + [phase_id],
        "current_phase": phase_id,
        "current_phase_label": f"V{vi} {label} 完成（mock）",
    }
    if advance_volume:
        patch["current_volume_index"] = vi + 1
    if extra:
        patch.update(extra)
    print(f"  ✓ [MOCK·V{vi}·{short}] 完成"
          + (f" → 推进 current_volume_index={vi+1}" if advance_volume else ""))
    return patch


def vol_stage(s):     return _vol_patch(s, "stage",     "卷叙事舞台")
def vol_beats(s):     return _vol_patch(s, "beats",     "主角舞台节拍")
def vol_outline(s):   return _vol_patch(s, "vol",       "逐章大纲")
def vol_ctp(s):       return _vol_patch(s, "ctp",       "章节类型")
def vol_lifecycle(s): return _vol_patch(s, "lifecycle", "lifecycle 落章",
                                          advance_volume=True)
