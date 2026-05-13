"""
SensitiveFilter —— 敏感词过滤。

网文平台对敏感词有严格限制。这个 agent 在 Critic 之后、定稿前跑：
1. 本地扫描：拿 config.SENSITIVE_WORDS + 可选外部词库，硬匹配
2. LLM 辅助：扫一些"上下文敏感"的词（同样的字，A 语境 OK，B 语境不行）

命中后：
- 轻度：直接替换为 * 或换词
- 中度：在返回里列出，让 director 决定要不要 revise
- 重度：强制 revise
"""
from __future__ import annotations
import re
from typing import Optional

try:
    from config import SENSITIVE_WORDS, SENSITIVE_REPLACEMENTS
except ImportError:
    SENSITIVE_WORDS = []
    SENSITIVE_REPLACEMENTS = {}


def scan_sensitive(content: str, extra_words: Optional[list] = None) -> dict:
    """
    扫描内容里的敏感词。
    返回 {"hits": [{"word": w, "count": n, "positions": [...]}], "severity": "none|minor|major"}
    """
    words = list(SENSITIVE_WORDS) + (extra_words or [])
    if not words:
        return {"hits": [], "severity": "none", "total_count": 0}

    hits = []
    for w in words:
        if not w:
            continue
        positions = [m.start() for m in re.finditer(re.escape(w), content)]
        if positions:
            hits.append({
                "word": w,
                "count": len(positions),
                "positions": positions[:5],  # 保留前 5 个位置供定位
            })

    total = sum(h["count"] for h in hits)
    if total == 0:
        severity = "none"
    elif total <= 3:
        severity = "minor"
    elif total <= 10:
        severity = "major"
    else:
        severity = "critical"
    return {"hits": hits, "severity": severity, "total_count": total}


def auto_replace(content: str, extra_replacements: Optional[dict] = None) -> tuple[str, int]:
    """
    自动替换敏感词（只替换 SENSITIVE_REPLACEMENTS 里明确定义的）。
    返回 (新 content, 替换次数)。
    """
    replacements = dict(SENSITIVE_REPLACEMENTS)
    if extra_replacements:
        replacements.update(extra_replacements)
    if not replacements:
        return content, 0

    count = 0
    for src, dst in replacements.items():
        if not src:
            continue
        new_content, n = re.subn(re.escape(src), dst, content)
        if n:
            content = new_content
            count += n
    return content, count


def filter_and_report(content: str) -> dict:
    """
    一站式：扫描 + 自动替换（对可替换的直接替换），返回最终的 content 和处理报告。
    调用方根据 severity 决定下一步：
      - none: 直接定稿
      - minor: 已自动替换，可以直接定稿
      - major/critical: 触发 revise，让 writer 重写
    """
    # 1. 先做自动替换
    new_content, replaced = auto_replace(content)
    # 2. 再扫一遍，看还剩什么没处理的
    scan = scan_sensitive(new_content)

    report = {
        "final_content": new_content,
        "replaced_count": replaced,
        "remaining_hits": scan["hits"],
        "severity": scan["severity"],
        "total_remaining": scan["total_count"],
    }
    return report


def format_report(report: dict) -> str:
    """控制台打印用的简报。"""
    if report["severity"] == "none" and report["replaced_count"] == 0:
        return ""
    parts = []
    if report["replaced_count"]:
        parts.append(f"自动替换{report['replaced_count']}处")
    if report["total_remaining"]:
        words = " / ".join(h["word"] for h in report["remaining_hits"][:5])
        parts.append(f"剩余{report['total_remaining']}处敏感词（{words}）[{report['severity']}]")
    return "；".join(parts)
