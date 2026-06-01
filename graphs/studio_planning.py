"""LangGraph Studio 入口模块。

Studio 期望：导出已 compile 的图对象（不带 checkpointer——Studio 自己接管）。

提供两个版本：
  · graph_mock     纯 mock，秒级跑完，不花 token，演示框架机制
  · graph_real     真节点（调 v1 agent + 真 LLM），跑完整规划期
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graphs.planning_full import build_planning_graph

# mock 演示版（Studio 启动后默认用这个，安全 + 秒级出结果）
graph_mock = build_planning_graph(checkpointer=None, mock=True, stepwise=False)

# 真节点版（消耗 token；只有真正要跑小说时用）
graph_real = build_planning_graph(checkpointer=None, mock=False, stepwise=False)
