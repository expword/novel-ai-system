"""
JSON parsing utilities — robust extraction from LLM output.

三层防护：
1. llm.chat 已处理网络/5xx 重试（5 次指数退避）
2. 本模块 request_json 处理结构校验 + 带错误反馈的重试（最多 max_retries 次）
3. 调用方传 fallback，校验彻底失败时返回占位数据
"""
import json
import re
from typing import Callable, Optional


def pick_list(data: dict, *candidates) -> list:
    """
    容错取列表——LLM 输出的 key 名可能漂移（volumes / volume_list / items / 等）。
    依次尝试 candidates；如果都不存在：
      - data 本身是列表 → 返回它
      - data 里只有一个值是列表 → 返回那个
      - 否则返回空列表
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in candidates:
        v = data.get(key)
        if isinstance(v, list):
            return v
    # 兜底：找第一个是列表的 value
    list_values = [v for v in data.values() if isinstance(v, list)]
    if len(list_values) == 1:
        return list_values[0]
    return []


def extract_json(text: str) -> str:
    """从LLM输出中提取JSON字符串，处理各种格式。"""
    # 优先处理 ```json ... ``` 代码块
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1)
        # fallback：找第一个 { 到最后一个 }
        start = text.find("{", text.find("```"))
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return text[start:end]

    # 直接找最外层 { ... }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]

    return text


def safe_parse(text: str, fallback: dict = None) -> dict:
    """提取并解析JSON，失败返回fallback。"""
    try:
        return json.loads(extract_json(text))
    except (json.JSONDecodeError, ValueError):
        return fallback or {}


class LLMStructureError(Exception):
    """LLM 输出结构校验失败——经过重试仍不合规。"""


def request_json(
    system: str,
    user: str,
    *,
    required_keys: Optional[list[str]] = None,
    list_candidates: Optional[list[str]] = None,
    min_items: int = 1,
    item_required_keys: Optional[list[str]] = None,
    custom_validator: Optional[Callable[[dict], tuple[bool, str]]] = None,
    max_retries: int = 5,
    temperature: float = 0.7,
    agent_name: str = "",
    empty_ok: bool = False,
    example_schema: Optional[str] = None,
) -> dict:
    """
    带校验+反馈+重试的 JSON LLM 请求。

    重试策略：
    - 每次重试把上次的错误和 JSON 片段反馈进 prompt，让 LLM 看到自己的问题
    - 温度递减：每次 -0.1（更保守），下限 0.2
    - 最后一轮：加入最严格的指令和 schema 示例（如提供）
    - 重试全部耗尽：
        - 若 empty_ok=True → 返回空 dict（调用方按"跳过本层"处理）
        - 否则 → 抛 LLMStructureError（让用户看到真问题）

    【刻意不做】：不会产出假内容/占位数据冒充真实结果——宁可报错也不污染小说。

    参数：
      required_keys        必须存在的顶层 key
      list_candidates      期望取一个列表时的候选 key
      min_items            列表最少项数
      item_required_keys   列表每项必须包含的 key
      custom_validator     额外校验函数 (data) -> (ok, err_msg)
      max_retries          最多重试次数（含首次），默认 5
      temperature          初始温度（每次重试递减 0.1，下限 0.2）
      empty_ok             True=允许返回空 dict 作为"跳过"信号；False=失败时抛错
      example_schema       可选，最后一轮兜底时贴给 LLM 作为参考的最小合法 JSON 示例
    """
    from llm import system_user

    last_err = ""
    last_raw = ""

    for attempt in range(1, max_retries + 1):
        # 温度递减
        cur_temp = max(0.2, temperature - 0.1 * (attempt - 1))

        # 构造本轮 prompt
        if attempt == 1:
            prompt = user
        elif attempt < max_retries:
            prompt = (
                f"{user}\n\n"
                f"═══ 上次输出不合格，请修正后重新输出完整 JSON ═══\n"
                f"上次错误：{last_err}\n"
                f"上次输出片段：{last_raw[:300]}\n"
                f"请严格按要求输出合法 JSON，不要添加解释性文字。"
            )
        else:
            # 最后一轮：最严格的提示 + schema 示例（如提供）
            schema_hint = f"\n【最小合法示例】\n{example_schema}\n" if example_schema else ""
            prompt = (
                f"{user}\n\n"
                f"═══ 最后一次机会：请必须产出合法 JSON ═══\n"
                f"前几次错误：{last_err}\n"
                f"上次输出片段：{last_raw[:300]}\n"
                f"现在请：\n"
                f"1. 只输出 JSON 对象，不要任何其他文字\n"
                f"2. 所有字符串必须用双引号，不得用单引号\n"
                f"3. 所有 key 名必须严格对齐 schema\n"
                f"4. 列表不得为空（除非 schema 明确允许）\n"
                f"{schema_hint}"
            )

        try:
            raw = system_user(system, prompt, temperature=cur_temp)
        except Exception as e:
            last_err = f"LLM 调用异常：{type(e).__name__}:{str(e)[:100]}"
            print(f"  ⚠ [{agent_name}] 第{attempt}/{max_retries}次 {last_err}")
            continue

        last_raw = raw
        data = repair_json(raw)

        ok, err = _validate(
            data, required_keys, list_candidates, min_items,
            item_required_keys, custom_validator
        )
        if ok:
            if attempt > 1:
                print(f"  ✓ [{agent_name}] 第{attempt}次重试通过（temp={cur_temp:.2f}）")
            return data

        last_err = err
        print(f"  ⚠ [{agent_name}] 第{attempt}/{max_retries}次校验失败（temp={cur_temp:.2f}）：{err}")

    # 全部失败
    msg = (
        f"[{agent_name}] 重试 {max_retries} 次仍无法得到合规 JSON。"
        f"最后错误：{last_err}。LLM原文前300字：{last_raw[:300]}"
    )
    if empty_ok:
        print(f"  ✗ {msg}")
        print(f"    → 允许空结果（empty_ok=True），本层规划跳过")
        return {}
    raise LLMStructureError(msg)


def request_json_with_profile(
    profile_id: str,
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
    """
    指定 profile 的 request_json——用于审核/反思等需要独立模型的 agent。

    与 request_json 的区别：
      - 走 llm.chat_with_profile（用指定 profile_id + 独立 API key）
      - 默认 empty_ok=True, max_retries=3（审核失败不该阻断主流程）
    """
    from llm import chat_with_profile

    last_err = ""
    last_raw = ""

    for attempt in range(1, max_retries + 1):
        cur_temp = max(0.2, temperature - 0.05 * (attempt - 1))

        if attempt == 1:
            prompt = user
        else:
            schema_hint = f"\n【示例 schema】\n{example_schema}\n" if example_schema else ""
            prompt = (
                f"{user}\n\n"
                f"═══ 上次输出不合格 ═══\n"
                f"错误：{last_err}\n"
                f"上次片段：{last_raw[:300]}\n"
                f"请严格输出合法 JSON，不要加解释文字。{schema_hint}"
            )

        try:
            raw = chat_with_profile(
                profile_id,
                [{"role": "system", "content": system},
                 {"role": "user", "content": prompt}],
                temperature=cur_temp,
                max_tokens=4096,
                max_retries=1,  # 外层已经有重试循环
            )
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:100]}"
            print(f"  ⚠ [{agent_name}] 第{attempt}/{max_retries}次 调用失败：{last_err}")
            continue

        last_raw = raw
        data = repair_json(raw)

        ok, err = _validate(data, required_keys, list_candidates, min_items, None, None)
        if ok:
            return data

        last_err = err
        print(f"  ⚠ [{agent_name}] 第{attempt}/{max_retries}次校验失败：{err}")

    if empty_ok:
        print(f"  ✗ [{agent_name}] 审核 {max_retries} 次都失败——返回空结果跳过")
        return {}
    raise LLMStructureError(f"[{agent_name}] 审核失败：{last_err}")


def _validate(
    data: dict,
    required_keys: Optional[list[str]],
    list_candidates: Optional[list[str]],
    min_items: int,
    item_required_keys: Optional[list[str]],
    custom_validator: Optional[Callable[[dict], tuple[bool, str]]],
) -> tuple[bool, str]:
    """返回 (通过, 错误消息)"""
    if not isinstance(data, dict) and not isinstance(data, list):
        return False, "LLM 输出不是合法 JSON 对象或数组"

    # 顶层必需 key
    if required_keys:
        if not isinstance(data, dict):
            return False, f"期望顶层是对象含 {required_keys}，但得到 {type(data).__name__}"
        missing = [k for k in required_keys if k not in data]
        if missing:
            return False, f"缺少顶层字段：{missing}"

    # 列表校验
    if list_candidates:
        items = pick_list(data if isinstance(data, dict) else {}, *list_candidates) \
            if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if len(items) < min_items:
            return False, (f"期望字段 {list_candidates} 对应的列表至少 {min_items} 项，"
                           f"实际 {len(items)} 项")
        if item_required_keys:
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    return False, f"第 {i} 个列表项不是对象"
                missing = [k for k in item_required_keys if k not in it]
                if missing:
                    return False, f"第 {i} 项缺少字段：{missing}"

    # 自定义校验
    if custom_validator:
        try:
            ok, err = custom_validator(data)
            if not ok:
                return False, err
        except Exception as e:
            return False, f"custom_validator 异常：{e}"

    return True, ""


def repair_json(text: str) -> dict:
    """
    尝试修复常见的JSON截断问题：
    - 数组末尾缺少 ]
    - 对象末尾缺少 }
    """
    raw = extract_json(text)

    # 先尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 修复截断的JSON：补全缺失的括号
    open_braces = raw.count("{") - raw.count("}")
    open_brackets = raw.count("[") - raw.count("]")

    # 找到最后一个完整的数组元素（以 } 结尾的位置）
    last_valid = raw.rfind("}")
    if last_valid > 0:
        truncated = raw[:last_valid + 1]
        # 补全括号
        truncated += "]" * open_brackets + "}" * open_braces
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    return {}


def run_chapter_audit(
    *,
    chapter_index: int,
    chapter_text: str,
    system: str,
    user: str,
    required_keys: list,
    agent_label: str,
    temperature: float = 0.4,
    max_retries: int = 2,
) -> Optional[tuple]:
    """
    章节级 auditor 共用 pipeline：空文本 → None / 请求 / 异常吞掉打日志 / 解析失败 → None。
    各 auditor（ability/dialogue/reader_experience）只负责拼 prompt + 解析 data + 构造 dataclass。
    返回 (data: dict, ts: str, profile_id: str) 或 None。
    """
    if not chapter_text or not chapter_text.strip():
        return None
    try:
        data = request_json(
            system=system, user=user,
            required_keys=required_keys,
            max_retries=max_retries,
            temperature=temperature,
            agent_name=f"{agent_label}[Ch{chapter_index}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  [{agent_label.lower()}] 第 {chapter_index} 章审计失败：{type(e).__name__}: {e}")
        return None
    if not data:
        return None
    from datetime import datetime
    from llm_runtime import resolve_profile
    try:
        profile_id = resolve_profile().get("id", "")
    except Exception:
        profile_id = ""
    ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return data, ts, profile_id
