"""
Flask 后端 —— 前端和 state.json 之间的桥梁。

API 一览：
  GET  /api/state                         — 返回精简版完整状态（用于侧边栏导航）
  GET  /api/section/<name>                — 读某个模块
  PUT  /api/section/<name>                — 整体写某个模块（传 JSON 即覆盖）
  PUT  /api/section/<name>/<id>           — 写某条（list 里的单个 entry）
  POST /api/regen/<action>                — 无参重建
  POST /api/regen/<action>/<arg>          — 带参重建
  GET  /api/versions                      — 快照列表
  POST /api/rollback/<timestamp>          — 回退到某快照
  GET  /api/approvals                     — HITL 待审核列表
  POST /api/approvals/<id>/approve        — 批准某个审核
  GET  /api/chapter/<index>               — 读某章正文
  GET  /api/chapter_summaries             — 已完成章节摘要（图表用）
  GET  /api/invariants                    — 一致性检查报告
"""
from __future__ import annotations
import os
import sys
import json
import dataclasses
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort, Response, stream_with_context

# 把项目根加入 sys.path，让 Flask 能 import agents/*
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistence.checkpoint import load_state, save_state, STATE_FILE, _to_json, _load_state
from persistence.state import NovelState
from persistence import version_control
from utils import invariants
from project_mgmt import project_context
from project_mgmt import project_manager
from web import regenerate as regen_mod
from utils import ops_tracker
# 注意：不从 config import OUTPUT_DIR——它在模块加载时冻结，切换项目后会指向错误路径。
# 所有路径请用 project_context.project_dir() 按请求动态获取。


# 启动时把 legacy output/ 迁到 projects/main/
project_manager.migrate_legacy_output_to_main()

# 启动时应用用户自定义的 prompt 覆盖（prompts/overrides.json → setattr 到各 agent 模块）
try:
    from utils import prompts_registry
    prompts_registry.apply_all_overrides()
except Exception as e:
    print(f"[startup] 应用 prompt 覆盖失败（不影响启动）：{type(e).__name__}: {e}")

# 启动时确保前端依赖库已下载到本地（第一次启动会拉 ~600KB，之后瞬开）
try:
    from web.vendor_loader import ensure_vendor_libs
    _vendor_result = ensure_vendor_libs(verbose=True)
    if not all(_vendor_result.values()):
        print("⚠ 部分前端依赖库下载失败——index.html 会自动 fallback 到 CDN")
except Exception as _e:
    print(f"⚠ vendor_loader 异常：{_e}——跳过，使用 CDN")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


# ═══════════════════════════════════════════════════════
#  首页
# ═══════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


# project_id 白名单：中文字符、字母数字、下划线、连字符，1-64 字
# 拒绝任何含 / \ .. : 等可能逃出目录的字符
import re as _re
_PROJECT_ID_RE = _re.compile(r"^[\w一-鿿\- ]{1,64}$", _re.UNICODE)


def _validate_project_id(pid: str) -> bool:
    if not pid:
        return False
    # 显式拒绝路径元字符 + 跨平台考虑
    if any(ch in pid for ch in ("/", "\\", "..", ":", "\x00", "\n", "\r")):
        return False
    return bool(_PROJECT_ID_RE.match(pid))


# 每个 API 请求前——根据 ?project=<id> 或 URL path 切换当前项目上下文
# 带路径注入保护：不合法 pid 直接 400，不进 project_context
@app.before_request
def _set_project_from_query():
    if not request.path.startswith("/api/"):
        return
    # 优先从 URL path 里的 project_id 取（view args），其次 query/header
    view_args = request.view_args or {}
    pid = view_args.get("project_id") or request.args.get("project") or request.headers.get("X-Project-Id")
    if not pid:
        return
    if not _validate_project_id(pid):
        return jsonify({"error": f"非法 project_id：{pid!r}（只允许字母/数字/中文/下划线/连字符/空格，1-64 字）"}), 400
    project_context.set_project(pid)


# ═══════════════════════════════════════════════════════
#  项目管理
# ═══════════════════════════════════════════════════════

@app.route("/api/projects", methods=["GET"])
def api_projects_list():
    return jsonify(project_manager.list_projects())


@app.route("/api/projects", methods=["POST"])
def api_projects_create():
    body = request.get_json() or {}
    pid = body.get("id") or body.get("title", "").strip().replace(" ", "_")
    if not pid:
        abort(400, description="必须提供 id 或 title")
    title = body.get("title", pid)
    intent_desc = body.get("intent_description", "").strip()
    # ── 故事根基（真实 vs 虚构）──
    reality_basis = (body.get("reality_basis") or "").strip()
    if reality_basis not in {"real_history", "real_adapted", "fictional", ""}:
        reality_basis = ""
    historical_setting = (body.get("historical_setting") or "").strip()
    real_persons_raw = body.get("real_persons") or []
    if not isinstance(real_persons_raw, list):
        real_persons_raw = []
    real_persons = [str(p).strip() for p in real_persons_raw if str(p).strip()]

    try:
        meta = project_manager.create(
            project_id=pid,
            title=title,
            genre=body.get("genre", "玄幻"),
            theme=body.get("theme", ""),
            intent_description=intent_desc,
            num_volumes=int(body.get("num_volumes", 6)),
            reality_basis=reality_basis,
            historical_setting=historical_setting,
            real_persons=real_persons,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    # 运行模式：默认 stepwise（用户审核每个阶段组），可选 auto（一气跑到底）
    mode = body.get("mode") or "stepwise"
    if mode in ("auto", "stepwise"):
        try:
            project_manager.set_mode(pid, mode)
            meta["mode"] = mode
        except Exception:
            pass

    analyze_now = bool(body.get("analyze_now", False)) and bool(intent_desc)
    start_after = bool(body.get("start_after", False))
    analysis_result = None
    start_result = None

    # ── start_after=True：不在后端预热，直接启动子进程
    if start_after:
        try:
            subprocess_pid = project_manager.start(pid)
            start_result = f"✓ 已启动完整流水线（PID={subprocess_pid}）——Phase -1/0/1/2... 在后台按顺序跑"
            if analyze_now:
                analysis_result = "→ 意图分析 + 立项已纳入子进程流水线（不重复跑）"
        except Exception as e:
            start_result = f"⚠ 启动失败：{e}"

    elif analyze_now:
        # 只想要立项不启动写作——后端同步跑，写 progress_status.json 让前端看见
        with ops_tracker.operation_scope(pid, "创建新小说·同步分析", "初始化") as got_lock:
            if not got_lock:
                return jsonify({"error": ops_tracker.active_op_error_message(pid)}), 409
            try:
                project_context.set_project(pid)
                from agents.intent_analyzer import analyze_intent
                from agents.concept_pitch import design_concept_phase
                from persistence.checkpoint import load_state, save_state as _save_state, mark_phase_done

                ops_tracker.set_progress(pid, agent="IntentAnalyzer", detail="分析作者意图（LLM 调用中）")
                s = load_state()
                if s:
                    s.creative_intent.analyzed = False
                    analyze_intent(s, intent_desc)
                    mark_phase_done("-1", s)

                    ops_tracker.set_progress(pid, agent="ConceptPitch", detail="生成立项三件套 pitch + tropes + tone")
                    design_concept_phase(s)
                    mark_phase_done("0", s)
                    _save_state(s)
                    analysis_result = "✓ 意图已分析 + 立项已生成（Phase -1/0 完成）"
            except Exception as e:
                analysis_result = f"⚠ 分析失败：{e}"

    meta["analysis_result"] = analysis_result
    meta["start_result"] = start_result
    return jsonify(meta)


@app.route("/api/projects/<project_id>", methods=["GET"])
def api_projects_meta(project_id):
    return jsonify(project_manager.get_meta(project_id))


@app.route("/api/projects/<project_id>", methods=["DELETE"])
def api_projects_delete(project_id):
    force = request.args.get("force", "false").lower() == "true"
    try:
        project_manager.delete(project_id, force=force)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok"})


@app.route("/api/projects/<project_id>/start", methods=["POST"])
def api_project_start(project_id):
    try:
        pid = project_manager.start(project_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "ok", "pid": pid, "project_status": project_manager.status(project_id)})


@app.route("/api/projects/<project_id>/pause", methods=["POST"])
def api_project_pause(project_id):
    project_manager.pause(project_id)
    return jsonify({"status": "ok", "project_status": project_manager.status(project_id)})


@app.route("/api/projects/<project_id>/resume", methods=["POST"])
def api_project_resume(project_id):
    project_manager.resume(project_id)
    return jsonify({"status": "ok", "project_status": project_manager.status(project_id)})


@app.route("/api/projects/<project_id>/stop", methods=["POST"])
def api_project_stop(project_id):
    project_manager.stop(project_id)
    return jsonify({"status": "ok", "project_status": project_manager.status(project_id)})


@app.route("/api/projects/<project_id>/mode", methods=["GET"])
def api_project_mode_get(project_id):
    return jsonify({"mode": project_manager.get_mode(project_id)})


@app.route("/api/projects/<project_id>/mode", methods=["POST"])
def api_project_mode_set(project_id):
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "").strip()
    try:
        project_manager.set_mode(project_id, mode)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"mode": project_manager.get_mode(project_id)})


@app.route("/api/projects/<project_id>/stepwise/mark_reviewed", methods=["POST"])
def api_stepwise_mark_reviewed(project_id):
    """给当前 state 存一个"reviewed"快照，标记作者已审核完该阶段组。
    body: {"group_id": "G1_intent"}
    """
    project_context.set_project(project_id)
    body = request.get_json(silent=True) or {}
    group_id = (body.get("group_id") or "").strip()
    if not group_id:
        return jsonify({"error": "缺 group_id"}), 400
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    try:
        ts = version_control.snapshot(
            s,
            label=f"stepwise_{group_id}_reviewed",
            phase=group_id,
            notes=f"作者审核/修改后确认——阶段组 {group_id}",
        )
        save_state(s)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"status": "ok", "group_id": group_id, "snapshot_ts": ts})


@app.route("/api/projects/<project_id>/stepwise/rollback", methods=["POST"])
def api_stepwise_rollback(project_id):
    """回滚到某 group 完成时的快照（丢弃审核期间的所有编辑）。
    body: {"group_id": "G1_intent"}
    """
    project_context.set_project(project_id)
    body = request.get_json(silent=True) or {}
    group_id = (body.get("group_id") or "").strip()
    if not group_id:
        return jsonify({"error": "缺 group_id"}), 400

    # 找到 stepwise_{group_id}_done 对应的快照时间戳
    label_key = f"stepwise_{group_id}_done"
    snaps = version_control.list_snapshots()
    # 同 label 多份时取最新（最靠前，list_snapshots 已按 timestamp DESC）
    target = None
    for sn in snaps:
        # sn 结构：{timestamp, label, ...}
        if label_key in (sn.get("label") or sn.get("file", "")):
            target = sn
            break
    if not target:
        # 友好诊断：列出已有快照标签，帮用户判断为什么找不到
        available_labels = sorted({
            sn.get("label", "")[:60] for sn in snaps if sn.get("label")
        })
        diag = (
            f"未找到回滚快照（label={label_key}）。"
            "可能原因：① 该阶段是 auto 模式跑完的，没写 stepwise 回滚点；"
            "② 项目从老版本升级，无快照；③ 快照被清理。"
        )
        if available_labels:
            preview = " / ".join(available_labels[:6])
            diag += f" 当前可用快照标签（共 {len(available_labels)} 个）：{preview}"
            if len(available_labels) > 6:
                diag += f" …等 {len(available_labels) - 6} 个"
        else:
            diag += " 当前项目还没有任何快照。"
        return jsonify({"error": diag}), 404

    ts = target["timestamp"]
    try:
        restored = version_control.rollback(ts, label_hint=label_key)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    if restored is None:
        return jsonify({"error": "回滚失败"}), 500
    # rollback 只改了 state.json 老格式——分片也要跟着更新
    try:
        save_state(restored)
    except Exception as e:
        print(f"[rollback] 分片保存失败（已回到 legacy state.json）：{e}")
    return jsonify({
        "status": "ok", "group_id": group_id, "restored_from": ts,
        "snapshot_label": target.get("label", ""),
    })


# 阶段组的定义——对应 director._stepwise_checkpoint 的 5 个点
_PHASE_GROUPS = [
    {"id": "G1_intent",          "name": "立项",     "phases": ["-1", "0", "0.5", "0.6"]},
    {"id": "G2_world",           "name": "世界",     "phases": ["1A", "1A2", "1B", "1C", "1D", "1E", "1F", "1G", "1H"]},
    {"id": "G3_characters",      "name": "人物",     "phases": ["2", "2A2", "2B", "2C"]},
    {"id": "G4_plot",            "name": "情节",     "phases": ["3A", "3B", "3B2", "3C", "3D", "3D2", "3E", "3E2", "3E3", "3F", "3G"]},
    {"id": "G5_framework_ready", "name": "框架就绪", "phases": []},  # 虚拟组：前 4 组全完成即满足
]


@app.route("/api/projects/<project_id>/next_phase_group")
def api_project_next_phase_group(project_id):
    """
    返回当前已完成到哪一组、下一组是什么、各组进度。
    供前端在 stepwise 模式下显示"▶ 继续下一阶段"或"📝 框架就绪"横幅。
    """
    from project_mgmt import project_manager
    import json as _j

    pf = project_context.progress_file(project_id)
    try:
        with open(pf, encoding="utf-8") as f:
            done_phases = set((_j.load(f).get("phases") or []))
    except (OSError, _j.JSONDecodeError, FileNotFoundError):
        done_phases = set()

    groups_info = []
    all_prev_done = True
    current_group_id = None
    next_group_id = None

    for g in _PHASE_GROUPS:
        required = g["phases"]
        # G5 (framework_ready) 没有自己的 phase——前面都完成即视为达成
        if not required:
            is_done = all_prev_done
        else:
            is_done = all(p in done_phases for p in required)
        done_count = sum(1 for p in required if p in done_phases)
        groups_info.append({
            "id": g["id"],
            "name": g["name"],
            "phases_total": len(required),
            "phases_done": done_count,
            "done": is_done,
        })
        if is_done:
            current_group_id = g["id"]
        else:
            if next_group_id is None:
                next_group_id = g["id"]
            all_prev_done = False

    framework_ready = all(gi["done"] for gi in groups_info)
    return jsonify({
        "project_id": project_id,
        "mode": project_manager.get_mode(project_id),
        "groups": groups_info,
        "current_group_id": current_group_id,     # 最近一个已完成的组
        "next_group_id": next_group_id,            # 下一个要做的组；全做完=None
        "framework_ready": framework_ready,
    })


@app.route("/api/projects/<project_id>/status")
def api_project_status(project_id):
    return jsonify({
        "id": project_id,
        "status": project_manager.status(project_id),
        "progress": project_manager._progress_summary(project_id),
        "current_step": _read_current_step(project_id),
    })


def _read_current_step(project_id: str) -> dict:
    """读 director 实时写入的 progress_status.json。

    这个文件可能被多个进程（director + web 同步任务）同时写——
    并发写会把一次写的尾巴留在另一次写的内容之后，导致字节级损坏。
    读端能宽容地吞掉（下一次写会覆盖掉坏字节）。
    """
    from project_mgmt import project_context
    path = project_context.progress_status_file(project_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {}


@app.route("/api/llm_pool/stats")
def api_llm_pool_stats():
    """LLM 池实时状态——并发数/速率/熔断/最近延迟。"""
    try:
        from llm_layer import llm_pool
        return jsonify(llm_pool.get_default_pool().stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/projects/<project_id>/progress")
def api_project_progress(project_id):
    """只返回实时步骤（比 status 轻量，适合高频轮询）。"""
    return jsonify(_read_current_step(project_id))


@app.route("/api/projects/<project_id>/warnings/clear", methods=["POST"])
def api_clear_warnings(project_id):
    """清空 progress_status.json 的 warnings 数组（用户在前端点"清除"调用）。"""
    from persistence.checkpoint import clear_progress_warnings
    clear_progress_warnings(project_id=project_id)
    return jsonify({"ok": True})


@app.route("/api/projects/<project_id>/log")
def api_project_log(project_id):
    lines = int(request.args.get("lines", 200))
    return jsonify({"log": project_manager.read_log_tail(project_id, lines)})


# ═══════════════════════════════════════════════════════
#  辅助
# ═══════════════════════════════════════════════════════

def _load():
    s = load_state()
    if s is None:
        # 没有 state.json——返回一个空 NovelState（用 meta 填基础信息）
        meta = project_manager._read_meta(project_context.current())
        s = NovelState(
            title=meta.get("title", project_context.current()),
            genre=meta.get("genre", ""),
            theme=meta.get("theme", ""),
        )
        # 不保存——只是为了让 GET 能返回空壳
    return s


def _save(state, label="web_edit"):
    version_control.snapshot(state, label=label, notes="manual edit via web")
    save_state(state)


# SECTION 映射：名字 → (getter, setter)
# getter(state) 返回可 JSON 化的 dict；setter(state, data) 把 data 写回 state
_MODULE_FLOW_NODES = [
    {"id": "creative_intent", "label": "创作意图", "phase": "1 起点", "section": "creative_intent", "regen": None, "desc": "作者输入的题材、主题、受众和禁区，是后续所有模块的根。"},
    {"id": "concept_pitch", "label": "市场定位", "phase": "2 定位", "section": "concept_pitch", "regen": None, "desc": "把意图收束成卖点、读者承诺和基础商业方向。"},
    {"id": "trope_library", "label": "套路库", "phase": "2 定位", "section": "trope_library", "regen": "trope_library", "desc": "从定位拆出可反复兑现的类型套路和期待管理。"},
    {"id": "tone_manual", "label": "文风手册", "phase": "2 定位", "section": "tone_manual", "regen": "tone_manual", "desc": "约束叙述口吻、节奏、句式和文本气质。"},
    {"id": "book_structure", "label": "全书结构", "phase": "3 蓝图", "section": "book_structure", "regen": "master_outline", "desc": "全书级起承转合和核心命题。"},
    {"id": "protagonist_journey", "label": "主角历程", "phase": "3 蓝图", "section": "protagonist_journey", "regen": None, "desc": "主角心理、目标和能力成长的长期轨迹。"},
    {"id": "world", "label": "世界观", "phase": "4 世界", "section": "world", "regen": "world", "desc": "世界规则、基础背景和全局叙事土壤。"},
    {"id": "power_system", "label": "力量体系", "phase": "4 世界", "section": "power_system", "regen": "power_system", "desc": "能力规则、限制、使用时机和成长机制。"},
    {"id": "geography", "label": "地理", "phase": "4 世界", "section": "geography", "regen": "geography", "desc": "地图、地区层级和活动空间。"},
    {"id": "timeline", "label": "时间线", "phase": "4 世界", "section": "timeline", "regen": "timeline", "desc": "历史锚点、时代节点和关键时间背景。"},
    {"id": "economy", "label": "经济", "phase": "4 世界", "section": "economy", "regen": "economy", "desc": "资源、财富、产业和主角经济曲线。"},
    {"id": "factions", "label": "势力格局", "phase": "4 世界", "section": "factions", "regen": "factions", "desc": "组织、阵营、利益关系和外部压力。"},
    {"id": "characters", "label": "人物档案", "phase": "5 人物", "section": "characters", "regen": "characters", "desc": "主角、配角、反派和人物基础设定。"},
    {"id": "relationship_web", "label": "关系网", "phase": "5 人物", "section": "relationship_web", "regen": "relationships", "desc": "人物之间的表层关系、真实关系和张力。"},
    {"id": "character_arcs", "label": "心理弧光", "phase": "5 人物", "section": "character_arcs", "regen": None, "desc": "人物长期变化、转折和情感弧线。"},
    {"id": "volumes", "label": "卷结构/章节大纲", "phase": "6 情节", "section": "volumes", "regen": "volumes", "arg_regen": "volume_outline", "arg_label": "卷", "desc": "卷级目标和每章 outline，是章节生成的主骨架。"},
    {"id": "lines", "label": "叙事线", "phase": "6 情节", "section": "lines", "regen": "lines", "desc": "主线、支线和卷内线索的推进轨道。"},
    {"id": "conflict_ladder", "label": "冲突阶梯", "phase": "6 情节", "section": "conflict_ladder", "regen": "conflict_ladder", "desc": "冲突如何逐级升级。"},
    {"id": "emotion_curve", "label": "情绪曲线", "phase": "6 情节", "section": "emotion_curve", "regen": "emotion_curve", "desc": "读者情绪的高低起伏和释放点。"},
    {"id": "rhythm_plans", "label": "节奏规划", "phase": "6 情节", "section": "rhythm_plans", "regen": None, "desc": "章节密度、快慢和叙事呼吸。"},
    {"id": "satisfaction_points", "label": "爽点系统", "phase": "6 情节", "section": "satisfaction_points", "regen": "satisfaction", "desc": "铺垫、兑现和读者满足感节点。"},
    {"id": "foreshadow_items", "label": "伏笔体系", "phase": "6 情节", "section": "foreshadow_items", "regen": "foreshadows", "desc": "伏笔的埋设、延迟和回收。"},
    {"id": "twist_system", "label": "反转系统", "phase": "6 情节", "section": "twist_system", "regen": "twists", "desc": "误导、揭示和层级反转。"},
    {"id": "story_stages", "label": "叙事舞台", "phase": "7 章节", "section": "story_stages", "regen": "stages", "arg_regen": "stages", "arg_label": "卷", "desc": "卷内大段落舞台和每个阶段的叙事任务。"},
    {"id": "chapter_type_plans", "label": "章节类型", "phase": "7 章节", "section": "chapter_type_plans", "regen": None, "arg_regen": "chapter_types", "arg_label": "卷", "desc": "每章承担的功能类型和节奏职责。"},
    {"id": "completed_chapters", "label": "已完成章节", "phase": "8 正文", "section": "completed_chapters", "regen": None, "desc": "已经写出的正文与章节摘要。"},
]

_MODULE_FLOW_EDGES = [
    ("creative_intent", "concept_pitch"),
    ("concept_pitch", "trope_library"),
    ("concept_pitch", "tone_manual"),
    ("concept_pitch", "book_structure"),
    ("concept_pitch", "world"),
    ("book_structure", "protagonist_journey"),
    ("book_structure", "volumes"),
    ("world", "power_system"),
    ("world", "geography"),
    ("world", "timeline"),
    ("world", "economy"),
    ("world", "factions"),
    ("power_system", "characters"),
    ("power_system", "protagonist_journey"),
    ("geography", "characters"),
    ("timeline", "characters"),
    ("economy", "characters"),
    ("factions", "characters"),
    ("characters", "relationship_web"),
    ("characters", "character_arcs"),
    ("relationship_web", "character_arcs"),
    ("relationship_web", "lines"),
    ("character_arcs", "lines"),
    ("protagonist_journey", "lines"),
    ("volumes", "lines"),
    ("volumes", "story_stages"),
    ("lines", "conflict_ladder"),
    ("lines", "emotion_curve"),
    ("lines", "rhythm_plans"),
    ("lines", "satisfaction_points"),
    ("lines", "foreshadow_items"),
    ("lines", "twist_system"),
    ("conflict_ladder", "story_stages"),
    ("emotion_curve", "story_stages"),
    ("rhythm_plans", "story_stages"),
    ("satisfaction_points", "story_stages"),
    ("foreshadow_items", "story_stages"),
    ("twist_system", "story_stages"),
    ("story_stages", "chapter_type_plans"),
    ("story_stages", "volumes"),
    ("chapter_type_plans", "completed_chapters"),
    ("volumes", "completed_chapters"),
    ("tone_manual", "completed_chapters"),
    ("power_system", "completed_chapters"),
    ("relationship_web", "completed_chapters"),
    ("lines", "completed_chapters"),
]


def _module_flow_payload(_state=None):
    upstream: dict[str, list[str]] = {n["id"]: [] for n in _MODULE_FLOW_NODES}
    downstream: dict[str, list[str]] = {n["id"]: [] for n in _MODULE_FLOW_NODES}
    for src, dst in _MODULE_FLOW_EDGES:
        downstream.setdefault(src, []).append(dst)
        upstream.setdefault(dst, []).append(src)

    nodes = []
    for node in _MODULE_FLOW_NODES:
        item = dict(node)
        regen = item.get("regen")
        arg_regen = item.get("arg_regen")
        item["upstream"] = upstream.get(item["id"], [])
        item["downstream"] = downstream.get(item["id"], [])
        item["can_rebuild"] = bool(
            (regen and regen in regen_mod.REGEN_ACTIONS)
            or (arg_regen and arg_regen in regen_mod.REGEN_ACTIONS_WITH_ARG)
        )
        item["rebuild_mode"] = "arg" if arg_regen and arg_regen in regen_mod.REGEN_ACTIONS_WITH_ARG else ("direct" if regen and regen in regen_mod.REGEN_ACTIONS else "")
        item["rebuild_action"] = arg_regen if item["rebuild_mode"] == "arg" else (regen or "")
        nodes.append(item)

    return {
        "nodes": nodes,
        "edges": [{"from": src, "to": dst} for src, dst in _MODULE_FLOW_EDGES],
        "default_arg": 1,
    }


def _section_map():
    return {
        "module_flow": (
            lambda s: _module_flow_payload(s),
            None,
        ),
        # Phase -1
        "creative_intent": (
            lambda s: _dump_creative_intent_for_api(s.creative_intent),
            lambda s, d: _replace_creative_intent(s.creative_intent, d),
        ),
        # Phase 0
        "concept_pitch": (
            lambda s: s.concept_pitch.__dict__,
            lambda s, d: _replace_dataclass(s.concept_pitch, d),
        ),
        "trope_library": (
            lambda s: s.trope_library.__dict__,
            lambda s, d: _replace_dataclass(s.trope_library, d),
        ),
        "tone_manual": (
            lambda s: s.tone_manual.__dict__,
            lambda s, d: _replace_dataclass(s.tone_manual, d),
        ),
        # Phase 1
        "world": (
            lambda s: {
                "world_setting": s.world_setting,
                "world_factions_desc": s.world_factions_desc,
                "overall_arc": s.overall_arc,
            },
            lambda s, d: (
                setattr(s, "world_setting", d.get("world_setting", s.world_setting)),
                setattr(s, "world_factions_desc", d.get("world_factions_desc", s.world_factions_desc)),
                setattr(s, "overall_arc", d.get("overall_arc", s.overall_arc)),
            ),
        ),
        "geography": (
            lambda s: regen_mod._dump_geo(s),
            None,  # 只读；要改请用 regen
        ),
        "timeline": (
            lambda s: {
                "current_era": s.timeline.current_era,
                "current_year_desc": s.timeline.current_year_desc,
                "events": [e.__dict__ for e in s.timeline.events_sorted()],
            },
            None,
        ),
        "economy": (
            lambda s: regen_mod._dump_economy(s),
            None,
        ),
        "power_system": (
            lambda s: _dump_power_system(s),
            lambda s, d: _replace_power_system(s, d),
        ),
        "factions": (
            lambda s: _dump_factions(s),
            None,
        ),
        # Phase 2
        "characters": (
            lambda s: [regen_mod._char_to_dict(c) for c in s.characters],
            lambda s, d: _replace_characters(s, d),
        ),
        "relationship_web": (
            lambda s: regen_mod._dump_relationship_web(s),
            None,
        ),
        "character_arcs": (
            lambda s: [_arc_to_dict(a) for a in s.character_arcs],
            None,
        ),
        # Phase 1-B
        "volumes": (
            lambda s: [v.__dict__ for v in s.volumes],
            lambda s, d: _replace_volumes(s, d),
        ),
        "book_structure": (
            lambda s: s.book_structure.__dict__,
            lambda s, d: _replace_dataclass(s.book_structure, d),
        ),
        # Phase 3
        "lines": (
            lambda s: {
                "global_lines": [_line_to_dict(l) for l in s.global_lines],
                "volume_lines": [_line_to_dict(l) for l in s.volume_lines],
            },
            None,
        ),
        "satisfaction_points": (
            lambda s: [_sp_to_dict(sp) for sp in s.satisfaction_points],
            None,
        ),
        "foreshadow_items": (
            lambda s: [_fw_to_dict(fw) for fw in s.foreshadow_items],
            None,
        ),
        "red_herrings": (
            lambda s: [r.__dict__ for r in s.red_herrings],
            None,
        ),
        "twist_system": (
            lambda s: _twist_system_to_dict(s.twist_system),
            None,
        ),
        "fortunes": (
            lambda s: [f.__dict__ for f in s.fortunes],
            None,
        ),
        "conflict_ladder": (
            lambda s: {"entries": [e.__dict__ for e in s.conflict_ladder.entries]},
            None,
        ),
        "emotion_curve": (
            lambda s: {"notes": [n.__dict__ for n in s.emotion_curve.notes]},
            None,
        ),
        "rhythm_plans": (
            lambda s: [_rhythm_to_dict(p) for p in s.rhythm_plans],
            None,
        ),
        "story_stages": (
            lambda s: [_stage_to_dict(st) for st in s.story_stages],
            None,
        ),
        "chapter_type_plans": (
            lambda s: [_ctp_to_dict(p) for p in s.chapter_type_plans],
            None,
        ),
        "protagonist_journey": (
            lambda s: _journey_to_dict(s.protagonist_journey),
            None,
        ),
        # 元系统
        "glossary": (
            lambda s: [g.__dict__ for g in s.glossary],
            lambda s, d: _replace_glossary(s, d),
        ),
        "world_events": (
            lambda s: [w.__dict__ for w in s.world_events],
            None,
        ),
        "character_state_history": (
            lambda s: {
                name: [snap.__dict__ for snap in snaps]
                for name, snaps in s.character_state_history.items()
            },
            None,
        ),
        "completed_chapters": (
            lambda s: _completed_chapters_with_drafts(s),
            None,
        ),
    }


def _chapter_volume_index(state, index: int) -> int:
    if state and state.volumes:
        for v in state.volumes:
            if v.chapter_start <= index <= v.chapter_end:
                return v.index
    return 0


def _chapter_title_from_text(text: str, index: int) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:60]
    return f"第{index}章"


def _chapter_index_from_path(path: str):
    m = _re.search(r"chapter_(\d{4})\.txt(?:\.draft)?$", os.path.basename(path))
    return int(m.group(1)) if m else None


def _chapter_candidates(project_root_abs: str, state, index: int, *, include_draft: bool = True, prefer_draft: bool = False):
    import glob as _glob

    chapter_fname = f"chapter_{index:04d}.txt"
    names = [chapter_fname + ".draft", chapter_fname] if (include_draft and prefer_draft) else [chapter_fname]
    if include_draft and not prefer_draft:
        names.append(chapter_fname + ".draft")

    candidates = []
    vol_idx = _chapter_volume_index(state, index)
    if vol_idx > 0:
        for name in names:
            candidates.append(os.path.join(project_root_abs, f"vol{vol_idx:02d}", name))

    for name in names:
        for p in _glob.glob(os.path.join(project_root_abs, "vol*", name)):
            if p not in candidates:
                candidates.append(p)

    if not candidates:
        for name in names:
            for p in _glob.glob(os.path.join(project_root_abs, "**", name), recursive=True):
                if p not in candidates:
                    candidates.append(p)
    return candidates


def _find_chapter_file(project_root_abs: str, state, index: int, *, include_draft: bool = True, prefer_draft: bool = False):
    candidates = _chapter_candidates(
        project_root_abs, state, index, include_draft=include_draft, prefer_draft=prefer_draft
    )
    path = next((c for c in candidates if os.path.isfile(c)), None)
    if not path:
        return None, False, _chapter_volume_index(state, index), candidates
    is_draft = path.endswith(".draft")
    vol_idx = _chapter_volume_index(state, index)
    if vol_idx <= 0:
        m = _re.search(r"vol(\d+)", path.replace("\\", "/"))
        if m:
            vol_idx = int(m.group(1))
    return path, is_draft, vol_idx, candidates


def _chapter_draft_warning(index: int) -> str:
    path = project_context.progress_status_file()
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for w in reversed(data.get("warnings") or []):
            if w.get("source") in (f"chapter:{index}:draft", f"chapter:{index}:canon"):
                return w.get("message") or ""
    except Exception:
        return ""
    return ""


def _draft_summary_dict(state, index: int, path: str, volume_index: int, content: str = None):
    from persistence.state import count_chapter_words

    if content is None:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    reason = _chapter_draft_warning(index)
    return {
        "index": index,
        "volume_index": volume_index,
        "title": _chapter_title_from_text(content, index),
        "summary": reason or "草稿：未通过定稿前校验，尚未保存为正式正文。",
        "word_count": count_chapter_words(content),
        "tension": "草稿",
        "key_events": [],
        "sp_triggered": [],
        "closing_hook": "",
        "pacing": None,
        "is_draft": True,
        "status": "draft",
        "row_id": f"{index}:draft",
        "draft_reason": reason,
    }


def _completed_chapters_with_drafts(state):
    import glob as _glob

    rows = [_summary_to_dict(c) for c in (state.completed_chapters or [])]
    project_root_abs = os.path.abspath(project_context.project_dir())
    for path in sorted(_glob.glob(os.path.join(project_root_abs, "vol*", "chapter_*.txt.draft"))):
        index = _chapter_index_from_path(path)
        if not index:
            continue
        vol_idx = _chapter_volume_index(state, index)
        if vol_idx <= 0:
            m = _re.search(r"vol(\d+)", path.replace("\\", "/"))
            vol_idx = int(m.group(1)) if m else 0
        rows.append(_draft_summary_dict(state, index, path, vol_idx))
    return sorted(rows, key=lambda r: (r.get("index") or 0, 0 if r.get("is_draft") else 1))


# ═══════════════════════════════════════════════════════
#  GET /api/state —— 概览
# ═══════════════════════════════════════════════════════

@app.route("/api/state")
def api_state_overview():
    s = _load()
    sections = list(_section_map().keys())
    return jsonify({
        "title": s.title,
        "genre": s.genre,
        "theme": s.theme,
        "num_volumes": len(s.volumes),
        "num_characters": len(s.characters),
        "num_chapters_done": len(s.completed_chapters),
        "sections": sections,
        "book_proposition": s.book_structure.book_proposition,
        "concept_pitch_line": s.concept_pitch.one_line_pitch,
    })


# ═══════════════════════════════════════════════════════
#  GET / PUT /api/section/<name>
# ═══════════════════════════════════════════════════════

@app.route("/api/section/<name>", methods=["GET"])
def api_get_section(name):
    """
    快速路径：直接读该 section 的 JSON 文件（分文件存储），避免全量 load_state。
    fallback：老逻辑通过 _load() + getter 组合（兼容未在 state_storage 注册的 section）。
    """
    smap = _section_map()
    if name not in smap:
        abort(404, description=f"未知 section：{name}")

    # 已写章节的 word_count 历史用 len()（字符数）存的——首次访问时按新算法（中文小说标准）重算
    if name == "completed_chapters":
        _ensure_chapter_word_counts_migrated(project_context.current())
        s = _load()
        return jsonify(_completed_chapters_with_drafts(s))

    # Fast path：直接读分文件 JSON（split state）
    try:
        from persistence import state_storage
        spec = state_storage._init_spec()
        if name in spec:
            path = state_storage.section_file(name)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                # section_file 里存的是 asdict 形式（已 Enum→value 化）
                # 直接返回即可——前端字段名和 state 完全对齐
                return jsonify(raw)
    except Exception as e:
        # 读分文件失败就 fallback 到完整加载
        print(f"[api_get_section] fast-path failed for {name}: {e}")

    # Slow path：完整加载 state 走 getter lambda
    s = _load()
    getter, _ = smap[name]
    return jsonify(getter(s))


@app.route("/api/section/<name>", methods=["PUT"])
def api_put_section(name):
    s = _load()
    smap = _section_map()
    if name not in smap:
        abort(404, description=f"未知 section：{name}")
    getter, setter = smap[name]
    if setter is None:
        abort(400, description=f"section '{name}' 不支持直接写入——请用 regenerate 或手动编辑 state.json")
    data = request.get_json()
    setter(s, data)
    _save(s, label=f"edit_{name}")

    # 编辑 canon 类 section 后自动扫描下游产物，违规聚合写 progress_warning，
    # 让用户在 ⚠ 徽章里立刻看到"以下章节 outline 与新数据不一致"——
    # 不静默继续。结果通过 progress_status.json 通道推送（前端轮询自然能看到），
    # 不改变 PUT 响应结构（保持向后兼容）。
    try:
        from agents.downstream_staleness import report_downstream_staleness, _CANON_SECTIONS
        if name in _CANON_SECTIONS:
            report_downstream_staleness(s, changed_section=name, scan_chapters=False)
    except Exception as _e:
        print(f"  ⚠ 下游 staleness 扫描失败（不阻塞）：{type(_e).__name__}: {_e}")

    return jsonify(getter(s))


# ═══════════════════════════════════════════════════════
#  POST /api/regen/<action>[/<arg>]
# ═══════════════════════════════════════════════════════

@app.route("/api/regen/<action>", methods=["POST"])
def api_regen(action):
    if action not in regen_mod.REGEN_ACTIONS:
        abort(404, description=f"未知 regen 动作：{action}")
    try:
        result = regen_mod.REGEN_ACTIONS[action]()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "ok", "action": action, "result": result})


@app.route("/api/regen/<action>/<arg>", methods=["POST"])
def api_regen_with_arg(action, arg):
    if action not in regen_mod.REGEN_ACTIONS_WITH_ARG:
        abort(404, description=f"未知 regen 动作：{action}")
    try:
        # 尝试转 int
        try:
            arg_val = int(arg)
        except ValueError:
            arg_val = arg
        result = regen_mod.REGEN_ACTIONS_WITH_ARG[action](arg_val)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "ok", "action": action, "arg": arg, "result": result})


# ═══════════════════════════════════════════════════════
#  版本控制
# ═══════════════════════════════════════════════════════

@app.route("/api/versions")
def api_versions():
    s = load_state()
    if s is None:
        return jsonify([])
    return jsonify(version_control.list_snapshots(s))


@app.route("/api/rollback/<timestamp>", methods=["POST"])
def api_rollback(timestamp):
    label_hint = request.args.get("label_hint", "")
    try:
        result = regen_mod.rollback(timestamp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# ═══════════════════════════════════════════════════════
#  HITL 审批
# ═══════════════════════════════════════════════════════

@app.route("/api/approvals")
def api_approvals():
    s = _load()
    return jsonify([p.__dict__ for p in s.pending_approvals])


@app.route("/api/approvals/<approval_id>/approve", methods=["POST"])
def api_approve(approval_id):
    s = _load()
    note = (request.get_json() or {}).get("note", "")
    found = False
    for ap in s.pending_approvals:
        if ap.approval_id == approval_id:
            ap.approved = True
            ap.approver_note = note
            found = True
            break
    if not found:
        abort(404)
    # 同步审批文件
    from project_mgmt import human_in_loop
    human_in_loop.check_pending_approvals(s)  # 这个会扫外部文件也同步进来
    save_state(s)
    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════════════
#  章节正文
# ═══════════════════════════════════════════════════════

@app.route("/api/projects/<project_id>/chapter/<int:chapter_index>/rewrite", methods=["POST"])
def api_rewrite_chapter(project_id, chapter_index):
    """带作者反馈重写一章（同步；阻塞直到写完）。"""
    from web.rewrite_chapter import rewrite_chapter
    body = request.get_json() or {}
    feedback = (body.get("feedback") or "").strip()
    try:
        result = rewrite_chapter(project_id, chapter_index, user_feedback=feedback)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/api/projects/<project_id>/chapter/write_next", methods=["POST"])
def api_write_next_chapter(project_id):
    """
    写下一章（单章生成）——同步阻塞直到写完。
    body 可选：{"chapter_index": N} 指定要写的章号；不填则自动找下一个未写章。
    """
    from web.write_next_chapter import write_one_chapter
    body = request.get_json(silent=True) or {}
    idx = int(body.get("chapter_index", 0) or 0)
    try:
        result = write_one_chapter(project_id, chapter_index=idx)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify(result)


@app.route("/api/projects/<project_id>/chapter/next_unwritten", methods=["GET"])
def api_next_unwritten_chapter(project_id):
    """查询下一个未写的章号——前端按钮显示"写第 N 章"。"""
    from project_mgmt import project_context
    project_context.set_project(project_id)
    from persistence.checkpoint import load_state, load_progress
    from web.write_next_chapter import _next_unwritten
    state = load_state()
    if state is None or not state.volumes:
        return jsonify({"chapter_index": 0, "message": "尚未规划卷结构"})
    progress = load_progress()
    idx = _next_unwritten(state, progress)
    total = sum(v.chapter_end - v.chapter_start + 1 for v in state.volumes)
    done = len(progress.get("chapters", []) or [])
    return jsonify({
        "chapter_index": idx,
        "done": done,
        "total": total,
        "all_done": idx == 0 and done > 0,
    })


@app.route("/api/projects/<project_id>/chapter/<int:index>/outline", methods=["PUT"])
def api_chapter_outline_edit(project_id, index):
    """允许用户在【写下一章】卡片上直接改本章的 outline 字段（goal/purpose/expression/must_include）。
    写回 vol.chapter_outlines[local-1]，下次写章时 directive/blueprint 会读到新值。
    """
    project_context.set_project(project_id)
    s = _load()
    if not s or not s.volumes:
        return jsonify({"error": "state 未加载或 volumes 为空"}), 400
    vol = next((v for v in s.volumes if v.chapter_start <= index <= v.chapter_end), None)
    if not vol:
        return jsonify({"error": f"章号 {index} 不在任何卷范围内"}), 404
    body = request.get_json() or {}
    local = index - vol.chapter_start
    if not vol.chapter_outlines or local >= len(vol.chapter_outlines):
        # 卷大纲尚未生成——补一个空骨架
        if not vol.chapter_outlines:
            vol.chapter_outlines = []
        while len(vol.chapter_outlines) <= local:
            vol.chapter_outlines.append({})
    o = vol.chapter_outlines[local] or {}
    if not isinstance(o, dict):
        o = {}
    # 只更新前端真传来的字段（含新增 chapter_focus / reader_hook）
    for k in ("goal", "purpose", "expression", "title", "structure_role", "position",
                "chapter_focus", "reader_hook"):
        if k in body:
            o[k] = body[k] or ""
    if "must_include" in body and isinstance(body["must_include"], list):
        o["must_include"] = body["must_include"]
    vol.chapter_outlines[local] = o
    save_state(s)
    return jsonify({"ok": True, "outline": o})


@app.route("/api/projects/<project_id>/chapter/<int:index>/preview", methods=["GET"])
def api_chapter_preview(project_id, index):
    """
    返回第 index 章的"待写预览上下文"——作者写前能看到所有已知条件：
      · 卷 / 章节类型 / 位置 / 预计张力+节奏
      · 本章大纲目标 / 分形 structure_role
      · 需植入/回收的伏笔
      · 预计触发的爽点 + 铺垫 SP
      · 本章的反转揭露层
      · 本章涉及的叙事线（+ 当前 phase）
      · 上章末尾原文 + story_thread 现状
      · 作者已填的灵感
    只读，不会触发 LLM。
    """
    project_context.set_project(project_id)
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    if not s.volumes:
        return jsonify({"error": "卷结构未规划"}), 400

    vol = next((v for v in s.volumes if v.chapter_start <= index <= v.chapter_end), None)
    if not vol:
        return jsonify({"error": f"章号 {index} 不在任何卷范围内"}), 404

    result = {
        "chapter_index": index,
        "volume_index": vol.index,
        "volume_title": vol.title,
        "volume_theme": vol.theme,
        "volume_antagonist": getattr(vol, "volume_antagonist", ""),
        "volume_structure_role": getattr(vol, "structure_role", ""),
        "volume_purpose": getattr(vol, "purpose", ""),
    }

    # 章节位置（卷首/卷尾/卷中高潮/普通）
    local = index - vol.chapter_start + 1
    total_in_vol = vol.chapter_end - vol.chapter_start + 1
    if local == 1:
        result["chapter_position"] = "卷首"
    elif local == total_in_vol:
        result["chapter_position"] = "卷尾"
    elif local == total_in_vol // 2 or local == total_in_vol // 2 + 1:
        result["chapter_position"] = "卷中高潮"
    else:
        result["chapter_position"] = "普通"
    result["local_index"] = local
    result["total_in_volume"] = total_in_vol

    # 章节类型（chapter_type_plans 预规划）
    try:
        result["chapter_type"] = s.chapter_type_for(index) or ""
    except Exception:
        result["chapter_type"] = ""

    # 章节大纲（来自 volume.chapter_outlines）
    outline = {}
    try:
        outlines = vol.chapter_outlines or []
        # 通常 outlines[i] 对应 volume 内第 i+1 章
        if 0 <= local - 1 < len(outlines):
            outline = outlines[local - 1] or {}
    except Exception as _e:
        print(f"  [preview ch{index}] 分支失败：{type(_e).__name__}: {_e}")
    result["outline_goal"] = outline.get("goal", "")
    result["outline_must_include"] = outline.get("must_include", [])
    result["outline_structure_role"] = outline.get("structure_role", "")
    result["outline_purpose"] = outline.get("purpose", "")
    result["outline_expression"] = outline.get("expression", "")
    # ── 新增字段（volume_planner Phase 4_vol 生成）──
    result["outline_chapter_focus"] = outline.get("chapter_focus", "")
    result["outline_reader_hook"] = outline.get("reader_hook", "")
    # stage 归属（让前端显示"本章属于哪个大情节"badge）
    sid = (outline.get("stage_id") or "").strip()
    if not sid:
        # outline 缺 stage_id 时（旧存档），按章节范围推断
        st = s.primary_stage_for_chapter(index)
        if st and st.volume == vol.index:
            sid = st.stage_id
    result["outline_stage_id"] = sid
    if sid:
        st = next((x for x in s.story_stages if x.stage_id == sid), None)
        result["outline_stage_name"] = st.name if st else ""
        result["outline_stage_role"] = st.structure_role if st else ""
    else:
        result["outline_stage_name"] = ""
        result["outline_stage_role"] = ""

    # 节奏 / 张力
    try:
        rseg = s.get_rhythm_for_chapter(index)
        if rseg:
            result["rhythm"] = getattr(rseg.rhythm_type, "value", str(rseg.rhythm_type))
            result["word_pace"] = rseg.word_pace
    except Exception as _e:
        print(f"  [preview ch{index}] 分支失败：{type(_e).__name__}: {_e}")

    # 伏笔：要植入/要回收
    try:
        from agents.foreshadow_manager import get_chapter_foreshadow_directive
        fw_dir = get_chapter_foreshadow_directive(s, index)
        result["foreshadow_plant"] = [
            {"fw_id": fw.fw_id, "content": fw.content[:60]}
            for fw in (fw_dir.get("plant") or [])
        ]
        result["foreshadow_resolve"] = [
            {"fw_id": fw.fw_id, "content": fw.content[:60]}
            for fw in (fw_dir.get("resolve") or [])
        ]
    except Exception as e:
        result["foreshadow_plant"] = []
        result["foreshadow_resolve"] = []

    # 爽点（触发的 + 待铺垫的）
    try:
        from agents.satisfaction_system import get_sp_for_chapter
        sp_ops = get_sp_for_chapter(s, index)
        result["sp_trigger"] = [
            {"sp_id": sp.sp_id, "title": sp.title,
             "intensity": sp.intensity, "payoff": sp.payoff_description[:60]}
            for sp in (sp_ops.get("trigger") or [])
        ]
        result["sp_setup"] = [
            {"setup_content": op.get("setup_content", "")[:60]}
            for op in (sp_ops.get("setup") or [])
        ]
    except Exception:
        result["sp_trigger"] = []
        result["sp_setup"] = []

    # 反转层揭露
    try:
        twists = s.twist_reveals_for_chapter(vol.index, index)
        result["twist_reveals"] = [
            {"chain": ch.title, "layer": layer.layer,
             "reveal": layer.reveal[:60]}
            for ch, layer in (twists or [])
        ]
    except Exception:
        result["twist_reveals"] = []

    # 叙事线（活跃）
    try:
        active = s.lines_active_in_chapter(index) or []
        lines = []
        for ln in active[:6]:
            phase = ln.get_phase_for_chapter(index)
            lines.append({
                "line_id": ln.line_id,
                "name": ln.name,
                "scope": getattr(ln.scope, "value", str(ln.scope)),
                "current_phase": phase.name if phase else "",
                "phase_goal": (phase.description[:60] if phase and phase.description else ""),
            })
        result["active_lines"] = lines
    except Exception:
        result["active_lines"] = []

    # 上章末尾原文（供下章承接参考，显示 400 字）
    prev_tail = ""
    if index > 1:
        prev_path = os.path.join(
            project_context.project_dir(),
            f"vol{vol.index:02d}",
            f"chapter_{index - 1:04d}.txt",
        )
        # 上一章可能在前一卷
        if not os.path.exists(prev_path):
            for v in s.volumes:
                if v.chapter_start <= index - 1 <= v.chapter_end:
                    prev_path = os.path.join(
                        project_context.project_dir(),
                        f"vol{v.index:02d}",
                        f"chapter_{index - 1:04d}.txt",
                    )
                    break
        if os.path.exists(prev_path):
            try:
                with open(prev_path, encoding="utf-8") as f:
                    txt = f.read()
                prev_tail = txt[-600:]
            except OSError:
                pass
    result["prev_chapter_tail"] = prev_tail

    # 故事线索现状（由 ThreadTracker 维护）
    try:
        th = s.story_thread
        result["story_thread"] = {
            "scene_end_state": (th.scene_end_state or "")[:200],
            "next_chapter_opening": (th.next_chapter_opening or "")[:120],
            "protagonist_goal": (th.protagonist_immediate_goal or "")[:100],
            "protagonist_emotion": (th.protagonist_emotional_state or "")[:80],
            "current_location": th.current_location or "",
            "open_loops": [
                {"desc": l.description[:80], "urgency": l.urgency, "closed": l.closed}
                for l in (th.open_loops or [])[:5] if not l.closed
            ],
        }
    except Exception:
        result["story_thread"] = {}

    # 作者已保存的章节灵感
    result["author_inspiration"] = (getattr(s, "chapter_inspirations", {}) or {}).get(index, "")

    # ── 记忆层：跨章节的上下文（让作者写前就能"记起"）──

    # 最近 3 章章节摘要（已写的）
    recent_summaries = []
    for ch in sorted((s.completed_chapters or []), key=lambda c: c.index)[-3:]:
        recent_summaries.append({
            "index": ch.index,
            "title": ch.title,
            "summary": (ch.summary or "")[:200],
            "tension": getattr(ch.tension, "value", str(ch.tension)),
            "key_events": (ch.key_events or [])[:3],
            "closing_hook": (ch.closing_hook or "")[:100],
        })
    result["recent_chapter_summaries"] = recent_summaries

    # 本章活跃叙事线的相关记忆（最近 5 条）
    try:
        active_line_ids = [ln.line_id for ln in (s.lines_active_in_chapter(index) or [])[:4]]
        mem_text = s.memory.format_line_memory(active_line_ids, last_n=5) if active_line_ids else ""
        result["line_memory"] = mem_text if mem_text != "暂无相关记忆。" else ""
    except Exception:
        result["line_memory"] = ""

    # 重要世界事件（最近 5 条发生在当前章之前的）
    try:
        past_events = [e for e in (s.world_events or []) if e.chapter_index < index]
        past_events = sorted(past_events, key=lambda e: e.chapter_index)[-5:]
        result["world_events_recent"] = [
            {
                "chapter": e.chapter_index,
                "importance": e.importance,
                "desc": (e.event_desc or "")[:80],
                "factions": list(e.affected_factions)[:3],
            }
            for e in past_events
        ]
    except Exception:
        result["world_events_recent"] = []

    # 本章相关角色的最新状态快照（谁在哪、受伤、情绪、物品）
    try:
        # 先收集本章预期出现的角色：主角 + 活跃线里的 characters 列表
        involved: list[str] = []
        prot = next((c for c in (s.characters or [])
                     if getattr(c.role, "value", str(c.role)) == "主角"), None)
        if prot:
            involved.append(prot.name)
        for ln in (s.lines_active_in_chapter(index) or [])[:3]:
            for n in (ln.characters or []):
                if n not in involved:
                    involved.append(n)
        # 去主角后仍取前 5 个
        involved = involved[:5]
        char_states = []
        for name in involved:
            snap = s.latest_state_snapshot(name)
            if not snap:
                continue
            char_states.append({
                "name": name,
                "as_of_chapter": snap.chapter_index,
                "location": snap.location or "",
                "injury": snap.injury or "",
                "emotion": snap.emotion or "",
                "realm": snap.realm or "",
                "items": list(snap.items_on_hand or [])[:4],
            })
        result["character_states"] = char_states
    except Exception:
        result["character_states"] = []

    # 红鲱鱼（假线索）操作
    try:
        rh_ops = s.red_herrings_for_chapter(index)
        result["red_herring_plant"] = [
            {"rh_id": rh.rh_id, "content": rh.content[:60]}
            for rh in (rh_ops.get("plant") or [])
        ]
        result["red_herring_debunk"] = [
            {"rh_id": rh.rh_id, "content": rh.content[:60]}
            for rh in (rh_ops.get("debunk") or [])
        ]
    except Exception:
        result["red_herring_plant"] = []
        result["red_herring_debunk"] = []

    # 主角历程（当前舞台 + 节拍）
    try:
        active_stages = s.get_active_stages(index) or []
        if active_stages:
            from agents.protagonist_journey import get_stage_beat_context
            beats_text = []
            for stage in active_stages[:2]:
                ctx = get_stage_beat_context(s, stage.stage_id)
                if ctx:
                    beats_text.append(f"[{stage.name}] {ctx[:200]}")
            result["stage_beat_context"] = "\n".join(beats_text)
        else:
            result["stage_beat_context"] = ""
    except Exception:
        result["stage_beat_context"] = ""

    # 本章张力期望（从 tension_history + emotional_curve 推）
    try:
        # 简易：look up rhythm segment tension if any
        rseg = s.get_rhythm_for_chapter(index)
        if rseg and getattr(rseg, "tension_arc", None):
            result["expected_tension"] = rseg.tension_arc
    except Exception as _e:
        print(f"  [preview ch{index}] 分支失败：{type(_e).__name__}: {_e}")

    return jsonify(result)


# ═══════════════════════════════════════════════════════
#  多模型 profile
# ═══════════════════════════════════════════════════════

@app.route("/api/llm_profiles")
def api_llm_profiles():
    """返回所有可选模型（用户自定义优先 + 内置目录合并）。"""
    from llm_layer import llm_profiles
    from llm_layer import user_models
    user_list = user_models.list_all(include_key=False)
    # 把内置 PROFILES 也暴露（注意 provider 分组，UI 好展示）
    builtin = [{"id": pid, "_builtin": True, **prof}
               for pid, prof in llm_profiles.PROFILES.items()]
    return jsonify({
        "user_models": user_list,
        "builtin_profiles": builtin,
        "providers": llm_profiles.list_providers(),
        "default": llm_profiles.DEFAULT_PROFILE_ID,
        "known_usages": user_models.all_usages(),
        # 前端复选框 tooltip 用——内置 usage 标签的可读说明（main / reviewer /
        # fallback / in_story_ai 等）。in_story_ai 是新加的：勾上后该 profile
        # 会出现在 SpecialAbility 编辑界面的"真 AI 接入"下拉框里
        "usage_descriptions": user_models.usage_descriptions(),
    })


@app.route("/api/user_models", methods=["GET"])
def api_user_models_list():
    """列出所有用户自定义模型（api_key 遮挡）。"""
    from llm_layer import user_models
    return jsonify({"models": user_models.list_all(include_key=False)})


@app.route("/api/projects/<project_id>/asset_candidates")
def api_project_asset_candidates(project_id):
    """章后 asset 追踪——返回当前所有疑似新 asset 候选（连续 N 章出现的）。
    前端"力量体系"页可加 widget 显示 + 让用户一键登记为 SpecialAbility。
    """
    from project_mgmt import project_context
    project_context.set_project(project_id)
    s = _load()
    try:
        from agents.chapter_asset_tracker import list_pending_candidates
        return jsonify({
            "candidates": list_pending_candidates(s, min_chapters=2),
        })
    except Exception as e:
        return jsonify({"candidates": [], "error": str(e)}), 200


@app.route("/api/llm_call/routing")
def api_llm_call_routing():
    """返回每个 task type 当前实际走的 profile——前端可视化"哪类任务走哪个模型"。

    示例返回：
      {
        "writing":    {"usage":"main", "active_profile":"deepseek_v4_pro", "fallback_used":false, ...},
        "extraction": {"usage":"extractor", "active_profile":null, "fallback_used":true, "fallback_to":"deepseek_v4_pro"},
        ...
      }
    """
    from llm_layer.llm_call import get_task_routing_summary, TASK_DESCRIPTIONS
    return jsonify({
        "tasks": get_task_routing_summary(),
        "task_descriptions": TASK_DESCRIPTIONS,
    })


@app.route("/api/user_models/active_usage_map")
def api_user_models_active_usage_map():
    """返回每个内置 usage 当前实际生效的 profile（first-hit），以及
    UNIQUE usage 的多占冲突列表。前端模型管理页用此显示"main 当前生效=X"
    + 红字警告"还有 Y 个 profile 勾了 main 但不会生效"。

    示例返回：
      {
        "active": {"main": {...profile...}, "reviewer": {...}, "extractor": null, ...},
        "conflicts": [
          {"usage": "main", "active_profile_id": "doubao_...",
           "shadowed_profile_ids": ["deepseek_v4_pro"], "total": 2}
        ]
      }
    """
    from llm_layer import user_models
    active = {}
    for usage, m in user_models.active_usage_map().items():
        if m:
            # 遮挡 api_key
            m = dict(m)
            m.pop("api_key", None)
            m["api_key_masked"] = user_models._mask_key(
                user_models.find_by_usage(usage).get("api_key", "") if user_models.find_by_usage(usage) else ""
            )
        active[usage] = m
    return jsonify({
        "active": active,
        "conflicts": user_models.detect_usage_conflicts(),
        "unique_usages": sorted(user_models.UNIQUE_USAGES),
    })


@app.route("/api/user_models/in_story_ai")
def api_user_models_in_story_ai():
    """返回所有勾选了 in_story_ai usage 的 profile——
    SpecialAbility 编辑界面的 external_llm_profile 下拉框专用。

    避免用户把主写作 / 审核员模型误选作 in-story AI——下拉框只列
    专门标记可用的 profile。同时附 usage_descriptions 让 UI 可显示 tooltip。
    """
    from llm_layer import user_models
    return jsonify({
        "models": user_models.list_in_story_ai_profiles(),
        "usage_descriptions": user_models.usage_descriptions(),
    })


def _surface_usage_conflicts():
    """扫描 UNIQUE_USAGES 多占冲突，写 progress_warning（同 source 自动去重）。
    模型管理任何写操作后调一次——让用户在 ⚠ 徽章立即看到生效情况。
    """
    try:
        from llm_layer import user_models
        from persistence.checkpoint import add_progress_warning, clear_progress_warnings
        conflicts = user_models.detect_usage_conflicts()
        # 先清旧 warning——本次扫描没冲突就别留旧的
        clear_progress_warnings(source="user_models:usage_conflicts")
        if conflicts:
            lines = []
            for c in conflicts:
                lines.append(
                    f"usage='{c['usage']}' 被 {c['total']} 个 profile 同时占用 → "
                    f"实际生效：{c['active_profile_id']}；被覆盖：{','.join(c['shadowed_profile_ids'])}"
                )
            add_progress_warning(
                level="warn",
                source="user_models:usage_conflicts",
                message=("模型用途冲突（first-hit 静默选第一个）：\n" + "\n".join(lines)
                         + "\n建议：在'模型管理'里取消多余 profile 的勾选，保留你想生效的那一个"),
            )
    except Exception:
        pass


@app.route("/api/user_models", methods=["POST"])
def api_user_models_add():
    """新增用户自定义模型。

    默认会发一次真实 LLM ping 验证连通——慢 endpoint 会让保存等 5-20s。
    支持 ?skip_test=1 跳过验证（用户已知模型可达时用）。
    """
    from llm_layer import user_models
    body = request.get_json() or {}
    skip_test = request.args.get("skip_test", "").lower() in ("1", "true", "yes")
    test = None
    try:
        if not skip_test:
            test = user_models.test_model_config(body, timeout=10.0)
        entry = user_models.add(body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _surface_usage_conflicts()
    # 返回时遮挡 key
    result = dict(entry)
    result["api_key_masked"] = user_models._mask_key(result.get("api_key", ""))
    result.pop("api_key", None)
    return jsonify({"status": "ok", "model": result, "test": test, "skipped_test": skip_test})


# 决定是否需要重测 LLM 连通的关键字段——这些任一变化才 ping，
# 其它字段（display_name / usages / notes）变化不重测
_RETEST_TRIGGER_FIELDS = ("base_url", "api_key", "model", "extra_body")


@app.route("/api/user_models/<model_id>", methods=["PUT"])
def api_user_models_update(model_id):
    """更新——字段可部分传。api_key 留空则保留原 key。

    优化：只在 base_url / api_key / model / extra_body 实际发生变化时才 ping LLM；
    只改 display_name / usages / notes 等元数据时跳过验证，秒级响应。
    也支持 ?skip_test=1 强制跳过验证。
    """
    from llm_layer import user_models
    body = request.get_json() or {}
    skip_test = request.args.get("skip_test", "").lower() in ("1", "true", "yes")
    try:
        existing = user_models.get(model_id, include_key=True)
        if not existing:
            return jsonify({"error": f"未找到 id={model_id}"}), 404
        test_payload = dict(existing)
        for k, v in body.items():
            if k == "api_key" and not str(v or "").strip():
                continue
            test_payload[k] = v
        # 判断关键字段是否变了——没变就不 ping，省 5-20s
        critical_changed = any(
            (test_payload.get(f) or "") != (existing.get(f) or "")
            for f in _RETEST_TRIGGER_FIELDS
        )
        test = None
        if critical_changed and not skip_test:
            test = user_models.test_model_config(test_payload, timeout=10.0)
        entry = user_models.update(model_id, body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    _surface_usage_conflicts()
    result = dict(entry)
    result["api_key_masked"] = user_models._mask_key(result.get("api_key", ""))
    result.pop("api_key", None)
    return jsonify({
        "status": "ok",
        "model": result,
        "test": test,
        "tested": test is not None,
        "skipped_test": skip_test,
    })


@app.route("/api/user_models/<model_id>", methods=["DELETE"])
def api_user_models_delete(model_id):
    from llm_layer import user_models
    ok = user_models.remove(model_id)
    if not ok:
        return jsonify({"error": f"未找到 id={model_id}"}), 404
    _surface_usage_conflicts()  # 删除后冲突状况也可能变
    return jsonify({"status": "ok"})


@app.route("/api/projects/<project_id>/llm_profile", methods=["GET"])
def api_project_llm_profile_get(project_id):
    """
    查项目当前主模型。支持用户模型和内置模型——用 llm_runtime._lookup_profile
    统一查，避免用 llm_profiles.get() 对未知 id 静默回退默认导致展示错误。
    """
    from llm_layer import llm_runtime
    from llm_layer import llm_profiles
    pid = llm_runtime.get_project_profile_id(project_id)
    prof = llm_runtime._lookup_profile(pid) or llm_profiles.get(pid)
    # 遮挡 API key
    if prof and "_user_api_key" in prof:
        prof = {k: v for k, v in prof.items() if k != "_user_api_key"}
    return jsonify({
        "profile_id": pid,
        "profile": prof,
    })


@app.route("/api/projects/<project_id>/llm_profile", methods=["PUT"])
def api_project_llm_profile_set(project_id):
    from llm_layer import llm_runtime
    body = request.get_json() or {}
    pid = body.get("profile_id")
    if not pid:
        abort(400, description="必须提供 profile_id")
    try:
        meta = llm_runtime.set_project_profile(project_id, pid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "ok", "meta": meta})


@app.route("/api/chapter/<int:index>")
def api_chapter_text(index):
    """
    读指定章节正文——超级健壮版（正式稿和 .draft 草稿都能找到）。
    """
    import glob as _glob

    current_project = project_context.current()
    project_root_abs = os.path.abspath(project_context.project_dir())
    print(f"[api_chapter_text] ch={index} project={current_project} root={project_root_abs}")

    if not os.path.isdir(project_root_abs):
        return jsonify({
            "error": f"项目目录不存在：{project_root_abs}",
            "current_project": current_project,
            "output_dir": project_root_abs,
            "hint": "URL 的 project 参数可能写错了",
        }), 404

    s = _load()
    path, is_draft, vol_idx, candidates = _find_chapter_file(
        project_root_abs, s, index,
        include_draft=True,
        prefer_draft=request.args.get("draft") in ("1", "true", "yes"),
    )

    if not path:
        all_chapters = sorted(
            _glob.glob(os.path.join(project_root_abs, "vol*", "chapter_*.txt"))
            + _glob.glob(os.path.join(project_root_abs, "vol*", "chapter_*.txt.draft"))
        )
        available = [os.path.relpath(c, project_root_abs).replace("\\", "/") for c in all_chapters[:20]]
        print(f"[api_chapter_text] 404 tried={candidates} available={available[:5]}")
        return jsonify({
            "error": f"章节 {index} 文件不存在",
            "searched_paths": candidates,
            "current_project": current_project,
            "output_dir": project_root_abs,
            "available_chapters_sample": available[:10],
            "total_available": len(all_chapters),
        }), 404

    print(f"[api_chapter_text] found {path}")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    summary = next((c for c in (s.completed_chapters if s else []) if c.index == index), None)
    summary_dict = _summary_to_dict(summary) if summary else None
    if is_draft and not summary_dict:
        summary_dict = _draft_summary_dict(s, index, path, vol_idx, content)
    return jsonify({
        "index": index,
        "volume": vol_idx,
        "content": content,
        "summary": summary_dict,
        "is_draft": is_draft,
        "status": "draft" if is_draft else "final",
        "draft_reason": _chapter_draft_warning(index) if is_draft else "",
    })


@app.route("/api/chapter/<int:index>/accept_draft", methods=["POST"])
def api_chapter_accept_draft(index):
    """
    作者人工确认：把未过校验的 .draft 草稿保存为正式正文。
    这不会修复 canon 问题，只是明确由作者接管风险后放行。
    """
    from persistence.checkpoint import mark_chapter_done, clear_progress_warnings
    from persistence.state import ChapterSummary, TensionLevel, count_chapter_words

    body = request.get_json(silent=True) or {}
    overwrite = bool(body.get("overwrite"))
    project_root_abs = os.path.abspath(project_context.project_dir())
    s = _load()
    path, is_draft, vol_idx, candidates = _find_chapter_file(
        project_root_abs, s, index, include_draft=True, prefer_draft=True
    )
    if not path or not is_draft:
        return jsonify({
            "error": f"没有找到第 {index} 章草稿",
            "searched_paths": candidates,
        }), 404

    final_path = path[:-len(".draft")]
    if os.path.exists(final_path) and not overwrite:
        return jsonify({
            "error": "正式正文已存在。如需覆盖，请传 overwrite=true。",
            "final_path": final_path,
        }), 409

    os.replace(path, final_path)
    with open(final_path, encoding="utf-8") as f:
        content = f.read()

    summary = next((c for c in s.completed_chapters if c.index == index), None)
    if summary:
        summary.word_count = count_chapter_words(content)
        if not summary.title:
            summary.title = _chapter_title_from_text(content, index)
    else:
        summary = ChapterSummary(
            index=index,
            volume_index=vol_idx,
            title=_chapter_title_from_text(content, index),
            summary="作者手动将未通过定稿前校验的草稿保存为正式正文。",
            word_count=count_chapter_words(content),
            tension=TensionLevel.RISING,
            key_events=[],
            sp_triggered=[],
            closing_hook="",
        )
        s.completed_chapters.append(summary)

    if s.current_chapter_index < index:
        s.current_chapter_index = index
    s.completed_chapters = sorted(s.completed_chapters, key=lambda c: c.index)
    mark_chapter_done(index, s)
    clear_progress_warnings(source=f"chapter:{index}:draft")
    clear_progress_warnings(source=f"chapter:{index}:canon")
    return jsonify({
        "status": "ok",
        "index": index,
        "path": final_path,
        "summary": _summary_to_dict(summary),
    })


@app.route("/api/chapter/<int:index>/draft", methods=["DELETE"])
def api_chapter_discard_draft(index):
    """
    弃用草稿：只删除 chapter_XXXX.txt.draft，不改 completed_chapters、
    progress.chapters、current_chapter_index，也不清理后续章节状态。
    """
    from persistence.checkpoint import clear_progress_warnings

    project_root_abs = os.path.abspath(project_context.project_dir())
    s = _load()
    path, is_draft, _, candidates = _find_chapter_file(
        project_root_abs, s, index, include_draft=True, prefer_draft=True
    )
    if not path or not is_draft:
        return jsonify({
            "error": f"没有找到第 {index} 章草稿",
            "searched_paths": candidates,
        }), 404

    os.remove(path)
    clear_progress_warnings(source=f"chapter:{index}:draft")
    clear_progress_warnings(source=f"chapter:{index}:canon")
    return jsonify({
        "status": "ok",
        "index": index,
        "deleted_file": os.path.relpath(path, project_root_abs).replace("\\", "/"),
    })


@app.route("/api/chapter/<int:index>", methods=["DELETE"])
def api_chapter_delete(index):
    """
    删除指定章节。

    mode（query 参数）:
      "only_this"       - 只删这一章（可能导致后续章节与此章失联，危险）
      "this_and_after"  - 默认：删此章及之后所有章节（干净重来）
      "all"             - 删全书所有章节

    会同时：
      - 删除磁盘上的 chapter_NNNN.txt 文件
      - 从 state.completed_chapters 移除对应条目
      - 从 progress.chapters 移除（下次会重写）
      - 重置 state.current_chapter_index 到删除点前一章
      - 如果删到卷首，重置卷内状态
    """
    import glob as _glob
    from persistence.checkpoint import save_state, load_progress, _save_progress

    mode = request.args.get("mode", "this_and_after")
    if mode not in ("only_this", "this_and_after", "all"):
        return jsonify({"error": f"非法 mode：{mode}"}), 400

    project_root_abs = os.path.abspath(project_context.project_dir())
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400

    # ── 算出要删的章节索引集合（只删除正式正文；草稿由 /draft 单独弃用） ──
    chapter_files = _glob.glob(os.path.join(project_root_abs, "vol*", "chapter_*.txt"))
    chapter_indexes = [i for i in (_chapter_index_from_path(p) for p in chapter_files) if i is not None]
    if mode == "all":
        to_delete = set(chapter_indexes)
    elif mode == "only_this":
        to_delete = {index}
    else:  # this_and_after
        to_delete = {i for i in chapter_indexes if i >= index}

    if not to_delete:
        return jsonify({"error": f"没有找到要删除的章节（起始 {index}）"}), 404

    # ── 删磁盘文件 ──
    deleted_files = []
    for idx in sorted(to_delete):
        for p in _glob.glob(os.path.join(project_root_abs, "vol*", f"chapter_{idx:04d}.txt")):
            try:
                os.remove(p)
                deleted_files.append(os.path.relpath(p, project_root_abs).replace("\\", "/"))
            except OSError as e:
                print(f"[delete_chapter] 删除 {p} 失败：{e}")

    # ── 更新 state ──
    s.completed_chapters = [c for c in s.completed_chapters if c.index not in to_delete]
    # 从 progress.chapters 移除
    progress = load_progress()
    progress["chapters"] = [c for c in progress.get("chapters", []) if c not in to_delete]
    _save_progress(progress)

    # ── 重置 current_chapter_index ──
    # 取保留章节的最大值；没有就置 0
    if s.completed_chapters:
        s.current_chapter_index = max(c.index for c in s.completed_chapters)
    else:
        s.current_chapter_index = 0

    # ── 清理所有按章追加/按章打标的派生状态 ──
    # 包含 memory、character_state_history、world_events、tension_history、
    # satisfaction/foreshadow/red_herring/fortune 的触发状态、叙事线阶段、
    # story_thread（尾部删时重置）等
    from persistence.chapter_cleanup import cleanup_chapter_state
    cleanup_chapter_state(s, to_delete)

    # ── 存 state ──
    save_state(s)

    print(f"[delete_chapter] mode={mode} deleted {len(deleted_files)} files: {deleted_files[:3]}...")

    return jsonify({
        "status": "ok",
        "mode": mode,
        "deleted_chapter_indexes": sorted(to_delete),
        "deleted_files": deleted_files,
        "remaining_chapters": len(s.completed_chapters),
        "new_current_chapter_index": s.current_chapter_index,
    })


# ═══════════════════════════════════════════════════════
#  章节对话调整（chapter chat）——不动骨架改笔触的流式工具
# ═══════════════════════════════════════════════════════

# system 模板来源：agents.chat_editor.SYSTEM_TEMPLATE
# 用户可通过 /api/prompts 覆盖；prompts_registry 会把覆盖值 setattr 到模块上，
# 下面每次调用 getattr 拿到的都是最新版。
_CHAT_PROMPT_ID = "agents.chat_editor:SYSTEM_TEMPLATE"


def _build_chat_system_prompt(state, chapter_index, vol, summary, chapter_text):
    from persistence.state import ChatMessage  # noqa
    from agents import chat_editor as _chat_editor
    history = state.chapter_chats.get(chapter_index, []) or []
    prior_user_msgs = [m.content.strip() for m in history if m.role == "user" and m.content.strip()]
    if prior_user_msgs:
        lines = "\n".join(f"{i+1}. {msg}" for i, msg in enumerate(prior_user_msgs))
        prior_block = (
            "作者此前已经跟你提过的要求（按时间顺序；底稿已经是按这些要求累计修改过的版本，仅供参考）：\n"
            f"{lines}\n\n"
        )
    else:
        prior_block = ""
    from persistence.state import count_chapter_words
    wc = count_chapter_words(chapter_text)
    template = getattr(_chat_editor, "SYSTEM_TEMPLATE", "")
    try:
        return template.format(
            chapter_index=chapter_index,
            volume_index=vol.index,
            volume_title=getattr(vol, "title", "") or "",
            summary=(summary.summary if summary else "") or "（无摘要）",
            word_count=wc,
            prior_requests_block=prior_block,
            chapter_text=chapter_text,
        )
    except (KeyError, IndexError) as e:
        # 用户把模板里的格式变量改坏了——回退到原始模板
        print(f"[chat] system 模板 format 失败（{type(e).__name__}: {e}），使用代码默认值")
        from utils import prompts_registry as pr
        entry = pr.get_entry(_CHAT_PROMPT_ID)
        fallback = entry.default if entry else ""
        return fallback.format(
            chapter_index=chapter_index,
            volume_index=vol.index,
            volume_title=getattr(vol, "title", "") or "",
            summary=(summary.summary if summary else "") or "（无摘要）",
            word_count=wc,
            prior_requests_block=prior_block,
            chapter_text=chapter_text,
        )


_word_count_migrated_projects: set = set()


def _ensure_chapter_word_counts_migrated(project_id: str) -> None:
    """已写章节的 word_count 历史用字符数（含标点空格）存——按新算法（汉字+英文word+数字）
    重算并写回分片 JSON。每个项目每次启动只跑一次。
    """
    if project_id in _word_count_migrated_projects:
        return
    _word_count_migrated_projects.add(project_id)
    try:
        from persistence import state_storage
        path = state_storage.section_file("completed_chapters")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or not data:
            return
        from persistence.state import count_chapter_words
        changed = 0
        for c in data:
            idx = c.get("index")
            vol_idx = c.get("volume_index")
            if idx is None or vol_idx is None:
                continue
            chap_path = os.path.join(
                project_context.project_dir(project_id),
                f"vol{vol_idx:02d}",
                f"chapter_{idx:04d}.txt",
            )
            if not os.path.exists(chap_path):
                continue
            try:
                with open(chap_path, encoding="utf-8") as fc:
                    text = fc.read()
            except OSError:
                continue
            new_wc = count_chapter_words(text)
            if c.get("word_count") != new_wc:
                c["word_count"] = new_wc
                changed += 1
        if changed > 0:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[migrate-wc] {project_id}: 重算 {changed} 章字数（按新算法）")
    except Exception as e:
        print(f"[migrate-wc] {project_id} 失败：{type(e).__name__}: {e}")


def _find_chapter_path(state, chapter_index):
    """(path, volume) 对，找不到返回 (None, None)"""
    vol = next((v for v in state.volumes if v.chapter_start <= chapter_index <= v.chapter_end), None)
    if not vol:
        return None, None
    path = os.path.join(
        project_context.project_dir(),
        f"vol{vol.index:02d}",
        f"chapter_{chapter_index:04d}.txt",
    )
    return path, vol


@app.route("/api/chapter/<int:index>/chat", methods=["GET"])
def api_chapter_chat_history(index):
    """读某章的对话历史。"""
    s = _load()
    msgs = (s.chapter_chats or {}).get(index, []) if s else []
    return jsonify({
        "chapter_index": index,
        "messages": [dataclasses.asdict(m) for m in msgs],
    })


@app.route("/api/chapter/<int:index>/chat", methods=["DELETE"])
def api_chapter_chat_clear(index):
    """清空某章的对话历史。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    if s.chapter_chats and index in s.chapter_chats:
        s.chapter_chats.pop(index, None)
    save_state(s)
    return jsonify({"status": "ok", "chapter_index": index})


@app.route("/api/chapter/<int:index>/chat/message", methods=["POST"])
def api_chapter_chat_send(index):
    """发一条消息；SSE 流式返回 AI 生成的新章节正文。
    事件格式：
      data: {"type":"delta","text":"..."}\n\n   — 一个文字片段
      data: {"type":"done","full_length":N}\n\n — 流结束，服务端已存好
      data: {"type":"error","message":"..."}    — 出错
    """
    body = request.get_json(silent=True) or {}
    user_msg = (body.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "空消息"}), 400

    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400

    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404
    if not os.path.exists(path):
        return jsonify({"error": f"章节文件不存在：{path}"}), 404

    with open(path, encoding="utf-8") as f:
        chapter_text = f.read()

    summary = next((c for c in s.completed_chapters if c.index == index), None)
    system_prompt = _build_chat_system_prompt(s, index, vol, summary, chapter_text)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    def generate():
        import datetime as _dt
        from llm_layer.llm import chat_stream
        from persistence.state import ChatMessage
        acc: list[str] = []
        try:
            for piece in chat_stream(messages, temperature=0.85, max_tokens=12000):
                acc.append(piece)
                data = json.dumps({"type": "delta", "text": piece}, ensure_ascii=False)
                yield f"data: {data}\n\n"
        except Exception as e:
            err = json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False)
            yield f"data: {err}\n\n"
            return

        full = "".join(acc)
        now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        if s.chapter_chats is None:
            s.chapter_chats = {}
        s.chapter_chats.setdefault(index, []).extend([
            ChatMessage(role="user", content=user_msg, ts=now),
            ChatMessage(role="assistant", content=full, ts=now),
        ])
        try:
            save_state(s)
        except Exception as e:
            err = json.dumps({"type": "error", "message": f"保存对话失败：{e}"},
                             ensure_ascii=False)
            yield f"data: {err}\n\n"
            return

        done = json.dumps({"type": "done", "full_length": len(full)}, ensure_ascii=False)
        yield f"data: {done}\n\n"

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    # 禁止中间层缓冲，让 SSE 立刻吐出
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/chapter/<int:index>/chat/accept", methods=["POST"])
def api_chapter_chat_accept(index):
    """采纳一版新正文——覆盖磁盘 chapter 文件，更新 word_count。"""
    body = request.get_json(silent=True) or {}
    new_text = body.get("text", "") or ""
    if not new_text.strip():
        return jsonify({"error": "空正文，不能采纳"}), 400

    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400

    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404

    # 采纳前存快照，留后路
    version_control.snapshot(
        s, label=f"before_chat_accept_ch{index}",
        chapter_index=index,
        notes="chat 采纳前快照",
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)

    from persistence.state import count_chapter_words
    wc = count_chapter_words(new_text)
    summary = next((c for c in s.completed_chapters if c.index == index), None)
    if summary:
        summary.word_count = wc

    save_state(s)
    return jsonify({
        "status": "ok",
        "chapter_index": index,
        "word_count": wc,
    })


# ═══════════════════════════════════════════════════════
#  章节能力审计（ability_audits）—— 金手指/技能使用合理性
# ═══════════════════════════════════════════════════════

def _audit_to_dict(audit):
    if audit is None:
        return None
    return {
        "chapter_index": audit.chapter_index,
        "ability_uses": [dataclasses.asdict(u) for u in audit.ability_uses],
        "issues": [dataclasses.asdict(i) for i in audit.issues],
        "overall_score": audit.overall_score,
        "summary": audit.summary,
        "ts": audit.ts,
        "auditor_model": audit.auditor_model,
    }


@app.route("/api/chapter/<int:index>/ability_audit", methods=["GET"])
def api_chapter_ability_audit_get(index):
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audit = (s.ability_audits or {}).get(index)
    return jsonify({
        "chapter_index": index,
        "audit": _audit_to_dict(audit),
    })


@app.route("/api/chapter/<int:index>/ability_audit", methods=["POST"])
def api_chapter_ability_audit_run(index):
    """手动触发一次能力审计（同步阻塞）。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404
    if not os.path.exists(path):
        return jsonify({"error": f"章节文件不存在：{path}"}), 404
    with open(path, encoding="utf-8") as f:
        chapter_text = f.read()

    try:
        from agents.ability_auditor import audit_chapter
        audit = audit_chapter(s, index, chapter_text)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if audit is None:
        return jsonify({"error": "审计失败（LLM 无返回或解析失败），未更新"}), 500

    s.ability_audits[index] = audit
    save_state(s)
    return jsonify({
        "status": "ok",
        "chapter_index": index,
        "audit": _audit_to_dict(audit),
    })


@app.route("/api/ability_audits", methods=["GET"])
def api_ability_audits_all():
    """全书所有已审计章节的简要列表。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audits = s.ability_audits or {}
    items = []
    for idx in sorted(audits.keys()):
        a = audits[idx]
        sev_counts = {"critical": 0, "major": 0, "minor": 0}
        for iss in a.issues:
            sev_counts[iss.severity] = sev_counts.get(iss.severity, 0) + 1
        items.append({
            "chapter_index": idx,
            "overall_score": a.overall_score,
            "summary": a.summary,
            "uses_count": len(a.ability_uses),
            "issue_count": len(a.issues),
            "sev_counts": sev_counts,
            "ts": a.ts,
        })
    return jsonify({"items": items})


# ═══════════════════════════════════════════════════════
#  章节读者视角审计（reader_audits）—— 读者会不会追更
# ═══════════════════════════════════════════════════════

def _reader_audit_to_dict(audit):
    if audit is None:
        return None
    return {
        "chapter_index": audit.chapter_index,
        "new_info_density": audit.new_info_density,
        "emotional_anchor": audit.emotional_anchor,
        "hook_strength": audit.hook_strength,
        "novelty": audit.novelty,
        "satisfaction_balance": audit.satisfaction_balance,
        "fluency": audit.fluency,
        "empathy_depth": audit.empathy_depth,
        "retention_estimate": audit.retention_estimate,
        "dropout_risk_points": list(audit.dropout_risk_points or []),
        "issues": [dataclasses.asdict(i) for i in audit.issues],
        "overall_score": audit.overall_score,
        "summary": audit.summary,
        "ts": audit.ts,
        "auditor_model": audit.auditor_model,
    }


@app.route("/api/chapter/<int:index>/reader_audit", methods=["GET"])
def api_chapter_reader_audit_get(index):
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audit = (s.reader_audits or {}).get(index)
    return jsonify({
        "chapter_index": index,
        "audit": _reader_audit_to_dict(audit),
    })


@app.route("/api/chapter/<int:index>/reader_audit", methods=["POST"])
def api_chapter_reader_audit_run(index):
    """手动触发一次读者视角审计。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404
    if not os.path.exists(path):
        return jsonify({"error": f"章节文件不存在：{path}"}), 404
    with open(path, encoding="utf-8") as f:
        chapter_text = f.read()

    try:
        from agents.reader_experience_auditor import audit_chapter as reader_audit
        audit = reader_audit(s, index, chapter_text)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if audit is None:
        return jsonify({"error": "读者审计失败（LLM 无返回或解析失败）"}), 500

    s.reader_audits[index] = audit
    save_state(s)
    return jsonify({
        "status": "ok",
        "chapter_index": index,
        "audit": _reader_audit_to_dict(audit),
    })


@app.route("/api/reader_audits", methods=["GET"])
def api_reader_audits_all():
    """全书所有已做过读者审计的章节概要。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audits = s.reader_audits or {}
    items = []
    for idx in sorted(audits.keys()):
        a = audits[idx]
        sev_counts = {"critical": 0, "major": 0, "minor": 0}
        for iss in a.issues:
            sev_counts[iss.severity] = sev_counts.get(iss.severity, 0) + 1
        items.append({
            "chapter_index": idx,
            "overall_score": a.overall_score,
            "retention_estimate": a.retention_estimate,
            "emotional_anchor": a.emotional_anchor,
            "hook_strength": a.hook_strength,
            "summary": a.summary,
            "issue_count": len(a.issues),
            "sev_counts": sev_counts,
            "risk_count": len(a.dropout_risk_points),
            "ts": a.ts,
        })
    return jsonify({"items": items})


# ═══════════════════════════════════════════════════════
#  章节对话质量审计（dialogue_audits）
# ═══════════════════════════════════════════════════════

def _dialogue_audit_to_dict(audit):
    if audit is None:
        return None
    return {
        "chapter_index": audit.chapter_index,
        "total_dialogue_count": audit.total_dialogue_count,
        "speaking_characters": list(audit.speaking_characters or []),
        "dialogue_ratio_percent": audit.dialogue_ratio_percent,
        "subtext_density": audit.subtext_density,
        "voice_distinctiveness": audit.voice_distinctiveness,
        "action_beats_integration": audit.action_beats_integration,
        "emotional_pacing": audit.emotional_pacing,
        "address_accuracy": audit.address_accuracy,
        "infodump_level": audit.infodump_level,
        "dialogue_purpose": audit.dialogue_purpose,
        "issues": [dataclasses.asdict(i) for i in audit.issues],
        "overall_score": audit.overall_score,
        "summary": audit.summary,
        "ts": audit.ts,
        "auditor_model": audit.auditor_model,
    }


@app.route("/api/chapter/<int:index>/dialogue_audit", methods=["GET"])
def api_chapter_dialogue_audit_get(index):
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audit = (s.dialogue_audits or {}).get(index)
    return jsonify({"chapter_index": index, "audit": _dialogue_audit_to_dict(audit)})


@app.route("/api/chapter/<int:index>/dialogue_audit", methods=["POST"])
def api_chapter_dialogue_audit_run(index):
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404
    if not os.path.exists(path):
        return jsonify({"error": f"章节文件不存在：{path}"}), 404
    with open(path, encoding="utf-8") as f:
        chapter_text = f.read()

    try:
        from agents.dialogue_auditor import audit_chapter as dialogue_audit
        audit = dialogue_audit(s, index, chapter_text)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if audit is None:
        return jsonify({"error": "对话审计失败（LLM 无返回或解析失败）"}), 500

    s.dialogue_audits[index] = audit
    save_state(s)
    return jsonify({"status": "ok", "chapter_index": index, "audit": _dialogue_audit_to_dict(audit)})


# ═══════════════════════════════════════════════════════
#  氛围库（atmosphere_library）—— 让世界活起来的细节碎片
# ═══════════════════════════════════════════════════════

@app.route("/api/antagonists/design_depth", methods=["POST"])
def api_antagonists_design_depth():
    """触发为所有反派补充深度字段（信仰/魅力/绝望时刻/POV/伤痕）。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    try:
        from agents.antagonist_depth_designer import design_antagonist_depth
        result = design_antagonist_depth(s)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    save_state(s)
    return jsonify(result)


@app.route("/api/romance_arcs", methods=["GET"])
def api_romance_arcs_get():
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    arcs = []
    for a in (s.romance_arcs or []):
        arcs.append({
            "relationship_id": a.relationship_id,
            "char_a": a.char_a, "char_b": a.char_b,
            "label": a.relationship_label,
            "progress": a.progress_score, "target": a.target_progress,
            "stage": a.current_stage,
            "last_interaction_chapter": a.last_interaction_chapter,
            "events_count": len(a.actual_events),
            "planned_beats": list(a.planned_beats),
        })
    return jsonify({"arcs": arcs})


@app.route("/api/romance_arcs/scan", methods=["POST"])
def api_romance_arcs_scan():
    """从 character_web.bonds 自动扫描登记主角的感情线。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    try:
        from agents.romance_arc_planner import design_arcs_from_state
        added = design_arcs_from_state(s)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    save_state(s)
    return jsonify({"added": added, "total": len(s.romance_arcs)})


@app.route("/api/line_stage_alignment", methods=["GET"])
def api_line_stage_alignment():
    """叙事线 × 叙事舞台 对齐审计。query 可加 ?volume=N 限定单卷。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    vol = request.args.get("volume", "").strip()
    vol_idx = None
    if vol:
        try:
            vol_idx = int(vol)
        except (TypeError, ValueError):
            return jsonify({"error": f"非法 volume：{vol}"}), 400
    try:
        from agents.line_stage_alignment import analyze_alignment
        return jsonify(analyze_alignment(s, vol_idx))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/cohesion_report", methods=["GET"])
def api_cohesion_report():
    """跨卷连贯性报告：销号角色/空挂物品/承诺挂账。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    try:
        from agents.long_term_cohesion import generate_cohesion_report
        cur_ch = s.current_chapter_index or 0
        if not cur_ch and s.completed_chapters:
            cur_ch = max(c.index for c in s.completed_chapters)
        rep = generate_cohesion_report(s, cur_ch or 1)
        save_state(s)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/clue_registry", methods=["GET"])
def api_clue_registry():
    """伏笔/爽点铺垫/反转线索/红鲱鱼的统一只读视图。"""
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    try:
        from agents.clue_registry import overview
        return jsonify(overview(s))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/atmosphere", methods=["GET"])
def api_atmosphere_get():
    """读全书氛围库——所有 scope 概要。"""
    s = _load()
    if not s or not s.atmosphere_library:
        return jsonify({"scopes": []})
    out = []
    for sc in s.atmosphere_library.scopes:
        out.append({
            "scope_type": sc.scope_type,
            "scope_key": sc.scope_key,
            "label": sc.label,
            "fragments_count": len(sc.fragments),
            "customs_count": len(sc.customs),
            "fragments": [dataclasses.asdict(f) for f in sc.fragments],
            "customs": [dataclasses.asdict(c) for c in sc.customs],
        })
    return jsonify({"scopes": out})


@app.route("/api/atmosphere/design", methods=["POST"])
def api_atmosphere_design():
    """触发为某个 scope 生成氛围库。
    body: {"scope_type": "volume|region|faction", "scope_key": "...", "label": "..."}
    """
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    body = request.get_json(silent=True) or {}
    st_type = (body.get("scope_type") or "").strip()
    st_key = str(body.get("scope_key") or "").strip()
    label = str(body.get("label") or "").strip()
    if st_type not in ("volume", "region", "faction", "general"):
        return jsonify({"error": "scope_type 必须是 volume/region/faction/general"}), 400
    if not st_key:
        return jsonify({"error": "scope_key 不能为空"}), 400

    try:
        from agents.customs_designer import design_atmosphere
        sc = design_atmosphere(s, st_type, st_key, label=label)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    if not sc:
        return jsonify({"error": "生成失败（LLM 无返回）"}), 500

    if s.atmosphere_library is None:
        from persistence.state import AtmosphereLibrary
        s.atmosphere_library = AtmosphereLibrary()
    s.atmosphere_library.upsert(sc)
    save_state(s)
    return jsonify({
        "status": "ok",
        "scope": {
            "scope_type": sc.scope_type,
            "scope_key": sc.scope_key,
            "label": sc.label,
            "fragments_count": len(sc.fragments),
            "customs_count": len(sc.customs),
        },
    })


@app.route("/api/dialogue_audits", methods=["GET"])
def api_dialogue_audits_all():
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    audits = s.dialogue_audits or {}
    items = []
    for idx in sorted(audits.keys()):
        a = audits[idx]
        sev_counts = {"critical": 0, "major": 0, "minor": 0}
        for iss in a.issues:
            sev_counts[iss.severity] = sev_counts.get(iss.severity, 0) + 1
        items.append({
            "chapter_index": idx,
            "overall_score": a.overall_score,
            "subtext_density": a.subtext_density,
            "voice_distinctiveness": a.voice_distinctiveness,
            "infodump_level": a.infodump_level,
            "dialogue_ratio_percent": a.dialogue_ratio_percent,
            "summary": a.summary,
            "issue_count": len(a.issues),
            "sev_counts": sev_counts,
            "ts": a.ts,
        })
    return jsonify({"items": items})


# ═══════════════════════════════════════════════════════
#  按审计结果润色章节（targeted polish）
# ═══════════════════════════════════════════════════════

@app.route("/api/chapter/<int:index>/polish", methods=["POST"])
def api_chapter_polish_stream(index):
    """
    读当前章节正文 + AbilityAudit → 流式吐出润色后的完整章节正文。
    无 audit 或 audit 无 issues → 400（无事可做）。
    服务端只流式返回文本，不保存到磁盘；用户确认后走 /polish/accept 采纳。
    """
    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400

    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404
    if not os.path.exists(path):
        return jsonify({"error": f"章节文件不存在：{path}"}), 404

    audit = (s.ability_audits or {}).get(index)
    if not audit:
        return jsonify({"error": "本章还没审计——先跑审计再润色"}), 400
    if not audit.issues:
        return jsonify({"error": "本章审计无问题，无需润色"}), 400

    with open(path, encoding="utf-8") as f:
        chapter_text = f.read()

    from agents.chapter_polisher import build_polish_messages
    messages = build_polish_messages(s, index, chapter_text, audit)
    if messages is None:
        return jsonify({"error": "无 issue，无需润色"}), 400

    def generate():
        from llm_layer.llm import chat_stream
        try:
            for piece in chat_stream(messages, temperature=0.5, max_tokens=12000):
                data = json.dumps({"type": "delta", "text": piece}, ensure_ascii=False)
                yield f"data: {data}\n\n"
        except Exception as e:
            err = json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False)
            yield f"data: {err}\n\n"
            return
        done = json.dumps({"type": "done"}, ensure_ascii=False)
        yield f"data: {done}\n\n"

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/chapter/<int:index>/polish/accept", methods=["POST"])
def api_chapter_polish_accept(index):
    """
    采纳润色版：版本快照 → 覆盖 chapter txt → 更新 word_count → 自动重跑 audit。
    body: {"text": "..."}
    """
    body = request.get_json(silent=True) or {}
    new_text = body.get("text", "") or ""
    if not new_text.strip():
        return jsonify({"error": "空正文，不能采纳"}), 400

    s = _load()
    if not s:
        return jsonify({"error": "state 未加载"}), 400
    path, vol = _find_chapter_path(s, index)
    if not vol:
        return jsonify({"error": f"找不到第 {index} 章对应的卷"}), 404

    # 快照留后路
    version_control.snapshot(
        s, label=f"before_polish_ch{index}",
        chapter_index=index,
        notes="按审计润色前快照",
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)

    from persistence.state import count_chapter_words
    summary = next((c for c in s.completed_chapters if c.index == index), None)
    if summary:
        summary.word_count = count_chapter_words(new_text)

    # 自动重跑审计——验证 issues 是否被修掉
    new_audit_dict = None
    try:
        from agents.ability_auditor import audit_chapter
        new_audit = audit_chapter(s, index, new_text)
        if new_audit is not None:
            s.ability_audits[index] = new_audit
            new_audit_dict = _audit_to_dict(new_audit)
    except Exception as e:
        print(f"[polish_accept] 重审失败（不影响保存）：{type(e).__name__}: {e}")

    save_state(s)
    return jsonify({
        "status": "ok",
        "chapter_index": index,
        "word_count": len(new_text),
        "new_audit": new_audit_dict,
    })


@app.route("/api/chapter_summaries")
def api_chapter_summaries():
    s = _load()
    return jsonify([_summary_to_dict(c) for c in s.completed_chapters])


# ═══════════════════════════════════════════════════════
#  检查类
# ═══════════════════════════════════════════════════════

@app.route("/api/analyze_intent", methods=["POST"])
def api_analyze_intent():
    """前端专用：分析意图（+ 可选重建 Phase 0 + 可选启动 Phase 1-5 子进程）。

    关键：start_after=True 时不在后端同步跑 Phase 0，直接启动子进程——
    否则后端跑一次 + 子进程再跑一次 = double LLM 调用。
    """
    from agents.intent_analyzer import analyze_intent
    from persistence.checkpoint import mark_phase_done
    s = _load()
    body = request.get_json() or {}
    desc = (body.get("raw_description") or "").strip()
    if not desc:
        abort(400, description="raw_description 不能为空")

    regen_downstream = bool(body.get("regen_downstream", False))
    start_after = bool(body.get("start_after", False))
    pid = project_context.current()

    # 抢锁——防止用户连点两次产生两个并发的 IntentAnalyzer
    with ops_tracker.operation_scope(pid, "意图分析", "启动中") as got_lock:
        if not got_lock:
            return jsonify({"error": ops_tracker.active_op_error_message(pid)}), 409

        # Phase -1：一定跑
        ops_tracker.set_progress(pid, agent="IntentAnalyzer", detail="解析作者意图（LLM 调用中）")
        s.creative_intent.analyzed = False
        try:
            analyze_intent(s, desc)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        mark_phase_done("-1", s)

        regen_result = None
        start_result = None

        if start_after:
            # 交给子进程跑 Phase 0+1+2...
            _save(s, label="intent_analyzed")
            try:
                subprocess_pid = project_manager.start(pid)
                start_result = f"✓ 已启动完整流水线（PID={subprocess_pid}）"
                if regen_downstream:
                    regen_result = "→ 立项重建已纳入子进程"
            except Exception as e:
                start_result = f"⚠ 启动失败：{e}"
        else:
            if regen_downstream:
                try:
                    from agents.concept_pitch import design_concept_phase
                    ops_tracker.set_progress(pid, agent="ConceptPitch", detail="重建立项（pitch+tropes+tone）")
                    design_concept_phase(s)
                    mark_phase_done("0", s)
                    regen_result = "pitch + tropes + tone 已按新意图重建"
                except Exception as e:
                    regen_result = f"重建失败：{e}"
            _save(s, label="intent_analyzed")

        return jsonify({
            "status": "ok",
            "creative_intent": s.creative_intent.__dict__,
            "regen_downstream": regen_result,
            "start_after": start_result,
        })


@app.route("/api/reanalyze_intent", methods=["POST"])
def api_reanalyze_intent():
    """
    重新分析意图 + 清空下游所有数据 + 启动写作。

    彻底的"推倒重来"操作——保留：
      · title / genre / theme 元信息
      · 用户提供的新意图文本
    清空：
      · 所有 Phase 0+ 的生成数据（世界/人物/卷/情节/章节）
      · 磁盘上所有 chapter_*.txt
      · progress.json 里的 phases/chapters 标记

    幂等性：允许多次调用（前一次还没完成也能再次触发）。
    """
    import glob as _glob
    import shutil
    from persistence.checkpoint import save_state, _save_progress

    body = request.get_json() or {}
    desc = (body.get("raw_description") or "").strip()
    if not desc:
        abort(400, description="raw_description 不能为空")

    pid = project_context.current()

    with ops_tracker.operation_scope(pid, "重新分析并重建", "清空旧数据") as got_lock:
        if not got_lock:
            return jsonify({"error": ops_tracker.active_op_error_message(pid)}), 409

        # ── Step 1: 如有子进程在跑，先停掉 ──
        try:
            if project_manager.status(pid) in ("running", "paused"):
                ops_tracker.set_progress(pid, agent="reset", detail="停止正在运行的子进程")
                print(f"  [reanalyze] 停止现有子进程...")
                project_manager.stop(pid, grace_seconds=5.0)
        except Exception as e:
            print(f"  [!] 停止子进程失败：{e}（继续）")

        # ── Step 2: 清磁盘上的章节文件 ──
        ops_tracker.set_progress(pid, agent="reset", detail="删除已生成的章节文件")
        project_root = os.path.abspath(project_context.project_dir())
        deleted_files = 0
        for p in _glob.glob(os.path.join(project_root, "vol*", "chapter_*.txt")):
            try:
                os.remove(p)
                deleted_files += 1
            except OSError:
                pass
        # 删空的 vol 目录
        for d in _glob.glob(os.path.join(project_root, "vol*")):
            if os.path.isdir(d) and not os.listdir(d):
                try: os.rmdir(d)
                except OSError: pass

        # ── Step 3: 清 state/ 目录（分文件存储）──
        ops_tracker.set_progress(pid, agent="reset", detail="清空 state section 文件")
        from persistence import state_storage
        state_dir = state_storage.state_dir()
        # 删除所有 section 文件（包括 meta.json）——下面会重新写
        deleted_sections = 0
        if os.path.isdir(state_dir):
            for f in os.listdir(state_dir):
                fp = os.path.join(state_dir, f)
                if os.path.isfile(fp):
                    try:
                        os.remove(fp)
                        deleted_sections += 1
                    except OSError:
                        pass

        # ── Step 4: 清老的 state.json + progress.json ──
        legacy_state = project_context.state_file()
        if os.path.exists(legacy_state):
            try: os.remove(legacy_state)
            except OSError: pass

        # progress.json 清零
        _save_progress({"phases": [], "chapters": []})

        # ── Step 5: 构造全新 state——保留元信息 + 新意图 ──
        ops_tracker.set_progress(pid, agent="reset", detail="初始化新 state")
        # 从 meta.json（project-level）读元信息
        from project_mgmt.project_manager import _read_meta
        meta = _read_meta(pid)
        from persistence.state import NovelState, CreativeIntent
        fresh = NovelState(
            title=meta.get("title", pid),
            genre=meta.get("genre", ""),
            theme=meta.get("theme", ""),
        )
        fresh.creative_intent = CreativeIntent(
            raw_description=desc,
            analyzed=False,
        )
        save_state(fresh)

        # ── Step 6: 重新分析意图 ──
        ops_tracker.set_progress(pid, agent="IntentAnalyzer", detail="解析新意图")
        from agents.intent_analyzer import analyze_intent
        from persistence.checkpoint import load_state, mark_phase_done
        s = load_state()
        try:
            analyze_intent(s, desc)
            mark_phase_done("-1", s)  # Phase -1 done，子进程不会重复跑
        except Exception as e:
            return jsonify({"error": f"意图分析失败：{e}"}), 500

        # ── Step 7: 启动子进程跑 Phase 0 起所有阶段 ──
        ops_tracker.set_progress(pid, agent="director", detail="启动完整写作流水线")
        start_result = None
        try:
            subprocess_pid = project_manager.start(pid)
            start_result = f"✓ 已启动完整流水线（PID={subprocess_pid}）——Phase 0 起全部重跑"
        except Exception as e:
            start_result = f"⚠ 启动失败：{e}"

        _save(s, label="reanalyzed_reset")

        return jsonify({
            "status": "ok",
            "creative_intent": _to_json(s.creative_intent),
            "reset_summary": f"清空 {deleted_files} 章节文件 + {deleted_sections} 个 state 文件",
            "start_result": start_result,
        })


@app.route("/api/refine_intent", methods=["POST"])
def api_refine_intent():
    """
    在已有 creative_intent 上追加一段补充描述并重分析。
    不覆盖旧内容——旧描述 + 新补充拼接后重跑 IntentAnalyzer。

    cascade_level：
      "light"   - 只更新 creative_intent 本身（下次生成时生效）
      "phase0"  - 同时重建立项三件套（pitch/tropes/tone，会覆盖）
      "full"    - **增量精炼**：在现有 Phase 1-3（世界/体系/势力/人物/关系/卷）基础上按新意图增量修改，不清空，不重跑。
                  会追加新元素、微调已有字段、保留已生成的内容。
    """
    from agents.intent_analyzer import refine_intent
    s = _load()
    body = request.get_json() or {}
    addition = (body.get("addition") or "").strip()
    if not addition:
        abort(400, description="addition 不能为空")
    if not s.creative_intent.analyzed:
        return jsonify({"error": "请先完成首次意图分析，再使用追加功能"}), 400

    # 向后兼容：旧字段 regen_downstream=True 映射到 cascade_level="phase0"
    cascade_level = body.get("cascade_level")
    if not cascade_level:
        cascade_level = "phase0" if body.get("regen_downstream") else "light"
    cascade_level = str(cascade_level).lower()

    pid = project_context.current()
    with ops_tracker.operation_scope(pid, f"追加意图·{cascade_level}", "准备中") as got_lock:
        if not got_lock:
            return jsonify({"error": ops_tracker.active_op_error_message(pid)}), 409

        ops_tracker.set_progress(pid, agent="IntentRefiner", detail=f"重分析意图（第 {len(s.creative_intent.revisions)+1} 轮）")
        try:
            refine_intent(s, addition)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        phase0_result = None
        cascade_result = None

        if cascade_level in ("phase0", "full"):
            try:
                from agents.concept_pitch import design_concept_phase
                ops_tracker.set_progress(pid, agent="ConceptPitch", detail="重建立项三件套")
                design_concept_phase(s)
                phase0_result = "✓ 立项三件套（pitch/tropes/tone）已按新意图重建"
            except Exception as e:
                phase0_result = f"⚠ 立项重建失败：{e}"

        if cascade_level == "full":
            try:
                from agents.intent_refiner import cascade_refine_all
                only = body.get("only_sections") or None
                ops_tracker.set_progress(pid, agent="IntentRefiner", detail="增量精炼 9 个下游模块（逐模块 LLM 调用）")
                refine_results = cascade_refine_all(s, addition, only_sections=only, progress_hook=lambda section, i, total: ops_tracker.set_progress(pid, agent="IntentRefiner", detail=f"精炼 {section} ({i}/{total})"))
                ok_count = sum(1 for v in refine_results.values() if v is True)
                skip_count = sum(1 for v in refine_results.values() if v == "skipped")
                fail_count = sum(1 for v in refine_results.values() if v is False)
                cascade_result = {
                    "summary": f"✓ 增量精炼：{ok_count} 模块更新｜{fail_count} 未变｜{skip_count} 跳过",
                    "details": refine_results,
                }
            except Exception as e:
                cascade_result = {"summary": f"⚠ 增量精炼失败：{e}", "details": {}}

        _save(s, label=f"intent_refined_r{len(s.creative_intent.revisions)}_{cascade_level}")
        return jsonify({
            "status": "ok",
            "creative_intent": _to_json(s.creative_intent),
            "cascade_level": cascade_level,
            "phase0_result": phase0_result,
            "cascade_result": cascade_result,
            "regen_downstream": phase0_result,
        })


@app.route("/api/validate/<section>")
def api_validate_section(section):
    """手动触发 section 合规验证，只返回问题列表不重生。"""
    try:
        from utils import validators
    except Exception:
        return jsonify({"error": "validators 模块加载失败"}), 500
    s = _load()
    issues = validators.validate_section(s, section)
    return jsonify({
        "section": section,
        "ok": len(issues) == 0,
        "issues": issues,
    })


@app.route("/api/invariants")
def api_invariants():
    s = _load()
    issues = invariants.check_all(s)
    return jsonify({"issues": issues})


@app.route("/api/stage_review_reports", methods=["GET"])
def api_stage_review_reports():
    """
    列出 stage 级审查报告。
    可选 query：volume=N  → 只返回该卷的 stage 报告
    返回格式：{"reports": [{"stage_id", "volume", "stage_name", "issues":[...]}]}
    """
    from dataclasses import asdict
    s = _load()
    vol_filter = request.args.get("volume", type=int)
    stage_by_id = {st.stage_id: st for st in s.story_stages}
    reports = []
    for sid, issues in (s.stage_review_reports or {}).items():
        st = stage_by_id.get(sid)
        if vol_filter is not None and (not st or st.volume != vol_filter):
            continue
        reports.append({
            "stage_id": sid,
            "stage_name": st.name if st else "",
            "volume": st.volume if st else None,
            "chapter_start": st.chapter_start if st else None,
            "chapter_end": st.chapter_end if st else None,
            "structure_role": st.structure_role if st else "",
            "passed": sid in (s.done_stage_ids or []),
            "issues": [asdict(i) for i in (issues or [])],
        })
    reports.sort(key=lambda r: ((r["volume"] or 0), r["chapter_start"] or 0))
    return jsonify({"reports": reports})


@app.route("/api/volume_review_reports", methods=["GET"])
def api_volume_review_reports():
    """
    列出卷级审查报告。
    可选 query：volume=N  → 只返回该卷
    """
    from dataclasses import asdict
    s = _load()
    vol_filter = request.args.get("volume", type=int)
    reports = []
    for vi, issues in (s.volume_review_reports or {}).items():
        if vol_filter is not None and vi != vol_filter:
            continue
        v = s.get_volume(vi)
        reports.append({
            "volume": vi,
            "title": v.title if v else "",
            "passed": vi in (s.done_volume_review_indices or []),
            "issues": [asdict(i) for i in (issues or [])],
        })
    reports.sort(key=lambda r: r["volume"])
    return jsonify({"reports": reports})


@app.route("/api/review/stage/<stage_id>", methods=["POST"])
def api_review_stage_run(stage_id):
    """
    手动触发某个 stage 的再审查。仅出报告，不执行重写。
    报告写回 state.stage_review_reports[stage_id] 并持久化。
    """
    from dataclasses import asdict
    from agents.stage_reviewer import review_stage as _review_stage
    s = _load()
    st = next((x for x in s.story_stages if x.stage_id == stage_id), None)
    if not st:
        return jsonify({"error": f"stage {stage_id} 不存在"}), 404
    issues = _review_stage(s, st.volume, stage_id, iteration=0)
    s.stage_review_reports[stage_id] = list(issues)
    save_state(s)
    return jsonify({
        "stage_id": stage_id,
        "volume": st.volume,
        "issues": [asdict(i) for i in issues],
    })


@app.route("/api/review/volume/<int:volume_index>", methods=["POST"])
def api_review_volume_run(volume_index):
    """
    手动触发某卷的再审查。仅出报告，不执行重写。
    """
    from dataclasses import asdict
    from agents.volume_reviewer import review_volume as _review_volume
    s = _load()
    v = s.get_volume(volume_index)
    if not v:
        return jsonify({"error": f"卷 {volume_index} 不存在"}), 404
    issues = _review_volume(s, volume_index, iteration=0)
    s.volume_review_reports[volume_index] = list(issues)
    save_state(s)
    return jsonify({
        "volume": volume_index,
        "issues": [asdict(i) for i in issues],
    })


@app.route("/api/chapter_inspirations", methods=["GET"])
def api_chapter_inspirations_list():
    """列出所有章节灵感，附带每章是否已写（前端决定按钮文案）。"""
    s = _load()
    ins = getattr(s, "chapter_inspirations", {}) or {}
    written = {c.index for c in s.completed_chapters}
    return jsonify({
        "entries": sorted(
            [
                {
                    "chapter_index": int(k),
                    "text": v,
                    "is_written": int(k) in written,
                }
                for k, v in ins.items()
            ],
            key=lambda x: x["chapter_index"],
        ),
        "written_chapters": sorted(written),
    })


@app.route("/api/chapter_inspiration/<int:chapter_index>", methods=["GET"])
def api_chapter_inspiration_get(chapter_index):
    s = _load()
    ins = getattr(s, "chapter_inspirations", {}) or {}
    return jsonify({
        "chapter_index": chapter_index,
        "text": ins.get(chapter_index, ""),
    })


@app.route("/api/chapter_inspiration/<int:chapter_index>", methods=["POST", "PUT"])
def api_chapter_inspiration_save(chapter_index):
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    s = _load()
    if not hasattr(s, "chapter_inspirations") or s.chapter_inspirations is None:
        s.chapter_inspirations = {}
    if text:
        s.chapter_inspirations[chapter_index] = text
    else:
        # 空字符串 = 删除
        s.chapter_inspirations.pop(chapter_index, None)
    _save(s, label=f"inspiration_ch{chapter_index}")
    return jsonify({
        "status": "ok",
        "chapter_index": chapter_index,
        "text": text,
    })


@app.route("/api/chapter_inspiration/<int:chapter_index>", methods=["DELETE"])
def api_chapter_inspiration_delete(chapter_index):
    s = _load()
    if hasattr(s, "chapter_inspirations") and s.chapter_inspirations:
        s.chapter_inspirations.pop(chapter_index, None)
    _save(s, label=f"inspiration_del_ch{chapter_index}")
    return jsonify({"status": "ok", "chapter_index": chapter_index})


@app.route("/api/chapter_inspiration/<int:chapter_index>/validate", methods=["POST"])
def api_chapter_inspiration_validate(chapter_index):
    """对本章灵感做合规预检——不写盘、不阻塞保存，仅返回验证结果。

    前端用法：保存灵感后异步调本接口，拿到 validation 弹提示。
    body: {"text": "..."}  —— 可传外部文本验证（不一定要先保存）；
          省略则读 state 里已保存的灵感。
    """
    body = request.get_json(silent=True) or {}
    s = _load()
    text = body.get("text")
    if text is None:
        ins = getattr(s, "chapter_inspirations", {}) or {}
        text = ins.get(chapter_index, "")
    text = (text or "").strip()
    try:
        from agents.inspiration_validator import validate_inspiration
        result = validate_inspiration(s, chapter_index, text)
        return jsonify({
            "status": "ok",
            "chapter_index": chapter_index,
            "validation": result.to_dict(),
        })
    except Exception as e:
        return jsonify({
            "status": "ok",  # 验证失败不阻塞，前端仍可继续
            "chapter_index": chapter_index,
            "validation": {
                "ok": False,
                "has_issues": False,
                "issues": [],
                "suggested_rewrite": "",
                "summary": f"验证调用失败：{type(e).__name__}",
            },
        })


@app.route("/api/prompts", methods=["GET"])
def api_prompts_list():
    """列出所有已注册的系统提示词（按分类）。"""
    from utils import prompts_registry as pr
    entries = pr.all_entries()
    # 按注册顺序保留，同时给前端分组
    grouped: dict[str, list[dict]] = {}
    for e in entries:
        grouped.setdefault(e.category, []).append({
            "id": e.id,
            "label": e.label,
            "description": e.description,
            "module": e.module,
            "attr": e.attr,
            "overridden": e.overridden,
            "body_preview": (e.current or "")[:120],
        })
    return jsonify({
        "categories": pr.categories(),
        "grouped": grouped,
        "total": len(entries),
        "overridden_count": sum(1 for e in entries if e.overridden),
    })


@app.route("/api/prompts/<path:prompt_id>", methods=["GET"])
def api_prompt_get(prompt_id):
    """读取单个 prompt 的完整内容（含 default 与 current）。"""
    from utils import prompts_registry as pr
    entry = pr.get_entry(prompt_id)
    if not entry:
        return jsonify({"error": f"未注册的 prompt_id: {prompt_id}"}), 404
    return jsonify({
        "id": entry.id,
        "label": entry.label,
        "category": entry.category,
        "description": entry.description,
        "module": entry.module,
        "attr": entry.attr,
        "current": entry.current,
        "default": entry.default,
        "overridden": entry.overridden,
    })


@app.route("/api/prompts/<path:prompt_id>", methods=["POST", "PUT"])
def api_prompt_save(prompt_id):
    """保存 prompt 覆盖。body={"text": "..."}；空串/等于默认会清除覆盖。"""
    from utils import prompts_registry as pr
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    try:
        entry = pr.save_override(prompt_id, text)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400
    return jsonify({
        "status": "ok",
        "id": entry.id,
        "overridden": entry.overridden,
        "current_length": len(entry.current),
    })


@app.route("/api/prompts/<path:prompt_id>", methods=["DELETE"])
def api_prompt_delete(prompt_id):
    """删除 override，恢复到代码默认值。"""
    from utils import prompts_registry as pr
    try:
        entry = pr.delete_override(prompt_id)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400
    return jsonify({"status": "ok", "id": entry.id, "overridden": entry.overridden})


@app.route("/api/state_audit")
def api_state_audit():
    """
    全项目状态审计：每个 section 是否已生成 / 部分 / 缺失。
    前端顶栏用它显示"⚠ 有 N 个模块未生成"banner。
    """
    try:
        from persistence.state_audit import audit_state
    except Exception as e:
        return jsonify({"error": f"state_audit 模块加载失败：{e}"}), 500
    s = _load()
    return jsonify(audit_state(s))


# audit section key → regen action name（供前端复盘"修复"按钮用）
_AUDIT_SECTION_TO_ACTION: dict[str, str] = {
    "master_outline":  "master_outline",
    "power_system":    "power_system",
    "volumes":         "volumes",
    "factions":        "factions",
    "world_setting":   "world",
    "geography":       "geography",
    "timeline":        "timeline",
    "economy":         "economy",
    "characters":      "characters",
    "lines":           "lines",
    "satisfaction":    "satisfaction",
    "foreshadows":     "foreshadows",
    "twists":          "twists",
    "stages":          "stages",
    # concept_pitch / creative_intent 不自动修复——它们是用户输入源头，需手动编辑
}


@app.route("/api/state_audit/actions")
def api_state_audit_actions():
    """返回 audit 每个 section 可用的修复动作名，前端按需显示按钮。"""
    return jsonify(_AUDIT_SECTION_TO_ACTION)


@app.route("/api/state_audit/fix/<section>", methods=["POST"])
def api_state_audit_fix(section):
    """
    按 audit 的 section key 触发对应重建。
    注意：这调用对应的 regen_* 函数（清空现有数据后重生成），并会打快照。
    """
    action = _AUDIT_SECTION_TO_ACTION.get(section)
    if not action:
        return jsonify({
            "error": f"section '{section}' 没有自动修复动作；请人工编辑或用相关的 regen",
        }), 400
    if action not in regen_mod.REGEN_ACTIONS:
        return jsonify({
            "error": f"regen 动作 '{action}' 未注册，内部错误",
        }), 500
    try:
        result = regen_mod.REGEN_ACTIONS[action]()
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    # 修复完返回最新审计 + 本次修复结果
    from persistence.state_audit import audit_state
    s = _load()
    return jsonify({
        "status": "ok",
        "section": section,
        "action": action,
        "result": result,
        "audit": audit_state(s),
    })


@app.route("/api/state_audit/fix_all", methods=["POST"])
def api_state_audit_fix_all():
    """
    一键复盘：对所有 status == 'empty' 或 'partial' 的 section 依次调用对应 regen。
    按预定义依赖顺序（power_system → volumes → factions → world → ...）执行。
    返回每个 section 的修复结果（含失败原因）。
    """
    from persistence.state_audit import audit_state
    s = _load()
    report = audit_state(s)
    # 依赖顺序——上游先修（这些键的顺序重要）
    ORDER = [
        "master_outline", "power_system", "volumes", "factions",
        "world_setting", "geography", "timeline", "economy",
        "characters", "lines", "satisfaction", "foreshadows", "twists", "stages",
    ]
    by_key = {sec["key"]: sec for sec in report.get("sections", [])}
    only = request.args.getlist("only")  # 可选：?only=twists&only=factions 只修指定
    results = []
    for key in ORDER:
        sec = by_key.get(key)
        if not sec:
            continue
        if only and key not in only:
            continue
        if sec["status"] == "ok":
            continue
        action = _AUDIT_SECTION_TO_ACTION.get(key)
        if not action or action not in regen_mod.REGEN_ACTIONS:
            results.append({"section": key, "status": "skip", "reason": "no auto-fix action"})
            continue
        try:
            r = regen_mod.REGEN_ACTIONS[action]()
            results.append({"section": key, "status": "fixed", "result": r})
        except Exception as e:
            results.append({"section": key, "status": "failed", "error": f"{type(e).__name__}: {e}"})
    # 修完再 audit 一次
    s2 = _load()
    return jsonify({
        "results": results,
        "audit": audit_state(s2),
    })


# ═══════════════════════════════════════════════════════
#  dict 转换辅助
# ═══════════════════════════════════════════════════════

def _replace_dataclass(obj, d: dict):
    for k, v in d.items():
        if hasattr(obj, k):
            setattr(obj, k, v)


def _dump_creative_intent_for_api(ci):
    """把 CreativeIntent 序列化成 API 友好的 dict（plot_supplements 展开为 list[dict]）。"""
    out = dict(ci.__dict__)
    out["plot_supplements"] = [
        {
            "name": p.name, "what": p.what, "why_engaging": p.why_engaging,
            "where_to_inject": p.where_to_inject, "intensity": p.intensity,
            "adopted": p.adopted, "notes": p.notes,
        }
        for p in (ci.plot_supplements or [])
    ]
    # revisions 同样展开（如果有的话）—— IntentRevision 是 dataclass
    out["revisions"] = [r.__dict__ if hasattr(r, "__dict__") else r for r in (ci.revisions or [])]
    return out


def _replace_creative_intent(ci, d: dict):
    """前端 PUT creative_intent 时——把 plot_supplements 的 dict 转回 PlotSupplement。"""
    from persistence.state import PlotSupplement
    for k, v in d.items():
        if not hasattr(ci, k):
            continue
        if k == "plot_supplements" and isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, dict):
                    new_list.append(PlotSupplement(
                        name=item.get("name", ""),
                        what=item.get("what", ""),
                        why_engaging=item.get("why_engaging", ""),
                        where_to_inject=item.get("where_to_inject", ""),
                        intensity=item.get("intensity", "mid"),
                        adopted=item.get("adopted", None),
                        notes=item.get("notes", ""),
                    ))
                elif isinstance(item, PlotSupplement):
                    new_list.append(item)
            ci.plot_supplements = new_list
        else:
            setattr(ci, k, v)


def _replace_characters(state, d):
    """只更新 name 匹配的已有角色，不增不删。"""
    if not isinstance(d, list):
        return
    by_name = {c.name: c for c in state.characters}
    for entry in d:
        name = entry.get("name")
        if name and name in by_name:
            c = by_name[name]
            editable = [
                "age_desc", "appearance", "personality", "personality_detail",
                "background", "trauma", "desire", "fear", "speech_pattern",
                "ability", "realm", "arc", "motivation", "fatal_flaw",
                "signature_mannerisms", "verbal_tics", "sensory_signature",
                "default_stress_response", "defining_memory", "secret_desire",
                "contrast_with_protagonist",
                "high_freq_vocab", "speech_taboo",
                "speech_under_anger", "speech_under_fear", "speech_under_joy",
                "sentence_length_preference",
            ]
            for k in editable:
                if k in entry:
                    setattr(c, k, entry[k])


def _replace_volumes(state, d):
    if not isinstance(d, list):
        return
    by_idx = {v.index: v for v in state.volumes}
    for entry in d:
        idx = entry.get("index")
        if idx in by_idx:
            v = by_idx[idx]
            for k in ("title", "theme", "arc", "structure_role", "purpose",
                      "expression", "opening_hook", "closing_hook",
                      "volume_antagonist", "key_events"):
                if k in entry:
                    setattr(v, k, entry[k])


def _replace_glossary(state, d):
    from persistence.state import GlossaryEntry
    if not isinstance(d, list):
        return
    new_list = []
    for e in d:
        if not isinstance(e, dict) or not e.get("term"):
            continue
        new_list.append(GlossaryEntry(
            term=e["term"],
            category=e.get("category", "其他"),
            definition=e.get("definition", ""),
            first_appeared_chapter=int(e.get("first_appeared_chapter", 0)),
            aliases=e.get("aliases", []),
        ))
    state.glossary = new_list


def _dump_power_system(s):
    ps = s.power_system
    if not ps:
        return {}
    return {
        "system_name": ps.system_name,
        "system_description": ps.system_description,
        "realms": [regen_mod._realm_to_dict(r) for r in ps.realms],
        "special_abilities": [regen_mod._ability_to_dict(a) for a in ps.special_abilities],
        "cultivation_resources": ps.cultivation_resources,
        "protagonist_realm_plan": ps.protagonist_realm_plan,
    }


def _replace_power_system(state, d: dict):
    """允许前端整体 PUT power_system——支持改 special_abilities 字段（用户改能力名）。
    只更新 d 中明确出现的字段；缺失字段保留原值，避免前端只发一部分时把别的字段清空。
    """
    from persistence.state import (
        PowerSystem, Realm, SpecialAbility, AbilityAwakeningStage,
    )
    ps = state.power_system
    setattr(state, "_explicit_power_system_put", True)
    if ps is None:
        ps = PowerSystem(system_name="", system_description="", realms=[])
        state.power_system = ps
    if "system_name" in d:        ps.system_name = d["system_name"] or ""
    if "system_description" in d: ps.system_description = d["system_description"] or ""
    if "cultivation_resources" in d: ps.cultivation_resources = d["cultivation_resources"] or []
    if "protagonist_realm_plan" in d:
        try:
            ps.protagonist_realm_plan = {int(k): v for k, v in (d["protagonist_realm_plan"] or {}).items()}
        except (ValueError, TypeError):
            pass

    # realms 只在 d 包含 realms 且非空 list 时才替换；缺失/空 list 时保留原 realms
    # 用 dataclass.replace 模式：拿原 realm 做模板，只更新前端真传来的字段
    if "realms" in d and isinstance(d["realms"], list):
        # 旧 realms 按 index 索引，方便部分更新
        old_by_idx = {r.index: r for r in (ps.realms or [])}
        new_realms = []
        for ri, raw in enumerate(d["realms"]):
            if not isinstance(raw, dict):
                continue
            try:
                idx = int(raw.get("index", ri + 1))
            except (ValueError, TypeError):
                idx = ri + 1
            old = old_by_idx.get(idx)
            if old:
                # 部分更新：只用前端传来的字段覆盖
                for k, v in raw.items():
                    if hasattr(old, k):
                        try: setattr(old, k, v)
                        except Exception: pass
                new_realms.append(old)
            else:
                # 新建——只用 dataclass 实际有的字段，避免 TypeError
                kwargs = {"index": idx, "name": raw.get("name", "") or ""}
                for k in ("sub_realms", "power_description", "breakthrough_condition",
                          "resource_requirement", "average_time", "rarity",
                          "combat_capability", "lifespan", "consciousness_range",
                          "mana_capacity", "overleap_rule", "specific_examples"):
                    if k in raw:
                        kwargs[k] = raw[k] or ("" if k != "sub_realms" and k != "specific_examples" else [])
                # required 字段缺失时给空默认值
                for k in ("sub_realms", "power_description", "breakthrough_condition",
                          "resource_requirement", "average_time", "rarity"):
                    if k not in kwargs:
                        kwargs[k] = "" if k not in ("sub_realms", "specific_examples") else []
                try:
                    new_realms.append(Realm(**kwargs))
                except TypeError as e:
                    print(f"  ⚠ realm 创建失败（字段不匹配）：{e}（跳过该 realm）")
        ps.realms = new_realms

    if "special_abilities" in d and isinstance(d["special_abilities"], list):
        from persistence.state import LifecycleNode
        # 按 name 索引旧 ability——前端 PUT 时不传的字段必须从旧对象保留，
        # 不能用 dataclass 默认值（空 list / 空 str）静默覆盖。
        # 历史教训：前端表单不渲染 lifecycle_nodes / entry_kind，PUT 一次就被清零，
        # 导致 ability_planner 的节点分级保护全失效——污染下游 outline / writer。
        old_by_name = {old.name: old for old in (ps.special_abilities or [])}
        new_abs = []
        for a in d["special_abilities"]:
            if not isinstance(a, dict):
                continue
            name = a.get("name", "") or ""
            old = old_by_name.get(name)

            # awakening_stages：前端传了 list 才覆盖；缺失则保留旧的
            if "awakening_stages" in a and isinstance(a["awakening_stages"], list):
                stages = []
                for st in a["awakening_stages"]:
                    if not isinstance(st, dict):
                        continue
                    try:
                        stages.append(AbilityAwakeningStage(
                            stage_index=int(st.get("stage_index", len(stages) + 1) or len(stages) + 1),
                            stage_name=st.get("stage_name", "") or "",
                            target_volume=int(st.get("target_volume", 1) or 1),
                            triggering_event=st.get("triggering_event", "") or "",
                            new_power=st.get("new_power", "") or "",
                            cost_or_risk=st.get("cost_or_risk", "") or "",
                        ))
                    except (ValueError, TypeError):
                        continue
            else:
                stages = list(old.awakening_stages) if old else []

            # lifecycle_nodes：**只在前端发非空 list 时才覆盖**——
            # 历史教训：前端 form 编辑 ability（如改 external_llm_profile）时，
            # 会把整个 ability dict 原样回发（含从 GET 拿到的 lifecycle_nodes 字段）。
            # 第一版修复用 `"lifecycle_nodes" in a` 判断"用户显式想替换"，
            # 但前端永远把这字段带回来——即便它是空（极少 form 暴露 lifecycle 编辑）。
            # 实际上 lifecycle_nodes 是 ability_roadmap_planner 自动产物，
            # 用户在 UI 没有"显式清空"的合理场景。所以：
            #   · 前端发非空 list → 覆盖（用户真的有手动编辑能力）
            #   · 前端发空 list / 缺失 → 保留旧值（避免静默清零）
            incoming_nodes = a.get("lifecycle_nodes") if isinstance(a.get("lifecycle_nodes"), list) else None
            if incoming_nodes:  # 非空 list 才覆盖
                nodes = []
                for n in incoming_nodes:
                    if not isinstance(n, dict):
                        continue
                    try:
                        nodes.append(LifecycleNode(
                            node_type=str(n.get("node_type") or "acquired"),
                            target_volume=int(n.get("target_volume", 1) or 1),
                            target_chapter=int(n.get("target_chapter", 0) or 0),
                            prerequisites=str(n.get("prerequisites") or "")[:200],
                            narrative_purpose=str(n.get("narrative_purpose") or "")[:200],
                            is_dramatic=bool(n.get("is_dramatic", False)),
                            linked_sp_id=str(n.get("linked_sp_id") or ""),
                        ))
                    except (ValueError, TypeError):
                        continue
            else:
                # 空 list 或缺失字段——保留旧 lifecycle_nodes（防静默清零）
                nodes = list(old.lifecycle_nodes) if old else []

            # 其他字符串/布尔字段——传了就用，没传保留旧值（或空默认）
            def _pick(field, default=""):
                if field in a:
                    return a[field] or default
                return getattr(old, field, default) if old else default

            try:
                new_abs.append(SpecialAbility(
                    name=name,
                    source=_pick("source"),
                    description=_pick("description"),
                    unlock_condition=_pick("unlock_condition"),
                    usage_rule=_pick("usage_rule"),
                    effect_scope=_pick("effect_scope"),
                    hard_limits=_pick("hard_limits"),
                    cost_rule=_pick("cost_rule"),
                    holder_role=_pick("holder_role"),
                    holder_name=_pick("holder_name"),
                    is_protagonist_signature=bool(_pick("is_protagonist_signature", False)),
                    entry_kind=_pick("entry_kind", "ability"),
                    awakening_stages=stages,
                    lifecycle_nodes=nodes,
                    plot_integration=_pick("plot_integration"),
                    narrative_hook=_pick("narrative_hook"),
                    external_llm_profile=_pick("external_llm_profile"),
                ))
            except TypeError as e:
                print(f"  ⚠ ability 创建失败：{e}（跳过该 ability）")
        ps.special_abilities = new_abs
    save_state(state)
    return ps


def _dump_factions(s):
    return [f.to_dict() for f in s.factions]


def _line_to_dict(l):
    return {
        "line_id": l.line_id,
        "type": l.line_type.value,
        "scope": l.scope.value,
        "name": l.name,
        "description": l.description,
        "characters": l.characters,
        "volume_range": list(l.volume_range),
        "current_phase": l.current_phase,
        "resolved": l.resolved,
        "phases": [p.__dict__ for p in l.phases],
    }


def _sp_to_dict(sp):
    return {
        "sp_id": sp.sp_id,
        "sp_type": sp.sp_type.value,
        "title": sp.title,
        "description": sp.description,
        "intensity": sp.intensity,
        "volume": sp.volume,
        "target_chapter": sp.target_chapter,
        "setup_chain": [s.__dict__ for s in sp.setup_chain],
        "payoff_description": sp.payoff_description,
        "triggered": sp.triggered,
        "actual_chapter": sp.actual_chapter,
    }


def _fw_to_dict(fw):
    return {
        "fw_id": fw.fw_id,
        "content": fw.content,
        "hidden_meaning": fw.hidden_meaning,
        "importance": fw.importance.value,
        "planted_chapter": fw.planted_chapter,
        "planned_resolve_volume": fw.planned_resolve_volume,
        "planned_resolve_chapter": fw.planned_resolve_chapter,
        "resolution_description": fw.resolution_description,
        "resolved": fw.resolved,
        "actual_resolve_chapter": fw.actual_resolve_chapter,
        "activation_chapter": fw.activation_chapter,
        "activation_sign": fw.activation_sign,
        "resolution_quality": fw.resolution_quality,
    }


def _rhythm_to_dict(p):
    return {
        "volume": p.volume_index,
        "overall_pattern": p.overall_pattern,
        "segments": [{"start": s.chapter_start, "end": s.chapter_end,
                       "type": s.rhythm_type.value, "pace": s.word_pace,
                       "description": s.description} for s in p.segments],
        "breathing_chapters": p.breathing_chapters,
        "climax_chapters": p.climax_chapters,
    }


def _stage_to_dict(s):
    return {
        "stage_id": s.stage_id, "name": s.name, "stage_type": s.stage_type,
        "volume": s.volume, "chapter_start": s.chapter_start, "chapter_end": s.chapter_end,
        "structure_role": s.structure_role,
        "atmosphere": s.atmosphere, "protagonist_role": s.protagonist_role,
        "purpose": s.purpose, "expression": s.expression,
        "sub_scenes": [ss.__dict__ for ss in s.sub_scenes],
    }


def _ctp_to_dict(p):
    return {
        "volume": p.volume,
        "type_distribution": p.type_distribution,
        "per_chapter": [a.__dict__ for a in p.per_chapter],
    }


def _arc_to_dict(a):
    return {
        "character_name": a.character_name,
        "theme": a.theme,
        "start_state": a.start_state,
        "end_state": a.end_state,
        "transitions": [t.__dict__ for t in a.transitions],
    }


def _journey_to_dict(j):
    return {
        "overall_theme": j.overall_theme,
        "core_wound": j.core_wound,
        "true_goal": j.true_goal,
        "fatal_flaw": j.fatal_flaw,
        "central_conflict": j.central_conflict,
        "growth_arc": j.growth_arc,
        "milestones": [m.__dict__ for m in j.milestones],
        "stage_beats": [b.__dict__ for b in j.stage_beats],
    }


def _twist_system_to_dict(ts):
    if ts is None:
        return {"chains": [], "design_principle": "", "reader_experience_curve": ""}
    return {
        "design_principle": ts.design_principle,
        "reader_experience_curve": ts.reader_experience_curve,
        "chains": [
            {
                "chain_id": c.chain_id,
                "title": c.title,
                "category": c.category,
                "initial_setup": c.initial_setup,
                "target_layers": c.target_layers,
                "difficulty": c.difficulty,
                "scope": c.scope,
                "anchor_volume": c.anchor_volume,
                "volume_span": c.volume_span,
                "involved_characters": c.involved_characters,
                "involved_factions": c.involved_factions,
                "design_rationale": c.design_rationale,
                "linked_foreshadow_ids": c.linked_foreshadow_ids,
                "layers": [l.__dict__ for l in c.layers],
            }
            for c in ts.chains
        ],
    }


def _summary_to_dict(c):
    # Batch 2/3/5 新字段全部 dump,前端可显示 callback / hook_type / 模拟评论
    comments = []
    for sc in getattr(c, "simulated_comments", None) or []:
        comments.append({
            "reader_type": sc.reader_type,
            "nickname": sc.nickname,
            "text": sc.text,
            "sentiment": sc.sentiment,
        })
    return {
        "index": c.index,
        "volume_index": c.volume_index,
        "title": c.title,
        "summary": c.summary,
        "word_count": c.word_count,
        "tension": c.tension.value,
        "key_events": c.key_events,
        "sp_triggered": c.sp_triggered,
        "closing_hook": c.closing_hook,
        "closing_hook_type": getattr(c, "closing_hook_type", "") or "",
        "setup_callbacks_invoked": list(getattr(c, "setup_callbacks_invoked", []) or []),
        "simulated_comments": comments,
        "is_draft": False,
        "status": "final",
        "row_id": f"{c.index}:final",
        "draft_reason": "",
    }


# ═══════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════

def run(host="127.0.0.1", port=5000, debug=False):
    print(f"  🌐 前端启动：http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run(debug=True)
