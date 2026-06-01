"""
LLM 调用 facade —— 按任务类型自动路由到合适的 usage profile。

═══ 解决的问题 ═══

之前各 agent 调 LLM 散乱：
  · 写作 agent：from llm_layer.llm import system_user, chat
  · 规划 agent：from utils.json_utils import request_json   # 走 main
  · 抽取 agent：request_json_with_profile("extractor", ...)
  · 审核 agent：request_json_with_profile("reviewer", ...)
  · in-story AI：直接 from llm_layer 自己手写

每个 agent 自己决定走哪个 profile + 自己处理 fallback——重复、易错、
"哪些 agent 该走 extractor"这种知识散在各文件 docstring 里。

═══ 抽象 ═══

按**任务类型**统一接口：

  call_for_task(task, system, user, **kwargs)       → str（自由文本输出）
  request_json_for_task(task, system, user, ...)    → dict（结构化 JSON 输出）

  task 取值：
    "writing"     长文本创作（writer / 扩写 / 改写）
    "planning"    结构化规划（chapter_planner / line_planner / ability_planner 等）
    "extraction"  从自然语言抽结构化字段（world_canon / intent_asset / 章后能力时间线）
    "review"      审核打分（critic / reader_audit / dialogue_audit / canon 修订反馈）
    "in_story_ai" 角色扮演（主角金手指真 AI 对话）—— 需显式传 state + ability

  自动按 TASK_USAGE 表映射到 user_models usage：
    writing/planning → "main"
    extraction → "extractor"（没绑自动 fallback main）
    review → "reviewer"
    in_story_ai → 由 ability.external_llm_profile 决定

═══ 向后兼容 ═══

不破坏旧 API（request_json / chat_with_profile）—— 旧 agent 不需要改。
新 agent 默认用这层 facade；老 agent 渐进迁移。

═══ 设计原则 ═══

· task 是抽象的（"我要写章" / "我要抽字段"），usage 是路由细节
· agent 不应该关心"我现在用哪个 profile"——只声明"我做什么任务"
· 未来加新 task type（如 "summarization"）只改本文件一处
"""
from __future__ import annotations
from typing import Optional, Callable, Any

# ─── 任务类型 → usage 映射 ───
TASK_USAGE: dict[str, str] = {
    "writing":     "main",       # 只跑 writer 写正文，独占 main——保证写作质量
    "planning":    "planner",    # 所有规划 agent（chapter_planner / line_planner / etc）独立 profile；
                                  # 没绑自动 fallback main
    "extraction":  "extractor",  # 抽取，没绑自动 fallback main
    "review":      "reviewer",   # 审核
    # in_story_ai 没固定 usage——由 ability.external_llm_profile 决定（每个 asset 可绑不同）
}

TASK_DESCRIPTIONS: dict[str, str] = {
    "writing":     "长文本创作（writer 写章正文 / 扩写 / 改写）—— main 模型，需要文笔",
    "planning":    "结构化规划（chapter_planner / line_planner / ability_planner / "
                    "twist_designer / 等所有'先想后写'agent）—— planner 模型，"
                    "需要推理 + 严格 JSON。没绑 fallback main",
    "extraction":  "从自然语言抽结构化字段（world_canon / intent_asset / 章后能力时间线）"
                    "—— extractor 模型，便宜轻量。没绑 fallback main",
    "review":      "审核打分（critic / reader_audit / dialogue_audit）—— reviewer 模型，便宜快",
    "in_story_ai": "角色扮演（主角金手指真 AI 对话）—— 需显式传 ability，"
                    "走 ability.external_llm_profile",
}


def _resolve_usage(task: str) -> str:
    """task → usage。未知 task 抛 ValueError——避免静默走 main。"""
    if task not in TASK_USAGE:
        raise ValueError(
            f"未知 task 类型: {task!r}。已支持: {list(TASK_USAGE.keys())}。"
            "in_story_ai 请用 agents.external_ai_query.query_real_ai。"
        )
    usage = TASK_USAGE[task]
    if usage is None:
        raise ValueError(
            f"task {task} 需要显式指定 profile（如 in_story_ai）——"
            "不要用 call_for_task；改用专用 API"
        )
    return usage


# ═══════════════════════════════════════════════════════════════
#  自由文本（chat 风格）
# ═══════════════════════════════════════════════════════════════

def call_for_task(
    task: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
    agent_name: str = "",
) -> str:
    """按任务类型发自由文本对话。

    内部走 llm_layer.llm.chat_with_profile(usage_from_task)。
    chat_with_profile 在 profile_id 是 usage 名时会自动 find_by_usage——找不到
    fallback 到 main（见 request_json_with_profile 的自动 fallback 实现）。
    """
    from llm_layer.llm import chat_with_profile
    usage = _resolve_usage(task)
    label = agent_name or f"call_for[{task}]"
    return chat_with_profile(
        usage,
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )


# ═══════════════════════════════════════════════════════════════
#  结构化 JSON
# ═══════════════════════════════════════════════════════════════

def request_json_for_task(
    task: str,
    system: str,
    user: str,
    *,
    required_keys: Optional[list[str]] = None,
    list_candidates: Optional[list[str]] = None,
    min_items: int = 1,
    max_retries: int = 3,
    temperature: float = 0.4,
    agent_name: str = "",
    empty_ok: bool = True,
    example_schema: Optional[str] = None,
) -> dict:
    """按任务类型发结构化 JSON 请求。

    内部走 utils.json_utils.request_json_with_profile(usage_from_task)——
    带 schema 校验 + 重试 + temperature 递减 + (extractor profile 缺失) 自动 fallback main。
    """
    from utils.json_utils import request_json_with_profile
    usage = _resolve_usage(task)
    label = agent_name or f"json_for[{task}]"
    return request_json_with_profile(
        usage,
        system=system, user=user,
        required_keys=required_keys,
        list_candidates=list_candidates,
        min_items=min_items,
        max_retries=max_retries,
        temperature=temperature,
        agent_name=label,
        empty_ok=empty_ok,
        example_schema=example_schema,
    )


# ═══════════════════════════════════════════════════════════════
#  查询：当前 task 路由生效情况（供 web UI 显示 + 调试）
# ═══════════════════════════════════════════════════════════════

def get_task_routing_summary() -> dict[str, dict]:
    """返回每个 task type 当前实际走的 profile—— web 端可显示
    "writing 当前生效 = deepseek_v4_pro / extraction 当前生效 = (未配置→fallback main)"。
    """
    from llm_layer import user_models
    out = {}
    for task, usage in TASK_USAGE.items():
        if usage is None:
            out[task] = {
                "usage": None,
                "active_profile": None,
                "note": TASK_DESCRIPTIONS.get(task, ""),
            }
            continue
        active = user_models.find_by_usage(usage)
        out[task] = {
            "usage": usage,
            "active_profile": active.get("id") if active else None,
            "active_model": active.get("model") if active else None,
            "fallback_used": active is None and usage != "main",
            "fallback_to": (user_models.find_by_usage("main") or {}).get("id")
                            if active is None else None,
            "note": TASK_DESCRIPTIONS.get(task, ""),
        }
    return out
