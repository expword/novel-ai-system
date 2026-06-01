"""验证 LangGraph 章级 cycle：critic 审校循环 ↔ revise 反复直到通过或达上限。

三个场景：
  A. 一次通过      （pass_at_round=1）：write → critic(pass) → finalize；revision_round=1
  B. 三轮才通过    （pass_at_round=3）：write → critic(fail) → revise → critic(fail) → revise → critic(pass) → finalize
                                          revision_round=3
  C. 永远不通过    （pass_at_round=10，max_rounds=3）：达上限强制 finalize
                                          revision_round=3, finalize_reason='max_rounds_exceeded'
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from langgraph.checkpoint.memory import InMemorySaver
from state_chapter import ChapterState
from graphs.chapter_subgraph import build_chapter_graph


def run_case(case_name: str, pass_at_round: int, max_rounds: int = 3,
              chapter_index: int = 1):
    os.environ["CHAPTER_CYCLE_PASS_AT_ROUND"] = str(pass_at_round)
    print(f"\n══ {case_name} （pass_at_round={pass_at_round}, max_rounds={max_rounds}）══")
    cp = InMemorySaver()
    g = build_chapter_graph(checkpointer=cp, mock=True)
    config = {"configurable": {"thread_id": f"{case_name}_ch{chapter_index}"}}
    initial = ChapterState(chapter_index=chapter_index, max_rounds=max_rounds)
    final = g.invoke(initial, config=config)
    print(f"  最终：finalized={final.get('finalized')}  revision_round={final.get('revision_round')}"
          f"  reason='{final.get('finalize_reason')}'")
    return final


def main():
    print("══ 测试 4：章级 critic cycle ══")

    # A. 一次通过
    a = run_case("场景A 一次通过", pass_at_round=1)
    assert a["finalized"] is True, "应 finalize"
    assert a["revision_round"] == 1, f"应 1 轮就过，实际 {a['revision_round']}"
    assert "passed" in a["finalize_reason"], "理由应是 passed"

    # B. 三轮才通过（critic 跑 3 次 + revise 跑 2 次）
    b = run_case("场景B 三轮才通过", pass_at_round=3)
    assert b["finalized"] is True
    assert b["revision_round"] == 3, f"应 3 轮，实际 {b['revision_round']}"
    assert "passed" in b["finalize_reason"]

    # C. 永远不通过：达上限强制收尾
    c = run_case("场景C 达上限强制收尾", pass_at_round=10, max_rounds=3)
    assert c["finalized"] is True
    assert c["revision_round"] == 3, f"应卡在上限 3，实际 {c['revision_round']}"
    assert "max_rounds_exceeded" in c["finalize_reason"]
    assert c["review_passed"] is False  # 没通过，是被上限赶下去

    print("\n══ ✓ 测试 4 通过：critic cycle 三种场景全过 ══")
    print(f"  场景A: 1 次 critic + 0 次 revise = 1 轮就 finalize")
    print(f"  场景B: 3 次 critic + 2 次 revise = 3 轮才 pass")
    print(f"  场景C: 3 次 critic + 3 次 revise（达上限）+ 强制 finalize")


if __name__ == "__main__":
    main()
