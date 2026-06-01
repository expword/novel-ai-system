# LangGraph Studio 使用手册

LangGraph 官方做的可视化 IDE，比 v1 Flask Web UI 强得多——节点拓扑图 / 时间旅行 / 流式输出 / state 检查全有。

## 启动

在 PowerShell / Terminal 中：

```powershell
cd F:\xiaoshuo_v2
D:\Anaconda\envs\hello\python.exe -m langgraph_cli dev --port 2024
```

启动成功后会输出：

```
- API: http://127.0.0.1:2024
- Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
- API Docs: http://127.0.0.1:2024/docs
```

**保持终端不关**（langgraph dev 是长期前台进程；Ctrl+C 停止）。

## 浏览器访问

打开：
```
https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```

⚠️ 这是 LangSmith 网站的"前端壳"，但**所有数据都在本地 127.0.0.1:2024**——不会上传你的小说到 LangSmith。**无需注册 LangSmith 账户**也能用（noop auth）。

如果浏览器报跨域 / 拒绝连接，加上 `--browser` flag 让 CLI 自动开浏览器：

```powershell
D:\Anaconda\envs\hello\python.exe -m langgraph_cli dev --port 2024 --browser
```

## 你能在 Studio 里做的

### 1. 选 graph
左侧下拉菜单 3 个选项：
- **planning_mock**：30 节点完整规划 + 卷级 cycle + 章级嵌套（mock，秒级，0 成本）—— **强烈推荐先用这个熟悉**
- **planning_real**：同上，但调真 LLM —— **跑一次会消耗 token**，慎用
- **chapter_cycle_mock**：章内 critic 审校循环（4 节点）—— 看 cycle 怎么回头

### 2. 看节点拓扑图
Graph 视图自动渲染整个工作流的节点 + 边 + cycle 回头边。比命令行 mermaid 直观 10 倍。

### 3. New Thread（新建跑一次）
- 点 **+ New Thread**
- 在 Input 框填初始 state（JSON）：
  ```json
  {
    "project_id": "studio_demo_1",
    "title": "测试小说",
    "genre": "穿越",
    "theme": "重生",
    "intent_description": "主角是 35 岁 AI 研究员穿越..."
  }
  ```
- 点 **Submit**
- 看右侧实时流式输出每个节点完成、state 变化

### 4. 时间旅行（Studio 最强功能）
任何一个 checkpoint 都可以：
- 点它回到那一刻的 state
- **Fork**：从这个 checkpoint 创建新分支重新跑（比如 phase_0.6 跑出不满意的主角内核，fork 回去用不同 input 重跑）
- 这正是 v1 director.py 死活做不到的——v1 一旦写错只能 reset 重头

### 5. State 检查
任何时刻点 **State** 标签，看完整 state 字段树（NovelStateV2 所有字段）。可视化好，比 CLI `runner.py show` 直观。

### 6. Interrupt（暂停点编辑）
对 mock graph：planning_mock 没设 interrupt（一路跑完）。
对 stepwise：可以在 Studio 设置 interrupt_before / interrupt_after，让某节点前/后暂停，等用户在 Studio 里改 state 再继续。

## 几个直接体验项

### A. 跑一次 30 节点完整规划（mock，秒级）

1. 选 graph: **planning_mock**
2. New Thread, Input: `{"project_id": "demo", "title": "测试", "intent_description": "测试 intent"}`
3. Submit
4. 看节点一个个亮起（拓扑图实时染色）+ state 字段一个个填上
5. 跑完后看完整 state（30 phases_done + 24 个产物字段）

### B. 跑一次章内 critic cycle

1. 选 graph: **chapter_cycle_mock**
2. New Thread, Input: `{"chapter_index": 1, "max_rounds": 3}`
3. Submit
4. 看 cycle 真的回头：write_draft → critic_review → revise → critic_review → revise → critic_review → finalize（第 3 轮 pass）
5. 想模拟"3 轮才过"或"一次过"：在节点环境变量里改 `CHAPTER_CYCLE_PASS_AT_ROUND`

### C. 跑真路径 G1 4 节点（消耗 token）

1. 选 graph: **planning_real**
2. New Thread, Input:
   ```json
   {
     "project_id": "studio_real_1",
     "title": "我的小说",
     "genre": "穿越商战",
     "theme": "寒门重生",
     "intent_description": "<你的意图文本>"
   }
   ```
3. Submit
4. 等 1-3 分钟跑完 G1 4 节点（real 模式没设 stepwise 默认一路跑到 G4 末）
5. ⚠️ 想只跑 G1 不跑后面：用 CLI `runner.py new --g1-only --auto`

## CLI vs Studio 怎么选

| 场景 | 用哪个 |
|---|---|
| 写一份新 intent 跑 G1 看产物 | CLI `runner.py new` 简单 |
| 看节点拓扑图理解工作流 | Studio |
| 看每个节点中间产物 | Studio（实时染色 + state 检查）|
| 调试 cycle 跑几轮 / 哪个节点崩了 | Studio（时间旅行）|
| 批量跑多个项目 | CLI |
| 想从某 phase fork 重新跑 | Studio（Fork 按钮）|
| 看完整执行历史 | Studio 或 CLI `runner.py history` |

## 后台运行 + 端口冲突

如果 2024 端口被占，换：

```powershell
D:\Anaconda\envs\hello\python.exe -m langgraph_cli dev --port 8123
# 浏览器访问 https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123
```

`Ctrl+C` 停止服务（不是关浏览器）。

## 已知

- **数据隔离**：Studio 的 in-memory checkpointer 跟 CLI 的 `checkpoints.sqlite` 是两套——Studio 里跑的 thread 不会落到 v2 SQLite，反之亦然
- **没接 LangSmith**：Studio 只是前端壳，可以工作；如果想要云端 tracing 历史，需要去 langsmith.com 注册拿 API key + 设 `LANGSMITH_API_KEY` 环境变量

## 故障排查

| 现象 | 修法 |
|---|---|
| `pydantic_core._pydantic_core` ModuleNotFoundError | 升 Python 后 pydantic 的 C 扩展不兼容。`pip install --force-reinstall --no-deps pydantic pydantic-core` |
| 启动后浏览器打不开 Studio | 检查防火墙；浏览器手动输入 `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024` |
| `port 2024 is in use` | 换端口 `--port 8123` |
| Studio 显示 graph 但跑不动 | 看终端日志 stack trace；通常是节点函数报错 |
| 跑 planning_real 但没产物 | 检查 v1 user_models.json 里 main profile 的 API key 有效 |
