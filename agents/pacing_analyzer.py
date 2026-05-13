"""
PacingAnalyzerAgent — Phase 5：章节节奏统计分析。

本章写完后自动统计：
- 对话 / 动作 / 描写 / 心理描写 的占比
- 每千字出现几个转折点（情绪/局势位移）
- 与本卷同期章节的节奏对比

偏离预设太多就报警。输出写回 ChapterSummary.pacing_stats。
用 LLM 做比例判断（比正则扫描更准）。
"""
from utils.json_utils import request_json
from persistence.state import NovelState, ChapterDirective, ChapterSummary, ChapterPacingStats


SYSTEM = """你是小说节奏分析师。
给你一段章节正文，你要估算四种内容的占比（加起来约等于 100%）：
- dialogue_ratio 对话（带引号的对白）
- action_ratio 动作（物理动作、战斗、移动、肢体反应）
- description_ratio 描写（环境、外貌、场景、氛围）
- inner_monologue_ratio 心理描写（主观思考、内心独白、感受）

还要数 turns_per_1000_words：每千字出现几个"转折点"——情绪/局势/认知的明显位移
（人物突然改变态度、新信息突然出现、局面突然反转 这类）。

估算要基于内容权重，不是机械字数。输出严格 JSON。"""


def analyze_pacing(state: NovelState, directive: ChapterDirective, content: str) -> ChapterPacingStats:
    """
    分析章节节奏，写入 ChapterPacingStats 并与本卷同期章节对比。
    """
    # 截取代表性片段——开头 + 中段 + 结尾
    if len(content) > 5000:
        third = len(content) // 3
        sample = content[:1500] + "\n[...中段...]\n" + content[third:third+1500] + "\n[...尾段...]\n" + content[-1500:]
    else:
        sample = content

    # 同期比较：本卷最近 3 章的统计做参照
    vol = directive.volume_index
    peer_stats = [
        c.pacing_stats for c in state.completed_chapters[-6:]
        if c.volume_index == vol and c.pacing_stats
    ][-3:]
    peer_hint = ""
    if peer_stats:
        avg_dialog = sum(p.dialogue_ratio for p in peer_stats) / len(peer_stats)
        avg_action = sum(p.action_ratio for p in peer_stats) / len(peer_stats)
        avg_turns = sum(p.turns_per_1000_words for p in peer_stats) / len(peer_stats)
        peer_hint = (
            f"\n本卷最近 {len(peer_stats)} 章平均：对话 {avg_dialog:.0%}｜"
            f"动作 {avg_action:.0%}｜转折密度 {avg_turns:.1f}/千字"
        )

    prompt = f"""分析第 {directive.chapter_index} 章节奏统计。

【章节类型】{directive.chapter_type or '未规划'}
【张力/节奏预设】{directive.tension.value} / {directive.rhythm.value}
{peer_hint}

【正文节选】
{sample}

输出 JSON：
{{
  "dialogue_ratio": 0.xx,
  "action_ratio": 0.xx,
  "description_ratio": 0.xx,
  "inner_monologue_ratio": 0.xx,
  "turns_per_1000_words": 整数,
  "deviation_note": "本章与本卷同期章节比较的偏离说明（25字，如'对话偏多10%，心理戏不足'）"
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["dialogue_ratio"],
        max_retries=2, temperature=0.3,
        agent_name=f"PacingAnalyzer[Ch{directive.chapter_index}]",
        empty_ok=True,
    )
    if not data:
        return ChapterPacingStats(chapter_index=directive.chapter_index)

    return ChapterPacingStats(
        chapter_index=directive.chapter_index,
        dialogue_ratio=float(data.get("dialogue_ratio", 0.0)),
        action_ratio=float(data.get("action_ratio", 0.0)),
        description_ratio=float(data.get("description_ratio", 0.0)),
        inner_monologue_ratio=float(data.get("inner_monologue_ratio", 0.0)),
        turns_per_1000_words=int(data.get("turns_per_1000_words", 0)),
        deviation_note=data.get("deviation_note", ""),
    )


def attach_pacing_to_summary(summary: ChapterSummary, stats: ChapterPacingStats) -> None:
    """把节奏统计挂到章节 summary 上。"""
    summary.pacing_stats = stats


def format_pacing_report(stats: ChapterPacingStats) -> str:
    """控制台输出用。"""
    if not stats or stats.chapter_index == 0:
        return ""
    return (
        f"  节奏：对话{stats.dialogue_ratio:.0%}｜动作{stats.action_ratio:.0%}"
        f"｜描写{stats.description_ratio:.0%}｜心理{stats.inner_monologue_ratio:.0%}"
        f"｜转折密度{stats.turns_per_1000_words}/千字"
        + (f"｜{stats.deviation_note}" if stats.deviation_note else "")
    )
