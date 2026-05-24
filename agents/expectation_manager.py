"""
ExpectationManager —— 读者预期管理。

═══ 解决用户的诉求 ═══

网文老读者一看就预测下一步会发生什么:"师父去南方了 → 师父要死";
"今天心情很好 → 要出事";"我已经...哦不没什么 → 有阴谋"。

好作者三选一:
  · 满足预期(satisfy) → 给爽,但读者已经预测到所以不惊喜
  · 反转预期(reverse) → 给惊喜,但要合理
  · 加料 (stack)     → 预期 + 额外惊喜(双向)

最差是"读者准确预测了你还按预测走,且毫无加料"——平庸。

ExpectationManager 写章前预测 3-5 条读者预期,塞 directive.reader_expectations。
chapter_planner 看到后必须对每条标 decision,writer 看到 decision 调整写法。

═══ 单章一次 LLM 调用 ═══

· 输入:上 1-3 章摘要 + 上章末钩子 + setup_ledger pending entries 摘要 + 主角名
· 输出:3-5 条 ReaderExpectation
· 走 'extractor' usage(轻量便宜),empty_ok=True
· 失败写 progress_warning,不阻塞主流程
"""
from __future__ import annotations

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register
from persistence.state import NovelState, ReaderExpectation


CONTRACT = register(AgentContract(
    name="expectation_manager.predict_reader_expectations",
    inputs=[
        "completed_chapters[*].summary",
        "completed_chapters[*].closing_hook",
        "setup_ledger",
    ],
    outputs=[
        # 写到 directive.reader_expectations,不直接改 state
    ],
    invariants=[],
    notes=(
        "写章前预测 3-5 条读者预期,塞 directive.reader_expectations。"
        "chapter_planner LLM 对每条标 decision(satisfy/reverse/stack)。"
    ),
))


SYSTEM = """你是网文老读者预期模拟器——读完一章后,模拟一个追读了 N 章的资深读者
此刻在心里默默预测的"下一章会发生什么"。

═══ 任务 ═══

输入:某章已读完的剧情摘要 + 章末钩子 + 一些埋下的悬念(setup)。
输出:3-5 条该读者会下意识做出的具体预测。

═══ 真实读者的预测特征 ═══

· 具体——读者预测的是具体事件/角色/动作,不是"会发生什么事"这种空话
· 基于线索——每条预测都源于读到的具体内容(章末钩子/反派说的话/角色异常反应)
· 套路化——网文老读者对套路敏感:"师父去南方→师父要死""今天心情很好→要出事"
· 有时是反向预测——"看起来要赢,实际要崩"

═══ 每条预期格式 ═══

  expectation   读者预测下一章会发生什么(30字,具体到事件/角色/动作)
  based_on      基于哪个线索(20字,如"第 5 章末:门外咳嗽声"/"反派那句'我已经'")

═══ 铁律 ═══

· 不要凭空编造——每条都要基于真实读到的线索
· 不要重复——3-5 条要覆盖不同方向(主线/感情/反派/伏笔回收)
· 不要写超长——一条 30 字内,基于 20 字内
· 不要写"我希望"——是"我预测",反映读者下意识的判断

═══ 输出严格 JSON ═══

{
  "expectations": [
    {"expectation":"...","based_on":"..."},
    ...
  ]
}"""


def predict_reader_expectations(state: NovelState, chapter_index: int,
                                  lookback: int = 3) -> list[ReaderExpectation]:
    """写章前预测读者预期。

    chapter_index 是**当前要写的章**;读它前面 lookback 章作为上下文。
    返回 list[ReaderExpectation](decision 字段空,由 chapter_planner 后续填)。
    失败返回 [] 并写 progress_warning。
    """
    if chapter_index <= 1:
        # 第 1 章没有"前 N 章"作为基础,跳过
        return []

    # 取前 lookback 章的摘要 + 上章末钩子
    prev_summaries = [
        s for s in state.completed_chapters if s.index < chapter_index
    ][-lookback:]
    if not prev_summaries:
        return []

    summary_lines = []
    for s in prev_summaries:
        summary_lines.append(
            f"  · 第{s.index}章《{s.title}》: {s.summary[:120]}"
        )
        if s.closing_hook:
            summary_lines.append(f"    末钩子: {s.closing_hook[:60]}")

    # pending setup ledger 中近 30 章内的(可能成为读者预测来源)
    pending_lines = []
    for e in (state.setup_ledger or []):
        if e.payoff_status != "pending":
            continue
        if chapter_index - e.chapter > 30:
            continue
        preview = e.quote[:30] if e.quote else e.scene_summary[:30]
        cp = f"·{e.counterpart}" if e.counterpart else ""
        pending_lines.append(f"  · {e.kind.value}·第{e.chapter}章{cp}: {preview}")
    pending_block = "\n".join(pending_lines[:8]) or "  (无近期 pending setup)"

    protagonist = next(
        (c.name for c in state.characters
         if getattr(c.role, "value", "") == "主角"),
        "主角"
    )

    user = f"""═══ 上下文 ═══
主角: {protagonist}
即将写: 第 {chapter_index} 章

═══ 已读章节摘要(前 {len(prev_summaries)} 章) ═══
{chr(10).join(summary_lines)}

═══ 近期 pending setup(读者可能预测会兑现) ═══
{pending_block}

按 SYSTEM 规则生成 3-5 条读者下意识预期。严格 JSON。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM, user=user,
            required_keys=["expectations"], max_retries=2, temperature=0.4,
            agent_name=f"ExpectationManager[ch{chapter_index}]", empty_ok=True,
        )
    except Exception as _e:
        _emit_warning(chapter_index, f"预期预测失败:{type(_e).__name__}: {_e}")
        return []

    if not data:
        return []

    expectations: list[ReaderExpectation] = []
    for raw in (data.get("expectations") or []):
        if not isinstance(raw, dict):
            continue
        exp = str(raw.get("expectation") or "").strip()[:60]
        if not exp:
            continue
        based = str(raw.get("based_on") or "").strip()[:40]
        expectations.append(ReaderExpectation(
            expectation=exp, based_on=based, decision="",
        ))

    return expectations[:5]


def format_expectations_for_prompt(expectations: list[ReaderExpectation]) -> str:
    """把 expectations 格式化为字符串块,塞到 chapter_planner / writer prompt。"""
    if not expectations:
        return ""
    lines = ["═══ ⚠ 读者预期清单(必须对每条标 decision:satisfy/reverse/stack) ═══"]
    for i, e in enumerate(expectations, 1):
        based = f" [基于:{e.based_on}]" if e.based_on else ""
        decision = f" → 已决策:{e.decision}" if e.decision else ""
        lines.append(f"  {i}. {e.expectation}{based}{decision}")
    lines.append(
        "—— 满足预期 = 读者爽(常规);反转预期 = 读者惊喜(有风险);"
        "加料 = 满足 + 额外惊喜(最佳)。最差是「读者准确预测了你还按预测走」——平庸。"
    )
    return "\n".join(lines)


def _emit_warning(chapter_index: int, msg: str) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:expectation_manager",
            message=msg,
        )
    except Exception:
        pass
