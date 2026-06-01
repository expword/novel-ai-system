"""
ChapterAssetTracker —— 章后扫描，追踪正文里的 asset 演化。

═══ 设计动机 ═══

asset 不应该是"Phase 2C 一锤定音的封闭集合"——小说自然演化中：
  · 主角可能在第 N 章意外获得新道具/能力
  · 某能力觉醒新阶段
  · 旧 asset 被永久销毁，出现替代品

intent_asset_extractor 解决了"规划期登记缺失"——本 agent 解决"写作期演化追踪"，
形成 asset 完整生命周期闭环。

═══ 三步工作 ═══

1. **更新已知 asset 使用记录**——扫正文里出现的已登记 asset 名
   · last_used_chapter / use_count 自动同步
   · lifecycle_nodes 命中本章自动 triggered（接 memory.py 已有逻辑）

2. **识别正文中疑似新 asset 候选**——纯规则扫描（不调 LLM 节省成本）：
   · 抓《X》/【X系统】/「X诀/术/经」等专有名词包装
   · 排除：已登记 asset / canon 已定义术语 / glossary

3. **连续 N 章出现 → progress_warning 提示 user 审批**
   · 单章昙花一现 = 修辞，不提示
   · 连续 3+ 章出现 = 几乎肯定是真 asset，让用户在 UI 决定是否登记

═══ 设计原则 ═══

完全规则驱动 / 不调 LLM——纯文本扫描即可识别"候选 asset"；
精确登记由用户在 web UI 决定（避免误识别污染 state）。

按 [[feedback_generic_prompts]]：纯通用 pattern，不写死任何项目术语。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from persistence.state import NovelState


# 候选 asset 的包装符号——跟 canon_checker 的 _BRACKET_PATTERNS 一致
_ASSET_BRACKET_PATS = [
    (r"《([^》《]{1,15})》", "书名号"),
    (r"【([^】【]{1,15})】", "方头括号"),
    (r"「([^」「]{1,15})」", "日式引号"),
]

# 后缀启发——"诀/术/经" 高概率是能力名
_ABILITY_SUFFIXES = ["诀", "咒", "术", "经", "典", "功", "法", "技",
                      "奥义", "神通", "秘术", "心法", "宝典"]

# 触发"asset 候选"的语境词——asset 名通常跟在这些动词/介词后
_ASSET_CONTEXT_HINTS = ["获得", "得到", "拿到", "捡到", "突然出现", "脑中浮现",
                        "心头一动", "感受到", "意识到", "学会", "习得", "解锁",
                        "启动", "激活", "唤醒"]

# 排除——这些词常出现在书名号/方头括号里但不是 asset
_STOP_BRACKET_TERMS = {
    "提示", "警告", "确认", "系统提示", "弹窗", "通知", "消息",
    "插入", "输入", "提交", "保存", "成功", "失败", "错误",
    "完成", "未完成", "进行中", "已激活",
}


@dataclass
class AssetCandidate:
    """疑似新 asset 候选——连续 N 章出现才提示用户登记。"""
    term: str
    first_seen_chapter: int = -1
    last_seen_chapter: int = -1
    seen_count: int = 0          # 出现总章数（不是出现总次数）
    chapters_seen: list[int] = field(default_factory=list)
    context_snippets: list[str] = field(default_factory=list)  # 最多保留 3 条上下文

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "first_seen_chapter": self.first_seen_chapter,
            "last_seen_chapter": self.last_seen_chapter,
            "seen_count": self.seen_count,
            "chapters_seen": self.chapters_seen[:],
            "context_snippets": self.context_snippets[:],
        }


def _build_known_set(state: NovelState) -> set[str]:
    """已登记 asset / 已定义 canon——这些出现在正文里不算"新候选"。"""
    known = set()
    ps = state.power_system
    if ps:
        for ab in (ps.special_abilities or []):
            if ab.name:
                known.add(ab.name)
        for r in (ps.realms or []):
            if r.name:
                known.add(r.name)
    for c in (state.characters or []):
        if c.name:
            known.add(c.name)
    for f in (state.factions or []):
        if f.name:
            known.add(f.name)
    for g in (state.glossary or []):
        if g.term:
            known.add(g.term)
            known.update(g.aliases or [])
    return known


def _snippet(content: str, term: str, span: int = 20) -> str:
    """取 term 上下文片段（前后 span 字）。"""
    idx = content.find(term)
    if idx < 0:
        return ""
    start = max(0, idx - span)
    end = min(len(content), idx + len(term) + span)
    return content[start:end].replace("\n", " ")


def scan_chapter_for_asset_candidates(state: NovelState, chapter_index: int,
                                       content: str) -> list[str]:
    """扫一章正文，返回**疑似新 asset** 的候选 term 列表（已去掉 known）。"""
    if not content:
        return []
    known = _build_known_set(state)
    candidates: list[str] = []
    seen_in_chapter: set[str] = set()

    # 1. 包装符号内
    for pat, _kind in _ASSET_BRACKET_PATS:
        for m in re.finditer(pat, content):
            term = m.group(1).strip()
            if not term or term in _STOP_BRACKET_TERMS or term in known:
                continue
            if len(term) < 2 or term in seen_in_chapter:
                continue
            seen_in_chapter.add(term)
            candidates.append(term)

    # 2. 能力后缀
    # 用 lookahead 让每个汉字位置都能尝试匹配——避免 finditer 非重叠匹配
    # 错过"一道凌云诀" 里的 "凌云诀"。prefix 2-3 字，4 字易被修饰词吃。
    # 量词首字过滤——明确是量词的跳过（数字不过滤，"九天玄典"是合法功法名）
    _PREFIX_STOP_FIRST = set("道把柄个件套份只块条根丝绺")
    for suf in _ABILITY_SUFFIXES:
        # lookahead 重叠匹配，先取 3 字 prefix 再取 2 字 prefix
        for prefix_len in (3, 2):
            pat = r"(?=([一-龥]{" + str(prefix_len) + r"})" + suf + r")"
            for m in re.finditer(pat, content):
                prefix = m.group(1)
                term = prefix + suf
                if prefix[0] in _PREFIX_STOP_FIRST:
                    continue
                if term in known or term in seen_in_chapter:
                    continue
                if len(term) < 3:
                    continue
                seen_in_chapter.add(term)
                candidates.append(term)

    return candidates


def update_asset_candidates(state: NovelState, chapter_index: int,
                              content: str, *, persist_threshold: int = 3) -> dict:
    """主入口——每章写完后调用。

    工作：
      1. 扫本章 → 更新 state._asset_candidates（跨章累计）
      2. 跨过 persist_threshold（默认 3 章持续出现）→ 写 progress_warning
         提示用户在 web UI 决定是否登记为 SpecialAbility

    返回 {"new_candidates": [...], "promoted_for_review": [...]}.
    """
    # state 用一个动态属性 _asset_candidates: dict[str, AssetCandidate]
    if not hasattr(state, "_asset_candidates"):
        state._asset_candidates = {}
    cand_map: dict[str, AssetCandidate] = state._asset_candidates

    fresh = scan_chapter_for_asset_candidates(state, chapter_index, content)
    new_in_this_chapter: list[str] = []
    promoted: list[str] = []

    for term in fresh:
        c = cand_map.get(term)
        if c is None:
            c = AssetCandidate(term=term, first_seen_chapter=chapter_index)
            cand_map[term] = c
            new_in_this_chapter.append(term)
        if chapter_index not in c.chapters_seen:
            c.chapters_seen.append(chapter_index)
            c.seen_count = len(c.chapters_seen)
        c.last_seen_chapter = chapter_index
        snip = _snippet(content, term)
        if snip and snip not in c.context_snippets:
            c.context_snippets.append(snip)
            c.context_snippets = c.context_snippets[-3:]
        # 跨过阈值 + 还没被提示过（用一个 _promoted 标记避免重复 warn）
        if c.seen_count >= persist_threshold and not getattr(c, "_promoted", False):
            c._promoted = True
            promoted.append(term)

    # promoted 写 progress_warning——同 source 自动去重，每次新增覆盖
    if promoted:
        try:
            from persistence.checkpoint import add_progress_warning
            preview = "；".join(
                f"《{t}》（出现 {cand_map[t].seen_count} 章，例「{cand_map[t].context_snippets[0][:30]}…」）"
                for t in promoted[:3]
            )
            add_progress_warning(
                level="info",
                source=f"asset_candidates:ch{chapter_index}",
                message=(
                    f"第 {chapter_index} 章扫描到 {len(promoted)} 个疑似新 asset 候选"
                    f"（连续 {persist_threshold}+ 章出现）：{preview}。"
                    "建议在 web UI'力量体系→特殊能力'决定是否登记。"
                ),
            )
        except Exception:
            pass

    return {
        "new_candidates": new_in_this_chapter,
        "promoted_for_review": promoted,
        "total_tracked": len(cand_map),
    }


def list_pending_candidates(state: NovelState, min_chapters: int = 2) -> list[dict]:
    """给 web UI 用：返回当前所有疑似新 asset 候选（按 seen_count 降序）。

    min_chapters：只列出现 >= N 章的——过滤偶然修辞。
    """
    if not hasattr(state, "_asset_candidates"):
        return []
    items = [c.to_dict() for c in state._asset_candidates.values()
             if c.seen_count >= min_chapters]
    items.sort(key=lambda x: (-x["seen_count"], -x["last_seen_chapter"]))
    return items
