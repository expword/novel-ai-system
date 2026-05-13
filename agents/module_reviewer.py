"""
ModuleReviewer —— 规划阶段（Phase -1 ~ 3G）模块产出的通用质量审核 + 重生触发。

每个 Phase 的产出（人物档案 / 卷结构 / 叙事线 / 反转链 / 主角弧 …）写完后调用
本模块，由独立 LLM 评分（1-10），不达标自动触发"重生"——把审核反馈塞回 agent
prompt 让 LLM 重写一次。

设计原则：
  · 审核员的视角 = "读这本书的高级编辑" —— 不是规则匹配，是创作判断
  · 评分维度按 phase 不同（卷结构看连贯/张力，人物看立体度，叙事线看交叉…）
  · 不及格阈值默认 7（可调），低于阈值让 generator 重跑一次（最多 N 次）
  · 重生时把审核反馈作为 hint 塞进 generator——不是简单重试

接通的 phase（在 director.py 各 phase 块的 mark_phase_done_if 之前调用）：
  - 0.6 主角内核
  - 1A 力量体系
  - 1B 卷结构
  - 2A 人物档案
  - 3A 全局叙事线
  - 3E3 反转系统
  - 3G 主角历程
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional

from utils.json_utils import request_json
from persistence.state import NovelState
from persistence import checkpoint as _ckpt


@dataclass
class ModuleReview:
    phase: str
    overall_score: int = 8       # 1-10
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)  # 给 regenerator 的修订指南
    summary: str = ""


# ── 审核员 SYSTEM ──────────────────────────────────────
SYSTEM_REVIEWER = """你是一位资深小说编辑，专门给作者的写作骨架做质量评审。
不写小说本身——只评判骨架（人物/世界观/卷结构/叙事线/反转/主角弧）是否够好、能否撑起一本好书。

核心评判标准：
  · 立体（不是工具人式的一句话设定）
  · 连贯（前后逻辑通、不矛盾）
  · 有钩子（让人想往下读）
  · 主角中心（一切设计都最终落到主角身上）
  · 不重复不雷同（对不同条目，避免一个模板套出来的）
  · 戏剧性（有冲突、有代价、有转折，不平淡）

打分严苛，不及格就如实说"不及格"。
输出严格 JSON。"""


# ── 每个 phase 的审核 prompt 配置 ──
_PHASE_REVIEW_CONFIGS = {
    "0.6": {
        "label": "主角内核",
        "data_extractor": lambda s: {
            "overall_theme": s.protagonist_journey.overall_theme,
            "core_wound": s.protagonist_journey.core_wound,
            "true_goal": s.protagonist_journey.true_goal,
            "fatal_flaw": s.protagonist_journey.fatal_flaw,
            "central_conflict": s.protagonist_journey.central_conflict,
            "growth_arc": s.protagonist_journey.growth_arc,
        },
        "criteria": (
            "1) overall_theme 是不是一句话能说清整本书在讲什么（不是泛泛而谈）；\n"
            "2) core_wound 是不是真'深'（具体到无法直面、不能言说），而不是'失去亲人'这种通用模板；\n"
            "3) true_goal 是不是与 core_wound 形成张力（不是重复 wound 的反面）；\n"
            "4) fatal_flaw 是不是具体到能反复制造代价（不是'骄傲/固执'这种空话）；\n"
            "5) central_conflict 是不是 wound + flaw + true_goal 共同织出的张力，不是简单'主角 vs 反派'；\n"
            "6) growth_arc 是不是真的描述了**内在变化**（不是外在成就清单）。"
        ),
    },
    "1A": {
        "label": "力量体系",
        "data_extractor": lambda s: {
            "system_name": s.power_system.system_name if s.power_system else "",
            "system_nature": s.power_system.system_nature if s.power_system else "",
            "realms": [r.name + ":" + (r.combat_capability or "")[:60] for r in (s.power_system.realms if s.power_system else [])][:10],
            "special_mechanics": [(m.name + ":" + m.description[:40]) for m in (s.power_system.special_mechanics if s.power_system else [])][:5],
        },
        "criteria": (
            "1) 体系是否符合本书题材（玄幻/都市/职场/末世各自该有的味道）；\n"
            "2) 阶梯设计是否有差异——每级有明确的'能力上限'和'代价/限制'，不是简单数值升级；\n"
            "3) 是否有让读者想看的'特殊机制'（突破点/天赋差异/越级压制规则）；\n"
            "4) 主角的成长路径是否从体系里能看出来（不是脱离体系的金手指）。"
        ),
    },
    "1B": {
        "label": "卷结构",
        "data_extractor": lambda s: {
            "num_volumes": len(s.volumes),
            "volumes": [{
                "index": v.index, "title": v.title, "structure_role": v.structure_role,
                "theme": v.theme, "arc": v.arc[:120],
                "antagonist": v.volume_antagonist,
                "opening_hook": v.opening_hook[:60], "closing_hook": v.closing_hook[:60],
                "key_events": v.key_events[:3],
            } for v in s.volumes],
        },
        "criteria": (
            "1) 整本书的起承转合是否成立——'起'是否真的奠定基调，'转'是否真的转折，'合'是否有归宿感；\n"
            "2) 各卷主题是否递进、不重复（不是'第 N 卷又一次新对手'流水账）；\n"
            "3) 每卷对手的等级/性质是否有梯度，不是同一类反派换皮；\n"
            "4) 卷首/卷尾钩子是否真的吸引人，不是'继续推进剧情'这种废话；\n"
            "5) 卷之间过渡是否自然（卷尾问题→下一卷开局有承接）。"
        ),
    },
    "2A": {
        "label": "人物档案",
        "data_extractor": lambda s: {
            "total_characters": len(s.characters),
            "by_role": {role: sum(1 for c in s.characters if c.role.value == role) for role in ["主角", "主要配角", "反派", "次要配角"]},
            "characters": [{
                "name": c.name, "role": c.role.value,
                "personality": c.personality[:60], "motivation": c.motivation[:50],
                "trauma": c.trauma[:50], "desire": c.desire[:50], "fatal_flaw": c.fatal_flaw[:50],
            } for c in s.characters[:15]],
        },
        "criteria": (
            "1) 主角是否立体——personality/motivation/trauma/desire/fatal_flaw 是否互相支撑（不是模板拼贴）；\n"
            "2) 主要配角是否各有独立动机（不是只为主角服务的工具）；\n"
            "3) 反派是否有合理性的动机（不是'为了恶而恶'）；\n"
            "4) 角色之间是否有'反差/互补/对立'结构（人物群像而不是单角秀）；\n"
            "5) 是否有重复（多个角色的 trauma/desire/fatal_flaw 是否雷同）。"
        ),
    },
    "3A": {
        "label": "全局叙事线",
        "data_extractor": lambda s: {
            "total_lines": len(s.global_lines),
            "by_type": {t: sum(1 for ln in s.global_lines if ln.line_type.value == t) for t in ["故事线", "情感线", "人物线", "悬疑线"]},
            "lines": [{
                "name": ln.name, "type": ln.line_type.value,
                "description": ln.description[:80],
                "volume_range": list(ln.volume_range),
                "phase_count": len(ln.phases),
                "characters": ln.characters[:3],
            } for ln in s.global_lines],
        },
        "criteria": (
            "1) 每条线是否最终落到主角身上（不允许纯背景板线）；\n"
            "2) 每条线自身是否完整起承转合（从 phases 看）；\n"
            "3) 各条线之间是否有交叉点，不是平行赛道；\n"
            "4) 张力分布是否高低错落（不全程高亢也不全程平静）；\n"
            "5) 故事/情感/人物三类线是否都覆盖到主角的不同侧面。"
        ),
    },
    "3E3": {
        "label": "反转系统",
        "data_extractor": lambda s: {
            "design_principle": getattr(s.twist_system, "design_principle", "") if s.twist_system else "",
            "total_chains": len(s.twist_system.chains) if s.twist_system else 0,
            "chains": [{
                "title": c.title, "category": c.category, "difficulty": c.difficulty,
                "scope": c.scope, "target_layers": c.target_layers,
                "actual_layers": len(c.layers),
                "layers": [{"layer": l.layer, "surface": l.surface_belief[:60],
                            "reveal": l.reveal[:60], "anchor": l.reveal_anchor[:30],
                            "clues": l.clues_planted[:3]} for l in c.layers[:4]],
            } for c in (s.twist_system.chains if s.twist_system else [])],
        },
        "criteria": (
            "1) 至少有 1 条 mind_bending（4 层）+ 1 条 brain_burning（3 层）的大反转；\n"
            "2) 每层 reveal 是否真的颠覆前一层 surface_belief，不是机械翻转；\n"
            "3) 每层是否都有 clues_planted（不是空手套白狼的'凭空反转'）；\n"
            "4) reveal_anchor 是否分布合理（不是全堆在最后）；\n"
            "5) 反转手法是否多样（信息缺失/视角欺骗/因果颠倒/身份替换 都用到）。"
        ),
    },
    "3G": {
        "label": "主角历程·里程碑",
        "data_extractor": lambda s: {
            "overall_theme": s.protagonist_journey.overall_theme,
            "growth_arc": s.protagonist_journey.growth_arc[:100],
            "milestones_count": len(s.protagonist_journey.milestones),
            "milestones": [{
                "volume": m.volume,
                "entry": m.entry_state[:50], "exit": m.exit_state[:50],
                "inner_growth": m.inner_growth[:40],
                "hardest_choice": m.hardest_choice[:50],
                "darkest": m.darkest_moment[:50],
                "triumph": m.triumph_moment[:50],
            } for m in s.protagonist_journey.milestones],
        },
        "criteria": (
            "1) 每卷的 entry_state → exit_state 是否有真实变化（不是'还是那样'）；\n"
            "2) hardest_choice 是否真的'最难'——必须有代价、必须让主角'痛'；\n"
            "3) darkest_moment 是否真的'最黑暗'——能让读者揪心，不是泛泛'危机'；\n"
            "4) triumph_moment 是否被前面的 darkest 真正铺垫了（不是凭空爆发）；\n"
            "5) 多卷里程碑是否构成一条完整的内在弧线（从 overall_theme 看得出整体走向）；\n"
            "6) 是否有'兜底骨架'残留（如出现 'LLM 未产出'/'(请在 web UI 重建)' 之类，必须不及格）。"
        ),
    },
}


def review_module(state: NovelState, phase: str, threshold: int = 7) -> ModuleReview:
    """
    审核某个 phase 的产出。返回 ModuleReview。
    threshold 默认 7（不及格 → review.passed=False，调用方触发重生）。
    """
    cfg = _PHASE_REVIEW_CONFIGS.get(phase)
    if not cfg:
        return ModuleReview(phase=phase, overall_score=10, passed=True, summary="无审核规则")

    print(f"  [ModuleReviewer] {cfg['label']}（{phase}）开始评分（LLM 调用中，~10-30s）...")
    try:
        data = cfg["data_extractor"](state)
    except Exception as e:
        print(f"  [ModuleReviewer] {cfg['label']} data_extractor 失败：{type(e).__name__}: {e}")
        return ModuleReview(phase=phase, overall_score=0, passed=False,
                             summary=f"data_extractor 失败：{type(e).__name__}: {e}",
                             issues=[f"无法提取 phase {phase} 的数据"])

    import json as _json
    user_prompt = f"""审核【{cfg['label']}】产出（Phase {phase}）。

═══ 当前产出（JSON 摘要）═══
{_json.dumps(data, ensure_ascii=False, indent=2)[:6000]}

═══ 审核标准（逐条对照打分）═══
{cfg['criteria']}

输出 JSON：
{{
  "overall_score": 1-10 整数（≥{threshold} 算及格），
  "issues": ["具体问题 1", "具体问题 2", ...]（最多 6 条，每条一句话指出哪条不及格）,
  "suggestions": ["修订方向 1", "修订方向 2", ...]（最多 5 条，给重生时的具体指导）,
  "summary": "一句话总评（30 字内）"
}}

注意：打分要严苛——本书要冲'高质量'，6 分都算偏低；
如果产出明显空泛/雷同/兜底骨架/逻辑断裂，直接给 ≤5 分。
"""
    try:
        resp = request_json(
            system=SYSTEM_REVIEWER, user=user_prompt,
            required_keys=["overall_score"],
            max_retries=1, temperature=0.4,  # 审核失败放行——别让审核卡住主流程
            agent_name=f"ModuleReviewer[{cfg['label']}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  [ModuleReviewer] {cfg['label']} 审核 LLM 异常：{type(e).__name__}: {e}——默认放行")
        return ModuleReview(phase=phase, overall_score=8, passed=True,
                             summary=f"审核异常默认放行：{type(e).__name__}")
    if not resp:
        print(f"  [ModuleReviewer] {cfg['label']} 审核 LLM 无响应——默认放行")
        return ModuleReview(phase=phase, overall_score=8, passed=True,
                             summary="审核 LLM 失败，默认放行")

    try:
        score = int(resp.get("overall_score", 8))
    except (ValueError, TypeError):
        score = 8
    issues = list(resp.get("issues", []) or [])
    suggestions = list(resp.get("suggestions", []) or [])
    summary = resp.get("summary", "")
    print(f"  [ModuleReviewer] {cfg['label']} 评分 = {score}/10")
    review = ModuleReview(
        phase=phase,
        overall_score=score,
        passed=(score >= threshold),
        issues=issues,
        suggestions=suggestions,
        summary=summary,
    )
    # 审核 warning（让用户在前端看到）
    try:
        if not review.passed:
            _ckpt.add_progress_warning(
                level="warn",
                source=f"review:{phase}",
                message=f"{cfg['label']} 评分 {score}/10（不及格 <{threshold}）："
                        + (issues[0][:80] if issues else summary[:80]),
            )
        else:
            # 通过的清掉旧 warning
            _ckpt.clear_progress_warnings(source=f"review:{phase}")
    except Exception:
        pass
    return review


# 每个 phase 重生时需要 backup/restore 的字段（避免重生失败导致产物全失）
# fn 接收 state，返回当前值的副本；setter 接收 (state, value) 还原。
_PHASE_BACKUP_FIELDS = {
    "0.6": [
        ("protagonist_journey.overall_theme", lambda s: s.protagonist_journey.overall_theme,
                                              lambda s, v: setattr(s.protagonist_journey, "overall_theme", v)),
        ("protagonist_journey.core_wound",    lambda s: s.protagonist_journey.core_wound,
                                              lambda s, v: setattr(s.protagonist_journey, "core_wound", v)),
        ("protagonist_journey.true_goal",     lambda s: s.protagonist_journey.true_goal,
                                              lambda s, v: setattr(s.protagonist_journey, "true_goal", v)),
        ("protagonist_journey.fatal_flaw",    lambda s: s.protagonist_journey.fatal_flaw,
                                              lambda s, v: setattr(s.protagonist_journey, "fatal_flaw", v)),
        ("protagonist_journey.central_conflict", lambda s: s.protagonist_journey.central_conflict,
                                                 lambda s, v: setattr(s.protagonist_journey, "central_conflict", v)),
        ("protagonist_journey.growth_arc",    lambda s: s.protagonist_journey.growth_arc,
                                              lambda s, v: setattr(s.protagonist_journey, "growth_arc", v)),
    ],
    "1A": [("power_system", lambda s: s.power_system, lambda s, v: setattr(s, "power_system", v))],
    "1B": [("volumes",      lambda s: list(s.volumes), lambda s, v: setattr(s, "volumes", list(v)))],
    "2A": [("characters",   lambda s: list(s.characters), lambda s, v: setattr(s, "characters", list(v)))],
    "3A": [("global_lines", lambda s: list(s.global_lines), lambda s, v: setattr(s, "global_lines", list(v)))],
    "3E3":[("twist_system", lambda s: s.twist_system, lambda s, v: setattr(s, "twist_system", v))],
    "3G": [("protagonist_journey.milestones", lambda s: list(s.protagonist_journey.milestones),
                                              lambda s, v: setattr(s.protagonist_journey, "milestones", list(v)))],
}


def _backup_phase_artifact(state: NovelState, phase: str) -> dict:
    """重生前快照——返回 {field_name: value} 的备份。"""
    spec = _PHASE_BACKUP_FIELDS.get(phase) or []
    return {name: getter(state) for name, getter, _ in spec}


def _restore_phase_artifact(state: NovelState, phase: str, backup: dict) -> None:
    """重生失败时恢复备份。"""
    spec = _PHASE_BACKUP_FIELDS.get(phase) or []
    for name, _, setter in spec:
        if name in backup:
            try:
                setter(state, backup[name])
            except Exception:
                pass


def _phase_artifact_is_empty(state: NovelState, phase: str) -> bool:
    """判定 phase 产出是否"实质为空"——用来识别"重生跑完但产物变没了"。"""
    spec = _PHASE_BACKUP_FIELDS.get(phase)
    if not spec:
        return False  # 没注册的不判
    for name, getter, _ in spec:
        try:
            v = getter(state)
        except Exception:
            return True
        # list 空、None、空 string 都算"实质为空"
        if v is None: return True
        if isinstance(v, (list, tuple)) and len(v) == 0: return True
        if isinstance(v, str) and not v.strip(): return True
        # power_system / twist_system 这种 dataclass——只要存在就不算空
    return False


def review_and_regenerate(
    state: NovelState,
    phase: str,
    regenerator: Callable[[NovelState], None],
    *,
    threshold: int = 7,
    max_attempts: int = 2,
) -> ModuleReview:
    """
    审核 + 不达标重生循环（带 backup/restore，重生失败/变空自动回滚）。
      · 第 1 次跑过 generator 后，调审核
      · 不及格则备份当前产物 → 重生 → 重生失败或产物变空就回滚到备份
      · 重生 max_attempts 轮还不及格就 mark warning 但不阻塞
    """
    review = review_module(state, phase, threshold=threshold)
    if review.passed:
        print(f"  ✓ [{phase}] 审核通过：{review.overall_score}/10 — {review.summary[:50]}")
        return review

    print(f"  ⚠ [{phase}] 审核未通过：{review.overall_score}/10")
    for i, iss in enumerate(review.issues[:5], 1):
        print(f"      {i}. {iss[:80]}")

    state.last_module_review = {
        "phase": phase,
        "score": review.overall_score,
        "issues": review.issues,
        "suggestions": review.suggestions,
    }

    for attempt in range(1, max_attempts + 1):
        # 重生前备份当前产物——避免清空后重跑失败留下空 state
        backup = _backup_phase_artifact(state, phase)
        print(f"  ↻ [{phase}] 第 {attempt} 次重生（按审核反馈，已备份当前产物）")
        try:
            regenerator(state)
            # 检查重生后产物是否反而变空——若是则回滚
            if _phase_artifact_is_empty(state, phase):
                _restore_phase_artifact(state, phase, backup)
                print(f"  ↩ [{phase}] 重生后产物变空——回滚到上次产物")
                continue  # 继续下一轮（如果还有）
        except Exception as e:
            _restore_phase_artifact(state, phase, backup)
            print(f"  ↩ [{phase}] 重生异常：{type(e).__name__}: {e} — 已回滚到上次产物")
            break
        review = review_module(state, phase, threshold=threshold)
        print(f"  → [{phase}] 第 {attempt} 次重生后审核：{review.overall_score}/10")
        if review.passed:
            print(f"  ✓ [{phase}] 通过：{review.summary[:50]}")
            try:
                del state.last_module_review
            except (AttributeError, KeyError):
                pass
            return review

    print(f"  ⚠ [{phase}] {max_attempts} 轮重生后仍 {review.overall_score}/10——继续推进（保留最后一次的产物），警告留在前端")
    return review
