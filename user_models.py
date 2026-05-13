"""
用户自定义模型持久化 —— 支持 Web 界面增删改查。

存储位置：F:/xiaoshuo/user_models.json（仓库根目录，全局，不按项目隔离）
理由：
  - API key 是用户级，不是项目级（多项目共用）
  - 模型配置相对稳定，不需要频繁切换

每条记录字段：
  id           唯一标识（英文/数字/下划线，内部 key）
  display_name 显示名（用户可读）
  base_url     OpenAI 兼容的 API 端点
  api_key      原始 API key（敏感，UI 里展示时遮挡）
  model        传给 provider 的 model 名
  usage        用途标签 list[str]（一条记录可同时承担多个用途）
  notes        可选备注（展示给用户）
  created_at   ISO 时间戳
  updated_at   ISO 时间戳

用途（usage）约定（一条记录可勾选多个，组合自由）：
  "main"      — 主 LLM（writer/规划 agents 用它）
  "reviewer"  — 章节合规审核（setup_reviewer 用）
  "fallback"  — 备用：主调用 120s 超时或失败时自动改用此模型重试
  其他自定义  — 预留给未来扩展（按字符串字面匹配）

叙事内 AI（如主角带的"豆包"）不通过 usage 路由——见 agents/external_ai_query.py：
  state.power_system.special_abilities[].external_llm_profile 直接绑 user_model id。

兼容性：老数据 usage 是字符串，读取时自动归一化为 list；下次保存时写回 list 形态。

**关于与内置 llm_profiles.PROFILES 的关系**：
  两者互补：
    - llm_profiles.PROFILES 是"厂商目录"（25+ 常见模型的预设）
    - user_models.json 是"用户实际用的"（带 key 的具体配置）
  在需要按 usage 查找时，优先查 user_models；内置 PROFILES 仍可通过 id 直接选。
"""
from __future__ import annotations
import os
import json
import re
import threading
from datetime import datetime
from typing import Optional

# 固定在仓库根（无论从哪里 import）
_THIS_FILE = os.path.abspath(__file__)
_REPO_ROOT = os.path.dirname(_THIS_FILE)
STORAGE_PATH = os.path.join(_REPO_ROOT, "user_models.json")

# 内存锁——多线程写同一文件
_lock = threading.Lock()


# ═══════════════════════════════════════════════════════
#  默认种子——首次启动时写入
# ═══════════════════════════════════════════════════════

_DEFAULT_MODELS = [
    {
        "id": "main_yunwu_deepseek",
        "display_name": "主模型 · DeepSeek Chat（云雾）",
        "base_url": "https://yunwu.ai/v1",
        "api_key": "sk-JRAlCYmGiP3W8qudKiHQNonGHeuADBMPdOhBohvkOdNW2Qt7",
        "model": "deepseek-chat",
        "usage": ["main"],
        "notes": "默认主模型——写作/规划 agents 都走它",
    },
    {
        "id": "reviewer_yunwu_gemini_flash",
        "display_name": "审核 · Gemini 3.1 Flash Lite（云雾）",
        "base_url": "https://yunwu.ai/v1",
        "api_key": "sk-04xy72huTJqzdgEJ4Jfk1wxDVzAmOpAjvmijnbNPCuUcSA6r",
        "model": "gemini-3.1-flash-lite-preview",
        "usage": ["reviewer"],
        "notes": "章节合规审核专用——快速便宜",
    },
]


def _normalize_usage(value) -> list[str]:
    """
    把 usage 归一化为 list[str]：
      None/空     → []
      字符串 "x"  → ["x"]（向后兼容老数据）
      逗号串      → 拆分（前端兜底）
      list        → 去空 / 去重保序
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        # 兼容单字符串与逗号分隔
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return parts if len(parts) > 1 else ([value.strip()] if value.strip() else [])
    if isinstance(value, (list, tuple, set)):
        seen, out = set(), []
        for v in value:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out
    # 其他类型——保守转字符串
    s = str(value).strip()
    return [s] if s else []


# ═══════════════════════════════════════════════════════
#  持久化读写
# ═══════════════════════════════════════════════════════

def _load_raw() -> dict:
    """读原始 JSON。首次启动自动写入默认种子；usage 字段读时归一化为 list[str]。"""
    if not os.path.exists(STORAGE_PATH):
        _save_raw({"models": list(_DEFAULT_MODELS)})
    try:
        with open(STORAGE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "models" not in data:
            return {"models": list(_DEFAULT_MODELS)}
    except (OSError, json.JSONDecodeError):
        return {"models": list(_DEFAULT_MODELS)}
    # 老数据兼容：usage 是字符串就归一化成 list（不写回，下次 add/update 时持久化）
    for m in data.get("models", []):
        m["usage"] = _normalize_usage(m.get("usage"))
    return data


def _save_raw(data: dict) -> None:
    with _lock:
        tmp = STORAGE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STORAGE_PATH)


# ═══════════════════════════════════════════════════════
#  CRUD
# ═══════════════════════════════════════════════════════

def list_all(include_key: bool = False) -> list[dict]:
    """
    返回所有用户模型。
    include_key=False 时会遮挡 api_key（只留前 4 位 + 末 4 位，中间打 ***）——供前端列表展示用。
    include_key=True 仅在后端内部解析 key 时用。
    """
    data = _load_raw()
    result = []
    for m in data.get("models", []):
        entry = dict(m)
        if not include_key:
            k = entry.get("api_key", "")
            entry["api_key_masked"] = _mask_key(k)
            # 前端不拿到真实 key
            entry.pop("api_key", None)
        result.append(entry)
    return result


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "***"
    return f"{key[:4]}***{key[-4:]}"


def get(model_id: str, include_key: bool = True) -> Optional[dict]:
    """按 id 找一条。include_key=True 返回包含真实 key 的完整记录。"""
    for m in _load_raw().get("models", []):
        if m.get("id") == model_id:
            if include_key:
                return dict(m)
            entry = dict(m)
            entry["api_key_masked"] = _mask_key(entry.get("api_key", ""))
            entry.pop("api_key", None)
            return entry
    return None


def add(model: dict) -> dict:
    """
    新增一条。必填：display_name, base_url, api_key, model。可选：id(留空自动生成), usage, notes。
    抛 ValueError 表示校验失败。
    """
    _validate(model, require_key=True)
    now = datetime.now().isoformat(timespec="seconds")
    data = _load_raw()
    existing_ids = {m.get("id") for m in data.get("models", [])}

    # 自动生成 id（基于 display_name）
    mid = (model.get("id") or "").strip()
    if not mid:
        mid = _generate_id(model.get("display_name", ""), existing_ids)
    else:
        if mid in existing_ids:
            raise ValueError(f"id 已存在：{mid}")

    usage_list = _normalize_usage(model.get("usage"))  # 允许空 list（不参与任何路由）
    entry = {
        "id": mid,
        "display_name": model["display_name"].strip(),
        "base_url": model["base_url"].strip(),
        "api_key": model["api_key"].strip(),
        "model": model["model"].strip(),
        "usage": usage_list,
        "notes": (model.get("notes") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    data["models"].append(entry)
    _save_raw(data)
    return entry


def update(model_id: str, patch: dict) -> dict:
    """
    更新某条。patch 里可以只含部分字段。不能改 id。
    如果 api_key 字段缺失或为空字符串，保留原 key（允许用户编辑时不必重填 key）。
    usage 字段如有传入，归一化为 list；省略则保留原值。
    """
    data = _load_raw()
    for m in data["models"]:
        if m.get("id") == model_id:
            # 允许用户不改 key——跳过空字段
            for k in ("display_name", "base_url", "model", "notes"):
                if k in patch and patch[k] is not None:
                    m[k] = str(patch[k]).strip()
            if "usage" in patch and patch["usage"] is not None:
                m["usage"] = _normalize_usage(patch["usage"])
            if "api_key" in patch and patch["api_key"] and str(patch["api_key"]).strip():
                m["api_key"] = str(patch["api_key"]).strip()
            m["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _validate(m, require_key=True)
            _save_raw(data)
            return dict(m)
    raise ValueError(f"未找到 id={model_id}")


def remove(model_id: str) -> bool:
    data = _load_raw()
    before = len(data["models"])
    data["models"] = [m for m in data["models"] if m.get("id") != model_id]
    if len(data["models"]) == before:
        return False
    _save_raw(data)
    return True


# ═══════════════════════════════════════════════════════
#  按 usage 查找（运行时路由用）
# ═══════════════════════════════════════════════════════

def find_by_usage(usage: str) -> Optional[dict]:
    """
    找第一条 usage 列表里包含指定标签的模型（含 key）。
    例：一条记录 usage=["main", "reviewer"] 会同时被 find_by_usage("main")
    和 find_by_usage("reviewer") 命中。
    """
    for m in _load_raw().get("models", []):
        if usage in m.get("usage", []):
            return dict(m)
    return None


def all_usages() -> list[str]:
    """返回所有出现过的 usage 标签（含内置可选项）。"""
    builtin = {"main", "reviewer", "fallback"}
    seen = set(builtin)
    for m in _load_raw().get("models", []):
        for u in m.get("usage", []):
            if u:
                seen.add(u)
    return sorted(seen)


# ═══════════════════════════════════════════════════════
#  校验
# ═══════════════════════════════════════════════════════

def _validate(model: dict, require_key: bool = True) -> None:
    for k in ("display_name", "base_url", "model"):
        if not str(model.get(k, "")).strip():
            raise ValueError(f"必填字段缺失：{k}")
    if require_key and not str(model.get("api_key", "")).strip():
        raise ValueError("必填字段缺失：api_key")
    if not model["base_url"].startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")


def _generate_id(seed: str, existing: set) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", seed.strip()).strip("_").lower() or "model"
    base = base[:40]
    cand = base
    i = 2
    while cand in existing:
        cand = f"{base}_{i}"
        i += 1
    return cand


# ═══════════════════════════════════════════════════════
#  兼容层：把 user_models 变成 llm_profiles 风格的 dict
# ═══════════════════════════════════════════════════════

def to_profile_dict(m: dict) -> dict:
    """转成 llm_profiles.PROFILES 条目的形状——方便现有代码直接 fallback 使用。"""
    return {
        "display_name": m["display_name"],
        "provider": "用户自定义",
        "base_url": m["base_url"],
        "model": m["model"],
        "env_key": "",  # 用户模型直接带 key，不走环境变量
        "max_output": 4096,
        "context_window": 128000,
        "notes": m.get("notes", ""),
        "_user_api_key": m.get("api_key", ""),  # 特殊字段：llm.py 会识别
        "extra_body": m.get("extra_body"),  # 透传给 OpenAI SDK 的额外字段（如关闭 reasoning）
    }
