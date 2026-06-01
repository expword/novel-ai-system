# xiaoshuo_v2 使用手册

## 项目位置

```
F:/xiaoshuo_v2/                  ← v2 项目目录（这里）
F:/xiaoshuo/                     ← v1 原项目（被 adapter 桥复用，不动）
F:/xiaoshuo/langgraph/           ← LangGraph 框架源码（参考用，不动）
F:/xiaoshuo_v2/checkpoints.sqlite ← 所有项目 state 都在这一个 SQLite 数据库里
```

## Python 环境

```
D:/Anaconda/envs/hello/python.exe
```
  cd F:\xiaoshuo_v2
  D:\Anaconda\envs\hello\python.exe -m langgraph_cli dev --port 2024
LangGraph 已经装在这个 env 里（`langgraph` + `langgraph-checkpoint-sqlite`）。

## 8 个 CLI 命令一览

每个命令都加 `--project <id>` 指定项目（项目 id = LangGraph thread_id；每个项目独立 state）。

| 命令 | 干啥 | 何时用 |
|---|---|---|
| `new` | 新建项目并启动跑 | 起步 / 重启全跑 |
| `run` | 从断点续跑 | stepwise interrupt 暂停后 / 崩溃恢复后 |
| `state` | 看项目当前 state 概览（字段大小 + phases 进度） | 想知道项目跑到哪了 |
| `show` | 看某个字段的具体内容 | 想看 LLM 真生成的产物（master_outline 等） |
| `export` | 导出 state 到 JSON 文件 | 离线 review / 备份 |
| `list` | 列出所有项目 | 忘了起过哪些项目 |
| `reset` | 清掉某项目 checkpoint | 重新开始 |

---

## 实战路径

### 路径 1：起一个新项目跑完 G1（约 5-10 分钟，几毛钱 token）

**第 1 步：写一份"作者意图"文本**

随便起个文件，比如 `my_intent.txt`，写 200-500 字描述：主角是谁、穿越到哪、起点冲突、想要什么类型/风格：

```
主角林砚是 35 岁的 AI 实验室研究员，意外车祸后穿越到大雍朝（虚构古代王朝）...
（参考 F:/xiaoshuo_v2/test_real_min.py 里那段 INTENT）
```

**第 2 步：启动**

```powershell
D:/Anaconda/envs/hello/python.exe F:/xiaoshuo_v2/runner.py new ^
  --project my_novel ^
  --title "我的小说" ^
  --genre "穿越商战" ^
  --theme "寒门重生" ^
  --intent-file F:/xiaoshuo_v2/my_intent.txt ^
  --g1-only --auto
```

`--g1-only` 只跑 G1 4 节点（先验证 LLM 真路径），`--auto` 不暂停一路跑完。约 4 次 LLM 调用 + module_reviewer 偶尔重生，总 1-3 分钟。

**第 3 步：看产物**

```powershell
# 概览
runner.py state --project my_novel --g1-only

# 看完整全书骨架蓝图（约 10000+ 字符）
runner.py show --project my_novel --field master_outline --g1-only --full

# 看意图分析（题材/卖点/原型/对标作品）
runner.py show --project my_novel --field creative_intent --g1-only --full

# 看立项三件套
runner.py show --project my_novel --field concept_pitch --g1-only --full
runner.py show --project my_novel --field tone_manual --g1-only --full

# 看主角内核
runner.py show --project my_novel --field protagonist_journey --g1-only --full

# 一键导出整个 state 到文件离线看
runner.py export --project my_novel --g1-only --out my_novel.json
```

---

### 路径 2：跑完完整规划期 G1+G2+G3+G4（30 节点真路径）

⚠️ **大约 30-50 分钟 + 几十块钱 token**。30 个 LLM 调用 + 多个 module_reviewer 重生。

```powershell
# 启动（stepwise 默认开——每组末会暂停）
runner.py new --project full_real --title "..." --genre "..." --theme "..." --intent-file ...
```

stepwise 默认开启时：
1. **跑到 G1 末（phase_0.6）暂停**。检查 `runner.py state --project full_real`，确认 G1 产物没问题
2. **`runner.py run --project full_real`** 续跑 G2
3. 跑到 G2 末（phase_1H）暂停，再检查
4. **`runner.py run --project full_real`** 续跑 G3
5. 跑到 G3 末（phase_2C2）暂停
6. **`runner.py run --project full_real`** 续跑 G4
7. 跑到 G4 末（phase_3G）暂停
8. **`runner.py run --project full_real`** 跑卷级循环（按卷数 N 跑 N×5 个 phase）

如果不想暂停（一路跑通）：加 `--auto`。

如果某一步崩了（网络断、token 耗尽）：直接 `runner.py run --project full_real`，LangGraph 会自动从崩溃的那个 phase 续跑（前面跑过的都不会重跑）。

---

### 路径 3：纯 mock（不调 LLM，秒级，0 成本）

只验证 LangGraph 框架机制——节点编排 / cycle / interrupt 都能跑。

```powershell
# 跑完整 30 节点 + 卷级 + 章级嵌套 cycle
runner.py new --project mock_demo --title T --genre G --theme T --mock --auto

# 看跑过的字段
runner.py state --project mock_demo
runner.py show --project mock_demo --field master_outline
```

---

## 4 个回归测试脚本

确认 LangGraph 框架机制没坏：

```powershell
D:/Anaconda/envs/hello/python.exe F:/xiaoshuo_v2/test_resume.py        # 崩溃恢复
D:/Anaconda/envs/hello/python.exe F:/xiaoshuo_v2/test_interrupt.py     # stepwise interrupt
D:/Anaconda/envs/hello/python.exe F:/xiaoshuo_v2/test_chapter_cycle.py # critic cycle 3 场景
D:/Anaconda/envs/hello/python.exe F:/xiaoshuo_v2/test_real_min.py      # 真 LLM 调用一次（~30s + 几分钱）
```

---

## 怎么直接看 SQLite 里的数据

LangGraph 把 state 全部存在 `F:/xiaoshuo_v2/checkpoints.sqlite`。可以用任何 SQLite 浏览器（DB Browser for SQLite / DBeaver / VS Code SQLite 插件）打开看：

- `checkpoints` 表：每个 thread_id（项目 id）的所有快照，按 checkpoint_id 排序，每个节点跑完都会插一行
- `writes` 表：节点对 state 的修改记录

或者用脚本：

```python
import sqlite3, json
conn = sqlite3.connect(r'F:/xiaoshuo_v2/checkpoints.sqlite')
for row in conn.execute("SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id"):
    print(row)
```

---

## 当前项目状态

| 项目 id | 状态 | 怎么继续 |
|---|---|---|
| `g1_real` | G1 真路径已跑完（4 phase）| 已是真产物，可以查看 |
| `demo_full` | mock 跑完整 30 phase | 验证用 |
| `demo_cycle` | mock 跑完整规划 + 6 卷循环 = 60 phase | 验证用 |
| `nest_test` | mock 跑完嵌套 cycle（6 卷 × 3 章 = 18 章）| 验证用 |
| 其他 demo_* | 早期 mock 测试 | 用完即弃，可 reset |

要看 G1 真路径的全书蓝图：

```powershell
runner.py show --project g1_real --field master_outline --g1-only --full
```

---

## 接下来该往哪走（建议）

1. **跑一次自己的 intent**（你写一份 intent.txt）走路径 1，看 v2 实际能产出什么
2. **如果产物质量满意**：考虑路径 2（跑完整 30 phase 真路径，约 30-50 块钱）
3. **如果想接着完成 v2 业务功能**：剩下要做：章级真路径实测 + 章后审计 cycle + HITL 关卡 + Web UI（每项 0.5-3 天）
