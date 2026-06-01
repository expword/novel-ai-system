"""阶段 4 破冰：调真 v1 agent 跑 G1 第 1 个 phase（意图分析），验证 adapter 桥能接通。

不走 LangGraph，直接调节点 func；这样：
  · 出错时 stack trace 最干净
  · 不消耗 LangGraph checkpoint 资源
  · 一次 LLM 调用 ≈ 10-30 秒 + 几分钱

成功 = adapter 设计正确（sys.path 注入 / project_context.set_project /
load_or_build_v1_state / to_jsonable）全部 work。
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from state_v2 import NovelStateV2
from agents_v2.g1_nodes import node_phase_minus1


INTENT = """
主角林砚是 35 岁的 AI 实验室研究员，意外车祸后穿越到大雍朝（虚构古代王朝）成为同名贫穷秀才沈砚。
穿越时电脑上未关的"豆包"网页（一个 AI 助手）随他意识一同来到古代，成为他脑内可随时调用的智能助手。
故事开场：父亲刚去世、母亲病重、家中欠下三十两高利贷，债主三日内来收房。
主角必须靠豆包提供的现代知识（化学、医药、商业、律法）在三天内筹款救母，
开启从寒门秀才到改革者的逆袭之路。题材：穿越商战 + 寒门重生 + AI 金手指。
"""


def main():
    state = NovelStateV2(
        project_id="test_real_min",
        title="真路径破冰测试",
        genre="穿越",
        theme="寒门重生",
        intent_description=INTENT.strip(),
    )
    print(f"═══ 调 node_phase_minus1 ═══")
    print(f"  intent 长度: {len(state.intent_description)} 字\n")

    import time
    t0 = time.time()
    try:
        patch = node_phase_minus1(state)
    except Exception as e:
        print(f"\n  ✗ adapter 桥失败：{type(e).__name__}: {e}")
        raise
    dt = time.time() - t0

    print(f"\n═══ 完成 ({dt:.1f}s) ═══")
    print(f"  返回 patch keys: {list(patch.keys())}")
    print(f"  phases_done: {patch.get('phases_done')}")
    ci = patch.get("creative_intent") or {}
    print(f"  creative_intent 字段数: {len(ci)}")
    print(f"    raw_description（前 60）: {(ci.get('raw_description') or '')[:60]}")
    print(f"    analyzed: {ci.get('analyzed')}")
    # 检查 analyzed_themes / motifs / 等 v1 字段
    for key in ("analyzed_themes", "motifs", "protagonist_archetype_hint",
                "selling_points", "target_audience"):
        if key in ci:
            val = ci[key]
            if isinstance(val, list):
                preview = str(val)[:80]
                print(f"    {key}: {len(val)} 项 — {preview}")
            else:
                print(f"    {key}: {str(val)[:80]}")
    print()
    print("✓ 阶段 4 破冰成功——adapter 桥真路径打通")


if __name__ == "__main__":
    main()
