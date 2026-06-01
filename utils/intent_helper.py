"""
intent_helper — 让每个生成类 agent 看见用户的原始创作意图（一段自然语言原话）。

# 解决的问题
用户在 web UI 「用一段自然语言描述你想写什么」表单里填了一段原话
（state.creative_intent.raw_description）。下游各 phase agent 跑生成时应该
直接看用户原话——而不是看 intent_analyzer 把原话翻译后的二手分类标签
（villain_policy_hint='灰色模糊型' 等）——因为原话保留了用户的语气和细节。

# 长原话怎么办
用户可能写一段很长的原话（设定文档 5000 字+）。直接全量塞每个 phase prompt
既浪费 token 又稀释焦点。本模块的策略：

  · 短原话（≤3000 字）：直接全量塞——没必要再调 LLM 切片
  · 长原话（>3000 字）：调 agents/intent_excerpt_extractor 智能体，按 agent_name
    从原话里摘出与该 phase 相关的段落，只把切片塞下游 prompt

智能体提取的结果带 runtime cache——同一会话内同 (raw, agent) 不重复跑。

# 用法（不变）
    from utils.intent_helper import build_intent_brief
    intent_brief = build_intent_brief(state, "character_designer")
    prompt = f"\""{intent_brief}
    ... 原 prompt
    "\""
"""
import hashlib


# 超过这个字数走智能体提取；以下直接全量塞
_INLINE_LIMIT = 3000

# 提取失败时的回退：截原话前 N 字塞下去（避免长原话项目完全没原话引导）
_FALLBACK_TRUNCATE = 1500

# runtime cache: (raw_hash, agent_name) -> excerpt
# 同一会话内同 raw 同 agent 只提取一次；进程重启清空；raw 变了 hash 变自动失效
_excerpt_cache: dict[tuple[str, str], str] = {}


def build_intent_brief(state, agent_name: str = "") -> str:
    """返回"用户原话"前缀块。

    无 raw_description 时返回空字符串——拼到 f-string 的 f"\""{intent_brief}..."\"" 安全。

    短原话直接全量塞；长原话走智能体提取——如果原话里没明确提到本 agent 相关内容，
    智能体返回空，本函数也返回空（让下游 agent 按流程自由发挥）。
    """
    ci = getattr(state, "creative_intent", None)
    if not ci:
        return ""
    raw = (getattr(ci, "raw_description", "") or "").strip()
    if not raw:
        return ""

    if len(raw) <= _INLINE_LIMIT:
        # 短原话：直接全量塞
        excerpt = raw
        source_note = ""
    else:
        # 长原话：调智能体提取
        excerpt = _get_or_extract_excerpt(raw, agent_name)
        if not excerpt:
            # 智能体判断原话里没提到本 agent 相关内容——不塞 brief，让下游按流程走
            return ""
        source_note = f"（用户原话共 {len(raw)} 字，已由提取智能体摘出与本步骤相关段落）"

    suffix = f"\n{source_note}" if source_note else ""
    return (
        "═══ 用户原话（用户在「用一段自然语言描述你想写什么」里写的原始描述——"
        "优先尊重，在此基础上优化；如果原话里没明确提到本步要设计的内容，则按流程自由发挥）═══\n"
        f"{excerpt}{suffix}\n"
        "═══════════════════════════════════════\n\n"
    )


def _get_or_extract_excerpt(raw: str, agent_name: str) -> str:
    """带 cache 调智能体；失败回退到原话前 _FALLBACK_TRUNCATE 字。"""
    raw_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    key = (raw_hash, agent_name)
    if key in _excerpt_cache:
        return _excerpt_cache[key]

    try:
        from agents.intent_excerpt_extractor import extract_for_agent
        excerpt = extract_for_agent(raw, agent_name)
    except Exception as e:
        # 提取失败回退——让下游 agent 至少能看到部分用户意图，不要完全失去引导
        print(f"  [intent_extract] {agent_name} 提取失败，回退到原话前 {_FALLBACK_TRUNCATE} 字: "
              f"{type(e).__name__}: {e}")
        excerpt = raw[:_FALLBACK_TRUNCATE]

    _excerpt_cache[key] = excerpt
    return excerpt


def clear_excerpt_cache() -> None:
    """清空 runtime cache（测试用 / 用户改了 raw_description 想立即重提取时手动调）。"""
    _excerpt_cache.clear()
