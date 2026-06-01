"""
AntagonistLifecycleTracker —— 反派 lifecycle roadmap（镜像 ability_roadmap_planner）。

═══ 解决的问题 ═══

ability_roadmap_planner 管金手指 lifecycle(铺垫/获得/首用/升级/...),
但**反派**没有对应的——结果两大病:
  · 反派出现后 2-3 章被秒杀(读者爽点未发酵就消失了)
  · 反派出现后 30+ 章没进展(读者忘了 / 失去威胁感)
  · 反派从未"升级"过(主角一路碾压 = 无张力)

AntagonistLifecycleTracker 为每个 antagonist 设计 6 节点 lifecycle:
  1. introduction         首次出场(露面 / 描述身份)
  2. first_conflict       第一次与主角冲突(直接对手戏)
  3. true_threat_revealed 显露真实威胁(主角才知道有多危险)
  4. escalation           升级(增强实力 / 扩大势力 / 新手段)
  5. final_confrontation  终极对峙(决战章)
  6. defeat_or_redemption 落败 / 转化 / 蛰伏

每节点 anchor 到具体章号(规划时定的); 写章后扫稿自动标记 triggered。

· 反派出场后 X 章无 first_conflict → progress_warning "反派搁置太久"
· 反派 introduction → 2 章内 defeat → progress_warning "反派秒杀(戏剧浪费)"

═══ 设计原则 ═══

· 不入 Phase chain(避免改 scheduler_tasks)——按需触发 design
· state.antagonist_lifecycles 缺失时,director 写章前可触发 design_all_if_needed
· 章后追踪零 LLM(纯规则扫 summary 中的反派名)——便宜
· 设计反派 lifecycle 用 LLM(每个反派一次调用,extractor usage)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="antagonist_lifecycle_tracker",
    inputs=[
        "characters[*].name",
        "characters[*].role",
        "antagonist_lifecycles",
    ],
    outputs=[
        "antagonist_lifecycles",
        # + progress_warning(antagonist:NAME:lifecycle)
    ],
    invariants=[],
    notes=(
        "为每个 antagonist 设计 6 节点 lifecycle。章后纯规则扫 summary 标 triggered。"
        "搁置/秒杀超阈值 → progress_warning。"
    ),
))


# 标准 6 节点
LIFECYCLE_NODES = [
    ("introduction",         "首次出场",          "反派露面/被提及/读者知道有此人"),
    ("first_conflict",       "首次冲突",          "反派与主角第一次直接对手戏"),
    ("true_threat_revealed", "真实威胁显露",      "主角(及读者)才知道反派有多危险"),
    ("escalation",           "升级",              "反派实力/势力/手段升级"),
    ("final_confrontation",  "终极对峙",          "本卷或全书的决战章"),
    ("defeat_or_redemption", "落败/转化/蛰伏",    "反派被打败/转化为盟友/暂时退场"),
]

# 反派从 introduction 到 first_conflict 应在 N 章内,否则"搁置"
INTRO_TO_FIRST_CONFLICT_MAX_GAP = 8

# 反派从 introduction 到 defeat 应至少 N 章,否则"秒杀"
INTRO_TO_DEFEAT_MIN_GAP = 4


@dataclass
class LifecycleNode:
    key: str               # introduction / first_conflict / ...
    label: str             # 中文标签
    description: str       # 这个节点应该发生什么
    planned_chapter: int = -1   # 规划章号(LLM 给的)
    triggered: bool = False
    actual_chapter: int = -1


@dataclass
class AntagonistLifecycle:
    antagonist_name: str
    motivation_brief: str = ""   # 反派核心动机一句话
    threat_level: str = ""       # 卷级/全书/分级威胁
    nodes: list[LifecycleNode] = field(default_factory=list)

    def get_node(self, key: str) -> Optional[LifecycleNode]:
        for n in self.nodes:
            if n.key == key:
                return n
        return None

    def to_dict(self) -> dict:
        return {
            "antagonist_name": self.antagonist_name,
            "motivation_brief": self.motivation_brief,
            "threat_level": self.threat_level,
            "nodes": [
                {"key": n.key, "label": n.label, "description": n.description,
                 "planned_chapter": n.planned_chapter,
                 "triggered": n.triggered, "actual_chapter": n.actual_chapter}
                for n in self.nodes
            ],
        }


# ═══════════════════════════════════════════════════════
#  Design (LLM)
# ═══════════════════════════════════════════════════════

DESIGN_SYSTEM = """你是【反派 lifecycle 设计师】——为单个反派设计 6 节点 lifecycle。

═══ 6 个标准节点(必须全部覆盖)═══

1. introduction         首次出场(读者知道有此人)
2. first_conflict       首次直接与主角冲突(对手戏)
3. true_threat_revealed 主角才知道反派有多危险
4. escalation           反派升级(实力/势力/手段)
5. final_confrontation  终极对峙(本卷或全书决战)
6. defeat_or_redemption 落败/转化/蛰伏

═══ 关键约束 ═══

· 6 个节点的章号必须严格递增
· introduction → first_conflict 之间不超过 8 章(避免搁置)
· introduction → defeat 之间至少 4 章(避免秒杀,戏剧浪费)
· 全部章号必须在反派活跃的卷/章范围内

═══ 输出 ═══

JSON:
{
  "motivation_brief": "30 字核心动机",
  "threat_level": "卷级|全书|分级",
  "nodes": [
    {"key": "introduction", "description": "本节点应该发生什么(30 字)", "planned_chapter": 5},
    {"key": "first_conflict", "description": "...", "planned_chapter": 12},
    ... (6 个全部)
  ]
}"""


def design_lifecycle(
    state,
    antagonist_name: str,
    *,
    intro_chapter_hint: int = 1,
    final_chapter_hint: int = 100,
) -> Optional[AntagonistLifecycle]:
    """
    为单个 antagonist 设计 lifecycle。失败返回 None。
    """
    char = _find_character(state, antagonist_name)
    if not char:
        return None

    # 主角名(给 LLM 上下文)
    proto = _get_protagonist_name(state)

    user_parts = [f"反派姓名: {antagonist_name}"]
    if proto:
        user_parts.append(f"主角姓名: {proto}")
    bg = (getattr(char, "background", "") or "")[:120]
    if bg:
        user_parts.append(f"反派背景: {bg}")
    motive = (getattr(char, "motivation", "") or "")[:120]
    if motive:
        user_parts.append(f"反派动机: {motive}")
    user_parts.append(f"反派活跃范围: 第 {intro_chapter_hint} 章至第 {final_chapter_hint} 章")
    user_parts.append("")
    user_parts.append(
        "请输出 JSON: {\"motivation_brief\":\"...\",\"threat_level\":\"...\",\"nodes\":[6 个节点]}"
    )
    user = "\n".join(user_parts)

    try:
        result = request_json_with_profile(
            system_prompt=DESIGN_SYSTEM,
            user_prompt=user,
            required_keys=["nodes"],
            usage="extractor",
            max_attempts=2,
            empty_ok=False,
        )
    except Exception as e:
        _surface_design_failure(antagonist_name, e)
        return None

    if not isinstance(result, dict):
        return None

    raw_nodes = result.get("nodes") or []
    if not isinstance(raw_nodes, list) or len(raw_nodes) < 3:
        return None

    by_key = {}
    for r in raw_nodes:
        if not isinstance(r, dict):
            continue
        k = (r.get("key") or "").strip()
        if not k:
            continue
        by_key[k] = r

    out = AntagonistLifecycle(
        antagonist_name=antagonist_name,
        motivation_brief=(result.get("motivation_brief") or "").strip()[:80],
        threat_level=(result.get("threat_level") or "").strip()[:30],
    )
    for k, label, default_desc in LIFECYCLE_NODES:
        r = by_key.get(k, {})
        try:
            ch = int(r.get("planned_chapter") or -1)
        except Exception:
            ch = -1
        out.nodes.append(LifecycleNode(
            key=k, label=label,
            description=(r.get("description") or default_desc)[:80],
            planned_chapter=ch,
        ))
    return out


def design_all_if_needed(state) -> int:
    """
    扫所有 antagonist 角色,缺 lifecycle 的批量设计。
    返回新设计的反派数。
    """
    if not hasattr(state, "antagonist_lifecycles") or state.antagonist_lifecycles is None:
        state.antagonist_lifecycles = {}
    designed = 0
    for c in (getattr(state, "characters", None) or []):
        role_val = getattr(getattr(c, "role", None), "value", str(getattr(c, "role", "")))
        if role_val != "反派":
            continue
        name = getattr(c, "name", "")
        if not name or name in state.antagonist_lifecycles:
            continue
        # 估算活跃范围
        intro_hint = max(1, int(getattr(c, "first_volume", 1) or 1) * 10)  # 粗:每卷 10 章
        final_hint = intro_hint + 50
        lc = design_lifecycle(state, name,
                                intro_chapter_hint=intro_hint,
                                final_chapter_hint=final_hint)
        if lc:
            state.antagonist_lifecycles[name] = lc.to_dict()
            designed += 1
    return designed


# ═══════════════════════════════════════════════════════
#  Track (纯规则)
# ═══════════════════════════════════════════════════════

def track_after_chapter(state, chapter_index: int, chapter_text: str = "") -> dict:
    """
    章后纯规则扫描:
    · 出场的反派 → 检查 lifecycle 节点哪些应该 triggered
    · introduction 已 trigger 但很久未 first_conflict → warn(搁置)
    · introduction triggered 后 ≤ X 章 defeat → warn(秒杀)

    返回 {triggered_count, missing_warnings:[]}
    """
    if not hasattr(state, "antagonist_lifecycles") or not state.antagonist_lifecycles:
        return {"triggered_count": 0, "missing_warnings": []}

    triggered_count = 0
    missing_warnings: list[dict] = []

    # 找本章对应的 ChapterSummary
    current = None
    for ch in (getattr(state, "completed_chapters", None) or []):
        if getattr(ch, "index", -1) == chapter_index:
            current = ch
            break
    if current is not None:
        haystack = (
            (getattr(current, "summary", "") or "")
            + "\n" + "\n".join(str(e) for e in (getattr(current, "key_events", None) or []))
        )
    else:
        haystack = chapter_text or ""

    for name, lc_dict in state.antagonist_lifecycles.items():
        if not isinstance(lc_dict, dict):
            continue
        nodes = lc_dict.get("nodes") or []
        if not name or len(name) < 2 or name not in haystack:
            # 本章未出场 → 检查搁置 warning
            intro = _find_node(nodes, "introduction")
            first_conf = _find_node(nodes, "first_conflict")
            defeat = _find_node(nodes, "defeat_or_redemption")
            if (intro and intro.get("triggered")
                and first_conf and not first_conf.get("triggered")):
                gap = chapter_index - intro.get("actual_chapter", chapter_index)
                if gap > INTRO_TO_FIRST_CONFLICT_MAX_GAP:
                    missing_warnings.append({
                        "antagonist_name": name,
                        "issue": "搁置",
                        "gap_chapters": gap,
                        "detail": f"反派 {name} 已出场 {gap} 章但未与主角冲突",
                    })
            continue

        # 反派在本章出场 → 按规则标 triggered
        # 简单规则:依次找第一个未 triggered 的节点,标为本章 actual
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("triggered"):
                continue
            # 第一个未 triggered 的 → 触发(粗启发,后续可加更细判断)
            n["triggered"] = True
            n["actual_chapter"] = chapter_index
            triggered_count += 1

            # 秒杀检查(intro 后 ≤ N 章 defeat)
            if n.get("key") == "defeat_or_redemption":
                intro = _find_node(nodes, "introduction")
                intro_ch = intro.get("actual_chapter", -1) if intro else -1
                if intro_ch > 0 and chapter_index - intro_ch < INTRO_TO_DEFEAT_MIN_GAP:
                    missing_warnings.append({
                        "antagonist_name": name,
                        "issue": "秒杀",
                        "gap_chapters": chapter_index - intro_ch,
                        "detail": (
                            f"反派 {name} 出场后仅 {chapter_index - intro_ch} 章即落败"
                            "(戏剧浪费,读者爽点未充分发酵)"
                        ),
                    })
            break  # 一章只 advance 一个节点

    _surface_warnings(chapter_index, missing_warnings)
    return {
        "triggered_count": triggered_count,
        "missing_warnings": missing_warnings,
    }


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _find_node(nodes: list, key: str) -> Optional[dict]:
    for n in nodes:
        if isinstance(n, dict) and n.get("key") == key:
            return n
    return None


def _find_character(state, name: str):
    for c in (getattr(state, "characters", None) or []):
        if getattr(c, "name", "") == name:
            return c
    return None


def _get_protagonist_name(state) -> str:
    for c in (getattr(state, "characters", None) or []):
        role_val = getattr(getattr(c, "role", None), "value", str(getattr(c, "role", "")))
        if role_val == "主角":
            return getattr(c, "name", "")
    return ""


def _surface_warnings(chapter_index: int, warnings: list[dict]) -> None:
    if not warnings:
        return
    try:
        from persistence.checkpoint import add_progress_warning
        text = " | ".join(f"{w['antagonist_name']}({w['issue']}): {w['detail'][:40]}"
                          for w in warnings[:5])
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:antagonist_lifecycle",
            message=f"反派 lifecycle 异常: {text}",
        )
    except Exception:
        pass


def _surface_design_failure(antagonist_name: str, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"antagonist:{antagonist_name}:lifecycle_design",
            message=f"反派 lifecycle 设计失败: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
