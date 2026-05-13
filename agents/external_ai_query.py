"""
ExternalAIQuery —— 当主角在小说正文里"问 AI"（豆包/系统/搜索引擎等）时，
**真的把问题发给一个 LLM**拿回答，不让 writer 自己编造。

工作流程：
  1. user 在 web UI 给某个 SpecialAbility 设了 external_llm_profile（如 "doubao"）
  2. writer 写到主角问该能力的场景，用 [[ASK_AI:能力名|问题文本]] 占位
  3. 章节定稿前，本模块扫描占位 → 用绑定的 profile 真发问 → 替换为答案

按用户严格要求：**只发问题文本，不带任何 system prompt 或前置上下文**——
保持 LLM 真实输出，不让"提示词工程"污染答案。
"""
from __future__ import annotations
import re
from typing import Optional


# 占位格式：[[ASK_AI:能力名|问题文本]]
# 能力名/问题里允许中文/英文/数字/符号，但不能含 | 和 ]]
_ASK_PATTERN = re.compile(r"\[\[ASK_AI:([^|\]]+)\|([^\]]+)\]\]")


def query_real_ai(profile_id: str, question: str, *, max_tokens: int = 2000,
                  timeout: float = 90.0) -> str:
    """
    用 user_models.json 里 id=profile_id 的 LLM 真的发一个问题。
    **只发问题本身**，不带 system prompt——按用户要求保持 LLM 原生输出。
    返回 LLM 的 content；失败时 raise RuntimeError。
    """
    from openai import OpenAI
    import user_models as _um
    entry = _um.get(profile_id, include_key=True)
    if not entry:
        raise RuntimeError(f"profile_id={profile_id!r} 不在 user_models.json，无法发外部 AI 问询")
    api_key = entry.get("api_key", "") or ""
    base_url = entry["base_url"]
    model = entry["model"]
    extra_body = entry.get("extra_body") or {}

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": question}],  # 只发问题，无 system
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


def find_asks(chapter_text: str) -> list[tuple[str, str]]:
    """扫描正文里所有 [[ASK_AI:能力名|问题]] 占位，返回 [(能力名, 问题), ...]。"""
    return [(m.group(1).strip(), m.group(2).strip())
            for m in _ASK_PATTERN.finditer(chapter_text or "")]


def resolve_asks_in_chapter(state, chapter_text: str) -> tuple[str, list[dict]]:
    """
    把章节正文里所有 [[ASK_AI:能力名|问题]] 替换成真实 LLM 回答。
    返回 (新章节文本, 报告列表)。
    报告项：{"ability": ..., "profile": ..., "question": ..., "answer": ..., "ok": True|False, "error": ""}
    """
    if not chapter_text:
        return chapter_text, []

    asks = find_asks(chapter_text)
    if not asks:
        return chapter_text, []

    # 把能力名 → external_llm_profile 映射出来
    ability_to_profile = {}
    if state.power_system and state.power_system.special_abilities:
        for ab in state.power_system.special_abilities:
            if ab.name and ab.external_llm_profile:
                ability_to_profile[ab.name] = ab.external_llm_profile

    reports = []
    new_text = chapter_text

    # 用 set 去重——同一问题可能被多处占位引用，这里只发一次
    seen = {}  # (ability, question) -> answer
    for ability_name, question in asks:
        if not ability_name or not question:
            continue
        key = (ability_name, question)
        if key in seen:
            continue
        profile_id = ability_to_profile.get(ability_name)
        if not profile_id:
            reports.append({
                "ability": ability_name, "profile": "", "question": question,
                "answer": "", "ok": False,
                "error": f"能力《{ability_name}》没绑外部 LLM profile（external_llm_profile 为空）"
            })
            seen[key] = None
            continue
        try:
            answer = query_real_ai(profile_id, question)
            seen[key] = answer
            reports.append({
                "ability": ability_name, "profile": profile_id, "question": question,
                "answer": answer, "ok": True, "error": "",
            })
        except Exception as e:
            seen[key] = None
            reports.append({
                "ability": ability_name, "profile": profile_id, "question": question,
                "answer": "", "ok": False, "error": f"{type(e).__name__}: {e}",
            })

    # 把所有占位替换为答案——失败的占位替换为 [AI 答询失败：err] 让用户能看到
    def _repl(m):
        ability = m.group(1).strip()
        question = m.group(2).strip()
        ans = seen.get((ability, question))
        if ans:
            return ans
        # 失败时保留可读痕迹，但不留占位（避免被当作"未处理"再次扫描）
        rep = next((r for r in reports if r["ability"] == ability and r["question"] == question), None)
        err = (rep["error"] if rep else "未知") if rep else "未知"
        return f"【{ability} 答询失败：{err[:60]}】"

    new_text = _ASK_PATTERN.sub(_repl, chapter_text)
    return new_text, reports
