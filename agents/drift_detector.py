"""
DriftDetector — 漂移检测。

每写 N 章跑一次，扫最近章节，检查：
1. 主角实力是否超出原规划（膨胀太快 vs realm_plan）
2. 世界观是否出现未经授权的新设定（与 glossary / world_setting 冲突）
3. 文风是否偏离 ToneManual（banned_words 命中、视角变换）
4. 爽点密度是否偏离 SatisfactionSystem 规划（应有爽点没触发）

产出 drift_report：哪几章可疑、可疑在哪、建议关注点。不自动回改，交给作者/HITL 决定。
"""
from __future__ import annotations
from json_utils import request_json
from state import NovelState, CharacterRole


def detect_drift(state: NovelState, window: int = 10) -> dict:
    """
    扫描最近 window 章检查漂移。
    返回 {"has_drift": bool, "areas": {...}, "flagged_chapters": [...], "recommendations": [...]}
    """
    if not state.completed_chapters:
        return {"has_drift": False, "areas": {}, "flagged_chapters": [], "recommendations": []}

    recent = state.completed_chapters[-window:]
    result = {
        "has_drift": False,
        "areas": {},
        "flagged_chapters": [],
        "recommendations": [],
    }

    # ── 1. 主角实力膨胀检测 ────────────────────────
    realm_drift = _check_realm_inflation(state, recent)
    if realm_drift:
        result["areas"]["realm_inflation"] = realm_drift
        result["has_drift"] = True

    # ── 2. 爽点密度偏离 ─────────────────────────────
    sp_drift = _check_sp_density(state, recent)
    if sp_drift:
        result["areas"]["sp_density"] = sp_drift
        result["has_drift"] = True

    # ── 3. 文风偏离（banned_words 命中扫描）─────────
    tone_drift = _check_tone_compliance(state, recent, window)
    if tone_drift:
        result["areas"]["tone"] = tone_drift
        result["has_drift"] = True
        result["flagged_chapters"].extend(tone_drift.get("flagged_chapters", []))

    # ── 4. 未经授权设定——用 LLM 判断 ──────────────
    new_settings_drift = _check_new_settings_llm(state, recent)
    if new_settings_drift:
        result["areas"]["new_settings"] = new_settings_drift
        result["has_drift"] = True

    # 汇总建议
    for area, data in result["areas"].items():
        if data.get("recommendation"):
            result["recommendations"].append(f"[{area}] {data['recommendation']}")

    return result


def _check_realm_inflation(state, recent_chapters) -> dict:
    """检查主角的级别（境界/职位/异能等级/学历/官阶……按题材而异）是否跑得比规划快。
    本函数对没有层级体系（has_hierarchy=False 或 realms 为空）的题材自动跳过——
    纯情感/纯穿越无外挂等不需要这项检查。"""
    if not state.power_system or not state.power_system.protagonist_realm_plan:
        return {}
    if not getattr(state.power_system, "has_hierarchy", True):
        return {}
    if not state.power_system.realms:
        return {}
    protagonist = next((c for c in state.characters if c.role == CharacterRole.PROTAGONIST), None)
    if not protagonist:
        return {}

    # 当前卷末规划级别
    current_vol = recent_chapters[-1].volume_index if recent_chapters else 1
    planned = state.power_system.protagonist_realm_plan.get(current_vol, "")
    if not planned:
        return {}

    # 主角最近状态快照里的级别
    latest_snap = state.latest_state_snapshot(protagonist.name)
    if not latest_snap or not latest_snap.realm:
        return {}

    # 用级别在体系中的索引比较
    realms = state.power_system.realms
    name_to_idx = {r.name: r.index for r in realms}
    # 兼容"XX初期/XX圆满/XX中/XX前期"这类小阶段表述——取主级别名
    def extract_main_realm(txt):
        for r in realms:
            if r.name in txt:
                return r.name
        return None

    planned_main = extract_main_realm(planned)
    actual_main = extract_main_realm(latest_snap.realm)
    if not planned_main or not actual_main:
        return {}

    planned_idx = name_to_idx.get(planned_main, 0)
    actual_idx = name_to_idx.get(actual_main, 0)
    if actual_idx > planned_idx:
        diff = actual_idx - planned_idx
        return {
            "planned": planned,
            "actual": latest_snap.realm,
            "gap": diff,
            "recommendation": f"主角当前级别【{latest_snap.realm}】已超出第{current_vol}卷规划【{planned}】{diff}级——考虑放缓或修订规划",
        }
    return {}


def _check_sp_density(state, recent_chapters) -> dict:
    """检查最近章节的爽点触发密度 vs 规划。"""
    if not recent_chapters:
        return {}
    # 本卷规划了多少爽点
    current_vol = recent_chapters[-1].volume_index
    planned_sps = [sp for sp in state.satisfaction_points if sp.volume == current_vol]
    if not planned_sps:
        return {}
    triggered_sps = [sp for sp in planned_sps if sp.triggered]

    vol = state.get_volume(current_vol)
    if not vol:
        return {}
    progress_ratio = (recent_chapters[-1].index - vol.chapter_start + 1) / max(vol.total_chapters, 1)
    expected_triggered = int(len(planned_sps) * progress_ratio)

    delta = len(triggered_sps) - expected_triggered
    if abs(delta) >= 2:
        return {
            "planned": len(planned_sps),
            "triggered": len(triggered_sps),
            "expected_at_progress": expected_triggered,
            "delta": delta,
            "recommendation": (
                f"本卷进度 {progress_ratio:.0%}：应触发约 {expected_triggered} 个爽点，"
                f"实际 {len(triggered_sps)} 个（{'超前' if delta > 0 else '滞后'} {abs(delta)} 个）"
            ),
        }
    return {}


def _check_tone_compliance(state, recent_chapters, window: int) -> dict:
    """扫描最近章节文本（从磁盘读），命中 banned_words 的章节要标记。"""
    import os
    from config import OUTPUT_DIR
    banned = state.tone_manual.banned_words if state.tone_manual else []
    if not banned:
        return {}

    flagged = []
    for summary in recent_chapters:
        path = f"{OUTPUT_DIR}/vol{summary.volume_index:02d}/chapter_{summary.index:04d}.txt"
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        hits = [w for w in banned if w and w in content]
        if hits:
            flagged.append({"chapter": summary.index, "hits": hits[:6]})

    if not flagged:
        return {}
    return {
        "flagged_chapters": [f["chapter"] for f in flagged],
        "details": flagged,
        "recommendation": f"最近 {len(recent_chapters)} 章中有 {len(flagged)} 章使用了 ToneManual 禁用词——建议回头清理或修订手册",
    }


def _check_new_settings_llm(state, recent_chapters) -> dict:
    """
    用 LLM 扫描最近章节摘要，判断是否引入了未经授权的新设定（势力/地名/规则）。
    """
    if not recent_chapters:
        return {}
    summaries = "\n".join(
        f"第{c.index}章《{c.title}》：{c.summary[:120]}"
        for c in recent_chapters[-8:]
    )
    known_factions = "、".join(f.name for f in state.factions[:15])
    known_regions = "、".join(r.name for r in state.geography.regions[:15]) if state.geography else ""
    known_glossary = "、".join(g.term for g in state.glossary[:30])

    prompt = f"""扫描最近章节，看有没有引入未经授权的新设定。

【已知势力】{known_factions}
【已知地名】{known_regions}
【术语表中已有的词】{known_glossary}

【最近章节摘要】
{summaries}

═══ 审查 ═══
检查摘要中是否出现（按本书题材判断"新设定"——可能是修真功法、都市行业潜规则、末世异能、星际科技、校园社团活动、宫斗礼制……）：
- 新的势力/组织（不在已知势力里）
- 新的地名（不在已知地名里且不在术语表里）
- 新的规则/设定/能力/技术/制度（与已有世界观/能力体系冲突或大幅扩展）

只报告确实"新增且可疑"的——如果只是已知概念的同义表达或变体，不算。
输出 JSON：
{{
  "has_new_unauthorized": true 或 false,
  "new_factions": [...],
  "new_regions": [...],
  "new_rules": [...],
  "recommendation": "20字建议（如无则空）"
}}
"""
    data = request_json(
        system="你是小说世界观审核员，抓未经授权的新设定。",
        user=prompt, max_retries=2, temperature=0.3,
        agent_name="DriftDetector[新设定]",
        empty_ok=True,
    )
    if not data or not data.get("has_new_unauthorized"):
        return {}
    return {
        "new_factions": data.get("new_factions", []),
        "new_regions": data.get("new_regions", []),
        "new_rules": data.get("new_rules", []),
        "recommendation": data.get("recommendation", "发现疑似新设定，建议作者审阅"),
    }


def print_drift_report(report: dict) -> None:
    if not report.get("has_drift"):
        print("  ✓ 漂移检测：无问题")
        return
    print("  ⚠ 漂移检测：发现问题")
    for area, data in report.get("areas", {}).items():
        rec = data.get("recommendation", "")
        if rec:
            print(f"    · [{area}] {rec[:100]}")
    if report.get("flagged_chapters"):
        print(f"    · 可疑章节：{report['flagged_chapters'][:10]}")
