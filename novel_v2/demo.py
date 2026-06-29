"""端到端演示 —— 零 API key、零外部服务即可运行：

    python -m novel_v2.demo

演示内容：
  A. RAG 记忆的完整增删改查(insert / query / upsert-改写 / delete-回滚)
  B. LangGraph 写作循环：canon-revise 自愈环 + 卷级 interrupt 人审 + 断点续跑
"""
from __future__ import annotations

from . import llm  # noqa: F401  (默认 mock 后端)
from .rag.indexer import RagMemory
from .state import initial_state


def banner(t: str) -> None:
    print("\n" + "=" * 64 + f"\n{t}\n" + "=" * 64)


def demo_rag_crud() -> None:
    banner("A. RAG 记忆 · 增删改查(CRUD)")
    rag = RagMemory()
    print(f"后端: {type(rag.store).__name__}  维度: {rag.embedder.dim}")

    # C(insert)
    rag.index_chapter(index=1, volume_index=1, title="第1章",
                      prose="少年林川在青崖宗外门修炼，引气入体，初窥练气一层。",
                      summary="林川入门，练气一层", characters=["林川"])
    rag.index_chapter(index=2, volume_index=1, title="第2章",
                      prose="林川在藏经阁获得残破剑诀，夜半偷练，剑光初成。",
                      summary="林川得剑诀", characters=["林川"])
    print(f"insert 后向量条数: {rag.count()}")

    # R(query)
    print("\nquery『剑诀 剑光』→")
    for h in rag.retrieve("林川 剑诀 剑光", top_k=2):
        print(f"  score={h.score:.3f} [{h.metadata['granularity']}] {h.document[:30]}")

    # U(upsert：第2章被改写)
    rag.reindex_chapter(index=2, volume_index=1, title="第2章",
                        prose="林川获得的并非剑诀，而是一卷古老刀法，刀气纵横。",
                        summary="林川得刀法(改)", characters=["林川"])
    print(f"\nupsert 改写第2章后条数: {rag.count()}(删旧增新，非累加)")
    print("query『刀法』→", [h.document[:20] for h in rag.retrieve("林川 刀法 刀气", top_k=1)])

    # D(回滚：删除第2章之后的记忆)
    removed = rag.rollback_to(max_index=1)
    print(f"\nrollback_to(1) 删除 {removed} 条第2章向量，剩余 {rag.count()} 条")


def demo_writing_loop() -> None:
    banner("B. LangGraph 写作循环 · 自愈环 + 人审 + 断点续跑")
    try:
        from langgraph.types import Command
        from .graph import build_graph, make_checkpointer
    except Exception as e:  # noqa: BLE001
        print(f"(跳过：未安装 langgraph —— {e})\n  pip install langgraph 后重试")
        return

    rag = RagMemory()
    graph = build_graph(rag, checkpointer=make_checkpointer())
    config = {"configurable": {"thread_id": "demo-novel"}, "recursion_limit": 200}

    init = initial_state(title="剑起青崖", genre="xianxia",
                         volume_size=3, target_chapters=5, debug_inject_flaw=True)
    print("目标: 写 5 章, 每 3 章一卷(第3章后触发卷级人审)\n")

    res = graph.invoke(init, config)
    # 命中 interrupt 则人审后续跑(演示断点恢复)
    while True:
        snap = graph.get_state(config)
        if not snap.next:                       # next 为空 → 全图执行完毕
            break
        payload = snap.tasks[0].interrupts[0].value
        print(f"  ⏸  HITL 暂停: {payload['message']}")
        print("  ▶  作者裁决 approve，从断点续跑…")
        res = graph.invoke(Command(resume="approve"), config)

    print("\n—— 执行日志 ——")
    for line in res["event_log"]:
        print(" ", line)
    print(f"\n完成章节: {[c['index'] for c in res['completed_chapters']]}")
    print(f"向量库记忆条数: {rag.count()}")
    print("注: 第2章初稿含占位违规 → canon 命中 → 自愈修订环已自动重写(见日志 revise)")


def main() -> None:
    try:                                  # Windows 控制台中文输出
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    demo_rag_crud()
    demo_writing_loop()
    banner("DONE")


if __name__ == "__main__":
    main()
