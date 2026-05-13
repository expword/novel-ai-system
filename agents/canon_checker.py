"""
CanonCheckerAgent —— 设定护栏（确定性、无 LLM、快）。

写完一章后扫正文，检查"是否引用了未定义的专有名词"。
和 continuity_checker（LLM 语义检查）不同，本模块是纯字符串规则：
  · 抓 《...》 / 【...】 / 「...」 / 〔...〕 内的短语
  · 抓"...宗/门/派/帮/会/城/国/山/谷/洲"等常见命名后缀
  · 和 state 里已定义的【能力/境界/地名/势力/角色/术语】交叉比对
  · 不在已定义集合里的 → 发警告（LOW/MEDIUM 严重度）

警告不阻断流程——只汇总到 canon_audit 报告，让用户决定：
  · 是笔误/变体 → 手动并入 glossary
  · 是新设定 → 补进 state 的结构化 canon
  · 是真的破坏设定 → 重写本章

目的：让 writer 不能偷偷造词；让设定保持严格。
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from persistence.state import NovelState


# 中文专有名词的四种常见包裹符
_BRACKET_PATTERNS = [
    (r"《([^》《]{1,12})》",  "书名号"),
    (r"【([^】【]{1,12})】",  "方头括号"),
    (r"「([^」「]{1,12})」",  "日式引号"),
    (r"〔([^〕〔]{1,12})〕",  "六角括号"),
]

# 命名后缀启发 —— 用于抓取势力/地点类专有名词
_FACTION_SUFFIXES = ["宗", "门", "派", "会", "盟", "帮", "教", "殿", "阁", "楼", "堂", "阵营", "家族"]
_LOCATION_SUFFIXES = ["城", "山", "岭", "谷", "峰", "洲", "原", "国", "都", "府", "街", "镇", "村", "寨", "坊", "境"]
_ABILITY_SUFFIXES = ["诀", "咒", "术", "经", "典", "功", "法", "技", "奥义", "神通", "秘术"]

# 停用词——避免把普通词当成专有名词
_STOP_WORDS = {
    "一般", "现实", "故事", "主角", "师父", "师傅", "朋友", "仇敌", "世界", "天下", "修仙",
    "修真", "异能", "觉醒", "重生", "穿越", "任务", "系统", "能力", "技能", "实力", "境界",
    "大陆", "江湖", "天地", "天空", "大地", "山川", "河流", "城市", "学校", "公司", "医院",
    "此刻", "片刻", "顷刻", "须臾", "突然", "忽然", "刹那",
}


@dataclass
class CanonIssue:
    kind: str              # "ability" / "faction" / "region" / "character"
    term: str              # 原文出现的词
    context_snippet: str   # 上下文片段（30 字）
    severity: str          # "info" / "warn"
    suggestion: str        # 建议（如"在 power_system.special_abilities 中补充定义"）


def check_canon(state: NovelState, chapter_index: int, content: str) -> dict:
    """
    返回 {
      "issues": [CanonIssue 的 dict 形式],
      "stats":  {"new_abilities": N, "new_factions": N, "new_regions": N, "unknown_brackets": N}
    }
    issues 不阻断流程；由调用方决定如何展示。
    """
    # ── 构造已定义 canon 集合（规范名 + 别名）──
    known_abilities = set()
    known_realms = set()
    if state.power_system:
        for ab in state.power_system.special_abilities or []:
            known_abilities.add(ab.name)
        for r in state.power_system.realms or []:
            known_realms.add(r.name)

    known_regions = set()
    geo = state.geography
    if geo and geo.regions:
        for rg in geo.regions:
            known_regions.add(rg.name)

    known_factions = {f.name for f in (state.factions or [])}
    known_characters = {c.name for c in (state.characters or [])}
    known_glossary = set()
    for g in (state.glossary or []):
        known_glossary.add(g.term)
        known_glossary.update(g.aliases or [])

    # 总的"可接受"集合（术语表是大兜底）
    all_known = (
        known_abilities | known_realms | known_regions |
        known_factions | known_characters | known_glossary
    )

    issues: list[CanonIssue] = []
    stats = {
        "new_abilities": 0, "new_factions": 0, "new_regions": 0,
        "unknown_brackets": 0, "total_scanned": 0,
    }

    def _is_variant(term: str, known_set: set[str]) -> bool:
        """term 是否是已知名的变体（子串/超串）。"""
        if term in known_set:
            return True
        for name in known_set:
            if not name:
                continue
            # 变体判定：若 term 是 name 的子串（≥2 字）或反之，视为匹配
            if len(term) >= 2 and (term in name or name in term):
                return True
        return False

    def _snippet(term: str) -> str:
        idx = content.find(term)
        if idx < 0:
            return ""
        start = max(0, idx - 10)
        end = min(len(content), idx + len(term) + 10)
        return content[start:end].replace("\n", " ")

    # ── 1. 括号包裹的词 ──
    for pat, _kind in _BRACKET_PATTERNS:
        for m in re.finditer(pat, content):
            term = m.group(1).strip()
            if not term or term in _STOP_WORDS or len(term) < 2:
                continue
            stats["total_scanned"] += 1
            if _is_variant(term, all_known):
                continue
            # 按后缀推测类别
            if any(term.endswith(s) for s in _ABILITY_SUFFIXES):
                issues.append(CanonIssue(
                    kind="ability", term=term,
                    context_snippet=_snippet(term),
                    severity="warn",
                    suggestion="看似功法/技能但未在 power_system.special_abilities 定义；确认是笔误/新设定",
                ))
                stats["new_abilities"] += 1
            else:
                issues.append(CanonIssue(
                    kind="unknown_bracket", term=term,
                    context_snippet=_snippet(term),
                    severity="info",
                    suggestion="括号内专有名词但未登记——若是固定设定，建议补进 glossary",
                ))
                stats["unknown_brackets"] += 1

    # ── 2. 后缀命名法：2-3 字专有名词 + 典型后缀 ──
    # 边界放宽：句首/标点后/常见动词介词后（见/到/在/向/朝/往/进/入/出/至/是/道/的/为/被）
    BOUNDARY = r"(?:^|(?<=[，。！？、；：「」『』（）\s\"\'""''见到在向朝往进入出至是道的为被回返抵投奔过经离开了]))"

    # 势力：在子句边界后，紧接着 2-3 字汉字 + 后缀
    faction_pattern = BOUNDARY + r"([一-龥]{2,3})(" + "|".join(_FACTION_SUFFIXES) + r")"
    for m in re.finditer(faction_pattern, content):
        full = m.group(1) + m.group(2)
        if full in _STOP_WORDS or len(full) < 3:
            continue
        # 后缀后再来一个汉字 → 可能是词的一部分（如"宗师"/"门徒"），跳过
        end = m.end()
        if end < len(content) and re.match(r"[一-龥]", content[end]):
            tail = content[end]
            # 允许常见助词/方位词跟随："宗的"/"派内"/"殿门口"
            if tail not in "的之及与和或者也者们里内外上下中前后旁门口侧周围":
                continue
        stats["total_scanned"] += 1
        if _is_variant(full, known_factions | known_glossary):
            continue
        issues.append(CanonIssue(
            kind="faction", term=full,
            context_snippet=_snippet(full),
            severity="warn",
            suggestion="看似势力/组织但未在 factions/glossary 定义；若是新势力请补 state.factions",
        ))
        stats["new_factions"] += 1

    # 地点
    region_pattern = BOUNDARY + r"([一-龥]{2,3})(" + "|".join(_LOCATION_SUFFIXES) + r")"
    seen_regions = set()
    for m in re.finditer(region_pattern, content):
        full = m.group(1) + m.group(2)
        if full in seen_regions:
            continue
        seen_regions.add(full)
        if len(full) < 3 or full in _STOP_WORDS:
            continue
        end = m.end()
        if end < len(content) and re.match(r"[一-龥]", content[end]):
            tail = content[end]
            if tail not in "的之及与和或者也者们里内外上下脉":
                continue
        stats["total_scanned"] += 1
        if _is_variant(full, known_regions | known_glossary):
            continue
        issues.append(CanonIssue(
            kind="region", term=full,
            context_snippet=_snippet(full),
            severity="info",
            suggestion="看似地点但未在 geography.regions/glossary 定义；若是新场景请补 geography 或 glossary",
        ))
        stats["new_regions"] += 1

    # ── 3. 外接 LLM 的 asset 必须用 [[ASK_AI:..|..]] 占位（writer 不许脑补回答）──
    # 这一条是最严的：state 已登记 asset 有 external_llm_profile，正文出现 asset 名
    # 却没有任何 [[ASK_AI:asset_name|...]] 占位 → critical（error 级），触发 canon-revise。
    if state.power_system:
        _ASK_AI_PAT = re.compile(r"\[\[ASK_AI:([^|\]]+)\|[^\]]+\]\]")
        placeholders_used = {m.group(1).strip()
                              for m in _ASK_AI_PAT.finditer(content)}
        for ab in state.power_system.special_abilities or []:
            llm_profile = (ab.external_llm_profile or "").strip()
            if not llm_profile:
                continue  # 无外接 LLM 的 asset 跳过
            if not ab.name or ab.name not in content:
                continue  # 正文里没出现这个 asset，不用管
            if ab.name in placeholders_used:
                continue  # 已经用过占位符，OK
            # 出现了 asset 名但没用占位 → writer 自己编了内容
            issues.append(CanonIssue(
                kind="external_ai_no_placeholder",
                term=ab.name,
                context_snippet=_snippet(ab.name),
                severity="error",
                suggestion=(
                    f"《{ab.name}》绑定真 LLM（external_llm_profile={llm_profile}），"
                    f"主角与它的交互正文里必须用 [[ASK_AI:{ab.name}|具体问题]] 占位；"
                    "writer 不许自己编 AI 的回答。把'X 说...'/'X 告诉他...'"
                    f"改写成主角提问 + 占位 + 反应的形式。"
                ),
            ))

    # ── 4. 去重（同一个词只报一次，按首次出现）──
    seen = set()
    unique_issues = []
    for iss in issues:
        key = (iss.kind, iss.term)
        if key in seen:
            continue
        seen.add(key)
        unique_issues.append(iss)

    return {
        "issues": [iss.__dict__ for iss in unique_issues],
        "stats": stats,
        "chapter_index": chapter_index,
    }


def format_canon_report(report: dict, max_items: int = 8) -> str:
    """人眼可读的简报，给 director 日志用。"""
    issues = report.get("issues", [])
    if not issues:
        return ""
    by_kind: dict[str, list[dict]] = {}
    for iss in issues:
        by_kind.setdefault(iss["kind"], []).append(iss)
    lines = []
    for kind, items in by_kind.items():
        kind_label = {
            "ability": "未定义能力", "faction": "未定义势力",
            "region": "未定义地点", "unknown_bracket": "未登记括号词",
        }.get(kind, kind)
        sev_icon = "⚠" if any(i["severity"] == "warn" for i in items) else "·"
        terms_preview = " / ".join(i["term"] for i in items[:max_items])
        more = f"（另 {len(items) - max_items} 个）" if len(items) > max_items else ""
        lines.append(f"  {sev_icon} {kind_label}：{terms_preview}{more}")
    return "设定护栏报告：\n" + "\n".join(lines)
