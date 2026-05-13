"""
TaskScheduler —— DAG 任务调度器。

替换 director.py 里手写的 Phase 串行链——改为任务图：
  - 每个任务声明 depends_on（依赖的其他任务 id）
  - 调度器按拓扑排序分波并发执行
  - 一波内所有任务通过 concurrency.parallel_map 并行
  - 单个任务失败不阻塞其他独立分支（除非标记 critical=True）
  - 任务级 checkpoint：done 的不重跑（进程崩了恢复后跳过）

好处：Phase 1-D/1-F/1-G/1-H 自动并发；Phase 2-B/2-C/2-D 自动并发；
Phase 3 多数子 Phase 自动并发。整条流水线时间压缩 40-50%。
"""
from __future__ import annotations
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

from utils.concurrency import parallel_map
from persistence.state import NovelState


# ═══════════════════════════════════════════════════════
#  Task 数据结构
# ═══════════════════════════════════════════════════════

@dataclass
class Task:
    """
    调度器里的一个任务节点。

    关键字段：
      id         唯一标识（一般是 Phase 编号 "1D" / "2A2"）
      phase      展示名（"Phase 1-D"）
      agent_name 进度条显示用（"WorldBuilder"）
      detail     进度条详情
      fn         实际执行函数，签名 fn(state: NovelState) -> None
      depends_on 依赖的任务 id 列表（该任务开始前这些必须完成）
      critical   True = 失败会抛异常终止整个流水线；False = 失败记录警告但继续
      skip_if    可选断言函数 skip_if(state) -> bool，True 跳过本任务（如无前置数据）
    """
    id: str
    phase: str = ""
    agent_name: str = ""
    detail: str = ""
    fn: Callable[[NovelState], None] = None
    depends_on: list[str] = field(default_factory=list)
    critical: bool = False
    skip_if: Optional[Callable[[NovelState], bool]] = None


# ═══════════════════════════════════════════════════════
#  TaskScheduler
# ═══════════════════════════════════════════════════════

class TaskScheduler:
    """
    DAG 调度器——按依赖拓扑分波并发执行任务。

    不写 progress.json（那是 director 的事），只暴露 hook：
      - on_task_start(task)    任务即将执行
      - on_task_success(task, elapsed)
      - on_task_failure(task, err, elapsed)
      - on_task_skipped(task)

    这样 director 可以用 hook 注入 _set_current_step / _save / mark_phase_done 等。
    """

    def __init__(self, tasks: list[Task]):
        self.tasks: dict[str, Task] = {t.id: t for t in tasks}
        # Hooks（director 注入）
        self.on_task_start: Optional[Callable[[Task], None]] = None
        self.on_task_success: Optional[Callable[[Task, float], None]] = None
        self.on_task_failure: Optional[Callable[[Task, Exception, float], None]] = None
        self.on_task_skipped: Optional[Callable[[Task], None]] = None
        self.on_wave_start: Optional[Callable[[int, list[Task]], None]] = None
        self._validate_deps()

    def _validate_deps(self):
        for t in self.tasks.values():
            for d in t.depends_on:
                if d not in self.tasks:
                    raise ValueError(f"Task {t.id} 依赖了不存在的任务 {d}")

    def run(
        self,
        state: NovelState,
        done_ids: set[str] = None,
        max_parallel: int = 4,
    ) -> dict[str, str]:
        """
        跑所有任务。done_ids 传入已完成的（用于断点恢复）。
        返回 {task_id: "done" | "failed" | "skipped"}。
        """
        done = set(done_ids or [])
        # 记录本次 run 的结果
        outcome: dict[str, str] = {tid: "done" for tid in done}
        pending = {tid for tid in self.tasks if tid not in done}

        wave_num = 0
        while pending:
            # 找出当前可执行的任务（所有依赖都完成的）
            ready = sorted([
                tid for tid in pending
                if all(d in done for d in self.tasks[tid].depends_on)
            ])
            if not ready:
                # 拓扑死锁——通常是循环依赖或上游失败标记错误
                raise RuntimeError(
                    f"任务图死锁：剩余 {pending}，但没有任务的依赖被全部满足。"
                    f"可能原因：某上游任务失败但未标 done；或存在循环依赖"
                )

            wave_num += 1
            wave_tasks = [self.tasks[tid] for tid in ready]
            if self.on_wave_start:
                try: self.on_wave_start(wave_num, wave_tasks)
                except Exception: pass

            print(f"\n═══ 调度波 #{wave_num}：{len(wave_tasks)} 个任务并发 ═══")
            for t in wave_tasks:
                print(f"  · [{t.id}] {t.phase} — {t.agent_name}")

            # 并发执行本波——parallel_map 底层用 ThreadPoolExecutor
            # 每个 worker 最终落到 LLM 调用时会走 llm_pool（已有速率/熔断控制）
            results = parallel_map(
                fn=lambda task: self._run_one(task, state),
                items=wave_tasks,
                max_workers=max_parallel,
                label=f"Wave{wave_num}",
            )

            # 收集结果——哪些 done / failed / skipped
            for task, res in zip(wave_tasks, results):
                status = res or "failed"
                outcome[task.id] = status
                if status in ("done", "skipped", "failed"):
                    # failed 非 critical 时也标 done——让依赖它的任务能继续跑
                    # critical 失败已在 _run_one 里 raise 了
                    done.add(task.id)
                pending.discard(task.id)

        return outcome

    def _run_one(self, task: Task, state: NovelState) -> str:
        """执行单个任务。返回 'done' / 'skipped' / 'failed'。"""
        # 可选：skip_if 条件判断
        if task.skip_if is not None:
            try:
                if task.skip_if(state):
                    if self.on_task_skipped:
                        try: self.on_task_skipped(task)
                        except Exception: pass
                    return "skipped"
            except Exception:
                pass  # skip_if 出错就当没 skip

        if self.on_task_start:
            try: self.on_task_start(task)
            except Exception: pass

        t0 = time.monotonic()
        try:
            task.fn(state)
            elapsed = time.monotonic() - t0
            if self.on_task_success:
                try: self.on_task_success(task, elapsed)
                except Exception: pass
            return "done"
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  ✗ [{task.id}] 失败（{elapsed:.1f}s）：{type(e).__name__}: {e}")
            traceback.print_exc()
            if self.on_task_failure:
                try: self.on_task_failure(task, e, elapsed)
                except Exception: pass
            if task.critical:
                # critical 任务失败直接抛，终止整个流水线
                raise
            return "failed"


# ═══════════════════════════════════════════════════════
#  可视化辅助（调试 + 前端展示）
# ═══════════════════════════════════════════════════════

def compute_waves(tasks: list[Task], done: set[str] = None) -> list[list[str]]:
    """
    预计算执行波次——不实际执行，只返回每波的任务 id。
    用于前端预览或调试。
    """
    task_map = {t.id: t for t in tasks}
    done_set = set(done or [])
    pending = {t.id for t in tasks if t.id not in done_set}
    waves: list[list[str]] = []
    while pending:
        ready = sorted([
            tid for tid in pending
            if all(d in done_set for d in task_map[tid].depends_on)
        ])
        if not ready:
            raise RuntimeError(f"死锁：剩余 {pending}")
        waves.append(ready)
        for tid in ready:
            done_set.add(tid)
            pending.discard(tid)
    return waves
