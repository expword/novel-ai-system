# 长篇小说 AI 生成系统

一个把"写一本完整长篇小说"拆成 **30+ 个职能 agent 协同**的工作流系统。从作者意图开始，经规划期（意图分析 → 世界观 → 人物 → 情节）→ 卷级规划 → 章级写作 → 章后审计，端到端产出小说正文。

支持单作者「自然语言意图」→「全书蓝图（22 角色槽位、6 层势力、N 卷大纲）」→「逐章草稿 + 多维审计 + 自动修订」。

---

## 核心特性

- **30+ 阶段流水线（DAG 调度）**：规划期 30 个 phase 按依赖图并发跑；卷级 / 章级嵌套循环
- **职能型 agent**：60+ 个独立 agent，每个只负责一件事（意图分析 / 力量体系 / 关系网 / 爽点系统 / 反转链 / 伏笔 / 章节写作 / 章后审计 ...）
- **章级审校循环**：critic 审 → revise 改 → critic 复审，最多 N 轮直到通过或达上限
- **真 AI 接入**（外接 LLM）：主角金手指如果是 AI/系统类，正文里的对话**真发给绑定的 LLM 拿真实回答**，不让主写模型脑补
- **HITL 人在回路**：主角境界突破 / 主线伏笔回收 等关键节点暂停等人审
- **断点恢复**：每个 phase 完成都落盘，崩溃可从断点继续（不重跑已完成）
- **多模型路由**：通过 `user_models.json` 配置主模型 / 审核员 / 兜底 / 叙事内 AI 各自的 profile
- **Web UI**：项目管理 / 启动暂停 / 看进度 / 看产物 / 重写章节 / 章节对话编辑器
- **章级深度清理**：删章 / 重写时同步清理 18 类派生数据（记忆 / 审计 / 关系演化 / 伏笔状态 等），不留脏数据

---

## 快速开始

### 1. 环境

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# 或 source venv/bin/activate  # macOS / Linux
pip install -r requirements.txt
```

要求 Python 3.10+。

### 2. 配置 LLM

```bash
cp user_models.example.json user_models.json
# 编辑 user_models.json 填入真实 API key
```

至少配一个 `usage: ["main"]` 的主模型。可选配 `reviewer` / `fallback` / 叙事内 AI（如豆包）。

### 3. 启动 Web UI

```bash
python run_web.py
# 默认 http://127.0.0.1:5000
```

浏览器打开后：
1. **新建项目** → 填标题 / 题材 / 主题 / 作者意图（200-500 字自然语言）
2. **启动写作** → 后台跑 30 phase 规划期
3. **看进度** → 每个 phase 跑完产物实时展示
4. **stepwise 模式**（推荐）：每个阶段组（意图 / 世界 / 人物 / 情节）跑完暂停，审完产物点继续

### 4. 或者纯 CLI

```bash
# 跑完整规划 + 章节写作
python main.py
# 通过环境变量指定项目
$env:XIAOSHUO_PROJECT_ID="my_novel"     # PowerShell
python main.py
```

---

## 项目结构

```
xiaoshuo/
├─ main.py                          # CLI 入口
├─ run_web.py                       # Web 入口
├─ config.py                        # 全局配置（轮数/阈值/字数 等）
├─ requirements.txt
├─ user_models.example.json         # LLM 配置模板（拷贝为 user_models.json）
├─ README.md / ARCHITECTURE.md / .gitignore
│
├─ core/                            # 流程总控
│   ├─ director.py                  # 30 phase + 卷级 + 章级写作管线
│   ├─ scheduler.py                 # 并发任务调度器（DAG）
│   └─ scheduler_tasks.py           # 30 phase 任务定义 + 依赖
│
├─ persistence/                     # 状态层 + 章节清理
│   ├─ state.py                     # NovelState dataclass 集合（几十个嵌套数据类）
│   ├─ checkpoint.py                # save_state / load_state / mark_phase_done
│   ├─ state_storage.py             # 分文件分段存储
│   ├─ state_audit.py               # state 一致性校验
│   ├─ chapter_cleanup.py           # 删章时清 18 类派生数据
│   ├─ entity_cleanup.py
│   └─ version_control.py           # 快照 + 回滚
│
├─ llm_layer/                       # LLM 调度
│   ├─ llm.py                       # 主接口 chat() / chat_stream()
│   ├─ llm_pool.py                  # 并发池 + 速率限制 + 熔断器
│   ├─ llm_profiles.py              # 模型 profile
│   ├─ llm_runtime.py               # 运行时 profile 路由
│   ├─ user_models.py               # user_models.json 读取
│   └─ fallback_runner.py           # 主模型失败兜底
│
├─ project_mgmt/                    # 项目管理 + HITL
│   ├─ project_context.py           # 当前项目路径绑定
│   ├─ project_manager.py           # 项目 CRUD + 子进程管理
│   └─ human_in_loop.py             # HITL 关卡
│
├─ utils/                           # 通用工具
│   ├─ json_utils.py                # 带重试的 LLM JSON 请求
│   ├─ context_manager.py           # 上下文窗口管理
│   ├─ invariants.py                # 不变量检查
│   ├─ validators.py                # 数据校验
│   ├─ ops_tracker.py               # 操作日志
│   ├─ prompts_registry.py
│   └─ concurrency.py
│
├─ agents/                          # 60+ 个职能型 agent
│   │   每个 agent 做一件事：分析意图 / 设计人物 / 写章 / 审校 / 等
│   │   详见 ARCHITECTURE.md 的 agent 分层
│   └─ ...
│
├─ web/                             # Flask Web UI
│   ├─ app.py                       # 主服务（REST API）
│   ├─ rewrite_chapter.py           # 重写一章入口
│   ├─ write_next_chapter.py        # 写下一章入口
│   ├─ regenerate.py                # 重生某个 phase
│   ├─ vendor_loader.py
│   └─ static/                      # 前端 HTML/JS/CSS
│
└─ prompts/                         # prompt 覆盖配置
    └─ overrides.json
```

详细分层 + 30 phase 流程图见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

---

## 30 阶段流水线一览

```
G1 意图组（4 phase）
  -1 意图分析 → 0 立项三件套 → 0.5 全书蓝图 → 0.6 主角内核

G2 世界组（9 phase）
  1A 力量体系 → 1A2 力量刻度 → 1B 卷结构 → 1C 势力 →
  1D 世界观 → 1E 校验 → 1F 地理 → 1G 时间线 → 1H 经济

G3 人物组（6 phase）
  2 人物档案 → 2A2 深化 → 2B 关系网 → 2C 特殊能力 →
  2D 心理弧 → 2C2 能力路线图（lifecycle）

G4 情节组（11 phase）
  3A 全局叙事线 → 3B 卷内叙事线 → 3B2 冲突阶梯 → 3C 爽点系统 →
  3D 节奏 → 3D2 情绪曲线 → 3E3 反转链 → 3E 伏笔 →
  3E2 红鲱鱼 → 3F 机缘 → 3G 主角历程

卷级规划（每卷 5 phase）
  4_stage 卷舞台 → 4_beats 主角节拍 → 4_vol 章节大纲 →
  4_ctp 章节类型 → 4_lifecycle 能力节点落章

章级写作（每章 20+ step）
  生成 directive → 场景蓝图 → 能力规划 → 写初稿 → 真 AI 占位替换 →
  连续性 / 口吻 / critic 审校循环 → setup_reviewer → canon_checker →
  敏感词 → HITL 关卡 → 状态回写 → 章后审计（能力/读者/对话）→
  blueprint_compliance → drift_detector
```

---

## 配置说明

### `config.py`（全局参数）

- `MAX_REVISION_ROUNDS`：critic 审校循环最大轮数
- `STAGE_REVIEW_MAX_REWRITE_ROUNDS`：stage 级重写循环上限
- `VOLUME_REVIEW_MAX_REWRITE_ROUNDS`：卷级重写循环上限
- `DRIFT_CHECK_EVERY_N_CHAPTERS`：每 N 章做一次漂移检测
- `WORDS_PER_CHAPTER`：单章字数目标
- `HITL_MODE`：`auto` / `pause` / `skip`
- `PARALLEL_WORKERS`：并发 agent 工作进程数

### `user_models.json`（LLM 路由）

每个模型一条记录，`usage` 字段决定它充当哪些角色：
- `main` — 写作主模型
- `reviewer` — 审核员模型
- `fallback` — 主模型失败兜底
- 不带 `usage` 的 profile（如豆包）由 `state.power_system.special_abilities[*].external_llm_profile` **显式按 id 绑定**——专门做"小说里主角问 AI 时真调它"。

详见 `user_models.example.json`。

---

## 真 AI 接入（小说里主角问 AI）

如果你的小说主角金手指是「智能 AI / 系统 / 智能玉佩」类型（穿越带的豆包、未来手机里的助手、上古文明的量子智能体...），系统支持**正文里主角问它时真发给绑定的 LLM**，不是主写模型自己脑补。

机制：
1. `state.power_system.special_abilities[i].external_llm_profile` 绑 user_models 里某个 profile id
2. writer 写正文时用占位符 `[[ASK_AI:asset 名|具体问题]]`
3. 章节定稿前 `agents/external_ai_query.py` 扫描占位 → 真发给绑定的 LLM 拿回答 → 替换占位

**功能边界**（在 writer prompt 里硬约束）：
- ✅ 现代真实世界已有的知识 / 普世原理（科学 / 工程 / 商业 / 现代法理 / 数学 / 现代世界史案例）
- ❌ 本书虚构设定的专有信息（自创律法条文 / 当朝具体人事 / 虚构地名行情）
- ❌ 预言未来 / 占卜吉凶
- ★ 正确套路：AI 给「现代普世知识」 + 主角靠自己拿「本书设定里的当地信息」 → 主角自己组合做决策

详见 `agents/writer.py` 的 `_format_external_ai_constraint`。

---

## 章级清理（删章/重写时同步回滚的派生数据）

删除或重写章节时，`chapter_cleanup.py` 会同步清理 **18 类**派生状态——避免旧数据污染新章：

记忆 / 角色状态快照 / 世界事件 / 张力曲线 / 爽点触发 / 伏笔状态 / 红鲱鱼状态 / 机缘状态 / 叙事线进度 / 故事线尾部 / 章节审计（能力/读者/对话）/ 主角实力日志 / canon 审计 / lifecycle 节点状态 / 感情线事件 ...

详见 `chapter_cleanup.py` 顶部注释。

---

## 已知限制

- **单机运行**：当前没做分布式；规划期 30 phase 在本机串行/并发跑
- **生成速度**：单章 5-15 分钟（取决于 LLM 速度、审校轮数）
- **token 消耗**：跑完整规划期约 30-50 元，写一本 N 章小说额外 N × 0.5-2 元
- **质量依赖 LLM**：主模型越强，产物越好；推荐 GPT-4o / Claude Sonnet / DeepSeek V4 级别

---

## 贡献

欢迎 PR / Issue：
- 新的 agent（更细的审计 / 更专精的设计器）
- 新题材的 prompt 模板
- Web UI 改进
- 文档完善

代码风格：
- 写完一个 agent，跟 `agents/` 里的同类风格保持一致（独立函数 / dataclass / JSON 输入输出）
- 状态字段加进 `state.py`，序列化加进 `checkpoint.py` 对应的 `_load_xxx` 函数（**勿漏**——旧反序列化会丢字段）

---

## License

（自选）

---

## 鸣谢

LangGraph 团队提供的工作流编排思路。本项目当前是手撸调度（director.py），未来计划用 LangGraph 重构（v2 探索见 ../xiaoshuo_v2，本仓库暂不包含）。
