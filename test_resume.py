"""验证 LangGraph 框架的核心特性：跑一半崩溃后能从断点续跑（不重跑已完成 phase）。

流程：
  1. reset 项目
  2. 替换 mock_phase_0_5 → 故意抛 RuntimeError
  3. invoke graph → 跑到 0.5 时崩，但 -1 和 0 已落 SQLite
  4. get_state 查看：phases_done 应该是 ['-1', '0']，其它字段也已存
  5. 还原 mock_phase_0_5
  6. resume invoke（不传 input）→ 从 0.5 继续跑（不重跑 -1/0）
  7. 跑完后查 state：phases_done 应 ['-1', '0', '0.5', '0.6']
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from langgraph.checkpoint.sqlite import SqliteSaver
from state_v2 import NovelStateV2
from graphs.planning_g1 import build_g1_graph
import agents_v2.g1_mock_nodes as g1m

DB = str(Path(__file__).resolve().parent / "checkpoints.sqlite")
PROJECT = "test_resume"
CONFIG = {"configurable": {"thread_id": PROJECT}}


def reset_project():
    import sqlite3
    if not Path(DB).exists():
        return
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (PROJECT,))
    cur.execute("DELETE FROM writes WHERE thread_id = ?", (PROJECT,))
    conn.commit()
    conn.close()
    print(f"✓ 清掉 {PROJECT} 历史 checkpoint")


def main():
    print("══ 测试 2：中断恢复 ══\n")
    reset_project()

    # --- 第一次跑：phase_0_5 故意崩 ---
    original = g1m.mock_phase_0_5
    def broken(state):
        print("  💥 [MOCK·0.5] 故意抛错模拟崩溃")
        raise RuntimeError("simulated crash at phase_0.5")
    g1m.mock_phase_0_5 = broken

    initial = NovelStateV2(
        project_id=PROJECT, title="崩溃测试", genre="测试",
        theme="测试", intent_description="测试 intent",
    )

    print("─── 第一次 invoke（预期崩在 0.5）───")
    try:
        with SqliteSaver.from_conn_string(DB) as cp:
            g = build_g1_graph(checkpointer=cp, mock=True)
            g.invoke(initial, config=CONFIG)
        print("  ✗ 居然没崩！测试失败")
        return
    except RuntimeError as e:
        print(f"  ✓ 如期崩溃：{e}")

    # --- 检查 SQLite 里的 state ---
    print("\n─── 第一次崩溃后 get_state ───")
    with SqliteSaver.from_conn_string(DB) as cp:
        g = build_g1_graph(checkpointer=cp, mock=True)
        snap = g.get_state(CONFIG)
    v = snap.values
    print(f"  phases_done: {v.get('phases_done')}")
    print(f"  current_phase: {v.get('current_phase')}")
    print(f"  creative_intent 已存？{bool(v.get('creative_intent'))}")
    print(f"  concept_pitch 已存？{bool(v.get('concept_pitch'))}")
    print(f"  master_outline 应未存：{v.get('master_outline')}")
    print(f"  protagonist_journey 应未存：{v.get('protagonist_journey')}")
    expected_done = ["-1", "0"]
    if v.get("phases_done") != expected_done:
        print(f"  ✗ phases_done 不对，期望 {expected_done}")
        return
    print(f"  ✓ 前 2 节点已落盘，后 2 节点未跑")

    # --- 还原 mock，resume ---
    g1m.mock_phase_0_5 = original
    print("\n─── 还原 mock_phase_0_5，invoke(None) 续跑 ───")
    with SqliteSaver.from_conn_string(DB) as cp:
        g = build_g1_graph(checkpointer=cp, mock=True)
        final = g.invoke(None, config=CONFIG)

    print(f"\n─── 续跑后最终 state ───")
    print(f"  phases_done: {final.get('phases_done')}")
    print(f"  master_outline 已存？{bool(final.get('master_outline'))}")
    print(f"  protagonist_journey 已存？{bool(final.get('protagonist_journey'))}")

    if final.get("phases_done") == ["-1", "0", "0.5", "0.6"]:
        print("\n══ ✓ 测试 2 通过 ══")
    else:
        print(f"\n══ ✗ 测试 2 失败 ══")


if __name__ == "__main__":
    main()
