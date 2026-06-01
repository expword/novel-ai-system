"""
LLMPool —— 全局 LLM 请求池。

解决大规模并发下的四类问题：
  1. **并发上限**：即使多个 parallel_map 嵌套，总并发也不会超过池上限
  2. **速率限制**：token bucket 控制每分钟请求数（RPM），避免打爆 provider
  3. **熔断**：连续失败 N 次后暂停新请求一段时间，给 provider 喘息
  4. **观测**：统计每次调用的耗时/成败/token，写入 metrics.jsonl（可选）

所有 LLM 调用（llm.chat）透明走这个池——不改业务代码，零侵入。

线程安全。全局单例（模块级变量 + lazy init）。
"""
from __future__ import annotations
import os
import json
import time
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


# ═══════════════════════════════════════════════════════
#  令牌桶（速率限制）
# ═══════════════════════════════════════════════════════

class TokenBucket:
    """
    固定 RPM 的令牌桶。capacity = rpm（突发最多这么多），refill rate = rpm/60 per second。
    acquire() 阻塞直到拿到一个 token。
    """
    def __init__(self, rpm: int):
        self.capacity = max(1, rpm)
        self.refill_rate = max(0.01, rpm / 60.0)  # tokens/second
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def acquire(self, timeout: float = None) -> bool:
        """阻塞直到拿到一个 token。返回 True = 成功，False = 超时。"""
        deadline = (time.monotonic() + timeout) if timeout else None
        with self._cond:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # 计算下个 token 何时可用
                wait = (1.0 - self._tokens) / self.refill_rate
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait = min(wait, remaining)
                self._cond.wait(timeout=wait)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now


# ═══════════════════════════════════════════════════════
#  熔断器
# ═══════════════════════════════════════════════════════

class CircuitBreaker:
    """
    三态熔断：
      CLOSED    —— 正常，每次失败计数；失败数 >= threshold 切 OPEN
      OPEN      —— 拒绝所有请求，等待 cooldown 秒
      HALF_OPEN —— 放一个请求过去试探：成功→CLOSED，失败→OPEN（重置 cooldown）
    """
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, cooldown_sec: float = 30.0):
        self.threshold = failure_threshold
        self.cooldown = cooldown_sec
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()
        # 最近失败的错误摘要——用于熔断打开时把根因附进 progress_warning，
        # 让前端 ⚠ 徽章能直接显示"为什么熔断了"，不必去翻 stdout.log。
        self._recent_errors: deque[tuple[str, str]] = deque(maxlen=10)  # (error_type, short_msg)

    def can_proceed(self) -> bool:
        with self._lock:
            if self._state == self.CLOSED:
                return True
            # OPEN: 冷却够了就切 HALF_OPEN
            if self._state == self.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown:
                    self._state = self.HALF_OPEN
                    return True  # 放一个试探
                return False
            # HALF_OPEN: 仅放一个试探（其他并发请求仍拒绝）
            return False

    def record_success(self):
        with self._lock:
            was_open = self._state != self.CLOSED
            self._state = self.CLOSED
            self._failure_count = 0
            self._recent_errors.clear()
        if was_open:
            print(f"  [CB] 熔断器恢复：CLOSED")
            # 通知前端徽章消失
            try:
                from persistence.checkpoint import (
                    clear_progress_warnings,
                    add_progress_warning,
                )
                clear_progress_warnings(source="llm:circuit_breaker")
                add_progress_warning(
                    level="info",
                    source="llm:circuit_breaker",
                    message="LLM 熔断器已恢复——试探请求成功，恢复正常调度",
                )
            except Exception:
                pass

    def record_failure(self, err: Exception = None):
        # 先在锁内更新状态，记录是否发生 CLOSED→OPEN 转换
        with self._lock:
            self._failure_count += 1
            if err is not None:
                self._recent_errors.append(
                    (type(err).__name__, str(err)[:120].replace("\n", " "))
                )
            transition_to_open = False
            if self._state == self.HALF_OPEN or self._failure_count >= self.threshold:
                if self._state != self.OPEN:
                    transition_to_open = True
                self._state = self.OPEN
                self._opened_at = time.monotonic()
            # 拍一份快照在锁外用
            snap_errors = list(self._recent_errors)
            snap_failure_count = self._failure_count
            snap_cooldown = self.cooldown

        if transition_to_open:
            # 拼错误类型计数 + 最后一条错误的简短文本
            from collections import Counter
            type_counts = Counter(t for t, _ in snap_errors)
            type_summary = " / ".join(
                f"{t} x{c}" for t, c in type_counts.most_common(4)
            )
            last_msg = snap_errors[-1][1] if snap_errors else ""
            print(
                f"  [CB] 熔断器打开（连续 {snap_failure_count} 次失败）"
                f"——拒绝新请求 {snap_cooldown:.0f} 秒；最近错误：{type_summary}"
            )
            # 写 progress_warning——让前端 ⚠ 徽章看到熔断事件 + 根因
            try:
                from persistence.checkpoint import add_progress_warning
                add_progress_warning(
                    level="error",
                    source="llm:circuit_breaker",
                    message=(
                        f"LLM 熔断器打开：连续 {snap_failure_count} 次失败，"
                        f"冷却 {snap_cooldown:.0f}s 内拒绝新请求。"
                        f"最近错误类型：{type_summary or '未知'}"
                        + (f"；最后一次：{last_msg}" if last_msg else "")
                    ),
                )
            except Exception:
                pass

    @property
    def state(self) -> str:
        return self._state


class CircuitOpenError(RuntimeError):
    """熔断器 OPEN 状态下新请求直接抛这个——不消耗速率配额、不占并发槽。"""


# ═══════════════════════════════════════════════════════
#  统计
# ═══════════════════════════════════════════════════════

@dataclass
class CallStat:
    ts: str = ""
    success: bool = True
    latency_sec: float = 0.0
    agent_name: str = ""
    error_type: str = ""


class PoolStats:
    """维护最近 N 次调用的统计。可选写入 metrics.jsonl。"""
    def __init__(self, recent_limit: int = 500, metrics_file: Optional[str] = None):
        self._recent: deque[CallStat] = deque(maxlen=recent_limit)
        self._total_calls = 0
        self._total_failures = 0
        self._lock = threading.Lock()
        self._metrics_file = metrics_file

    def record(self, success: bool, latency_sec: float,
               agent_name: str = "", error: Exception = None):
        stat = CallStat(
            ts=datetime.now().isoformat(timespec="seconds"),
            success=success,
            latency_sec=round(latency_sec, 3),
            agent_name=agent_name,
            error_type=type(error).__name__ if error else "",
        )
        with self._lock:
            self._recent.append(stat)
            self._total_calls += 1
            if not success:
                self._total_failures += 1
        if self._metrics_file:
            try:
                os.makedirs(os.path.dirname(self._metrics_file), exist_ok=True)
                with open(self._metrics_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(stat.__dict__, ensure_ascii=False) + "\n")
            except Exception:
                pass  # 统计不该影响主流程

    def snapshot(self) -> dict:
        with self._lock:
            recent = list(self._recent)
            total = self._total_calls
            fails = self._total_failures
        if not recent:
            return {"total_calls": total, "total_failures": fails}
        success_calls = [s for s in recent if s.success]
        avg_latency = (sum(s.latency_sec for s in success_calls) / len(success_calls)) if success_calls else 0.0
        return {
            "total_calls": total,
            "total_failures": fails,
            "recent_window": len(recent),
            "recent_success_rate": round(sum(1 for s in recent if s.success) / len(recent), 3),
            "recent_avg_latency_sec": round(avg_latency, 2),
        }


# ═══════════════════════════════════════════════════════
#  LLMPool
# ═══════════════════════════════════════════════════════

class LLMPool:
    """
    全局 LLM 请求池——所有 LLM 调用都通过 call() 统一经过这里。
    """
    def __init__(
        self,
        max_concurrent: int = 8,
        rate_limit_rpm: int = 60,
        circuit_failure_threshold: int = 5,
        circuit_cooldown_sec: float = 30.0,
        metrics_file: Optional[str] = None,
    ):
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._rate_bucket = TokenBucket(rate_limit_rpm)
        self._circuit = CircuitBreaker(circuit_failure_threshold, circuit_cooldown_sec)
        self._stats = PoolStats(metrics_file=metrics_file)
        self.max_concurrent = max_concurrent
        self.rate_limit_rpm = rate_limit_rpm

    def call(self, fn: Callable[..., T], *args,
             agent_name: str = "", **kwargs) -> T:
        """
        同步调用。先检查熔断 → 取速率令牌 → 取并发槽 → 跑 fn。
        fn 抛异常会计入失败并触发熔断。
        """
        # 1. 熔断检查——不消耗配额
        if not self._circuit.can_proceed():
            raise CircuitOpenError(
                f"LLM 熔断器 OPEN（连续失败过多，冷却中）——本次请求被拒绝"
            )

        # 2. 速率限制
        self._rate_bucket.acquire()

        # 3. 并发槽 + 执行
        t0 = time.monotonic()
        self._semaphore.acquire()
        try:
            result = fn(*args, **kwargs)
            self._circuit.record_success()
            self._stats.record(success=True, latency_sec=time.monotonic() - t0,
                               agent_name=agent_name)
            return result
        except Exception as e:
            self._circuit.record_failure(e)
            self._stats.record(success=False, latency_sec=time.monotonic() - t0,
                               agent_name=agent_name, error=e)
            raise
        finally:
            self._semaphore.release()

    def stats(self) -> dict:
        # 最近失败摘要——让前端能看到熔断的"根因画像"
        from collections import Counter
        with self._circuit._lock:
            recent = list(self._circuit._recent_errors)
        type_counts = Counter(t for t, _ in recent)
        return {
            **self._stats.snapshot(),
            "circuit_state": self._circuit.state,
            "circuit_recent_errors": [
                {"type": t, "msg": m} for t, m in recent[-5:]
            ],
            "circuit_error_type_counts": dict(type_counts.most_common(6)),
            "max_concurrent": self.max_concurrent,
            "rate_limit_rpm": self.rate_limit_rpm,
        }


# ═══════════════════════════════════════════════════════
#  全局单例（lazy init，从 config 读默认值）
# ═══════════════════════════════════════════════════════

_default_pool: Optional[LLMPool] = None
_default_pool_lock = threading.Lock()


def get_default_pool() -> LLMPool:
    """获取全局单例。首次调用时从 config 读参数。"""
    global _default_pool
    if _default_pool is None:
        with _default_pool_lock:
            if _default_pool is None:
                _default_pool = _build_default_pool()
    return _default_pool


def _build_default_pool() -> LLMPool:
    # 参数从 config 读，缺就用保守默认值
    try:
        import config
        max_concurrent = getattr(config, "LLM_MAX_CONCURRENT", 8)
        rate_limit_rpm = getattr(config, "LLM_RATE_LIMIT_RPM", 60)
        cb_threshold = getattr(config, "LLM_CB_FAILURE_THRESHOLD", 5)
        cb_cooldown = getattr(config, "LLM_CB_COOLDOWN_SEC", 30.0)
    except Exception:
        max_concurrent, rate_limit_rpm, cb_threshold, cb_cooldown = 8, 60, 5, 30.0

    # metrics 文件——按当前项目走（每项目独立 metrics.jsonl）
    metrics_file = None
    try:
        from project_mgmt import project_context as pctx
        metrics_file = os.path.join(pctx.control_dir(), "llm_metrics.jsonl")
    except Exception:
        pass

    print(f"  [pool] LLMPool 初始化：并发={max_concurrent}｜RPM={rate_limit_rpm}"
          f"｜熔断阈值={cb_threshold}｜冷却={cb_cooldown}s")
    return LLMPool(
        max_concurrent=max_concurrent,
        rate_limit_rpm=rate_limit_rpm,
        circuit_failure_threshold=cb_threshold,
        circuit_cooldown_sec=cb_cooldown,
        metrics_file=metrics_file,
    )


def reset_default_pool():
    """用于测试：强制重建单例。"""
    global _default_pool
    with _default_pool_lock:
        _default_pool = None


# ═══════════════════════════════════════════════════════
#  便捷装饰器
# ═══════════════════════════════════════════════════════

def pooled(fn: Callable) -> Callable:
    """装饰器：让函数透明地走全局池。"""
    def wrapper(*args, **kwargs):
        agent = kwargs.pop("_pool_agent_name", "")
        pool = get_default_pool()
        return pool.call(fn, *args, agent_name=agent, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
