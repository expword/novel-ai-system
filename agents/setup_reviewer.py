"""
SetupReviewer —— 章节完稿后的"设定合规审核"智能体。

在 writer 写完 + critic 审完之后跑一次。和 continuity_checker / critic 的分工：
  - continuity_checker：只看"硬事实"（位置/伤势/物品/境界 是否连贯）
  - critic：看"文学质量"（文笔/节奏/画面感）
  - ★ setup_reviewer（本 agent）：看"设定合规性"——
      · 这一章是否符合 MasterOutline 的规划？
      · 人物的行为是否符合他的性格/动机/致命弱点？
      · 世界观/力量体系/势力设定有没有被违反？
      · 已埋的伏笔有没有被错误兑现或提前泄露？
      · 本卷的 purpose / expression 有没有被推进？

**可以决定大改**：如果合规分数 < 阈值，触发 writer.rewrite_chapter 重写。

实现要点：
  · 使用独立的 LLM profile（默认 user_models.find_by_usage("reviewer")，
    回退到内置 PROFILES 的 yunwu-gemini-3-1-flash-lite）
  · 独立 API key——不抢主模型的并发/额度
  · 轻量提示词——让审核跑得快（每章增加 5-15 秒而非 30+ 秒）
"""
from __future__ import annotations
from utils.json_utils import request_json_with_profile
from persistence.state import NovelState, ChapterDirective

# 优先从 user_models 找 usage="reviewer" 的配置——支持 UI 自定义
# 找不到再 fallback 到内置审核 profile
def _reviewer_profile_id() -> str:
    try:
        from llm_layer import user_models as _um
        um = _um.find_by_usage("reviewer")
        if um:
            return um["id"]  # 用 user_models 的 id，chat_with_profile 会识别
    except Exception:
        pass
    return "yunwu-gemini-3-1-flash-lite"


SYSTEM = """你是小说"设定合规审核员"——章节刚写完，作者请你对照全书设定做最终检查。

你不评文学，不挑词句——你只答：**这一章符不符合我们之前定好的所有设定？**

【审核维度】
1. 世界合规：章内描写是否违反 world_setting / power_system 的规则？新出现的地名/势力/功法/物品是不是在已有 glossary 或 factions 里？
2. 人物合规：
   · 每个出场角色的行为是否符合他的 personality / motivation / fatal_flaw？
   · 说话风格是否匹配他的 speech_pattern / verbal_tics？
   · 没有"突然开窍"或"突然 OOC"的表现？
3. 情节合规：
   · 本章是否推进了本卷的 purpose / expression / arc？
   · 本章在 structure_role（起/承/转/合）的位置是否合适？
   · MasterOutline 的 plot_setpieces 里涉及本卷的节点，有没有提前触发或遗漏？
4. 伏笔合规：
   · 本章是否"兑现"了某个已埋伏笔？兑现方式是否符合 resolution_description？
   · 有没有意外泄露不该这卷揭露的秘密（hidden_secret / world_secrets）？
5. 逻辑合规：
   · 因果链是否自然？有没有 deus ex machina？
   · 时间/空间/势力关系是否自洽？

【评分标准】overall_score 0-10：
  10 完美合规，可以发布
  8-9 轻微瑕疵，不影响大局（minor_fix）
  5-7 明显偏离设定，需要局部改（moderate_fix）
  <5  严重偏离，建议大改（major_rewrite）

【给 writer 的重写指导（当 overall_score < 阈值时必填）】
rewrite_directives 字段：50-150 字的具体改写方向——**哪里错了 + 怎么改**。
不要说"写得不好"，要说"第二场景主角的决策与他 fatal_flaw=优柔寡断 不符——应改成他犹豫良久后才做决定"。

输出严格 JSON。"""


def _check_lifecycle_compliance(state: NovelState, directive: ChapterDirective,
                                  content: str) -> list[dict]:
    """本地确定性检查：本章命中的 lifecycle 节点 asset 名是否在正文中出现。
    未出现 → 返回 critical 级 issue 列表（LLM 审核会跟它合并）。
    """
    try:
        from agents.ability_roadmap_planner import find_nodes_hitting_chapter
    except Exception:
        return []
    proto_name = next((c.name for c in state.characters if c.role.value == "主角"), None)
    nodes = find_nodes_hitting_chapter(state, directive.chapter_index, holder_name=proto_name)
    if not nodes:
        return []
    issues = []
    for n in nodes:
        asset_name = (n.get("asset_name") or "").strip()
        if not asset_name:
            continue
        if asset_name in content:
            continue
        ask_hint = (f"用 [[ASK_AI:{asset_name}|具体问题]] 占位"
                    if n.get("external_llm_profile") else
                    "主角实际获取/使用该 asset")
        issues.append({
            "category": "情节",
            "severity": "critical",
            "description": (f"本章为《{asset_name}》的 lifecycle 锚定章（节点 [{n.get('node_type','')}]），"
                            f"正文未出现该 asset 名。规划目的：{(n.get('narrative_purpose') or '')[:40]}"),
            "suggestion": f"必须把《{asset_name}》写进本章关键场景；{ask_hint}",
        })
    return issues


def review_chapter(state: NovelState, directive: ChapterDirective, content: str) -> dict:
    """
    跑一次合规审核，返回：
      {
        "overall_score": 0-10,
        "verdict": "pass" | "minor_fix" | "moderate_fix" | "major_rewrite",
        "issues": [...],
        "rewrite_directives": "..."  # major_rewrite 时必填
      }
    """
    chapter_idx = directive.chapter_index
    vol = state.get_volume(directive.volume_index)
    active_chars = state.active_characters_in_volume(directive.volume_index)

    # 本地确定性兜底：lifecycle 节点章未落地 → critical（无论 LLM 跑不跑得通都执行）
    lifecycle_issues = _check_lifecycle_compliance(state, directive, content)

    # ─── 构造"设定锚点"——审核员要对照哪些东西 ───
    setup_block = _build_setup_anchors(state, directive, vol, active_chars)

    # 章节内容——太长就取头尾
    if len(content) > 3500:
        content_sample = content[:2000] + "\n\n[...]\n\n" + content[-1500:]
    else:
        content_sample = content

    prompt = f"""对第 {chapter_idx} 章做设定合规审核。

{setup_block}

═══ 本章正文（若 > 3500 字则取头尾节选）═══
{content_sample}

═══ 审核要求 ═══
逐一对照上面 5 个合规维度检查。发现问题列出来，并判整体分。

输出 JSON：
{{
  "overall_score": 0 到 10,
  "verdict": "pass|minor_fix|moderate_fix|major_rewrite",
  "issues": [
    {{
      "category": "世界|人物|情节|伏笔|逻辑",
      "severity": "critical|major|minor",
      "description": "具体问题（50字，指明是哪一段）",
      "suggestion": "怎么改（30字）"
    }}
  ],
  "rewrite_directives": "（若 verdict=major_rewrite：50-150字具体重写方向；否则空字符串）",
  "reviewer_note": "（可选，给作者的总体评语，30字）"
}}
"""
    try:
        data = request_json_with_profile(
            profile_id=_reviewer_profile_id(),
            system=SYSTEM, user=prompt,
            required_keys=["overall_score", "verdict"],
            max_retries=2, temperature=0.3,
            agent_name=f"SetupReviewer[Ch{chapter_idx}]",
            empty_ok=True,
        )
    except Exception as e:
        # 不兜底默认 pass——审核服务故障 = 本章没被审，必须让 caller 看到这个事实
        print(f"  [!] SetupReviewer 调用失败：{e}——返回 review_failed 信号（lifecycle 兜底仍生效）")
        return _merge_lifecycle_issues(
            {"overall_score": 0, "verdict": "review_failed", "issues": [],
             "rewrite_directives": "",
             "reviewer_note": f"审核服务调用失败：{e}",
             "review_failed": True,
             "review_failed_reason": f"{type(e).__name__}: {str(e)[:120]}"},
            lifecycle_issues,
        )

    if not data:
        return _merge_lifecycle_issues(
            {"overall_score": 0, "verdict": "review_failed", "issues": [],
             "rewrite_directives": "",
             "reviewer_note": "审核 LLM 无返回",
             "review_failed": True,
             "review_failed_reason": "LLM 多轮重试后仍无合规 JSON 返回"},
            lifecycle_issues,
        )

    # 确保字段齐全
    result = {
        "overall_score": int(data.get("overall_score", 10)),
        "verdict": data.get("verdict", "pass"),
        "issues": data.get("issues", []) or [],
        "rewrite_directives": data.get("rewrite_directives", "") or "",
        "reviewer_note": data.get("reviewer_note", "") or "",
    }
    return _merge_lifecycle_issues(result, lifecycle_issues)


def _merge_lifecycle_issues(result: dict, lifecycle_issues: list[dict]) -> dict:
    """把 lifecycle 兜底 issues 合并进 review 结果，必要时拉低 score / 升级 verdict。"""
    if not lifecycle_issues:
        return result
    result["issues"] = list(result.get("issues") or []) + lifecycle_issues
    # 任何 lifecycle critical 都把分降到 4 以下，verdict 至少升到 moderate_fix
    result["overall_score"] = min(int(result.get("overall_score", 10)), 4)
    if result.get("verdict") in ("pass", "minor_fix"):
        result["verdict"] = "moderate_fix"
    print(f"  ⚠ [setup_reviewer] lifecycle 兜底追加 {len(lifecycle_issues)} 条 critical "
          f"（本章命中节点章但正文未出现 asset 名）")
    return result


def should_rewrite(review_result: dict) -> bool:
    """根据审核结果判断是否触发大改写。"""
    verdict = review_result.get("verdict", "")
    score = review_result.get("overall_score", 10)
    return verdict == "major_rewrite" or score < REVIEWER_REWRITE_THRESHOLD


def format_rewrite_feedback(review_result: dict) -> str:
    """把审核结果格式化成 writer.rewrite_chapter 能用的 feedback 字符串。"""
    directives = review_result.get("rewrite_directives", "")
    issues = review_result.get("issues", [])
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    major_issues = [i for i in issues if i.get("severity") == "major"]

    parts = []
    if directives:
        parts.append(f"【审核员重写指导】\n{directives}")
    if critical_issues:
        parts.append("【critical 级问题（必须修）】")
        for iss in critical_issues[:5]:
            parts.append(f"  · [{iss.get('category','?')}] {iss.get('description','')}"
                         f"\n    → 建议：{iss.get('suggestion','')}")
    if major_issues:
        parts.append("【major 级问题】")
        for iss in major_issues[:3]:
            parts.append(f"  · [{iss.get('category','?')}] {iss.get('description','')}"
                         f"\n    → 建议：{iss.get('suggestion','')}")
    return "\n".join(parts) if parts else "审核员要求重写（未给出具体指导）"


# ═══════════════════════════════════════════════════════
#  构造设定锚点——给审核员看的"对照物"
# ═══════════════════════════════════════════════════════

def _build_setup_anchors(state, directive, vol, active_chars) -> str:
    """
    精简地把"本章涉及的设定"拼起来。太长审核员读不完，所以按相关性严格裁剪。
    """
    parts = []

    # ── 卷 / 结构位置 ──
    if vol:
        parts.append(
            f"═══ 本卷定位 ═══\n"
            f"第 {vol.index} 卷《{vol.title}》[结构角色：{vol.structure_role}]\n"
            f"卷目的：{vol.purpose[:80]}\n"
            f"卷表达：{vol.expression[:60]}\n"
            f"卷弧线：{vol.arc[:150]}"
        )

    # ── 本章 directive 的关键约束 ──
    if directive:
        parts.append(
            f"\n═══ 本章 directive（写作前的规划）═══\n"
            f"大纲目标：{directive.outline_goal[:100] if hasattr(directive, 'outline_goal') else ''}\n"
            f"结构角色：{directive.structure_role}\n"
            f"purpose：{directive.purpose[:80]}\n"
            f"expression：{directive.expression[:60]}\n"
            f"张力/节奏：{directive.tension.value if hasattr(directive.tension, 'value') else directive.tension}"
            f" / {directive.rhythm.value if hasattr(directive.rhythm, 'value') else directive.rhythm}\n"
            f"必含事件：{' / '.join(directive.must_include[:3]) if directive.must_include else '无'}"
        )

    # ── 出场角色（本章涉及的）──
    if active_chars:
        char_lines = ["\n═══ 核心角色（审核行为是否符合人设）═══"]
        for c in active_chars[:6]:
            char_lines.append(
                f"  · {c.name}（{c.role.value}）"
                f"｜动机：{c.motivation[:30]}"
                f"｜致命弱点：{c.fatal_flaw[:25]}"
                f"｜说话：{c.speech_pattern[:25]}"
            )
        parts.append("\n".join(char_lines))

    # ── MasterOutline 的本卷相关 setpieces ──
    if state.master_outline.generated and state.master_outline.plot_setpieces:
        vol_setpieces = [
            p for p in state.master_outline.plot_setpieces
            if vol and f"第{vol.index}卷" in p.anchor
        ]
        if vol_setpieces:
            sp_lines = ["\n═══ 本卷 MasterOutline 关键节点 ═══"]
            for sp in vol_setpieces[:4]:
                sp_lines.append(f"  · {sp.anchor}·{sp.kind}：{sp.gist[:60]}")
            parts.append("\n".join(sp_lines))

    # ── 已埋待兑现的伏笔（本章可能涉及）──
    pending_fws = [
        fw for fw in state.foreshadow_items
        if fw.planted_chapter > 0
        and not fw.resolved
        and fw.planned_resolve_chapter in (-1, directive.chapter_index)
    ][:5]
    if pending_fws:
        fw_lines = ["\n═══ 可能涉及的伏笔（审核兑现合规性）═══"]
        for fw in pending_fws:
            fw_lines.append(
                f"  · [{fw.fw_id}] 植入于 Ch{fw.planted_chapter}"
                f"｜计划兑现：{fw.resolution_description[:60]}"
            )
        parts.append("\n".join(fw_lines))

    # ── 力量体系简要（避免角色突然跃级）──
    if state.power_system and state.power_system.realms:
        ps = state.power_system
        parts.append(
            f"\n═══ 力量/体系（查越级）═══\n"
            f"{ps.system_name}｜{ps.realm_list_str()[:150]}"
        )

    # ── 世界观摘要 ──
    if state.world_setting:
        parts.append(
            f"\n═══ 世界观摘要（查违反世界规则）═══\n"
            f"{state.world_setting[:400]}"
        )

    return "\n".join(parts)
