"""
分文件 state 存储 —— 解决单体 state.json 的性能 + 并发问题。

原架构痛点：
  - 大项目 state.json 可能 10-30MB，每次 mark_phase_done 都全量重写
  - 多线程并发写同一文件有竞态
  - 前端 /api/state 拉全量响应慢
  - 章节写作中频繁 save_state 导致磁盘 IO 爆

新架构：
  projects/<id>/checkpoint/state/
    meta.json                 # 轻量顶层字段
    concept_pitch.json        # 各个单体 section
    master_outline.json
    ...
    volumes.json              # 列表 section
    characters.json
    factions.json
    ...

每个 section 独立文件：
  - 只写"改了的"，不动没改的
  - 不同 section 写入互不干扰（无竞态）
  - 前端按 section 拉，响应快

保留向后兼容：老项目加载时自动检测 state.json → 迁移到 state/ 目录。
写入始终走新格式，老 state.json 保留作备份不删。
"""
from __future__ import annotations
import os
import json
import threading
from dataclasses import asdict
from typing import Any, Optional

from persistence.state import NovelState


# ═══════════════════════════════════════════════════════
#  每个 section 的"存取规则"
#
#  每个 section 写一行条目：
#    state_attr    -  state 对象上的属性名
#    kind          -  "scalar"（单一 dataclass/dict）或 "list"（列表）
#    loader        -  读盘时用，把 dict 转回 dataclass（复用 checkpoint._load_xxx）
#    saver         -  写盘时用，把 dataclass 转 dict（默认 asdict + _to_json 辅助）
# ═══════════════════════════════════════════════════════

# 定义在 _init_spec() 里延迟初始化（避免循环导入 checkpoint）
_SECTION_SPEC: Optional[dict] = None
_section_lock = threading.Lock()  # 写入锁（每个 section 内部独占）
_per_section_locks: dict[str, threading.Lock] = {}


def _get_lock_for_section(name: str) -> threading.Lock:
    """每个 section 一把锁——避免两个线程同时写 characters.json。"""
    with _section_lock:
        if name not in _per_section_locks:
            _per_section_locks[name] = threading.Lock()
        return _per_section_locks[name]


def _init_spec() -> dict:
    """懒初始化 section spec。这里延迟到运行时 import，避免 checkpoint 循环依赖。"""
    global _SECTION_SPEC
    if _SECTION_SPEC is not None:
        return _SECTION_SPEC
    from persistence import checkpoint as ck

    # 每个 section: (state_attr, kind, loader, default_factory)
    spec = {
        # ─── 单体 section（scalar）──────────────────────
        "creative_intent":    ("creative_intent",    "scalar", ck._load_creative_intent, None),
        "concept_pitch":      ("concept_pitch",      "scalar", ck._load_concept_pitch, None),
        "trope_library":      ("trope_library",      "scalar", ck._load_trope_library, None),
        "tone_manual":        ("tone_manual",        "scalar", ck._load_tone_manual, None),
        "master_outline":     ("master_outline",     "scalar", ck._load_master_outline, None),
        "world_canon":        ("world_canon",        "scalar", ck._load_world_canon, None),
        "character_ability_profiles": (
            "character_ability_profiles", "scalar",
            lambda d: {n: ck._load_character_ability_profile(p) for n, p in (d or {}).items() if isinstance(p, dict)},
            dict,
        ),
        "power_events":       ("power_events",       "list",   ck._load_power_event, None),
        "book_structure":     ("book_structure",     "scalar", ck._load_book_structure, None),
        "geography":          ("geography",          "scalar", ck._load_geography, None),
        "timeline":           ("timeline",           "scalar", ck._load_timeline, None),
        "economy":            ("economy",            "scalar", ck._load_economy, None),
        "relationship_web":   ("relationship_web",   "scalar", ck._load_relationship_web, None),
        "protagonist_journey":("protagonist_journey","scalar", ck._load_protagonist_journey, None),
        "story_thread":       ("story_thread",       "scalar", ck._load_story_thread, None),
        "conflict_ladder":    ("conflict_ladder",    "scalar", ck._load_conflict_ladder, None),
        "emotion_curve":      ("emotion_curve",      "scalar", ck._load_emotion_curve, None),
        "twist_system":       ("twist_system",       "scalar", ck._load_twist_system, None),
        "power_system":       ("power_system",       "scalar", _load_power_system_maybe_none, None),
        "memory":             ("memory",             "scalar", _load_memory, None),
        # ─── 列表 section（list）──────────────────────
        "volumes":            ("volumes",            "list", ck._load_volume, None),
        "characters":         ("characters",         "list", ck._load_character, None),
        "factions":           ("factions",           "list", ck._load_faction, None),
        "character_arcs":     ("character_arcs",     "list", ck._load_character_arc, None),
        "satisfaction_points":("satisfaction_points","list", ck._load_sp, None),
        "foreshadow_items":   ("foreshadow_items",   "list", ck._load_fw, None),
        "fortunes":           ("fortunes",           "list", ck._load_fortune, None),
        "story_stages":       ("story_stages",       "list", ck._load_story_stage, None),
        "red_herrings":       ("red_herrings",       "list", ck._load_red_herring, None),
        "rhythm_plans":       ("rhythm_plans",       "list", ck._load_rhythm_plan, None),
        "global_lines":       ("global_lines",       "list", ck._load_narrative_line, None),
        "volume_lines":       ("volume_lines",       "list", ck._load_narrative_line, None),
        "chapter_type_plans": ("chapter_type_plans", "list", ck._load_volume_ctp, None),
        "completed_chapters": ("completed_chapters", "list", ck._load_chapter_summary, None),
        "world_events":       ("world_events",       "list", ck._load_world_event, None),
        "glossary":           ("glossary",           "list", ck._load_glossary_entry, None),
        "version_snapshots":  ("version_snapshots",  "list", ck._load_version_snapshot, None),
        "pending_approvals":  ("pending_approvals",  "list", ck._load_pending_approval, None),
        # 氛围库——分片单独保存
        "atmosphere_library": ("atmosphere_library", "scalar", _load_atmosphere_library, None),
    }
    _SECTION_SPEC = spec
    return spec


# ─── 辅助：特殊 section 的 loader ───────────────────

def _load_power_system_maybe_none(d: dict):
    from persistence import checkpoint as ck
    if not d:
        return None
    return ck._load_power_system(d)


def _load_memory(d: dict):
    """Memory 是 {entries, facts, character_states} 三段."""
    from persistence.state import MemoryBank
    from persistence import checkpoint as ck
    return MemoryBank(
        entries=[ck._load_memory_entry(e) for e in d.get("entries", [])],
        facts=d.get("facts", []),
        character_states=d.get("character_states", {}),
    )


def _load_atmosphere_library(d: dict):
    """AtmosphereLibrary { scopes: [{scope_type, scope_key, label, fragments, customs}] }"""
    from persistence.state import AtmosphereLibrary, AtmosphereScope, AtmosphereFragment, CulturalCustom
    if not d:
        return AtmosphereLibrary()
    import dataclasses as _dc
    _F = {f.name for f in _dc.fields(AtmosphereFragment)}
    _C = {f.name for f in _dc.fields(CulturalCustom)}
    scopes = []
    for s in (d.get("scopes") or []):
        if not isinstance(s, dict):
            continue
        frags = []
        for fr in (s.get("fragments") or []):
            if isinstance(fr, dict):
                try:
                    frags.append(AtmosphereFragment(**{kk: vv for kk, vv in fr.items() if kk in _F}))
                except Exception:
                    pass
        customs = []
        for cu in (s.get("customs") or []):
            if isinstance(cu, dict):
                try:
                    customs.append(CulturalCustom(**{kk: vv for kk, vv in cu.items() if kk in _C}))
                except Exception:
                    pass
        scopes.append(AtmosphereScope(
            scope_type=s.get("scope_type", ""),
            scope_key=str(s.get("scope_key", "")),
            label=s.get("label", ""),
            fragments=frags,
            customs=customs,
        ))
    return AtmosphereLibrary(scopes=scopes)


# ═══════════════════════════════════════════════════════
#  路径
# ═══════════════════════════════════════════════════════

def state_dir() -> str:
    from project_mgmt import project_context as pctx
    return os.path.join(pctx.checkpoint_dir(), "state")


def section_file(name: str) -> str:
    return os.path.join(state_dir(), f"{name}.json")


def meta_file() -> str:
    return os.path.join(state_dir(), "meta.json")


def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.replace(tmp, path)
    except PermissionError:
        # Windows 上目标文件偶发被短暂占用时，os.replace 会失败；直接覆盖可保证本次保存落盘。
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.remove(tmp)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════
#  单 section 读写
# ═══════════════════════════════════════════════════════

def save_section(name: str, state: NovelState, *, preserve_power_assets_if_empty: bool = False) -> None:
    """只写一个 section 文件。线程安全。"""
    spec = _init_spec()
    if name not in spec:
        raise KeyError(f"未知 section：{name}")

    state_attr, kind, _, _ = spec[name]
    value = getattr(state, state_attr, None)

    # 转成可序列化的 dict / list——支持嵌套 dataclass
    def _to_serializable(v):
        if v is None:
            return None
        if hasattr(v, "__dataclass_fields__"):
            return asdict(v)
        if isinstance(v, dict):
            return {k: _to_serializable(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_to_serializable(x) for x in v]
        return v

    data: Any
    if kind == "scalar":
        if value is None:
            data = {}
        else:
            data = _to_serializable(value)
            data = _deep_unroll_enums(data)
    else:  # list
        data = [_to_serializable(v) for v in (value or [])]
        data = _deep_unroll_enums(data)

    if (
        preserve_power_assets_if_empty
        and name == "power_system"
        and isinstance(data, dict)
        and not getattr(state, "_explicit_power_system_put", False)
    ):
        incoming = data.get("special_abilities")
        if not incoming:
            path = section_file(name)
            try:
                if os.path.exists(path):
                    with open(path, encoding="utf-8-sig") as f:
                        old = json.load(f) or {}
                    old_abs = old.get("special_abilities") or []
                    if old_abs:
                        data["special_abilities"] = old_abs
                        print("  [state_storage] 保留磁盘中已有 special_abilities，避免后台旧 state 清空")
            except Exception as e:
                print(f"  [state_storage] special_abilities 保留检查失败：{type(e).__name__}: {e}")

    path = section_file(name)
    with _get_lock_for_section(name):
        _write_json_file(path, data)


def _deep_unroll_enums(obj):
    """把任意层级里的 Enum 替换成 .value。和 checkpoint._to_json 行为一致。"""
    import enum
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _deep_unroll_enums(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_unroll_enums(v) for v in obj]
    if isinstance(obj, tuple):
        return [_deep_unroll_enums(v) for v in obj]
    return obj


def load_section(name: str, state: NovelState) -> bool:
    """读一个 section 文件并写回 state。文件不存在返回 False。"""
    spec = _init_spec()
    if name not in spec:
        return False
    path = section_file(name)
    if not os.path.exists(path):
        return False

    state_attr, kind, loader, _ = spec[name]
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [!] 读 section {name} 失败：{e}")
        return False

    if kind == "scalar":
        try:
            setattr(state, state_attr, loader(raw))
        except Exception as e:
            print(f"  [!] 反序列化 {name} 失败：{e}")
            return False
    else:
        try:
            setattr(state, state_attr, [loader(x) for x in (raw or [])])
        except Exception as e:
            print(f"  [!] 反序列化 list {name} 失败：{e}")
            return False
    return True


# ═══════════════════════════════════════════════════════
#  meta（顶层字段）
# ═══════════════════════════════════════════════════════

META_FIELDS = (
    "title", "genre", "theme",
    "world_setting", "world_factions_desc", "overall_arc",
    "current_volume_index", "current_chapter_index",
    "tension_history",
    "character_state_history",
    "chapter_inspirations",
    "chapter_chats",
    "ability_audits",
    "reader_audits",
    "dialogue_audits",
    "tension_debt",
    "novelty_budget",
    "last_reconcile_report",
    "promises",
    "asset_usage",
    "last_cohesion_report",
    "romance_arcs",
    # Stage / Volume 级审查
    "stage_review_reports",
    "volume_review_reports",
    "done_stage_ids",
    "done_volume_review_indices",
)


def save_meta(state: NovelState) -> None:
    """写顶层简单字段到 meta.json。"""
    meta = {}
    for k in META_FIELDS:
        v = getattr(state, k, None)
        if k == "tension_history":
            # list[Enum] → list[str]
            meta[k] = [t.value if hasattr(t, "value") else t for t in (v or [])]
        elif k == "character_state_history":
            # dict[str, list[CharacterStateSnapshot]]
            meta[k] = {
                name: [asdict(s) for s in (snaps or [])]
                for name, snaps in (v or {}).items()
            }
        elif k == "chapter_inspirations":
            # dict[int, str] —— JSON 只支持字符串 key，write 时转 str
            meta[k] = {str(ci): txt for ci, txt in (v or {}).items()}
        elif k == "chapter_chats":
            # dict[int, list[ChatMessage]]
            meta[k] = {
                str(ci): [asdict(m) for m in (msgs or [])]
                for ci, msgs in (v or {}).items()
            }
        elif k == "ability_audits":
            # dict[int, AbilityAudit]（AbilityAudit 内嵌 list[AbilityUse/Issue]）
            meta[k] = {
                str(ci): asdict(audit)
                for ci, audit in (v or {}).items()
            }
        elif k == "reader_audits":
            meta[k] = {
                str(ci): asdict(audit)
                for ci, audit in (v or {}).items()
            }
        elif k == "dialogue_audits":
            meta[k] = {
                str(ci): asdict(audit)
                for ci, audit in (v or {}).items()
            }
        elif k == "stage_review_reports":
            # dict[str, list[ReviewIssue]]
            meta[k] = {
                str(sid): [asdict(it) for it in (issues or [])]
                for sid, issues in (v or {}).items()
            }
        elif k == "volume_review_reports":
            # dict[int, list[ReviewIssue]] —— int key 转 str
            meta[k] = {
                str(vi): [asdict(it) for it in (issues or [])]
                for vi, issues in (v or {}).items()
            }
        else:
            meta[k] = v

    path = meta_file()
    with _get_lock_for_section("_meta"):
        _write_json_file(path, meta)


def load_meta(state: NovelState) -> bool:
    """从 meta.json 读顶层字段写回 state。"""
    from persistence import checkpoint as ck
    path = meta_file()
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8-sig") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    from persistence.state import TensionLevel
    for k in META_FIELDS:
        if k not in meta:
            continue
        v = meta[k]
        if k == "tension_history":
            setattr(state, k, [ck._enum(TensionLevel, x) for x in v])
        elif k == "character_state_history":
            setattr(state, k, {
                name: [ck._load_state_snapshot(s) for s in (snaps or [])]
                for name, snaps in (v or {}).items()
            })
        elif k == "chapter_inspirations":
            # 读回时 key 转回 int
            setattr(state, k, {int(ci): txt for ci, txt in (v or {}).items()})
        elif k == "chapter_chats":
            # 用字段过滤防老快照带多余字段崩
            from persistence.state import ChatMessage
            import dataclasses as _dc
            _CM = {f.name for f in _dc.fields(ChatMessage)}
            parsed_chats = {}
            for ci, msgs in (v or {}).items():
                try: ci_i = int(ci)
                except (TypeError, ValueError): continue
                lst = []
                for m in (msgs or []):
                    if not isinstance(m, dict): continue
                    try:
                        lst.append(ChatMessage(**{kk: vv for kk, vv in m.items() if kk in _CM}))
                    except Exception:
                        continue
                parsed_chats[ci_i] = lst
            setattr(state, k, parsed_chats)
        elif k == "reader_audits":
            from persistence.state import ReaderExperienceAudit, ReaderExperienceIssue
            import dataclasses as _dc
            _RI = {f.name for f in _dc.fields(ReaderExperienceIssue)}
            _RA = {f.name for f in _dc.fields(ReaderExperienceAudit)}
            parsed = {}
            for ci, audit_d in (v or {}).items():
                if not isinstance(audit_d, dict): continue
                try: ci_i = int(ci)
                except (TypeError, ValueError): continue
                issues = []
                for i in (audit_d.get("issues") or []):
                    if isinstance(i, dict):
                        try:
                            issues.append(ReaderExperienceIssue(**{kk: vv for kk, vv in i.items() if kk in _RI}))
                        except Exception: pass
                try:
                    kw = {kk: vv for kk, vv in audit_d.items()
                          if kk in _RA and kk != "issues"}
                    kw["chapter_index"] = kw.get("chapter_index", ci_i)
                    kw["issues"] = issues
                    parsed[ci_i] = ReaderExperienceAudit(**kw)
                except Exception as e:
                    print(f"  [load_meta] reader_audits[{ci_i}] 解析失败：{type(e).__name__}: {e}")
            setattr(state, k, parsed)
        elif k == "dialogue_audits":
            from persistence.state import DialogueAudit, DialogueIssue
            import dataclasses as _dc
            _DI = {f.name for f in _dc.fields(DialogueIssue)}
            _DA = {f.name for f in _dc.fields(DialogueAudit)}
            parsed = {}
            for ci, audit_d in (v or {}).items():
                if not isinstance(audit_d, dict): continue
                try: ci_i = int(ci)
                except (TypeError, ValueError): continue
                issues = []
                for i in (audit_d.get("issues") or []):
                    if isinstance(i, dict):
                        try:
                            issues.append(DialogueIssue(**{kk: vv for kk, vv in i.items() if kk in _DI}))
                        except Exception: pass
                try:
                    kw = {kk: vv for kk, vv in audit_d.items() if kk in _DA and kk != "issues"}
                    kw["chapter_index"] = kw.get("chapter_index", ci_i)
                    kw["issues"] = issues
                    parsed[ci_i] = DialogueAudit(**kw)
                except Exception as e:
                    print(f"  [load_meta] dialogue_audits[{ci_i}] 解析失败：{type(e).__name__}: {e}")
            setattr(state, k, parsed)
        elif k == "stage_review_reports":
            from persistence.state import ReviewIssue
            import dataclasses as _dc
            _RI = {f.name for f in _dc.fields(ReviewIssue)}
            parsed = {}
            for sid, issues in (v or {}).items():
                lst = []
                for i in (issues or []):
                    if isinstance(i, dict):
                        try:
                            lst.append(ReviewIssue(**{kk: vv for kk, vv in i.items() if kk in _RI}))
                        except Exception: pass
                parsed[str(sid)] = lst
            setattr(state, k, parsed)
        elif k == "volume_review_reports":
            from persistence.state import ReviewIssue
            import dataclasses as _dc
            _RI = {f.name for f in _dc.fields(ReviewIssue)}
            parsed = {}
            for vi, issues in (v or {}).items():
                try:
                    vi_i = int(vi)
                except (TypeError, ValueError):
                    continue
                lst = []
                for i in (issues or []):
                    if isinstance(i, dict):
                        try:
                            lst.append(ReviewIssue(**{kk: vv for kk, vv in i.items() if kk in _RI}))
                        except Exception: pass
                parsed[vi_i] = lst
            setattr(state, k, parsed)
        elif k == "done_volume_review_indices":
            # list[int]——JSON 里可能是 int 或 str
            parsed = []
            for x in (v or []):
                try: parsed.append(int(x))
                except (TypeError, ValueError): continue
            setattr(state, k, parsed)
        elif k == "promises":
            from persistence.state import Promise
            import dataclasses as _dc
            _P = {f.name for f in _dc.fields(Promise)}
            parsed = []
            for p in (v or []):
                if isinstance(p, dict):
                    try:
                        parsed.append(Promise(**{kk: vv for kk, vv in p.items() if kk in _P}))
                    except Exception:
                        pass
            setattr(state, k, parsed)
        elif k == "asset_usage":
            from persistence.state import AssetUsage
            import dataclasses as _dc
            _A = {f.name for f in _dc.fields(AssetUsage)}
            parsed = {}
            for kk, val in (v or {}).items():
                if isinstance(val, dict):
                    try:
                        parsed[kk] = AssetUsage(**{kkk: vvv for kkk, vvv in val.items() if kkk in _A})
                    except Exception:
                        pass
            setattr(state, k, parsed)
        elif k == "romance_arcs":
            from persistence.state import RomanceArc, RomanceEvent
            import dataclasses as _dc
            _A = {f.name for f in _dc.fields(RomanceArc)}
            _E = {f.name for f in _dc.fields(RomanceEvent)}
            parsed = []
            for r in (v or []):
                if not isinstance(r, dict):
                    continue
                evs = []
                for e in (r.get("actual_events") or []):
                    if isinstance(e, dict):
                        try:
                            evs.append(RomanceEvent(**{kkk: vvv for kkk, vvv in e.items() if kkk in _E}))
                        except Exception:
                            pass
                try:
                    kw = {kkk: vvv for kkk, vvv in r.items() if kkk in _A and kkk != "actual_events"}
                    kw["actual_events"] = evs
                    parsed.append(RomanceArc(**kw))
                except Exception:
                    pass
            setattr(state, k, parsed)
        elif k == "ability_audits":
            from persistence.state import AbilityAudit, AbilityUse, AbilityIssue
            import dataclasses as _dc
            _AU = {f.name for f in _dc.fields(AbilityUse)}
            _AI = {f.name for f in _dc.fields(AbilityIssue)}
            _AA = {f.name for f in _dc.fields(AbilityAudit)}
            parsed = {}
            for ci, audit_d in (v or {}).items():
                if not isinstance(audit_d, dict): continue
                try: ci_i = int(ci)
                except (TypeError, ValueError): continue
                uses = []
                for u in (audit_d.get("ability_uses") or []):
                    if isinstance(u, dict):
                        try:
                            uses.append(AbilityUse(**{kk: vv for kk, vv in u.items() if kk in _AU}))
                        except Exception: pass
                issues = []
                for i in (audit_d.get("issues") or []):
                    if isinstance(i, dict):
                        try:
                            issues.append(AbilityIssue(**{kk: vv for kk, vv in i.items() if kk in _AI}))
                        except Exception: pass
                try:
                    aa_kw = {kk: vv for kk, vv in audit_d.items()
                             if kk in _AA and kk not in ("ability_uses", "issues")}
                    aa_kw["chapter_index"] = aa_kw.get("chapter_index", ci_i)
                    aa_kw["ability_uses"] = uses
                    aa_kw["issues"] = issues
                    parsed[ci_i] = AbilityAudit(**aa_kw)
                except Exception as e:
                    print(f"  [load_meta] ability_audits[{ci_i}] 解析失败：{type(e).__name__}: {e}")
            setattr(state, k, parsed)
        else:
            setattr(state, k, v)
    return True


# ═══════════════════════════════════════════════════════
#  全量读写
# ═══════════════════════════════════════════════════════

def save_split(state: NovelState) -> None:
    """把整个 state 拆成多个 section 文件写入。"""
    save_meta(state)
    for name in _init_spec():
        try:
            save_section(name, state, preserve_power_assets_if_empty=True)
        except Exception as e:
            print(f"  [!] 保存 section {name} 失败：{e}")


def load_split(state: NovelState) -> bool:
    """
    从 state/ 目录各文件恢复 state。返回是否成功（有文件）。
    部分 section 加载失败不会整体失败，但会汇总打印警告——
    避免静默的"某字段用默认值"导致后续行为诡异。
    """
    if not os.path.isdir(state_dir()):
        return False
    any_loaded = load_meta(state)
    failed_sections: list[str] = []
    missing_sections: list[str] = []
    for name in _init_spec():
        path = section_file(name)
        if not os.path.exists(path):
            missing_sections.append(name)
            continue
        if load_section(name, state):
            any_loaded = True
        else:
            failed_sections.append(name)
    if failed_sections:
        print(f"  ⚠ [load_split] {len(failed_sections)} 个 section 读失败（用默认值，可能行为异常）：{failed_sections}")
    # missing 不一定是问题——新项目许多 section 本来就没生成；只有 >50% 缺失才 warn
    if missing_sections and len(missing_sections) > len(_init_spec()) // 2:
        print(f"  ℹ [load_split] {len(missing_sections)} 个 section 缺文件（可能是新项目，正常）")
    return any_loaded


# ═══════════════════════════════════════════════════════
#  老项目迁移：state.json → state/ 目录
# ═══════════════════════════════════════════════════════

def migrate_from_single(legacy_state_json: str) -> bool:
    """
    从老的单体 state.json 迁移到分文件结构。
    幂等：state/ 已存在且有 meta.json 就跳过。
    """
    if not os.path.exists(legacy_state_json):
        return False
    if os.path.exists(meta_file()):
        return False  # 已迁移过

    print(f"  [migrate] 检测到老 state.json，正在迁移到分文件结构...")
    from persistence import checkpoint as ck
    with open(legacy_state_json, encoding="utf-8") as f:
        raw = json.load(f)
    # 用旧 loader 恢复完整 state
    from persistence.state import NovelState
    state = ck._load_state(raw)
    # 拆分写入
    save_split(state)
    print(f"  [migrate] 迁移完成——老 state.json 保留作备份不删")
    return True
