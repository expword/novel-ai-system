"""
大模型 Profile 注册表 —— 市面上常见模型的预设。

每个 profile 字段：
  display_name   — 显示名（UI 用）
  provider       — 厂商（用于分组）
  base_url       — OpenAI 兼容的 chat/completions 端点（去掉 /chat/completions 后缀）
  model          — 传给 API 的 model 名
  env_key        — 从哪个环境变量读 API key
  max_output     — 单次输出 token 上限（影响 writer 分场景阈值）
  context_window — 上下文窗口大小（仅作参考）
  notes          — 备注说明

绝大多数国内厂商都支持 OpenAI 兼容接口，基本可以即插即用。
少数不兼容的（如 Anthropic 原生）可以走聚合网关（如 yunwu.ai / openrouter）。
"""

# 用户当前 yunwu 聚合网关（从 config.py 继承 API_KEY 和 BASE_URL）
_YUNWU_BASE = "https://yunwu.ai/v1"
_YUNWU_KEY = "YUNWU_API_KEY"

PROFILES = {
    # ═══════════════════════════════════════════════════
    #  DeepSeek（推荐——性价比最高）
    # ═══════════════════════════════════════════════════
    "deepseek-chat": {
        "display_name": "DeepSeek V3 Chat",
        "provider": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "max_output": 8000,
        "context_window": 128000,
        "notes": "综合能力强，价格便宜；推荐长篇默认选它",
    },
    "deepseek-reasoner": {
        "display_name": "DeepSeek R1 Reasoner",
        "provider": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-reasoner",
        "env_key": "DEEPSEEK_API_KEY",
        "max_output": 8000,
        "context_window": 64000,
        "notes": "带推理过程；适合复杂规划（卷结构/冲突阶梯等）",
    },

    # ═══════════════════════════════════════════════════
    #  OpenAI
    # ═══════════════════════════════════════════════════
    "gpt-4o": {
        "display_name": "GPT-4o",
        "provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "max_output": 16000,
        "context_window": 128000,
        "notes": "旗舰，中英文都强；价格较高",
    },
    "gpt-4o-mini": {
        "display_name": "GPT-4o mini",
        "provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "max_output": 16000,
        "context_window": 128000,
        "notes": "便宜快速，适合批量章节",
    },
    "gpt-4-turbo": {
        "display_name": "GPT-4 Turbo",
        "provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4-turbo",
        "env_key": "OPENAI_API_KEY",
        "max_output": 4096,
        "context_window": 128000,
    },

    # ═══════════════════════════════════════════════════
    #  Anthropic Claude（通过兼容网关）
    # ═══════════════════════════════════════════════════
    "claude-3-5-sonnet": {
        "display_name": "Claude 3.5 Sonnet",
        "provider": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
        "env_key": "ANTHROPIC_API_KEY",
        "max_output": 8192,
        "context_window": 200000,
        "notes": "文笔细腻，适合写作；原生 API 不兼容 OpenAI，需用支持的网关",
    },
    "claude-3-opus": {
        "display_name": "Claude 3 Opus",
        "provider": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-opus-20240229",
        "env_key": "ANTHROPIC_API_KEY",
        "max_output": 4096,
        "context_window": 200000,
        "notes": "旗舰版，创作能力极强；成本最高",
    },

    # ═══════════════════════════════════════════════════
    #  Moonshot（月之暗面 Kimi）
    # ═══════════════════════════════════════════════════
    "moonshot-v1-8k": {
        "display_name": "Moonshot v1 8K",
        "provider": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "env_key": "MOONSHOT_API_KEY",
        "max_output": 4096,
        "context_window": 8192,
    },
    "moonshot-v1-32k": {
        "display_name": "Moonshot v1 32K",
        "provider": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "env_key": "MOONSHOT_API_KEY",
        "max_output": 4096,
        "context_window": 32768,
    },
    "moonshot-v1-128k": {
        "display_name": "Moonshot v1 128K",
        "provider": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-128k",
        "env_key": "MOONSHOT_API_KEY",
        "max_output": 4096,
        "context_window": 131072,
        "notes": "超长上下文，适合全书规划",
    },

    # ═══════════════════════════════════════════════════
    #  智谱 GLM
    # ═══════════════════════════════════════════════════
    "glm-4-plus": {
        "display_name": "GLM-4 Plus",
        "provider": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "env_key": "ZHIPU_API_KEY",
        "max_output": 4095,
        "context_window": 128000,
    },
    "glm-4-air": {
        "display_name": "GLM-4 Air",
        "provider": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-air",
        "env_key": "ZHIPU_API_KEY",
        "max_output": 4095,
        "context_window": 128000,
        "notes": "便宜快速版",
    },
    "glm-4-long": {
        "display_name": "GLM-4 Long",
        "provider": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-long",
        "env_key": "ZHIPU_API_KEY",
        "max_output": 4095,
        "context_window": 1000000,
        "notes": "百万级上下文",
    },

    # ═══════════════════════════════════════════════════
    #  阿里 Qwen（通义千问）
    # ═══════════════════════════════════════════════════
    "qwen-plus": {
        "display_name": "Qwen Plus",
        "provider": "阿里",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "env_key": "DASHSCOPE_API_KEY",
        "max_output": 8000,
        "context_window": 131072,
    },
    "qwen-max": {
        "display_name": "Qwen Max",
        "provider": "阿里",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "env_key": "DASHSCOPE_API_KEY",
        "max_output": 8000,
        "context_window": 32768,
        "notes": "通义千问旗舰",
    },
    "qwen-long": {
        "display_name": "Qwen Long",
        "provider": "阿里",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-long",
        "env_key": "DASHSCOPE_API_KEY",
        "max_output": 8000,
        "context_window": 10000000,
        "notes": "千万级上下文",
    },

    # ═══════════════════════════════════════════════════
    #  01.AI Yi
    # ═══════════════════════════════════════════════════
    "yi-large": {
        "display_name": "Yi Large",
        "provider": "01.AI",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-large",
        "env_key": "YI_API_KEY",
        "max_output": 4096,
        "context_window": 32768,
    },
    "yi-medium": {
        "display_name": "Yi Medium",
        "provider": "01.AI",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "model": "yi-medium",
        "env_key": "YI_API_KEY",
        "max_output": 4096,
        "context_window": 16384,
    },

    # ═══════════════════════════════════════════════════
    #  百川 Baichuan
    # ═══════════════════════════════════════════════════
    "baichuan4": {
        "display_name": "Baichuan 4",
        "provider": "百川",
        "base_url": "https://api.baichuan-ai.com/v1",
        "model": "Baichuan4",
        "env_key": "BAICHUAN_API_KEY",
        "max_output": 4096,
        "context_window": 32768,
    },

    # ═══════════════════════════════════════════════════
    #  字节豆包 Doubao
    # ═══════════════════════════════════════════════════
    "doubao-pro-32k": {
        "display_name": "Doubao Pro 32K",
        "provider": "字节",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-32k",
        "env_key": "DOUBAO_API_KEY",
        "max_output": 4096,
        "context_window": 32768,
        "notes": "字节豆包；需要在火山方舟创建 endpoint",
    },

    # ═══════════════════════════════════════════════════
    #  聚合网关（云雾——当前用的）
    # ═══════════════════════════════════════════════════
    "yunwu-deepseek-chat": {
        "display_name": "🌈 云雾-DeepSeek Chat",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "deepseek-chat",
        "env_key": _YUNWU_KEY,
        "max_output": 8000,
        "context_window": 128000,
        "notes": "通过云雾聚合网关访问 DeepSeek（国内无需翻墙）",
    },
    "yunwu-gpt-4o": {
        "display_name": "🌈 云雾-GPT-4o",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "gpt-4o",
        "env_key": _YUNWU_KEY,
        "max_output": 16000,
        "context_window": 128000,
    },
    "yunwu-claude-3-5-sonnet": {
        "display_name": "🌈 云雾-Claude 3.5 Sonnet",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "claude-3-5-sonnet-20241022",
        "env_key": _YUNWU_KEY,
        "max_output": 8192,
        "context_window": 200000,
    },
    "yunwu-claude-3-opus": {
        "display_name": "🌈 云雾-Claude 3 Opus",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "claude-3-opus-20240229",
        "env_key": _YUNWU_KEY,
        "max_output": 4096,
        "context_window": 200000,
    },
    "yunwu-gemini-1-5-pro": {
        "display_name": "🌈 云雾-Gemini 1.5 Pro",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "gemini-1.5-pro",
        "env_key": _YUNWU_KEY,
        "max_output": 8192,
        "context_window": 2000000,
    },
    # 审核专用——章节审核智能体用 Gemini Flash Lite（性价比高，快）
    "yunwu-gemini-3-1-flash-lite": {
        "display_name": "🔍 云雾-Gemini 3.1 Flash Lite（审核专用）",
        "provider": "云雾聚合",
        "base_url": _YUNWU_BASE,
        "model": "gemini-3.1-flash-lite-preview",
        "env_key": "YUNWU_REVIEWER_KEY",  # 独立 key，和主 LLM 分开，不互相抢额度
        "max_output": 4096,
        "context_window": 1000000,
        "notes": "章节合规审核专用模型——快速、便宜、有大上下文可塞整个设定",
    },

    # ═══════════════════════════════════════════════════
    #  OpenRouter（国际聚合）
    # ═══════════════════════════════════════════════════
    "openrouter-default": {
        "display_name": "🌐 OpenRouter (自选模型)",
        "provider": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o",
        "env_key": "OPENROUTER_API_KEY",
        "max_output": 8000,
        "context_window": 128000,
        "notes": "需要在 profile 里自己改 model 字段；支持上百种模型",
    },
}


# 默认 profile——用户首次启动时的选择
DEFAULT_PROFILE_ID = "yunwu-deepseek-chat"


def list_providers() -> list[str]:
    """返回所有不同的 provider 名，供 UI 分组。"""
    seen = []
    for p in PROFILES.values():
        if p["provider"] not in seen:
            seen.append(p["provider"])
    return seen


def profiles_by_provider() -> dict:
    """返回 {provider: [ (id, profile), ... ]}，供 UI 下拉分组展示。"""
    grouped = {}
    for pid, prof in PROFILES.items():
        grouped.setdefault(prof["provider"], []).append((pid, prof))
    return grouped


def get(profile_id: str) -> dict:
    """按 id 取 profile。不存在返回默认。"""
    return PROFILES.get(profile_id, PROFILES[DEFAULT_PROFILE_ID])
