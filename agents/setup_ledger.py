"""
SetupLedger —— 爽点 callback 锚点账本。

═══ 解决用户的诉求 ═══

网文爽感强度 = 铺垫的具体性。打脸要爽,前面 X 章必须有"被嘲讽的具体台词"
被记录,触发爽点时把那句原话还回去,读者才会拍案。

SatisfactionPoint.setup_chain 是规划期预定的(抽象 content),
但 LLM 写章时拿不到"前面被嘲讽的真实台词"——只有抽象的 setup.content。

SetupLedger 章后扫稿提取真实事件(humiliation/loss/rejection/...),
触发爽点章前 find_callback_seeds 拉出相关 pending entry,
塞到 directive.callback_seeds 给 writer 当具体回响锚点。

═══ 单章一次 LLM 调用 ═══

· 输入:本章正文 + 已有 pending ledger 摘要 + state.satisfaction_points 列表
· 输出:new_entries(本章新埋的 setup) + callbacks(本章兑现了哪些 entry_id)
· 走 'extractor' usage(轻量便宜),empty_ok=True

═══ 配合 director ═══

· _generate_directive: 触发爽点章时 find_callback_seeds → directive.callback_seeds
· _write_one_chapter 章后:extract_setups_from_chapter → 更新 state.setup_ledger
"""
from __future__ import annotations
import re

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register
from persistence.state import (
    NovelState, SetupEntry, SetupKind, SatisfactionType,
)


# ═══ Agent 形式契约 ═══════════════════════════════════════════
CONTRACT = register(AgentContract(
    name="setup_ledger.extract_setups_from_chapter",
    inputs=[
        "characters[*].name",
        "satisfaction_points[*].sp_id",
        "setup_ledger",
    ],
    outputs=[
        "setup_ledger",
    ],
    invariants=[],
    notes=(
        "章后扫正文识别 setup 事件(被嘲讽/被夺/被拒/被低估/失败/立誓/欠债)"
        " + 兑现已埋的 setup(callback)。失败写 progress_warning"
        " 'chapter:N:setup_ledger'。绕过 [[ASK_AI:..]] 占位段。"
    ),
))


# ═══ Prompt ═══════════════════════════════════════════════════
SYSTEM = """你是网文章节扫稿员,专门识别"打脸铺垫"。

读一章正文回答两类问题:
1. 本章有没有埋下需要日后兑现的事件? (主角或主要配角)
2. 本章有没有兑现/回响了前几章埋下的某条 setup?

═══ 7 类 setup 事件 ═══

  humiliation       被嘲讽/侮辱(必须能引用具体台词)
  loss              被夺走/失去重要人事物
  rejection         被拒绝/被推开
  underestimation   被小看/被无视(暗讽、轻视)
  failed_attempt    主角自己尝试失败(挑战/突破/挽救/比试 等)
  vow               主角立下誓言/承诺(以后我必如何如何)
  debt              欠下人情/仇恨(对某具体角色)

═══ 每条 new_entry 输出 ═══

  kind            上述 7 种之一
  actor           主体(谁被嘲讽/谁立誓)——通常是主角名
  counterpart     对手方(谁嘲讽他/他要还的人情对象)——空则填""
  quote           具体台词(20-50字,原文摘录,若无对白则空字符串)
  scene_summary   具体场景(50字内,把当时画面说清楚)
  suggested_sp_id 若匹配 state 中某 sp_id 的题材,填该 id;否则空字符串

═══ 每条 callback 输出 ═══

只在本章**明确兑现/回响**了 pending ledger 中某条 entry 时输出。
  entry_id        ledger 中那条的 id
  callback_quote  本章里兑现/回应时的具体台词或动作(30字)

═══ 铁律 ═══

· 不要凭空编造——只列正文里真实出现的事件
· 没事件就 new_entries=[] / callbacks=[]
· 同章可识别 0-5 条 new_entries
· quote 必须是原文摘录,不可改写
· 主角已经升级到看不起反派的对话不算 humiliation(主体反了)

═══ 输出严格 JSON ═══

{
  "new_entries": [
    {"kind":"humiliation","actor":"...","counterpart":"...","quote":"...","scene_summary":"...","suggested_sp_id":""}
  ],
  "callbacks": [
    {"entry_id":"setup_0003","callback_quote":"..."}
  ]
}"""


# ═══ sp_type → SetupKind 映射规则 ═════════════════════════════
# 不同爽点类型回响哪些 setup kind 才贴切
_SP_TYPE_TO_KINDS: dict[SatisfactionType, list[SetupKind]] = {
    SatisfactionType.SLAP_FACE:       [SetupKind.HUMILIATION, SetupKind.UNDERESTIMATION, SetupKind.REJECTION],
    SatisfactionType.SHOW_STRENGTH:   [SetupKind.UNDERESTIMATION, SetupKind.HUMILIATION],
    SatisfactionType.REVENGE:         [SetupKind.LOSS, SetupKind.DEBT, SetupKind.HUMILIATION],
    SatisfactionType.REVERSAL:        [SetupKind.FAILED_ATTEMPT, SetupKind.REJECTION, SetupKind.UNDERESTIMATION],
    SatisfactionType.BREAKTHROUGH:    [SetupKind.UNDERESTIMATION, SetupKind.FAILED_ATTEMPT],
    SatisfactionType.EMOTIONAL:       [SetupKind.REJECTION, SetupKind.LOSS, SetupKind.VOW],
    SatisfactionType.REUNION:         [SetupKind.VOW, SetupKind.DEBT],
    SatisfactionType.REVELATION:      [],   # 真相揭露 — any kind
    SatisfactionType.ASSET_LIFECYCLE: [SetupKind.FAILED_ATTEMPT],
}

_RECENT_WINDOW = 30   # 近期优先窗口(章节数)
_PLACEHOLDER_RE = re.compile(r"\[\[ASK_AI:.*?\]\]", re.DOTALL)


# ═══ 公开 API ═════════════════════════════════════════════════

def find_callback_seeds(state: NovelState, sp_id: str, current_chapter: int,
                          limit: int = 5) -> list[SetupEntry]:
    """触发爽点前查找匹配 pending entries。

    匹配规则:
      · sp_type → SetupKind 集合(若映射为空表示 any kind)
      · 优先 suggested_sp_id == sp_id 的精确匹配条目
      · 然后 kind 命中映射 + payoff_status=pending
      · 近期(current_chapter - entry.chapter <= 30)优先排前
    """
    if not sp_id or not state.setup_ledger:
        return []
    sp = next((s for s in state.satisfaction_points if s.sp_id == sp_id), None)
    if not sp:
        return []
    allowed_kinds = _SP_TYPE_TO_KINDS.get(sp.sp_type, [])
    candidates: list[SetupEntry] = []
    for e in state.setup_ledger:
        if e.payoff_status != "pending":
            continue
        if e.chapter > current_chapter:
            continue
        if e.suggested_sp_id == sp_id:
            candidates.append(e)
        elif not allowed_kinds:       # 映射为空 → any kind
            candidates.append(e)
        elif e.kind in allowed_kinds:
            candidates.append(e)
    # 排序: 精确匹配 sp_id 最优先, 近期次之, 旧的最末
    def _sort_key(e: SetupEntry):
        exact = 0 if e.suggested_sp_id == sp_id else 1
        gap = current_chapter - e.chapter
        recent = 0 if gap <= _RECENT_WINDOW else 1
        return (exact, recent, gap)
    candidates.sort(key=_sort_key)
    return candidates[:limit]


def format_callback_seeds_for_directive(entries: list[SetupEntry]) -> list[str]:
    """格式化 entries 为 list[str] 塞到 directive.callback_seeds。"""
    out = []
    for e in entries:
        header_bits = [f"{e.kind.value}·第{e.chapter}章"]
        if e.counterpart:
            header_bits.append(e.counterpart)
        header = f"[{'·'.join(header_bits)}]"
        quote_part = f" 「{e.quote}」" if e.quote else ""
        summary_part = f" — {e.scene_summary}" if e.scene_summary else ""
        out.append(header + quote_part + summary_part)
    return out


def extract_setups_from_chapter(state: NovelState, chapter_index: int,
                                  content: str) -> dict:
    """章后扫正文 → 识别 new setup + callback。

    返回 {"new_entries": [SetupEntry], "callbacks": [...]}
    失败时返回空 dict 并写 progress_warning。
    """
    if not content or len(content.strip()) < 200:
        return {"new_entries": [], "callbacks": []}

    # 绕过 [[ASK_AI:..]] 占位段(避免把 AI 答案当 setup 收录)
    text = _PLACEHOLDER_RE.sub("", content)
    snippet = text[:8000] if len(text) > 8000 else text

    # 构造 LLM 上下文
    sp_lines = []
    for sp in state.satisfaction_points[:30]:
        sp_lines.append(f"  · {sp.sp_id}: {sp.sp_type.value} - {sp.title}")
    sp_list = "\n".join(sp_lines) or "  (无爽点规划)"

    pending_lines = []
    for e in state.setup_ledger:
        if e.payoff_status != "pending":
            continue
        cp = f"·{e.counterpart}" if e.counterpart else ""
        preview = e.quote[:30] if e.quote else e.scene_summary[:30]
        pending_lines.append(f"  · {e.entry_id}({e.kind.value}·第{e.chapter}章{cp}): {preview}")
    pending_summary = "\n".join(pending_lines)[:3000] or "  (无 pending entry)"

    protagonist = next(
        (c.name for c in state.characters
         if getattr(c.role, "value", "") == "主角"),
        "主角"
    )

    user = f"""═══ 主角 ═══
{protagonist}

═══ 全书爽点规划(可作 suggested_sp_id 参考) ═══
{sp_list}

═══ 当前 pending setup ledger(可作 callback 参考) ═══
{pending_summary}

═══ 第 {chapter_index} 章正文(节选) ═══
\"\"\"
{snippet}
\"\"\"

按 SYSTEM 规则识别本章 new_entries 和 callbacks。严格 JSON。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM, user=user,
            required_keys=["new_entries"], max_retries=2, temperature=0.3,
            agent_name=f"SetupLedger[ch{chapter_index}]", empty_ok=True,
        )
    except Exception as _e:
        _emit_warning(chapter_index, f"提取失败:{type(_e).__name__}: {_e}")
        return {"new_entries": [], "callbacks": []}

    if not data:
        return {"new_entries": [], "callbacks": []}

    # 处理 new_entries
    new_entries: list[SetupEntry] = []
    existing_ids = {e.entry_id for e in state.setup_ledger}
    next_seq = _next_seq(state.setup_ledger)
    for raw in (data.get("new_entries") or []):
        if not isinstance(raw, dict):
            continue
        try:
            kind = SetupKind(raw.get("kind", "humiliation"))
        except ValueError:
            kind = SetupKind.HUMILIATION
        actor = str(raw.get("actor") or "").strip()
        if not actor:
            continue
        eid = f"setup_{next_seq:04d}"
        while eid in existing_ids:
            next_seq += 1
            eid = f"setup_{next_seq:04d}"
        existing_ids.add(eid)
        next_seq += 1

        entry = SetupEntry(
            entry_id=eid,
            chapter=chapter_index,
            kind=kind,
            actor=actor,
            counterpart=str(raw.get("counterpart") or "").strip()[:40],
            quote=str(raw.get("quote") or "").strip()[:80],
            scene_summary=str(raw.get("scene_summary") or "").strip()[:120],
            suggested_sp_id=str(raw.get("suggested_sp_id") or "").strip(),
        )
        new_entries.append(entry)

    # 处理 callbacks
    invoked_ids: list[str] = []
    for raw in (data.get("callbacks") or []):
        if not isinstance(raw, dict):
            continue
        eid = str(raw.get("entry_id") or "").strip()
        cb_quote = str(raw.get("callback_quote") or "").strip()[:120]
        if not eid:
            continue
        entry = next((e for e in state.setup_ledger if e.entry_id == eid), None)
        if not entry:
            continue
        entry.payoff_status = "paid"
        entry.callback_chapter = chapter_index
        entry.callback_quote = cb_quote
        invoked_ids.append(eid)

    # 写入 state
    state.setup_ledger.extend(new_entries)

    # 更新 ChapterSummary.setup_callbacks_invoked
    if invoked_ids:
        summary = next(
            (s for s in state.completed_chapters if s.index == chapter_index),
            None,
        )
        if summary is not None:
            summary.setup_callbacks_invoked = invoked_ids

    return {
        "new_entries": new_entries,
        "callbacks": [
            {
                "entry_id": eid,
                "callback_quote": next(
                    (e.callback_quote for e in state.setup_ledger
                     if e.entry_id == eid),
                    "",
                ),
            }
            for eid in invoked_ids
        ],
    }


# ═══ 内部辅助 ═════════════════════════════════════════════════

def _next_seq(ledger: list[SetupEntry]) -> int:
    """从已有 ledger 找出下一个 setup_NNNN 序号。"""
    max_seq = 0
    for e in ledger:
        if e.entry_id.startswith("setup_"):
            try:
                n = int(e.entry_id.split("_", 1)[1])
                if n > max_seq:
                    max_seq = n
            except (ValueError, IndexError):
                pass
    return max_seq + 1


def _emit_warning(chapter_index: int, msg: str) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:setup_ledger",
            message=msg,
        )
    except Exception:
        pass
