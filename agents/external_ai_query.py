"""
ExternalAIQuery —— 当主角在小说正文里"问 AI"（豆包/系统/搜索引擎等）时，
**真的把问题发给一个 LLM**拿回答，不让 writer 自己编造。

工作流程：
  1. user 在 web UI 给某个 SpecialAbility 设了 external_llm_profile（如 "doubao"）
  2. writer 写到主角问该能力的场景，用 [[ASK_AI:能力名|问题文本]] 占位
  3. 章节定稿前，本模块扫描占位 → 用绑定的 profile 真发问 → 替换为答案

═══ 两种模式 ═══

**in-story 模式**（默认，推荐）：传 state + ability 给 query_real_ai。
本模块**动态从 state 构造 system prompt**——告诉 AI 它扮演什么 in-story 角色、
当前题材/时代/朝代是什么、不要暴露真实模型身份、不要用现代品牌/版本号/营销话术、
不要带 emoji / markdown / "我是 X 助手"等元语言。所有上下文从 state 取，
不写死任何项目术语（朝代名 / 题材 / 桥段——见 [[feedback_generic_prompts]]）。
回答返回前还会跑 sanitize_ai_answer 二次净化。

**raw 模式**（向后兼容）：不传 state/ability 时启用——只发问题文本，
LLM 原生输出。早期设计沿用，新代码应优先用 in-story 模式。
"""
from __future__ import annotations
import re
from typing import Optional


# 占位格式：[[ASK_AI:能力名|问题文本]]
# 能力名/问题里允许中文/英文/数字/符号，但不能含 | 和 ]]
_ASK_PATTERN = re.compile(r"\[\[ASK_AI:([^|\]]+)\|([^\]]+)\]\]")


# 真 AI 回答里出现的"元语言/免责声明"——这些是 LLM 自身的现代身份/能力说明，
# 直接嵌入古风/穿越/玄幻小说正文会破坏沉浸感（读者会发现"这真的是 AI"，
# 让金手指设定崩坏）。canon_checker 抓不到（不是术语问题），单独审一遍。
_AI_META_HINTS = [
    # 身份自述
    "我是一个 AI", "我是一个AI", "我是 AI", "我是AI",
    "作为 AI", "作为AI", "作为一个 AI", "作为一个AI",
    "AI 助手", "AI助手", "智能助手", "语言模型", "大语言模型",
    "I am an AI", "As an AI", "as an AI", "language model",
    # 能力/数据声明
    "我的训练数据", "训练数据中", "我的知识截止", "知识截止",
    "由于我的训练", "我无法访问", "我没有访问",
    "不在我的训练", "超出我的能力",
    # 免责/拒答（少量合理但量多就有问题）
    "请咨询专业", "请寻求专业", "建议咨询医生", "建议咨询律师",
    "我不能提供", "我无法提供", "免责声明", "仅供参考",
    # 时空错乱（古代/穿越场景里 AI 提"现代"会出戏）
    "现代社会", "在现代", "21世纪", "21 世纪", "互联网上",
]


class ExternalAIResolutionError(RuntimeError):
    """真 AI 占位解析失败。携带 reports，供 director 写进 progress warning。"""

    def __init__(self, message: str, reports: list[dict]):
        super().__init__(message)
        self.reports = reports


def build_in_story_system_prompt(state, ability) -> str:
    """**动态从 state 构造** in-story system prompt——告诉 LLM 它在小说里扮演什么角色。

    所有信息从 state / ability 字段动态取，**不写死任何项目特定术语**
    （按 [[feedback_generic_prompts]]）。同一函数适用于豆包/系统/搜索引擎/水晶球等
    任何 in-story 真 AI asset，无论穿越/玄幻/科幻/都市题材。
    """
    # ─ 1. in-story 身份（asset 名 + 描述 + 来源）─
    ability_name = (getattr(ability, "name", "") or "").strip() or "AI"
    ability_desc = (getattr(ability, "description", "") or "").strip()
    ability_source = (getattr(ability, "source", "") or "").strip()

    identity_lines = [f"你在一本小说里扮演一个名为「{ability_name}」的 in-story 智能体。"]
    if ability_desc:
        identity_lines.append(f"你的 in-story 设定：{ability_desc[:200]}")
    if ability_source:
        identity_lines.append(f"你的 in-story 来源：{ability_source[:120]}")

    # ─ 2. 题材 + 时代背景（从 state 动态取）─
    background_lines = []
    genre = (getattr(state, "genre", "") or "").strip()
    if genre:
        background_lines.append(f"小说题材：{genre}")
    wc = getattr(state, "world_canon", None)
    if wc:
        epoch_bits = []
        if wc.dynasty_name:
            epoch_bits.append(f"朝代={wc.dynasty_name}")
        if wc.era_name:
            epoch_bits.append(f"年号={wc.era_name}")
        if wc.region_root:
            epoch_bits.append(f"地点={wc.region_root}")
        if wc.epoch_summary:
            epoch_bits.append(f"时代={wc.epoch_summary}")
        if epoch_bits:
            background_lines.append("故事时空：" + " | ".join(epoch_bits))

    # ─ 3. 主角名（让 AI 知道是谁在问，但不强加视角）─
    proto = None
    for c in (getattr(state, "characters", []) or []):
        role = getattr(getattr(c, "role", None), "value", "")
        if role == "主角":
            proto = c.name
            break
    if proto:
        background_lines.append(f"提问者：小说主角「{proto}」")

    background = "\n".join(background_lines)

    # ─ 4. 输出风格铁律（这部分**不依赖项目术语**——是 in-story AI 的通用原则）─
    style_rules = [
        "═══ 输出风格铁律 ═══",
        "1. **不要暴露真实模型身份**——不说'我是 DeepSeek/Claude/ChatGPT/GPT/豆包大模型/AI 助手/语言模型'，"
        f"   只以 in-story 名「{ability_name}」自称（或用'本工具/此设定'等中性指代）。",
        "2. **不要提任何现代品牌/版本号/平台/官方应用商店**——'128K 上下文 / 训练数据 2024 年 / 联网搜索 /"
        "   官方 App / 免费使用 / 上传文件'等产品话术绝对禁止。",
        "3. **不要带 emoji**（💬📁🔍✨😊 等）——任何场景都不要。",
        "4. **不要用 markdown 列表语法**（`- 项目` / `* 项目` / `1. 项目` 等）——直接用自然语句。",
        '5. **不要说「我的知识截止 / 训练数据」**——in-story 设定里没有这种概念。'
        '如果不知道就直接说不知道，不要解释原因。',
        '6. **不要说「作为 AI」/「请咨询专业医生律师」/「仅供参考」/「免责声明」** 等元语言。',
        '7. 回答**符合提问者所在时空的语言风格**——古风背景用书面文言半白话，现代背景用普通话。',
        '8. 只答**现代真实世界的知识/普世原理**——本书虚构设定（朝代具体律法/虚构人名/本地行情/未来预言）'
        '不在你的能力范围，直接说「此事我答不出」或「超出我能告知的范围」。',
        '9. 不要为凑字数 padding——简短直接。3-5 段即可，每段 2-4 句。',
    ]

    sections = [
        "\n".join(identity_lines),
    ]
    if background:
        sections.append("═══ 故事背景（in-story 上下文）═══\n" + background)
        sections.append(
            "能力边界说明：以上故事背景只用于语气和沉浸感，不是可查询数据库。"
            "你不得根据这些背景编造当地律法、人名背景、债契真伪、本地行情、阴谋底牌或未来走向。"
        )
    sections.append(
        "金手指硬边界：你不能直接变出物品，不能操控现实，不能替主角执行行动，"
        "不能预知古代具体人事。你只能提供现代真实世界的普世原理、公式、计算方法、"
        "图纸思路、风险模型和策略建议。凡涉及本书虚构专名、当地事实或具体人物判断，"
        "必须回答此事无法直接判断，需要主角提供账册、样本、律文或观察记录后，再按现代方法分析。"
    )
    sections.append("\n".join(style_rules))
    sections.append(
        f"现在主角在小说情节中向你「{ability_name}」发问——按上述铁律答。\n"
        "你的回答会被直接嵌入小说正文，**任何破坏沉浸感的话都会让整本书崩**。"
    )
    return "\n\n".join(sections)


# ───── 输出净化：剥离漏网的元语言 / emoji / markdown ─────
# system prompt 是第一道防线（让 LLM 不要产出这些）；这里是第二道（即便产出也剥除）

# emoji 范围（覆盖常见输出 emoji）
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"   # 表情/符号
    "\U0001F600-\U0001F64F"   # 笑脸
    "\U0001F680-\U0001F6FF"   # 交通/地图
    "\U0001F900-\U0001F9FF"   # 补充符号
    "☀-⛿"           # 杂项符号
    "✀-➿"           # 装饰符号
    "✨💬📱📁🔍✅❌⚠️📝🎯🚀💡"
    "]+",
    flags=re.UNICODE,
)

# 段首 markdown 列表标记（- / * / 1. 2. 等）
_MD_BULLET_PATTERN = re.compile(r"^[\s]*(?:[-*•・]|\d+[\.、])\s+", flags=re.MULTILINE)


def sanitize_ai_answer(answer: str) -> str:
    """第二道防线——剥离漏网的 emoji、markdown 列表、元语言开头段。

    保持文本主体不变，只去掉破坏沉浸感的字符级噪音。**不改写语义**——
    语义层面靠 system prompt 兜底；这里只做安全字符过滤。
    """
    if not answer:
        return answer
    txt = answer

    # 1. emoji 全删
    txt = _EMOJI_PATTERN.sub("", txt)

    # 2. markdown 列表段首标记替换为破折号（保持可读性、去掉 markdown 痕迹）
    txt = _MD_BULLET_PATTERN.sub("", txt)

    # 3. 删开头段如果只是元语言自我介绍（"你好！我是 X..."）
    lines = txt.split("\n")
    cleaned = []
    skipped_intro = False
    for i, ln in enumerate(lines):
        l = ln.strip()
        if not skipped_intro and i < 3 and l:
            # 这一行是否纯粹自我介绍 / 产品介绍开场白
            intro_markers = ["你好！我是", "你好，我是", "我是一个", "作为一个", "作为 AI", "作为AI",
                             "我是 DeepSeek", "我是DeepSeek", "我是 Claude", "我是Claude",
                             "我是 GPT", "我是GPT", "我是 ChatGPT", "我是ChatGPT",
                             "Hello, I'm", "Hi, I'm", "I am an AI"]
            if any(l.startswith(m) for m in intro_markers):
                skipped_intro = True
                continue
        cleaned.append(ln)
    txt = "\n".join(cleaned)

    # 4. 折叠多余空行
    txt = re.sub(r"\n{3,}", "\n\n", txt)

    return txt.strip()


def audit_ai_answer(answer: str, max_hits: int = 5) -> list[str]:
    """扫描真 AI 回答里的元语言/免责声明/时空错乱词。

    返回命中关键词列表（去重，按出现顺序）。空列表 = 干净。
    调用方决定如何处理（打 log / 写 progress warning / 触发回答改写）。
    """
    if not answer:
        return []
    hits = []
    seen = set()
    for hint in _AI_META_HINTS:
        if hint in answer and hint not in seen:
            seen.add(hint)
            hits.append(hint)
            if len(hits) >= max_hits:
                break
    return hits


def _fiction_terms_from_state(state) -> list[str]:
    terms: list[str] = []
    wc = getattr(state, "world_canon", None)
    if wc:
        for attr in ("dynasty_name", "era_name", "region_root"):
            v = (getattr(wc, attr, "") or "").strip()
            if v:
                terms.append(v)
    for c in (getattr(state, "characters", []) or []):
        name = (getattr(c, "name", "") or "").strip()
        if name:
            terms.append(name)
    for f in (getattr(state, "factions", []) or []):
        name = (getattr(f, "name", "") or "").strip()
        if name:
            terms.append(name)
    return [t for t in dict.fromkeys(terms) if len(t) >= 2]


def validate_in_story_ai_question(question: str, state=None) -> None:
    """Block questions that ask the external AI to know fictional local facts.

    Allowed questions are about transferable modern methods: principles,
    formulas, diagrams, calculation procedures, and strategy models.  A question
    about a specific local person/document/law must be reframed as a method
    question before it can be sent to the real LLM.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("外部 AI 问题为空")

    allowed_method = [
        "原理", "公式", "方法", "流程", "模型", "图纸", "结构", "计算", "算法",
        "会计", "记账", "统计", "概率", "力学", "化学", "冶金", "工程", "策略",
        "博弈", "风险模型", "鉴别方法", "分析方法", "验证步骤",
    ]
    forbidden_fact = [
        "法律效力", "律法", "条款", "本地行情", "行情", "是谁", "身份", "背景",
        "底牌", "阴谋", "未来", "预知", "家族标记", "对应", "可雇佣性",
        "接触风险", "真伪", "藏在哪里", "会不会", "能不能成功",
    ]
    asks_local_fact = any(x in q for x in forbidden_fact)
    asks_method = any(x in q for x in allowed_method)
    fiction_terms = _fiction_terms_from_state(state) if state is not None else []
    mentions_fiction = any(t in q for t in fiction_terms)

    if asks_local_fact and not asks_method:
        raise ValueError(
            "问题越过金手指边界：外部 AI 不能判断古代本地事实/人物/律法/未来，"
            "只能问现代原理、公式、图纸、流程或策略方法"
        )
    if mentions_fiction and asks_local_fact:
        raise ValueError(
            "问题包含本书虚构专名并要求事实判断；请改成“给出某类问题的现代分析方法/验证步骤”"
        )


def query_real_ai(profile_id: str, question: str, *,
                  state=None, ability=None,
                  max_tokens: int = 2000, timeout: float = 90.0) -> str:
    """用 user_models.json 里 id=profile_id 的 LLM 发问。

    **模式选择**：
      · state + ability 都传 → **in-story 模式**：用 build_in_story_system_prompt 构造
        动态 system prompt（题材/朝代/角色身份从 state 取，零硬编码），回答经
        sanitize_ai_answer 净化后返回。新代码默认走这条
      · 任一为 None → **raw 模式**：保留旧行为，只发 question，LLM 原生输出
        （向后兼容；不建议新代码用——会有现代品牌话术污染）

    返回 LLM 回答。失败时 raise RuntimeError。
    """
    from openai import OpenAI
    from llm_layer import user_models as _um
    entry = _um.get(profile_id, include_key=True)
    if not entry:
        fallback = _um.find_by_usage("in_story_ai")
        if fallback:
            old = profile_id
            profile_id = fallback.get("id") or profile_id
            entry = _um.get(profile_id, include_key=True) or fallback
            print(f"  ⚠ 外部 AI profile {old!r} 不存在，改用 in_story_ai={profile_id!r}")
        else:
            raise RuntimeError(
                f"profile_id={profile_id!r} 不在 user_models.json，且没有任何模型勾选 in_story_ai，"
                "无法发外部 AI 询问"
            )
    api_key = entry.get("api_key", "") or ""
    base_url = entry["base_url"]
    model = entry["model"]
    extra_body = entry.get("extra_body") or {}

    # 选择模式：in-story 还是 raw
    messages = []
    in_story = state is not None and ability is not None
    if in_story:
        validate_in_story_ai_question(question, state=state)
        system_prompt = build_in_story_system_prompt(state, ability)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body
    response = client.chat.completions.create(**kwargs)
    if isinstance(response, str):
        raw_answer = response.strip()
    elif isinstance(response, dict):
        choices = response.get("choices") or []
        msg = choices[0].get("message") if choices else {}
        raw_answer = (msg.get("content") or "").strip() if isinstance(msg, dict) else ""
    else:
        raw_answer = (response.choices[0].message.content or "").strip()

    # in-story 模式跑第二道净化（剥 emoji / markdown 列表 / 元语言开场白）
    if in_story:
        return sanitize_ai_answer(raw_answer)
    return raw_answer


def find_asks(chapter_text: str) -> list[tuple[str, str]]:
    """扫描正文里所有 [[ASK_AI:能力名|问题]] 占位，返回 [(能力名, 问题), ...]。"""
    return [(m.group(1).strip(), m.group(2).strip())
            for m in _ASK_PATTERN.finditer(chapter_text or "")]


def resolve_asks_in_chapter(
    state,
    chapter_text: str,
    asks: Optional[list[tuple[str, str]]] = None,
) -> tuple[str, list[dict]]:
    """
    把章节正文里所有 [[ASK_AI:能力名|问题]] 替换成真实 LLM 回答。
    返回 (新章节文本, 报告列表)。
    报告项：{"ability": ..., "profile": ..., "question": ..., "answer": ..., "ok": True|False, "error": ""}
    """
    if not chapter_text:
        return chapter_text, []

    asks = asks if asks is not None else find_asks(chapter_text)
    if not asks:
        return chapter_text, []

    # 把能力名 → (external_llm_profile, ability 对象) 映射出来。
    # 这里优先重读磁盘上的最新 state：写章子进程可能已经运行很久，
    # 而作者会在 Web 端临时改 SpecialAbility.external_llm_profile。
    # 如果继续用子进程启动时的旧内存，就会出现"Web 明明改了模型，实际仍打旧 profile"。
    profile_state = state
    try:
        from persistence.checkpoint import load_state
        latest_state = load_state()
        if latest_state and latest_state.power_system:
            profile_state = latest_state
    except Exception:
        profile_state = state

    # 同时保留 ability 对象本身——传给 query_real_ai 后启用 in-story 模式
    ability_to_meta = {}
    if profile_state.power_system and profile_state.power_system.special_abilities:
        for ab in profile_state.power_system.special_abilities:
            if ab.name and ab.external_llm_profile:
                ability_to_meta[ab.name] = (ab.external_llm_profile, ab)

    reports = []

    # 用 set 去重——同一问题可能被多处占位引用，这里只发一次
    seen = {}  # (ability, question) -> answer
    for ability_name, question in asks:
        if not ability_name or not question:
            continue
        key = (ability_name, question)
        if key in seen:
            continue
        meta = ability_to_meta.get(ability_name)
        if not meta:
            reports.append({
                "ability": ability_name, "profile": "", "question": question,
                "answer": "", "ok": False,
                "error": f"能力《{ability_name}》没绑外部 LLM profile（external_llm_profile 为空）"
            })
            seen[key] = None
            continue
        profile_id, ability_obj = meta
        try:
            # in-story 模式：传 state + ability，让 query_real_ai 构造动态 system prompt
            # + 输出净化，避免现代品牌话术 / emoji / markdown 污染正文
            answer = query_real_ai(profile_id, question, state=state, ability=ability_obj)
            seen[key] = answer
            # 第三道防线——审一遍回答里漏网的元语言。
            # system prompt + sanitize 之后理论上应该清零，meta_hits 仍非空说明 LLM 没听 system，
            # 写 progress_warning 让用户察觉
            meta_hits = audit_ai_answer(answer)
            reports.append({
                "ability": ability_name, "profile": profile_id, "question": question,
                "answer": answer, "ok": True, "error": "",
                "meta_hits": meta_hits,
            })
        except Exception as e:
            seen[key] = None
            reports.append({
                "ability": ability_name, "profile": profile_id, "question": question,
                "answer": "", "ok": False, "error": f"{type(e).__name__}: {e}",
                "meta_hits": [],
            })

    failed = [r for r in reports if not r.get("ok")]
    if failed:
        preview = "；".join(
            f"《{r.get('ability','?')}》{(r.get('error') or '')[:80]}"
            for r in failed[:3]
        )
        raise ExternalAIResolutionError(
            f"{len(failed)} 个真 AI 占位解析失败：{preview}",
            reports,
        )

    # 把所有占位替换为答案；失败会在上面抛错，避免把错误文本写进正文定稿。
    def _repl(m):
        ability = m.group(1).strip()
        question = m.group(2).strip()
        ans = seen.get((ability, question))
        if ans:
            return ans
        return m.group(0)

    new_text = _ASK_PATTERN.sub(_repl, chapter_text)
    return new_text, reports
