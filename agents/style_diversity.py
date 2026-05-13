"""
StyleDiversityTracker —— 章节后扫描笔触指纹，下章 writer 用作 forbidden 输入。

不需要 LLM——纯文本启发式：
  · opening：首句或前 30 字
  · closing：末句或末 30 字
  · metaphors：识别"像 X 一样" / "如 X 般" / "仿佛 X" 抽出本体（X 部分）
  · transitions：高频过渡词（"忽然 / 蓦地 / 那一刻 / 与此同时 / 片刻后" 等）

写到 state.style_signature_history[ch_index]，写下章前由 director 取最近 N 章拼成
forbidden_content 注入 writer prompt。
"""
from __future__ import annotations
import re
from state import NovelState


_METAPHOR_PATTERNS = [
    re.compile(r"像([^，。！？\s]{2,12})一样"),
    re.compile(r"如([^，。！？\s]{2,12})般"),
    re.compile(r"如同([^，。！？\s]{2,12})"),
    re.compile(r"仿佛([^，。！？\s]{2,15})"),
    re.compile(r"宛如([^，。！？\s]{2,12})"),
]

_TRANSITION_TOKENS = [
    "忽然", "蓦地", "那一刻", "与此同时", "片刻后", "刹那", "倏然", "霎时",
    "随即", "紧接着", "下一秒", "话音未落", "话音方落", "言罢", "言毕",
    "话音刚落", "未几", "稍顷", "良久", "半晌",
]


def extract_signature(chapter_text: str) -> dict:
    """从章节正文抽笔触指纹——纯启发式，零 LLM 调用。"""
    if not chapter_text:
        return {"opening": "", "closing": "", "metaphors": [], "transitions": []}
    text = chapter_text.strip()
    # 去标题（如果首行像"第 X 章 XXX"）
    if text.startswith("第") and "章" in text[:30]:
        first_break = text.find("\n")
        if 0 < first_break < 40:
            text = text[first_break + 1:].lstrip()
    # 首句：到第一个句号/问号/感叹号止；保 30 字
    first_end = next((i for i, ch in enumerate(text[:80]) if ch in "。！？"), -1)
    opening = text[: first_end + 1] if first_end > 0 else text[:30]
    opening = opening.strip()[:40]
    # 末句：从末尾倒推
    tail = text[-200:]
    last_starts = [i for i, ch in enumerate(tail) if ch in "。！？"]
    closing = (tail[last_starts[-2] + 1:].strip() if len(last_starts) >= 2 else tail[-40:]).strip()[:40]
    # 比喻本体
    metaphors = []
    for pat in _METAPHOR_PATTERNS:
        for m in pat.findall(text):
            metaphors.append(m.strip())
    # 去重保留前 8
    seen = set(); deduped = []
    for m in metaphors:
        if m not in seen:
            seen.add(m); deduped.append(m)
    metaphors = deduped[:8]
    # 过渡词
    transitions = [tok for tok in _TRANSITION_TOKENS if text.count(tok) >= 1]
    return {
        "opening": opening,
        "closing": closing,
        "metaphors": metaphors,
        "transitions": transitions[:8],
    }


def record_chapter_signature(state: NovelState, chapter_index: int, chapter_text: str) -> None:
    """章节定稿后调用——抽指纹存到 state.style_signature_history。"""
    sig = extract_signature(chapter_text)
    state.style_signature_history[chapter_index] = sig


def title_signature(title: str) -> str:
    """标题去重指纹：去掉"第N章 "前缀，取核心 4 字 + 主要意象词。"""
    if not title:
        return ""
    # 去 "第 N 章 " 前缀
    title = re.sub(r"^第[一二三四五六七八九十百千零\d]+章[\s:：]*", "", title.strip())
    # 取前 4 字 + 全部独特"非常用字"作为指纹
    head = title[:4]
    # 简化：用前 4 字 + 长度作 signature
    return f"{head}|len{len(title)}"


def record_chapter_title(state: NovelState, chapter_index: int, title: str) -> None:
    state.used_titles_signature[chapter_index] = title_signature(title)


def recent_signatures(state: NovelState, current_chapter: int, n: int = 5) -> dict:
    """汇总近 N 章的笔触指纹——给 writer 作 forbidden 输入。"""
    keys = sorted([k for k in state.style_signature_history.keys() if k < current_chapter])[-n:]
    out = {"openings": [], "closings": [], "metaphors": set(), "transitions": set(), "titles": []}
    for k in keys:
        sig = state.style_signature_history.get(k, {})
        if sig.get("opening"): out["openings"].append(sig["opening"])
        if sig.get("closing"): out["closings"].append(sig["closing"])
        for m in sig.get("metaphors", []): out["metaphors"].add(m)
        for t in sig.get("transitions", []): out["transitions"].add(t)
    # 标题
    for k in sorted(state.used_titles_signature.keys())[-n:]:
        out["titles"].append(state.used_titles_signature[k])
    out["metaphors"] = sorted(out["metaphors"])
    out["transitions"] = sorted(out["transitions"])
    return out


def format_forbidden_block(state: NovelState, current_chapter: int, n: int = 5) -> str:
    """把近 N 章的指纹拼成一段适合塞进 writer prompt 的 forbidden 提示。"""
    sigs = recent_signatures(state, current_chapter, n=n)
    if not any([sigs["openings"], sigs["closings"], sigs["metaphors"], sigs["transitions"]]):
        return ""
    lines = [f"【近 {n} 章已用过的笔触——本章必须换花样，禁止复用以下任何一项】"]
    if sigs["openings"]:
        lines.append("章首句模式（不要再用类似开头）：")
        for o in sigs["openings"][-5:]:
            lines.append(f"  · {o}")
    if sigs["closings"]:
        lines.append("章末钩子模式（不要再用类似末句结构）：")
        for c in sigs["closings"][-5:]:
            lines.append(f"  · {c}")
    if sigs["metaphors"]:
        lines.append("已用过的比喻本体（不要再用 像/如/仿佛 + 这些本体）：")
        lines.append("  " + " / ".join(sigs["metaphors"][:15]))
    if sigs["transitions"]:
        lines.append("已频繁使用的过渡词（本章每个最多用 1 次）：")
        lines.append("  " + " / ".join(sigs["transitions"][:10]))
    return "\n".join(lines)
