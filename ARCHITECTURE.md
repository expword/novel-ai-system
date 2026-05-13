# 架构详解

本文件描述系统的分层、agent 职能分类、数据流、关键设计决策。

---

## 三层架构

```
┌──────────────────────────────────────────────────────────┐
│ 入口层                                                    │
│   · main.py                  CLI 入口                     │
│   · run_web.py               Web 入口                     │
│   · web/app.py               Flask REST API               │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ 流程总控层 — core/                                        │
│   · core/director.py         主流程 / 写章管线             │
│   · core/scheduler.py        DAG 任务调度器                │
│   · core/scheduler_tasks.py  30 phase 任务定义             │
│                                                           │
│ 项目管理 — project_mgmt/                                  │
│   · project_context.py       当前项目路径绑定               │
│   · project_manager.py       项目 CRUD + 子进程管理         │
│   · human_in_loop.py         HITL 关卡                    │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ Agent 层 — agents/ （60+ 个职能型 agent）                  │
│   每个 agent 做一件事：分析 / 设计 / 写 / 审 / 改          │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ 基础设施层                                                 │
│ ── LLM 调度 llm_layer/                                    │
│   · llm.py / llm_pool.py / llm_profiles.py /              │
│     llm_runtime.py / user_models.py / fallback_runner.py  │
│ ── 状态持久化 persistence/                                 │
│   · state.py / checkpoint.py / state_storage.py /         │
│     state_audit.py / chapter_cleanup.py /                 │
│     entity_cleanup.py / version_control.py                │
│ ── 工具 utils/                                            │
│   · json_utils.py / validators.py / invariants.py /       │
│     context_manager.py / ops_tracker.py /                 │
│     prompts_registry.py / concurrency.py                  │
└──────────────────────────────────────────────────────────┘
```

> **import 约定**：所有跨包引用走完整路径（`from persistence.state import NovelState` / `from llm_layer.llm import chat` / `from utils.json_utils import request_json`）。同包内可用相对（`from .checkpoint import ...`）但项目里目前统一用绝对路径，便于 IDE 跳转。

---

## Agent 分层（按职能分类，60+ 个）

### A. 规划期 Agents

#### A1. 意图组（G1，4 个）

| Agent | 文件 | 干啥 |
|---|---|---|
| `intent_analyzer` | `agents/intent_analyzer.py` | 从自然语言意图拆出主题/卖点/原型/对标作品 |
| `intent_refiner` | `agents/intent_refiner.py` | 用户多轮追加意图时的合并 |
| `concept_pitch` | `agents/concept_pitch.py` | 立项三件套（ConceptPitch + TropeLibrary + ToneManual） |
| `master_dispatcher` | `agents/master_dispatcher.py` | Phase 0.5：一次 LLM 产全书骨架蓝图（22 角色槽位/势力骨架/关键节点） |
| `protagonist_journey` | `agents/protagonist_journey.py` | 主角内核（创伤/真实目标/致命弱点）+ 卷级里程碑 |

#### A2. 世界组（G2，9 个）

| Agent | 文件 | 干啥 |
|---|---|---|
| `realm_designer` | `agents/realm_designer.py` | 1A 力量体系/境界 + 1A2 战力刻度 + 2C 特殊能力 |
| `volume_planner` | `agents/volume_planner.py` | 1B 卷结构（每卷主题/章节范围）+ 4B 逐章大纲 |
| `faction_architect` | `agents/faction_architect.py` | 1C 势力架构 |
| `world_builder` | `agents/world_builder.py` | 1D 世界观 + 1E 完整性校验 |
| `geography_designer` | `agents/geography_designer.py` | 1F 地理 / 区划 / 距离矩阵 |
| `timeline_anchor` | `agents/timeline_anchor.py` | 1G 时间锚点 |
| `economy_designer` | `agents/economy_designer.py` | 1H 经济系统 |
| `customs_designer` | `agents/customs_designer.py` | 风俗（可选） |

#### A3. 人物组（G3，6 个）

| Agent | 文件 | 干啥 |
|---|---|---|
| `character_designer` | `agents/character_designer.py` | 2/2A 核心人物档案（主角圈/盟友/反派/卷内） |
| `major_supporting_refiner` | `agents/major_supporting_refiner.py` | 2A2 主要人物细腻深化 |
| `character_web` | `agents/character_web.py` | 2B 人物关系网络 |
| `character_arc_designer` | `agents/character_arc_designer.py` | 2D 心理弧（每人一条） |
| `antagonist_depth_designer` | `agents/antagonist_depth_designer.py` | 反派深化（动机/手段/弱点） |
| `ability_roadmap_planner` | `agents/ability_roadmap_planner.py` | **2C2 能力路线图**（金手指 lifecycle 节点序列 + 反向产爽点） |

#### A4. 情节组（G4，11 个）

| Agent | 文件 | 干啥 |
|---|---|---|
| `line_planner` | `agents/line_planner.py` | 3A 全局叙事线 + 3B 卷内叙事线 |
| `line_stage_alignment` | `agents/line_stage_alignment.py` | 叙事线与卷舞台对齐 |
| `conflict_ladder` | `agents/conflict_ladder.py` | 3B2 冲突阶梯 |
| `satisfaction_system` | `agents/satisfaction_system.py` | 3C 爽点系统 |
| `rhythm_designer` | `agents/rhythm_designer.py` | 3D 节奏 |
| `emotion_curve` | `agents/emotion_curve.py` | 3D2 情绪曲线 |
| `twist_designer` | `agents/twist_designer.py` | 3E3 反转链（先于伏笔，声明每层需要的 clues） |
| `foreshadow_manager` | `agents/foreshadow_manager.py` | 3E 伏笔 + 3E2 红鲱鱼 |
| `fortune_planner` | `agents/fortune_planner.py` | 3F 机缘体系 |

### B. 卷级规划 Agents（每卷 5 个）

| Agent | 文件 | 干啥 |
|---|---|---|
| `stage_architect` | `agents/stage_architect.py` | 4_stage 卷叙事舞台（每卷 5-8 个舞台/转折） |
| `protagonist_journey._beats_for_volume` | 同上 | 4_beats 主角在每个舞台的内心节拍 |
| `volume_planner.plan_volume_chapters` | 同上 | 4_vol 逐章大纲（goal/purpose/expression/标题） |
| `chapter_type_planner` | `agents/chapter_type_planner.py` | 4_ctp 章节类型分布（日常/铺垫/战斗/反转） |
| `ability_roadmap_planner.assign_chapter_to_lifecycle_nodes` | 同上 | 4_lifecycle 把粗粒度卷锚 lifecycle 节点细化到具体章 |

### C. 章级写作 Agents（每章 ~20 个）

#### C1. 写章前

| Agent | 文件 | 干啥 |
|---|---|---|
| `chapter_dispatcher` | `agents/chapter_dispatcher.py` | 章节调度（开篇章特殊处理） |
| `chapter_planner` | `agents/chapter_planner.py` | 场景蓝图（2-3 幕，每幕 Goal/Conflict/Outcome） |
| `ability_planner` | `agents/ability_planner.py` | 能力使用规划（含 lifecycle 命中章强制 should_use） |

#### C2. 写正文

| Agent | 文件 | 干啥 |
|---|---|---|
| `writer` | `agents/writer.py` | 写初稿 + 修订（按场景分批；占位符规则；真 AI 边界）|
| `external_ai_query` | `agents/external_ai_query.py` | 扫描 [[ASK_AI:...]] 占位 → 真发绑定的 LLM 取答 → 替换 |

#### C3. 写后立即审

| Agent | 文件 | 干啥 |
|---|---|---|
| `continuity_checker` | `agents/continuity_checker.py` | 连续性（硬事实 / 设定 / 因果） |
| `voice_consistency_checker` | `agents/voice_consistency_checker.py` | 角色口吻一致性 |
| `critic` | `agents/critic.py` | 10 维度评分（叙事 / 张力 / 角色 / 钩子 / 结构 / 主角 / 表达 / 细腻 / 戏剧 / 文风） |

#### C4. 写章 + 修订循环

```
write_chapter → critic → passed? → break / revise → critic → ... (MAX_REVISION_ROUNDS 轮)
```

#### C5. 章定稿前

| Agent | 文件 | 干啥 |
|---|---|---|
| `setup_reviewer` | `agents/setup_reviewer.py` | 设定合规审核（独立 LLM，专门审"是否符合 canon"，含 lifecycle 兜底）|
| `canon_checker` | `agents/canon_checker.py` | 设定护栏（确定性扫描 + 真 AI 占位检测）|
| `sensitive_filter` | `agents/sensitive_filter.py` | 敏感词过滤 |

#### C6. 状态回写 + 元数据更新

| Agent | 文件 | 干啥 |
|---|---|---|
| `memory` | `agents/memory.py` | 记忆提取（章节摘要） |
| `pacing_analyzer` | `agents/pacing_analyzer.py` | 节奏分析（对话 / 动作 / 描写 / 心理占比） |
| `state_updater` | `agents/state_updater.py` | 状态集中回写（快照 / 关系 / 伏笔激活 / 世界事件） |
| `glossary_manager` | `agents/glossary_manager.py` | 术语表增量更新 |
| `thread_tracker` | `agents/thread_tracker.py` | 实时故事线索（下章精确起点） |
| `style_diversity` | `agents/style_diversity.py` | 笔触多样性指纹 |

#### C7. 章后审计

| Agent | 文件 | 干啥 |
|---|---|---|
| `ability_auditor` | `agents/ability_auditor.py` | 金手指/技能使用合理性（critical/major 触发 polisher） |
| `chapter_polisher` | `agents/chapter_polisher.py` | 章节定向修订（被 ability_auditor 触发） |
| `reader_experience_auditor` | `agents/reader_experience_auditor.py` | 模拟读者会不会追更 |
| `dialogue_auditor` | `agents/dialogue_auditor.py` | 对话质量（潜台词/差异化/说教抑制/节拍） |
| `blueprint_compliance` | `agents/blueprint_compliance.py` | 蓝图遵循度审计 |
| `plan_reconciler` | `agents/plan_reconciler.py` | 规划-执行反馈闭环（更新 tension_debt / novelty_budget） |
| `long_term_cohesion` | `agents/long_term_cohesion.py` | 长篇连贯性（销号角色 / 空挂物品 / 承诺挂账） |
| `romance_arc_planner` | `agents/romance_arc_planner.py` | 感情线进度追踪 |
| `drift_detector` | `agents/drift_detector.py` | 漂移检测（实力膨胀 / 爽点密度 / 文风偏离 / 未授权设定） |

### D. 阶段 / 卷级审查 Agents

| Agent | 文件 | 干啥 |
|---|---|---|
| `stage_reviewer` | `agents/stage_reviewer.py` | Stage 审 → critical 触发指定章重写循环 |
| `volume_reviewer` | `agents/volume_reviewer.py` | 整卷审 → critical 触发指定章重写循环 |
| `module_reviewer` | `agents/module_reviewer.py` | 各 phase 产物审 → 不通过触发整 phase 重生 |

### E. 编辑工具

| Agent | 文件 | 干啥 |
|---|---|---|
| `chat_editor` | `agents/chat_editor.py` | Web UI 上"章节对话编辑器"——基于对话改章节 |
| `prompt_variants` | `agents/prompt_variants.py` | prompt 多变体（A/B 测试用） |
| `clue_registry` | `agents/clue_registry.py` | 线索登记表 |

---

## 数据流图

### 规划期（30 phase 一次性跑完）

```
作者意图(自然语言)
       │
       ▼ Phase -1 intent_analyzer
state.creative_intent (题材/卖点/原型/对标)
       │
       ▼ Phase 0 concept_pitch
state.concept_pitch + trope_library + tone_manual
       │
       ▼ Phase 0.5 master_dispatcher
state.master_outline (22 角色槽位 + 势力骨架 + 关键节点)
       │
       ▼ Phase 0.6 protagonist_journey (核心字段)
state.protagonist_journey.overall_theme / fatal_flaw / ...
       │
       ▼ ⏸ stepwise G1 末暂停（可选）
       ▼ Phase 1A-1H (G2 世界组 9 个)
state.power_system / volumes / factions / world_setting / geography / timeline / economy
       │
       ▼ ⏸ stepwise G2 末暂停
       ▼ Phase 2/2A2/2B/2C/2D/2C2 (G3 人物组 6 个)
state.characters / relationship_web / character_arcs +
state.power_system.special_abilities (含 lifecycle_nodes)
       │
       ▼ ⏸ stepwise G3 末暂停
       ▼ Phase 3A-3G (G4 情节组 11 个)
state.global_lines / volume_lines / conflict_ladder / satisfaction_points /
state.rhythm_plans / emotion_curve / twist_system / foreshadow_items /
state.red_herrings / fortunes / protagonist_journey.milestones
       │
       ▼ ⏸ stepwise G4 末暂停（规划全部完成）
```

### 卷级 + 章级（按卷循环 → 按章循环）

```
for vol_index in 1..N_volumes:
    [4_stage]    design_volume_stages(vol_index)
    [4_beats]    _beats_for_volume(vol_index)
    [4_vol]      plan_volume_chapters(vol_index)
    [4_ctp]      plan_chapter_types(vol_index)
    [4_lifecycle] assign_chapter_to_lifecycle_nodes(vol_index)

    for chapter_index in vol.chapter_start..chapter_end:
        # 写章前
        directive = _generate_directive(...)
        blueprint = build_chapter_blueprint(...)
        plan = plan_chapter_abilities(...)

        # 写初稿
        draft = write_chapter(state, directive, ...)
        draft = resolve_asks_in_chapter(state, draft)   # 真 AI 占位替换

        # 立即审
        continuity = check_continuity(...)
        voice = check_voice_consistency(...)

        # critic 审校循环
        for rnd in 1..MAX_REVISION_ROUNDS:
            review = review_chapter(...)
            if passed: break
            final = revise_chapter(...)

        # 定稿前
        setup_reviewer.review_chapter(...) → 局部修订（最多 2 轮）
        canon_checker.check_canon(...) → canon-revise（如有 critical）
        sensitive_filter.filter_and_report(...) → sensitive-revise

        # HITL 关卡（境界突破 / 主线伏笔回收）
        _maybe_hitl_gates(...)

        # 状态回写
        update_story_thread(...)
        write_to_disk(chapter_XXXX.txt)

        # 章后审计 + 可选 revise
        memory.process_chapter(...)
        ability_auditor → chapter_polisher (if critical/major)
        reader_experience_auditor → reader-revise
        dialogue_auditor → dialogue-revise
        plan_reconciler / long_term_cohesion / romance_arc / blueprint_compliance

        # 每 N 章一次
        if chapter_index % DRIFT_CHECK_EVERY_N_CHAPTERS == 0:
            drift_detector.detect_drift(...)
            version_control.snapshot(...)

    volume_reviewer.review_volume(vol_index)
```

---

## 关键设计决策

### D1. 单 NovelState dataclass 集中状态

所有规划产物 + 章节摘要 + 各种审计结果都挂在 `state.py` 的 `NovelState` 一个大 dataclass 上。每个字段是子 dataclass（CreativeIntent / PowerSystem / Volume / CharacterArc / 等）。

**优点**：所有 agent 共享一个 state，传递简单（只传 `state, directive, ...`）；持久化用 `_to_json` 递归 dump
**缺点**：state.py 138K 文件巨大；reload 一次全在内存；分文件存储靠 `state_storage.py` 拆

### D2. JSON-only LLM 接口

每个 agent 强制 LLM 输出 JSON（用 `json_utils.request_json`），带：
- 重试（最多 5 轮，温度递减）
- 失败时 fallback 模型
- 校验（必填 key / 列表 min_items / 自定义 validator）
- 最严苛轮加 schema example

理由：JSON 比纯文本可控，下游解析稳定，校验失败可重生

### D3. 章级深度清理（chapter_cleanup.py）

删章 / 重写章时，**18 类派生数据**全部回滚或按章过滤：

| 类别 | 字段 |
|---|---|
| 记忆 | memory.entries, memory.character_states |
| 角色 | character_state_history |
| 世界 | world_events, tension_history |
| 情节 | satisfaction_points.triggered, foreshadow_items.planted/activation/resolved, red_herrings.planted/debunked, fortunes.obtained |
| 叙事线 | all_lines[*].phases[*].completed, story_thread + open_loops |
| 章级审计 | chapter_chats, ability_audits, reader_audits, dialogue_audits |
| 主角 | protagonist_power_log |
| Canon | _canon_audit |
| Lifecycle | special_abilities[*].lifecycle_nodes[*].triggered/actual_chapter |
| 感情线 | romance_arcs[*].actual_events, last_interaction_chapter |

不清理会让 writer 拿到脏数据漂移。

### D4. 真 AI 接入（in-story tool call）

主角金手指如果是 AI 类，正文里"主角问 AI"用占位符 `[[ASK_AI:asset_name|具体问题]]`，章节定稿前 `external_ai_query.resolve_asks_in_chapter` 用绑定的 LLM 真实发问换回回答。

writer prompt 里硬约束：
- 占位符规则（不能自己编 AI 回答）
- 功能边界（不能问本书虚构设定的专有信息）
- 戏剧形式自由（怎么获取/呈现）

详见 README.md 的"真 AI 接入"小节。

### D5. 多 LLM 路由

`user_models.json` 配多个模型 profile，`usage` 字段决定它充当哪些角色：
- `main` — 主写作模型
- `reviewer` — 审核员
- `fallback` — 主模型失败兜底
- 不带 usage 的 → 由 `special_abilities[*].external_llm_profile` 显式按 id 绑定（如豆包做叙事内 AI）

`llm.py` 主入口 `chat()` 透明路由，所有 agent 调它即可。

### D6. 断点恢复 + stepwise interrupt

- 每个 phase 跑完调 `mark_phase_done(phase_id, state)` → 内部 `save_state` + 写 `progress.json`
- 重启时 `is_phase_done(phase_id, progress)` 跳过已完成
- stepwise 模式：每个 group（G1/G2/G3/G4）末 save + SystemExit(0)，等用户点继续重新启动子进程从 progress.json 续

### D7. agent 之间不直接通信

所有 agent 通过 state 间接交互：A 写 state.X，B 读 state.X。不互相调用。这让：
- 添加新 agent 容易（只关心读哪些 state 字段、写哪些）
- 调试简单（看 state 字段在哪个 phase 被填）
- 并发安全（不同 agent 写不同字段，scheduler 控制依赖）

---

## 扩展指南

### 加一个新 agent

1. 在 `agents/` 加 `your_agent.py`，导出主函数 `your_function(state, ...)`
2. 决定它输出到 state 哪个字段（在 `state.py` 加新字段，或扩展已有字段）
3. 修 `checkpoint.py` 加对应 `_load_xxx` 反序列化函数（**勿漏**——旧 state.json 不会自动有这字段）
4. 在 `scheduler_tasks.py` 加 Task 注册，写 `depends_on`
5. 在 `director.py` 的 stepwise fallback 块加一段调用（auto 模式靠 scheduler 自动跑）

### 加一种新的章后审计

1. 在 `agents/` 加 `your_auditor.py`，输入 `(state, chapter_index, content)`，输出 issues 列表
2. 在 `director._write_one_chapter` 章后部分调你的 auditor
3. critical issue 触发 revise：调 `writer.revise_chapter(state, directive, content, feedback)`
4. 章级清理：如果你的 auditor 把结果存到 `state.your_audits[ch_index]`，记得在 `chapter_cleanup.py` 加 pop 逻辑

### 加一个新题材的支持

不需要改代码——所有 agent 的 prompt 都设计成"读 state 字段动态适配"。新题材只需要：
- 创建项目时给好的 `intent_description`
- 让 LLM 自动产出符合该题材的 state（力量体系 / 人物 / 情节）

如果某些 phase 对新题材不合适（如纯写实文学不需要"力量体系"），可以在 `config.py` 加 phase 跳过开关，或者让对应 agent 在该 system_type 下早退（参考 `realm_designer.design_special_abilities` 对 system_type="realms"/"skill_tiers" 的判断）。

---

## 测试

当前项目**没有完整测试套件**——这是已知不足。建议添加：

```
tests/
├─ test_agents/       # 每个 agent 一个测试，mock LLM 验证 prompt → JSON 解析
├─ test_state/        # state 字段读写 / 序列化往返
├─ test_director/     # director 流程（mock agents）
└─ test_cleanup/      # chapter_cleanup 18 类数据清理验证
```

欢迎贡献。
