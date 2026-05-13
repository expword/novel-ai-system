"""
并发工具——把"串行跑 N 次 LLM"变成"并发跑 min(N, MAX) 次"。

LLM 调用是 I/O 密集（等待远程响应），ThreadPoolExecutor 是最合适的形式——
不受 GIL 限制，跟官方 requests/openai SDK 完全兼容。

用法：
    from utils.concurrency import parallel_map
    results = parallel_map(
        fn=_design_one_arc,          # 接受单个 item 返回单个 result
        items=characters,
        max_workers=4,
        label="CharacterArc",
    )
    # results 是按输入顺序对齐的结果列表，失败项为 None
"""
from __future__ import annotations
import concurrent.futures
import threading
from typing import Callable, Iterable, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# 打印锁——避免并发 print 交错（只是为了可读性）
_print_lock = threading.Lock()


def safe_print(msg: str):
    with _print_lock:
        print(msg)


def parallel_map(
    fn: Callable[[T], R],
    items: Iterable[T],
    max_workers: int = 4,
    label: str = "",
    on_progress: Optional[Callable[[int, int, T], None]] = None,
) -> list[Optional[R]]:
    """
    对 items 里每个 item 并发调 fn(item)，返回按输入顺序对齐的结果列表。
    失败的位置为 None，不影响其他任务。

    参数：
      max_workers：并发度。LLM 通常 3-5 安全；更高可能打爆 provider rate limit
      label：日志标签，如 "CharacterArc"
      on_progress：可选回调 (done_count, total, current_item) —— 完成每个任务时调用
    """
    items_list = list(items)
    total = len(items_list)
    if total == 0:
        return []
    if total == 1:
        # 单项没必要开线程池，直接跑（避免 ThreadPoolExecutor 的开销和日志噪音）
        try:
            return [fn(items_list[0])]
        except Exception as e:
            safe_print(f"  [{label}] 单项失败：{e}")
            return [None]

    results: list[Optional[R]] = [None] * total
    done_count = 0

    if label:
        safe_print(f"  [{label}] 并发跑 {total} 项（max_workers={max_workers}）...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {pool.submit(fn, item): (i, item) for i, item in enumerate(items_list)}
        for fut in concurrent.futures.as_completed(future_to_idx):
            i, item = future_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                safe_print(f"  [{label}] 任务 {i} 异常：{type(e).__name__}: {e}")
                results[i] = None
            done_count += 1
            if on_progress:
                try:
                    on_progress(done_count, total, item)
                except Exception:
                    pass
            if label:
                safe_print(f"  [{label}] {done_count}/{total} 完成")

    return results
