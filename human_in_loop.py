"""
HumanInTheLoop —— 关键节点暂停机制。

定义"必须人审的时刻"，碰到就：
1. 写一个 pending_approval JSON 到 output/checkpoint/pending_approvals/
2. 记录到 state.pending_approvals 里
3. 要么 raise 暂停（默认），要么记录 warning 但继续（按 HITL_MODE 决定）

用户处理完后，可以：
- 编辑 pending_approval 文件里 approved=true，然后重新 python main.py
- 或删除文件，重新 main.py

触发点（director 里明确调用）：
- 每卷开始前（卷大纲必须人审）
- 关键转折章（主角跨大境界、重要人物死亡、主线伏笔回收）
"""
from __future__ import annotations
import os
import json
import uuid
from datetime import datetime
from typing import Optional

from state import NovelState, PendingApproval


import project_context as _pctx
APPROVAL_DIR = _pctx.approval_dir()


class HITLPause(Exception):
    """人工介入点触发暂停。"""


def _ensure_dir():
    os.makedirs(APPROVAL_DIR, exist_ok=True)


def request_approval(
    state: NovelState,
    reason: str,
    *,
    trigger_chapter: int = -1,
    trigger_phase: str = "",
    payload: Optional[dict] = None,
    mode: str = "pause",
) -> bool:
    """
    请求人工审核。返回 True=已通过（历史记录里已 approved 或 mode!="pause"）；raise HITLPause=需要暂停等待。
    payload 是附加数据（比如卷大纲的 JSON），会写进 approval 文件。
    mode:
      - "pause"（默认）：如果没批过就 raise HITLPause，director 决定如何处理
      - "warn"：只记录警告，不暂停
      - "skip"：完全跳过（相当于关闭 HITL）
    """
    # 查已审过的
    for ap in state.pending_approvals:
        if (ap.reason == reason and
                ap.trigger_chapter == trigger_chapter and
                ap.trigger_phase == trigger_phase):
            if ap.approved:
                return True
            # 找到了但未批——继续写新文件/提示
            break

    if mode == "skip":
        return True

    _ensure_dir()
    approval_id = f"ap_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    filename = f"{approval_id}__{_sanitize(reason)[:30]}.json"
    path = os.path.join(APPROVAL_DIR, filename)

    content = {
        "approval_id": approval_id,
        "reason": reason,
        "trigger_chapter": trigger_chapter,
        "trigger_phase": trigger_phase,
        "created_at": datetime.now().isoformat(),
        "approved": False,
        "approver_note": "",
        "payload": payload or {},
        "how_to_approve": (
            "编辑此文件：把 approved 改为 true，保存；"
            "可选填 approver_note。然后重新运行 python main.py 继续。"
            "若要拒绝/修改内容，直接去改 output/plans/ 下的对应文件后再把 approved 设 true。"
        ),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)

    # 记到 state
    state.pending_approvals.append(PendingApproval(
        approval_id=approval_id,
        reason=reason,
        trigger_chapter=trigger_chapter,
        trigger_phase=trigger_phase,
        created_at=content["created_at"],
        approved=False,
        approver_note="",
    ))

    msg = f"🛑 HITL 暂停：{reason}（文件：{path}）"
    if mode == "warn":
        print(f"  ⚠ {msg}  [mode=warn，继续执行但请注意]")
        return True
    # mode == "pause"
    print(f"  {msg}")
    print(f"     请审核后编辑文件把 approved=true，然后重新 python main.py")
    raise HITLPause(msg)


def check_pending_approvals(state: NovelState) -> None:
    """
    启动时调用：扫 pending_approvals 目录，把 approved=true 的同步到 state。
    同名 approval 以文件里的 approved 状态为准。
    """
    _ensure_dir()
    updated = 0
    for fname in os.listdir(APPROVAL_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(APPROVAL_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("approved"):
            continue
        ap_id = data.get("approval_id", "")
        # 在 state 里找对应
        for ap in state.pending_approvals:
            if ap.approval_id == ap_id and not ap.approved:
                ap.approved = True
                ap.approver_note = data.get("approver_note", "")
                updated += 1
                # 已批的文件可以移到 archive
                archive_dir = os.path.join(APPROVAL_DIR, "archive")
                os.makedirs(archive_dir, exist_ok=True)
                try:
                    os.rename(path, os.path.join(archive_dir, fname))
                except OSError:
                    pass
                break
    if updated:
        print(f"  ✓ 同步 {updated} 个已批准的 HITL 审核")


def _sanitize(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)


# ═══════════════════════════════════════════════════════
#  预设触发点工厂函数
# ═══════════════════════════════════════════════════════

def gate_volume_start(state, volume_index: int, vol_summary: dict, mode: str = "pause") -> bool:
    """卷大纲必须人审。"""
    return request_approval(
        state,
        reason=f"卷{volume_index}大纲审核",
        trigger_phase=f"volume_{volume_index}_start",
        payload=vol_summary,
        mode=mode,
    )


def gate_breakthrough(state, chapter_index: int, realm_from: str, realm_to: str, mode: str = "pause") -> bool:
    """主角跨大境界必须人审。"""
    return request_approval(
        state,
        reason=f"主角境界突破：{realm_from} → {realm_to}",
        trigger_chapter=chapter_index,
        mode=mode,
    )


def gate_major_death(state, chapter_index: int, character_name: str, mode: str = "pause") -> bool:
    """重要人物死亡必须人审。"""
    return request_approval(
        state,
        reason=f"重要人物死亡：{character_name}",
        trigger_chapter=chapter_index,
        mode=mode,
    )


def gate_major_foreshadow_resolve(state, chapter_index: int, fw_id: str, mode: str = "pause") -> bool:
    """主线伏笔回收必须人审（效果要保证）。"""
    return request_approval(
        state,
        reason=f"主线伏笔{fw_id}回收",
        trigger_chapter=chapter_index,
        mode=mode,
    )
