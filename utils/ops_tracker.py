"""
OpsTracker —— 给"同步 HTTP 请求里的长耗时 LLM 工作流"提供两件事：

1. 实时进度写入 control/progress_status.json（前端轮询这个文件看进度）
2. 每项目的并发锁：同一个项目同时只允许一个同步操作跑——用户连点两次
   第二个会立刻被拒绝（409），不会覆盖进行中的工作。

director.py 跑子进程时也调 write_progress（source="director"），web 请求里直接调
（source="web-sync"），两边写同一个 control/progress_status.json，前端不区分源。

用法：
    with operation_scope(project_id, "refine_intent", "开始精炼世界观") as ok:
        if not ok:
            return {"error": "该项目已有同步任务在跑，请等待或刷新"}, 409
        set_progress(project_id, "IntentAnalyzer", "正在解析意图")
        ... do work ...
        set_progress(project_id, "ConceptPitch", "正在生成卖点")
        ... more work ...
    # 退出 with 会自动 clear 进度 + 释放锁
"""
from __future__ import annotations
import os
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from project_mgmt import project_context as pctx


# 每个 project_id 一个 Lock——放内存里（Flask 单进程 dev server 够用）
# 子进程不会用这个锁（director 跑时 Flask 的这个模块在自己内存里，互不干扰）
_locks: dict[str, threading.Lock] = {}
_locks_table_lock = threading.Lock()

# 记录每个项目当前活跃操作（只给报错消息用）
_active_ops: dict[str, dict] = {}


def _get_lock(project_id: str) -> threading.Lock:
    with _locks_table_lock:
        lk = _locks.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _locks[project_id] = lk
        return lk


def try_acquire(project_id: str, op_name: str) -> bool:
    """非阻塞抢锁。成功 True，已被占用返回 False。"""
    lk = _get_lock(project_id)
    if not lk.acquire(blocking=False):
        return False
    _active_ops[project_id] = {
        "op": op_name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    return True


def release(project_id: str):
    """释放锁 + 清活跃记录。"""
    _active_ops.pop(project_id, None)
    lk = _locks.get(project_id)
    if lk and lk.locked():
        try:
            lk.release()
        except RuntimeError:
            pass


def active_op(project_id: str) -> Optional[dict]:
    """查询当前活跃操作——给 409 错误消息用。"""
    return _active_ops.get(project_id)


def write_progress(project_id: Optional[str] = None, *, source: str = "web-sync", **fields):
    """
    写进度到 projects/<id>/control/progress_status.json。前端统一读这一份。

    project_id 省略 / 传 None 时走 project_context 当前项目（director subprocess 用）。
    source 区分写入方："web-sync"（Flask 请求）/ "director"（subprocess 流水线）/
    "web-sync-done"（请求结束清场）。前端不区分源，仅作调试线索。

    常用字段：phase / agent / detail / progress_ratio / total_chapters / chapters_done
    """
    try:
        path = pctx.progress_status_file(project_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 读已有数据（保留未覆盖字段，比如 subprocess 写过的章节计数）
        data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                data = {}
        data.update(fields)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data["source"] = source
        # 原子写（tmp + rename）——避免并发写互相踩字节
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def clear_progress(project_id: str):
    """操作结束时清掉 phase/agent/detail，避免残留误导。"""
    write_progress(project_id, source="web-sync-done", phase="", agent="", detail="")


def set_progress(project_id: str, agent: str = "", detail: str = "", phase: str = ""):
    """简化接口：写 agent + detail（可选 phase）。"""
    fields = {}
    if phase:
        fields["phase"] = phase
    if agent:
        fields["agent"] = agent
    if detail:
        fields["detail"] = detail
    if fields:
        write_progress(project_id, **fields)


@contextmanager
def operation_scope(project_id: str, op_name: str, initial_detail: str = ""):
    """
    上下文管理器：进锁 + 写初始进度；退出时自动释放锁 + 清进度。

    yields:
        bool — True 表示抢到锁可以干活；False 表示已被占用，调用方应立即
        返回 409 给前端。
    """
    acquired = try_acquire(project_id, op_name)
    if not acquired:
        yield False
        return
    try:
        # 首次写入——告诉前端"开始了"
        set_progress(project_id,
                     phase=op_name,
                     agent="web",
                     detail=initial_detail or "准备中...")
        yield True
    finally:
        clear_progress(project_id)
        release(project_id)


def active_op_error_message(project_id: str) -> str:
    """给 409 错误生成友好消息。"""
    info = active_op(project_id)
    if not info:
        return "该项目已有任务在运行"
    op = info.get("op", "未知操作")
    started = info.get("started_at", "")
    return f"该项目正在执行【{op}】（始于 {started}），请等待完成或刷新页面查看进度"
