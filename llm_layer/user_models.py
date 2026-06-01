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
  "main"        — **主写作模型**（writer 写章正文 / 扩写 / 改写 · 长文本 + 文笔要求高）
  "planner"     — **规划模型**（chapter_planner / line_planner / ability_planner /
                  twist_designer / character_designer 等所有"先想后写"的结构化规划 agent）
                  需求：严格 JSON + 推理能力 + 中等上下文。可以用比 main 更便宜的模型，
                  也可以反过来用比 main 更强的（让规划更智能、写作放轻量便宜）。没绑
                  fallback main。
  "reviewer"    — 章节合规审核（setup_reviewer 用）
  "fallback"    — 备用：主调用 120s 超时或失败时自动改用此模型重试
  "in_story_ai" — **可作为 in-story 真 AI**（如主角金手指 AI 助手）被
                  state.power_system.special_abilities[].external_llm_profile 绑定。
                  勾选此 usage 的 profile 会出现在 SpecialAbility 编辑界面的下拉框里。
                  agents/external_ai_query.py 会附加 in-story system prompt 让 AI
                  扮演 in-story 角色（不暴露真实模型身份 / 不用现代品牌话术）。
  "extractor"   — **结构化提取/生成专用模型**。从大段自然语言抽锚点
                  （朝代/角色/asset 声明等）写回 state 结构化字段的任务，
                  跟"长文本创作"模型需求完全不同——
                    · 短输入 → 短 JSON 输出
                    · 严格 schema 遵循 + 高精度
                    · 不需要文笔
                  适合绑轻量便宜模型（如 claude-haiku / gemini-flash）。
                  用 agent: world_canon_extractor / intent_asset_extractor /
                  chapter_asset_tracker / 等结构化抽取 agent。
                  没绑 → 自动 fallback 到 main（向后兼容，但成本高）。
  其他自定义    — 预留给未来扩展（按字符串字面匹配）

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
import time
from datetime import datetime
from typing import Optional

# 固定在仓库根（无论从哪里 import）
_THIS_FILE = os.path.abspath(__file__)
_REPO_ROOT = os.path.dirname(os.path.dirname(_THIS_FILE))
STORAGE_PATH = os.path.join(_REPO_ROOT, "user_models.json")

# 历史 bug 防御——曾经路径解析只 dirname 一次，写到 llm_layer/user_models.json，
# 用户后续看到两份文件混乱。模块 import 时检测旧路径若有残留文件，写醒目 warn。
_GHOST_PATH = os.path.join(os.path.dirname(_THIS_FILE), "user_models.json")
if os.path.exists(_GHOST_PATH) and _GHOST_PATH != STORAGE_PATH:
    print("=" * 70)
    print(f"⚠ user_models.py: 检测到幽灵副本 {_GHOST_PATH}")
    print(f"  当前实际使用的是 {STORAGE_PATH}")
    print(f"  旧文件可能含用户老配置——请手工 merge 后删除，避免混淆")
    print("=" * 70)

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
        last_err = None
        for attempt in range(8):
            try:
                os.replace(tmp, STORAGE_PATH)
                return
            except PermissionError as e:
                # Windows 上浏览器/杀毒/另一个 Python 进程偶发短暂占用目标文件，
                # os.replace 会报 WinError 5。给它一点时间再替换。
                last_err = e
                time.sleep(0.05 * (attempt + 1))
        try:
            # 兜底：如果 rename 一直被拒，但普通写入允许，就直接覆盖目标文件。
            # 这比让 Web 保存 500 更可恢复；tmp 保留/删除都不影响下一次保存。
            with open(STORAGE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        except OSError:
            if last_err:
                raise last_err
            raise


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
    # UNIQUE_USAGES 自动互斥（同 update）
    my_unique = set(usage_list) & UNIQUE_USAGES
    if my_unique:
        for other in data["models"]:
            other_usage = other.get("usage") or []
            stripped = [u for u in other_usage if u not in my_unique]
            if stripped != other_usage:
                print(f"  [i] unique usage 互斥：从 {other.get('id')} 移除 "
                      f"{sorted(my_unique & set(other_usage))}（让位给 {mid}）")
                other["usage"] = stripped
                other["updated_at"] = now
    data["models"].append(entry)
    _save_raw(data)
    return entry


def update(model_id: str, patch: dict) -> dict:
    """
    更新某条。patch 里可以只含部分字段。不能改 id。
    如果 api_key 字段缺失或为空字符串，保留原 key（允许用户编辑时不必重填 key）。
    usage 字段如有传入，归一化为 list；省略则保留原值。

    **UNIQUE_USAGES (main/reviewer/extractor) 自动互斥**：
    本 model 勾上某 unique usage 后，自动从其他 model 移除同 usage——
    防止 first-hit 静默冲突。
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
                # ── UNIQUE_USAGES 自动互斥：把本 model 上的 unique usage 从其他 model 清掉 ──
                my_unique = set(m["usage"]) & UNIQUE_USAGES
                if my_unique:
                    for other in data["models"]:
                        if other.get("id") == model_id:
                            continue
                        other_usage = other.get("usage") or []
                        stripped = [u for u in other_usage if u not in my_unique]
                        if stripped != other_usage:
                            print(f"  [i] unique usage 互斥：从 {other.get('id')} 移除 "
                                  f"{sorted(my_unique & set(other_usage))}（让位给 {model_id}）")
                            other["usage"] = stripped
                            other["updated_at"] = datetime.now().isoformat(timespec="seconds")
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

    ⚠ first-hit 匹配——多个 profile 勾同一 usage 时，按文件物理顺序选最先出现的。
    用 active_usage_map() 看实际生效；用 detect_usage_conflicts() 检测多占问题。
    """
    for m in _load_raw().get("models", []):
        if usage in m.get("usage", []):
            return dict(m)
    return None


# 这些 usage 是"应该唯一"的——多 profile 同时占用会导致 first-hit 静默冲突
# （用户不知道实际生效哪个）。fallback / in_story_ai / custom 允许多占（场景合理）
UNIQUE_USAGES: set[str] = {"main", "planner", "reviewer", "extractor"}


def active_usage_map(include_key: bool = False) -> dict[str, Optional[dict]]:
    """返回每个内置 usage 当前实际生效的 profile（first-hit）。

    前端用此显示"main 当前生效 = X / reviewer 当前生效 = Y"，避免静默冲突。
    """
    return {
        usage: find_by_usage(usage) if not include_key
               else find_by_usage(usage)  # 内部已含 key
        for usage in USAGE_BUILTIN
    }


def detect_usage_conflicts() -> list[dict]:
    """扫描 UNIQUE_USAGES 是否被多个 profile 同时勾选。

    返回 [{"usage": ..., "active_profile_id": ..., "shadowed_profile_ids": [...]}]
    供 web 启动 / 编辑后调用，写 progress_warning 提示用户"已勾的不生效，
    实际生效是另一条"。
    """
    out = []
    for usage in UNIQUE_USAGES:
        matches = [m for m in _load_raw().get("models", [])
                    if usage in m.get("usage", [])]
        if len(matches) > 1:
            out.append({
                "usage": usage,
                "active_profile_id": matches[0]["id"],   # first-hit 实际生效的
                "shadowed_profile_ids": [m["id"] for m in matches[1:]],  # 被覆盖的
                "total": len(matches),
            })
    return out


# ═══════════════════════════════════════════════════════
#  Usage 词汇表（前端用于渲染复选框 / tooltip）
# ═══════════════════════════════════════════════════════
USAGE_BUILTIN: dict[str, str] = {
    "main":        "主写作模型（writer 写章正文 · 长文本 + 文笔）",
    "planner":     "规划模型（chapter_planner / line_planner / 等所有'先想后写'的"
                   "结构化规划 agent · JSON + 推理 + 中等上下文）",
    "reviewer":    "章节合规审核（setup_reviewer 用）",
    "fallback":    "备用：主调用失败时改用此模型重试",
    "in_story_ai": "可作 in-story 真 AI ——主角金手指 AI 助手可绑此 profile",
    "extractor":   "结构化提取/生成专用 —— world_canon / asset 声明 / intent 解析等"
                   "短输入短输出 JSON 任务；适合轻量便宜模型（不需要文笔）",
}


def all_usages() -> list[str]:
    """返回所有出现过的 usage 标签（含内置可选项）。"""
    seen = set(USAGE_BUILTIN.keys())
    for m in _load_raw().get("models", []):
        for u in m.get("usage", []):
            if u:
                seen.add(u)
    return sorted(seen)


def usage_descriptions() -> dict[str, str]:
    """返回所有 usage 标签的可读说明——前端 tooltip 用。

    内置 usage 用 USAGE_BUILTIN 文案；用户自定义 usage 用 'auto:<usage>' 占位。
    """
    out = dict(USAGE_BUILTIN)
    for u in all_usages():
        out.setdefault(u, f"自定义 usage：{u}")
    return out


def list_in_story_ai_profiles(include_key: bool = False) -> list[dict]:
    """返回所有勾选了 'in_story_ai' usage 的 profile。

    专给 SpecialAbility 编辑界面的 external_llm_profile 下拉框过滤用——
    避免用户把不该作 in-story AI 的 profile（如主写作 / 审核员）误选上去。
    返回结构同 list_all（默认遮挡 api_key）。
    """
    return [
        m for m in list_all(include_key=include_key)
        if "in_story_ai" in (m.get("usage") or [])
    ]


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


def test_model_config(model: dict, timeout: float = 25.0) -> dict:
    """Send one tiny OpenAI-compatible chat request before accepting a web model.

    The result intentionally never includes api_key. The web UI uses this as a
    hard gate so a structurally valid but unreachable/wrong model is not saved.
    """
    _validate(model, require_key=True)
    try:
        from openai import OpenAI
    except Exception as e:
        raise ValueError(f"模型连通验证失败：OpenAI SDK 不可用：{type(e).__name__}: {e}") from e

    base_url = str(model.get("base_url") or "").strip()
    api_key = str(model.get("api_key") or "").strip()
    model_name = str(model.get("model") or "").strip()
    extra_body = model.get("extra_body") or {}

    started = time.monotonic()
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "ping"},
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    try:
        response = client.chat.completions.create(**kwargs)
        content = (response.choices[0].message.content or "").strip()
    except Exception as e:
        raise ValueError(f"模型连通验证失败：{type(e).__name__}: {e}") from e

    latency_ms = int((time.monotonic() - started) * 1000)
    if not content:
        raise ValueError("模型连通验证失败：接口返回为空")
    return {"ok": True, "latency_ms": latency_ms, "preview": content[:80]}


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
