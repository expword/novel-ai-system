"""
DirectorAgent — 总导演：管理12个智能体的完整执行流水线。

执行顺序：
═══ PHASE 1: 世界基础 ═══
  1. RealmDesigner     → 境界/力量体系
  2. VolumePlanner     → 卷结构（需要卷数据才能设计势力/人物）
  3. FactionArchitect  → 世界势力架构
  4. WorldBuilder      → 世界观详细设定
  5. WorldChecklist    → 世界观完整性校验

═══ PHASE 2: 人物设计 ═══
  6. CharacterDesigner → 人物档案（基于完整世界+势力+境界）

═══ PHASE 3: 故事架构 ═══
  7. LinePlanner(全局) → 全局叙事线
  8. LinePlanner(各卷) → 所有卷的专属叙事线
  9. SatisfactionSystem→ 爽点总规划
  10. RhythmDesigner   → 节奏蓝图
  11. ForeshadowManager→ 伏笔总规划

═══ PHASE 4: 卷级（每卷开始前）═══
  12. VolumePlanner(章)→ 本卷逐章大纲

═══ PHASE 5: 章节循环 ═══
  13. Director         → 生成章节指令（整合所有系统）
  14. Writer           → 写作
  15. Critic           → 审校
  16. Memory           → 记忆提取
"""
import os
import json
from dataclasses import asdict
from datetime import datetime
from utils.json_utils import repair_json, safe_parse
from persistence.checkpoint import (
    save_state, load_state, load_progress,
    mark_phase_done, mark_phase_done_if, mark_chapter_done,
    is_phase_done, is_chapter_done, clear_checkpoint,
    migrate_legacy_phase_ids,
)
from persistence.state import (
    NovelState, ChapterDirective, TensionLevel, RhythmType,
    SatisfactionPoint, ForeshadowItem,
)
from agents.concept_pitch import design_concept_phase
from agents.intent_analyzer import analyze_intent
from agents.realm_designer import (
    design_realm_system, design_power_scaling,
    design_special_abilities, bind_abilities_to_characters,
)
from agents.geography_designer import design_geography
from agents.timeline_anchor import design_timeline
from agents.economy_designer import design_economy
from agents.volume_planner import (
    plan_all_volumes,
    plan_volume_chapters,
    validate_volume_outline_structure,
)
from agents.faction_architect import design_factions, get_factions_for_volume
from agents.world_builder import build_world, run_world_checklist
from agents.character_designer import design_all_characters
from agents.major_supporting_refiner import refine_major_characters
from agents.line_planner import plan_global_lines, plan_volume_lines
from agents.satisfaction_system import (
    plan_all_satisfaction_points, get_sp_for_chapter, mark_sp_triggered,
)
from agents.rhythm_designer import design_all_rhythms, get_rhythm_instruction
from agents.foreshadow_manager import (
    plan_all_foreshadowing, get_chapter_foreshadow_directive,
    get_foreshadow_status_report,
)
from agents.fortune_planner import plan_all_fortunes, get_fortunes_for_volume_brief
from agents.stage_architect import design_volume_stages
from agents.character_web import design_relationship_web
from agents.protagonist_journey import (
    plan_protagonist_journey, get_journey_context_for_volume, get_stage_beat_context,
)
from agents.writer import write_chapter, revise_chapter
from agents.critic import review_chapter
from agents.memory import process_chapter, format_writing_context
from agents.chapter_planner import build_chapter_blueprint
from agents.thread_tracker import update_story_thread
from agents.conflict_ladder import design_conflict_ladder
from agents.emotion_curve import design_emotion_curve
from agents.foreshadow_manager import plan_red_herrings
from agents.chapter_type_planner import plan_chapter_types
from agents.continuity_checker import check_continuity
from agents.voice_consistency_checker import check_voice_consistency
from agents.state_updater import update_state_after_chapter
from agents.glossary_manager import update_glossary
from agents.sensitive_filter import filter_and_report, format_report as format_sensitive_report
from persistence import version_control
from project_mgmt import human_in_loop
from project_mgmt.human_in_loop import HITLPause
from utils import invariants
from project_mgmt import project_context
from utils import ops_tracker
from config import (
    NOVEL_TITLE, NOVEL_GENRE, NOVEL_THEME,
    NUM_VOLUMES, WORDS_PER_CHAPTER,
    MAX_REVISION_ROUNDS, MIN_PASS_SCORE,
    OUTPUT_DIR, PLANS_DIR,
    HITL_MODE,
    INTENT_DESCRIPTION,
)
from llm_layer.llm import system_user


# ── 卷内张力曲线模板 ──────────────────────────────────
VOLUME_TENSION_CURVE = [
    (0.00, 0.08, TensionLevel.CALM),
    (0.08, 0.22, TensionLevel.RISING),
    (0.22, 0.30, TensionLevel.PEAK),
    (0.30, 0.42, TensionLevel.FALLING),
    (0.42, 0.52, TensionLevel.RISING),
    (0.52, 0.58, TensionLevel.TWIST),
    (0.58, 0.68, TensionLevel.FALLING),
    (0.68, 0.80, TensionLevel.RISING),
    (0.80, 0.90, TensionLevel.PEAK),
    (0.90, 0.95, TensionLevel.FALLING),
    (0.95, 1.00, TensionLevel.TWIST),
]

DIRECTOR_SYSTEM = "你是经验丰富的小说总导演，为每章生成精确叙事指令。输出严格JSON。"


class ChapterCanonBlockedError(RuntimeError):
    """canon-revise 跑满 N 轮仍有 critical 违规——本章拒绝定稿，整个写作流程 halt。

    语义（不兜底）：
      · 章节文件 rename 为 .draft.txt（作者可手动审查/修改）
      · completed_chapters 中本章被 pop（保持一致：未定稿不算完成章节）
      · 不调 mark_chapter_done（下次启动会重写本章）
      · 抛出后整个 _write_volume / run 链路停止，用户必须修复 outline/canon 才能继续

    存在意义：避免"critical 残留 → progress_warning → 章节照样定稿"的软兜底。
    """


# ── stepwise 阶段组定义 ──────────────────────────────────
# 与 web/app.py:_PHASE_GROUPS 保持一致；复制一份避免循环 import。
# _stepwise_checkpoint 用它判定"本组应跑的 phase 是否全部 done"——
# 如果 stepwise 跑完一组但仍有 phase 没标 done（多半是 LLM 失败 / 上游缺失），
# 不能悄悄滑到下一组，要在 progress_status 留下警告 + 强制暂停让用户审核。
_STEPWISE_GROUPS = {
    # 注意：-1.5（intent_asset_extractor）和 -0.7（plot_enhancer）是 best-effort 步骤——
    # 失败不阻塞 G1，所以不挂在 group 必跑清单里（director 单独 if-not-done 跑）
    "G1_intent":          ["-1", "0", "0.5", "0.6"],
    "G2_world":           ["1A", "1A2", "1B", "1C", "1D", "1E", "1F", "1G", "1H"],
    "G3_characters":      ["2", "2A2", "2B", "2C", "2C2"],
    "G4_plot":            ["3A", "3B", "3B2", "3C", "3D", "3D2", "3E", "3E2", "3E3", "3F", "3G"],
    "G5_framework_ready": [],
}


# phase id → 跟左侧大纲一致的中文名——用于 stdout 摘要、warning 文本
# 与前端 app.js 的 PHASE_LABELS 保持一致
_PHASE_LABELS = {
    "-1": "意图分析", "0": "立项三件套", "0.5": "全书蓝图", "0.6": "主角内核",
    "1A": "力量体系", "1A2": "力量刻度",
    "1B": "卷结构", "1C": "势力格局", "1D": "世界观", "1E": "世界观校验",
    "1F": "地理", "1G": "时间线", "1H": "经济",
    "2": "人物档案", "2A": "人物档案", "2A2": "人物深化",
    "2B": "关系网络", "2C": "特殊能力", "2D": "心理弧光",
    "2C2": "能力路线图",
    "3A": "全局叙事线", "3B": "卷内叙事线", "3B2": "冲突阶梯",
    "3C": "爽点系统", "3D": "节奏", "3D2": "情绪曲线",
    "3E": "伏笔", "3E2": "红鲱鱼", "3E3": "反转系统",
    "3F": "机缘", "3G": "主角历程",
}


def _phase_label(pid: str) -> str:
    """phase id → '主角内核 (0.6)' 这种带中文名的可读形式。"""
    label = _PHASE_LABELS.get(pid)
    if label:
        return f"{label}（{pid}）"
    # 卷级 phase
    if pid.startswith("4_stage_"):
        try: return f"叙事舞台·第{int(pid.rsplit('_',1)[-1])}卷"
        except Exception: return pid
    if pid.startswith("4_beats_"):
        try: return f"舞台节拍·第{int(pid.rsplit('_',1)[-1])}卷"
        except Exception: return pid
    if pid.startswith("4_lifecycle_"):
        try: return f"能力节点落章·第{int(pid.rsplit('_',1)[-1])}卷"
        except Exception: return pid
    if pid.startswith("4_vol"):
        try: return f"章节大纲·第{int(pid[5:])}卷"
        except Exception: return pid
    if pid.startswith("4_ctp_"):
        try: return f"章节类型·第{int(pid.rsplit('_',1)[-1])}卷"
        except Exception: return pid
    return pid


def _phase_labels_join(pids) -> str:
    """把 phase id list 转成"主角内核 / 反转系统 / 主角历程"这种中文连接字符串。"""
    return " / ".join(_phase_label(p) for p in pids)


# ── scheduler 任务的产物校验 ──────────────────────────
# 每个 task.id 对应一个 predicate(state) -> bool。返回 False 表示"agent 跑完了
# 但产物为空"，scheduler 在 _on_success 里就不会写 progress，下次会重跑。
# 没列在这里的 task.id 默认通过（保持兼容）。
_TASK_PREDICATES = {
    # Phase 0.6 · 主角内核（前置）
    "0.6": lambda s: bool(s.protagonist_journey.overall_theme and s.protagonist_journey.fatal_flaw),
    # Phase 1 · 世界
    "1A":  lambda s: bool(s.power_system and (not getattr(s.power_system, "has_hierarchy", True) or s.power_system.realms)),
    "1B":  lambda s: bool(s.volumes),
    "1C":  lambda s: bool(s.factions),
    "1D":  lambda s: bool(s.world_setting),
    "1F":  lambda s: bool(getattr(s.geography, "regions", None)),
    "1G":  lambda s: bool(getattr(s.timeline, "events", None)),
    "1H":  lambda s: bool(getattr(s.economy, "currencies", None) or getattr(s.economy, "items", None)),
    # Phase 2 · 人物
    "2A":  lambda s: bool(s.characters),
    "2B":  lambda s: bool(s.relationship_web.bonds),
    # Phase 3 · 情节
    "3A":  lambda s: bool(s.global_lines),
    "3B":  lambda s: bool(s.volume_lines),
    "3B2": lambda s: bool(s.conflict_ladder.entries),
    "3C":  lambda s: bool(s.satisfaction_points),
    "3D":  lambda s: bool(s.rhythm_plans),
    "3D2": lambda s: bool(s.emotion_curve.notes),
    "3E":  lambda s: bool(s.foreshadow_items),
    "3E2": lambda s: bool(s.red_herrings),  # 红鲱鱼（director.py 模式注册）
    "3E3": lambda s: bool(s.twist_system and s.twist_system.chains),  # 反转链
    "3F":  lambda s: bool(s.fortunes),
    "3G":  lambda s: bool(s.protagonist_journey.overall_theme),
    "2C2": lambda s: bool(
        s.power_system
        and s.power_system.special_abilities
        and all(getattr(a, "lifecycle_nodes", None) for a in s.power_system.special_abilities)
    ),
    "4":   lambda s: bool(s.story_stages),
    "4C":  lambda s: bool(s.chapter_type_plans),
}


def _volume_lifecycle_nodes_assigned(state: NovelState, volume_index: int) -> bool:
    if not state.power_system or not state.power_system.special_abilities:
        return False
    if not all(getattr(a, "lifecycle_nodes", None) for a in state.power_system.special_abilities):
        return False
    nodes = [
        n
        for asset in (state.power_system.special_abilities or [])
        for n in (getattr(asset, "lifecycle_nodes", None) or [])
        if getattr(n, "target_volume", 0) == volume_index
    ]
    return not nodes or all(getattr(n, "target_chapter", 0) > 0 for n in nodes)


def _scheduler_predicate(task, state) -> bool:
    """根据 task.id 查 _TASK_PREDICATES。未注册的默认 True（不强制校验）。"""
    pred = _TASK_PREDICATES.get(task.id)
    if pred is None:
        return True
    try:
        return bool(pred(state))
    except Exception:
        return False


# ── phase 失败诊断：检测到 phase 产物为空时输出"具体哪个字段空 / 长度多少" ──
# 用法：_diagnose_phase_failure("3G", state) → "protagonist_journey.milestones=0条（期望≥6）"
# stepwise_checkpoint 强标 missing 时调用，让用户在 stdout/前端 warning 看到根因。
_PHASE_FIELDS_FOR_DIAGNOSIS = {
    "0.6": [("protagonist_journey.overall_theme",   lambda s: 1 if s.protagonist_journey.overall_theme else 0,  1, "字段")],
    "1A":  [("power_system.realms",                 lambda s: len(s.power_system.realms) if s.power_system else 0, 1, "条")],
    "1B":  [("volumes",                             lambda s: len(s.volumes), 1, "卷")],
    "1C":  [("factions",                            lambda s: len(s.factions), 1, "条")],
    "1D":  [("world_setting",                       lambda s: len(s.world_setting), 100, "字符")],
    "1F":  [("geography.regions",                   lambda s: len(getattr(s.geography, "regions", []) or []), 1, "条")],
    "1G":  [("timeline.events",                     lambda s: len(getattr(s.timeline, "events", []) or []), 1, "条")],
    "1H":  [("economy.currencies / items",          lambda s: len(getattr(s.economy, "currencies", []) or []) + len(getattr(s.economy, "items", []) or []), 1, "条")],
    "2A":  [("characters",                          lambda s: len(s.characters), 3, "个")],
    "2B":  [("relationship_web.bonds",              lambda s: len(s.relationship_web.bonds), 1, "条")],
    "3A":  [("global_lines",                        lambda s: len(s.global_lines), 1, "条")],
    "3B":  [("volume_lines",                        lambda s: len(s.volume_lines), 1, "条")],
    "3B2": [("conflict_ladder.entries",             lambda s: len(s.conflict_ladder.entries), 1, "条")],
    "3C":  [("satisfaction_points",                 lambda s: len(s.satisfaction_points), 1, "个")],
    "3D":  [("rhythm_plans",                        lambda s: len(s.rhythm_plans), 1, "个")],
    "3D2": [("emotion_curve.notes",                 lambda s: len(s.emotion_curve.notes), 1, "条")],
    "3E":  [("foreshadow_items",                    lambda s: len(s.foreshadow_items), 1, "个")],
    "3E2": [("red_herrings",                        lambda s: len(s.red_herrings), 1, "个")],
    "3E3": [("twist_system.chains",                 lambda s: len(s.twist_system.chains) if s.twist_system else 0, 1, "条")],
    "3F":  [("fortunes",                            lambda s: len(s.fortunes), 1, "个")],
    "3G":  [
        ("protagonist_journey.overall_theme",   lambda s: 1 if s.protagonist_journey.overall_theme else 0, 1, "字段"),
        ("protagonist_journey.milestones",      lambda s: len(s.protagonist_journey.milestones), 1, "卷"),
    ],
    "4":   [("story_stages",                        lambda s: len(s.story_stages), 1, "个")],
    "4C":  [("chapter_type_plans",                  lambda s: len(s.chapter_type_plans), 1, "个")],
}


def _diagnose_phase_failure(phase: str, state) -> str:
    """返回 phase 失败的具体原因——哪些字段空了 / 数量不达标。"""
    spec = _PHASE_FIELDS_FOR_DIAGNOSIS.get(phase)
    if not spec:
        return f"{phase}: 无诊断规则（未在 _PHASE_FIELDS_FOR_DIAGNOSIS 注册）"
    parts = []
    for field_name, getter, expected, unit in spec:
        try:
            actual = getter(state)
        except Exception as e:
            parts.append(f"{field_name} 取值异常({type(e).__name__})")
            continue
        status = "OK" if actual >= expected else "[空]"
        parts.append(f"{field_name}={actual}{unit}（期望≥{expected}{unit}） {status}")
    return f"{phase}: " + " | ".join(parts)


class DirectorAgent:

    def __init__(self, resume: bool = True):
        os.makedirs(project_context.project_dir(), exist_ok=True)
        os.makedirs(project_context.plans_dir(), exist_ok=True)

        self._progress = load_progress()
        # 启动时的 phase 完成集合——用它对比判断"本次运行新完成了哪些 phase"
        # 防止 stepwise 下"每点一次继续都立即退出不推进"：
        # 只有某组里有 NEW 已完成 phase，checkpoint 才触发；
        # 全部跳过（已完成）就落到下一组不暂停
        self._phases_done_at_start: set = set(self._progress.get("phases", []) or [])
        restored = load_state() if resume else None

        if restored:
            self.state = restored
            done = len(self._progress["chapters"])
            print(f"  ♻  检测到断点，恢复进度：已完成阶段 {self._progress['phases']}，已写章节 {done} 章")
        else:
            if not resume:
                clear_checkpoint()
            self.state = NovelState(title=NOVEL_TITLE, genre=NOVEL_GENRE, theme=NOVEL_THEME)

        # 同步 HITL 审核状态（把外部文件里 approved=true 的同步进 state）
        human_in_loop.check_pending_approvals(self.state)

        # 写 PID 文件（让 project_manager 能查进程状态）
        # 必须用 _write_pid_record（带 create_time 锚点），否则 status() 会因为
        # PID 复用/in-process 写章把 web 进程 PID 长期当成 director 误判 running
        try:
            from project_mgmt import project_manager as _pm
            _pm._write_pid_record(project_context.current(), os.getpid())
        except Exception:
            pass

    def _set_current_step(self, phase: str = "", agent: str = "",
                            detail: str = "", chapter_index: int = -1):
        """章节层级细粒度更新；_section 负责 phase 层级。"""
        total_chapters = sum(v.total_chapters for v in self.state.volumes) if self.state.volumes else 0
        chapters_done = len(self.state.completed_chapters)
        fields = {
            "chapters_done": chapters_done,
            "total_chapters": total_chapters,
            "progress_ratio": (chapters_done / total_chapters) if total_chapters > 0 else 0,
        }
        if phase:
            fields["phase"] = phase
        if agent:
            fields["agent"] = agent
        if detail:
            fields["detail"] = detail
        if chapter_index >= 0:
            fields["chapter_index"] = chapter_index
        _update_progress_status(**fields)

    def _clear_current_step(self):
        try:
            path = project_context.progress_status_file()
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _apply_revision_and_reaudit(self, *, chapter_index, path, original_text,
                                    new_text, audit_fn, audits_dict, label):
        """
        章后审计触发整章修订后的统一收尾——三处审计循环（能力/读者/对话）共用：
          1. new_text 长度不足原稿 70% → 拒绝写回，返回 (None, original_text)
          2. 否则覆写 chapter 文件、刷新 ChapterSummary.word_count、重新跑一次 audit
          3. 返回 (new_audit_or_None, accepted_text) — 调用方负责 block-specific 打印

        audit_fn: callable(state, chapter_index, text) -> audit_obj 或 None
        audits_dict: state.<...>_audits 引用，重审通过会覆盖该 chapter_index 的条目
        label: 仅用于"输出过短"日志（"polish" / "reader-revise" / "dialogue-revise"）

        实现：内部走 core.revise_loop.run_revise_loop(max_rounds=1)——
        这样 5 处 revise 路径全部归一到同一框架，写盘/字数同步/长度兜底
        逻辑单一来源。caller 行为不变（向后兼容）。
        """
        from core.revise_loop import ReviseConfig, run_revise_loop

        # caller 自己负责调 revise——这个 helper 收到的是 new_text 已经生成好的
        # 包装成"假 revise_fn"：忽略 feedback，直接返回 caller 给的 new_text
        # 同时 audit 包装成始终 needs_revise=False（因为已经触发过，单轮即收）
        def _stub_revise(_s, _d, _t, _fb):
            return new_text

        def _audit_wrap(state, ci, text):
            return audit_fn(state, ci, text) if audit_fn else None

        cfg = ReviseConfig(
            label=label,
            audit_fn=_audit_wrap,
            # 第一次 audit（initial_audit=None 时入口跑）总是返回 None 或 audit obj——
            # needs_revise 永远 True 才能进 loop（caller 已经决定了要 revise）
            needs_revise=lambda _a: True,
            feedback_builder=lambda _a, _r: "",
            revise_fn=_stub_revise,
            max_rounds=1,
            min_length_ratio=0.7,
            on_short=lambda r, n, o, s: print(
                f"  ⚠ {label} 输出过短 ({n}/{o})，保留原稿"
            ),
            chapter_path=path,
            update_word_count=True,
        )
        # 给 initial_audit 一个 dummy 让 needs_revise=True 评估通过（不实际重跑入口 audit）
        result = run_revise_loop(
            state=self.state, chapter_index=chapter_index, directive=None,
            config=cfg, initial_text=original_text,
            initial_audit=object(),  # dummy，让 needs_revise 立即返回 True
        )
        if result.rounds_accepted == 0:
            return None, original_text
        # 重审结果已写到 result.last_audit；同步到 audits_dict
        if result.last_audit is not None:
            audits_dict[chapter_index] = result.last_audit
        return result.last_audit, result.final_text

    def _stepwise_checkpoint(self, group_id: str, group_name: str):
        """
        逐步审核模式的暂停点——阶段组完成后 graceful exit，让用户审核/修改。
        用户点"继续"会再次启动子进程，靠 is_phase_done 跳过已完成的，从下一组继续。

        group_id:   "G0_intent"/"G1_world"/"G2_characters"/"G3_plot"/"G4_framework_ready"
        group_name: 展示给用户看的中文
        """
        from project_mgmt import project_manager
        try:
            mode = project_manager.get_mode(project_context.current())
        except Exception:
            mode = "auto"
        if mode != "stepwise":
            return
        current_done = set(load_progress().get("phases", []) or [])
        new_phases = current_done - self._phases_done_at_start
        # 检查本组应跑的 phase 是否全部 done——失败的（产物为空被 helper 跳过）会在这里被捕获
        expected = set(_STEPWISE_GROUPS.get(group_id, []))
        missing = expected - current_done
        if missing:
            # 本组有 phase 没成功——逐个诊断 + 写 error warning，然后暂停。
            # 不能强制 mark_done；否则会留下"进度已完成但 state 产物为空"的脏断点。
            from persistence.checkpoint import add_progress_warning
            sorted_missing = sorted(missing)
            print(f"\n  ⚠ 阶段组《{group_name}》缺 {len(missing)} 个模块：{_phase_labels_join(sorted_missing)}")
            print(f"  ── 失败诊断 ───────────────────────")
            phase_diagnostics = []
            for pid in sorted_missing:
                raw_diag = _diagnose_phase_failure(pid, self.state)
                # raw_diag 形如 "3G: protagonist_journey...="——把前缀 phase id 改为中文名
                diag_body = raw_diag.split(":", 1)[-1].strip() if ":" in raw_diag else raw_diag
                pretty = f"{_phase_label(pid)}: {diag_body}"
                print(f"    · {pretty}")
                phase_diagnostics.append(pretty)
                # 每个 phase 单独写一条 warning
                add_progress_warning(
                    level="error",
                    source=f"phase:{pid}",
                    message=f"产物未达标 → {diag_body}（未标完成；修复后会重跑）",
                )
            print(f"  ── 未标完成；请修复配置/提示词或重建该模块后继续 ─")
            # 顶层 group 摘要 warning
            add_progress_warning(
                level="error",
                source=f"group:{group_id}",
                message=f"阶段组《{group_name}》：{len(missing)} 个模块产物为空，已暂停："
                        + _phase_labels_join(sorted_missing)
                        + "。详情：" + " ; ".join(phase_diagnostics),
            )
            save_state(self.state)
            self._set_current_step(
                phase=f"⏸ {group_name} 未完成",
                agent="stepwise",
                detail=f"阶段组 [{group_id}] 缺产物：{_phase_labels_join(sorted_missing)}",
            )
            try:
                os.remove(project_context.pid_file())
            except OSError:
                pass
            raise SystemExit(0)
        elif not new_phases:
            # 既没缺，又没新增——说明本组本来就早已完成，无需再暂停
            return
        # 保存进度 + 快照（回滚点，标签含 group_id，前端可用它找到）
        save_state(self.state)
        try:
            version_control.snapshot(
                self.state,
                label=f"stepwise_{group_id}_done",
                phase=group_id,
                notes=f"阶段组《{group_name}》完成——审核期回滚点",
            )
        except Exception as e:
            print(f"  [stepwise] 存快照失败（不致命）：{type(e).__name__}: {e}")
        self._set_current_step(
            phase=f"⏸ {group_name} 已完成",
            agent="stepwise",
            detail=f"阶段组 [{group_id}] 完成——审核后点 ▶ 继续下一阶段",
            # 用一个专门字段告诉前端是哪组刚完成（供 auto-jump 用）
        )
        # 单独写一条 group_just_completed 到 progress_status.json
        try:
            from core.director import _update_progress_status  # 内部函数
            _update_progress_status(group_just_completed=group_id)
        except Exception:
            pass
        print(f"\n⏸ [stepwise] 阶段组《{group_name}》完成，子进程退出。")
        print(f"  已存快照 stepwise_{group_id}_done（可用于回滚）。")
        print(f"  打开 web UI 审核/修改相关模块，然后点击 ▶ 继续进入下一阶段。")
        # 清掉 pid 文件让 status 回到 idle
        try:
            os.remove(project_context.pid_file())
        except OSError:
            pass
        raise SystemExit(0)

    def _scrub_stale_phases(self):
        """启动时扫描已 done 的 phase——若它的产物校验失败（state 字段为空），
        从 progress 中移除它，下次会重跑。覆盖：人工删 state 字段、checkpoint
        损坏、迁移之后的不一致等。
        """
        progress = load_progress()
        phases = list(progress.get("phases", []) or [])
        kept, dropped = [], []
        for pid in phases:
            pred = _TASK_PREDICATES.get(pid)
            # 卷级 phase（4_stage_3 / 4_vol2 / ...）不在 _TASK_PREDICATES 里，单独算
            if pred is None and pid.startswith("4_"):
                if pid.startswith("4_stage_"):
                    try:
                        vi = int(pid.rsplit("_", 1)[-1])
                        ok = any(s.volume == vi for s in self.state.story_stages)
                    except Exception:
                        ok = True  # 不确定就保守保留
                elif pid.startswith("4_vol"):
                    try:
                        vi = int(pid[5:])
                        vol = self.state.get_volume(vi)
                        ok = bool(vol and vol.chapter_outlines)
                    except Exception:
                        ok = True
                elif pid.startswith("4_ctp_"):
                    try:
                        vi = int(pid.rsplit("_", 1)[-1])
                        ok = any(p.volume == vi and p.per_chapter for p in self.state.chapter_type_plans)
                    except Exception:
                        ok = True
                elif pid.startswith("4_lifecycle_"):
                    try:
                        vi = int(pid.rsplit("_", 1)[-1])
                        ok = _volume_lifecycle_nodes_assigned(self.state, vi)
                    except Exception:
                        ok = True
                elif pid.startswith("4_beats_"):
                    # beats 校验难做（按卷区分 stage_id），保守保留
                    ok = True
                else:
                    ok = True
            elif pred is None:
                ok = True  # 没注册的 phase 默认通过
            else:
                try:
                    ok = bool(pred(self.state))
                except Exception:
                    ok = True
            if ok:
                kept.append(pid)
            else:
                dropped.append(pid)
        if dropped:
            from persistence.checkpoint import _save_progress, add_progress_warning
            progress["phases"] = kept
            _save_progress(progress)
            self._progress = progress
            msg = f"产物完整性扫描清除了 {len(dropped)} 个空 phase：{dropped}（下次启动会重跑）"
            print(f"  🧹 {msg}")
            add_progress_warning(level="warn", source="scrub", message=msg)

    def _check_control_point(self):
        """在每个 Phase / 每章之间调。碰到 stop 就退出；碰到 pause 就阻塞等。"""
        sig = project_context.check_control()
        if sig == "stop":
            print("\n  🛑 检测到 stop 标志，保存后优雅退出")
            save_state(self.state)
            # 清掉 pid 文件
            try:
                os.remove(project_context.pid_file())
            except OSError:
                pass
            raise SystemExit(0)
        if sig == "pause":
            print("\n  ⏸ 检测到 pause 标志，保存后等待恢复……")
            save_state(self.state)
            result = project_context.wait_while_paused()
            if result == "stop":
                print("  🛑 等待中收到 stop")
                try:
                    os.remove(project_context.pid_file())
                except OSError:
                    pass
                raise SystemExit(0)
            print("  ▶ 恢复")

    # ═══════════════════════════════════════════════════
    #  规划阶段：DAG 调度器驱动
    # ═══════════════════════════════════════════════════

    def _run_planning_with_scheduler(self):
        """
        用 TaskScheduler 并发跑所有规划 Phase。
        依赖独立的任务会被分波并发——Phase 1-D/1-F/1-G/1-H 同时跑，
        Phase 2-A2/2-B/2-C/2-D 同时跑，Phase 3 大量并发。

        已完成的任务从 progress.json 读取并跳过。
        失败的非 critical 任务不阻塞其他分支；critical 失败则抛异常终止。

        【stepwise 模式】：跳过调度器，走下面的顺序 fallback 块——
        这样每个 phase 组边界的 checkpoint 才能在正确位置触发。
        """
        # stepwise 模式：不跑调度器，让下面的顺序块一个个跑
        from project_mgmt import project_manager
        try:
            _mode = project_manager.get_mode(project_context.current())
        except Exception:
            _mode = "auto"
        if _mode == "stepwise":
            print("  [stepwise] 跳过并发调度器，用顺序模式（允许组间暂停）")
            return

        from core.scheduler import TaskScheduler
        from core.scheduler_tasks import ALL_TASKS
        from config import PARALLEL_WORKERS

        sched = TaskScheduler(ALL_TASKS)

        # ── Hook：任务开始/结束时的副作用（写 progress_status、mark_phase_done 等）──
        def _on_start(task):
            self._check_control_point()  # 每任务前检查 pause/stop 信号
            self._set_current_step(task.phase, task.agent_name, task.detail)
            _section(f"{task.phase}: {task.detail}")

        def _on_success(task, elapsed):
            # 即便 fn 没抛异常，也校验一下产物——LLM 失败被 empty_ok 吞掉时 fn 也是"成功"
            ok = _scheduler_predicate(task, self.state)
            if ok:
                mark_phase_done(task.id, self.state)
                print(f"  ✓ [{task.id}] 完成（{elapsed:.1f}s）")
            else:
                print(f"  ⚠ [{task.id}] 完成但产物为空（{elapsed:.1f}s）——不写 progress，下次会重跑")
                save_state(self.state)

        def _on_failure(task, err, elapsed):
            print(f"  ! [{task.id}] 失败（{elapsed:.1f}s）：{type(err).__name__}: {err}")
            # 失败一律不写 progress——critical 已在 scheduler._run_one 抛错；
            # 非 critical 失败保持 progress 干净，下次重启会重跑
            save_state(self.state)

        def _on_skipped(task):
            # task.skip_if 显式判断为 True（如"无 raw_description"）才进这里——是合法跳过
            print(f"  → [{task.id}] 跳过（skip_if 条件满足）")
            mark_phase_done(task.id, self.state)

        def _on_wave_start(wave_num, tasks):
            names = " / ".join(t.id for t in tasks)
            self._set_current_step(
                f"Wave #{wave_num}",
                "Scheduler",
                f"并发 {len(tasks)} 任务：{names[:60]}"
            )

        sched.on_task_start = _on_start
        sched.on_task_success = _on_success
        sched.on_task_failure = _on_failure
        sched.on_task_skipped = _on_skipped
        sched.on_wave_start = _on_wave_start

        # ── 已完成任务（从 progress.json 读）──
        done_ids = set(self._progress.get("phases", []))

        # ── 启动调度器 ──
        try:
            outcome = sched.run(
                self.state,
                done_ids=done_ids,
                max_parallel=PARALLEL_WORKERS,
            )
            done_count = sum(1 for v in outcome.values() if v == "done")
            fail_count = sum(1 for v in outcome.values() if v == "failed")
            skip_count = sum(1 for v in outcome.values() if v == "skipped")
            print(f"\n  ═══ 调度器完成：{done_count} 成功 / {fail_count} 失败 / {skip_count} 跳过 ═══\n")

            failed_ids = [tid for tid, st in outcome.items() if st == "failed"]
            if failed_ids:
                print(f"  ⚠ 失败任务：{failed_ids}")
        except Exception as e:
            print(f"\n  !! 调度器中断：{type(e).__name__}: {e}")
            print("  （后续的传统 Phase 检查会从断点处接着跑）")

        # ── 状态审计：用户要求"如果有地方没有生成，显示问题，不要盲目运行" ──
        try:
            from persistence.state_audit import print_state_audit
            print_state_audit(self.state)
        except Exception as e:
            print(f"  ⚠ 状态审计失败（不影响主流程）：{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    #  主入口
    # ═══════════════════════════════════════════════════

    def run(self):
        _banner(f"开始创作《{self.state.title}》")
        # 老项目的 phase id 一致性迁移（3E2 双义等历史问题）
        try:
            if migrate_legacy_phase_ids(self.state):
                self._progress = load_progress()
        except Exception as e:
            print(f"  ⚠ phase 迁移检查失败（不致命）：{type(e).__name__}: {e}")
        # 产物完整性扫描：清理"phase 标 done 但产物实际为空"的进度记录，
        # 避免人工删了某个 state 字段后仍被跳过（HIGH #13）
        try:
            self._scrub_stale_phases()
        except Exception as e:
            print(f"  ⚠ 产物完整性扫描失败（不致命）：{type(e).__name__}: {e}")
        p = self._progress
        self._check_control_point()
        self._set_current_step("启动中", "DirectorAgent", "初始化")

        # ── 规划阶段（Phase -1 ~ Phase 4-C）：改用 DAG 调度器并发执行 ─────
        # 独立分支（1-D/1-F/1-G/1-H；2-A2/2-B/2-C/2-D；Phase 3 多数）自动并发
        # LLM 调用统一走 llm_pool（速率/并发/熔断保护）
        self._run_planning_with_scheduler()
        # 重新读 progress，因为 scheduler 会调 mark_phase_done 更新
        p = self._progress = load_progress()

        # 下面的传统 Phase 块保留作为"已完成检查"——调度器跑完后全部 skip
        # 如果调度器失败了，这里会接着跑未完成的任务（断点恢复）

        # ── Phase -1: 创作意图分析（fallback）──────────────
        if not is_phase_done("-1", p):
            intent = self.state.creative_intent
            desc = intent.raw_description or INTENT_DESCRIPTION
            if desc and not intent.analyzed:
                _section("Phase -1: 意图分析（自然语言 → 立项信号）")
                self._set_current_step("Phase -1", "IntentAnalyzer", "分析作者意图")
                intent.raw_description = desc
                analyze_intent(self.state, desc)
                self._save("creative_intent.json", self._dump_creative_intent())
            mark_phase_done("-1", self.state)
        else:
            print("  ✓ [跳过] Phase -1 意图分析已完成")

        # ── Phase -1.5: 主动抽取用户在 intent 里**明确声明的 asset** ──
        # **独立 phase**（之前嵌在 Phase -1 if 块里——历史 bug：老项目 Phase -1 已 done
        # 就永远跳过 asset 抽取，导致所有 5/17 前创建的项目 special_abilities 永远是空）
        # 现在独立后，老项目下次启动会自动补跑。
        if not is_phase_done("-1.5", p):
            _section("Phase -1.5: 从意图抽取 asset（intent_asset_extractor）")
            try:
                from agents.intent_asset_extractor import extract_assets_from_intent
                self._set_current_step("Phase -1.5", "IntentAssetExtractor", "抽取用户声明 asset")
                extract_assets_from_intent(self.state)
                self._save("power_system.json", self._dump_power_system())
            except Exception as _e:
                print(f"  ⚠ intent_asset_extractor 失败（不阻塞）：{type(_e).__name__}: {_e}")
            mark_phase_done("-1.5", self.state)
        else:
            print("  ✓ [跳过] Phase -1.5 意图 asset 抽取已完成")

        # ── Phase -0.7: 补充情节建议（plot_enhancer）──
        # 主动反问"只看作者写的会不会无聊"，补 3-5 个能让读者翻下一页的钩子。
        # 失败/产出空都不阻塞下游——concept_pitch 仍按作者原意走。
        if not is_phase_done("-0.7", p):
            _section("Phase -0.7: 补充情节建议（plot_enhancer）")
            try:
                from agents.plot_enhancer import enhance_plot
                self._set_current_step("Phase -0.7", "PlotEnhancer",
                                         "生成补充情节建议供作者审")
                enhance_plot(self.state)
                self._save("creative_intent.json", self._dump_creative_intent())
            except Exception as _e:
                print(f"  ⚠ plot_enhancer 失败（不阻塞）：{type(_e).__name__}: {_e}")
            mark_phase_done("-0.7", self.state)
        else:
            print("  ✓ [跳过] Phase -0.7 补充情节建议已完成")

        # ── Phase 0: 创作立项（卖点 + 套路库 + 文风手册）──
        if not is_phase_done("0", p):
            _section("Phase 0: 立项层（ConceptPitch + TropeLibrary + ToneManual）")
            self._set_current_step("Phase 0", "ConceptPitch", "卖点定位 + 套路库 + 文风手册")
            design_concept_phase(self.state)
            self._save("concept_pitch.json", self._dump_concept_pitch())
            mark_phase_done("0", self.state)
        else:
            print("  ✓ [跳过] Phase 0 立项已完成")

        # ── Phase 0.5: MasterDispatcher ────────────────
        # 中央调度器：一次 LLM 产出全书骨架蓝图（故事/角色槽位/势力骨架/关键节点）
        # 下游 agent 按骨架并发填充，每次 LLM 任务单一、prompt 短
        if not is_phase_done("0.5", p):
            _section("Phase 0.5: Master Dispatcher（全书骨架蓝图）")
            self._set_current_step("Phase 0.5", "MasterDispatcher", "生成全书蓝图：故事/角色槽位/势力骨架/关键节点")
            from agents.master_dispatcher import dispatch_master_outline
            dispatch_master_outline(self.state)
            mark_phase_done("0.5", self.state)
        else:
            print("  ✓ [跳过] Phase 0.5 MasterOutline 已完成")

        # ── Phase 0.6: 主角内核（核心创伤/真实目标/致命弱点）──
        # 提到 1A/1B/2A 之前——下游卷结构/人物档案/叙事线都围绕这套内核展开，
        # 而不是事后由 3G 来"反推合理化"
        if not is_phase_done("0.6", p):
            _section("Phase 0.6: 主角内核（创伤/目标/弱点——下游所有 phase 围绕它展开）")
            self._set_current_step("Phase 0.6", "ProtagonistCore", "定主角核心创伤/真实目标/致命弱点")
            from agents.protagonist_journey import design_protagonist_core
            design_protagonist_core(self.state)
            # 模块审核 + 不达标重生（最多 2 次）
            try:
                from agents.module_reviewer import review_and_regenerate
                review_and_regenerate(self.state, "0.6", lambda s: design_protagonist_core(s))
            except Exception as _e:
                print(f"  ⚠ 0.6 模块审核失败（不影响产出）：{type(_e).__name__}: {_e}")
            mark_phase_done_if("0.6", self.state,
                lambda s: bool(s.protagonist_journey.overall_theme and s.protagonist_journey.fatal_flaw),
                on_skip_msg="主角内核未生成")
        else:
            print("  ✓ [跳过] Phase 0.6 主角内核已完成")

        # ⏸ 阶段组 1：立项完成
        self._stepwise_checkpoint("G1_intent", "立项（意图/卖点/套路/文风/骨架蓝图）")

        # ── Phase 1: 世界基础 ──────────────────────────
        if not is_phase_done("1A", p):
            _section("Phase 1-A: 境界/力量体系")
            self._set_current_step("Phase 1-A", "RealmDesigner", "境界/力量体系")
            design_realm_system(self.state)
            try:
                from agents.module_reviewer import review_and_regenerate
                def _re_realm(s):
                    preserved_special_abilities = []
                    if s.power_system and s.power_system.special_abilities:
                        preserved_special_abilities = list(s.power_system.special_abilities)
                    s.power_system = None
                    design_realm_system(s)
                    if s.power_system and preserved_special_abilities and not s.power_system.special_abilities:
                        s.power_system.special_abilities = preserved_special_abilities
                review_and_regenerate(self.state, "1A", _re_realm)
            except Exception as _e:
                print(f"  ⚠ 1A 模块审核失败：{type(_e).__name__}: {_e}")
            mark_phase_done("1A", self.state)
        else:
            print("  ✓ [跳过] Phase 1-A 境界体系已完成")

        if not is_phase_done("1A2", p):
            _section("Phase 1-A2: 力量刻度（战力/寿命/神识/越级规则）")
            design_power_scaling(self.state)
            mark_phase_done("1A2", self.state)
        else:
            print("  ✓ [跳过] Phase 1-A2 力量刻度已完成")

        if not is_phase_done("1B", p):
            _section("Phase 1-B: 卷结构规划（MasterOutline 驱动 · 并发 per-volume）")
            self._set_current_step("Phase 1-B", "VolumePlanner", "整本起承转合分配 + 并发各卷详情")
            from agents.volume_planner import plan_all_volumes_dispatched
            plan_all_volumes_dispatched(self.state)
            self._save("volumes.json", self._dump_volumes())
            try:
                from agents.module_reviewer import review_and_regenerate
                def _re_vols(s):
                    s.volumes = []
                    plan_all_volumes_dispatched(s)
                review_and_regenerate(self.state, "1B", _re_vols)
                self._save("volumes.json", self._dump_volumes())
            except Exception as _e:
                print(f"  ⚠ 1B 模块审核失败：{type(_e).__name__}: {_e}")
            mark_phase_done("1B", self.state)
        else:
            print("  ✓ [跳过] Phase 1-B 卷结构已完成")

        if not is_phase_done("1C", p):
            _section("Phase 1-C: 世界势力架构")
            design_factions(self.state)
            self._save("factions.json", self._dump_factions())
            mark_phase_done("1C", self.state)
        else:
            print("  ✓ [跳过] Phase 1-C 势力架构已完成")

        if not is_phase_done("1D", p):
            _section("Phase 1-D: 世界观构建")
            build_world(self.state)
            # 1D 后立刻抽 world_canon——把 world_setting 大段自然语言里的关键锚点
            # （朝代/年号/根地理/时代定性）抽成机器可读字段，让下游 canon_checker
            # 能直接比对、不再靠模糊文本匹配。幂等：source_hash 未变化时跳过。
            try:
                from agents.world_canon_extractor import extract_world_canon
                extract_world_canon(self.state)
            except Exception as _e:
                print(f"  ⚠ world_canon 抽取失败（不阻塞）：{type(_e).__name__}: {_e}")
            mark_phase_done("1D", self.state)
        else:
            print("  ✓ [跳过] Phase 1-D 世界观已完成")

        if not is_phase_done("1E", p):
            _section("Phase 1-E: 世界观完整性校验")
            gaps = run_world_checklist(self.state)
            self._save("world.json", self._dump_world())
            if gaps:
                # 把 gaps 写进 progress warning 让用户在前端能看到，但**不阻塞**——
                # LLM 评估"世界观完整性"主观性高，几乎永远会报些 gaps；
                # 阻塞会让 stepwise 死循环（每次进 G2 都因 1E 不通过而触发暂停）
                print(f"  ⚠ 世界观仍有 {len(gaps)} 处提示性缺失（非阻塞，已留 warning）：{gaps[:3]}")
                try:
                    from persistence.checkpoint import add_progress_warning
                    add_progress_warning(
                        level="warn",
                        source="phase:1E",
                        message=f"世界观还可以更完整（{len(gaps)} 处提示）："
                                + " / ".join(str(g)[:30] for g in gaps[:3])
                                + "（已记入 facts；想精修可在 web UI 重建 world，否则可继续）",
                    )
                except Exception:
                    pass
            mark_phase_done("1E", self.state)
        else:
            print("  ✓ [跳过] Phase 1-E 世界观校验已完成")

        if not is_phase_done("1F", p):
            _section("Phase 1-F: 地理系统（区划/交通/距离矩阵）")
            design_geography(self.state)
            self._save("geography.json", self._dump_geography())
            mark_phase_done("1F", self.state)
        else:
            print("  ✓ [跳过] Phase 1-F 地理已完成")

        if not is_phase_done("1G", p):
            _section("Phase 1-G: 时间锚点（历史事件时间轴）")
            design_timeline(self.state)
            self._save("timeline.json", self._dump_timeline())
            mark_phase_done("1G", self.state)
        else:
            print("  ✓ [跳过] Phase 1-G 时间线已完成")

        if not is_phase_done("1H", p):
            _section("Phase 1-H: 经济系统（货币/物价/财富曲线）")
            design_economy(self.state)
            self._save("economy.json", self._dump_economy())
            mark_phase_done("1H", self.state)
        else:
            print("  ✓ [跳过] Phase 1-H 经济已完成")

        # ⏸ 阶段组 2：世界完成
        self._stepwise_checkpoint("G2_world", "世界（境界/世界观/地理/时间/经济/势力）")

        # ── Phase 2: 人物设计 ──────────────────────────
        if not is_phase_done("2", p):
            _section("Phase 2-A: 核心人物档案设计（分批：主角圈→盟友→反派→卷内）")
            try:
                design_all_characters(self.state)
            except Exception as e:
                print(f"  ⚠ Phase 2-A 异常：{type(e).__name__}: {e}")
                print(f"  ⚠ 已设计 {len(self.state.characters)} 个角色，继续后续 Phase")
            self._save("characters.json", self._dump_characters())
            # 至少有主角才能 mark done；一个角色都没有说明 LLM 彻底挂了
            if self.state.characters:
                try:
                    from agents.module_reviewer import review_and_regenerate
                    def _re_chars(s):
                        s.characters = []
                        design_all_characters(s)
                    review_and_regenerate(self.state, "2A", _re_chars)
                    self._save("characters.json", self._dump_characters())
                except Exception as _e:
                    print(f"  ⚠ 2A 模块审核失败：{type(_e).__name__}: {_e}")
                mark_phase_done("2", self.state)
            else:
                raise RuntimeError("Phase 2-A 一个角色都没生成——LLM 调用有严重问题，请检查 API key/网络/模型")
        else:
            print("  ✓ [跳过] Phase 2 人物设计已完成")

        if not is_phase_done("2A2", p):
            _section("Phase 2-A2: 主角+主要配角+反派 细腻深化（一人一次）")
            refine_major_characters(self.state)
            self._save("characters.json", self._dump_characters())
            mark_phase_done("2A2", self.state)
        else:
            print("  ✓ [跳过] Phase 2-A2 细腻深化已完成")

        # Phase 2-A3: 角色能力档案设计——每个 (角色 × 能力) 独立 LLM 调用，
        # 形成累积契约防止后续矛盾。范围：主角 + 主要配角 + 反派
        if not is_phase_done("2A3", p):
            _section("Phase 2-A3: 核心角色能力档案（每个能力独立深化）")
            try:
                from agents.character_ability_designer import design_all_character_abilities
                from persistence.checkpoint import save_state_section
                design_all_character_abilities(self.state)
                save_state_section(self.state, "character_ability_profiles")
            except Exception as _e:
                print(f"  ⚠ 2A3 角色能力档案失败（不阻塞后续）：{type(_e).__name__}: {_e}")
            mark_phase_done("2A3", self.state)
        else:
            print("  ✓ [跳过] Phase 2-A3 角色能力档案已完成")

        if not is_phase_done("2B", p):
            _section("Phase 2-B: 人物关系网络设计")
            design_relationship_web(self.state)
            self._save("relationship_web.json", self._dump_relationship_web())
            mark_phase_done_if("2B", self.state,
                lambda s: bool(s.relationship_web.bonds),
                on_skip_msg="未生成任何 bond")
        else:
            print("  ✓ [跳过] Phase 2-B 关系网络已完成")

        if not is_phase_done("2C", p):
            _section("Phase 2-C: 特殊能力设计（已有角色名，直接绑定持有者）")
            design_special_abilities(self.state)
            # 若仍有 holder_name 为空的，兜底再跑一次 LLM 匹配
            bind_abilities_to_characters(self.state)
            self._save("characters.json", self._dump_characters())
            mark_phase_done("2C", self.state)
        else:
            print("  ✓ [跳过] Phase 2-C 特殊能力已完成")

        if not is_phase_done("2C2", p):
            _section("Phase 2-C2: 能力路线图（金手指 lifecycle + 反向 SP + 标 arc）")
            from agents.ability_roadmap_planner import run_phase_2c2
            run_phase_2c2(self.state)
            # 只有 lifecycle 真写入后才标完成；否则下次启动要重跑，不能留下"完成但空产物"。
            mark_phase_done_if(
                "2C2",
                self.state,
                _TASK_PREDICATES["2C2"],
                on_skip_msg="能力路线图产物为空：special_abilities 缺 lifecycle_nodes",
            )
        else:
            print("  ✓ [跳过] Phase 2-C2 能力路线图已完成")

        # ⏸ 阶段组 3：人物完成
        self._stepwise_checkpoint("G3_characters", "人物（角色档案/关系网/特殊能力/心理弧光/能力路线图）")

        # ── Phase 3: 故事架构 ──────────────────────────
        if not is_phase_done("3A", p):
            _section("Phase 3-A: 全局叙事线规划")
            plan_global_lines(self.state)
            if self.state.global_lines:
                try:
                    from agents.module_reviewer import review_and_regenerate
                    def _re_3a(s):
                        s.global_lines = []
                        plan_global_lines(s)
                    review_and_regenerate(self.state, "3A", _re_3a)
                except Exception as _e:
                    print(f"  ⚠ 3A 模块审核失败：{type(_e).__name__}: {_e}")
            mark_phase_done_if("3A", self.state,
                lambda s: bool(s.global_lines),
                on_skip_msg="LLM 熔断/失败导致全局叙事线为 0 条")
        else:
            print("  ✓ [跳过] Phase 3-A 全局叙事线已完成")

        if not is_phase_done("3B", p):
            _section("Phase 3-B: 各卷专属叙事线规划（并发）")
            from agents.line_planner import plan_all_volume_lines_parallel
            plan_all_volume_lines_parallel(self.state)
            self._save("lines_plan.json", self._dump_lines())
            mark_phase_done_if("3B", self.state,
                lambda s: bool(s.volume_lines),
                on_skip_msg="LLM 失败导致卷内叙事线为 0 条")
        else:
            print("  ✓ [跳过] Phase 3-B 卷内叙事线已完成")

        if not is_phase_done("3B2", p):
            _section("Phase 3-B2: 冲突阶梯（每卷冲突类型+层级+解决方式）")
            design_conflict_ladder(self.state)
            mark_phase_done_if("3B2", self.state,
                lambda s: bool(s.conflict_ladder.entries),
                on_skip_msg="未生成任何冲突条目")
        else:
            print("  ✓ [跳过] Phase 3-B2 冲突阶梯已完成")

        if not is_phase_done("3C", p):
            _section("Phase 3-C: 爽点系统规划")
            plan_all_satisfaction_points(self.state)
            self._save("satisfaction_points.json", self._dump_satisfaction_points())
            mark_phase_done_if("3C", self.state,
                lambda s: bool(s.satisfaction_points),
                on_skip_msg="未生成任何爽点")
        else:
            print("  ✓ [跳过] Phase 3-C 爽点规划已完成")

        if not is_phase_done("3D", p):
            _section("Phase 3-D: 情节节奏设计")
            design_all_rhythms(self.state)
            self._save("rhythm_plans.json", self._dump_rhythm_plans())
            mark_phase_done_if("3D", self.state,
                lambda s: bool(s.rhythm_plans),
                on_skip_msg="未生成任何节奏计划")
        else:
            print("  ✓ [跳过] Phase 3-D 节奏设计已完成")

        if not is_phase_done("3D2", p):
            _section("Phase 3-D2: 情绪曲线（每卷基调+低谷+高点+对冲）")
            design_emotion_curve(self.state)
            mark_phase_done_if("3D2", self.state,
                lambda s: bool(s.emotion_curve.notes),
                on_skip_msg="未生成任何情绪曲线节点")
        else:
            print("  ✓ [跳过] Phase 3-D2 情绪曲线已完成")

        # 次序：先反转链（声明每层需要的 clues），再伏笔（优先满足 clues + 补独立伏笔），
        # 最后红鲱鱼（依赖伏笔已存在）。这样反转的 clues_planted 不会成为孤儿。
        if not is_phase_done("3E3", p):
            _section("Phase 3-E3: 反转系统设计（先于伏笔——每层 clues 给伏笔阶段铺路）")
            from agents.twist_designer import design_twists
            from persistence.state import TwistSystem as _TwistSystem
            design_twists(self.state)
            if self.state.twist_system and self.state.twist_system.chains:
                try:
                    from agents.module_reviewer import review_and_regenerate
                    def _re_3e3(s):
                        s.twist_system = _TwistSystem()
                        design_twists(s)
                    review_and_regenerate(self.state, "3E3", _re_3e3)
                except Exception as _e:
                    print(f"  ⚠ 3E3 模块审核失败：{type(_e).__name__}: {_e}")
            mark_phase_done_if("3E3", self.state,
                lambda s: bool(s.twist_system and s.twist_system.chains),
                on_skip_msg="未生成任何反转链")
        else:
            print("  ✓ [跳过] Phase 3-E3 反转系统已完成")

        if not is_phase_done("3E", p):
            _section("Phase 3-E: 伏笔体系规划（含为反转铺路的必要 clues）")
            plan_all_foreshadowing(self.state)
            self._save("foreshadow_plan.json", self._dump_foreshadow_plan())
            mark_phase_done_if("3E", self.state,
                lambda s: bool(s.foreshadow_items),
                on_skip_msg="未生成任何伏笔")
        else:
            print("  ✓ [跳过] Phase 3-E 伏笔规划已完成")

        if not is_phase_done("3E2", p):
            _section("Phase 3-E2: 红鲱鱼（假线索）规划")
            plan_red_herrings(self.state)
            mark_phase_done_if("3E2", self.state,
                lambda s: bool(s.red_herrings),
                on_skip_msg="未生成任何红鲱鱼")
        else:
            print("  ✓ [跳过] Phase 3-E2 红鲱鱼已完成")

        if not is_phase_done("3F", p):
            _section("Phase 3-F: 机缘体系规划")
            plan_all_fortunes(self.state)
            self._save("fortune_plan.json", self._dump_fortunes())
            mark_phase_done_if("3F", self.state,
                lambda s: bool(s.fortunes),
                on_skip_msg="未生成任何机缘")
        else:
            print("  ✓ [跳过] Phase 3-F 机缘规划已完成")

        if not is_phase_done("3G", p):
            _section("Phase 3-G: 主角历程规划（卷级里程碑——内核已在 0.6 定）")
            from agents.protagonist_journey import _step1_overall_arc, _step2_volume_milestones
            # 0.6 跑过的项目内核已填好——只在缺失时补跑（向后兼容老项目）
            if not self.state.protagonist_journey.overall_theme:
                print("  ⓘ 内核未填（老项目或 0.6 跳过），用 1A/1B/2A 上下文补跑")
                _step1_overall_arc(self.state)
            _step2_volume_milestones(self.state)
            self._save("protagonist_journey.json", self._dump_protagonist_journey())
            # 模块审核（已加兜底——LLM 失败时也会有最小骨架，审核能识别出来）
            try:
                from agents.module_reviewer import review_and_regenerate
                def _re_3g(s):
                    s.protagonist_journey.milestones = []
                    _step2_volume_milestones(s)
                review_and_regenerate(self.state, "3G", _re_3g)
                self._save("protagonist_journey.json", self._dump_protagonist_journey())
            except Exception as _e:
                print(f"  ⚠ 3G 模块审核失败：{type(_e).__name__}: {_e}")
            # 修 typo：state 字段是 milestones 不是 volume_milestones
            mark_phase_done_if("3G", self.state,
                lambda s: bool(s.protagonist_journey.overall_theme) and bool(s.protagonist_journey.milestones),
                on_skip_msg="主角整体弧线 overall_theme 或卷级 milestones 列表为空")
        else:
            print("  ✓ [跳过] Phase 3-G 主角历程已完成")

        # ⏸ 阶段组 4：情节完成
        self._stepwise_checkpoint("G4_plot", "情节（叙事线/爽点/伏笔/反转/节奏/情绪/主角历程）")

        self._print_full_overview()

        # 全规划完成后做一次 SSoT 一致性检查
        _section("SSoT 一致性检查")
        invariants.print_report(self.state)
        # 快照"全规划完成"节点
        version_control.snapshot(self.state, label="phase_all_planning_done", phase="planning")

        # ⏸ 阶段组 5：框架全部就绪，进入逐卷章节规划+写作
        # 注意：第一卷的 stage/beats/outline/ctp 会在 _write_volume 里设计，
        # 所以这里暂停的语义是"主框架完成，准备开始逐章写作"
        self._stepwise_checkpoint("G5_framework_ready", "框架就绪（主框架完成，即将进入章节写作）")

        # stepwise 模式下还要拦截章节循环——不自动写章，等用户逐章触发
        from project_mgmt import project_manager as _pm
        if _pm.get_mode(project_context.current()) == "stepwise":
            print("\n✓ [stepwise] 框架全部规划完成。")
            print("  章节写作请去 web UI 的「写下一章」卡片，逐章触发。")
            save_state(self.state)
            self._set_current_step(
                phase="✓ 框架就绪",
                agent="stepwise",
                detail="主框架全部完成——逐章触发写作",
            )
            try:
                os.remove(project_context.pid_file())
            except OSError:
                pass
            return

        # ── Phase 4+5: 逐卷写作 ────────────────────────
        _section("Phase 4+5: 开始写作")
        for volume_index in range(1, NUM_VOLUMES + 1):
            self._write_volume(volume_index)

        # ── 收尾 ───────────────────────────────────────
        self._compile_novel()
        self._save("memory_report.json", self._dump_memory_report())
        print(print(get_foreshadow_status_report(self.state)))
        _banner(f"《{self.state.title}》创作完成！→ {project_context.project_dir()}/")

        # 清 PID 文件 + progress_status（项目状态变回 idle）
        try:
            os.remove(project_context.pid_file())
        except OSError:
            pass
        self._clear_current_step()

    # ═══════════════════════════════════════════════════
    #  卷级写作
    # ═══════════════════════════════════════════════════

    def prepare_volume_planning(self, volume_index: int):
        """
        为某一卷跑完 4 个 pre-chapter-writing 规划 phase：
          · 叙事舞台设计
          · 主角舞台节拍
          · 章节大纲
          · 章节类型规划
        可被 _write_volume（自动模式）和 write_next_chapter（逐章模式）共用。
        所有 phase 已完成就直接跳过。
        """
        vol = self.state.get_volume(volume_index)
        if not vol:
            return

        # Phase 4A: 本卷叙事舞台设计
        stage_phase = f"4_stage_{volume_index}"
        stage_regenerated = False
        if not is_phase_done(stage_phase, self._progress):
            _section(f"第{volume_index}卷《{vol.title}》叙事舞台设计")
            design_volume_stages(self.state, volume_index)
            stage_regenerated = True
            vol.chapter_outlines = []
            self._save(f"vol{volume_index:02d}_stages.json", self._dump_stages(volume_index))
            mark_phase_done_if(stage_phase, self.state,
                lambda s: any(st.volume == volume_index for st in s.story_stages),
                on_skip_msg=f"第{volume_index}卷未生成任何舞台")
        else:
            print(f"  ✓ [跳过] 第{volume_index}卷叙事舞台已设计")

        # Phase 4A-2: 本卷主角舞台节拍（依赖本卷舞台已生成）
        beat_phase = f"4_beats_{volume_index}"
        if not is_phase_done(beat_phase, self._progress):
            vol_stage_ids = [st.stage_id for st in self.state.story_stages if st.volume == volume_index]
            if not vol_stage_ids:
                print(f"  ⚠ 第{volume_index}卷无舞台——跳过 beats 规划，下次会重跑")
            else:
                _beats_for_volume(self.state, volume_index)
                self._save("protagonist_journey.json", self._dump_protagonist_journey())
                mark_phase_done_if(beat_phase, self.state,
                    lambda s: any(b.stage_id in vol_stage_ids for b in s.protagonist_journey.stage_beats),
                    on_skip_msg=f"第{volume_index}卷未生成任何舞台节拍")
        else:
            print(f"  ✓ [跳过] 第{volume_index}卷舞台节拍已规划")

        # Phase 4B: 本卷逐章大纲
        outline_phase = f"4_vol{volume_index}"
        outline_report = validate_volume_outline_structure(self.state, volume_index)
        outline_done = is_phase_done(outline_phase, self._progress)
        outline_needs_replan = stage_regenerated or (not outline_done) or (not outline_report.get("ok"))
        if outline_needs_replan:
            if outline_done and not stage_regenerated:
                print(f"  ⚠ 第{volume_index}卷章节大纲结构已过期，自动重建：{outline_report}")
            elif stage_regenerated and outline_done:
                print(f"  ℹ 第{volume_index}卷舞台刚重建，自动同步重建章节大纲")
            _section(f"第{volume_index}卷《{vol.title}》章节大纲规划")
            plan_volume_chapters(self.state, volume_index)
            mark_phase_done_if(outline_phase, self.state,
                lambda s: bool(getattr(vol, "chapter_outlines", None)),
                on_skip_msg=f"第{volume_index}卷章节大纲为空")
        else:
            print(f"  ✓ [跳过] 第{volume_index}卷章节大纲已完成")

        # Phase 4C: 本卷章节类型规划
        ctp_phase = f"4_ctp_{volume_index}"
        if not is_phase_done(ctp_phase, self._progress):
            _section(f"第{volume_index}卷章节类型规划")
            plan_chapter_types(self.state, volume_index)
            mark_phase_done_if(ctp_phase, self.state,
                lambda s: any(p.volume == volume_index and p.per_chapter for p in s.chapter_type_plans),
                on_skip_msg=f"第{volume_index}卷章节类型分布为空")
        else:
            print(f"  ✓ [跳过] 第{volume_index}卷章节类型已规划")

        # Phase 4D: 把本卷粗粒度 lifecycle 节点（target_chapter=0）细化到具体章
        # —— 落章后 SP 系统能正确触发本章爽点，ability_planner 能识别 must-use
        lifecycle_phase = f"4_lifecycle_{volume_index}"
        if not is_phase_done(lifecycle_phase, self._progress):
            try:
                from agents.ability_roadmap_planner import assign_chapter_to_lifecycle_nodes
                written = set(self._progress.get("chapters", []) or [])
                count = assign_chapter_to_lifecycle_nodes(self.state, volume_index, written)
                if count:
                    print(f"  ✓ 第{volume_index}卷 lifecycle 落章：{count} 个节点")
                mark_phase_done_if(
                    lifecycle_phase,
                    self.state,
                    lambda s: _volume_lifecycle_nodes_assigned(s, volume_index),
                    on_skip_msg=f"第{volume_index}卷 lifecycle 节点未落到具体章节",
                )
            except Exception as _e:
                print(f"  ⚠ lifecycle 落章失败（不阻塞）：{type(_e).__name__}: {_e}")
        else:
            print(f"  ✓ [跳过] 第{volume_index}卷 lifecycle 已落章")

    def _write_volume(self, volume_index: int):
        vol = self.state.get_volume(volume_index)
        if not vol:
            return

        self.state.current_volume_index = volume_index

        # HITL 关卡：每卷开始前要求人审（审核卷大纲）
        try:
            vol_summary = {
                "volume": volume_index,
                "title": vol.title,
                "theme": vol.theme,
                "arc": vol.arc,
                "structure_role": vol.structure_role,
                "purpose": vol.purpose,
                "expression": vol.expression,
                "antagonist": vol.volume_antagonist,
                "key_events": vol.key_events,
            }
            human_in_loop.gate_volume_start(
                self.state, volume_index, vol_summary, mode=HITL_MODE
            )
        except HITLPause as e:
            save_state(self.state)
            print(f"\n  {e}")
            raise SystemExit(0)

        # 本卷 4 个 pre-writing phase
        self.prepare_volume_planning(volume_index)

        # 检查本卷是否所有章节已完成 + 卷级审查已通过
        all_done = all(
            is_chapter_done(ci, self._progress)
            for ci in range(vol.chapter_start, vol.chapter_end + 1)
        )
        vol_review_passed = volume_index in (self.state.done_volume_review_indices or [])
        if all_done and vol_review_passed:
            print(f"  ✓ [跳过] 第{volume_index}卷全部章节已完成且通过卷级审查")
            return

        _section(f"第{volume_index}卷《{vol.title}》写作 [{vol.chapter_start}-{vol.chapter_end}章]")
        print(f"  主题：{vol.theme} | 主要对手：{vol.volume_antagonist}")
        print(f"  关键事件：{' / '.join(vol.key_events[:3])}")

        factions_in_vol = get_factions_for_volume(self.state, volume_index)
        if factions_in_vol and factions_in_vol != "本卷无特定势力重点。":
            print(f"  本卷势力：{factions_in_vol[:100]}")

        os.makedirs(f"{project_context.project_dir()}/vol{volume_index:02d}", exist_ok=True)

        # 按 stage 切批：每个 stage 写完所有章 → stage 级审查 → 修订循环 → 下一 stage
        stages = self.state.stages_in_volume(volume_index)
        if stages:
            for stage in stages:
                if stage.stage_id in (self.state.done_stage_ids or []):
                    print(f"  ✓ [跳过] Stage [{stage.name}] 已通过审查")
                    continue
                self._write_one_stage(volume_index, stage)
            # 处理 stage 未覆盖的 gap 章节（fallback 直写，不进 stage 审）
            covered = set()
            for st in stages:
                for ci in range(st.chapter_start, st.chapter_end + 1):
                    covered.add(ci)
            gap = [ci for ci in range(vol.chapter_start, vol.chapter_end + 1) if ci not in covered]
            for ci in gap:
                if is_chapter_done(ci, self._progress):
                    continue
                self._check_control_point()
                self.state.current_chapter_index = ci
                self._write_one_chapter(ci, volume_index)
        else:
            # 无 stage 设计——退回逐章直写
            print(f"  ⚠ 第{volume_index}卷未设计任何 stage，按章节直写（无 stage 级审查）")
            for chapter_index in range(vol.chapter_start, vol.chapter_end + 1):
                if is_chapter_done(chapter_index, self._progress):
                    print(f"  ✓ [跳过] 第{chapter_index}章已完成")
                    continue
                self._check_control_point()
                self.state.current_chapter_index = chapter_index
                self._write_one_chapter(chapter_index, volume_index)

        # 整卷写完——卷级审查 + 修订循环
        if not vol_review_passed:
            self._review_and_revise_volume(volume_index)

        self._save(f"vol{volume_index:02d}_summary.json", self._dump_volume_summary(volume_index))
        print(f"\n  ✓ 第{volume_index}卷完成")
        print(self.state.volume_progress_str())

    # ═══════════════════════════════════════════════════
    #  Stage / Volume 批次写作 + 审查
    # ═══════════════════════════════════════════════════

    def _write_one_stage(self, volume_index: int, stage):
        """
        写完一个 stage 内的所有章 → 跑 stage_reviewer → critical 触发指定章重写循环。
        通过审查后把 stage_id 写入 state.done_stage_ids。
        """
        from agents.stage_reviewer import review_stage
        from config import STAGE_REVIEW_MAX_REWRITE_ROUNDS

        role_tag = f"[{stage.structure_role}]" if stage.structure_role else ""
        _section(f"Stage 写作 · {stage.name}{role_tag} · Ch{stage.chapter_start}-{stage.chapter_end}")
        print(f"  使命：{stage.purpose[:50]} | 表达：{stage.expression[:40]}")

        # 写完该 stage 内的所有章——按 outline 归属精确取（避免与 parallel stage 重叠）
        own_chapters = self.state.chapters_in_stage(volume_index, stage.stage_id)
        for ci in own_chapters:
            if is_chapter_done(ci, self._progress):
                print(f"  ✓ [跳过] 第{ci}章已完成")
                continue
            self._check_control_point()
            self.state.current_volume_index = volume_index
            self.state.current_chapter_index = ci
            self._write_one_chapter(ci, volume_index)

        # Stage 级审查 + 修订循环
        iteration = 0
        while True:
            self._set_current_step(
                phase=f"Phase 6 · V{volume_index}",
                agent="StageReviewer",
                detail=f"Stage [{stage.name}] 第 {iteration + 1} 轮审查",
            )
            print(f"\n  ── Stage 审查 [{stage.name}] · 第 {iteration + 1} 轮")
            issues = review_stage(self.state, volume_index, stage.stage_id, iteration=iteration)
            self.state.stage_review_reports[stage.stage_id] = list(issues)
            self._save_meta_safely()

            critical = [i for i in issues if i.level == "critical"]
            major = [i for i in issues if i.level == "major"]
            minor = [i for i in issues if i.level == "minor"]
            print(f"     审查结果：critical={len(critical)} major={len(major)} minor={len(minor)}")

            if not critical:
                print(f"  ✓ Stage 审查通过")
                break
            if iteration >= STAGE_REVIEW_MAX_REWRITE_ROUNDS:
                print(f"  ⚠ Stage 审查达最大重写轮数（{STAGE_REVIEW_MAX_REWRITE_ROUNDS}），保留 {len(critical)} 条 critical 继续推进")
                break

            # 用本 stage 实际归属的章节端点，避免连锁扩展把 parallel stage 的章卷进来
            stage_chs = own_chapters or list(range(stage.chapter_start, stage.chapter_end + 1))
            ch2fb = self._collect_revise_feedback(critical, stage_chs[0], stage_chs[-1], source="Stage 审查")
            if not ch2fb:
                print(f"  ⚠ critical 问题未指明 affected_chapters，无法定位章节，跳出循环")
                break
            print(f"  ↻ 触发 {len(ch2fb)} 章重写：{sorted(ch2fb.keys())}")
            self._revise_chapters_with_feedback(ch2fb, volume_index)
            iteration += 1

        # 标记 stage 通过（即便有 major/minor 也通过，user 可后续手动复审）
        if stage.stage_id not in self.state.done_stage_ids:
            self.state.done_stage_ids.append(stage.stage_id)
        self._save_meta_safely()

    def _review_and_revise_volume(self, volume_index: int):
        """整卷写完后——跑 volume_reviewer → critical 触发指定章重写循环。"""
        from agents.volume_reviewer import review_volume
        from config import VOLUME_REVIEW_MAX_REWRITE_ROUNDS

        vol = self.state.get_volume(volume_index)
        if not vol:
            return

        iteration = 0
        while True:
            self._set_current_step(
                phase=f"Phase 7 · V{volume_index}",
                agent="VolumeReviewer",
                detail=f"卷级审查 第 {iteration + 1} 轮",
            )
            print(f"\n  ══ 卷级审查 V{volume_index}《{vol.title}》第 {iteration + 1} 轮")
            issues = review_volume(self.state, volume_index, iteration=iteration)
            self.state.volume_review_reports[volume_index] = list(issues)
            self._save_meta_safely()

            critical = [i for i in issues if i.level == "critical"]
            major = [i for i in issues if i.level == "major"]
            minor = [i for i in issues if i.level == "minor"]
            print(f"     审查结果：critical={len(critical)} major={len(major)} minor={len(minor)}")

            if not critical:
                print(f"  ✓ 卷级审查通过")
                break
            if iteration >= VOLUME_REVIEW_MAX_REWRITE_ROUNDS:
                print(f"  ⚠ 卷级审查达最大重写轮数（{VOLUME_REVIEW_MAX_REWRITE_ROUNDS}），保留 {len(critical)} 条 critical")
                break

            ch2fb = self._collect_revise_feedback(critical, vol.chapter_start, vol.chapter_end, source="卷级审查")
            if not ch2fb:
                print(f"  ⚠ critical 问题未指明 affected_chapters，跳出循环")
                break
            print(f"  ↻ 触发 {len(ch2fb)} 章重写：{sorted(ch2fb.keys())}")
            self._revise_chapters_with_feedback(ch2fb, volume_index)
            iteration += 1

        if volume_index not in self.state.done_volume_review_indices:
            self.state.done_volume_review_indices.append(volume_index)
        self._save_meta_safely()

    def _collect_revise_feedback(self, issues, ch_low: int, ch_high: int, source: str) -> dict:
        """
        把 critical issues 按章号聚合成重写反馈字符串。
        - A.2 fallback：issue.affected_chapters 为空时落到 ch_high（最后一章），保证总能定位到具体章节而不让审查白跑一轮。
        - A.1 连锁扩展：critical 命中最早章 m 后，把 m..ch_high 整段都纳入重写——后续章都基于"错的 m 章"派生，
                        必须重新出稿。扩展进来的章用通用反馈说明"前章重写过、本章基于新版本重新写"。
        """
        ch2fb = {}
        for issue in issues:
            targets = [ci for ci in (issue.affected_chapters or []) if ch_low <= ci <= ch_high]
            if not targets:
                # affected_chapters 缺位/越界——fallback 到该范围末尾一章
                targets = [ch_high]
            for ci in targets:
                line = f"【{source}·{issue.level}】{issue.issue}\n  → 修订建议：{issue.suggestion}"
                if ci in ch2fb:
                    ch2fb[ci] = ch2fb[ci] + "\n\n" + line
                else:
                    ch2fb[ci] = line

        if not ch2fb:
            return ch2fb

        # A.1：连锁扩展——从最早受影响章起，到 ch_high，全部重写
        ci_min = min(ch2fb.keys())
        cascade_note = (
            f"【{source}·连锁重写】前面第 {ci_min} 章起已根据审查反馈重写，本章原稿基于的派生状态\n"
            "已被清理。请基于上一章重写后的新版本（新事件/新情绪/新伏笔状态）重新出稿——\n"
            "保留原蓝图的核心走向，但任何与上一章新版本不一致的细节、情绪余波、对白引用都要更新。\n"
            "如果原稿走向因前章变化而失去铺垫支撑，要主动调整本章的关键转折/情绪/对白以适配新前情。"
        )
        for ci in range(ci_min, ch_high + 1):
            if ci not in ch2fb:
                ch2fb[ci] = cascade_note
        return ch2fb

    def _revise_chapters_with_feedback(self, chapter_to_feedback: dict, volume_index: int):
        """对一组章节注入反馈并重写——清进度/清正文/清派生状态后调用 _write_one_chapter。"""
        from persistence.chapter_cleanup import cleanup_chapter_state
        from persistence.checkpoint import _save_progress, load_progress

        if not chapter_to_feedback:
            return

        # 1. 从 progress 移除这些章 → 让 is_chapter_done 返回 False
        progress = load_progress()
        progress["chapters"] = [c for c in progress["chapters"] if c not in chapter_to_feedback]
        _save_progress(progress)

        # 2. 删 txt
        for ci in sorted(chapter_to_feedback.keys()):
            path = f"{project_context.project_dir()}/vol{volume_index:02d}/chapter_{ci:04d}.txt"
            if os.path.exists(path):
                os.remove(path)

        # 3. 清按章派生的 state（memory/快照/伏笔等）
        cleanup_chapter_state(self.state, set(chapter_to_feedback.keys()))
        save_state(self.state)

        # 4. 把反馈挂到自身，逐章重写
        self._rewrite_feedback_for_chapter = dict(chapter_to_feedback)
        self._progress = load_progress()
        for ci in sorted(chapter_to_feedback.keys()):
            self.state.current_volume_index = volume_index
            self.state.current_chapter_index = ci
            self._write_one_chapter(ci, volume_index)
        self._rewrite_feedback_for_chapter = {}

    def _save_meta_safely(self):
        """便捷封装：增量保存 meta（stage/volume review 报告写入）。失败不抛。"""
        try:
            from persistence.checkpoint import save_state_section
            save_state_section(self.state, "meta")
        except Exception as e:
            print(f"  ⚠ 保存审查报告失败：{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════
    #  章节写作
    # ═══════════════════════════════════════════════════

    def _mark_chapter_as_draft(self, chapter_index: int, path: str, reason: str) -> None:
        """把已写盘的章节正稿降级为 .draft——任何"未通过定稿前最终校验"的路径都该走这个：
          1. 磁盘上 chapter_XXXX.txt → chapter_XXXX.txt.draft（旧 .draft 先删避免冲突）
          2. 从 state.completed_chapters 移除本章（process_chapter 提前 append 的会被 rollback）
          3. 持久化 state——重启后看到的就是"本章未完成"，会重写
          4. 写 progress_warning(error) 让 web UI 红字提示

        为什么不在这里 raise：caller 已经准备好抛 ChapterCanonBlockedError /
        ExternalAIResolutionError 等"语义化异常"，本方法只做"状态清洗"。
        """
        try:
            if os.path.exists(path):
                draft_path = path + ".draft"
                if os.path.exists(draft_path):
                    os.remove(draft_path)
                os.rename(path, draft_path)
                print(f"  ❌ 章节正稿 rename → {os.path.basename(draft_path)}（原因：{reason[:60]}）")
        except Exception as _re:
            print(f"  ⚠ rename 草稿失败：{type(_re).__name__}: {_re}")
        self.state.completed_chapters = [
            c for c in self.state.completed_chapters if c.index != chapter_index
        ]
        try:
            save_state(self.state)
        except Exception as _se:
            print(f"  ⚠ rollback 后 save_state 失败：{type(_se).__name__}: {_se}")
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="error",
                source=f"chapter:{chapter_index}:draft",
                message=f"第 {chapter_index} 章未通过定稿前校验，已降级为草稿——{reason}",
            )
        except Exception as _we:
            print(f"  ⚠ 写 progress_warning 失败：{type(_we).__name__}: {_we}")

    def _write_one_chapter(self, chapter_index: int, volume_index: int):
        # 每章入口重置——避免上一章的 canon 阻塞状态泄漏到本章
        self._canon_critical_blocked_info = None
        self._set_current_step(
            phase=f"Phase 5 · V{volume_index}",
            agent="Director",
            detail=f"生成第 {chapter_index} 章指令",
            chapter_index=chapter_index,
        )
        # 生成完整章节指令
        directive = self._generate_directive(chapter_index, volume_index)
        outline = self._get_outline(chapter_index, volume_index)

        vol = self.state.get_volume(volume_index)
        local = chapter_index - vol.chapter_start + 1 if vol else chapter_index
        path = f"{project_context.project_dir()}/vol{volume_index:02d}/chapter_{chapter_index:04d}.txt"

        # 场景蓝图（ChapterPlannerAgent）
        self._set_current_step(agent="ChapterPlanner", detail=f"第 {chapter_index} 章 场景蓝图",
                                chapter_index=chapter_index)
        blueprint = build_chapter_blueprint(
            self.state, directive,
            outline_goal=outline.get("goal", "继续推进故事"),
            total_words=WORDS_PER_CHAPTER,
        )
        directive.blueprint = blueprint
        scenes = len(blueprint.scene_beats)

        # ── 写章前的"本章计划摘要"——一段人类可读的预审清单 ──
        ch_type = getattr(directive, "chapter_type", "") or ""
        role_tag = f"[{directive.structure_role}]" if directive.structure_role else ""
        vol_progress_pct = int(100 * (local / max(vol.total_chapters, 1))) if vol else 0
        title_hint = (outline.get("title") or "").strip()
        print()
        print(f"  ╔═══ 第 {chapter_index} 章 · 本章计划摘要 ═══")
        print(f"  ║ 位置：V{volume_index}C{local:03d}/全书{chapter_index} · 本卷进度 {vol_progress_pct}%"
              + (f" · 类型 {ch_type}" if ch_type else ""))
        if title_hint:
            print(f"  ║ 拟用标题：{title_hint}")
        print(f"  ║ 节奏：张力={directive.tension.value} 节奏={directive.rhythm.value} 位置={directive.chapter_position} 字数预算≈{WORDS_PER_CHAPTER}")
        print(f"  ║ 主线：{directive.primary_line}")
        print(f"  ║ 大纲目标：{outline.get('goal','')[:80]}")
        if directive.purpose:
            print(f"  ║ 结构{role_tag} purpose：{directive.purpose[:60]}")
        if directive.expression:
            print(f"  ║ 想让读者感受：{directive.expression[:50]}")
        print(f"  ║ 本章变化(delta)：{blueprint.chapter_delta[:80]}")
        if blueprint.scene_beats:
            print(f"  ║ {scenes} 个场景：")
            for i, beat in enumerate(blueprint.scene_beats[:5], 1):
                bp = (getattr(beat, "purpose", "") or "")[:40]
                bw = getattr(beat, "word_quota", 0)
                print(f"  ║   {i}. [{bw}字] {bp}")
        if directive.satisfaction_points:
            print(f"  ║ 必含爽点：{directive.satisfaction_points}")
        if directive.foreshadow_plant:
            print(f"  ║ 必植伏笔：{directive.foreshadow_plant}")
        if directive.foreshadow_resolve:
            print(f"  ║ 必兑伏笔：{directive.foreshadow_resolve}")
        if getattr(directive, "twist_reveals", None):
            print(f"  ║ 反转揭露：{directive.twist_reveals}")
        if getattr(directive, "red_herring_plant", None) or getattr(directive, "red_herring_debunk", None):
            print(f"  ║ 红鲱鱼：植 {directive.red_herring_plant} / 揭 {directive.red_herring_debunk}")
        print(f"  ║ 章末钩子：{blueprint.closing_hook[:60]}")
        # ── 能力使用规划（写章前自审）──
        try:
            from agents.ability_planner import plan_chapter_abilities
            self._set_current_step(agent="AbilityPlanner",
                                    detail=f"第 {chapter_index} 章 能力使用规划",
                                    chapter_index=chapter_index)
            ability_plan = plan_chapter_abilities(self.state, directive)
            directive.ability_plan = ability_plan
            print(f"  ║ 能力规划：{ability_plan.summary}")
            if ability_plan.should_use:
                for it in ability_plan.items[:3]:
                    print(f"  ║   · 《{it.ability_name}》@ {it.when_to_use[:15]} | 代价：{it.cost_to_pay[:30]}")
        except Exception as _e:
            print(f"  ║ 能力规划失败（不影响写章）：{type(_e).__name__}: {_e}")
        print(f"  ╚════════════════════════════════")

        # 读取上章末尾原文（文字级接续）
        prev_tail = self._get_prev_chapter_tail(chapter_index, volume_index)

        # 写初稿
        self._set_current_step(agent="Writer", detail=f"第 {chapter_index} 章 正文写作",
                                chapter_index=chapter_index)
        draft = write_chapter(self.state, directive, WORDS_PER_CHAPTER, prev_tail=prev_tail)
        print(f"  ✓ 初稿 {len(draft)} 字")

        # ── 真 AI 接入：扫描 [[ASK_AI:能力名|问题]] 占位，用绑定的真 LLM 回答替换 ──
        try:
            from agents.external_ai_query import (
                ExternalAIResolutionError,
                resolve_asks_in_chapter,
                find_asks,
            )
            asks = find_asks(draft)
            if asks:
                self._set_current_step(agent="ExternalAIQuery",
                                        detail=f"第 {chapter_index} 章 调真 AI 替换 {len(asks)} 个占位",
                                        chapter_index=chapter_index)
                print(f"  🔌 检测到 {len(asks)} 处主角问真 AI 的占位——开始真发问询")
                draft, ask_reports = resolve_asks_in_chapter(self.state, draft, asks=asks)
                # 收集"AI 元语言"命中，写 progress warning（不阻塞）——
                # 这是 canon_checker 抓不到的另一类违规：AI 在回答里说
                # "我是一个 AI"/"我的训练数据"/"现代社会"等，会破坏穿越/古风沉浸感。
                ai_meta_warnings: list[str] = []
                for r in ask_reports:
                    meta_hits = r.get("meta_hits", []) or []
                    meta_tag = (
                        f"  ⚠ 含 AI 元语言: {'/'.join(meta_hits[:3])}" if meta_hits else ""
                    )
                    print(f"    ✓ 《{r['ability']}》→ {r['profile']}: {r['question'][:30]}... "
                          f"→ 回答 {len(r['answer'])} 字{meta_tag}")
                    if meta_hits:
                        ai_meta_warnings.append(
                            f"《{r['ability']}》「{r['question'][:25]}…」答中含 "
                            f"{'/'.join(meta_hits[:3])}"
                        )
                print(f"  ✓ 占位替换完成，正文 {len(draft)} 字符")
                if ai_meta_warnings:
                    try:
                        from persistence.checkpoint import add_progress_warning
                        add_progress_warning(
                            level="warn",
                            source=f"chapter:{chapter_index}:ai_meta",
                            message=(
                                f"第 {chapter_index} 章 {len(ai_meta_warnings)} 处真 AI 回答含"
                                f"元语言/免责声明/时空错乱词（破坏沉浸感）："
                                + "；".join(ai_meta_warnings[:3])
                                + "（建议人工检查回答或调整提问让 AI 别走元路径）"
                            ),
                        )
                    except Exception as _e_w:
                        print(f"  ⚠ 写 progress warning 失败：{type(_e_w).__name__}: {_e_w}")
        except ExternalAIResolutionError as _e:
            print(f"  ❌ 真 AI 占位替换失败，本章不定稿：{_e}")
            try:
                from persistence.checkpoint import add_progress_warning
                add_progress_warning(
                    level="error",
                    source=f"chapter:{chapter_index}:external_ai",
                    message=(
                        f"第 {chapter_index} 章真 AI 占位替换失败，已阻断定稿：{str(_e)[:180]}。"
                        "请检查 user_models.json 中对应 external_llm_profile 的配置，"
                        "或先移除/改写本章占位后重写。"
                    ),
                )
            except Exception as _e_w:
                print(f"  ⚠ 写 progress warning 失败：{type(_e_w).__name__}: {_e_w}")
            raise
        except Exception as _e:
            print(f"  ❌ 真 AI 占位替换异常，本章不定稿：{type(_e).__name__}: {_e}")
            try:
                from persistence.checkpoint import add_progress_warning
                add_progress_warning(
                    level="error",
                    source=f"chapter:{chapter_index}:external_ai",
                    message=(
                        f"第 {chapter_index} 章真 AI 占位替换异常，已阻断定稿："
                        f"{type(_e).__name__}: {str(_e)[:160]}"
                    ),
                )
            except Exception as _e_w:
                print(f"  ⚠ 写 progress warning 失败：{type(_e_w).__name__}: {_e_w}")
            raise

        # ── Phase 5 前置校验（Writer 之后，Critic 之前）──
        # 1. 连续性校验（硬事实/设定/因果）
        self._set_current_step(agent="ContinuityChecker", detail=f"第 {chapter_index} 章 连续性校验",
                                chapter_index=chapter_index)
        continuity = check_continuity(self.state, directive, draft)
        if continuity.get("has_issues"):
            sev = continuity.get("severity", "minor")
            issues = continuity.get("issues", [])
            print(f"  ⚠ 连续性[{sev}]：{len(issues)} 个问题")
            for i in issues[:2]:
                print(f"      · [{i.get('type','')}/{i.get('severity','')}] {i.get('description','')[:50]}")
        # 2. 口吻校验（对话/心理是否像该角色）
        self._set_current_step(agent="VoiceChecker", detail=f"第 {chapter_index} 章 角色口吻校验",
                                chapter_index=chapter_index)
        voice_check = check_voice_consistency(self.state, directive, draft)
        if voice_check.get("has_issues"):
            voice_issues = voice_check.get("issues", [])
            if voice_issues:
                print(f"  ⚠ 口吻：{len(voice_issues)} 个角色有偏差")
                for vi in voice_issues[:2]:
                    print(f"      · {vi.get('character','')}(评分{vi.get('score','?')})")

        # 组合额外 feedback 用于 revise（如果 critic 判定不通过会拼进去）
        extra_feedback_parts = []
        if continuity.get("has_issues"):
            for i in continuity.get("issues", [])[:3]:
                extra_feedback_parts.append(f"[连续性/{i.get('type','')}] {i.get('description','')}→{i.get('suggested_fix','')}")
        if voice_check.get("has_issues"):
            for vi in voice_check.get("issues", [])[:3]:
                problems = "; ".join(vi.get("problems", [])[:2])
                extra_feedback_parts.append(f"[口吻/{vi.get('character','')}] {problems}")
        extra_feedback = "\n".join(extra_feedback_parts)

        # 审校循环 —— 切到 core.revise_loop.run_revise_loop 统一框架
        from core.revise_loop import ReviseConfig, run_revise_loop
        from agents.chapter_dispatcher import OPENING_CHAPTER_THRESHOLD as _OPEN_TH

        _audit_round = [0]
        _extra_feedback_box = [extra_feedback]

        def _critic_audit(s, ci, t):
            _audit_round[0] += 1
            rnd = _audit_round[0]
            self._set_current_step(agent="Critic", detail=f"第 {ci} 章 审校·第 {rnd} 轮",
                                    chapter_index=ci)
            review = review_chapter(s, directive, t)
            score = review.get("score", 0)
            passed = review.get("passed", False)
            dim = review.get("dim_scores", {})
            pe = dim.get("purpose_expression", "?")
            pe_str = "—" if pe == -1 else str(pe)
            print(f"  审校{rnd}: {score}/10 "
                  f"[叙事{dim.get('narrative','?')} 张力{dim.get('tension','?')} "
                  f"角色{dim.get('character','?')} 钩子{dim.get('hook','?')} "
                  f"结构{dim.get('structure','?')} 主角{dim.get('protagonist_centric','?')} "
                  f"表达{pe_str} 细腻{dim.get('delicacy','?')} 戏剧{dim.get('drama','?')} "
                  f"文风{dim.get('tone_compliance','?')}] 通过={passed}")
            sc = review.get("structure_check", "")
            pc = review.get("protagonist_check", "")
            hls = review.get("highlights", [])
            if sc and sc != "到位":
                print(f"    ⚠ 结构：{sc[:60]}")
            if pc and pc != "到位":
                print(f"    ⚠ 主角：{pc[:60]}")
            if hls:
                print(f"    ★ 亮点：{hls[0][:60]}")
            return review

        def _critic_needs_revise(review):
            passed = review.get("passed", False)
            dim = review.get("dim_scores", {})
            force_reasons = []
            # 首次 audit 时,连续性 critical 强制 revise
            if _audit_round[0] == 1 and continuity.get("severity") in ("major", "critical"):
                force_reasons.append(f"连续性{continuity.get('severity')}")
            # 开篇前 N 章专项门槛:钩子/代入/结构必须都 ≥ 8
            if chapter_index <= _OPEN_TH:
                hook_score = dim.get("hook", 0)
                char_score = dim.get("character", 0)
                struct_score = dim.get("structure", 0)
                opening_failures = []
                if isinstance(hook_score, (int, float)) and hook_score < 8:
                    opening_failures.append(f"钩子 {hook_score}<8")
                if isinstance(char_score, (int, float)) and char_score < 8:
                    opening_failures.append(f"代入 {char_score}<8")
                if isinstance(struct_score, (int, float)) and struct_score < 8:
                    opening_failures.append(f"结构 {struct_score}<8")
                if opening_failures:
                    force_reasons.append(f"开篇专项[{ '/'.join(opening_failures)}]")
                    _extra_feedback_box[0] = (_extra_feedback_box[0] + "\n" if _extra_feedback_box[0] else "") + (
                        f"[开篇章前 {_OPEN_TH} 章专项要求] 钩子/代入/结构都必须 ≥ 8——本章某项不达标："
                        f"{'/'.join(opening_failures)}。修订时优先:把第一幕的情绪锚点收紧、"
                        f"让读者更早对主角产生'想跟下去'的情感,章末钩子做得更锐"
                    )

            # Batch 4: 黄金三章(卷 1 章 1/2/3)在开篇 ≥8 基础上加专项硬约束
            # 决定本书前期留存的关键 — 首句勾人 / 小爽 / 大爽 + 拍案级钩子
            if directive.volume_index == 1 and chapter_index in (1, 2, 3):
                golden_extra = []
                if chapter_index == 1:
                    # 第 1 章:hook + character 必须 ≥ 9(首句勾人 + 主角代入)
                    if isinstance(hook_score, (int, float)) and hook_score < 9:
                        golden_extra.append(f"黄金1·钩子 {hook_score}<9")
                    if isinstance(char_score, (int, float)) and char_score < 9:
                        golden_extra.append(f"黄金1·代入 {char_score}<9")
                elif chapter_index == 2:
                    # 第 2 章:drama ≥ 8(第一个小爽必须落地) + sp_check 不为"未触发"
                    drama_score = dim.get("drama", 0)
                    if isinstance(drama_score, (int, float)) and drama_score < 8:
                        golden_extra.append(f"黄金2·戏剧 {drama_score}<8")
                    if review.get("sp_check") == "未触发":
                        golden_extra.append("黄金2·小爽未触发")
                elif chapter_index == 3:
                    # 第 3 章:hook ≥ 9(拍案级) + 必有大爽(sp_check 不为"未触发")
                    if isinstance(hook_score, (int, float)) and hook_score < 9:
                        golden_extra.append(f"黄金3·钩子 {hook_score}<9")
                    if review.get("sp_check") == "未触发":
                        golden_extra.append("黄金3·大爽未触发")
                if golden_extra:
                    force_reasons.append(f"黄金三章[{'/'.join(golden_extra)}]")
                    _extra_feedback_box[0] = (_extra_feedback_box[0] + "\n" if _extra_feedback_box[0] else "") + (
                        f"[黄金三章专项要求] {'/'.join(golden_extra)}——网文 80% 前期决生死的位置,"
                        f"必须比一般开篇章更严格。第 1 章重首句勾人和主角代入;第 2 章重小爽兑现;"
                        f"第 3 章重拍案级钩子和大爽。修订时聚焦本章对应硬约束。"
                    )
            if force_reasons:
                print(f"  ⚠ 强制重修：{' + '.join(force_reasons)}")
            return (not passed) or bool(force_reasons)

        def _critic_feedback(review, round_idx):
            issues = review.get("issues", [])
            if issues:
                print(f"  ▶ {issues[0][:60]}")
            fb = review.get("feedback", "请改善章节整体质量")
            if round_idx == 1 and _extra_feedback_box[0]:
                fb = fb + "\n\n[额外校验反馈]\n" + _extra_feedback_box[0]
                _extra_feedback_box[0] = ""  # 只第一轮注入一次
            return fb

        def _critic_revise(s, d, t, fb):
            new = revise_chapter(s, d, t, fb)
            print(f"  ✓ 修改后 {len(new)} 字")
            return new

        critic_result = run_revise_loop(
            state=self.state, chapter_index=chapter_index, directive=directive,
            config=ReviseConfig(
                label="critic-revise",
                audit_fn=_critic_audit,
                needs_revise=_critic_needs_revise,
                feedback_builder=_critic_feedback,
                revise_fn=_critic_revise,
                max_rounds=MAX_REVISION_ROUNDS,
                min_length_ratio=0.5,  # critic-revise 偏宽松,避免误丢长度变化
                chapter_path="",        # critic 阶段不写盘 (summary 也还没创建)
                update_word_count=False,
            ),
            initial_text=draft,
        )
        final = critic_result.final_text

        # ── 设定合规审核（独立 LLM，Gemini Flash Lite）──────────────
        # critic 管"文学质量"；setup_reviewer 管"是否符合设定"——两层正交
        # 【设计原则】审核员只给修改建议，由 revise_chapter 做局部小修——
        #   不触发整章重写、无阈值判断、最多跑一轮
        from agents.setup_reviewer import (
            review_chapter as setup_review_chapter,
            format_rewrite_feedback as reviewer_format_feedback,
        )
        self._set_current_step(
            agent="SetupReviewer",
            detail=f"第 {chapter_index} 章 设定合规审核",
            chapter_index=chapter_index,
        )
        review_result = setup_review_chapter(self.state, directive, final)
        score = review_result.get("overall_score", 10)
        verdict = review_result.get("verdict", "pass")
        issues = review_result.get("issues", [])
        review_failed = bool(review_result.get("review_failed"))
        print(f"  [审核] 合规分={score}/10｜判定={verdict}｜问题={len(issues)}"
              + ("｜⚠ 审核服务故障" if review_failed else ""))
        critical_issues = [i for i in issues if i.get("severity") == "critical"]
        for ic in critical_issues[:3]:
            print(f"    ! [critical·{ic.get('category','?')}] {ic.get('description','')[:70]}")
        for im in [i for i in issues if i.get("severity") == "major"][:2]:
            print(f"    · [major·{im.get('category','?')}] {im.get('description','')[:70]}")
        if review_result.get("reviewer_note"):
            print(f"    审核评语：{review_result['reviewer_note'][:60]}")
        # critical 问题写到 progress warning，让用户能在 web UI 看到（不阻塞写盘）
        if critical_issues:
            from persistence.checkpoint import add_progress_warning
            preview = "；".join(
                f"{i.get('category','?')}: {i.get('description','')[:40]}"
                for i in critical_issues[:3]
            )
            add_progress_warning(
                level="error",
                source=f"chapter:{chapter_index}",
                message=f"第 {chapter_index} 章合规审核发现 {len(critical_issues)} 个 critical 问题：{preview}",
            )
        # 审核服务本身故障——单独写一条 error warning（之前默认 pass 兜底吞掉了这个信号）
        if review_failed:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="error",
                source=f"chapter:{chapter_index}:reviewer",
                message=(
                    f"第 {chapter_index} 章 setup_reviewer 服务故障（{review_result.get('review_failed_reason','')[:80]}）"
                    "——本章未通过 LLM 合规审核（lifecycle 兜底仍生效）；请检查审核模型/key 后重写本章"
                ),
            )

        # 有问题就做局部修 —— 切到 core.revise_loop 统一框架
        # 退出语义:首次 audit 看 critical+major 是否触发;后续轮次只看 critical 残留
        # (审核服务故障时 needs_revise=False,直接退出——服务故障不是章节内容问题,瞎修无意义)
        _setup_audit_round = [1]  # 已经审过一次了 (review_result 是初次审)

        def _setup_audit(s, ci, t):
            _setup_audit_round[0] += 1
            rnd = _setup_audit_round[0]
            review = setup_review_chapter(s, directive, t)
            _issues = review.get("issues", []) or []
            _critical = sum(1 for i in _issues if i.get("severity") == "critical")
            _major = sum(1 for i in _issues if i.get("severity") == "major")
            print(f"    [审核] 第 {rnd - 1} 轮后 critical={_critical} major={_major}")
            return review

        def _setup_needs_revise(review):
            if review.get("review_failed"):
                return False
            _issues = review.get("issues", []) or []
            if _setup_audit_round[0] == 1:
                return any(i.get("severity") in ("critical", "major") for i in _issues)
            return any(i.get("severity") == "critical" for i in _issues)

        def _setup_feedback(review, round_idx):
            _issues = review.get("issues", []) or []
            _significant = [i for i in _issues if i.get("severity") in ("critical", "major")]
            self._set_current_step(
                agent="Writer",
                detail=f"第 {chapter_index} 章 第{round_idx}轮局部修订",
                chapter_index=chapter_index,
            )
            print(f"  [审核] 第 {round_idx} 轮：{len(_significant)} 个显著问题——局部修订")
            return reviewer_format_feedback(review)

        def _setup_revise(s, d, t, fb):
            new = revise_chapter(s, d, t, fb)
            print(f"  ✓ 第 {_setup_audit_round[0]} 轮修订后 {len(new)} 字")
            return new

        setup_result = run_revise_loop(
            state=self.state, chapter_index=chapter_index, directive=directive,
            config=ReviseConfig(
                label="setup-revise",
                audit_fn=_setup_audit,
                needs_revise=_setup_needs_revise,
                feedback_builder=_setup_feedback,
                revise_fn=_setup_revise,
                max_rounds=2,
                min_length_ratio=0.5,
                chapter_path="",  # 还没写盘
                update_word_count=False,  # summary 还没创建
            ),
            initial_text=final,
            initial_audit=review_result,
        )
        final = setup_result.final_text

        # 记忆提取
        self._set_current_step(agent="Memory", detail=f"第 {chapter_index} 章 摘要/记忆提取",
                                chapter_index=chapter_index)
        summary = process_chapter(self.state, chapter_index, final)
        # Batch 3:同步 closing_hook_type 到 summary,critic 下章用本卷分布检查 hook 多样性
        try:
            bp = getattr(directive, "blueprint", None)
            hook_spec = getattr(bp, "closing_hook_spec", None) if bp else None
            if hook_spec is not None and hasattr(hook_spec.type, "value"):
                summary.closing_hook_type = hook_spec.type.value
        except Exception:
            pass
        # P2:把 critic 最后一轮 review 快照写到 summary(UI 可视化用)
        try:
            _last = getattr(critic_result, "last_audit", None)
            if isinstance(_last, dict):
                # 只存 UI 关心的字段,避免 state.json 膨胀
                summary.critic_review = {
                    "score":             _last.get("score", 0),
                    "passed":            _last.get("passed", False),
                    "dim_scores":        _last.get("dim_scores", {}) or {},
                    "sp_check":          _last.get("sp_check", ""),
                    "fw_check":          _last.get("fw_check", ""),
                    "structure_check":   _last.get("structure_check", ""),
                    "protagonist_check": _last.get("protagonist_check", ""),
                    "highlights":        list(_last.get("highlights") or [])[:3],
                    "issues":            list(_last.get("issues") or [])[:3],
                    "feedback":          (_last.get("feedback") or "")[:300],
                }
        except Exception:
            pass
        print(f"  ✓ [{summary.tension.value}] {summary.summary[:55]}...")

        # 状态集中回写（快照/关系/伏笔激活/世界事件）
        self._set_current_step(agent="StateUpdater", detail=f"第 {chapter_index} 章 状态回写",
                                chapter_index=chapter_index)
        update_state_after_chapter(self.state, chapter_index, volume_index, final, directive=directive)

        # 术语表更新（新专有名词入库，避免后续章节重名）
        self._set_current_step(agent="Glossary", detail=f"第 {chapter_index} 章 术语表更新",
                                chapter_index=chapter_index)
        update_glossary(self.state, chapter_index, final)

        # 设定护栏：确定性扫描本章是否引用了未定义的能力/地点/势力
        try:
            from agents.canon_checker import check_canon, format_canon_report
            canon_report = check_canon(self.state, chapter_index, final)
            report_text = format_canon_report(canon_report)
            if report_text:
                print(report_text)
            # 累积到 state 方便 web 端查询
            if not hasattr(self.state, "_canon_audit"):
                self.state._canon_audit = []
            self.state._canon_audit.append(canon_report)
            # ── 接通：canon issues 触发 revise（循环修订，残留 critical 写 progress warning）──
            #
            # 修订策略：最多 CANON_REVISE_MAX_ROUNDS 轮——
            #   · 每轮按当前残留 issues 重拼 feedback（writer 知道还剩哪些没改对）
            #   · 跑完再扫一次 canon——critical（error 级）清零就收工，否则下一轮
            #   · 跑满仍有 critical 残留 → 写 progress warning(level=error)，
            #     让 web UI 红字警示；不阻塞写盘（保留原稿等用户手动处理）
            #
            # 这套循环堵住的洞:原来只跑一轮 revise——LLM 一轮没修对就直接定稿，
            # external_ai_no_placeholder / 未定义术语 都可能漏过。
            CANON_REVISE_MAX_ROUNDS = 3

            def _sev(i): return i.get("severity") if isinstance(i, dict) else getattr(i, "severity", "")
            def _kind(i): return i.get("kind") if isinstance(i, dict) else getattr(i, "kind", "")
            def _term(i): return i.get("term", "") if isinstance(i, dict) else getattr(i, "term", "")
            def _ctx(i):  return i.get("context_snippet", "") if isinstance(i, dict) else getattr(i, "context_snippet", "")
            def _sug(i):  return i.get("suggestion", "") if isinstance(i, dict) else getattr(i, "suggestion", "")

            def _format_canon_feedback(severe_list, round_idx: int) -> str:
                """把当前轮残留的 severe issues 拼成 writer 能用的修订指令。"""
                placeholder_iss = [i for i in severe_list if _kind(i) == "external_ai_no_placeholder"]
                term_iss = [i for i in severe_list if _kind(i) != "external_ai_no_placeholder"]
                parts = []
                if round_idx > 1:
                    parts.append(f"[canon 修订·第 {round_idx} 轮] 上一轮修订后仍有违规——必须彻底修复：")
                if placeholder_iss:
                    parts.append("[占位符违规] 本章 writer 自己编了真 AI asset 的回答——必须改成占位符：")
                    for iss in placeholder_iss[:5]:
                        t, c, s = _term(iss), _ctx(iss), _sug(iss)
                        parts.append(
                            f"  - 《{t}》在「{c[:30]}…」处出现但未用占位符。\n"
                            f"    必须把「{t}说……/告诉……/浮现答案……」等编造内容，"
                            f"    改写成 [[ASK_AI:{t}|具体问题]] 占位形式：\n"
                            f"      ① 写主角的触发动作/疑问（不写答案）\n"
                            f"      ② 写 [[ASK_AI:{t}|具体问题文本]] 占位\n"
                            f"      ③ 写主角看到回答后的反应/思考\n"
                            f"    占位会在定稿前真发给 LLM 拿真实回答替换。详情：{s[:100]}"
                        )
                    parts.append("")
                if term_iss:
                    parts.append("[设定护栏] 本章引用了未在 canon 中定义的概念——必须替换或删除：")
                    for iss in term_iss[:5]:
                        parts.append(
                            f"  - [{_kind(iss)}] 未定义术语 '{_term(iss)}'"
                            f"（上下文：{_ctx(iss)[:30]}）→ {_sug(iss)[:80]}"
                        )
                    parts.append(
                        "修订要求：把上述未定义术语全部替换成 canon 中已存在的概念，"
                        "或删除整段；其余原文保留；不要新造任何术语/能力/地名。"
                    )
                return "\n".join(parts)

            issues = canon_report.get("issues", []) if isinstance(canon_report, dict) else []
            severe_issues = [i for i in issues if _sev(i) in ("warn", "error")]
            initial_severe = len(severe_issues)
            initial_critical = sum(1 for i in severe_issues if _sev(i) == "error")

            if initial_critical:
                print(
                    f"  [canon-revise] 触发设定护栏修订（critical={initial_critical}, "
                    f"warn={initial_severe - initial_critical}，最多 {CANON_REVISE_MAX_ROUNDS} 轮）"
                )
                # ── 切到通用 AuditReviseLoop 框架（A2 抽象）─────────
                # 把原本 130 行的"audit→拼 feedback→revise→长度兜底→写盘→重扫→重试"
                # 全部代理给 core.revise_loop.run_revise_loop——细节差异通过 callable 注入
                from core.revise_loop import ReviseConfig, run_revise_loop

                def _audit_canon(state, ci, text):
                    """重扫 canon 并把 issues 累计到 _canon_audit；返回 severe issues 列表。"""
                    rep = check_canon(state, ci, text)
                    state._canon_audit.append(rep)
                    return [i for i in (rep.get("issues", []) or []) if _sev(i) in ("warn", "error")]

                def _needs_revise(severe_list):
                    return any(_sev(i) == "error" for i in (severe_list or []))

                def _build_fb(severe_list, rnd):
                    return _format_canon_feedback(severe_list, rnd)

                def _revise(state, directive_, text, feedback):
                    self._set_current_step(
                        agent="Writer",
                        detail=f"第 {chapter_index} 章 canon 修订·第 {self._canon_revise_current_round} 轮",
                        chapter_index=chapter_index,
                    )
                    return revise_chapter(state, directive_, text, feedback)

                self._canon_revise_current_round = 0  # 给 _revise 显示用

                def _on_short(rnd, new_len, original_len, streak):
                    print(f"  ⚠ canon-revise 第 {rnd} 轮输出过短（{new_len}），"
                          f"丢弃此轮、保留上一版（连续 {streak} 次过短）")
                    if streak >= 2:
                        print(f"  ⚠ canon-revise 连续 {streak} 轮过短——LLM 可能异常，提前退出")

                def _on_round_done(rnd, before_severe, after_severe, _new_text):
                    self._canon_revise_current_round = rnd
                    crit_now = sum(1 for i in (after_severe or []) if _sev(i) == "error")
                    warn_now = sum(1 for i in (after_severe or []) if _sev(i) == "warn")
                    print(f"  [canon-revise·{rnd}] 修订后 critical={crit_now} warn={warn_now}（前 {initial_severe}）")

                def _on_residual(remaining_severe):
                    rem_crit = [i for i in (remaining_severe or []) if _sev(i) == "error"]
                    if not rem_crit:
                        return
                    preview = "；".join(
                        f"{_kind(i) or '?'}:{(_term(i) or '?')[:20]}"
                        for i in rem_crit[:5]
                    )
                    reason = (
                        f"canon-revise 跑满 {CANON_REVISE_MAX_ROUNDS} 轮后仍有 "
                        f"{len(rem_crit)} 处 critical 违规：{preview}"
                    )
                    # 状态清洗：rename .draft + rollback completed_chapters + save_state + warning
                    self._mark_chapter_as_draft(chapter_index, path, reason=reason)
                    # 标记 flag——try/except 外面会 raise ChapterCanonBlockedError 真 halt
                    self._canon_critical_blocked_info = {
                        "chapter_index": chapter_index,
                        "count": len(rem_crit),
                        "preview": preview,
                        "rounds": CANON_REVISE_MAX_ROUNDS,
                    }
                    print(f"  ❌ [canon-revise] 跑满后仍有 {len(rem_crit)} 处 critical——章节拒绝定稿，流程将 halt")

                cfg = ReviseConfig(
                    label="canon-revise",
                    audit_fn=_audit_canon,
                    needs_revise=_needs_revise,
                    feedback_builder=_build_fb,
                    revise_fn=_revise,
                    max_rounds=CANON_REVISE_MAX_ROUNDS,
                    min_length_ratio=0.7,
                    max_short_streak=2,
                    on_short=_on_short,
                    on_round_done=_on_round_done,
                    on_residual_critical=_on_residual,
                    chapter_path=path,
                    update_word_count=True,
                )
                result = run_revise_loop(
                    state=self.state, chapter_index=chapter_index,
                    directive=directive, config=cfg,
                    initial_text=final, initial_audit=severe_issues,
                )
                final = result.final_text
        except ChapterCanonBlockedError:
            # _on_residual 触发的真阻塞——直接传播，不要被外层 Exception catch 误吞
            raise
        except Exception as e:
            print(f"  ⚠ 设定护栏检查/修订失败（不影响主流程）：{type(e).__name__}: {e}")

        # canon-revise 跑满 critical → 已经 rename 草稿 + rollback completed_chapters，
        # 这里 raise 让整个写作链路 halt（不要继续敏感词/blueprint/mark_done，避免半定稿状态）
        if getattr(self, "_canon_critical_blocked_info", None):
            info = self._canon_critical_blocked_info
            self._canon_critical_blocked_info = None
            raise ChapterCanonBlockedError(
                f"第 {info['chapter_index']} 章 canon-revise 跑满 {info['rounds']} 轮仍有 "
                f"{info['count']} 处 critical 残留（{info['preview']}）——章节未定稿，"
                "请修复 outline/inspiration/canon 定义后再继续写作"
            )

        # 敏感词过滤（定稿前最后一步）—— 切到通用 AuditReviseLoop 框架
        sens_report = filter_and_report(final)
        if sens_report["severity"] in ("major", "critical"):
            print(f"  ⚠ 敏感词严重（{sens_report['total_remaining']}处）——触发清理修订")
            try:
                from core.revise_loop import ReviseConfig, run_revise_loop

                def _audit_sens(state, ci, text):
                    return filter_and_report(text)

                def _needs_revise_sens(rep):
                    return rep.get("severity") in ("major", "critical")

                def _build_fb_sens(rep, _rnd):
                    hits = rep.get("remaining_hits", []) or []
                    hit_summary = []
                    for h in hits[:10]:
                        if isinstance(h, dict):
                            hit_summary.append(f"  - {h.get('word','')} ({h.get('count',1)}次)")
                        else:
                            hit_summary.append(f"  - {h}")
                    return (
                        "[敏感词扫描] 本章被检测出严重敏感词——必须改写规避：\n"
                        f"严重度：{rep['severity']}，剩余 {rep.get('total_remaining', 0)} 处\n"
                        "命中清单：\n" + "\n".join(hit_summary) + "\n"
                        "修订要求：把上述敏感词改写成同义但更含蓄的表达（如：杀→送行/了断；"
                        "血→红；尸→骸 之类），情节/动作/情感节拍保持不变；其它部分原文保留。"
                    )

                def _revise_sens(state, directive_, text, feedback):
                    self._set_current_step(agent="Writer",
                                            detail=f"第 {chapter_index} 章 敏感词清理修订",
                                            chapter_index=chapter_index)
                    return revise_chapter(state, directive_, text, feedback)

                def _on_round_done_sens(_r, before, after, _new):
                    print(f"  [sensitive-revise] 修订后剩余 {after.get('total_remaining', 0)} 处"
                          f"（前 {before.get('total_remaining', 0)}）")

                cfg = ReviseConfig(
                    label="sensitive-revise",
                    audit_fn=_audit_sens,
                    needs_revise=_needs_revise_sens,
                    feedback_builder=_build_fb_sens,
                    revise_fn=_revise_sens,
                    max_rounds=1,                       # 敏感词修订单轮即可
                    min_length_ratio=0.7,
                    on_round_done=_on_round_done_sens,
                    chapter_path=path,
                    update_word_count=True,
                )
                # 初次 audit 已经在外层跑过——直接传入避免重复
                result = run_revise_loop(
                    state=self.state, chapter_index=chapter_index,
                    directive=directive, config=cfg,
                    initial_text=sens_report["final_content"],
                    initial_audit=sens_report,
                )
                # run_revise_loop 内部写盘后 final_text 是 revise_chapter 的输出（未走 filter）；
                # 拿出后再过一次 filter_and_report 自动替换剩余敏感词，作为最终定稿
                if result.rounds_accepted > 0:
                    final_after_filter = filter_and_report(result.final_text)["final_content"]
                    with open(path, "w", encoding="utf-8") as fpw:
                        fpw.write(final_after_filter)
                    final = final_after_filter
                else:
                    # 修订未被接受（如输出过短）——回退到初始自动替换的内容
                    print(f"  ⚠ sensitive-revise 输出过短或未触发，保留自动替换内容")
                    final = sens_report["final_content"]
            except Exception as _e:
                print(f"  ⚠ 敏感词清理修订失败：{type(_e).__name__}: {_e}")
                final = sens_report["final_content"]
        elif sens_report["severity"] != "none":
            msg = format_sensitive_report(sens_report)
            if msg:
                print(f"  敏感词：{msg}")
            final = sens_report["final_content"]
        else:
            final = sens_report["final_content"]

        # canon/setup/sensitive 修订都可能在初稿外部 AI 替换之后又生成新的 [[ASK_AI:...]]。
        # 最终保存前必须再扫一次；有剩余占位就真发问并替换，失败则阻断定稿。
        try:
            from agents.external_ai_query import (
                ExternalAIResolutionError,
                find_asks,
                resolve_asks_in_chapter,
            )
            remaining_asks = find_asks(final)
            if remaining_asks:
                self._set_current_step(agent="ExternalAI",
                                       detail=f"第 {chapter_index} 章 最终占位替换",
                                       chapter_index=chapter_index)
                print(f"  🔌 最终保存前发现 {len(remaining_asks)} 处 ASK_AI 占位——补跑真 AI 替换")
                final, ask_reports = resolve_asks_in_chapter(self.state, final, asks=remaining_asks)
                for r in ask_reports:
                    print(f"    ✓ 《{r['ability']}》→ {r['profile']}: {r['question'][:30]}... "
                          f"→ 回答 {len(r['answer'])} 字")
        except ExternalAIResolutionError as _e:
            # 真 AI 调用失败（429/超时/无可用 in_story 模型）——之前定稿流程写过的草稿
            # 磁盘上还在（含 [[ASK_AI:...]] 占位），rename .draft 避免被当成定稿，再把
            # completed_chapters 里的本章 rollback，保持和 canon-block 一致的语义
            self._mark_chapter_as_draft(chapter_index, path, reason=f"ASK_AI 替换失败：{_e}")
            raise
        except Exception as _e:
            print(f"  ❌ 最终 ASK_AI 占位替换异常，本章不定稿：{type(_e).__name__}: {_e}")
            self._mark_chapter_as_draft(chapter_index, path, reason=f"ASK_AI 占位替换异常：{type(_e).__name__}: {_e}")
            raise

        # HITL 关卡：主角跨大境界 / 主要角色死亡 / 主线伏笔回收
        self._maybe_hitl_gates(chapter_index, directive, summary)

        # 更新实时故事状态（ThreadTracker）——下章精确起点
        update_story_thread(self.state, chapter_index, final)
        thread = self.state.story_thread
        print(f"  ✓ 故事线索：{thread.protagonist_immediate_goal[:35]} | 开放循环×{len([l for l in thread.open_loops if not l.closed])}")

        # 保存当前定稿；章后审计若再修，会通过 _apply_revision_and_reaudit 覆写同一路径。
        with open(path, "w", encoding="utf-8") as f:
            f.write(final)

        # 笔触多样性指纹（写下章前作为 forbidden 注入 writer）
        try:
            from agents.style_diversity import record_chapter_signature, record_chapter_title
            record_chapter_signature(self.state, chapter_index, final)
            # 标题：从首行提取
            first_line = (final.split("\n", 1)[0] if final else "").strip()
            if first_line:
                record_chapter_title(self.state, chapter_index, first_line)
        except Exception as _e:
            print(f"  ⚠ 笔触指纹记录失败（不影响章节）：{type(_e).__name__}: {_e}")

        # 主角实力章级日志（下章 writer 知道主角"此刻"能调什么、近期是否升级）
        try:
            from persistence.state import CharacterRole
            proto = next((c for c in self.state.characters if c.role == CharacterRole.PROTAGONIST), None)
            if proto:
                snap = self.state.latest_state_snapshot(proto.name) if hasattr(self.state, "latest_state_snapshot") else None
                cur_realm = (snap.realm if snap else "") or (proto.realm or "")
                audit = self.state.ability_audits.get(chapter_index)
                key_means = []
                if audit:
                    for u in (audit.ability_uses or [])[:3]:
                        if u.ability_name:
                            key_means.append(u.ability_name)
                prev_log = self.state.protagonist_power_log.get(chapter_index - 1, {})
                breakthrough = ""
                if prev_log.get("realm") and cur_realm and prev_log["realm"] != cur_realm:
                    breakthrough = f"从{prev_log['realm']}→{cur_realm}"
                self.state.protagonist_power_log[chapter_index] = {
                    "realm": cur_realm,
                    "key_means": key_means,
                    "recent_breakthrough": breakthrough,
                }
        except Exception as _e:
            print(f"  ⚠ 主角实力日志失败（不影响章节）：{type(_e).__name__}: {_e}")

        # 章后能力审计 —— 金手指/技能使用合理性
        # 不阻塞主流程：失败只打印，不影响章节保存
        try:
            from agents.ability_auditor import audit_chapter
            self._set_current_step(agent="AbilityAuditor",
                                    detail=f"第 {chapter_index} 章 能力使用审计",
                                    chapter_index=chapter_index)
            audit = audit_chapter(self.state, chapter_index, final)
            if audit is not None:
                self.state.ability_audits[chapter_index] = audit
                if audit.issues:
                    sev_count = {"critical": 0, "major": 0, "minor": 0}
                    for iss in audit.issues:
                        sev_count[iss.severity] = sev_count.get(iss.severity, 0) + 1
                    print(f"  ⚠ 能力审计：score={audit.overall_score} "
                          f"critical={sev_count['critical']} major={sev_count['major']} "
                          f"minor={sev_count['minor']} — {audit.summary[:40]}")
                    # ── 接通 chapter_polisher：critical/major 触发定向修订 ──
                    serious_count = sev_count["critical"] + sev_count["major"]
                    if serious_count > 0:
                        try:
                            from agents.chapter_polisher import build_polish_messages
                            from llm_layer.llm import chat as _chat
                            msgs = build_polish_messages(self.state, chapter_index, final, audit)
                            if msgs:
                                self._set_current_step(
                                    agent="ChapterPolisher",
                                    detail=f"第 {chapter_index} 章 定向修 {serious_count} 个能力问题",
                                    chapter_index=chapter_index,
                                )
                                print(f"  [polish] 接通 polisher 定向修 {serious_count} 个 critical/major 问题")
                                new_text = _chat(msgs, temperature=0.55, max_tokens=20000).strip()
                                new_audit, final = self._apply_revision_and_reaudit(
                                    chapter_index=chapter_index, path=path,
                                    original_text=final, new_text=new_text,
                                    audit_fn=audit_chapter, audits_dict=self.state.ability_audits,
                                    label="polisher",
                                )
                                if new_audit is not None:
                                    new_crit = sum(1 for i in new_audit.issues if i.severity == "critical")
                                    new_maj  = sum(1 for i in new_audit.issues if i.severity == "major")
                                    print(f"  [polish] 修订完成：score={new_audit.overall_score} "
                                          f"critical={new_crit}（前 {sev_count['critical']}）"
                                          f" major={new_maj}（前 {sev_count['major']}）")
                        except Exception as e:
                            print(f"  ⚠ polisher 失败（不影响章节）：{type(e).__name__}: {e}")
                else:
                    print(f"  ✓ 能力审计：score={audit.overall_score} 无问题 — {audit.summary[:40]}")
        except Exception as e:
            print(f"  ⚠ 能力审计失败（不影响章节）：{type(e).__name__}: {e}")

        # 章后读者视角审计 —— 模拟读者会不会追更
        # 同样不阻塞主流程
        try:
            from agents.reader_experience_auditor import audit_chapter as reader_audit
            self._set_current_step(agent="ReaderExperienceAuditor",
                                    detail=f"第 {chapter_index} 章 读者视角审计",
                                    chapter_index=chapter_index)
            r_audit = reader_audit(self.state, chapter_index, final)
            if r_audit is not None:
                self.state.reader_audits[chapter_index] = r_audit
                tag = "⚠" if (r_audit.overall_score < 8 or r_audit.retention_estimate < 70) else "✓"
                print(f"  {tag} 读者审计：overall={r_audit.overall_score}/10 "
                      f"retention≈{r_audit.retention_estimate}% | "
                      f"代入={r_audit.emotional_anchor} 钩子={r_audit.hook_strength} "
                      f"新奇={r_audit.novelty} 流畅={r_audit.fluency} — {r_audit.summary[:40]}")
                if r_audit.dropout_risk_points:
                    print(f"    弃书风险点：{' / '.join(r_audit.dropout_risk_points[:3])}")
                # ── 接通：overall<8 或 retention<70 触发整章 revise ──
                if r_audit.overall_score < 8 or r_audit.retention_estimate < 70:
                    fb_parts = [
                        "[读者视角反馈] 本章被读者审计判为不够留人——必须改善：",
                        f"  · 综合 {r_audit.overall_score}/10，留存估计 {r_audit.retention_estimate}%",
                        f"  · 弱点：代入={r_audit.emotional_anchor} 钩子={r_audit.hook_strength} "
                        f"新奇={r_audit.novelty} 流畅={r_audit.fluency} 共情={r_audit.empathy_depth}",
                    ]
                    if r_audit.dropout_risk_points:
                        fb_parts.append("  · 弃书风险点（重点修这些）：")
                        for p in r_audit.dropout_risk_points[:5]:
                            fb_parts.append(f"      - {p}")
                    if r_audit.issues:
                        fb_parts.append("  · 具体问题：")
                        for iss in r_audit.issues[:5]:
                            dim = getattr(iss, "dimension", "") or getattr(iss, "type", "")
                            desc = getattr(iss, "description", "") or ""
                            fb_parts.append(f"      - [{dim}] {desc[:80]}")
                    fb_parts.append("修订要求：解决弃书风险点 + 把弱点维度提到 8 分以上；不动主线事件。")
                    fb = "\n".join(fb_parts)
                    self._set_current_step(agent="Writer", detail=f"第 {chapter_index} 章 读者反馈定向修订",
                                            chapter_index=chapter_index)
                    print(f"  [reader-revise] 触发整章修订（读者审计不达标）")
                    new_text = revise_chapter(self.state, directive, final, fb)
                    new_r, final = self._apply_revision_and_reaudit(
                        chapter_index=chapter_index, path=path,
                        original_text=final, new_text=new_text,
                        audit_fn=reader_audit, audits_dict=self.state.reader_audits,
                        label="reader-revise",
                    )
                    if new_r is not None:
                        print(f"  [reader-revise] 修订后 overall={new_r.overall_score} retention={new_r.retention_estimate}%")
        except Exception as e:
            print(f"  ⚠ 读者审计/修订失败（不影响章节）：{type(e).__name__}: {e}")

        # 章后对话质量审计 —— 角色是否立得住
        try:
            from agents.dialogue_auditor import audit_chapter as dialogue_audit
            self._set_current_step(agent="DialogueAuditor",
                                    detail=f"第 {chapter_index} 章 对话质量审计",
                                    chapter_index=chapter_index)
            d_audit = dialogue_audit(self.state, chapter_index, final)
            if d_audit is not None:
                self.state.dialogue_audits[chapter_index] = d_audit
                tag = "⚠" if d_audit.overall_score < 8 else "✓"
                print(f"  {tag} 对话审计:overall={d_audit.overall_score}/10 "
                      f"潜台词={d_audit.subtext_density} 差异化={d_audit.voice_distinctiveness} "
                      f"说教抑制={d_audit.infodump_level} 节拍={d_audit.emotional_pacing} "
                      f"对话占比={d_audit.dialogue_ratio_percent}% — {d_audit.summary[:40]}")
                # ── 接通：对话不达标触发对话定向修订 ──
                weak_dims = []
                if d_audit.overall_score < 8: weak_dims.append(f"综合 {d_audit.overall_score}/10")
                if d_audit.subtext_density < 6: weak_dims.append(f"潜台词 {d_audit.subtext_density}/10")
                if d_audit.voice_distinctiveness < 6: weak_dims.append(f"角色差异化 {d_audit.voice_distinctiveness}/10")
                if d_audit.infodump_level < 7: weak_dims.append(f"说教抑制 {d_audit.infodump_level}/10")
                if weak_dims:
                    fb_parts = [
                        "[对话审计反馈] 本章对话质量不达标——只改对话部分，不动主线/事件：",
                        f"  · 弱项：{' / '.join(weak_dims)}",
                    ]
                    if d_audit.issues:
                        fb_parts.append("  · 具体问题（按 type 修）：")
                        for iss in d_audit.issues[:6]:
                            t = getattr(iss, "type", "")
                            sev = getattr(iss, "severity", "")
                            desc = getattr(iss, "description", "") or ""
                            fb_parts.append(f"      - [{t}/{sev}] {desc[:80]}")
                    fb_parts.append(
                        "修订要求：增加潜台词（不要让角色直接说出动机）/ 让每个角色用词节奏不同 / "
                        "在对话之间穿插动作或环境打断 / 删除信息倾倒型台词；其它部分原文保留。"
                    )
                    fb = "\n".join(fb_parts)
                    self._set_current_step(agent="Writer", detail=f"第 {chapter_index} 章 对话定向修订",
                                            chapter_index=chapter_index)
                    print(f"  [dialogue-revise] 触发对话定向修订")
                    new_text = revise_chapter(self.state, directive, final, fb)
                    new_d, final = self._apply_revision_and_reaudit(
                        chapter_index=chapter_index, path=path,
                        original_text=final, new_text=new_text,
                        audit_fn=dialogue_audit, audits_dict=self.state.dialogue_audits,
                        label="dialogue-revise",
                    )
                    if new_d is not None:
                        print(f"  [dialogue-revise] 修订后 overall={new_d.overall_score} 潜台词={new_d.subtext_density} 差异化={new_d.voice_distinctiveness}")
        except Exception as e:
            print(f"  ⚠ 对话审计/修订失败（不影响章节）：{type(e).__name__}: {e}")

        # 规划-执行反馈闭环：对比本章实际 vs 计划，更新 tension_debt 和 novelty_budget
        try:
            from agents.plan_reconciler import reconcile_after_chapter
            rep = reconcile_after_chapter(self.state, chapter_index)
            if rep and not rep.get("error"):
                debt = rep.get("after", {}).get("tension_debt", 0)
                bud = rep.get("after", {}).get("novelty_budget", 0)
                adv_count = len(rep.get("advice", []))
                dev_count = len(rep.get("deviations", []))
                print(f"  📊 节奏反馈：tension_debt={debt:+d} novelty_budget={bud} "
                      f"deviations={dev_count} 建议={adv_count}")
                for a in rep.get("advice", [])[:2]:
                    print(f"      · {a}")
        except Exception as e:
            print(f"  ⚠ 规划反馈失败（不影响章节）：{type(e).__name__}: {e}")

        # 章后 asset 候选追踪——扫正文找疑似新 asset（如剧情自然演化出现的新道具/功法）
        # 连续 N 章出现 → 写 progress_warning 提示用户决定是否登记。纯规则不调 LLM
        try:
            from agents.chapter_asset_tracker import update_asset_candidates
            _ac_report = update_asset_candidates(self.state, chapter_index, final)
            if _ac_report["promoted_for_review"]:
                names = " / ".join(f"《{t}》" for t in _ac_report["promoted_for_review"][:3])
                print(f"  📦 章后 asset 追踪：{len(_ac_report['promoted_for_review'])} 个新候选触达阈值 {names}")
        except Exception as _e:
            print(f"  ⚠ asset 追踪失败（不阻塞）：{type(_e).__name__}: {_e}")

        # 章后能力时间线追踪——LLM 扫正文识别"X 用了 Y 能力"事件
        # 追加到 state.power_events，更新 character_ability_profiles 的 use_count
        try:
            from agents.power_timeline_tracker import (
                track_chapter_power_events, validate_power_consistency,
            )
            track_chapter_power_events(self.state, chapter_index, final)
            # 跨章一致性校验——用了未登记能力 / 超 ceiling / 矛盾
            issues = validate_power_consistency(self.state)
            critical = [i for i in issues if i.get("severity") == "error"]
            if critical:
                from persistence.checkpoint import add_progress_warning
                preview = "；".join(i.get("message", "")[:100] for i in critical[:3])
                add_progress_warning(
                    level="error",
                    source=f"chapter:{chapter_index}:power_consistency",
                    message=(
                        f"第 {chapter_index} 章能力一致性 {len(critical)} 处 critical："
                        f"{preview}（建议人工检查正文 / 补登记角色能力 profile）"
                    ),
                )
                print(f"  ❌ 能力一致性 {len(critical)} 处 critical——已写 progress warning")
        except Exception as _e:
            print(f"  ⚠ 能力时间线追踪失败（不阻塞）：{type(_e).__name__}: {_e}")

        # 章后 setup_ledger 提取——LLM 扫正文识别"被嘲讽/被夺/被拒/失败/立誓/欠债"事件
        # 触发爽点章前 find_callback_seeds 拉出 pending entries 给 writer 当回响锚点
        try:
            from agents.setup_ledger import extract_setups_from_chapter
            _le_report = extract_setups_from_chapter(self.state, chapter_index, final)
            _new_n = len(_le_report.get("new_entries") or [])
            _cb_n = len(_le_report.get("callbacks") or [])
            if _new_n or _cb_n:
                print(f"  📒 setup ledger: 新增 {_new_n} 条 / 回响 {_cb_n} 条")
        except Exception as _e:
            print(f"  ⚠ setup_ledger 失败(不阻塞):{type(_e).__name__}: {_e}")

        # Batch 5:章后模拟读者评论(4 类身份 5-10 条) —— 挂到 summary,前端可见
        try:
            from agents.comment_simulator import simulate_comments
            _comments = simulate_comments(self.state, chapter_index, final)
            if _comments:
                _sentiments = {}
                for c in _comments:
                    _sentiments[c.sentiment] = _sentiments.get(c.sentiment, 0) + 1
                _sent_summary = " / ".join(f"{k}:{v}" for k, v in _sentiments.items())
                print(f"  💬 模拟评论 {len(_comments)} 条 [{_sent_summary}]")
        except Exception as _e:
            print(f"  ⚠ 模拟评论失败(不阻塞):{type(_e).__name__}: {_e}")

        # Batch 6:每 3 章生成一条调味建议 —— 老作者直觉调味,下章 chapter_planner 会用
        if chapter_index >= 3 and chapter_index % 3 == 0:
            try:
                from agents.flavor_advisor import generate_advice
                _adv = generate_advice(self.state, chapter_index, lookback=3)
                if _adv:
                    print(f"  🧂 调味建议(第 {_adv.generated_at_chapter} 章后): {len(_adv.advice)} 条 → {_adv.target_range}")
                    for _line in _adv.advice[:3]:
                        print(f"      · {_line[:60]}")
            except Exception as _e:
                print(f"  ⚠ 调味建议失败(不阻塞):{type(_e).__name__}: {_e}")

        # 长篇连贯性追踪：记录物品使用 + 每 5 章生成一次连贯性报告
        try:
            from agents.long_term_cohesion import update_after_chapter, generate_cohesion_report
            update_after_chapter(self.state, chapter_index, final)
            if chapter_index % 5 == 0:
                cohesion = generate_cohesion_report(self.state, chapter_index)
                dorm = len(cohesion.get("dormant_characters", []))
                unused = len(cohesion.get("unused_assets", []))
                overdue = len(cohesion.get("overdue_promises", []))
                fw_overdue = len(cohesion.get("overdue_foreshadows", []))
                lc_missed = len(cohesion.get("missed_lifecycle_nodes", []))
                sp_missed = len(cohesion.get("missed_satisfaction_points", []))
                total = dorm + unused + overdue + fw_overdue + lc_missed + sp_missed
                if total > 0:
                    print(
                        f"  🧭 跨卷连贯性扫描：销号 {dorm} / 空挂 {unused} / 承诺挂账 {overdue} / "
                        f"伏笔挂账 {fw_overdue} / lifecycle 过期 {lc_missed} / 爽点过期 {sp_missed}"
                    )
                    # 把汇总写到 progress_warning——前端 ⚠ 徽章自动显示
                    # （单条同 source 自动去重，每次扫描覆盖旧的）
                    try:
                        from persistence.checkpoint import add_progress_warning
                        bits = []
                        if dorm: bits.append(f"销号角色 {dorm} 个")
                        if unused: bits.append(f"空挂物品 {unused} 个")
                        if overdue: bits.append(f"承诺挂账 {overdue} 条")
                        if fw_overdue: bits.append(f"伏笔挂账 {fw_overdue} 条")
                        if lc_missed: bits.append(f"lifecycle 过期 {lc_missed} 个")
                        if sp_missed: bits.append(f"爽点过期 {sp_missed} 个")
                        # 等级：lifecycle / 爽点 / 伏笔过期是 critical（破坏读者期待）；
                        # 销号/空挂/承诺是 warn（节奏感问题）
                        critical_kinds = fw_overdue + lc_missed + sp_missed
                        level = "error" if critical_kinds else "warn"
                        add_progress_warning(
                            level=level,
                            source="cohesion",
                            message=(
                                f"第 {chapter_index} 章连贯性扫描发现：" + " / ".join(bits)
                                + "（详见 state.last_cohesion_report；下章 chapter_planner 已收到 hints）"
                            ),
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"  ⚠ 连贯性扫描失败（不影响章节）：{type(e).__name__}: {e}")

        # 感情线进度追踪
        try:
            from agents.romance_arc_planner import update_after_chapter as _romance_update
            _romance_update(self.state, chapter_index, final)
        except Exception as e:
            print(f"  ⚠ 感情线追踪失败（不影响章节）：{type(e).__name__}: {e}")

        # 后置审计可能再次修改正文；完成前统一刷新章节摘要的标题/字数，避免断点与磁盘正文错位。
        try:
            from persistence.state import count_chapter_words
            sm = next((c for c in self.state.completed_chapters if c.index == chapter_index), None)
            if sm:
                first_line = (final.split("\n", 1)[0] if final else "").strip()
                if first_line:
                    title = first_line
                    if "章" in title:
                        title = title.split("章", 1)[-1].strip() or title
                    sm.title = title
                sm.word_count = count_chapter_words(final)
        except Exception as _e:
            print(f"  ⚠ 最终章节摘要刷新失败（不影响章节）：{type(_e).__name__}: {_e}")

        # 每 N 章一次版本快照（删除 drift_detector 后,仅保留 version snapshot)
        from config import DRIFT_CHECK_EVERY_N_CHAPTERS as _SNAP_EVERY_N
        if _SNAP_EVERY_N > 0 and chapter_index % _SNAP_EVERY_N == 0:
            version_control.snapshot(
                self.state, label=f"chapter_{chapter_index}_checkpoint",
                chapter_index=chapter_index,
            )

        # 所有章后审计/修订/追踪都完成后，才标记章节完成并保存断点。
        mark_chapter_done(chapter_index, self.state)

    def _maybe_hitl_gates(self, chapter_index: int, directive, summary) -> None:
        """在关键节点触发 HITL 暂停点。mode=skip 时完全不触发。"""
        if HITL_MODE == "skip":
            return
        # 1. 主角跨大境界——检测本章摘要/状态里主角境界变化
        protagonist = next((c for c in self.state.characters if c.role.value == "主角"), None)
        if protagonist and "突破" in summary.summary:
            try:
                human_in_loop.gate_breakthrough(
                    self.state, chapter_index, "前境界", "新境界", mode=HITL_MODE
                )
            except HITLPause as e:
                save_state(self.state)
                print(f"\n  {e}")
                raise SystemExit(0)
        # 2. 主线伏笔回收
        for fw_id in directive.foreshadow_resolve:
            fw = self.state.get_foreshadow(fw_id)
            if fw and fw.importance.value == "主线伏笔":
                try:
                    human_in_loop.gate_major_foreshadow_resolve(
                        self.state, chapter_index, fw_id, mode=HITL_MODE
                    )
                except HITLPause as e:
                    save_state(self.state)
                    print(f"\n  {e}")
                    raise SystemExit(0)

    # ═══════════════════════════════════════════════════
    #  章节指令生成（整合所有系统）
    # ═══════════════════════════════════════════════════

    def _generate_directive(self, chapter_index: int, volume_index: int) -> ChapterDirective:
        vol = self.state.get_volume(volume_index)

        # 张力
        tension = self._get_tension(chapter_index, vol)

        # 节奏
        rhythm_seg = self.state.get_rhythm_for_chapter(chapter_index)
        rhythm = rhythm_seg.rhythm_type if rhythm_seg else RhythmType.SLOW_BUILD
        word_pace = rhythm_seg.word_pace if rhythm_seg else "中等"

        # 叙事线
        active_lines = [ln.line_id for ln in self.state.lines_active_in_chapter(chapter_index)]
        primary = self._pick_primary_line(chapter_index, active_lines)

        # 爽点
        sp_ops = get_sp_for_chapter(self.state, chapter_index)
        sp_ids = [sp.sp_id for sp in sp_ops.get("trigger", [])]
        sp_setups = [op["setup_content"] for op in sp_ops.get("setup", [])]

        # 伏笔
        fw_directive = get_chapter_foreshadow_directive(self.state, chapter_index)
        fw_plant = [fw.fw_id for fw in fw_directive["plant"]]
        fw_resolve = [fw.fw_id for fw in fw_directive["resolve"]]

        # 位置
        position = self._get_position(chapter_index, vol)

        # Director 生成精细must_include
        must_include = self._generate_must_include(
            chapter_index, volume_index, tension, sp_ids,
            sp_setups, fw_plant, fw_resolve, active_lines, position
        )

        directive = ChapterDirective(
            chapter_index=chapter_index,
            volume_index=volume_index,
            tension=tension,
            rhythm=rhythm,
            active_lines=active_lines,
            primary_line=primary,
            must_include=must_include,
            satisfaction_points=sp_ids,
            foreshadow_plant=fw_plant,
            foreshadow_resolve=fw_resolve,
            emotional_note=self._get_emotional_note(tension, position),
            chapter_position=position,
            word_pace=word_pace,
        )
        # 预填分形结构链（chapter_planner 会再基于章节自身的 structure_role 更新一次）
        directive.structure_chain = self.state.structure_chain_for_chapter(chapter_index)
        # PreChapterBrief 扩展
        directive.chapter_type = self.state.chapter_type_for(chapter_index)
        directive.character_states = self._build_character_states_for_chapter(chapter_index, volume_index)
        directive.forbidden_content = self._build_forbidden_content(chapter_index, volume_index)
        # 红鲱鱼（假线索）操作
        rh_ops = self.state.red_herrings_for_chapter(chapter_index)
        directive.red_herring_plant = [rh.rh_id for rh in rh_ops["plant"]]
        directive.red_herring_debunk = [rh.rh_id for rh in rh_ops["debunk"]]
        # 反转系统：本章要揭露的反转层 + 要埋的反转伏笔
        twist_layers = self.state.twist_reveals_for_chapter(volume_index, chapter_index)
        directive.twist_reveals = [f"{chain.chain_id}:{layer.layer}" for chain, layer in twist_layers]
        # 未来几章内（≤5章）将触发的反转——本章要埋伏笔
        directive.twist_clues_plant = []
        for chain, layer in self.state.twist_reveals_for_volume(volume_index):
            if not layer.clues_planted:
                continue
            # 简单启发：reveal 在 ≤5 章后 → 本章可以埋一条伏笔
            anchor = layer.reveal_anchor or ""
            import re as _re
            m = _re.search(r"第\d+卷第(\d+)章", anchor)
            if m:
                reveal_ch = int(m.group(1))
                if 0 < reveal_ch - chapter_index <= 5:
                    directive.twist_clues_plant.append(f"{chain.chain_id}:{layer.layer}")
        # 作者重写反馈（从 _rewrite_feedback_for_chapter 注入）
        fb_map = getattr(self, "_rewrite_feedback_for_chapter", {}) or {}
        directive.user_feedback = fb_map.get(chapter_index, "")
        # 作者章节灵感（从 state.chapter_inspirations 读）
        inspirations = getattr(self.state, "chapter_inspirations", {}) or {}
        directive.user_inspiration = inspirations.get(chapter_index, "").strip()
        # 爽点 callback 锚点—— sp 触发时,从 setup_ledger 找相关 pending entries
        # 给 writer 当具体回响素材(原文台词/具体场景)
        if sp_ids:
            try:
                from agents.setup_ledger import (
                    find_callback_seeds, format_callback_seeds_for_directive,
                )
                _seeds = []
                for sp_id in sp_ids:
                    _seeds.extend(find_callback_seeds(self.state, sp_id, chapter_index, limit=3))
                # 去重(同 entry_id 只保留一份)
                _dedup = {}
                for _e in _seeds:
                    _dedup.setdefault(_e.entry_id, _e)
                directive.callback_seeds = format_callback_seeds_for_directive(
                    list(_dedup.values())[:5]
                )
            except Exception as _e:
                print(f"  ⚠ callback_seeds 准备失败(不阻塞):{type(_e).__name__}: {_e}")

        # Batch 5:读者预期预测 —— 写章前模拟老读者会预期什么
        # chapter_planner 会对每条预期标 decision(satisfy/reverse/stack),writer 据此调整
        try:
            from agents.expectation_manager import predict_reader_expectations
            expectations = predict_reader_expectations(
                self.state, chapter_index, lookback=3,
            )
            if expectations:
                directive.reader_expectations = expectations
                print(f"  🎯 读者预期: {len(expectations)} 条预测")
        except Exception as _e:
            print(f"  ⚠ 读者预期预测失败(不阻塞):{type(_e).__name__}: {_e}")
        return directive

    def _build_character_states_for_chapter(self, chapter_index: int, volume_index: int) -> dict:
        """
        从 character_state_history 拉出本章涉及角色的最近状态快照。
        仅返回本卷活跃角色，避免上下文爆炸。
        """
        active = self.state.active_characters_in_volume(volume_index)
        result = {}
        for c in active[:8]:  # 最多 8 个活跃角色
            snap = self.state.latest_state_snapshot(c.name)
            if snap:
                result[c.name] = {
                    "location": snap.location,
                    "injury": snap.injury,
                    "emotion": snap.emotion,
                    "items": snap.items_on_hand,
                    "realm": snap.realm,
                }
            else:
                # 新角色——用 Character 基础信息作为占位
                result[c.name] = {
                    "location": "", "injury": "",
                    "emotion": "", "items": [],
                    "realm": c.volume_realm.get(volume_index, c.realm),
                }
        return result

    def _build_forbidden_content(self, chapter_index: int, volume_index: int) -> list:
        """
        本章禁止出现的内容——防止剧透和设定冲突。
        规则：
        - 未到揭露卷的隐藏势力不得出现
        - 未植入的伏笔不能提前兑现
        - 未激活阶段的特殊能力不能提前表现
        - 未到 reveal_volume 的关系秘密不得透露
        """
        forbidden = []
        # 隐藏势力
        for f in self.state.factions:
            if f.is_hidden and f.reveal_volume > volume_index:
                forbidden.append(f"不得提及隐藏势力【{f.name}】（第{f.reveal_volume}卷才揭露）")
        # 未来才兑现的伏笔
        for fw in self.state.foreshadow_items:
            if fw.planned_resolve_chapter > chapter_index + 5:
                forbidden.append(f"不得提前兑现伏笔{fw.fw_id}（计划第{fw.planned_resolve_chapter}章兑现）")
        # 关系秘密
        for bond in self.state.relationship_web.bonds:
            if bond.hidden_secret and bond.reveal_volume > volume_index:
                forbidden.append(
                    f"不得泄露{bond.char_a}↔{bond.char_b}的真实关系秘密"
                    f"（第{bond.reveal_volume}卷才揭露）"
                )
        # 未激活的能力阶段
        if self.state.power_system:
            for ab in self.state.power_system.special_abilities:
                for st in ab.awakening_stages:
                    if st.target_volume > volume_index:
                        forbidden.append(
                            f"不得提前展示《{ab.name}》的{st.stage_name}阶段"
                            f"（第{st.target_volume}卷觉醒）"
                        )
                        break  # 每个能力只取最近的一条未来阶段
        # ── 笔触多样性 forbidden（近 5 章已用的开头/结尾/比喻/过渡/标题）──
        try:
            from agents.style_diversity import recent_signatures
            sigs = recent_signatures(self.state, chapter_index, n=5)
            if sigs["openings"]:
                forbidden.append(
                    "近 5 章章首句模式（本章开头不得复用类似句式）：" +
                    " / ".join(sigs["openings"][-5:])
                )
            if sigs["closings"]:
                forbidden.append(
                    "近 5 章章末钩子（本章结尾不得复用类似结构）：" +
                    " / ".join(sigs["closings"][-5:])
                )
            if sigs["metaphors"]:
                forbidden.append(
                    "近 5 章已用过的比喻本体（本章别再用 像/如/仿佛 + 这些）：" +
                    " / ".join(sigs["metaphors"][:15])
                )
            if sigs["transitions"]:
                forbidden.append(
                    "近 5 章频繁过渡词（本章每个最多 1 次）：" +
                    " / ".join(sigs["transitions"][:10])
                )
            if sigs["titles"]:
                forbidden.append(
                    "近 5 章已用过的标题指纹（本章拟标题不得撞同前缀同长度）：" +
                    " / ".join(sigs["titles"])
                )
        except Exception as _e:
            pass
        return forbidden[:14]  # 控制条数（原 10 → 14：留笔触约束 4 条余量）

    def _generate_must_include(
        self, chapter_index: int, volume_index: int,
        tension: TensionLevel, sp_ids: list, sp_setups: list,
        fw_plant: list, fw_resolve: list,
        active_lines: list, position: str,
    ) -> list[str]:
        """用LLM生成本章must_include事件列表。"""
        lines_status = self.state.lines_status_for_chapter(chapter_index)
        recent = self.state.last_n_summaries(2)
        sp_descs = []
        for sp_id in sp_ids:
            sp = next((s for s in self.state.satisfaction_points if s.sp_id == sp_id), None)
            if sp:
                sp_descs.append(f"【触发爽点】{sp.title}：{sp.payoff_description}")
        fw_plant_descs = []
        for fw_id in fw_plant:
            fw = self.state.get_foreshadow(fw_id)
            if fw:
                fw_plant_descs.append(f"【植入伏笔】{fw.content}")
        fw_resolve_descs = []
        for fw_id in fw_resolve:
            fw = self.state.get_foreshadow(fw_id)
            if fw:
                fw_resolve_descs.append(f"【兑现伏笔】{fw.resolution_description}")

        prompt = f"""
为第{chapter_index}章生成必须完成的事件列表。

张力：{tension.value}，位置：{position}

叙事线状态：
{lines_status}

强制任务：
{chr(10).join(sp_descs + sp_setups + fw_plant_descs + fw_resolve_descs) or '无强制任务'}

近期情节：
{recent}

请输出JSON：
{{
  "must_include": ["必须发生的事件1（30字）", "事件2", "事件3"],
  "emotional_note": "本章情绪基调（20字）"
}}
"""
        from utils.json_utils import request_json
        try:
            data = request_json(
                system=DIRECTOR_SYSTEM, user=prompt,
                max_retries=2, temperature=0.6,
                agent_name=f"Director[must_include Ch{chapter_index}]",
                empty_ok=True,
            )
            if data:
                return data.get("must_include", []) or (sp_descs + fw_plant_descs + fw_resolve_descs)
            return sp_descs + fw_plant_descs + fw_resolve_descs
        except Exception:
            return sp_descs + fw_plant_descs + fw_resolve_descs

    def _get_tension(self, chapter_index: int, vol) -> TensionLevel:
        if not vol:
            return TensionLevel.RISING
        ratio = (chapter_index - vol.chapter_start) / max(vol.total_chapters - 1, 1)
        for start, end, tension in VOLUME_TENSION_CURVE:
            if start <= ratio <= end:
                return tension
        return TensionLevel.RISING

    def _get_position(self, chapter_index: int, vol) -> str:
        if not vol:
            return "普通"
        if chapter_index == vol.chapter_start:
            return "卷首"
        if chapter_index == vol.chapter_end:
            return "卷尾"
        mid = (vol.chapter_start + vol.chapter_end) // 2
        if abs(chapter_index - mid) <= 2:
            return "卷中高潮"
        return "普通"

    def _get_emotional_note(self, tension: TensionLevel, position: str) -> str:
        notes = {
            TensionLevel.CALM: "静水流深，暗流涌动",
            TensionLevel.RISING: "山雨欲来，压抑中前行",
            TensionLevel.PEAK: "极度紧张，情感爆发",
            TensionLevel.FALLING: "大战后的沉寂与反思",
            TensionLevel.TWIST: "认知颠覆，命运转折",
        }
        if position == "卷首":
            return "开门见山，立刻建立悬念"
        if position == "卷尾":
            return "余波中埋下更大的钩子"
        return notes.get(tension, "自然推进")

    def _pick_primary_line(self, chapter_index: int, active_ids: list[str]) -> str:
        if not active_ids:
            return ""
        if chapter_index % 4 == 0:
            for lid in active_ids:
                ln = self.state.get_line(lid)
                if ln and ln.line_type.value in ("情感线", "人物线"):
                    return lid
        for lid in active_ids:
            ln = self.state.get_line(lid)
            if ln and ln.scope.value == "全局" and ln.line_type.value == "故事线":
                return lid
        return active_ids[0]

    # ═══════════════════════════════════════════════════
    #  持久化
    # ═══════════════════════════════════════════════════

    def _save(self, filename: str, data: dict):
        with open(f"{project_context.plans_dir()}/{filename}", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_outline(self, chapter_index: int, volume_index: int) -> dict:
        vol = self.state.get_volume(volume_index)
        if vol:
            for o in vol.chapter_outlines:
                if o["index"] == chapter_index:
                    return o
        return {"index": chapter_index, "goal": "继续推进故事"}

    def _compile_novel(self):
        path = f"{project_context.project_dir()}/{self.state.title}_完整版.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"《{self.state.title}》\n\n题材：{self.state.genre}\n简介：{self.state.theme}\n\n")
            f.write("═" * 50 + "\n\n")
            for vol in self.state.volumes:
                f.write(f"\n\n{'═'*50}\n第{vol.index}卷  {vol.title}\n{vol.theme}\n{'═'*50}\n\n")
                for i in range(vol.chapter_start, vol.chapter_end + 1):
                    p = f"{project_context.project_dir()}/vol{vol.index:02d}/chapter_{i:04d}.txt"
                    if os.path.exists(p):
                        with open(p, encoding="utf-8") as cf:
                            f.write(cf.read())
                        f.write("\n\n" + "─" * 30 + "\n\n")
        print(f"  ✓ 完整版 → {path}")

    # ── dump helpers ──────────────────────────────────

    def _dump_world(self) -> dict:
        ps = self.state.power_system
        return {
            "world_setting": self.state.world_setting,
            "overall_arc": self.state.overall_arc,
            "power_system": {
                "name": ps.system_name,
                "realms": [{"name": r.name, "description": r.power_description} for r in ps.realms],
                "protagonist_plan": ps.protagonist_realm_plan,
            } if ps else {},
        }

    def _dump_power_system(self) -> dict:
        ps = self.state.power_system
        return asdict(ps) if ps else {}

    def _dump_factions(self) -> dict:
        return {"factions": [f.to_dict() for f in self.state.factions]}

    def _dump_creative_intent(self) -> dict:
        i = self.state.creative_intent
        return {"creative_intent": i.__dict__}

    def _dump_geography(self) -> dict:
        g = self.state.geography
        return {
            "world_map_desc": g.world_map_desc,
            "regions": [
                {"id": r.region_id, "name": r.name, "level": r.level, "parent": r.parent_id,
                 "climate": r.climate, "products": r.products,
                 "notable_spots": r.notable_spots,
                 "description": r.description, "culture_notes": r.culture_notes}
                for r in g.regions
            ],
            "transport_modes": [
                {"name": m.name, "speed": m.speed_description,
                 "realm_required": m.realm_required, "cost": m.cost}
                for m in g.transport_modes
            ],
            "distances": [
                {"from": d.from_region, "to": d.to_region,
                 "distance": d.distance_desc, "by_mode": d.travel_time_by_mode}
                for d in g.distances
            ],
        }

    def _dump_timeline(self) -> dict:
        t = self.state.timeline
        return {
            "current_era": t.current_era,
            "current_year_desc": t.current_year_desc,
            "events": [
                {"id": e.event_id, "era": e.era, "years_ago": e.years_ago,
                 "name": e.name, "description": e.description,
                 "consequences": e.consequences, "related_factions": e.related_factions,
                 "foreshadow_potential": e.foreshadow_potential}
                for e in t.events_sorted()
            ],
        }

    def _dump_economy(self) -> dict:
        e = self.state.economy
        return {
            "currencies": [
                {"name": c.name, "rank": c.rank, "exchange_to_base": c.exchange_to_base, "notes": c.notes}
                for c in e.currencies
            ],
            "price_anchors": [
                {"item": a.item, "price": a.price, "tier": a.tier}
                for a in e.price_anchors
            ],
            "protagonist_wealth_curve": [
                {"volume": w.volume, "tier": w.tier, "description": w.description}
                for w in sorted(e.protagonist_wealth_curve, key=lambda x: x.volume)
            ],
            "trade_notes": e.trade_notes,
        }

    def _dump_character_arcs(self) -> dict:
        return {
            "character_arcs": [
                {
                    "character_name": a.character_name,
                    "theme": a.theme,
                    "start_state": a.start_state,
                    "end_state": a.end_state,
                    "transitions": [
                        {"volume": t.volume, "chapter_approx": t.chapter_approx,
                         "trigger_event": t.trigger_event,
                         "state_before": t.state_before, "state_after": t.state_after,
                         "inner_change": t.inner_change}
                        for t in a.transitions
                    ],
                }
                for a in self.state.character_arcs
            ]
        }

    def _dump_concept_pitch(self) -> dict:
        p = self.state.concept_pitch
        l = self.state.trope_library
        t = self.state.tone_manual
        return {
            "concept_pitch": {
                "one_line_pitch": p.one_line_pitch,
                "core_selling_points": p.core_selling_points,
                "target_audience": p.target_audience,
                "target_age_group": p.target_age_group,
                "target_platform": p.target_platform,
                "reader_profile": p.reader_profile,
                "benchmark_works": p.benchmark_works,
                "differentiation": p.differentiation,
                "expected_total_words": p.expected_total_words,
                "expected_volumes": p.expected_volumes,
                "expected_completion_weeks": p.expected_completion_weeks,
            },
            "trope_library": {
                "embrace_tropes": l.embrace_tropes,
                "avoid_tropes": l.avoid_tropes,
                "preferred_sp_types": l.preferred_sp_types,
                "villain_policy": l.villain_policy,
                "romance_policy": l.romance_policy,
                "harem_policy": l.harem_policy,
                "protagonist_archetype": l.protagonist_archetype,
                "world_tone": l.world_tone,
            },
            "tone_manual": {
                "narrative_voice": t.narrative_voice,
                "style_reference": t.style_reference,
                "prose_rhythm": t.prose_rhythm,
                "dialogue_style": t.dialogue_style,
                "sensory_weight": t.sensory_weight,
                "banned_words": t.banned_words,
                "careful_words": t.careful_words,
                "metaphor_preference": t.metaphor_preference,
                "opening_habit": t.opening_habit,
            },
        }

    def _dump_characters(self) -> dict:
        return {"characters": [
            {
                "name": c.name, "role": c.role.value, "realm": c.realm,
                "personality": c.personality_detail,
                "trauma": c.trauma, "desire": c.desire, "fear": c.fear,
                "speech_pattern": c.speech_pattern,
                "arc": c.arc, "motivation": c.motivation, "fatal_flaw": c.fatal_flaw,
                "volume_realm": c.volume_realm,
                "relationships": [{"target": r.target_name, "relation": r.relation} for r in c.relationships],
                "signature_mannerisms": c.signature_mannerisms,
                "verbal_tics": c.verbal_tics,
                "sensory_signature": c.sensory_signature,
                "default_stress_response": c.default_stress_response,
                "defining_memory": c.defining_memory,
                "secret_desire": c.secret_desire,
                "contrast_with_protagonist": c.contrast_with_protagonist,
            }
            for c in self.state.characters
        ]}

    def _dump_volumes(self) -> dict:
        return {"volumes": [
            {
                "index": v.index, "title": v.title, "theme": v.theme,
                "arc": v.arc, "chapters": f"{v.chapter_start}-{v.chapter_end}",
                "antagonist": v.volume_antagonist,
                "opening_hook": v.opening_hook, "closing_hook": v.closing_hook,
                "key_events": v.key_events,
            }
            for v in self.state.volumes
        ]}

    def _dump_lines(self) -> dict:
        def dl(ln):
            return {
                "id": ln.line_id, "type": ln.line_type.value, "scope": ln.scope.value,
                "name": ln.name,
                "phases": [
                    {"index": p.phase_index, "name": p.name,
                     "chapters": f"{p.chapter_start}-{p.chapter_end}", "tension": p.tension.value}
                    for p in ln.phases
                ],
            }
        return {
            "global_lines": [dl(ln) for ln in self.state.global_lines],
            "volume_lines": [dl(ln) for ln in self.state.volume_lines],
        }

    def _dump_satisfaction_points(self) -> dict:
        return {"satisfaction_points": [
            {
                "id": sp.sp_id, "type": sp.sp_type.value, "title": sp.title,
                "intensity": sp.intensity, "volume": sp.volume, "target_chapter": sp.target_chapter,
                "description": sp.description, "payoff": sp.payoff_description,
                "setup_chain": [{"chapter": s.chapter, "content": s.content} for s in sp.setup_chain],
            }
            for sp in self.state.satisfaction_points
        ]}

    def _dump_rhythm_plans(self) -> dict:
        return {"rhythm_plans": [
            {
                "volume": p.volume_index, "pattern": p.overall_pattern,
                "breathing_chapters": p.breathing_chapters,
                "climax_chapters": p.climax_chapters,
                "segments": [
                    {"chapters": f"{s.chapter_start}-{s.chapter_end}",
                     "type": s.rhythm_type.value, "pace": s.word_pace}
                    for s in p.segments
                ],
            }
            for p in self.state.rhythm_plans
        ]}

    def _dump_foreshadow_plan(self) -> dict:
        return {"foreshadow_items": [
            {
                "id": fw.fw_id, "importance": fw.importance.value,
                "content": fw.content, "hidden_meaning": fw.hidden_meaning,
                "plant_chapter": fw.planted_chapter,
                "resolve_volume": fw.planned_resolve_volume,
                "resolve_chapter": fw.planned_resolve_chapter,
                "resolution": fw.resolution_description,
            }
            for fw in self.state.foreshadow_items
        ]}

    def _dump_volume_summary(self, vi: int) -> dict:
        vol = self.state.get_volume(vi)
        completed = [c for c in self.state.completed_chapters if c.volume_index == vi]
        return {
            "volume": vi, "title": vol.title if vol else "",
            "chapters": len(completed),
            "tension_sequence": [c.tension.value for c in completed],
            "sp_triggered": [sp_id for c in completed for sp_id in c.sp_triggered],
            "summaries": [{"index": c.index, "title": c.title, "summary": c.summary} for c in completed],
        }

    def _dump_memory_report(self) -> dict:
        return {
            "tension_history": [t.value for t in self.state.tension_history],
            "world_facts": self.state.memory.facts[-30:],
            "character_states": self.state.memory.character_states,
            "foreshadow_resolved": len([f for f in self.state.foreshadow_items if f.resolved]),
            "foreshadow_unresolved": len(self.state.memory.facts),
            "chapter_summaries": [
                {"index": c.index, "volume": c.volume_index, "title": c.title,
                 "tension": c.tension.value, "sp": c.sp_triggered}
                for c in self.state.completed_chapters
            ],
        }

    def _dump_relationship_web(self) -> dict:
        web = self.state.relationship_web
        return {
            "bonds": [
                {
                    "id": b.bond_id, "char_a": b.char_a, "char_b": b.char_b,
                    "surface": b.surface_relation, "true": b.true_relation,
                    "secret": b.hidden_secret, "tension": b.tension_source,
                    "reveal_volume": b.reveal_volume,
                    "volume_evolution": b.volume_evolution,
                }
                for b in web.bonds
            ],
            "power_chains": web.power_chains,
            "hidden_alliances": web.hidden_alliances,
            "faction_affiliations": web.faction_affiliations,
        }

    def _dump_protagonist_journey(self) -> dict:
        j = self.state.protagonist_journey
        return {
            "overall_theme": j.overall_theme,
            "core_wound": j.core_wound,
            "true_goal": j.true_goal,
            "fatal_flaw": j.fatal_flaw,
            "central_conflict": j.central_conflict,
            "growth_arc": j.growth_arc,
            "milestones": [
                {
                    "volume": m.volume,
                    "entry_state": m.entry_state,
                    "exit_state": m.exit_state,
                    "inner_growth": m.inner_growth,
                    "outer_change": m.outer_change,
                    "key_relationships": m.key_relationships,
                    "inner_conflict": m.inner_conflict,
                    "hardest_choice": m.hardest_choice,
                    "darkest_moment": m.darkest_moment,
                    "triumph_moment": m.triumph_moment,
                }
                for m in j.milestones
            ],
            "stage_beats": [
                {
                    "beat_id": b.beat_id, "stage_id": b.stage_id, "volume": b.volume,
                    "entry_state": b.entry_state, "exit_state": b.exit_state,
                    "key_actions": b.key_actions,
                    "relationship_shifts": b.relationship_shifts,
                    "gained": b.gained, "lost": b.lost,
                    "milestone_phase": b.milestone_phase,
                }
                for b in j.stage_beats
            ],
        }

    def _get_prev_chapter_tail(self, chapter_index: int, volume_index: int, tail_chars: int = 300) -> str:
        """读取上一章末尾文字，供 writer 做文字级无缝衔接。"""
        if chapter_index <= 1:
            return ""
        prev = chapter_index - 1
        # 上一章可能在同卷或上一卷目录
        vol = self.state.get_volume(volume_index)
        prev_vol_index = volume_index
        if vol and prev < vol.chapter_start:
            prev_vol_index = volume_index - 1
        path = f"{project_context.project_dir()}/vol{prev_vol_index:02d}/chapter_{prev:04d}.txt"
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return content[-tail_chars:] if len(content) > tail_chars else content

    def _dump_fortunes(self) -> dict:
        return {"fortunes": [
            {
                "id": f.fortune_id, "type": f.fortune_type, "name": f.name,
                "description": f.description, "location": f.location_desc,
                "acquisition": f.acquisition_method, "prerequisite": f.prerequisite,
                "volume": f.volume, "target_chapter": f.target_chapter,
                "effect": f.effect_on_growth, "hook": f.narrative_hook,
                "obtained": f.obtained, "actual_chapter": f.actual_chapter,
            }
            for f in self.state.fortunes
        ]}

    def _dump_stages(self, volume_index: int) -> dict:
        stages = [s for s in self.state.story_stages if s.volume == volume_index]
        return {"volume": volume_index, "stages": [
            {
                "id": s.stage_id, "name": s.name, "type": s.stage_type,
                "chapters": f"{s.chapter_start}-{s.chapter_end}",
                "setting": s.setting_desc, "atmosphere": s.atmosphere,
                "protagonist_role": s.protagonist_role,
                "key_activities": s.key_activities,
                "fortune_ids": s.fortune_ids,
                "transition_in": s.transition_in, "transition_out": s.transition_out,
                "parallel": s.parallel_stage_ids,
                "sub_scenes": [
                    {
                        "id": ss.sub_id, "name": ss.name, "type": ss.sub_type,
                        "chapters": f"{ss.chapter_start}-{ss.chapter_end}",
                        "description": ss.description,
                        "key_events": ss.key_events, "fortune_ids": ss.fortune_ids,
                    }
                    for ss in s.sub_scenes
                ],
            }
            for s in stages
        ]}

    def _print_full_overview(self):
        print("\n" + "═" * 65)
        print("  全书规划完成 — 总览")
        print("═" * 65)
        total_ch = sum(v.total_chapters for v in self.state.volumes)
        print(f"  卷数：{len(self.state.volumes)}  总章节：{total_ch}  角色：{len(self.state.characters)}")
        print(f"  力量体系：{self.state.power_system_brief()}")
        print(f"  势力：{len(self.state.factions)} 个  |  全局线：{len(self.state.global_lines)} 条  |  卷内线：{len(self.state.volume_lines)} 条")
        print(f"  爽点：{len(self.state.satisfaction_points)} 个  |  伏笔：{len(self.state.foreshadow_items)} 个")
        web = self.state.relationship_web
        j = self.state.protagonist_journey
        print(f"  关系网：{len(web.bonds)} 条  |  机缘：{len(self.state.fortunes)} 个")
        if j.overall_theme:
            print(f"  主角主题：{j.overall_theme}")
            print(f"  核心矛盾：{j.central_conflict}")
        print("\n  卷结构：")
        for v in self.state.volumes:
            realm = self.state.power_system.protagonist_realm_plan.get(v.index, "?") if self.state.power_system else "?"
            sps = [s for s in self.state.satisfaction_points if s.volume == v.index]
            print(f"    第{v.index}卷《{v.title}》[{v.chapter_start}-{v.chapter_end}章] "
                  f"主角到达:{realm} 爽点:{len(sps)}个")
        print("═" * 65 + "\n")


# ── 工具函数 ───────────────────────────────────────────

def _beats_for_volume(state, volume_index: int):
    """为单卷设计主角舞台节拍（步骤3的单卷版本）。"""
    from agents.protagonist_journey import _step3_stage_beats as _full_step3
    # 临时只保留本卷舞台，跑完后 stages 恢复（实际上只写入本卷节拍）
    existing_beat_stage_ids = {b.stage_id for b in state.protagonist_journey.stage_beats}
    vol_stages = [s for s in state.story_stages if s.volume == volume_index]
    # 只处理还没有节拍的舞台
    new_stages = [s for s in vol_stages if s.stage_id not in existing_beat_stage_ids]
    if not new_stages:
        print(f"  ✓ 第{volume_index}卷舞台节拍已全部存在，跳过")
        return
    # 临时替换 story_stages 只含本卷新舞台，让 step3 只处理这些
    original_stages = state.story_stages
    state.story_stages = new_stages
    _full_step3(state)
    state.story_stages = original_stages


def _banner(msg: str):
    print(f"\n{'═'*65}\n  {msg}\n{'═'*65}\n")


def _section(title: str):
    print(f"\n{'─'*65}")
    print(f"  {title}")
    print(f"{'─'*65}")
    # 同步写到 progress_status.json——前端轮询读
    # 用 phase 字段（前端读的就是这个）；清掉 agent/detail 避免留上次残留
    _update_progress_status(phase=title, agent="", detail="")


def _update_progress_status(**fields):
    """委托给 ops_tracker.write_progress，标记 source=director 区别于 web 同步写入。"""
    ops_tracker.write_progress(source="director", **fields)

