"""
user_feedback helper —— stepwise 审核 modal "带反馈重生成" 的 prompt 注入机制.

═══ 解决的问题 ═══

用户在 modal 里输入反馈(例:"反派太脸谱,给一个有充分动机的灰色反派")后点 regen,
反馈必须被塞到 LLM prompt 顶部,LLM 才能真正按反馈调整产物.

如果改每个 regen 函数 + 每个 phase agent 函数的签名,影响面太大(20+ 处).
改用 **thread-local** 隐式传递:web endpoint 用 `user_feedback_scope` 包裹 regen 调用,
agent 在拼 prompt 时调用 `get_user_feedback_prefix()` 取前缀,无需改函数签名.

═══ 使用 ═══

caller (web endpoint / phase_draft helper):
    with user_feedback_scope("反派太脸谱,给灰色反派"):
        design_factions(state)

agent:
    from utils.feedback_helper import get_user_feedback_prefix
    prompt = f"{get_user_feedback_prefix()}你是势力设计师,..."

═══ 安全 ═══

· thread-local 隔离 - 多线程并发时各自独立
· scope 退出自动 clear,不会跨调用污染
· agent 没集成时 prefix 返回 "",不影响原行为(向后兼容)
"""
from __future__ import annotations
import threading
from contextlib import contextmanager
from typing import Iterator


_local = threading.local()


@contextmanager
def user_feedback_scope(feedback: str) -> Iterator[None]:
    """在 with 块内,任何 agent 调用 get_user_feedback_prefix() 都能拿到这条反馈.

    嵌套调用安全(prev 会被恢复).
    """
    prev = getattr(_local, "feedback", "")
    _local.feedback = (feedback or "").strip()
    try:
        yield
    finally:
        _local.feedback = prev


def get_user_feedback_prefix() -> str:
    """读当前 thread-local 反馈,返回 prompt 前缀字符串(无反馈时返回空).

    agent 在拼 prompt 时调用此函数即可,无需改签名.
    """
    fb = getattr(_local, "feedback", "") or ""
    if not fb:
        return ""
    return (
        "═══ ⚠ 用户反馈(本次重生成必须按此调整)═══\n"
        f"{fb}\n"
        "上一次的产物作者不满意,本次必须明确响应上述反馈:\n"
        "  · 不要重复上次的方向\n"
        "  · 在产物中体现你对该反馈的回应\n"
        "  · 在合理范围内大胆采纳反馈方向\n"
        "\n"
    )


def has_user_feedback() -> bool:
    """探测当前是否在 user_feedback_scope 内(且反馈非空)."""
    return bool(getattr(_local, "feedback", "") or "")


def current_feedback() -> str:
    """直接读当前反馈文本(无前缀格式),供 agent 自定义格式时用."""
    return getattr(_local, "feedback", "") or ""
