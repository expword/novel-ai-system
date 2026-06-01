"""V2 CLI 入口 —— 跑 G1 意图组。

用法：
  python runner.py new --project niu_ma --title "牛马重活一世" --genre "穿越商战" --theme "寒门重生" --intent-file intent.txt
  python runner.py run --project niu_ma          # 从断点继续
  python runner.py state --project niu_ma        # 看当前 state
  python runner.py reset --project niu_ma        # 清掉项目 checkpoint 重新开始

每个项目对应 LangGraph 一个 thread_id（= project_id）。state 存在
F:/xiaoshuo_v2/checkpoints.sqlite。
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# 让本目录的模块能 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台 UTF-8（项目里 ✓ ✗ ⚠ 等字符密集）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from langgraph.checkpoint.sqlite import SqliteSaver

from state_v2 import NovelStateV2
from graphs.planning_g1 import build_g1_graph
from graphs.planning_full import build_planning_graph


CHECKPOINT_DB = str(Path(__file__).resolve().parent / "checkpoints.sqlite")


def _config(project_id: str) -> dict:
    """LangGraph thread_id = project_id；每个项目一条 checkpoint 线。"""
    return {"configurable": {"thread_id": project_id}}


def _build_graph(args, cp):
    """根据 CLI flag 决定图形态：
    · --g1-only：只 G1 子图（早期验证用）
    · 否则：完整规划图（G1+G2，阶段 3/4 会再扩到 G3/G4 + 章级）
    · --auto：关闭 stepwise interrupt（一路跑通不暂停，回归测试用）
    · --mock：用 mock 节点（不调 v1 agent）
    """
    mock = getattr(args, "mock", False)
    if getattr(args, "g1_only", False):
        return build_g1_graph(checkpointer=cp, mock=mock)
    stepwise = not getattr(args, "auto", False)
    return build_planning_graph(checkpointer=cp, mock=mock, stepwise=stepwise)


def cmd_new(args):
    """新建项目并跑 G1。如果 thread 已存在会从断点继续，不会重跑已完成 phase。"""
    intent_desc = ""
    if args.intent_file:
        intent_desc = Path(args.intent_file).read_text(encoding="utf-8")

    initial = NovelStateV2(
        project_id=args.project,
        title=args.title,
        genre=args.genre,
        theme=args.theme,
        intent_description=intent_desc,
    )
    print(f"═══ 启动 G1 ═══  项目={args.project}  题材={args.genre}")
    print(f"  intent: {intent_desc[:80]}{'...' if len(intent_desc) > 80 else ''}\n")

    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        final = graph.invoke(initial, config=_config(args.project))
    print(f"\n═══ G1 完成 ═══")
    print(f"  phases_done: {final.get('phases_done')}")
    print(f"  current_phase_label: {final.get('current_phase_label')}")
    if final.get("warnings"):
        print(f"  warnings: {len(final['warnings'])} 条")


def cmd_run(args):
    """从断点继续跑。stepwise interrupt 暂停后用本命令 resume；
    崩溃恢复也走这个（LangGraph 自动从断点拾起）。"""
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        final = graph.invoke(None, config=_config(args.project))
    print(f"\n═══ 续跑完成 ═══")
    print(f"  phases_done: {final.get('phases_done')}")
    print(f"  current: {final.get('current_phase_label')}")


def cmd_state(args):
    """打印项目当前 state（从 SQLite checkpoint 读）"""
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        snap = graph.get_state(_config(args.project))
    if not snap or not snap.values:
        print(f"项目 {args.project} 尚无 state")
        return
    v = snap.values
    print(f"═══ 项目 {args.project} 当前 state ═══")
    print(f"  标题: {v.get('title')}")
    print(f"  题材: {v.get('genre')}  主题: {v.get('theme')}")
    print(f"  intent: {(v.get('intent_description') or '')[:80]}...")
    print(f"  phases_done: {len(v.get('phases_done') or [])} 项 → {v.get('phases_done')}")
    print(f"  current: {v.get('current_phase_label')}")
    print(f"  current_volume_index: {v.get('current_volume_index')} / total {len(v.get('volumes') or [])}")
    print()
    print("  ── G1 产物 ──")
    for fld in ("creative_intent", "concept_pitch", "trope_library",
                "tone_manual", "master_outline", "protagonist_journey"):
        val = v.get(fld) or {}
        has = "✓" if val else "✗"
        size = len(json.dumps(val, ensure_ascii=False)) if val else 0
        print(f"    {has} {fld}: {size} 字符")
    print("  ── G2 产物 ──")
    for fld in ("power_system", "volumes", "factions", "world_setting",
                "world_checklist_gaps", "geography", "timeline", "economy"):
        val = v.get(fld)
        is_empty = (not val) if not isinstance(val, list) else (len(val) == 0)
        has = "✗" if is_empty else "✓"
        size = len(json.dumps(val, ensure_ascii=False)) if val else 0
        print(f"    {has} {fld}: {size} 字符")
    print("  ── G3 产物 ──")
    for fld in ("characters", "relationship_web", "character_arcs"):
        val = v.get(fld)
        is_empty = (not val) if not isinstance(val, list) else (len(val) == 0)
        has = "✗" if is_empty else "✓"
        size = len(json.dumps(val, ensure_ascii=False)) if val else 0
        print(f"    {has} {fld}: {size} 字符")
    print("  ── G4 产物 ──")
    for fld in ("global_lines", "volume_lines", "conflict_ladder",
                "satisfaction_points", "rhythm_plans", "emotion_curve",
                "twist_system", "foreshadow_items", "red_herrings", "fortunes"):
        val = v.get(fld)
        is_empty = (not val) if not isinstance(val, list) else (len(val) == 0)
        has = "✗" if is_empty else "✓"
        size = len(json.dumps(val, ensure_ascii=False)) if val else 0
        print(f"    {has} {fld}: {size} 字符")


def cmd_show(args):
    """查看某字段的具体内容（不只是大小）。"""
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        snap = graph.get_state(_config(args.project))
    if not snap or not snap.values:
        print(f"项目 {args.project} 尚无 state")
        return
    v = snap.values
    fld = args.field
    if fld not in v:
        print(f"字段 {fld} 不存在；可用字段：")
        for k in sorted(v.keys()):
            print(f"  · {k}")
        return
    val = v[fld]
    if isinstance(val, (dict, list)):
        text = json.dumps(val, ensure_ascii=False, indent=2)
    else:
        text = str(val)
    if args.full:
        print(text)
    else:
        # 默认截到 args.head 字符（防终端淹没）
        limit = max(100, args.head)
        if len(text) <= limit:
            print(text)
        else:
            print(text[:limit])
            print(f"\n... [字段共 {len(text)} 字符；用 --full 查看完整内容]")


def cmd_export(args):
    """导出整个 state 到 JSON 文件，方便离线查看 / 备份。"""
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        snap = graph.get_state(_config(args.project))
    if not snap or not snap.values:
        print(f"项目 {args.project} 尚无 state")
        return
    out_path = args.out or f"{args.project}_state.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snap.values, f, ensure_ascii=False, indent=2)
    print(f"✓ 已导出 state 到 {out_path}（{sum(1 for _ in open(out_path, encoding='utf-8'))} 行）")


def cmd_list(args):
    """列出 SQLite 里所有项目（thread_id）+ 各项目进度概览。"""
    import sqlite3
    if not Path(CHECKPOINT_DB).exists():
        print("checkpoint 库不存在")
        return
    conn = sqlite3.connect(CHECKPOINT_DB)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id"
    ).fetchall()
    conn.close()
    if not rows:
        print("（没有任何项目）")
        return
    print(f"═══ 共 {len(rows)} 个项目 ═══")
    for thread_id, _ in rows:
        # 用 get_state 拿进度
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
            graph = _build_graph(args, cp)
            snap = graph.get_state({"configurable": {"thread_id": thread_id}})
        if snap and snap.values:
            v = snap.values
            phases = len(v.get("phases_done") or [])
            ch = len(v.get("chapters_done") or [])
            title = v.get("title", "")
            cur_label = v.get("current_phase_label", "")
            print(f"  · {thread_id}  '{title}'  phases={phases}  chapters={ch}  ({cur_label})")
        else:
            print(f"  · {thread_id}  (空 state)")


def cmd_stream(args):
    """流式跑——每个节点完成立刻输出 state 变化，而不是等全部完成。

    LangGraph 原生 .stream() 返回 (node_name, state_patch) 序列。
    比 .invoke() 多了"实时看每步产物"的能力。崩溃/中断时也能看到崩前最后一步。
    """
    if args.intent_file:
        intent_desc = Path(args.intent_file).read_text(encoding="utf-8")
    else:
        intent_desc = ""

    is_new = args.intent_file is not None
    if is_new:
        initial = NovelStateV2(
            project_id=args.project,
            title=args.title or args.project,
            genre=args.genre or "",
            theme=args.theme or "",
            intent_description=intent_desc,
        )
        print(f"═══ 流式启动 ═══  项目={args.project}")
    else:
        initial = None  # resume 模式
        print(f"═══ 流式续跑 ═══  项目={args.project}")

    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        # stream_mode="updates" 表示只输出"本节点对 state 的修改 patch"
        # stream_mode="values" 表示每次输出完整 state（更大但更全）
        mode = args.mode
        for chunk in graph.stream(initial, config=_config(args.project), stream_mode=mode):
            if mode == "updates":
                # chunk = {node_name: patch_dict}
                for node_name, patch in chunk.items():
                    keys = list(patch.keys())[:6]
                    print(f"\n▶ 节点 [{node_name}] 完成 → patch keys: {keys}")
                    # 简短显示几个关键字段
                    for k in ("phases_done", "current_phase_label",
                              "current_volume_index", "current_chapter_index"):
                        if k in patch:
                            val = patch[k]
                            preview = val if not isinstance(val, list) else (
                                f"[共 {len(val)} 项] {val[-3:]}" if len(val) > 3 else val
                            )
                            print(f"    {k}: {preview}")
            else:
                # values 模式：chunk 是完整 state dict
                phases = chunk.get("phases_done") or []
                cur = chunk.get("current_phase_label", "")
                print(f"\n▶ 当前 phases_done={len(phases)} 项  cur={cur}")
    print("\n✓ 流式跑完")


def cmd_history(args):
    """看项目完整执行历史——LangGraph get_state_history 列出每个 checkpoint。

    每个 checkpoint = 某个节点跑完后的快照。可以回放整个执行路径。
    """
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        snaps = list(graph.get_state_history(_config(args.project)))
    if not snaps:
        print(f"项目 {args.project} 没有任何 checkpoint")
        return
    # snaps 按时间倒序，从新到旧；我们按正序展示
    snaps = list(reversed(snaps))
    print(f"═══ 项目 {args.project} 共 {len(snaps)} 个 checkpoint ═══\n")
    limit = args.limit
    show = snaps[-limit:] if limit > 0 else snaps
    for i, snap in enumerate(show, start=len(snaps) - len(show) + 1):
        v = snap.values or {}
        ts = getattr(snap, "created_at", "") or ""
        next_nodes = list(snap.next or [])
        cur = v.get("current_phase_label", "")
        phases = len(v.get("phases_done") or [])
        chapters = len(v.get("chapters_done") or [])
        marker = "▶" if next_nodes else "✓"
        print(f"{marker} #{i:3d}  {ts[:19] if ts else '':19s}  "
              f"phases={phases:3d}  chapters={chapters:3d}  "
              f"next={next_nodes if next_nodes else '(END)'}  "
              f"cur='{cur}'")
    if limit < len(snaps):
        print(f"\n（只显示最后 {limit} 条；用 --limit 0 看全部）")


def cmd_graph(args):
    """把图结构画出来——输出 mermaid 文本（粘到 mermaid.live 网站可视化）或 PNG。"""
    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as cp:
        graph = _build_graph(args, cp)
        gobj = graph.get_graph()
    if args.format == "mermaid":
        mermaid_text = gobj.draw_mermaid()
        if args.out:
            Path(args.out).write_text(mermaid_text, encoding="utf-8")
            print(f"✓ mermaid 已写入 {args.out}")
            print("  可贴到 https://mermaid.live 在线渲染")
        else:
            print(mermaid_text)
    elif args.format == "png":
        out_path = args.out or "graph.png"
        try:
            png = gobj.draw_mermaid_png()
            Path(out_path).write_bytes(png)
            print(f"✓ PNG 已写入 {out_path}")
        except Exception as e:
            print(f"⚠ PNG 渲染失败：{type(e).__name__}: {e}")
            print("  fallback 到 mermaid 文本：")
            print(gobj.draw_mermaid())


def cmd_reset(args):
    """删除该 project_id 的所有 checkpoint。"""
    import sqlite3
    if not Path(CHECKPOINT_DB).exists():
        print(f"checkpoint 库不存在，无需清理")
        return
    conn = sqlite3.connect(CHECKPOINT_DB)
    cur = conn.cursor()
    # LangGraph SqliteSaver 的表名 checkpoints / writes
    n1 = cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (args.project,)).rowcount
    n2 = cur.execute("DELETE FROM writes WHERE thread_id = ?", (args.project,)).rowcount
    conn.commit()
    conn.close()
    print(f"✓ 已清理项目 {args.project} 的 checkpoint：checkpoints={n1} 行, writes={n2} 行")


def main():
    parser = argparse.ArgumentParser(description="xiaoshuo_v2 CLI（LangGraph 版）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="新建项目跑 G1")
    p_new.add_argument("--project", required=True)
    p_new.add_argument("--title", default="未命名")
    p_new.add_argument("--genre", default="")
    p_new.add_argument("--theme", default="")
    p_new.add_argument("--intent-file", help="作者意图文本文件（UTF-8）")
    p_new.add_argument("--mock", action="store_true",
                       help="不调 v1 agent / LLM，跑 mock 节点（只验证框架机制）")
    p_new.add_argument("--g1-only", dest="g1_only", action="store_true",
                       help="只跑 G1 子图（早期验证用）；默认跑完整规划图")
    p_new.add_argument("--auto", action="store_true",
                       help="关闭 stepwise interrupt，一路跑通不暂停（回归测试用）")
    p_new.set_defaults(func=cmd_new)

    p_run = sub.add_parser("run", help="从断点继续跑（stepwise resume / 崩溃恢复都用这个）")
    p_run.add_argument("--project", required=True)
    p_run.add_argument("--mock", action="store_true")
    p_run.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_run.add_argument("--auto", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_state = sub.add_parser("state", help="查看当前 state")
    p_state.add_argument("--project", required=True)
    p_state.add_argument("--mock", action="store_true")
    p_state.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_state.set_defaults(func=cmd_state)

    p_show = sub.add_parser("show", help="看某字段的具体内容（state 命令的细化版）")
    p_show.add_argument("--project", required=True)
    p_show.add_argument("--field", required=True, help="字段名，如 creative_intent / master_outline")
    p_show.add_argument("--head", type=int, default=2000, help="默认显示前 N 字符（防淹没）")
    p_show.add_argument("--full", action="store_true", help="不截断，输出完整内容")
    p_show.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_export = sub.add_parser("export", help="导出整个 state 到 JSON 文件")
    p_export.add_argument("--project", required=True)
    p_export.add_argument("--out", help="输出文件路径（默认 <project>_state.json）")
    p_export.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_export.set_defaults(func=cmd_export)

    p_list = sub.add_parser("list", help="列出 SQLite 里所有项目")
    p_list.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_list.set_defaults(func=cmd_list)

    # ── 流式跑 ──
    p_stream = sub.add_parser("stream", help="流式跑——每个节点完成立刻输出 state 变化（vs invoke 等全部完成）")
    p_stream.add_argument("--project", required=True)
    p_stream.add_argument("--title", default="")
    p_stream.add_argument("--genre", default="")
    p_stream.add_argument("--theme", default="")
    p_stream.add_argument("--intent-file", help="提供则按 new 模式启动；不提供按 run 续跑")
    p_stream.add_argument("--mode", choices=["updates", "values"], default="updates",
                          help="updates=只输出本节点 patch（默认）；values=每步输出完整 state")
    p_stream.add_argument("--mock", action="store_true")
    p_stream.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_stream.add_argument("--auto", action="store_true")
    p_stream.set_defaults(func=cmd_stream)

    # ── 执行历史 ──
    p_hist = sub.add_parser("history", help="看项目完整执行历史（每个 checkpoint = 某节点跑完后的快照）")
    p_hist.add_argument("--project", required=True)
    p_hist.add_argument("--limit", type=int, default=30, help="只显示最后 N 条（0=全部）")
    p_hist.add_argument("--mock", action="store_true")
    p_hist.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_hist.set_defaults(func=cmd_history)

    # ── 图可视化 ──
    p_graph = sub.add_parser("graph", help="导出图结构（mermaid 或 PNG）")
    p_graph.add_argument("--format", choices=["mermaid", "png"], default="mermaid")
    p_graph.add_argument("--out", help="输出文件路径（mermaid 默认打印到屏幕；png 默认 graph.png）")
    p_graph.add_argument("--mock", action="store_true")
    p_graph.add_argument("--g1-only", dest="g1_only", action="store_true")
    p_graph.set_defaults(func=cmd_graph)

    p_reset = sub.add_parser("reset", help="清掉项目所有 checkpoint")
    p_reset.add_argument("--project", required=True)
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
