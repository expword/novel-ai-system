"""
PlatformRulebook —— 加载并格式化平台读者偏好 rulebook。

立项时 (concept_pitch) 根据 state.concept_pitch.target_platform 加载一份
markdown rulebook,缓存到 state.platform_rules。

各 agent (writer / critic / chapter_planner) 通过 format_platform_block(state)
取一个简短摘要塞到 user prompt——而不是把完整 rulebook 注入(避免膨胀)。

支持的平台 (data/platform_rulebooks/):
  · 起点 / qidian
  · 晋江 / jjwxc
  · 番茄 / fanqie
  · 飞卢 / feilu
  · QQ阅读 / qqyuedu
  · 掌阅 / zhangyue
"""
from __future__ import annotations
import os


# 平台名 → rulebook 文件 base name 的映射 (中文 / 英文 / 拼音都接)
_PLATFORM_ALIASES: dict[str, str] = {
    "起点": "qidian", "起点中文网": "qidian", "qidian": "qidian",
    "晋江": "jjwxc", "晋江文学城": "jjwxc", "jjwxc": "jjwxc",
    "番茄": "fanqie", "番茄小说": "fanqie", "fanqie": "fanqie",
    "飞卢": "feilu", "飞卢小说": "feilu", "feilu": "feilu",
    "QQ阅读": "qqyuedu", "qq阅读": "qqyuedu", "QQyuedu": "qqyuedu", "qqyuedu": "qqyuedu",
    "掌阅": "zhangyue", "zhangyue": "zhangyue",
}


def _rulebook_dir() -> str:
    # data/platform_rulebooks/ 相对项目根目录
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "platform_rulebooks",
    )


def resolve_platform_alias(name: str) -> str:
    """把"起点中文网"等中文名归一为 rulebook 文件 base name (如 "qidian")。

    未匹配返回空串。
    """
    if not name:
        return ""
    key = name.strip()
    if key in _PLATFORM_ALIASES:
        return _PLATFORM_ALIASES[key]
    # 大小写不敏感二次尝试
    lower = key.lower()
    for k, v in _PLATFORM_ALIASES.items():
        if k.lower() == lower:
            return v
    return ""


def load_platform_rulebook(platform_name: str) -> str:
    """读 data/platform_rulebooks/<base>.md 返回原文。

    未匹配 / 文件缺失返回空串(不抛错——避免立项流程被中断)。
    """
    base = resolve_platform_alias(platform_name)
    if not base:
        return ""
    path = os.path.join(_rulebook_dir(), f"{base}.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def list_supported_platforms() -> list[str]:
    """返回所有支持的平台 base name (用于 UI 下拉)。"""
    seen = set()
    out = []
    for base in _PLATFORM_ALIASES.values():
        if base not in seen:
            seen.add(base)
            out.append(base)
    return out


def format_platform_block(state) -> str:
    """从 state.platform_rules 取规则,格式化为塞 prompt 的精简块。

    返回完整 markdown (rulebook 本身已经是 200 行以内),空规则时返回空串。
    """
    rules = getattr(state, "platform_rules", "") or ""
    if not rules.strip():
        return ""
    # 取平台名(从 concept_pitch)以便标注
    platform = ""
    try:
        platform = (state.concept_pitch.target_platform or "").strip()
    except Exception:
        pass
    header = f"═══ 📚 平台读者偏好({platform or '已加载'}) ═══\n"
    return header + rules.strip() + "\n"
