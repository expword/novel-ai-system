"""
PhaseDraft helper —— 阶段产物的候选生成 / 选定 / 丢弃.

═══ 解决的问题 ═══

Stepwise 审核 modal 里,用户希望对某个 phase(如世界观/势力/卷结构)跑 N 次
产出 N 个候选,对比挑选,而不是接受单次结果。

本模块提供通用机制:不需要改每个 phase agent fn,而是:
  1. backup 该 phase 改动的 state 顶层字段(deepcopy)
  2. 跑 regen_fn N 次,每次跑前先 restore,跑完提取本次结果存为 PhaseDraft
  3. 最终 restore 一次,state 回到 generate 前的状态(候选与 state 解耦)
  4. apply_draft(phase_id, version_index) 把指定候选的 payload 写回 state +
     save+reload 一次确保字段被规范化(dict → dataclass)
  5. discard_drafts 清空候选

═══ 范围 ═══

PHASE_FIELDS_MAP 列出**支持 3 候选**的 phase + 它们的顶层字段。
不在此处的 phase(嵌套属性 / G3 人物组)调 generate_phase_drafts 会 raise ValueError。

═══ 限制 ═══

· 只支持顶层字段(state.X = ...);嵌套属性(state.power_system.realms[].*)不在此处
· G3 人物组完全不支持(用户明确"除人物外")
· apply 后写盘 + 重读,确保 dataclass 字段类型正确(state.json IO 一次)
"""
from __future__ import annotations
import copy
from datetime import datetime
from typing import Callable

from persistence.state import NovelState, PhaseDraft


# ═══ Phase → 它会修改的 state 顶层字段 ═══════════════════════════
# 列出来的 phase 才支持 3 候选;其他 phase 走单版本 regen 即可。
PHASE_FIELDS_MAP: dict[str, list[str]] = {
    "0":   ["concept_pitch", "trope_library", "tone_manual"],
    "0.5": ["master_outline"],
    # G2 世界(全部覆盖)
    "1A":  ["power_system"],
    "1B":  ["volumes", "book_structure"],
    "1C":  ["factions", "world_factions_desc"],
    "1D":  ["world_setting", "world_canon"],
    "1F":  ["geography"],
    "1G":  ["timeline"],
    "1H":  ["economy"],
    # G4 情节(主要 phase)
    "3A":  ["global_lines"],
    "3B":  ["volume_lines"],
    "3B2": ["conflict_ladder"],
    "3D2": ["emotion_curve"],
    "3E":  ["foreshadow_items"],
    "3E3": ["twist_system"],
    # 跳过(用户明确"除人物外"+ 嵌套写 / 追加式难处理):
    # 2 / 2A2 / 2B / 2C (人物组)
    # 1A2 / 2C2 (写 power_system.realms[].*)
    # 3C (satisfaction 追加式)
    # 3D / 3F / 3G (低优先级)
}


def is_supported(phase_id: str) -> bool:
    """该 phase 是否支持 3 候选生成."""
    return phase_id in PHASE_FIELDS_MAP


def _backup_fields(state: NovelState, fields: list[str]) -> dict:
    """deepcopy 字段值,用于 restore."""
    return {f: copy.deepcopy(getattr(state, f, None)) for f in fields}


def _restore_fields(state: NovelState, backup: dict) -> None:
    for k, v in backup.items():
        setattr(state, k, v)


def _to_jsonable(obj):
    """复用 checkpoint._to_json 递归序列化(dataclass → dict, Enum → value)."""
    from persistence.checkpoint import _to_json
    return _to_json(obj)


# ═══ 公开 API ════════════════════════════════════════════════

def generate_phase_drafts(state: NovelState, phase_id: str,
                            regen_fn: Callable[[], None],
                            count: int = 3,
                            notes_prefix: str = "",
                            user_feedback: str = "") -> list[PhaseDraft]:
    """跑 regen_fn count 次,每次产生一个候选;state 自动 restore 到调用前.

    regen_fn 是无参可调用对象,内部应当调到对应 phase 的 agent 函数,
    修改 PHASE_FIELDS_MAP[phase_id] 列出的字段.

    user_feedback: 非空时通过 thread-local 注入到 agent prompt
    (要求 agent 调 utils.feedback_helper.get_user_feedback_prefix())

    返回新生成的 PhaseDraft 列表(已 append 到 state.phase_drafts[phase_id]).
    """
    if not is_supported(phase_id):
        raise ValueError(f"phase_id={phase_id!r} 不支持 3 候选(不在 PHASE_FIELDS_MAP)")

    fields = PHASE_FIELDS_MAP[phase_id]
    backup = _backup_fields(state, fields)

    existing = state.phase_drafts.get(phase_id) or []
    start_index = (max((d.version_index for d in existing), default=0) + 1) if existing else 1

    # user_feedback 通过 thread-local 隐式传递到 agent;支持嵌套 scope
    from utils.feedback_helper import user_feedback_scope

    new_drafts: list[PhaseDraft] = []
    with user_feedback_scope(user_feedback):
        for i in range(count):
            # 1. restore 字段(确保每次从同一起点跑)
            _restore_fields(state, backup)
            # 2. 跑 regen(agent 内部会读 thread-local feedback)
            try:
                regen_fn()
            except Exception as e:
                print(f"  ⚠ generate_phase_drafts phase={phase_id} v{start_index + i} 失败:{type(e).__name__}: {e}")
                continue
            # 3. 提取本次结果
            payload = {f: _to_jsonable(getattr(state, f, None)) for f in fields}
            # 4. 构造 PhaseDraft
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            draft = PhaseDraft(
                phase_id=phase_id,
                version_index=start_index + i,
                payload=payload,
                created_at=ts,
                notes=notes_prefix or "",
            )
            new_drafts.append(draft)

    # 5. 最终 restore,state 回到调用前状态(候选与 state 解耦)
    _restore_fields(state, backup)

    # 6. 写入 state.phase_drafts
    state.phase_drafts.setdefault(phase_id, []).extend(new_drafts)
    return new_drafts


def list_drafts(state: NovelState, phase_id: str) -> list[PhaseDraft]:
    """返回该 phase 的候选列表(用于 API 序列化)."""
    return list(state.phase_drafts.get(phase_id) or [])


def apply_draft(state: NovelState, phase_id: str, version_index: int) -> bool:
    """选定某候选 — 把 payload 写回 state 顶层字段, 然后写盘 + 重读规范化 (dict → dataclass)。

    返回 True 成功 / False 候选未找到。

    注意:重读后 caller 持有的 state 引用对应字段会被替换(用 setattr 同步)。
    """
    drafts = state.phase_drafts.get(phase_id) or []
    target = next((d for d in drafts if d.version_index == version_index), None)
    if not target:
        return False

    payload = target.payload or {}
    # 1. 直接 setattr(此时字段可能是 dict / list[dict],dataclass 退化)
    for k, v in payload.items():
        setattr(state, k, v)

    # 2. 写盘 + 重读,让 checkpoint 的 _load_xxx 把 dict 转回 dataclass
    try:
        from persistence.checkpoint import save_state, load_state
        save_state(state)
        fresh = load_state()
        # 3. 把 fresh 的对应字段 copy 回 state(保持 caller 持有的 state 引用有效)
        if fresh is not None:
            for f in PHASE_FIELDS_MAP.get(phase_id, []):
                setattr(state, f, getattr(fresh, f, None))
    except Exception as e:
        # 兜底失败不致命(state 字段仍可读,只是 type 退化为 dict)——
        # 但写 progress_warning 让用户知道
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="warn",
                source=f"phase_draft:apply:{phase_id}",
                message=f"apply 后规范化失败 (state 字段可能退化为 dict): {type(e).__name__}: {e}",
            )
        except Exception:
            pass

    # 4. 选定后清空该 phase 的所有候选(选了就提交)
    state.phase_drafts[phase_id] = []
    return True


def discard_drafts(state: NovelState, phase_id: str) -> int:
    """清空某 phase 的候选(整组取消时调).返回清空条数."""
    drafts = state.phase_drafts.get(phase_id) or []
    n = len(drafts)
    state.phase_drafts[phase_id] = []
    return n
