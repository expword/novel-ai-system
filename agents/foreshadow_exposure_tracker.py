"""
ForeshadowExposureTracker —— 伏笔暴露度追踪（章后审计）。

═══ 解决的问题 ═══

foreshadow_manager 管「埋了/激活了/回收了」三状态,但**没人统计某个伏笔
被多少线索暴露**。读者在伏笔回收前已猜中 = 反转失效、读者快感消失。

ForeshadowExposureTracker 章后扫所有 active foreshadow（已植入未回收的）,
判定哪些在本章被进一步提及/暗示,累加 exposure_count。

· exposure_count ≥ EXPOSURE_THRESHOLD (默认 4) → progress_warning
  ↳ 建议: "伏笔 X 已暴露 N 次,读者很可能猜中,考虑提前回收或加红鲱鱼掩盖"

═══ 单 LLM 调用 ═══

· 输入: 本章正文 + 所有 active foreshadow 的 content + activation_sign
· 输出: {exposures: [{fw_id, evidence, exposure_level}]}
· 走 'extractor' usage,empty_ok=True
· 失败不更新 exposure_count(避免误更新)

═══ 设计原则 ═══

· 只扫 active foreshadow(已植入未回收的)——回收的不需要追踪
· 每章最多更新 N 个 fw 的 exposure_count(避免一次性大量推 warning)
· 失败 → progress_warning 失败原因,不阻塞主流程
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="foreshadow_exposure_tracker.audit_chapter",
    inputs=[
        "foreshadow_items[*].fw_id",
        "foreshadow_items[*].content",
        "foreshadow_items[*].planted_chapter",
        "foreshadow_items[*].resolved",
    ],
    outputs=[
        "foreshadow_items[*].exposure_count",
        # + progress_warning(chapter:N:fw_exposure)
    ],
    invariants=[],
    notes=(
        "章后扫所有 active foreshadow,统计本章是否被进一步暴露。"
        "exposure_count 超阈值 → progress_warning。"
        "走 extractor usage,失败不更新 exposure_count。"
    ),
))


# ═══════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════

# 单个 fw 累计 exposure_count 达到此阈值时报警(默认 4)
EXPOSURE_THRESHOLD = 4

# 单章最多扫多少 active foreshadow(避免 prompt 爆炸)
MAX_ACTIVE_TO_SCAN = 15


SYSTEM = """你是【伏笔暴露追踪员】——专精识别正文是否进一步暴露既有伏笔。

═══ 你的任务 ═══

读一章正文 + 一组「已埋未回收」的伏笔清单。判定哪些伏笔在本章被**进一步暴露**:

· 直接提及(角色谈到这件事)
· 暗示(场景/对话/动作隐喻指向)
· 强烈暗示(读者基本能猜到真相)

═══ 注意 ═══

· 不要把"伏笔的回收"算作暴露(那是另一回事)
· 仅判定"是否给了读者更多线索去猜中真相",而非"是否兑现"
· 一个伏笔在本章可能被多次提及,记一次 + 记最强暴露等级即可

═══ 输出格式 ═══

JSON:
{
  "exposures": [
    {
      "fw_id": "对应的伏笔 ID(从 input 给的清单里选)",
      "evidence": "本章正文证据(30 字内摘录)",
      "exposure_level": "提及|暗示|强烈暗示"
    }
  ]
}

没有就给空数组。同一个 fw_id 只出现一次。"""


@dataclass
class ExposureEvent:
    fw_id: str
    evidence: str
    level: str  # 提及|暗示|强烈暗示


def audit_chapter(
    state,
    chapter_index: int,
    chapter_text: str,
) -> list[ExposureEvent]:
    """
    扫本章正文,识别哪些 active foreshadow 被暴露。返回 ExposureEvent 列表。
    失败返回空列表。
    """
    if not chapter_text or len(chapter_text) < 100:
        return []

    active = _get_active_foreshadows(state, chapter_index)
    if not active:
        return []

    fw_listing = []
    for fw in active[:MAX_ACTIVE_TO_SCAN]:
        content = (getattr(fw, "content", "") or "").strip()[:80]
        act_sign = (getattr(fw, "activation_sign", "") or "").strip()[:60]
        fw_id = getattr(fw, "fw_id", "")
        if not fw_id or not content:
            continue
        line = f"  · [{fw_id}] 伏笔内容:{content}"
        if act_sign:
            line += f"  / 已知激活信号:{act_sign}"
        fw_listing.append(line)

    if not fw_listing:
        return []

    user = "\n".join([
        f"以下是第 {chapter_index} 章正文 + 当前已埋未回收的伏笔清单。",
        "判定哪些伏笔在本章被进一步暴露(提及/暗示/强烈暗示)。",
        "",
        "═══ 已埋未回收伏笔 ═══",
        "\n".join(fw_listing),
        "",
        "═══ 本章正文 ═══",
        chapter_text[:6000],
        "",
        "输出 JSON 严格按 schema: {\"exposures\":[{\"fw_id\":...,\"evidence\":...,\"exposure_level\":...}]}",
    ])

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["exposures"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return []

    if not isinstance(result, dict):
        return []
    raw = result.get("exposures") or []
    if not isinstance(raw, list):
        return []

    valid_ids = {getattr(fw, "fw_id", "") for fw in active}
    out: list[ExposureEvent] = []
    seen_ids: set[str] = set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        fw_id = (r.get("fw_id") or "").strip()
        if not fw_id or fw_id not in valid_ids or fw_id in seen_ids:
            continue
        seen_ids.add(fw_id)
        out.append(ExposureEvent(
            fw_id=fw_id,
            evidence=(r.get("evidence") or "").strip()[:120],
            level=(r.get("exposure_level") or "提及").strip(),
        ))
    return out


def apply_exposures(state, events: list[ExposureEvent]) -> list[dict]:
    """
    把 ExposureEvent 应用到 state.foreshadow_items 的 exposure_count。
    返回触发阈值的伏笔列表(供 progress_warning 用)。
    """
    if not events:
        return []
    over_threshold = []
    fw_map = {}
    for fw in (getattr(state, "foreshadow_items", None) or []):
        fw_id = getattr(fw, "fw_id", "")
        if fw_id:
            fw_map[fw_id] = fw
    for ev in events:
        fw = fw_map.get(ev.fw_id)
        if fw is None:
            continue
        # 累加(强烈暗示+2,暗示+1,提及+1)
        if not hasattr(fw, "exposure_count"):
            try:
                setattr(fw, "exposure_count", 0)
            except Exception:
                continue
        inc = 2 if "强烈" in ev.level else 1
        fw.exposure_count = int(getattr(fw, "exposure_count", 0) or 0) + inc
        if fw.exposure_count >= EXPOSURE_THRESHOLD and not getattr(fw, "resolved", False):
            over_threshold.append({
                "fw_id": ev.fw_id,
                "exposure_count": fw.exposure_count,
                "latest_evidence": ev.evidence,
                "latest_level": ev.level,
                "content": (getattr(fw, "content", "") or "")[:80],
            })
    return over_threshold


def surface_warnings(chapter_index: int, over_threshold: list[dict]) -> None:
    """把超阈值的伏笔推到 progress_warning。"""
    source = f"chapter:{chapter_index}:fw_exposure"
    if not over_threshold:
        try:
            from persistence.checkpoint import clear_progress_warnings
            clear_progress_warnings(source=source)
        except Exception:
            pass
        return
    try:
        from persistence.checkpoint import add_progress_warning
        items = " | ".join(
            f"[{f['fw_id']} 暴露{f['exposure_count']}次] {f['content'][:30]}"
            for f in over_threshold[:5]
        )
        msg = (
            f"伏笔暴露度阈值告警({EXPOSURE_THRESHOLD}+)——读者可能已猜中,"
            f"考虑提前回收或加红鲱鱼掩盖: {items}"
        )
        add_progress_warning(level="warn", source=source, message=msg)
    except Exception:
        pass


def audit_and_apply(state, chapter_index: int, chapter_text: str) -> dict:
    """一站式:audit + 更新 exposure_count + 推 warning。返回统计 dict。"""
    events = audit_chapter(state, chapter_index, chapter_text)
    over = apply_exposures(state, events)
    surface_warnings(chapter_index, over)
    return {
        "exposed_count": len(events),
        "over_threshold_count": len(over),
    }


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _get_active_foreshadows(state, chapter_index: int) -> list:
    """active = 已植入(planted_chapter <= ch_idx) 且 未回收(not resolved)"""
    out = []
    for fw in (getattr(state, "foreshadow_items", None) or []):
        planted = int(getattr(fw, "planted_chapter", -1) or -1)
        if planted < 0 or planted >= chapter_index:
            continue
        if getattr(fw, "resolved", False):
            continue
        out.append(fw)
    return out


def _surface_failure(chapter_index: int, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:fw_exposure",
            message=f"伏笔暴露追踪失败,本章不更新 exposure_count: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
