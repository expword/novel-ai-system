"""
项目管理器 —— 多本小说的 CRUD + 进程控制。

状态：
  idle     — 没有在写
  running  — 有子进程在写
  paused   — 子进程在，但 pause.flag 存在（director 在等待）
"""
from __future__ import annotations
import os
import sys
import json
import time
import signal
import shutil
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from project_mgmt import project_context as pctx


PROJECTS_ROOT = "projects"


# ═══════════════════════════════════════════════════════
#  CRUD
# ═══════════════════════════════════════════════════════

def list_projects() -> list[dict]:
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    result = []
    for name in sorted(os.listdir(PROJECTS_ROOT)):
        p = os.path.join(PROJECTS_ROOT, name)
        if not os.path.isdir(p):
            continue
        meta = _read_meta(name)
        meta["id"] = name
        meta["status"] = status(name)
        meta["progress"] = _progress_summary(name)
        result.append(meta)
    return result


def create(project_id: str, title: str, genre: str = "玄幻",
           theme: str = "", intent_description: str = "",
           num_volumes: int = 6,
           reality_basis: str = "", historical_setting: str = "",
           real_persons: list[str] | None = None) -> dict:
    """新建项目——建目录结构 + 写 meta.json + 初始化空 state.json。

    reality_basis / historical_setting / real_persons 由前端"⓪ 故事根基"问答
    传入，会写到初始 state.creative_intent 上，作为下游 IntentAnalyzer 的预填约束。
    """
    project_id = _sanitize_id(project_id)
    root = f"{PROJECTS_ROOT}/{project_id}"
    if os.path.exists(root):
        raise RuntimeError(f"项目 {project_id} 已存在")

    pctx.ensure_project_dirs(project_id)

    meta = {
        "id": project_id,
        "title": title,
        "genre": genre,
        "theme": theme,
        "intent_description": intent_description,
        "num_volumes": num_volumes,
        "reality_basis": reality_basis or "",
        "historical_setting": historical_setting or "",
        "real_persons": list(real_persons or []),
        "created_at": datetime.now().isoformat(),
    }
    with open(pctx.meta_file(project_id), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 初始化 state.json——一个带有标题/题材/主题/意图的空 NovelState
    _init_state(project_id, meta)
    return meta


def _init_state(project_id: str, meta: dict):
    """把 meta 写成初始 state.json，让 director 第一次跑时能读到标题/题材/主题/意图。"""
    # 临时切到目标项目，然后用 checkpoint 写初始 state
    original = pctx.current()
    pctx.set_project(project_id)
    try:
        from persistence.state import NovelState, CreativeIntent
        from persistence.checkpoint import save_state
        state = NovelState(
            title=meta.get("title", ""),
            genre=meta.get("genre", ""),
            theme=meta.get("theme", ""),
        )
        rb = (meta.get("reality_basis") or "").strip()
        hs = (meta.get("historical_setting") or "").strip()
        rp = list(meta.get("real_persons") or [])
        if meta.get("intent_description") or rb:
            state.creative_intent = CreativeIntent(
                raw_description=meta.get("intent_description", "") or "",
                analyzed=False,
                reality_basis=rb,
                historical_setting=hs,
                real_persons=rp,
                # respect_real_figures：真实模式 + 有人物名单 → 自动开启
                respect_real_figures=(rb in {"real_history", "real_adapted"} and len(rp) > 0),
            )
        save_state(state)
    finally:
        if original != project_id:
            pctx.set_project(original)


def delete(project_id: str, force: bool = False):
    """删除整个项目目录（先确保没在跑）。"""
    if status(project_id) != "idle":
        if force:
            stop(project_id)
        else:
            raise RuntimeError(f"项目 {project_id} 正在运行——先停止再删除（或用 force=True）")
    root = f"{PROJECTS_ROOT}/{project_id}"
    if os.path.exists(root):
        shutil.rmtree(root)


def rename_title(project_id: str, new_title: str):
    """修改项目显示标题（不改 id）。"""
    meta = _read_meta(project_id)
    meta["title"] = new_title
    with open(pctx.meta_file(project_id), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def get_meta(project_id: str) -> dict:
    m = _read_meta(project_id)
    m["id"] = project_id
    m["status"] = status(project_id)
    m["progress"] = _progress_summary(project_id)
    return m


def _read_meta(project_id: str) -> dict:
    p = pctx.meta_file(project_id)
    if not os.path.exists(p):
        return {"title": project_id, "genre": "", "theme": "", "mode": "auto"}
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"title": project_id, "mode": "auto"}
    # 向老项目兜底默认 mode=auto
    if not m.get("mode"):
        m["mode"] = "auto"
    return m


def _write_meta(project_id: str, meta: dict) -> None:
    p = pctx.meta_file(project_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get_mode(project_id: str) -> str:
    """返回 'auto' 或 'stepwise'。"""
    return _read_meta(project_id).get("mode", "auto")


def set_mode(project_id: str, mode: str) -> None:
    if mode not in ("auto", "stepwise"):
        raise ValueError(f"非法 mode：{mode}")
    m = _read_meta(project_id)
    m["mode"] = mode
    _write_meta(project_id, m)


def _progress_summary(project_id: str) -> dict:
    """读 progress.json 算当前进度。"""
    pf = pctx.progress_file(project_id)
    if not os.path.exists(pf):
        return {"phases_done": 0, "chapters_done": 0}
    try:
        with open(pf, encoding="utf-8") as f:
            p = json.load(f)
        return {
            "phases_done": len(p.get("phases", [])),
            "chapters_done": len(p.get("chapters", [])),
            "latest_phase": (p.get("phases", []) or ["—"])[-1],
            "latest_chapter": max(p.get("chapters", []) or [0]),
        }
    except (OSError, json.JSONDecodeError):
        return {"phases_done": 0, "chapters_done": 0}


# ═══════════════════════════════════════════════════════
#  状态与进程控制
# ═══════════════════════════════════════════════════════

def status(project_id: str) -> str:
    pf = pctx.pid_file(project_id)
    if not os.path.exists(pf):
        return "idle"
    if not _is_director_alive(project_id):
        # PID 死了 / PID 被复用给了别的进程 / 旧格式 PID 文件无锚点——一律视作 stale
        try:
            os.remove(pf)
        except OSError:
            pass
        return "idle"
    return "paused" if os.path.exists(pctx.pause_flag(project_id)) else "running"


def _proc_create_time(pid: int) -> Optional[float]:
    """返回 PID 进程的创建时间（unix epoch 秒）。进程不存在/不可访问返回 None。"""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED = 0x1000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return None
        try:
            ct = wintypes.FILETIME()
            xt = wintypes.FILETIME()
            kt = wintypes.FILETIME()
            ut = wintypes.FILETIME()
            ok = k32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(xt),
                                     ctypes.byref(kt), ctypes.byref(ut))
            if not ok:
                return None
            # FILETIME = 100-ns 间隔 since 1601-01-01
            t = (ct.dwHighDateTime << 32) | ct.dwLowDateTime
            return (t - 116444736000000000) / 10_000_000
        finally:
            k32.CloseHandle(h)
    # POSIX：/proc/<pid>/stat 第 22 字段 starttime（clock ticks since boot）
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # comm 字段可能含空格和括号——以最后一个 ')' 后再切
        rest = data[data.rindex(")") + 2:].split()
        starttime_ticks = int(rest[19])  # field 22，去掉前两个字段后 index=19
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return time.time() - uptime + (starttime_ticks / hz)
    except (OSError, ValueError, IndexError):
        return None


def _is_director_alive(project_id: str) -> bool:
    """PID 存活且 create_time 与启动时记录的一致，才认为是真的 director。
    没有 create_time 锚点（旧格式）→ 一律视作 stale（PID 复用 bug 的自愈点）。"""
    rec = _read_pid_record(project_id)
    if not rec:
        return False
    pid, expected_ct = rec
    actual_ct = _proc_create_time(pid)
    if actual_ct is None:
        return False
    if expected_ct is None:
        return False
    # FILETIME 精度 100ns，给点容差
    return abs(actual_ct - expected_ct) < 1.0


def _is_pid_alive(pid: int) -> bool:
    """仅检 PID 存活——不验进程身份。除内部 fallback 外勿用，识别 director 请走 _is_director_alive。"""
    return _proc_create_time(pid) is not None


def start(project_id: str) -> int:
    """启动/恢复项目写作。返回 PID。已在跑则只清 pause 标志。"""
    cur_status = status(project_id)
    if cur_status == "running":
        return _read_pid(project_id) or -1
    if cur_status == "paused":
        # 清掉 pause 标志恢复
        _remove_flag(pctx.pause_flag(project_id))
        return _read_pid(project_id) or -1

    # idle → 启动子进程
    _clear_flags(project_id)
    pctx.ensure_project_dirs(project_id)

    env = os.environ.copy()
    env["XIAOSHUO_PROJECT_ID"] = project_id
    env["PYTHONIOENCODING"] = "utf-8"
    if sys.platform == "win32":
        dll_dirs = [
            os.path.join(sys.prefix, "Library", "bin"),
            os.path.join(sys.prefix, "DLLs"),
        ]
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([p for p in dll_dirs if os.path.isdir(p)] + [existing_path])

    log_path = pctx.log_file(project_id)
    # append 模式，保留历史日志
    log_fh = open(log_path, "a", encoding="utf-8")
    log_fh.write(f"\n\n===== 启动于 {datetime.now().isoformat()} =====\n")
    log_fh.flush()

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        env=env,
        cwd=os.getcwd(),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )
    _write_pid_record(project_id, proc.pid)
    return proc.pid


def pause(project_id: str):
    """写 pause 标志——director 在下个安全点会停下来等。"""
    pctx.ensure_project_dirs(project_id)
    open(pctx.pause_flag(project_id), "w").close()


def resume(project_id: str):
    """清 pause 标志。如果进程已死，启一个新的。"""
    _remove_flag(pctx.pause_flag(project_id))
    if status(project_id) == "idle":
        start(project_id)


def stop(project_id: str, grace_seconds: float = 3.0):
    """
    优雅停止：先写 stop 标志让 director 自己退出；超时还活着就强 kill。
    """
    pctx.ensure_project_dirs(project_id)
    open(pctx.stop_flag(project_id), "w").close()
    # 也清掉 pause（不然它卡在暂停里）
    _remove_flag(pctx.pause_flag(project_id))

    start_t = time.time()
    while status(project_id) in ("running", "paused") and time.time() - start_t < grace_seconds:
        time.sleep(0.3)

    # 还活着——强 kill（但只在 PID + create_time 都还匹配时才动手，避免误杀复用 PID）
    pid = _read_pid(project_id)
    if pid and _is_director_alive(project_id):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               check=False, capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                if _is_director_alive(project_id):
                    os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass

    _clear_flags(project_id)


def read_log_tail(project_id: str, lines: int = 200) -> str:
    """读进程 stdout.log 的尾部。"""
    p = pctx.log_file(project_id)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as f:
        data = f.readlines()
    return "".join(data[-lines:])


# ═══════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════

def _read_pid_record(project_id: str) -> Optional[tuple]:
    """读 PID 文件，返回 (pid, expected_create_time)。旧整数格式 expected_ct 为 None。"""
    pf = pctx.pid_file(project_id)
    if not os.path.exists(pf):
        return None
    try:
        with open(pf) as f:
            raw = f.read().strip()
        if not raw:
            return None
        if raw.startswith("{"):
            d = json.loads(raw)
            pid = int(d.get("pid", 0))
            if pid <= 0:
                return None
            ct = d.get("create_time")
            return (pid, float(ct) if ct is not None else None)
        # 旧格式：纯整数
        return (int(raw), None)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _read_pid(project_id: str) -> Optional[int]:
    rec = _read_pid_record(project_id)
    return rec[0] if rec else None


def _write_pid_record(project_id: str, pid: int) -> None:
    pf = pctx.pid_file(project_id)
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    record = {"pid": pid, "create_time": _proc_create_time(pid)}
    # Windows may keep a stale fixed "running.pid.tmp" locked after an
    # interrupted start. Use a unique temp file so a bad leftover cannot block
    # future launches.
    tmp = f"{pf}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(record, f)
        try:
            os.replace(tmp, pf)
        except PermissionError:
            # Some Windows ACL setups allow creating files but deny replacing
            # them. PID files are tiny control records, so fall back to a
            # direct write instead of blocking project startup.
            with open(pf, "w") as f:
                json.dump(record, f)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _remove_flag(path: str):
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _clear_flags(project_id: str):
    for p in (pctx.pause_flag(project_id),
              pctx.stop_flag(project_id),
              pctx.pid_file(project_id)):
        _remove_flag(p)


def _sanitize_id(raw: str) -> str:
    """项目 id 只允许字母/数字/下划线/中横线。"""
    import re
    s = re.sub(r"[^A-Za-z0-9_一-鿿\-]", "_", raw.strip())
    return s or datetime.now().strftime("project_%Y%m%d_%H%M%S")


# ═══════════════════════════════════════════════════════
#  迁移老数据：output/ → projects/main/
# ═══════════════════════════════════════════════════════

def migrate_legacy_output_to_main():
    """
    如果 output/ 有数据且 projects/main/ 还没建，把 output/ 迁到 projects/main/。
    只在 projects/main/ 不存在时迁；幂等。
    """
    legacy = "output"
    target = f"{PROJECTS_ROOT}/main"
    if not os.path.isdir(legacy):
        return False
    if os.path.exists(target) and os.listdir(target):
        return False
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    # 把 output 整体搬到 projects/main
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(legacy, target)
    # 写 meta.json（从 state.json 里读标题/题材/主题）
    state_file = f"{target}/checkpoint/state.json"
    title, genre, theme = "main", "", ""
    if os.path.exists(state_file):
        try:
            with open(state_file, encoding="utf-8") as f:
                d = json.load(f)
            title = d.get("title", "main")
            genre = d.get("genre", "")
            theme = d.get("theme", "")
        except Exception:
            pass
    meta = {
        "id": "main", "title": title, "genre": genre, "theme": theme,
        "intent_description": "",
        "created_at": datetime.now().isoformat(),
        "note": "从 output/ 目录迁移而来",
    }
    with open(f"{target}/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # 建控制目录
    os.makedirs(f"{target}/control", exist_ok=True)
    return True
