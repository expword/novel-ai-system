"""
Agents 包入口。

提供 require_upstream(state, agent_name, **specs) 守卫——
每个下游 agent 在跑 LLM 之前调用一次。返回 True = 上游齐备可继续；
返回 False = 缺上游，已写 progress warning，agent 应直接 return（不要继续跑 LLM）。

惯用法（每个下游 agent 入口）：
    if not require_upstream(state, "TwistDesigner",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters)):
        return

替代以前"上游空时 agent 把空字符串塞进 prompt → LLM 凭空编造"的静默失败。
"""
from __future__ import annotations
from typing import Callable

from state import NovelState
import checkpoint as _ckpt


def require_upstream(state: NovelState, agent_name: str, **specs: Callable[[NovelState], bool]) -> bool:
    """
    检查每个 spec 是否在 state 上为真。
    全部为真返回 True；任何一个为假则写一条 progress warning 并返回 False。

    用法：
        if not require_upstream(state, "TwistDesigner",
            volumes=lambda s: bool(s.volumes),
            characters=lambda s: bool(s.characters)):
            return
    """
    missing = []
    for label, predicate in specs.items():
        try:
            ok = bool(predicate(state))
        except Exception:
            ok = False
        if not ok:
            missing.append(label)
    if not missing:
        return True
    short = f"上游缺失：{' / '.join(missing)}（请先重建上游再回来）"
    # 终端打印带 agent_name 便于人读；progress warning 的 source 已带 agent_name，
    # message 不再重复（避免前端"agent:X X 上游..."叠词）
    print(f"  ! {agent_name} {short}")
    try:
        _ckpt.add_progress_warning(
            level="error",
            source=f"agent:{agent_name}",
            message=short,
        )
    except Exception:
        pass
    return False
