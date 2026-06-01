"""验证 LangGraph 的 stepwise interrupt 机制 —— G1 跑完后自动暂停，等用户审核
后 resume 进 G2（mock G2 占位节点模拟阶段 2 入口）。

业务对应：原 director.py 用 _stepwise_checkpoint 调 save_state + SystemExit(0) 让
子进程退出、等用户点"继续"再重启子进程从 progress.json 续跑——又复杂又脆弱。
LangGraph 的 interrupt_after：

  compile(checkpointer=..., interrupt_after=["phase_0.6"])

graph.invoke 跑到 phase_0.6 完就停下返回 state，进程不退出。用户审核完调用
graph.invoke(None, config=同 thread_id) 自动从断点续到下一节点。

测试流程：
  1. 构造图：-1 → 0 → 0.5 → 0.6 → g2_start → END，interrupt_after=["phase_0.6"]
  2. invoke(initial)：应跑完 4 个 G1 节点后**暂停**，g2_start 不应跑
  3. get_state：phases_done = G1 完整 4 项；snap.next 应指 g2_start
  4. invoke(None, config=同 thread_id) 续跑：g2_start 才被执行
  5. 最终 phases_done = G1 4 项 + 'G2_start'
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from state_v2 import NovelStateV2
from agents_v2.g1_mock_nodes import (
    mock_phase_minus1, mock_phase_0, mock_phase_0_5, mock_phase_0_6,
)

DB = str(Path(__file__).resolve().parent / "checkpoints.sqlite")
PROJECT = "test_interrupt"
CONFIG = {"configurable": {"thread_id": PROJECT}}


def mock_g2_start(state: NovelStateV2) -> dict:
    """G2 入口占位——证明 interrupt resume 后下一节点真的会跑。"""
    print("  ▶ [MOCK·G2_start] 世界组开工（模拟）...")
    return {
        "phases_done": state.phases_done + ["G2_start"],
        "current_phase": "G2_start",
        "current_phase_label": "G2 世界组 开工",
    }


def build_with_interrupt(cp):
    """G1 + G2 入口；interrupt_after G1 末节点。"""
    b = StateGraph(NovelStateV2)
    b.add_node("phase_-1", mock_phase_minus1)
    b.add_node("phase_0", mock_phase_0)
    b.add_node("phase_0.5", mock_phase_0_5)
    b.add_node("phase_0.6", mock_phase_0_6)
    b.add_node("G2_start", mock_g2_start)
    b.add_edge(START, "phase_-1")
    b.add_edge("phase_-1", "phase_0")
    b.add_edge("phase_0", "phase_0.5")
    b.add_edge("phase_0.5", "phase_0.6")
    b.add_edge("phase_0.6", "G2_start")
    b.add_edge("G2_start", END)
    return b.compile(checkpointer=cp, interrupt_after=["phase_0.6"])


def reset_project():
    import sqlite3
    if not Path(DB).exists():
        return
    conn = sqlite3.connect(DB); cur = conn.cursor()
    cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (PROJECT,))
    cur.execute("DELETE FROM writes WHERE thread_id = ?", (PROJECT,))
    conn.commit(); conn.close()
    print(f"✓ 清掉 {PROJECT} 历史 checkpoint\n")


def main():
    print("══ 测试 3：stepwise interrupt（G1 末尾暂停 + resume 进 G2）══\n")
    reset_project()

    initial = NovelStateV2(
        project_id=PROJECT, title="Interrupt测试", genre="测试",
        theme="测试", intent_description="测试 stepwise interrupt",
    )

    print("─── 第 1 次 invoke：跑完 G1 应在 phase_0.6 后暂停（不跑 G2_start）───")
    with SqliteSaver.from_conn_string(DB) as cp:
        g = build_with_interrupt(cp)
        result1 = g.invoke(initial, config=CONFIG)
    print(f"\n  invoke 返回 state.phases_done = {result1.get('phases_done')}")

    expected_g1 = ["-1", "0", "0.5", "0.6"]
    if result1.get("phases_done") != expected_g1:
        print(f"  ✗ 期望 phases_done={expected_g1}")
        return
    if "G2_start" in result1.get("phases_done", []):
        print("  ✗ G2_start 不应被跑（interrupt 失败）")
        return
    print(f"  ✓ G1 完整跑完，G2_start 没跑（interrupt 生效）")

    print("\n─── 用 get_state 查 snap.next 看下一个待跑节点 ───")
    with SqliteSaver.from_conn_string(DB) as cp:
        g = build_with_interrupt(cp)
        snap = g.get_state(CONFIG)
    print(f"  snap.next = {snap.next}")
    print(f"  snap.values.phases_done = {snap.values.get('phases_done')}")

    if "G2_start" not in (snap.next or ()):
        print(f"  ⚠ 期望 snap.next 含 'G2_start'")
    else:
        print(f"  ✓ 下一节点 G2_start 在等")

    print("\n─── 第 2 次 invoke(None)：resume 续跑应执行 G2_start ───")
    with SqliteSaver.from_conn_string(DB) as cp:
        g = build_with_interrupt(cp)
        result2 = g.invoke(None, config=CONFIG)
    print(f"\n  续跑后 phases_done = {result2.get('phases_done')}")

    expected_full = ["-1", "0", "0.5", "0.6", "G2_start"]
    if result2.get("phases_done") == expected_full:
        print(f"\n══ ✓ 测试 3 通过：stepwise interrupt + resume 流程完整 ══")
    else:
        print(f"\n══ ✗ 测试 3 失败：期望 {expected_full}，实际 {result2.get('phases_done')} ══")


if __name__ == "__main__":
    main()
