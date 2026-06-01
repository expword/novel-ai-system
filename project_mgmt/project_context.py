"""
项目上下文 —— 多小说支持的路径管理核心。

每本小说一个 project_id，存在 projects/<id>/ 下：
    projects/<id>/
      meta.json                  — 项目元数据（标题/题材/创建时间等）
      checkpoint/state.json
      checkpoint/progress.json
      checkpoint/history/        — 版本快照
      checkpoint/pending_approvals/
      plans/                     — 规划文档
      vol01/, vol02/, ...        — 章节正文
      control/                   — 进程控制文件（pid/pause/stop）

通过 `set_project(pid)` 切换，会把 checkpoint/config/version_control/human_in_loop
这几个模块的路径常量动态改写到对应项目下。

使用方式：
- 子进程（main.py 跑 director）：通过环境变量 XIAOSHUO_PROJECT_ID 设定
- Web 服务：每个请求前调 `set_project(...)` 切换
"""
from __future__ import annotations
import os
from contextvars import ContextVar

# 默认项目 id——兼容老的 `output/` 目录结构（映射为 "main"）
DEFAULT_PROJECT_ID = "main"

_current_project_var: ContextVar[str] = ContextVar(
    "xiaoshuo_current_project",
    default=os.environ.get("XIAOSHUO_PROJECT_ID", DEFAULT_PROJECT_ID),
)
_paths_initialized: bool = False


def current() -> str:
    """当前项目 id。"""
    return _current_project_var.get()


def project_dir(project_id: str = None) -> str:
    pid = project_id or current()
    return f"projects/{pid}"


def checkpoint_dir(project_id: str = None) -> str:
    return f"{project_dir(project_id)}/checkpoint"


def state_file(project_id: str = None) -> str:
    return f"{checkpoint_dir(project_id)}/state.json"


def progress_file(project_id: str = None) -> str:
    return f"{checkpoint_dir(project_id)}/progress.json"


def history_dir(project_id: str = None) -> str:
    return f"{checkpoint_dir(project_id)}/history"


def approval_dir(project_id: str = None) -> str:
    return f"{checkpoint_dir(project_id)}/pending_approvals"


def plans_dir(project_id: str = None) -> str:
    return f"{project_dir(project_id)}/plans"


def control_dir(project_id: str = None) -> str:
    return f"{project_dir(project_id)}/control"


def pid_file(project_id: str = None) -> str:
    return f"{control_dir(project_id)}/running.pid"


def pause_flag(project_id: str = None) -> str:
    return f"{control_dir(project_id)}/pause.flag"


def stop_flag(project_id: str = None) -> str:
    return f"{control_dir(project_id)}/stop.flag"


def log_file(project_id: str = None) -> str:
    return f"{control_dir(project_id)}/stdout.log"


def progress_status_file(project_id: str = None) -> str:
    """director 实时写入当前步骤——前端轮询这个文件。"""
    return f"{control_dir(project_id)}/progress_status.json"


def meta_file(project_id: str = None) -> str:
    return f"{project_dir(project_id)}/meta.json"


def ensure_project_dirs(project_id: str = None):
    """给项目建齐目录结构（不存在就创建）。"""
    pid = project_id or current()
    for d in (
        project_dir(pid), checkpoint_dir(pid), history_dir(pid),
        approval_dir(pid), plans_dir(pid), control_dir(pid),
    ):
        os.makedirs(d, exist_ok=True)


def set_project(project_id: str):
    """
    切换当前项目——重新绑定 checkpoint/config/version_control/human_in_loop 的路径常量。
    必须在 agent 调用任何 state I/O 之前调用。

    注意：不会创建目录。仅 create() / start() 等显式"写入"场景才建目录，
    避免每次 API 请求都把不存在的 project id 物化成空目录。
    """
    _current_project_var.set(project_id)
    _apply_paths()


def _apply_paths():
    """把当前项目的路径推送给各个使用路径常量的模块。"""
    global _paths_initialized
    try:
        from persistence import checkpoint
        checkpoint.CHECKPOINT_DIR = checkpoint_dir()
        checkpoint.STATE_FILE = state_file()
        checkpoint.PROGRESS_FILE = progress_file()
    except ImportError:
        pass
    try:
        import config
        config.OUTPUT_DIR = project_dir()
        config.PLANS_DIR = plans_dir()
    except ImportError:
        pass
    try:
        from persistence import version_control
        version_control.HISTORY_DIR = history_dir()
    except ImportError:
        pass
    try:
        from project_mgmt import human_in_loop
        human_in_loop.APPROVAL_DIR = approval_dir()
    except ImportError:
        pass
    _paths_initialized = True


# ═══════════════════════════════════════════════════════
#  控制信号——director 轮询这些
# ═══════════════════════════════════════════════════════

def check_control(project_id: str = None) -> str:
    """
    director 调用：返回 'stop' | 'pause' | 'ok'。
    stop 优先于 pause。
    """
    pid = project_id or current()
    if os.path.exists(stop_flag(pid)):
        return "stop"
    if os.path.exists(pause_flag(pid)):
        return "pause"
    return "ok"


def wait_while_paused(project_id: str = None, poll_interval: float = 1.0) -> str:
    """
    阻塞等待直到 pause 标志被移除，或出现 stop 标志。
    返回 'ok' 或 'stop'。
    """
    import time
    pid = project_id or current()
    while os.path.exists(pause_flag(pid)):
        if os.path.exists(stop_flag(pid)):
            return "stop"
        time.sleep(poll_interval)
    return "ok"


# 初始化：模块导入时根据环境变量绑一次路径
# （子进程启动前设置 XIAOSHUO_PROJECT_ID，checkpoint 等模块会取到正确路径）
# 不在此创建目录——目录只在真正写入时按需创建（create()/start() 等显式场景）。
# 否则仅仅 import 本模块就会把默认 "main" 项目目录物化出来。
if not _paths_initialized:
    _paths_initialized = True
