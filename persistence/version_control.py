"""
VersionControl —— state 版本快照管理。

每个 Phase 完成后、每章写完后，director 都可以调用 snapshot(state, label)。
快照写到 output/checkpoint/history/state_<timestamp>.json，并在 state.version_snapshots 里记一笔索引。

需要回退时可以用 rollback(timestamp) 把 state.json 恢复到某个快照。
"""
from __future__ import annotations
import os
import json
import shutil
import dataclasses
from datetime import datetime
from typing import Optional

from persistence.state import NovelState, VersionSnapshot
from persistence.checkpoint import _to_json, _load_state, STATE_FILE


from project_mgmt import project_context as _pctx
HISTORY_DIR = _pctx.history_dir()
MAX_SNAPSHOTS = 50   # 超过这个数自动清理最旧的


def _ensure_history_dir():
    os.makedirs(HISTORY_DIR, exist_ok=True)


def snapshot(state: NovelState, label: str, phase: str = "", chapter_index: int = -1, notes: str = "") -> str:
    """
    保存一次 state 快照。返回 timestamp。
    同时在 state.version_snapshots 里记一笔索引。
    """
    _ensure_history_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(HISTORY_DIR, f"state_{ts}_{label}.json")
    # 避免重名（同秒多次调用）
    counter = 1
    while os.path.exists(path):
        path = os.path.join(HISTORY_DIR, f"state_{ts}_{label}_{counter}.json")
        counter += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(_to_json(state), f, ensure_ascii=False, indent=2)

    # 记索引
    snap_record = VersionSnapshot(
        timestamp=ts, label=label, phase=phase,
        chapter_index=chapter_index, notes=notes,
    )
    state.version_snapshots.append(snap_record)

    # 清理：超过 MAX_SNAPSHOTS 就删掉最旧的
    _prune_old_snapshots(state)
    return ts


def _prune_old_snapshots(state: NovelState):
    if len(state.version_snapshots) <= MAX_SNAPSHOTS:
        return
    # 保留最近的；尤其保留 phase_* 结尾的重要节点
    sorted_snaps = sorted(state.version_snapshots, key=lambda s: s.timestamp)
    keep_count = MAX_SNAPSHOTS
    to_remove = sorted_snaps[:-keep_count]
    for snap in to_remove:
        # 重要节点（phase 完成）永远保留
        if snap.label.startswith("phase_"):
            continue
        # 物理删除文件
        prefix = f"state_{snap.timestamp}_{snap.label}"
        for fname in os.listdir(HISTORY_DIR):
            if fname.startswith(prefix):
                try:
                    os.remove(os.path.join(HISTORY_DIR, fname))
                except OSError:
                    pass
    # 从索引里剔除
    state.version_snapshots = [
        s for s in state.version_snapshots
        if s in sorted_snaps[-keep_count:] or s.label.startswith("phase_")
    ]


def list_snapshots(state: NovelState = None) -> list[dict]:
    """列出所有历史快照（从 state 里读，或直接扫描目录）。"""
    if state and state.version_snapshots:
        return [
            {"timestamp": s.timestamp, "label": s.label,
             "phase": s.phase, "chapter_index": s.chapter_index, "notes": s.notes}
            for s in sorted(state.version_snapshots, key=lambda x: x.timestamp, reverse=True)
        ]
    # 兜底：扫目录
    _ensure_history_dir()
    result = []
    for fname in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not fname.startswith("state_"):
            continue
        parts = fname[len("state_"):].rsplit(".json", 1)[0].split("_", 2)
        if len(parts) >= 2:
            ts = "_".join(parts[:2])
            label = parts[2] if len(parts) > 2 else ""
            result.append({"timestamp": ts, "label": label, "file": fname})
    return result


def rollback(timestamp: str, label_hint: str = "") -> Optional[NovelState]:
    """
    把整个 state 恢复到某个历史快照。返回恢复后的 NovelState，失败返回 None。
    timestamp 可以是精确时间戳或前缀匹配。

    【关键】：实际 state 是分片存的（checkpoint/state/*.json），load_state 优先读分片。
    所以回滚必须同时重写分片，否则"回滚"对真实 state 毫无影响。
    """
    _ensure_history_dir()
    candidates = []
    for fname in os.listdir(HISTORY_DIR):
        if fname.startswith(f"state_{timestamp}"):
            if not label_hint or label_hint in fname:
                candidates.append(fname)
    if not candidates:
        print(f"  ✗ 未找到 timestamp={timestamp}{' label~'+label_hint if label_hint else ''} 的快照")
        return None
    candidates.sort()
    src = os.path.join(HISTORY_DIR, candidates[0])

    # 1. 备份当前分片 state（便于事故回滚回滚）
    try:
        from persistence import state_storage
        sd = state_storage.state_dir()
        if os.path.isdir(sd):
            bak_dir = sd + ".before_rollback_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copytree(sd, bak_dir)
            print(f"  💾 当前分片 state 已备份到 {bak_dir}")
    except Exception as e:
        print(f"  ⚠ 分片备份失败（继续回滚）：{type(e).__name__}: {e}")

    # 2. 单体 state.json 也备份（历史兼容）
    if os.path.exists(STATE_FILE):
        bak = STATE_FILE + ".before_rollback_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(STATE_FILE, bak)
        print(f"  💾 当前单体 state.json 已备份到 {bak}")

    # 3. 从历史快照加载 state
    shutil.copy2(src, STATE_FILE)
    print(f"  ↩ 已读回 {src}")
    with open(STATE_FILE, encoding="utf-8") as f:
        restored = _load_state(json.load(f))

    # 4. 【关键】把恢复的 state 刷回分片目录——否则 load_state 优先读分片，回滚无效
    try:
        from persistence import state_storage
        sd = state_storage.state_dir()
        # 清掉旧分片再全量重写（避免"已删字段的老 section 文件"遗留）
        if os.path.isdir(sd):
            for fname in os.listdir(sd):
                try:
                    os.remove(os.path.join(sd, fname))
                except OSError:
                    pass
        state_storage.save_split(restored)
        print(f"  ✓ 分片目录已重写为快照版本")
    except Exception as e:
        print(f"  ⚠ 分片刷新失败（注意：回滚可能未生效）：{type(e).__name__}: {e}")

    return restored


def report_recent(state: NovelState, n: int = 5) -> str:
    """打印最近 N 个快照。"""
    recent = sorted(state.version_snapshots, key=lambda s: s.timestamp, reverse=True)[:n]
    if not recent:
        return "（无版本快照）"
    lines = ["【版本快照】"]
    for s in recent:
        ch = f" Ch{s.chapter_index}" if s.chapter_index > 0 else ""
        lines.append(f"  {s.timestamp} [{s.label}]{ch}")
    return "\n".join(lines)
