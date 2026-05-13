"""
LLM client wrapper using OpenAI-compatible API format.
自动重试：遇到 5xx / 网络错误时指数退避重试，最多5次。
支持多模型——每次调用动态读取 llm_runtime 的当前 profile。

**所有 LLM 调用透明走 llm_pool 全局池**——统一并发上限、速率限制、熔断。

**双层超时**（防止上游网关挂连接 5+ 小时不响应）：
  · HTTP_TIMEOUT_SEC：httpx/OpenAI client 单请求超时——能让 httpx 主动断开
  · WALL_CLOCK_TIMEOUT_SEC：本进程 wall-clock 硬上限（concurrent.futures）
    httpx 因 keepalive 等原因没断开时由它兜底；超时后请求被丢弃

**Fallback 备用模型**：主模型/profile 整体失败后，自动切到
user_models.find_by_usage("fallback") 重试一次；仍失败抛聚合错。
"""
from __future__ import annotations
import time
import random
import inspect
import concurrent.futures
from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError

import llm_runtime
import llm_pool


# 单请求超时配置
HTTP_TIMEOUT_SEC = 110.0          # OpenAI client / httpx 层超时（< wall-clock）
WALL_CLOCK_TIMEOUT_SEC = 120.0    # 本进程兜底硬上限——httpx 不生效时由它强制中断


class WallClockTimeoutError(TimeoutError):
    """LLM 调用超过 wall-clock 硬上限——独立于 httpx timeout。"""
    pass


# 按 profile_id 缓存 client（切换模型时新建，不反复重建）
_clients: dict = {}

# Wall-clock 守护用的后台 executor（daemon 线程池——超时被丢弃的请求不阻塞进程退出）
_wall_clock_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=64, thread_name_prefix="llm-wall-clock"
)


def _call_with_wall_clock(pool, fn, *args, agent_name: str = "", **kwargs):
    """
    把 pool.call(fn, ...) 包一层 wall-clock 硬上限。
    超时抛 WallClockTimeoutError；fn 内部异常按原样向上抛。
    """
    future = _wall_clock_executor.submit(pool.call, fn, *args,
                                          agent_name=agent_name, **kwargs)
    try:
        return future.result(timeout=WALL_CLOCK_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        # 不强行 cancel——任由后台线程跑完释放 pool 槽位（OpenAI httpx 层最终会失败）
        raise WallClockTimeoutError(
            f"LLM 调用超过 {WALL_CLOCK_TIMEOUT_SEC:.0f}s 无响应（wall-clock 硬上限）"
        )


def get_client(profile: dict = None) -> OpenAI:
    """根据 profile 获取 client。不传就走当前运行时 profile。

    cache key 必须包含 API key hash，否则两个共享 base_url 但 key 不同的
    用户模型会命中同一个 client（用了错的 key）。
    """
    if profile is None:
        profile = llm_runtime.resolve_profile()
    api_key = llm_runtime.resolve_api_key(profile)
    # 用 (base_url, api_key 前 8 位) 做 cache key——user_models 共享 base_url 也能区分
    key_suffix = (api_key[:8] if api_key else "") or profile.get("env_key", "")
    key = (profile["base_url"], key_suffix)
    if key not in _clients:
        _clients[key] = OpenAI(base_url=profile["base_url"], api_key=api_key, timeout=HTTP_TIMEOUT_SEC)
    return _clients[key]


def _raw_chat_once(messages, temperature, effective_max, profile, client) -> str:
    """一次实际的 HTTP 调用——不做重试。走 pool 的时候就是这个函数被 call。"""
    kwargs = {
        "model": profile["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": effective_max,
        "timeout": HTTP_TIMEOUT_SEC,  # 单请求超时（client 层级 timeout 仅作默认上限）
    }
    extra_body = profile.get("extra_body")
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    return (content or "").strip()


def _do_chat_with_profile(messages, temperature, max_tokens, max_retries,
                          profile, agent_name: str) -> str:
    """
    指定 profile 跑一轮 LLM 调用（含本 profile 内的 max_retries 次重试 + 每次的 wall-clock 守护）。
    所有错误向上抛，外层根据是否还有 fallback 决定是否再来一轮。
    """
    effective_max = min(max_tokens, profile.get("max_output", max_tokens))
    client = get_client(profile)
    pool = llm_pool.get_default_pool()
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            return _call_with_wall_clock(
                pool, _raw_chat_once,
                messages, temperature, effective_max, profile, client,
                agent_name=agent_name,
            )

        except llm_pool.CircuitOpenError as e:
            last_err = e
            print(f"  [CB] 熔断拦截（尝试 {attempt}/{max_retries}）：等待冷却...")
            time.sleep(5)
            continue

        except WallClockTimeoutError as e:
            last_err = e
            _log_retry(attempt, max_retries, "WallClockTimeout", e)

        except APIStatusError as e:
            if e.status_code and e.status_code < 500:
                raise
            last_err = e
            _log_retry(attempt, max_retries, f"HTTP {e.status_code}", e)

        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            _log_retry(attempt, max_retries, type(e).__name__, e)

        except Exception as e:
            last_err = e
            if attempt >= 2:
                raise
            _log_retry(attempt, max_retries, type(e).__name__, e)

        # 指数退避
        wait = min(2 ** attempt + random.uniform(0, 2), 60)
        print(f"  ⏳ 等待 {wait:.1f}s 后重试...")
        time.sleep(wait)

    raise RuntimeError(
        f"LLM 请求失败，已重试 {max_retries} 次。最后错误：{last_err}"
    ) from last_err


def _try_load_fallback_profile(exclude_profile: dict = None):
    """
    返回 user_models 里 usage="fallback" 那条 profile（dict 形式，含 _user_api_key）。
    没配 / 与当前主 profile 是同一条 → 返回 None（避免无意义自我重试）。
    """
    try:
        import user_models as _um
        fb = _um.find_by_usage("fallback")
    except Exception:
        return None
    if not fb:
        return None
    fb_profile = _um.to_profile_dict(fb)
    # 同模型同 base_url 视为相同——别把 fallback 当成主模型再重试一次
    if exclude_profile is not None:
        same_url = exclude_profile.get("base_url") == fb_profile.get("base_url")
        same_model = exclude_profile.get("model") == fb_profile.get("model")
        if same_url and same_model:
            return None
    return fb_profile


def chat(
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: int = 4096,
    max_retries: int = 5,
) -> str:
    """
    发送 LLM 请求。透明走 llm_pool 全局池——自动遵守并发/速率/熔断。

    流程：
      1. 主 profile 跑 max_retries 次（每次 wall-clock 120s 守护）
      2. 全部失败 → 切到 user_models.find_by_usage("fallback") 再跑一遍
      3. fallback 也失败 / 没配 fallback → 抛聚合 RuntimeError

    **线程本地 profile override**：如果本线程设置了 fallback_runner.profile_override，
    则主 profile 用那个（用于关键任务失败后的模型轮换）。
    """
    # 决定主 profile
    try:
        import fallback_runner
        override_pid = fallback_runner.get_thread_profile_override()
    except Exception:
        override_pid = None

    if override_pid:
        primary = llm_runtime._lookup_profile(override_pid) or llm_runtime.resolve_profile()
    else:
        primary = llm_runtime.resolve_profile()

    agent_name = _guess_caller_agent()

    # 第 1 轮：主 profile
    try:
        return _do_chat_with_profile(messages, temperature, max_tokens,
                                      max_retries, primary, agent_name)
    except Exception as primary_err:
        # 第 2 轮：尝试 fallback profile（只跑一次完整重试）
        fb_profile = _try_load_fallback_profile(exclude_profile=primary)
        if fb_profile is None:
            # 没有可用 fallback——直接抛主错
            raise
        primary_label = primary.get("display_name") or primary.get("model") or "主模型"
        fb_label = fb_profile.get("display_name") or fb_profile.get("model") or "备用模型"
        print(f"  🔄 主模型「{primary_label}」失败：{type(primary_err).__name__}: "
              f"{str(primary_err)[:120]}")
        print(f"     切换到备用模型「{fb_label}」重试...")
        try:
            return _do_chat_with_profile(messages, temperature, max_tokens,
                                          max_retries, fb_profile,
                                          f"{agent_name}[fallback]" if agent_name else "fallback")
        except Exception as fb_err:
            raise RuntimeError(
                f"主模型与备用模型均失败 — 主「{primary_label}」: {primary_err}；"
                f"备用「{fb_label}」: {fb_err}"
            ) from fb_err


def system_user(system: str, user: str, temperature: float = 0.8,
                 max_tokens: int = 4096) -> str:
    return chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=temperature, max_tokens=max_tokens)


def chat_stream(
    messages: list[dict],
    temperature: float = 0.8,
    max_tokens: int = 8192,
):
    """
    流式 LLM 调用——生成器，逐 token 输出 content delta。
    用于章节对话调整（chapter chat）那种需要前端增量渲染的场景。

    不走 llm_pool（池化是为了控并发，流式请求常驻连接，走池反而阻塞别的任务）。
    不做内置重试——由调用方在流中断时决定如何处理。

    Yields:
        str: 每次一个 content 片段（可能只有几个字）
    """
    profile = llm_runtime.resolve_profile()
    effective_max = min(max_tokens, profile.get("max_output", max_tokens))
    client = get_client(profile)

    stream_kwargs = {
        "model": profile["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": effective_max,
        "stream": True,
    }
    extra_body = profile.get("extra_body")
    if extra_body:
        stream_kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**stream_kwargs)
    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            yield piece


def chat_with_profile(
    profile_id: str,
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int = 4096,
    max_retries: int = 3,
) -> str:
    """
    指定 profile_id 调用 LLM——用于审核/反思等需要独立模型的场景。

    典型用法：
      chat_with_profile("yunwu-gemini-3-1-flash-lite", messages, ...)

    仍走 llm_pool 全局池（并发/速率/熔断）。
    """
    import llm_profiles
    import user_models as _um

    # 优先在 user_models 里查（按 id 或按 usage）
    profile = None
    um_entry = _um.get(profile_id, include_key=True)
    if um_entry:
        profile = _um.to_profile_dict(um_entry)
    else:
        # 再查 user_models 按 usage——比如传 profile_id="reviewer" 就自动用 reviewer 配的那个
        um_by_usage = _um.find_by_usage(profile_id)
        if um_by_usage:
            profile = _um.to_profile_dict(um_by_usage)
    # 都找不到，走内置 PROFILES
    if profile is None:
        if profile_id not in llm_profiles.PROFILES:
            raise ValueError(f"未注册的 profile：{profile_id}")
        profile = dict(llm_profiles.PROFILES[profile_id])

    # 解析 API key：用户模型自带 > profile env_key 环境变量
    api_key = _resolve_profile_key(profile_id, profile)
    if not api_key:
        raise RuntimeError(f"profile {profile_id} 未能找到 API key（env={profile.get('env_key')}）")

    # 用独立 client（按 (base_url, profile_id) 缓存，和主 LLM 的 client 分开）
    client_key = (profile["base_url"], profile_id)
    if client_key not in _clients:
        _clients[client_key] = OpenAI(
            base_url=profile["base_url"], api_key=api_key, timeout=HTTP_TIMEOUT_SEC
        )
    client = _clients[client_key]

    agent_name = _guess_caller_agent() or f"profile:{profile_id}"

    # 第 1 轮：指定 profile
    try:
        return _do_chat_with_profile(messages, temperature, max_tokens,
                                      max_retries, profile, agent_name)
    except Exception as primary_err:
        # 第 2 轮：fallback 兜底
        fb_profile = _try_load_fallback_profile(exclude_profile=profile)
        if fb_profile is None:
            raise RuntimeError(
                f"[{profile_id}] LLM 请求失败 {max_retries} 次：{primary_err}"
            ) from primary_err
        primary_label = profile.get("display_name") or profile_id
        fb_label = fb_profile.get("display_name") or fb_profile.get("model") or "备用模型"
        print(f"  🔄 指定模型「{primary_label}」失败：{type(primary_err).__name__}: "
              f"{str(primary_err)[:120]}")
        print(f"     切换到备用模型「{fb_label}」重试...")
        try:
            return _do_chat_with_profile(messages, temperature, max_tokens,
                                          max_retries, fb_profile,
                                          f"{agent_name}[fallback]")
        except Exception as fb_err:
            raise RuntimeError(
                f"[{profile_id}] 与备用模型均失败 — 主: {primary_err}；备用「{fb_label}」: {fb_err}"
            ) from fb_err


def _resolve_profile_key(profile_id: str, profile: dict) -> str | None:
    """解析 API key：用户模型自带 > profile.env_key 环境变量。"""
    import os as _os
    # 1. profile 里直接带的 _user_api_key（来自 user_models.to_profile_dict）——最高优先级
    user_key = profile.get("_user_api_key")
    if user_key:
        return user_key
    # 2. 环境变量（内置 PROFILES 的标准方式）
    env_key = profile.get("env_key", "")
    if env_key:
        val = _os.getenv(env_key)
        if val:
            return val
    return None


def _guess_caller_agent() -> str:
    """从调用栈找出 agents/*.py 里的调用者——仅用于 metrics 标签。失败返回 ""。"""
    try:
        frame = inspect.currentframe()
        # 跳过本函数 + chat()
        for _ in range(8):
            frame = frame.f_back
            if frame is None:
                return ""
            fname = frame.f_code.co_filename.replace("\\", "/")
            if "/agents/" in fname:
                mod = fname.rsplit("/", 1)[-1].replace(".py", "")
                return f"agents.{mod}.{frame.f_code.co_name}"
        return ""
    except Exception:
        return ""


def _log_retry(attempt: int, max_retries: int, err_type: str, err: Exception):
    print(f"  ⚠ LLM 请求失败（尝试 {attempt}/{max_retries}，{err_type}）：{str(err)[:80]}")
