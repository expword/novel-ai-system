# 入门指南

第一次使用本系统的完整步骤。已经熟悉的人看 [README.md](./README.md) 的快速开始就够了。

---

## 1. 环境准备

### Python 版本

要求 **Python 3.10+**。检查：

```bash
python --version
```

### 推荐：用虚拟环境隔离依赖

避免污染系统 Python。任选一种：

#### 方案 A：venv（标准库自带）

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

#### 方案 B：conda

```bash
conda create -n xiaoshuo python=3.11 -y
conda activate xiaoshuo
```

激活后命令行前面会出现 `(venv)` 或 `(xiaoshuo)` 标记。**之后所有命令都在激活的环境下跑**。

### 装依赖

```bash
pip install -r requirements.txt
```

如果 pip 慢，加镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 验证装好了

```bash
python -c "import flask, openai; print('OK')"
```

输出 `OK` 即可。

---

## 2. 配置 LLM（最关键的一步）

### 拷贝配置模板

```bash
cp user_models.example.json user_models.json
# Windows PowerShell：copy user_models.example.json user_models.json
```

`user_models.json` 在 `.gitignore` 里，**不会上传到 git**——可以放心填 API key。

### 编辑 `user_models.json`

按你拥有的 API key 配置。最简版本（只配一个主模型）：

```json
{
    "models": [
        {
            "id": "main_openai",
            "display_name": "GPT-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-你的真实key",
            "model": "gpt-4o",
            "usage": ["main", "fallback"]
        }
    ]
}
```

也可以配多个模型分工：

```json
{
    "models": [
        {
            "id": "main_openai",
            "display_name": "主模型 · GPT-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-...",
            "model": "gpt-4o",
            "usage": ["main"]
        },
        {
            "id": "reviewer_anthropic",
            "display_name": "审核员 · Claude Sonnet",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "sk-ant-...",
            "model": "claude-sonnet-4-5",
            "usage": ["reviewer"]
        },
        {
            "id": "fallback_deepseek",
            "display_name": "兜底 · DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-...",
            "model": "deepseek-chat",
            "usage": ["fallback"]
        },
        {
            "id": "doubao_for_in_story_ai",
            "display_name": "豆包 · 小说里的叙事内 AI",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "your-doubao-key",
            "model": "doubao-pro-32k",
            "usage": []
        }
    ]
}
```

### `usage` 字段含义

| 取值 | 谁用它 |
|---|---|
| `main` | 写作主模型（chapter_planner / writer / 各种 designer）—— **必须至少配一个** |
| `reviewer` | 审核员（critic / setup_reviewer / module_reviewer 等）|
| `fallback` | 主模型调用失败时兜底重试 |
| 空数组 `[]` | 仅供 `state.power_system.special_abilities[*].external_llm_profile` 显式按 id 绑定——给"小说里主角问 AI"用 |

### 推荐配置

- **最低**：一个 `main` 模型（GPT-4o / Claude / DeepSeek 任一）—— 可写小说
- **推荐**：`main` + `reviewer` 两个不同模型（避免主模型既当运动员又当裁判）
- **完整**：再加 `fallback` 提高稳定性 + 一个叙事内 AI（如果你的金手指设定是 AI 类）

---

## 3. 第一次启动

### 启动 Web

```bash
python run_web.py
```

终端应该输出类似：

```
  🌐 前端启动：http://127.0.0.1:5000
 * Serving Flask app 'web.app'
 * Running on http://127.0.0.1:5000
```

**保持终端不要关**。Web 服务靠它持续提供。

打开浏览器访问 `http://127.0.0.1:5000`。

### 创建你的第一本小说

主页右上角 **+ 新建小说**。选简单路径：

**用选择题向导**（推荐新人）：
1. 你要写哪类故事？穿越
2. 故事走什么路子？AI 金手指逆袭
3. 主角是什么类型？老实人觉醒
4. 整体什么基调？热血理性
5. 给哪个平台？起点男频

→ 点 **生成意图**，系统会自动凑出 intent 文本，预览没问题点 **创建项目**。

或者**用高级模式**直接粘自己写的意图（200-500 字最佳）。

### 选 stepwise 模式（强烈建议第一次用）

新建后顶栏的 **模式** 切换：选 **stepwise**。

这样每个阶段组（意图/世界/人物/情节）跑完会**自动暂停**，让你检查产物。第一次跑系统时这个非常重要——容易发现规划偏差。

### 启动

点顶栏 **▶ 启动**。后台开始跑：

- Phase -1 意图分析（30s 左右）
- Phase 0 立项三件套
- Phase 0.5 全书蓝图
- Phase 0.6 主角内核

跑完 G1 4 个 phase（约 2-4 分钟、几毛钱 token），系统暂停。

### 审产物

左侧菜单 → 阶段 1 立项 → 逐个点开看：

- **✨ 创作意图**：题材识别对了吗？卖点抓得准吗？对标作品合适吗？
- **卖点定位**：钩子吸引人吗？
- **套路库**：拥抱/规避的套路对吗？
- **文风手册**：基调对吗？
- **全书蓝图**：故事前提 / 22 个角色槽位 / 6 层势力 / 10 关键节点——这是最重要的，要花 5-10 分钟仔细看
- **主角内核**：核心创伤 / 真实目标 / 致命弱点——这决定全书走向

**有问题**：
- 直接改字段保存
- 或者点该面板的 **🔄 重生** 重跑（消耗 token）

### 继续跑

审完点顶栏 **▶ 继续**。开始跑 G2 世界组（9 phase，约 5-10 分钟）。跑完又暂停。

依此类推：G3 人物（6 phase）→ G4 情节（11 phase）→ 卷级 5 phase × N 卷 → 章节写作开始。

---

## 4. 整本书跑下来要多久 / 多少钱？

| 阶段 | 时间 | 估算成本 |
|---|---|---|
| G1 意图（4 phase）| 2-4 分钟 | 0.5-2 元 |
| G2 世界（9 phase） | 5-10 分钟 | 2-5 元 |
| G3 人物（6 phase） | 5-10 分钟 | 2-5 元 |
| G4 情节（11 phase）| 8-15 分钟 | 4-10 元 |
| 卷级 5 phase × 6 卷 | 30-60 分钟 | 15-30 元 |
| 单章写作 | 5-15 分钟 | 0.5-2 元 |
| **完整 1 卷小说（80 章）** | **约 6-15 小时** | **50-150 元** |
| **完整 6 卷长篇（480 章）** | **约 40-90 小时** | **300-1000 元** |

实际成本看你的主模型选择：

- GPT-4o ≈ Claude Sonnet（贵但质量高）
- DeepSeek V4（便宜，质量中上）
- Gemini Flash Lite（最便宜，质量中等）

---

## 5. 常见坑

### Q: 启动后浏览器一直转圈？

**A**: 检查
1. 终端有没有报错（启动 `python run_web.py` 时报错会立刻停）
2. 5000 端口被占了？换：`python run_web.py --port 8080`
3. 挂了 VPN：把 VPN 绕过规则加 `127.0.0.1` 和 `localhost`

### Q: 跑了一会儿提示"LLM 熔断"？

**A**: 主模型连续失败超过阈值（config.py 里 `LLM_CB_FAILURE_THRESHOLD=5`），系统自动熔断 30 秒。原因：
- API key 配错
- 触发供应商限速
- 网络抖动
- 上下文过长被拒

修法：
- 看终端日志的具体错误
- 配 fallback 模型分流
- 调小 `LLM_RATE_LIMIT_RPM`

### Q: 跑到一半电脑断电 / 进程崩了？

**A**: 没事。重启 `python run_web.py`，在 web 顶栏选回项目，点 **▶ 继续**——LangGraph-style 断点恢复，自动从崩溃前的最后一个 phase 继续。

### Q: 生成的内容质量不行 / 跟我意图差太多？

**A**:
1. **第一次跑用 stepwise 模式**——每组都审，发现偏差立即重生该 phase，比"跑完整本再改"省 token
2. **意图写具体**——「写个穿越商战故事」 → 系统乱编；「主角 35 岁 AI 研究员穿越到大雍朝寒门秀才，三天内要还 30 两高利贷救母」→ 系统抓得准
3. **配高质量主模型**——DeepSeek / Gemini Flash 等便宜模型质量一般；GPT-4o / Claude Sonnet 显著好
4. **重写章节带反馈**——某章不满意直接重写，feedback 越具体越好

### Q: 我已经有 v1 的"牛马一世"这种项目数据想保留？

**A**: 项目数据在 `projects/<project_id>/` 目录。**不要传 GitHub**（.gitignore 已经排除）。在新机器上：
- 把 `projects/` 整个目录拷过去
- 把 `user_models.json` 拷过去（注意：含 API key，**走加密通道**）
- 装好环境后 `python run_web.py` —— 项目会被自动识别

### Q: 想从某个章节开始重新写？

**A**: Web UI 左侧菜单 → 章节列表 → 选章 → 顶部 **🗑️ 删除** → 选 mode：
- `only_this` 只删这一章（危险——后续章节可能跟这章失联）
- `this_and_after` 删这章以及之后所有（推荐——干净重头）
- `all` 删全书章节

删完会自动清 18 类章级派生数据（记忆 / 审计 / 关系演化等），然后点 **▶ 写下一章** 开始重写。

详细操作流程见 [USAGE_WEB.md](./USAGE_WEB.md)。

---

## 6. 下一步

| 想了解 | 文档 |
|---|---|
| Web UI 全部按钮/面板/操作 | [USAGE_WEB.md](./USAGE_WEB.md) |
| 系统架构 / 60 个 agent 分类 / 数据流 | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| 自己加新 agent / 新审计 / 新题材 | [ARCHITECTURE.md](./ARCHITECTURE.md) 的"扩展指南" |
