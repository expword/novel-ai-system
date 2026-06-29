"""LLM 调用适配层 —— 可插拔后端，默认 mock(零 API key 可跑)。

镜像原项目 llm_layer.llm.system_user 的心智模型：对外暴露 system_user()，
内部走统一后端。这样 graph 的节点不关心用的是 mock 还是真实模型。

切真实模型(OpenAI 兼容)：
    from novel_v2 import llm
    llm.set_backend(llm.OpenAIBackend(base_url=..., api_key=..., model="..."))
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional, Protocol


class LLMBackend(Protocol):
    def complete(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str: ...


class MockBackend:
    """确定性 mock：不调任何 API，按提示词内容产出可预测文本。

    设计成「可被 demo 驱动」：
      · 写正文时若 user 里带 [INJECT_FLAW] 标记，会故意写入占位违规 token，
        用来触发 canon-revise 自愈循环；修订指令出现时则去掉它。
    """

    FORBIDDEN = "〔待补：占位符〕"   # 故意的设定违规 token，被 canon_checker 抓

    def complete(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        tag = hashlib.md5((system + user).encode("utf-8")).hexdigest()[:6]

        # 修订路径：提示里出现「修订」且要求移除占位符 → 产出干净文本
        if "修订" in user or "去掉占位" in user or "remove placeholder" in user.lower():
            return f"(修订稿·{tag}) 灵气自丹田涌出，他周身泛起微光，一步踏出三丈。剑光如练，劈开夜色。"

        # 故意注入违规(供 demo 演示自愈环)
        if "[INJECT_FLAW]" in user:
            return (f"(初稿·{tag}) 他催动灵力，{MockBackend.FORBIDDEN}，"
                    f"周身气息陡然攀升，引得四下惊呼。")

        # 普通写正文
        if "写正文" in system or "writer" in system.lower():
            return (f"(正文·{tag}) 晨雾未散，少年立于断崖之上，"
                    f"运转心法，灵气如溪流般汇入经脉，眼神渐渐坚定。")

        # 审稿/打分等其它用途
        return f"(LLM·{tag}) ok"


class OpenAIBackend:
    """真实后端(OpenAI 兼容)。需 `pip install openai`。"""

    def __init__(self, *, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 延迟导入：不装 openai 也能用 mock
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=110.0)
        self._model = model

    def complete(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""


_backends: dict = {}                 # usage -> backend
_default: LLMBackend = MockBackend()  # 未注册任何后端时的兜底(离线 mock)


def set_backend(backend: LLMBackend, usage: str = "main") -> None:
    """注册某用途的后端。usage 约定：main/reviewer/fallback/extractor/planner。

    只传 backend 即注册为 main(向后兼容旧调用)。
    """
    _backends[usage] = backend


def reset_backends() -> None:
    _backends.clear()


def system_user(system: str, user: str, *, usage: str = "main",
                temperature: float = 0.8, max_tokens: int = 2048) -> str:
    """统一入口 —— 按 usage 路由(Multi-Model Routing)。

    该用途无后端 → 回退 main → 回退离线默认；调用失败 → 尝试 fallback 用途。
    """
    b = _backends.get(usage) or _backends.get("main") or _default
    try:
        return b.complete(system, user, temperature=temperature, max_tokens=max_tokens)
    except Exception:
        fb = _backends.get("fallback")
        if fb is not None and fb is not b:
            return fb.complete(system, user, temperature=temperature, max_tokens=max_tokens)
        raise
