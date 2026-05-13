"""
StageReviewerAgent — Stage（大情节）写完后的整体审查。

在一个 stage 内所有章节都写完后跑：
- 检查 stage 的 purpose / expression 是否兑现（读者带走的就是 stage 想表达的东西吗？）
- 起承转合在 stage 内是否闭环（章节角色分配是否合理？关键转/合是否落地？）
- 跨章连贯：伏笔/情绪/情节/角色状态在本 stage 内是否前后呼应、不断裂
- 节奏起伏：是否长平段或无铺垫高潮
- 与前后 stage 的衔接钩子

输出 ReviewIssue 列表，level=critical 触发指定章重写循环。
"""
import os
from json_utils import request_json
from state import NovelState, ReviewIssue
import config


SYSTEM = """你是小说大情节（stage）级审稿人。给你一个 stage 内所有章节的浓缩呈现，你要从【整段戏】的角度找问题。

你不审单章文笔——那是 critic 的事。你只关心：
1. 这段大情节的 purpose / expression 是否兑现？读者读完这段戏，带走的是它想表达的情绪/认知/信息吗？
2. 起承转合在 stage 内是否闭环？开头是否入戏、关键转折是否到位、结尾是否落定？章与章的结构角色（起/承/转/合）分配是否合理？
3. 跨章连贯：本 stage 内伏笔是否前后呼应、情绪曲线是否顺、角色状态变化是否平滑、关键事件是否承接？
4. 节奏起伏：是否长平段（连续几章无张力提升）或无铺垫高潮（突然炸而前文无累积）？
5. 与上一 stage / 下一 stage 的衔接钩子是否就位？

按"作者补救清单"的标准给问题——不是空泛吐槽，要点出具体哪一章/哪一处、为什么有问题、怎么修。
没问题就老实说没问题，不凑数。

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


def _chapter_excerpt(volume_index: int, chapter_index: int, head: int = 600, tail: int = 600) -> str:
    text = _read_chapter_text(volume_index, chapter_index)
    if not text:
        return f"（第{chapter_index}章正文未找到）"
    if len(text) <= head + tail + 200:
        return text
    return text[:head] + "\n\n[……中段省略……]\n\n" + text[-tail:]


def review_stage(state: NovelState, volume_index: int, stage_id: str,
                 iteration: int = 0) -> list[ReviewIssue]:
    """对单个 stage 做整体审查，返回 ReviewIssue 列表。"""
    vol = state.get_volume(volume_index)
    if not vol:
        return []
    stage = next((s for s in state.story_stages if s.stage_id == stage_id), None)
    if not stage:
        return []

    # 用 outlines 归属取本 stage 章节集（不与 parallel stage 重叠）
    chapter_indices = state.chapters_in_stage(volume_index, stage_id)
    if not chapter_indices:
        chapter_indices = list(range(stage.chapter_start, stage.chapter_end + 1))
    chapter_index_set = set(chapter_indices)

    # 章节大纲行（goal/title/structure_role）
    outlines_lines = []
    for o in (vol.chapter_outlines or []):
        ci = o.get("index")
        if ci in chapter_index_set:
            outlines_lines.append(
                f"  · 第{ci}章《{o.get('title','')}》：{o.get('goal','')}"
            )
    outlines_block = "\n".join(outlines_lines) or "  （无 outline 数据）"

    # 各章节正文摘录
    chapter_blocks = []
    for ci in chapter_indices:
        excerpt = _chapter_excerpt(volume_index, ci)
        chapter_blocks.append(f"━━━ 第 {ci} 章 ━━━\n{excerpt}")
    chapters_text = "\n\n".join(chapter_blocks) if chapter_blocks else "（无章节正文）"

    # 上一/下一 stage 的简介（用于衔接钩子检查）
    stages_in_vol = state.stages_in_volume(volume_index)
    idx_in_vol = next((i for i, s in enumerate(stages_in_vol) if s.stage_id == stage_id), -1)
    neighbor_block_parts = []
    if idx_in_vol > 0:
        prev_st = stages_in_vol[idx_in_vol - 1]
        neighbor_block_parts.append(
            f"上一 stage：[{prev_st.structure_role}] {prev_st.name}（{prev_st.purpose[:40]}）"
        )
    if 0 <= idx_in_vol < len(stages_in_vol) - 1:
        next_st = stages_in_vol[idx_in_vol + 1]
        neighbor_block_parts.append(
            f"下一 stage：[{next_st.structure_role}] {next_st.name}（{next_st.purpose[:40]}）"
        )
    neighbor_block = "\n".join(neighbor_block_parts) or "（本 stage 是本卷首/尾 stage）"

    role_tag = f"[{stage.structure_role}]" if stage.structure_role else ""
    prompt = f"""第 {volume_index} 卷《{vol.title}》大情节审查。

═══ 待审 stage ═══
名称：{stage.name}{role_tag} | 类型：{stage.stage_type} | 章节范围：{stage.chapter_start}-{stage.chapter_end}
氛围：{stage.atmosphere}
主角处境：{stage.protagonist_role}
purpose（这段戏的使命）：{stage.purpose}
expression（想让读者感受）：{stage.expression}
关键活动：{' / '.join(stage.key_activities[:5])}

═══ 邻近 stage 衔接 ═══
{neighbor_block}

═══ 本 stage 章节大纲 ═══
{outlines_block}

═══ 各章正文摘录 ═══
{chapters_text}

═══ 审查要求 ═══
按 5 个维度找问题：
1. purpose/expression 是否兑现：读者读完这 {len(chapter_indices)} 章，带走的是不是这段戏想表达的情绪/认知？
2. 起承转合在 stage 内是否闭环（章节结构角色分配 + 关键转/合是否到位）
3. 跨章连贯：伏笔/情绪/情节/角色状态有无断裂或前后矛盾
4. 节奏起伏：长平段 / 无铺垫高潮 / 高潮密集挤压
5. 与邻近 stage 衔接：上 stage 留的钩子是否承接？是否给下 stage 留好钩子？

每条问题给：level（critical/major/minor）、issue（具体描述，60字内）、affected_chapters（章号列表）、suggestion（修订方向，40字内）。

判定原则：
- critical：直接破坏读者对这段戏的核心体验或后续故事——必须修
- major：明显瑕疵但不破坏整体——建议修
- minor：可接受范围内的小不顺——记录即可

输出严格 JSON：
{{
  "issues": [
    {{"level": "critical|major|minor", "issue": "...", "affected_chapters": [N, M], "suggestion": "..."}}
  ]
}}
没问题就输出 {{"issues": []}}。"""

    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["issues"],
        max_retries=3, temperature=0.4,
        agent_name=f"StageReviewer[{stage_id}]",
        empty_ok=True,
    )
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
