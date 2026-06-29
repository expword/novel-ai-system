"""真实生成入口 —— 接 user_models.json 的真实模型，逐章生成并落盘。

    python -m novel_v2.run --title 代码修仙 --chapters 3 --auto-approve \
        --premise "现代程序员穿越修仙世界，用工程思维在宗门崛起"

需要 user_models.json(默认读项目根，或用 NOVEL_V2_MODELS 指定路径)与 langgraph。
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config
from .graph import build_graph, make_checkpointer
from .state import initial_state


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="代码修仙")
    ap.add_argument("--premise",
                    default="现代程序员林川意外穿越到修仙世界，凭借严密的逻辑与工程思维，在青崖宗一步步崛起。")
    ap.add_argument("--genre", default="xianxia")
    ap.add_argument("--chapters", type=int, default=3)
    ap.add_argument("--volume-size", type=int, default=3)
    ap.add_argument("--out", default="output")
    ap.add_argument("--models", default=None, help="user_models.json 路径")
    ap.add_argument("--sqlite", default=None, help="跨进程断点续跑的 checkpoint 库路径")
    ap.add_argument("--auto-approve", action="store_true", help="HITL 关卡自动通过")
    args = ap.parse_args()

    from langgraph.types import Command

    rag, model = config.configure_real(models_path=args.models)
    out_dir = os.path.join(args.out, args.title)
    print(f"LLM={model}  Embedder=dim{rag.embedder.dim}  Store={type(rag.store).__name__}")
    print(f"输出目录: {out_dir}\n开始生成 {args.chapters} 章…\n")

    graph = build_graph(rag, checkpointer=make_checkpointer(args.sqlite))
    cfg = {"configurable": {"thread_id": args.title}, "recursion_limit": 200}
    init = initial_state(title=args.title, premise=args.premise, genre=args.genre,
                         output_dir=out_dir, volume_size=args.volume_size,
                         target_chapters=args.chapters)

    res = graph.invoke(init, cfg)
    while True:
        snap = graph.get_state(cfg)
        if not snap.next:
            break
        payload = snap.tasks[0].interrupts[0].value
        print(f"\n⏸  HITL: {payload['message']}")
        decision = "approve" if args.auto_approve else (
            input("  作者裁决 [approve/revise]，回车=approve: ").strip() or "approve")
        res = graph.invoke(Command(resume=decision), cfg)

    print("\n—— 执行日志 ——")
    for line in res["event_log"]:
        print(" ", line)
    print(f"\n完成 {len(res['completed_chapters'])} 章，正文已写入 {out_dir}")


if __name__ == "__main__":
    main()
