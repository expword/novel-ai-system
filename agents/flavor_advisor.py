"""
FlavorAdvisor —— 老作者直觉调味建议。

═══ 解决用户的诉求 ═══

网文老作者的肌肉记忆:
  · "这章太平,下章加个反派出场"
  · "连爽 5 章了,该让主角吃个小亏"
  · "感情线 15 章没推进,读者要骂"
  · "这个配角写出灵气了,加点戏"
  · "反派太弱,打不痛"

这种"调味"直觉是网文老作者最值钱的能力。本系统的所有 audit 都是扣分制(挑错),
没人主动告诉 writer "下一章应该补什么"。

FlavorAdvisor 每 N 章(N=3)扫一次最近章节的:
  · critic 评分(均分趋势)
  · reader_audit(留存率)
  · 模拟评论 sentiment 分布
  · 爽点触发频率

输出 1 条 FlavorAdvice(3-5 条建议),滚动加到 state.flavor_advices(只保留最近 5 条)。
chapter_planner 在写下一章时读最近一条 advice 作为可选灵感(directive 级 hint)。

═══ 单次 LLM 调用 ═══

· 输入:最近 3 章 summary + critic 维度 + reader_audit + 模拟评论 sentiment
· 输出:3-5 条具体建议
· 走 'extractor' usage(轻量),empty_ok=True
"""
from __future__ import annotations

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register
from persistence.state import NovelState, FlavorAdvice


CONTRACT = register(AgentContract(
    name="flavor_advisor.generate_advice",
    inputs=[
        "completed_chapters[*].summary",
        "completed_chapters[*].simulated_comments",
        "satisfaction_points",
    ],
    outputs=[
        "flavor_advices",
    ],
    invariants=[],
    notes=(
        "每 N 章扫一次最近章节,输出'下章应当加什么调味料'。"
        "失败写 'chapter:N:flavor_advisor' progress_warning。"
        "滚动维护——只保留最近 5 条 advice。"
    ),
))


SYSTEM = """你是网文老作者的"调味直觉"——读完最近几章后,你下意识知道下一章该补什么。

═══ 任务 ═══

读最近 3 章的:剧情摘要 / 钩子类型 / 模拟读者评论 / 爽点触发情况。
输出 3-5 条具体的"下一章应当补什么调味料"建议。

═══ 调味的典型方向 ═══

  · 平衡:连爽多章 → 让主角吃个小亏;连续低谷 → 给个小爽
  · 节奏:几章无新角色 → 让某新角色出场(反派/盟友/感情线人选);
          几章无新地点 → 移到新场景
  · 关系:某重要配角太久没出现 → 安排他登场;
          感情线进度停滞 → 推进 / 制造误会
  · 反派:反派打不痛 → 给反派加狠手段 / 给主角具体损失;
          反派太弱 → 揭示更强后台
  · 钩子多样性:连续同类型钩子 → 换类型
  · 主角:主角太顺 → 让他面对一个"无法用金手指解决"的难题
  · 设定回响:某章节埋的伏笔/设定多章没回响 → 在新章节呼应一次

═══ 输出格式 ═══

  target_range  下一章/接下来 1-3 章中的具体范围(如"下 1 章" / "第 N+1~N+3 章")
  advice        3-5 条具体建议,每条 30-50 字,可操作
                · 不要"加强人物深度"这种空话
                · 要"让反派 X 主动找主角,挑明上次的羞辱"这种具体动作
  reasoning     给建议的根据(50 字内,可空)——说明为什么这么调味
                · 如"最近 3 章爽感分 9.0+,该让主角受挫平衡"
                · 如"模拟评论挑刺派多,该补设定/逻辑细节"

═══ 铁律 ═══

· 不要重复 critic 已经在做的工作(扣分挑错)——你是"主动加料",不是"修复缺陷"
· 不要建议彻底改方向——你只动调味,大菜还是 chapter_planner 做
· 建议要可操作——chapter_planner 看到能直接采纳

═══ 输出严格 JSON ═══

{
  "target_range": "下 1-3 章",
  "advice": ["建议 1", "建议 2", "建议 3"],
  "reasoning": "..."
}"""


_KEEP_RECENT = 5      # 滚动队列长度
_LOOKBACK = 3         # 输入最近 N 章


def generate_advice(state: NovelState, chapter_index: int,
                     lookback: int = _LOOKBACK) -> FlavorAdvice | None:
    """生成本轮 advice 并加到 state.flavor_advices(滚动)。返回新 advice 或 None。

    chapter_index 是**刚写完的章**;advice 针对下 1-3 章。
    """
    if chapter_index < lookback:
        return None  # 章节不够,等积累再调

    recent = [
        s for s in state.completed_chapters
        if s.index <= chapter_index
    ][-lookback:]
    if len(recent) < lookback:
        return None

    # 拼最近 N 章的摘要 + 钩子 + 评论 sentiment 分布
    chapter_lines = []
    for s in recent:
        sentiments = {}
        for c in (s.simulated_comments or []):
            sentiments[c.sentiment] = sentiments.get(c.sentiment, 0) + 1
        sent_str = " ".join(f"{k}:{v}" for k, v in sentiments.items()) or "(无评论)"
        sp_str = f" 触发爽点={s.sp_triggered}" if s.sp_triggered else ""
        chapter_lines.append(
            f"  · 第{s.index}章《{s.title}》"
            f"\n     摘要: {s.summary[:120]}"
            f"\n     张力={s.tension.value} 钩子类型={s.closing_hook_type or '(无)'}{sp_str}"
            f"\n     模拟读者: {sent_str}"
        )

    # 本卷爽点统计
    sp_total = len(state.satisfaction_points)
    sp_triggered = sum(1 for sp in state.satisfaction_points if sp.triggered)

    protagonist = next(
        (c.name for c in state.characters
         if getattr(c.role, "value", "") == "主角"),
        "主角"
    )

    user = f"""═══ 上下文 ═══
主角: {protagonist}
当前: 第 {chapter_index} 章刚写完
本书爽点总数: {sp_total} / 已触发: {sp_triggered}

═══ 最近 {len(recent)} 章 ═══
{chr(10).join(chapter_lines)}

按 SYSTEM 规则给出 3-5 条调味建议。严格 JSON。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM, user=user,
            required_keys=["advice"], max_retries=2, temperature=0.6,
            agent_name=f"FlavorAdvisor[ch{chapter_index}]", empty_ok=True,
        )
    except Exception as _e:
        _emit_warning(chapter_index, f"调味建议失败:{type(_e).__name__}: {_e}")
        return None

    if not data:
        return None

    advice_list = [
        str(a).strip()[:80]
        for a in (data.get("advice") or [])
        if isinstance(a, str) and a.strip()
    ][:5]
    if not advice_list:
        return None

    advice = FlavorAdvice(
        generated_at_chapter=chapter_index,
        target_range=str(data.get("target_range") or "").strip()[:40] or f"下 1-3 章(第 {chapter_index+1}~{chapter_index+3} 章)",
        advice=advice_list,
        reasoning=str(data.get("reasoning") or "").strip()[:120],
    )

    # 滚动维护
    state.flavor_advices.append(advice)
    if len(state.flavor_advices) > _KEEP_RECENT:
        state.flavor_advices = state.flavor_advices[-_KEEP_RECENT:]

    return advice


def get_latest_advice_for_chapter(state: NovelState,
                                    chapter_index: int) -> FlavorAdvice | None:
    """取最近一条还在"目标范围"内的 advice,供 chapter_planner 注入下章 prompt。

    简单策略:取最新的、generated_at + 3 ≥ chapter_index 的(过期不再用)。
    """
    if not state.flavor_advices:
        return None
    for advice in reversed(state.flavor_advices):
        if advice.generated_at_chapter + 3 >= chapter_index:
            return advice
    return None


def format_advice_for_prompt(advice: FlavorAdvice) -> str:
    """把 advice 格式化为字符串块,塞 chapter_planner prompt。"""
    if not advice or not advice.advice:
        return ""
    lines = [f"═══ 🧂 调味建议(来自第 {advice.generated_at_chapter} 章后扫稿,作用范围:{advice.target_range}) ═══"]
    for i, a in enumerate(advice.advice, 1):
        lines.append(f"  {i}. {a}")
    if advice.reasoning:
        lines.append(f"  [依据:{advice.reasoning}]")
    lines.append("—— 这些是「主动加料」建议,作为本章 inspiration 的可选补充。可采纳全部/部分/忽略。")
    return "\n".join(lines)


def _emit_warning(chapter_index: int, msg: str) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:flavor_advisor",
            message=msg,
        )
    except Exception:
        pass
