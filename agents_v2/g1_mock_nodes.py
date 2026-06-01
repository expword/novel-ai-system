"""G1 节点的 MOCK 版本——只验证 LangGraph 框架机制，不调 v1 agent / LLM。

每个 mock 节点：
  · 返回有结构的假 patch（带 _mocked: True 标记）
  · sleep 0.3 模拟耗时（让 checkpoint 真有时间落盘 + 给中断测试留时间窗）
  · 写 stdout 让用户看到流转
"""
from __future__ import annotations
import time

from state_v2 import NovelStateV2


def _mock_patch(state: NovelStateV2, phase_id: str, label: str, payload: dict) -> dict:
    """统一构造 patch：标 phase done + current 进度 + 业务 payload。"""
    if phase_id in state.phases_done:
        print(f"  ✓ [MOCK·跳过] {phase_id} 已完成")
        return {"current_phase": phase_id, "current_phase_label": f"{label}（已完成）"}
    print(f"  ▶ [MOCK·{phase_id}] {label} ...")
    time.sleep(0.3)
    patch = dict(payload)
    patch["phases_done"] = state.phases_done + [phase_id]
    patch["current_phase"] = phase_id
    patch["current_phase_label"] = f"{label} 完成（mock）"
    print(f"  ✓ [MOCK·{phase_id}] 完成，phases_done={patch['phases_done']}")
    return patch


def mock_phase_minus1(state: NovelStateV2) -> dict:
    return _mock_patch(state, "-1", "意图分析", {
        "creative_intent": {
            "_mocked": True,
            "raw_description": state.intent_description[:100],
            "analyzed": True,
            "themes": ["fake_theme_1", "fake_theme_2"],
            "protagonist_archetype_hint": "fake_archetype",
        },
    })


def mock_phase_0(state: NovelStateV2) -> dict:
    return _mock_patch(state, "0", "立项三件套", {
        "concept_pitch": {"_mocked": True, "hook": "fake_hook"},
        "trope_library": {"_mocked": True, "tropes": ["fake_trope"]},
        "tone_manual": {"_mocked": True, "tone": "fake_tone"},
    })


def mock_phase_0_5(state: NovelStateV2) -> dict:
    return _mock_patch(state, "0.5", "全书蓝图", {
        "master_outline": {
            "_mocked": True,
            "volumes_count": 6,
            "skeleton": "fake_skeleton",
        },
    })


def mock_phase_0_6(state: NovelStateV2) -> dict:
    return _mock_patch(state, "0.6", "主角内核", {
        "protagonist_journey": {
            "_mocked": True,
            "overall_theme": "fake_overall_theme",
            "fatal_flaw": "fake_fatal_flaw",
            "core_trauma": "fake_trauma",
            "real_goal": "fake_real_goal",
        },
    })
