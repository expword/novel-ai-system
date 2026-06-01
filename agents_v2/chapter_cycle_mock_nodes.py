"""章级写作 cycle 的 mock 节点。

验证 LangGraph cycle 机制：
  write_draft → critic_review → [pass? → finalize: revise → critic_review]
                                       ↑__________________↓ (cycle)

  · critic 故意前 N-1 轮 fail、第 N 轮 pass，验证 cycle 真的会回头
  · 命中 max_rounds 时强制 finalize（防止无限循环）

env var 可注入测试条件：
  CHAPTER_CYCLE_PASS_AT_ROUND  —— critic 在第几轮 pass（默认 3）
"""
from __future__ import annotations
import os
import time

from state_chapter import ChapterState


def _pass_at_round() -> int:
    """从环境变量读 critic 通过轮次（默认 3）。"""
    try:
        return max(1, int(os.environ.get("CHAPTER_CYCLE_PASS_AT_ROUND", "3")))
    except ValueError:
        return 3


def write_draft(state: ChapterState) -> dict:
    """初稿：第 1 次进入是真正写初稿；后续 cycle 不会经过这里（直接 revise）。"""
    print(f"  ▶ [Ch{state.chapter_index}·write_draft] 写初稿 ...")
    time.sleep(0.1)
    draft = f"[mock 初稿 第 {state.chapter_index} 章] " + ("正文 " * 200)
    print(f"  ✓ [Ch{state.chapter_index}·write_draft] {len(draft)} 字")
    return {"draft": draft, "word_count": len(draft), "revision_round": 0}


def critic_review(state: ChapterState) -> dict:
    """审校。第 N 轮（N = pass_at_round）才返回 review_passed=True；
    之前每轮都 fail 触发 revise。"""
    next_round = state.revision_round + 1
    pass_at = _pass_at_round()
    if next_round >= pass_at:
        score = 9
        passed = True
        issues = []
        feedback = ""
    else:
        score = 5 + next_round  # 分数随轮次递增（mock 收敛）
        passed = False
        issues = [f"mock issue r{next_round}: 张力不够", f"mock issue r{next_round}: 对话扁平"]
        feedback = f"轮{next_round}反馈：加强张力 + 让对话有潜台词（mock）"
    print(f"  ▶ [Ch{state.chapter_index}·critic_review] 第 {next_round} 轮：score={score}/10 passed={passed}")
    time.sleep(0.05)
    return {
        "revision_round": next_round,
        "review_score": score,
        "review_passed": passed,
        "review_issues": issues,
        "review_feedback": feedback,
    }


def revise(state: ChapterState) -> dict:
    """带 feedback 改稿。改完不直接 finalize，回 critic_review 再审。"""
    print(f"  ▶ [Ch{state.chapter_index}·revise] 按反馈改稿（轮 {state.revision_round}）")
    time.sleep(0.1)
    new_draft = state.draft + f"\n[r{state.revision_round} 修订: {state.review_feedback[:30]}]"
    print(f"  ✓ [Ch{state.chapter_index}·revise] 修订后 {len(new_draft)} 字")
    return {"draft": new_draft, "word_count": len(new_draft)}


def finalize(state: ChapterState) -> dict:
    """定稿。打 finalized 标记，附 reason（通过 / 达上限）。"""
    if state.review_passed:
        reason = f"passed at round {state.revision_round}"
    else:
        reason = f"max_rounds_exceeded ({state.max_rounds})"
    print(f"  ✓ [Ch{state.chapter_index}·finalize] 定稿 — {reason}")
    return {"finalized": True, "finalize_reason": reason}
