"""
关键任务模型轮换重试器——critical agent 失败时自动换模型再跑。

触发场景：
  · 力量体系（RealmDesigner）彻底失败
  · 卷结构（VolumePlanner）彻底失败
  · 人物主角圈（CharacterDesigner 主角批次）彻底失败

执行策略：
  · 按 fallback 列表顺序挨个试模型
  · 每个模型跑 N 次（每次含 request_json 内部的 5 次小重试）
  · 实现机制：线程本地 profile override——让 llm.chat 在本线程临时用指定 profile

模型轮换顺序（可配置）：
  1. 当前默认（用户模型里 usage=main 的那个）
  2. DeepSeek Chat（稳定便宜）
  3. Moonshot 128K（上下文大，适合长 prompt）
  4. Yunwu 聚合的 Claude 3.5 Sonnet（质量高）
  5. GPT-4o-mini（格式稳定）
  6. Qwen Plus（国产兜底）
"""
from __future__ import annotations
import threading
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


# 线程本地存储：当前线程如果设了 profile override，chat() 会用它而非默认
_thread_local = threading.local()


def get_thread_profile_override() -> Optional[str]:
    return getattr(_thread_local, "profile_id", None)


def set_thread_profile_override(profile_id: Optional[str]) -> None:
    if profile_id is None:
        if hasattr(_thread_local, "profile_id"):
            delattr(_thread_local, "profile_id")
    else:
        _thread_local.profile_id = profile_id


class profile_override:
    """
    上下文管理器：在作用域内把本线程的 LLM profile 临时切到指定 id。
    退出时自动恢复。

    用法：
        with profile_override("deepseek-chat"):
            design_realm_system(state)  # 本线程所有 LLM 调用都用 deepseek-chat
    """
    def __init__(self, profile_id: Optional[str]):
        self.profile_id = profile_id
        self._prev = None

    def __enter__(self):
        self._prev = get_thread_profile_override()
        set_thread_profile_override(self.profile_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        set_thread_profile_override(self._prev)
        return False


# ═══════════════════════════════════════════════════════
#  模型轮换 fallback 列表
# ═══════════════════════════════════════════════════════

def build_fallback_chain() -> list[str]:
    """
    构造模型轮换列表：用户模型优先 + 内置目录里挑几个稳定的作兜底。
    顺序：user_models(usage=main) → 几个常见内置 profile id
    """
    chain: list[str] = []

    # 1. 用户模型里 usage=main
    try:
        import user_models
        main_um = user_models.find_by_usage("main")
        if main_um:
            chain.append(main_um["id"])
    except Exception:
        pass

    # 2. 内置目录里挑稳定的——按优先级排
    preferred_builtins = [
        "deepseek-chat",              # 性价比 + 稳定
        "yunwu-deepseek-chat",        # 云雾聚合的 deepseek
        "moonshot-v1-128k",           # 大上下文
        "gpt-4o-mini",                # OpenAI 稳定
        "yunwu-claude-3-5-sonnet",    # 高质量兜底
        "qwen-plus",                  # 国产
    ]
    try:
        import llm_profiles
        for pid in preferred_builtins:
            if pid in llm_profiles.PROFILES and pid not in chain:
                chain.append(pid)
    except Exception:
        pass

    return chain


# ═══════════════════════════════════════════════════════
#  核心：带模型轮换的执行器
# ═══════════════════════════════════════════════════════

def run_with_model_fallback(
    fn: Callable[[], T],
    agent_name: str,
    check_ok: Callable[[T], bool] = lambda r: r is not None,
    retries_per_model: int = 5,
    fallback_chain: Optional[list[str]] = None,
) -> Optional[T]:
    """
    带模型轮换的执行器。

    - fn: 无参数的任务函数，返回结果
    - check_ok(result): 判断结果是否成功（不成功会触发下一次尝试）
    - retries_per_model: 每个模型的重试次数（**注意**：LLM 层还有 5 次内部小重试）
    - fallback_chain: 模型 id 列表，默认用 build_fallback_chain()

    返回第一个成功的结果。所有模型都跑完还失败则返回 None（或抛最后一个异常）。
    """
    chain = fallback_chain if fallback_chain is not None else build_fallback_chain()
    if not chain:
        print(f"  [fallback] {agent_name} 没有可用的模型 chain——按当前默认直接跑")
        return _try_once(fn, check_ok)

    print(f"  [fallback] {agent_name} 启用模型轮换：{' → '.join(chain)}")
    last_err: Optional[Exception] = None

    for profile_id in chain:
        print(f"  [fallback] 切到模型: {profile_id}（每模型最多 {retries_per_model} 次尝试）")
        for attempt in range(1, retries_per_model + 1):
            try:
                with profile_override(profile_id):
                    result = fn()
                if check_ok(result):
                    if attempt > 1 or chain.index(profile_id) > 0:
                        print(f"  [fallback] ✓ {agent_name} 成功（模型={profile_id}，第 {attempt} 次）")
                    return result
                else:
                    print(f"  [fallback] {profile_id} 第 {attempt}/{retries_per_model} 次产出不合格，继续")
            except Exception as e:
                last_err = e
                print(f"  [fallback] {profile_id} 第 {attempt}/{retries_per_model} 次异常：{type(e).__name__}: {str(e)[:80]}")
        print(f"  [fallback] 模型 {profile_id} 已耗尽 {retries_per_model} 次尝试，换下一个")

    print(f"  [fallback] ✗ {agent_name} 所有模型都跑完仍失败")
    if last_err:
        raise RuntimeError(
            f"{agent_name} 模型轮换全部失败（{len(chain)} 个模型 × {retries_per_model} 次）。"
            f"最后错误：{type(last_err).__name__}: {last_err}"
        ) from last_err
    return None


def _try_once(fn: Callable[[], T], check_ok: Callable[[T], bool]) -> Optional[T]:
    """无 fallback 兜底——直接跑一次。"""
    try:
        result = fn()
        return result if check_ok(result) else None
    except Exception as e:
        print(f"  [fallback] 直跑失败：{e}")
        return None
