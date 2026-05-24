"""
AgentContract —— agent 与 state 的形式契约。

设计动机（治本而非治标）：
  · ARCHITECTURE.md D7 规定 agent 不直接通信，靠 state 间接交互
  · 但每个 agent 读哪些 state 字段、写哪些、产出该满足什么不变式，
    全靠程序员记得——这是漏 bug 的根源（例：volume_planner 漏读
    power_system.special_abilities → outline.goal 出现违规模式；
    _replace_power_system 漏写 lifecycle_nodes → 静默清零）
  · 把这些边界声明成机器可读的契约，让模块自我描述

使用模式（轻量级 / 渐进式）：

  # agent 顶部声明
  CONTRACT = AgentContract(
      name="volume_planner.plan_volume_chapters",
      inputs=[
          "volumes",                            # 卷主题/对手/叙事线
          "power_system.special_abilities",     # 真 AI asset 锚点
          "world_canon",                        # 朝代/根地理
          "factions[*].name",                   # 已定义势力名
      ],
      outputs=["volumes[*].chapter_outlines"],
      invariants=[
          # 每条产出必须能过 canon 校验
          lambda state: _all_outlines_pass_canon(state),
      ],
      notes="生成 outline 前必须先调 extract_world_canon",
  )

  # 主函数照常写，contract 是文档 + 可选运行时校验
  def plan_volume_chapters(state, volume_index):
      ...

可以纯文档用（不调任何 contract 函数）——以后想加运行时校验，调
`validate_contract(CONTRACT, state)` 即可。

设计原则：
  · 不强制——加 contract 是文档化第一步，运行时校验是奢侈品
  · 不破坏——只读 state，不改 state
  · 通用框架——任何 agent 可用，不绑题材
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Callable, Any

# 类型别名：invariant 函数签名
Invariant = Callable[["NovelState"], list[dict]]  # noqa: F821


@dataclass
class AgentContract:
    """Agent 与 state 的边界声明。

    inputs/outputs 是 state 字段的 dot 路径，支持简单语法：
      · "world_setting"               顶层标量字段
      · "world_canon.dynasty_name"    嵌套 dataclass 字段
      · "volumes[*].chapter_outlines" 列表里每个元素的字段（用 [*]）
      · "characters[*].name"          列表元素的标量
    invariants 是 (state -> list[Issue]) 的 callable，issue 字典形如：
      {"severity": "warn"|"error", "message": "..."}
    """
    name: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    invariants: list[Invariant] = field(default_factory=list)
    notes: str = ""


# ───── 路径取值 / 校验 ─────

_LIST_WILDCARD = re.compile(r"^([^\[\]]+)\[\*\]$")


def get_path(state: Any, path: str) -> Any:
    """按 dot path 取 state 字段——只用于"是否存在/非空"的轻量检查。

    支持 [*] 通配（返回所有元素的该字段值的列表）。失败返回 None。
    """
    parts = path.split(".")
    cur = state
    for p in parts:
        if cur is None:
            return None
        m = _LIST_WILDCARD.match(p)
        if m:
            attr = m.group(1)
            lst = getattr(cur, attr, None)
            if not isinstance(lst, list):
                return None
            cur = lst
            # 剩下的路径应用到每个元素
            continue
        if isinstance(cur, list):
            # 取列表中每个元素的同一字段
            cur = [getattr(item, p, None) for item in cur]
        else:
            cur = getattr(cur, p, None)
    return cur


def is_field_present(state: Any, path: str) -> bool:
    """字段是否存在且非空（None / 空字符串 / 空列表都视为缺失）。"""
    v = get_path(state, path)
    if v is None:
        return False
    if isinstance(v, (str, list, dict)) and len(v) == 0:
        return False
    return True


# ───── 校验入口 ─────

def validate_contract(contract: AgentContract, state: Any) -> list[dict]:
    """运行时校验：inputs 字段都就绪 + 跑 invariants。

    返回 issues 列表。空 = 全过。不阻塞调用方——由调用方决定是否报警/中断。
    """
    issues: list[dict] = []
    for path in contract.inputs:
        if not is_field_present(state, path):
            issues.append({
                "severity": "warn",
                "kind": "missing_input",
                "agent": contract.name,
                "path": path,
                "message": f"{contract.name} 声明依赖 inputs 字段 '{path}'，但当前 state 缺失或为空",
            })
    for inv in contract.invariants:
        try:
            inv_issues = inv(state) or []
            for iss in inv_issues:
                iss.setdefault("agent", contract.name)
                issues.append(iss)
        except Exception as e:
            issues.append({
                "severity": "warn",
                "kind": "invariant_exception",
                "agent": contract.name,
                "message": f"invariant 校验抛异常 {type(e).__name__}: {e}",
            })
    return issues


def surface_contract_issues(issues: list[dict], source: str) -> None:
    """把契约校验 issues 推到 progress_status warnings（按 [[feedback_surface_errors]]）。"""
    if not issues:
        return
    try:
        from persistence.checkpoint import add_progress_warning
    except Exception:
        return
    # 按 severity 聚合
    errors = [i for i in issues if i.get("severity") == "error"]
    warns = [i for i in issues if i.get("severity") != "error"]
    if errors:
        preview = "；".join((i.get("message") or "")[:120] for i in errors[:3])
        add_progress_warning(
            level="error",
            source=f"contract:{source}",
            message=f"契约校验 {len(errors)} 处 critical：{preview}",
        )
    if warns:
        preview = "；".join((i.get("message") or "")[:120] for i in warns[:3])
        add_progress_warning(
            level="warn",
            source=f"contract:{source}:warn",
            message=f"契约校验 {len(warns)} 处提示：{preview}",
        )


# ───── 注册表（可选）─────
#  agent 把它的 CONTRACT 注册进来，便于工具批量审计 / 测试
_REGISTRY: dict[str, AgentContract] = {}


def register(contract: AgentContract) -> AgentContract:
    """注册 contract 到全局表——便于扫描"哪些 agent 已声明契约"。返回 contract 本身。

    Pattern: CONTRACT = register(AgentContract(name="...", ...))
    """
    _REGISTRY[contract.name] = contract
    return contract


def all_contracts() -> dict[str, AgentContract]:
    """所有已注册 agent 契约——供工具扫描用。"""
    return dict(_REGISTRY)
