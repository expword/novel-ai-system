"""
LLM 运行时 —— 决定当前该用哪个 profile。

优先级（从高到低）：
  1. 项目 meta.json 里的 llm_profile 字段（Web UI 能改）
  2. 环境变量 XIAOSHUO_LLM_PROFILE
  3. user_models.json 里 usage="main" 的模型（用户自定义主模型）
  4. config.py 里的 LLM_PROFILE
  5. llm_profiles.DEFAULT_PROFILE_ID

user_models（用户自定义，全局共享）和 llm_profiles（内置目录）统一通过 profile_id 访问。

每次调 LLM 都会 resolve 一次，所以 UI 改完立即生效。
"""
from __future__ import annotations
import os
import json
from typing import Optional

from llm_layer import llm_profiles


def _profile_id_exists(pid: str) -> bool:
    """检查 pid 在内置 PROFILES 或 user_models 里存在。"""
    if pid in llm_profiles.PROFILES:
        return True
    try:
        from llm_layer import user_models
        return user_models.get(pid, include_key=False) is not None
    except Exception:
        return False


def _lookup_profile(pid: str) -> Optional[dict]:
    """按 pid 查出 profile 字典——user_models 优先，再查内置 PROFILES。"""
    # 1. 用户模型优先（确保 API key 正确携带）
    try:
        from llm_layer import user_models
        um = user_models.get(pid, include_key=True)
        if um:
            return user_models.to_profile_dict(um)
    except Exception:
        pass
    # 2. 内置目录
    if pid in llm_profiles.PROFILES:
        return dict(llm_profiles.PROFILES[pid])
    return None


def resolve_profile() -> dict:
    """返回当前应该用的 profile dict。"""
    pid = resolve_profile_id()
    prof = _lookup_profile(pid)
    if prof is not None:
        return prof
    # 兜底：取内置默认
    return llm_profiles.get(llm_profiles.DEFAULT_PROFILE_ID)


def resolve_profile_id() -> str:
    """返回当前 profile id。"""
    # 1. 项目 meta
    try:
        from project_mgmt import project_context
        meta_path = project_context.meta_file()
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            pid = meta.get("llm_profile")
            if pid and _profile_id_exists(pid):
                return pid
    except Exception:
        pass
    # 2. 环境变量
    env_pid = os.environ.get("XIAOSHUO_LLM_PROFILE")
    if env_pid and _profile_id_exists(env_pid):
        return env_pid
    # 3. user_models 里 usage="main" 的第一条
    try:
        from llm_layer import user_models
        main_um = user_models.find_by_usage("main")
        if main_um:
            return main_um["id"]
    except Exception:
        pass
    # 4. 内置默认（DEFAULT_PROFILE_ID = "yunwu-deepseek-chat"）
    return llm_profiles.DEFAULT_PROFILE_ID


def resolve_api_key(profile: dict) -> str:
    """
    取 API key。优先级：
      1. profile 里携带的 _user_api_key（来自 user_models.to_profile_dict）
      2. profile 指定的 env_key 环境变量
    """
    # 1. 用户模型直接携带的 key（来自 user_models）
    user_key = profile.get("_user_api_key")
    if user_key:
        return user_key
    # 2. 环境变量（内置 profile 的标准方式）
    env_key = profile.get("env_key", "")
    if env_key:
        val = os.environ.get(env_key)
        if val:
            return val
    return ""


def set_project_profile(project_id: str, profile_id: str) -> dict:
    """
    把项目的 llm_profile 写入 meta.json。
    profile_id 可以是内置 PROFILES 的 id，也可以是 user_models 的 id。
    """
    if not _profile_id_exists(profile_id):
        raise ValueError(f"未知 profile：{profile_id}（既不在内置目录也不在用户模型里）")
    from project_mgmt import project_context
    orig = project_context.current()
    try:
        project_context.set_project(project_id)
        meta_path = project_context.meta_file()
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        meta["llm_profile"] = profile_id
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta
    finally:
        if orig != project_id:
            project_context.set_project(orig)


def get_project_profile_id(project_id: str) -> str:
    """查某项目当前用的 profile id（可能是内置或用户模型）。"""
    from project_mgmt import project_context
    meta_path = project_context.meta_file(project_id)
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            pid = meta.get("llm_profile")
            if pid and _profile_id_exists(pid):
                return pid
        except (OSError, json.JSONDecodeError):
            pass
    return resolve_profile_id()
