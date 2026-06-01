"""
AnachronismDetector —— 穿越/重生题材的时代断代检查（章后审计）。

═══ 解决的问题 ═══

穿越/重生题材里,主角带着现代知识穿到古代。最常见破绽是:
  · 主角对古人**直接**说"GDP / 微积分 / 量子 / 区块链 / 微博"等现代术语
  · 主角心理戏用"PUA / 内卷 / 卷王 / 躺平"等当代网络用语

注意:**主角解释科学原理本身是合法的**(如真 AI 金手指可以告知原理)——破绽
不是"提到现代知识",而是"直接用现代术语对古人说话/古人立即理解"——这违反
戏剧形式(应该用古人能理解的表述如"算学之道")。

canon_checker 抓的是设定违规(本书内 canon),AnachronismDetector 抓的是
真实世界时代错位——两者正交。

═══ 双阶段降本 ═══

· 阶段 1: 正则 fast filter 扫常见现代词,无命中 → 不调 LLM 直接通过
· 阶段 2: 命中 → LLM 判定"是否真违和"(戏剧形式自由的合理场景不算违规)

═══ 设计原则 ═══

· 只在 reality_basis 涉及穿越/重生时启用(其他题材不调用)
· LLM 失败 → 不报警(避免阻塞)
· 按 [[feedback_generic_prompts]] —— prompt 通用,不针对具体项目
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="anachronism_detector.audit_chapter",
    inputs=[
        "creative_intent.reality_basis",
        "creative_intent.suggested_subgenre",
    ],
    outputs=[
        # + progress_warning(chapter:N:anachronism)
    ],
    invariants=[],
    notes=(
        "穿越/重生题材的时代断代检查。先 regex fast filter,命中才 LLM 判定。"
        "走 extractor usage,失败不报警。"
    ),
))


# ═══════════════════════════════════════════════════════
#  Fast filter —— 现代词正则(命中才调 LLM)
# ═══════════════════════════════════════════════════════

# 注意:这些词在正文出现"不一定"违规——主角解释原理可能合法
# 仅作为"需要进一步判定"的触发器
_MODERN_TERMS = [
    # 科学技术
    "微积分", "量子", "区块链", "DNA", "基因", "细胞", "分子", "原子",
    "电池", "电脑", "电视", "电话", "网络", "互联网", "数据库", "算法",
    # 经济金融
    "GDP", "股票", "基金", "通货膨胀", "央行", "上市", "IPO", "ETF",
    # 当代网络/社会
    "微博", "微信", "抖音", "短视频", "直播", "网红", "PUA", "内卷",
    "躺平", "卷王", "996", "打工人", "社畜", "韭菜", "凡尔赛",
    # 现代政治/管理
    "民主", "宪法", "议会", "选举", "总统", "总理", "联合国",
    # 现代医学
    "抗生素", "疫苗", "X光", "CT", "核磁共振", "病毒",
    # 现代度量
    "公里", "千米", "公斤", "千克", "毫升", "千卡", "千瓦",
    # P1-3 旁白/环境描写现代意象(古风世界最易出现的破代入)
    # 注意:此列表针对穿越/重生类古代背景,自动过滤民国可能合法的词
    "霓虹灯", "摩天", "写字楼", "办公室",
    "地铁", "公交", "汽车", "飞机", "红绿灯",
    "操场", "校园", "小区", "商场",
]

# 编译为大型正则(单 pass 扫描)
_MODERN_RE = re.compile("|".join(re.escape(t) for t in _MODERN_TERMS))


SYSTEM = """你是【时代断代守门员】——专精识别穿越/重生小说里的"现代词违和"。

═══ 你的任务 ═══

读一段穿越/重生题材的正文 + 一组可疑现代词命中位置,判定每个命中是否真违和。

═══ 判定原则 ═══

★ 必须扫描**全文**(对白 + 旁白 + 环境描写 + 心理戏),不只看对白 ★

✗ 真违和(critical/warn):
  · 主角对古人**直接说出**现代术语(没换成古人能理解的说法)
  · 古人**立即理解**现代术语(没有疑惑/解释过程)
  · **旁白/环境描写**用现代词(如"霓虹灯""高楼林立""班级""办公室")——这种**最隐蔽最破代入**
  · **环境意象**借现代视角("像地铁站""像写字楼")——古风世界出现现代意象 = 出戏
  · 心理戏用当代网络用语(卷王/PUA/内卷等)对人物心理破坏沉浸感

✓ 不违和(skip):
  · 主角心里用现代词思考(读者代入主角的现代意识 —— 合法)
  · 主角解释原理时用古人能理解的类比(如"算学之道"代"微积分" —— 合法)
  · 系统/金手指/真 AI 内部回答用现代术语(独立叙事空间 —— 合法)
  · 旁白以全知视角解释(本书是全知体的话)

═══ 输出格式 ═══

JSON:
{
  "violations": [
    {
      "term": "命中的现代词",
      "excerpt": "命中前后 30 字的上下文摘录",
      "reason": "为什么真违和(20 字)",
      "severity": "critical|warn",
      "suggestion": "应改为(20 字,如'换成 XX')"
    }
  ]
}

判定为不违和的不要进 violations。没有就给空数组。"""


@dataclass
class AnachronismIssue:
    term: str
    excerpt: str
    reason: str
    severity: str  # critical|warn
    suggestion: str


def is_applicable(state) -> bool:
    """只在穿越/重生题材启用。"""
    try:
        ci = getattr(state, "creative_intent", None)
        if not ci:
            return False
        subg = (getattr(ci, "suggested_subgenre", "") or "").lower()
        if "穿越" in subg or "重生" in subg:
            return True
        rb = (getattr(ci, "reality_basis", "") or "").lower()
        if "穿越" in rb or "重生" in rb:
            return True
        # 兜底:扫 raw_description
        raw = (getattr(ci, "raw_description", "") or "").lower()
        if "穿越" in raw or "重生" in raw:
            return True
    except Exception:
        return False
    return False


def fast_filter(text: str) -> list[tuple[str, int]]:
    """正则扫描现代词,返回 [(term, position), ...]。没命中返回 []。"""
    if not text:
        return []
    out: list[tuple[str, int]] = []
    for m in _MODERN_RE.finditer(text):
        out.append((m.group(0), m.start()))
        if len(out) >= 30:  # 限制最多 30 个命中,避免 LLM prompt 爆
            break
    return out


def audit_chapter(
    state,
    chapter_index: int,
    chapter_text: str,
) -> list[AnachronismIssue]:
    """
    章后审一次:返回 violations 列表。
    · 题材不匹配 → 直接返回空
    · fast filter 无命中 → 直接返回空(不调 LLM)
    · 命中 → LLM 判定,只返回真违和的
    · LLM 失败 → 返回空(不阻塞)
    """
    if not is_applicable(state):
        return []
    if not chapter_text or len(chapter_text) < 100:
        return []

    hits = fast_filter(chapter_text)
    if not hits:
        return []

    # 构造上下文摘录(每个命中点前后 30 字)
    hit_blocks = []
    for term, pos in hits[:20]:
        lo = max(0, pos - 30)
        hi = min(len(chapter_text), pos + len(term) + 30)
        snippet = chapter_text[lo:hi].replace("\n", " ")
        hit_blocks.append(f"  · 现代词「{term}」 上下文:「...{snippet}...」")

    user = "\n".join([
        f"以下是第 {chapter_index} 章(穿越/重生题材)正文中匹配到现代词的位置。",
        "请判定每个是否真违和(主角对古人直接说/古人立即理解/旁白破代入)。",
        "",
        "═══ 命中位置 ═══",
        "\n".join(hit_blocks),
        "",
        "输出 JSON 严格按 schema: {\"violations\":[{\"term\":...,\"excerpt\":...,\"reason\":...,\"severity\":...,\"suggestion\":...}]}",
    ])

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["violations"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return []

    if not isinstance(result, dict):
        return []
    raw = result.get("violations") or []
    if not isinstance(raw, list):
        return []

    out: list[AnachronismIssue] = []
    for v in raw:
        if not isinstance(v, dict):
            continue
        term = (v.get("term") or "").strip()
        excerpt = (v.get("excerpt") or "").strip()
        if not term or not excerpt:
            continue
        out.append(AnachronismIssue(
            term=term[:30],
            excerpt=excerpt[:120],
            reason=(v.get("reason") or "").strip()[:80],
            severity=(v.get("severity") or "warn").strip(),
            suggestion=(v.get("suggestion") or "").strip()[:80],
        ))
    return out


def audit_and_surface(state, chapter_index: int, chapter_text: str) -> list[AnachronismIssue]:
    """一站式:audit + 推 progress_warning。"""
    issues = audit_chapter(state, chapter_index, chapter_text)
    source = f"chapter:{chapter_index}:anachronism"
    if not issues:
        try:
            from persistence.checkpoint import clear_progress_warnings
            clear_progress_warnings(source=source)
        except Exception:
            pass
        return issues
    try:
        from persistence.checkpoint import add_progress_warning
        criticals = [i for i in issues if i.severity == "critical"]
        warns = [i for i in issues if i.severity != "critical"]
        if criticals:
            msg = (
                f"时代断代 {len(criticals)} 处 critical: "
                + " | ".join(f"「{i.term}」→{i.suggestion[:20]}" for i in criticals[:3])
            )
            add_progress_warning(level="error", source=source, message=msg)
        elif warns:
            msg = (
                f"时代断代 {len(warns)} 处 warn: "
                + " | ".join(f"「{i.term}」→{i.suggestion[:20]}" for i in warns[:3])
            )
            add_progress_warning(level="warn", source=source, message=msg)
    except Exception:
        pass
    return issues


def _surface_failure(chapter_index: int, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:anachronism",
            message=f"时代断代检查失败,本章不报警: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
