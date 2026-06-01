"""V1 ↔ V2 适配层。

阶段 1 策略：**复用** F:/xiaoshuo（v1）已有的 agent 函数和 prompt，
不重写 LLM 调用——v1 agent 已经经过反复调优。adapter 做的事：

  1. sys.path 注入 v1 项目目录，让 v2 能 import 旧 agents/* 模块
  2. 让 v1 project_context 指向 v2 项目目录（产物落到 F:/xiaoshuo_v2/projects/<id>/）
     · v1 写 checkpoint/state.json + 拆分 state/ 子目录——v2 不读它
     · v2 用 LangGraph SqliteSaver 做真正的状态持久化（checkpoints.sqlite）
     · 两套持久化并存，阶段 4 切断 v1 时删 v1 产物即可

  3. v2 节点 func 调 _run_v1_agent(...) → 内部加载 v1 NovelState、调旧 agent 函数、
     提取产物字段转 dict 返回 LangGraph patch。

主线接口：
  ensure_v1_env(project_id)                     —— 启动时调一次，绑路径
  load_or_build_v1_state(v2_state)              —— 从 v1 checkpoint 加载或按 v2 输入新建
  to_jsonable(obj)                              —— v1 dataclass → JSON-able dict
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

V1_ROOT = Path(r"F:/xiaoshuo")
V2_ROOT = Path(r"F:/xiaoshuo_v2")


def ensure_v1_env(project_id: str) -> None:
    """在 v2 进程内启用 v1 模块路径 + 把 v1 的 project_context 绑到 v2 项目目录。
    幂等：多次调用安全。每次都重新 set_project（即便已设过），避免主线切项目时漏切。
    """
    # 1. sys.path 注入
    v1_str = str(V1_ROOT)
    if v1_str not in sys.path:
        sys.path.insert(0, v1_str)

    # 2. cwd 切到 v2（v1 PROJECTS_ROOT = "projects" 是相对路径，
    #    在 v2 cwd 下解析就是 v2/projects/...）
    if Path.cwd().resolve() != V2_ROOT.resolve():
        os.chdir(V2_ROOT)

    # 3. v1 project_context 绑项目（影响 checkpoint / control / output 路径）
    import project_context as pctx  # type: ignore
    pctx.set_project(project_id)
    pctx.ensure_project_dirs(project_id)


def load_or_build_v1_state(v2_state):
    """根据 v2 state（已设 title/genre/theme/intent_description）拿到一个 v1 NovelState。

    流程：
      · 优先 load_state()——如果 v2 项目跑过前面 phase，checkpoint 已有进度，加载它
      · 否则按 v2 输入新建（标题/题材/主题/意图）
      · 把 v2.intent_description 同步到 v1.creative_intent.raw_description
        （v2 输入是权威，v1 checkpoint 上的旧值会被覆盖）
    """
    from state import NovelState, CreativeIntent  # type: ignore
    from checkpoint import load_state             # type: ignore

    v1 = load_state()
    if v1 is None:
        v1 = NovelState(
            title=v2_state.title or "未命名",
            genre=v2_state.genre or "",
            theme=v2_state.theme or "",
        )
    # 用 v2 元数据覆盖（v2 是权威）
    if v2_state.title:
        v1.title = v2_state.title
    if v2_state.genre:
        v1.genre = v2_state.genre
    if v2_state.theme:
        v1.theme = v2_state.theme

    if v2_state.intent_description:
        if not v1.creative_intent:
            v1.creative_intent = CreativeIntent(raw_description=v2_state.intent_description)
        else:
            v1.creative_intent.raw_description = v2_state.intent_description
    return v1


def to_jsonable(obj):
    """v1 dataclass / Enum / 嵌套 → JSON-able 字典/原值。复用 v1 自己的 _to_json。"""
    from checkpoint import _to_json  # type: ignore
    return _to_json(obj)
