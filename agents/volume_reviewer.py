"""
VolumeReviewerAgent — 整卷写完后的卷级审查。

在一卷内所有 stage 都通过 stage 级审查、所有章节都已出稿后跑：
- 卷的 purpose / expression 是否兑现（整卷想让读者带走的东西）
- 起承转合是否在卷内闭环（多个 stage 的结构角色分布是否齐全）
- 主线推进：本卷应解决/推进的关键事件、伏笔植入/兑现是否落地
- 节奏曲线：跨 stage 的张力起伏是否合理
- 与上一卷的承接：上卷遗留的钩子/伏笔本卷是否处理
- 与下一卷的过渡：closing_hook 是否给下卷留好启动点

输出 ReviewIssue 列表。
"""
import os
from utils.json_utils import request_json
from persistence.state import NovelState, ReviewIssue
import config


SYSTEM = """你是小说卷级审稿人。给你一整卷的章节地图、stage 摘要、关键章节摘录，你要从【整卷】的尺度找问题。

你不审单章细节——单章 critic 已经审过了；也不审 stage 内闭环——stage_reviewer 已经审过了。
你只关心整卷尺度的事：
1. 卷的 purpose / expression 是否兑现？读者合上这一卷时，带走的是它想表达的核心情绪/认知/信息吗？
2. 起承转合在卷内是否闭环？多个 stage 的结构角色分布是否齐全（起/承/转/合 都到位了吗）？
3. 主线与伏笔：本卷该推进的关键事件是否推进、该植入/兑现的伏笔是否落地？
4. 节奏曲线：跨 stage 的张力起伏是否健康？是否长平段或前重后轻、前轻后重？
5. 与上一卷衔接：上卷遗留的钩子/承诺/伏笔本卷是否回应或推进？
6. 与下一卷过渡：closing_hook 是否给下卷留好"放不下书"的钩子？

按"作者补救清单"的标准给问题——具体哪一段/哪几章/哪条线，怎么修。没问题就老实说没问题。

输出严格 JSON。"""


def _read_chapter_text(volume_index: int, chapter_index: int) -> str:
    path = f"{config.OUTPUT_DIR}/vol{volume_index:02d}/chapter_{chapter_index:04d}.txt"
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _chapter_brief(volume_index: int, chapter_index: int, head: int = 250, tail: int = 250) -> str:
    text = _read_chapter_text(volume_index, chapter_index)
    if not text:
        return ""
    if len(text) <= head + tail + 100:
        return text
    return text[:head] + "\n[……]\n" + text[-tail:]


def review_volume(state: NovelState, volume_index: int,
                  iteration: int = 0) -> list[ReviewIssue]:
    """对整卷做卷级审查，返回 ReviewIssue 列表。"""
    vol = state.get_volume(volume_index)
    if not vol:
        return []

    # Stage 摘要 + 已有 stage_review_reports
    stages = state.stages_in_volume(volume_index)
    stage_summary_lines = []
    for st in stages:
        role = f"[{st.structure_role}]" if st.structure_role else ""
        stage_summary_lines.append(
            f"  · {st.name}{role} (Ch{st.chapter_start}-{st.chapter_end}) "
            f"使命：{st.purpose[:40]} | 表达：{st.expression[:30]}"
        )
        # 该 stage 上一轮审查的 critical/major 也带上
        prev_issues = state.stage_review_reports.get(st.stage_id, [])
        critical_majors = [i for i in prev_issues if i.level in ("critical", "major")]
        if critical_majors:
            stage_summary_lines.append(
                f"      （stage 审查遗留 {len(critical_majors)} 条关键问题）"
            )
    stage_summary = "\n".join(stage_summary_lines) or "（本卷未设计 stage）"

    # 章节大纲一览
    outlines_lines = []
    for o in (vol.chapter_outlines or []):
        outlines_lines.append(
            f"  · 第{o.get('index')}章《{o.get('title','')}》：{(o.get('goal','') or '')[:50]}"
        )
    outlines_block = "\n".join(outlines_lines[:60])  # 防超长——卷大于 60 章时截断
    if len(outlines_lines) > 60:
        outlines_block += f"\n  …… (共 {len(outlines_lines)} 章, 已截至前 60)"

    # 关键章节摘录：卷首、卷中（每个 stage 的中点）、卷尾
    key_chapter_indices = set()
    key_chapter_indices.add(vol.chapter_start)
    key_chapter_indices.add(vol.chapter_end)
    for st in stages:
        mid = (st.chapter_start + st.chapter_end) // 2
        key_chapter_indices.add(mid)
        key_chapter_indices.add(st.chapter_end)
    excerpts_blocks = []
    for ci in sorted(key_chapter_indices):
        if ci < vol.chapter_start or ci > vol.chapter_end:
            continue
        brief = _chapter_brief(volume_index, ci)
        if brief:
            excerpts_blocks.append(f"━━ 第{ci}章 ━━\n{brief}")
    excerpts_text = "\n\n".join(excerpts_blocks) or "（无章节正文）"

    # 上一卷的 closing_hook + key_events 用于衔接审
    prev_vol_block = ""
    if volume_index > 1:
        prev_vol = state.get_volume(volume_index - 1)
        if prev_vol:
            prev_vol_block = (
                f"上一卷《{prev_vol.title}》：\n"
                f"  closing_hook：{prev_vol.closing_hook}\n"
                f"  key_events：{' / '.join(prev_vol.key_events[:3])}\n"
            )

    next_vol_block = ""
    next_vol = state.get_volume(volume_index + 1)
    if next_vol:
        next_vol_block = (
            f"下一卷《{next_vol.title}》：\n"
            f"  opening_hook：{next_vol.opening_hook}\n"
            f"  theme：{next_vol.theme}\n"
        )

    role_tag = f"[{vol.structure_role}]" if vol.structure_role else ""
    prompt = f"""卷级审查：第 {volume_index} 卷《{vol.title}》{role_tag}

═══ 待审卷 ═══
主题：{vol.theme} | 主要对手：{vol.volume_antagonist}
purpose（这一卷的使命）：{vol.purpose}
expression（想让读者带走什么）：{vol.expression}
key_events：{' / '.join(vol.key_events[:5])}
opening_hook：{vol.opening_hook}
closing_hook：{vol.closing_hook}
章节范围：{vol.chapter_start}-{vol.chapter_end}（共 {vol.total_chapters} 章）

═══ 上下卷衔接参照 ═══
{prev_vol_block or '（这是第一卷）'}
{next_vol_block or '（这是最后一卷）'}

═══ 本卷 stage 摘要 ═══
{stage_summary}

═══ 本卷章节大纲一览 ═══
{outlines_block}

═══ 关键章节正文摘录（卷首/各 stage 中点与末/卷尾）═══
{excerpts_text}

═══ 审查要求 ═══
按 6 个维度找问题：
1. 卷 purpose/expression 是否兑现
2. 起承转合在卷内是否闭环（多个 stage 的角色分配齐不齐）
3. 主线/伏笔：本卷该推进/落地的事是否落地
4. 节奏曲线：跨 stage 的张力起伏是否健康
5. 与上一卷衔接：上卷钩子/承诺/伏笔本卷是否回应
6. 与下一卷过渡：closing_hook 是否给下卷留好钩子（若有下卷）

每条问题给 level (critical/major/minor) + issue + affected_chapters + suggestion。
critical：破坏整卷核心体验或后续展开——必须修
major：明显问题但可控——建议修
minor：小瑕疵——记录即可

输出严格 JSON：
{{
  "issues": [
    {{"level": "critical|major|minor", "issue": "...", "affected_chapters": [N, M], "suggestion": "..."}}
  ]
}}
没问题就输出 {{"issues": []}}。"""

    # empty_ok=False——审核服务故障必须让 caller 看到，不静默"通过"
    try:
        data = request_json(
            system=SYSTEM, user=prompt,
            required_keys=["issues"],
            max_retries=3, temperature=0.4,
            agent_name=f"VolumeReviewer[V{volume_index}]",
            empty_ok=False,
        )
    except Exception as _e:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="error",
            source=f"volume:{volume_index}:reviewer",
            message=(
                f"第 {volume_index} 卷 volume_reviewer 审核服务故障"
                f"（{type(_e).__name__}: {str(_e)[:120]}）"
                "——本卷未通过 LLM 审核就放行了；请检查审核模型/key 并酌情人工复审"
            ),
        )
        print(f"  ❌ [volume_reviewer] V{volume_index} 审核失败：{type(_e).__name__}: {_e}")
        return []
    if not data:
        return []
    issues = []
    for item in (data.get("issues") or []):
        if not isinstance(item, dict):
            continue
        try:
            issues.append(ReviewIssue(
                level=str(item.get("level", "minor")).lower(),
                issue=str(item.get("issue", ""))[:200],
                affected_chapters=[int(x) for x in (item.get("affected_chapters") or []) if str(x).strip().isdigit()],
                suggestion=str(item.get("suggestion", ""))[:200],
                iteration=iteration,
            ))
        except Exception:
            continue
    return issues
