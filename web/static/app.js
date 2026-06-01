function app() {
  return {
    state: {},
    current: null,
    data: null,
    rawText: "",
    rawJson: false,
    hasEdits: false,
    error: "",
    flash: "",
    report: "",
    stateAudit: null,   // { sections: [...], summary: {ok,partial,empty} }
    auditActions: {},   // {section_key: regen_action_name} —— 哪些 section 可一键修复
    auditBusy: false,   // 正在跑修复（按钮防抖）
    auditFixLog: [],    // 本次复盘的日志 [{ts, icon, section, msg, status}]
    rewriteInspiration: "",                         // 重写弹窗里的灵感 textarea（已与章节灵感持久化绑定）
    // 提示词管理
    promptsData: null,                              // GET /api/prompts 响应
    promptEditor: null,                             // 当前编辑的条目（对象，null=关闭）
    promptBusy: false,
    showPromptDefault: false,                       // 切换显示代码默认值
    // 单章生成
    writingOne: false,           // 正在写单章（按钮防抖）
    nextUnwrittenIdx: 0,         // 下一个未写的章号
    totalChaptersDone: 0,        // 已写章数
    totalChaptersPlanned: 0,     // 规划总章数
    // 侧栏分组展开状态（持久化到 localStorage）
    sidebarOpen: {
      phase1: true, phase2: true, phase3: true, phase4: true,
      phase5: true, phase6: true, phasem: true, phaseaudit: true,
    },
    versions: [],
    showVersions: false,
    approvals: [],
    chapterModal: null,
    darkMode: true,
    network: null,
    moduleFlowNetwork: null,
    selectedFlowNodeId: "",
    flowVolume: 1,
    wealthChart: null,
    spChart: null,
    tensionChart: null,
    intentDraft: "",
    intentRegenDownstream: true,
    intentStartAfter: true,
    analyzing: false,
    creating: false,
    // creative_intent 面板的真实人物 chip 增删临时输入框
    newRealPerson: "",
    // 追加意图
    intentAddition: "",
    intentRefineRegen: true,              // 兼容：老字段，等同于 cascade_level=phase0
    intentCascadeLevel: "phase0",         // light | phase0 | full
    refining: false,
    refineResultDetails: null,            // full 精炼后的每模块结果
    // 多项目
    projects: [],
    currentProject: "",  // 不硬编码"main"——等 loadProjects 后从列表挑第一个
    projStatus: "idle",
    progressInfo: {},
    warningsExpanded: false,
    // 项目模式（auto=一键 / stepwise=逐步）+ 阶段组进度
    projMode: "auto",
    phaseGroups: [],          // [{id, name, done, phases_done, phases_total}]
    nextGroupId: null,
    currentGroupId: null,     // 最近一组已完成的组（用于 mark_reviewed / rollback 目标）
    frameworkReady: false,
    // 作者对某组已经做过二选一的决定（确定应用 / 取消应用），不能再选另一个
    // 切换到新组或回滚后会被清
    actedGroups: {},          // { [group_id]: 'reviewed' | 'rolled_back' }
    // ── 审核 modal(替换原 ✓/↺ 二选一)─────────────────────
    reviewModal: {
      open: false,
      groupId: "",
      groupName: "",
      phases: [],          // [{phase_id, name, section, regen_action}]
      activeTab: "",        // 当前选中的 phase_id
      tabData: {},          // { [phase_id]: {loading, data, error, busy} }
      // Phase 2:候选数据
      candidates: {},       // { [phase_id]: {loading, drafts:[], supported, activeVersion: 0} }
    },
    // 带反馈重生成的小弹窗
    feedbackModal: {
      open: false,
      phaseId: "",
      phaseName: "",
      text: "",
    },
    // 下一章写作（stepwise 框架就绪后显示）
    nextChapterIndex: 0,
    nextChapterInspiration: "",
    savingInspiration: false,
    // 下一章的已知条件（章节 preview）
    nextChapterPreview: null,       // 从 /chapter/<idx>/preview 拉回
    nextChapterPreviewLoading: false,
    nextChapterOutlineDirty: false, // 用户改了"已知条件"还没保存
    // 重建状态：{ action: "stages", arg: 1, startedAt: timestamp, secondsElapsed: 0 }
    regenRunning: null,
    _regenTimer: null,
    // 是否显示下一章卡片（用户可以主动关闭；新章号到来时重新打开）
    showNextChapterCard: true,
    // Stage / Volume 审查报告（懒加载）
    // stageReviewMap: { [stage_id]: { passed, issues: [...], loaded } }
    // volumeReviewMap: { [volume_idx]: { passed, issues: [...], loaded } }
    stageReviewMap: {},
    volumeReviewMap: {},
    expandedStageReviews: {},   // { [stage_id]: true } 控制详情展开
    expandedVolumeReviews: {},  // { [volume_idx]: true }
    reviewRunning: null,        // { kind: 'stage'|'volume', id }
    showNewProject: false,
    newProj: { id: "", title: "", genre: "玄幻", customGenreText: "", theme: "", intent_description: "", analyze_now: true, start_after: true, mode: "stepwise" },

    // ── 新建小说向导（选择题模式）─────────────────────
    wizardMode: true,                    // true=向导 6 步点击，false=高级 blank 表单
    wizardStep: 1,                       // 1..6
    wizardStepLabels: ["根基", "题材", "套路", "主角", "基调", "平台"],
    wizardPicks: {
      // Step 1 新增：故事根基（真实 / 虚构）
      reality_basis: "",       // real_history | real_adapted | fictional
      historical_setting: "",  // 真实模式下的朝代/时期/区域
      real_persons: [],        // 真实模式下要尊重的真实历史人物名单
      realPersonInput: "",     // chip 添加用的临时输入框
      // Step 2-6
      genre: "",
      customGenreText: "",   // 选 __custom__ 时用户自定义题材文本
      tropes: [],        // 可多选
      archetype: "",
      tone: "",
      audience: "",
      title: "",         // 小说标题——直接作为文件夹名
      extraNotes: "",    // 补充细节（可选，追加到合成意图末尾）
    },

    // 选项库——每组都有 emoji/标题/简短描述
    WIZARD_PRESETS: {
      genre: [
        { id: "玄幻",     emoji: "🔮", desc: "修炼者、超自然、大陆争霸" },
        { id: "仙侠",     emoji: "⚔️", desc: "修真问道、宗门斗争、飞升" },
        { id: "武侠",     emoji: "🗡️", desc: "江湖恩怨、武功绝学、侠义" },
        { id: "都市",     emoji: "🌆", desc: "现代城市、职场商战、异能" },
        { id: "科幻",     emoji: "🚀", desc: "未来星际、科技、机甲" },
        { id: "末世",     emoji: "☠️", desc: "灾变后、求生、丧尸废土" },
        { id: "悬疑",     emoji: "🔍", desc: "推理、犯罪、解谜" },
        { id: "克苏鲁",   emoji: "🐙", desc: "诡异非凡、邪神、不可名状" },
        { id: "宫斗",     emoji: "👑", desc: "后宫权谋、外戚朝堂、家族" },
        { id: "古代言情", emoji: "💮", desc: "朝代背景、才子佳人、权谋" },
        { id: "现代言情", emoji: "💖", desc: "都市爱情、总裁、校园" },
        { id: "青春校园", emoji: "🎒", desc: "学生时代、成长、暗恋" },
        { id: "网游",     emoji: "🎮", desc: "游戏世界、电竞、工会战" },
        { id: "历史",     emoji: "📜", desc: "真实朝代、政治军事、人物" },
        { id: "无限流",   emoji: "♾️", desc: "无限世界穿梭、副本求生" },
        { id: "系统流",   emoji: "📱", desc: "金手指系统、任务积分" },
      ],
      trope: [
        { id: "扮猪吃虎", emoji: "🐷", desc: "低调隐藏实力、关键时刻打脸" },
        { id: "反套路",   emoji: "🔄", desc: "拒绝俗套、逆向操作" },
        { id: "种田发育", emoji: "🌱", desc: "慢节奏、经营、积累逐强" },
        { id: "复仇打脸", emoji: "💢", desc: "仇恨驱动、一雪前耻" },
        { id: "逆袭",     emoji: "📈", desc: "从底层一步步爬升" },
        { id: "苟道发育", emoji: "🛡️", desc: "保命优先、低调苟住" },
        { id: "穿越",     emoji: "🌀", desc: "穿到另一世界/时空" },
        { id: "重生",     emoji: "⏮️", desc: "重回过去、改变命运" },
        { id: "扫地僧",   emoji: "🧙", desc: "表面平凡实为大佬" },
        { id: "快穿",     emoji: "🔀", desc: "切换多个世界完成任务" },
        { id: "爽文打脸", emoji: "💥", desc: "节奏快、高频打脸爆点" },
        { id: "腹黑权谋", emoji: "🎭", desc: "算计、阴谋、步步为营" },
      ],
      archetype: [
        { id: "逆袭型",   emoji: "🔥", desc: "从屌丝/废物一步步逆天" },
        { id: "天才型",   emoji: "🌟", desc: "天赋异禀、一路碾压" },
        { id: "苟道型",   emoji: "🐢", desc: "谨慎保命、猥琐发育" },
        { id: "腹黑型",   emoji: "😈", desc: "心机深沉、阴谋算计" },
        { id: "热血型",   emoji: "⚡", desc: "直率冲动、燃向" },
        { id: "成熟稳重", emoji: "🎩", desc: "成年人视角、理智冷静" },
        { id: "佛系",     emoji: "☯️", desc: "与世无争、平和" },
        { id: "冷漠毒舌", emoji: "❄️", desc: "口是心非、冷淡" },
      ],
      tone: [
        { id: "爽文快节奏", emoji: "🏎️", desc: "打脸爽点高密度、爽感至上" },
        { id: "古典诗意",   emoji: "🎋", desc: "文言韵味、意境悠远" },
        { id: "烟火气",     emoji: "🏮", desc: "日常细腻、市井人情" },
        { id: "沉郁深刻",   emoji: "🌌", desc: "厚重主题、人性探讨" },
        { id: "治愈温暖",   emoji: "☀️", desc: "温柔向、读完心暖" },
        { id: "黑暗重口",   emoji: "🩸", desc: "阴郁压抑、成人向" },
        { id: "轻松搞笑",   emoji: "🤣", desc: "欢乐向、段子梗密集" },
        { id: "现实冷峻",   emoji: "🧊", desc: "冷峻克制、现实基调" },
      ],
      audience: [
        { id: "起点男频",   emoji: "⚡", desc: "男频、爽文节奏、追求爆点" },
        { id: "晋江女频",   emoji: "💞", desc: "女频、感情细腻、文学性重" },
        { id: "番茄下沉",   emoji: "🍅", desc: "下沉市场、口语化、金手指足" },
        { id: "书旗/飞卢",  emoji: "📖", desc: "通俗娱乐向、梗密度高" },
        { id: "传统出版",   emoji: "📚", desc: "文学性、节奏慢、深度" },
        { id: "轻小说/二次元", emoji: "🎌", desc: "日式风格、脑洞向、年轻向" },
      ],
    },
    showLog: false,
    logText: "",
    autoRefresh: false,
    _logTimer: null,
    _statusTimer: null,
    // 模型切换
    currentStep: {},
    _progressTimer: null,
    showModelPicker: false,
    allModels: [],
    modelProviders: [],
    modelsByProvider: {},
    currentProfileId: "",
    currentProfile: null,
    // 用户自定义模型
    userModels: [],
    builtinProfiles: [],
    showBuiltinProfiles: false,
    showModelEdit: false,
    editingModel: { id: "", display_name: "", base_url: "", api_key: "", model: "", usage: ["main"], notes: "" },
    savingModel: false,
    // 章节重写
    rewriteModal: null,
    rewriteFeedback: "",
    rewriting: false,

    // 章节对话调整（不动骨架改笔触）
    chatModal: null,         // { index, title, volumeTitle } 打开时非空
    chatMessages: [],        // [{role, content, ts}]
    chatInput: "",
    chatPreview: "",         // 右侧流式预览的新章节正文
    chatStreaming: false,    // SSE 正在流
    chatOriginalText: "",    // 打开时从磁盘读的原正文（用于对比字数）
    chatAccepting: false,
    chatClearing: false,

    // 章后能力审计
    abilityAudits: {},       // {chapter_index: {score, issue_count, sev_counts, ...}}
    auditModal: null,        // 展开的审计详情 { index, title, audit }
    auditReRunning: false,
    // 读者视角审计
    readerAudits: {},        // {chapter_index: { overall_score, retention_estimate, ... }}
    readerModal: null,       // 展开详情 { index, title, audit }
    readerReRunning: false,
    // 对话质量审计
    dialogueAudits: {},
    dialogueModal: null,
    dialogueReRunning: false,
    // 按审计润色
    polishPreview: "",       // 右侧流式预览的润色版正文
    polishStreaming: false,
    polishAccepting: false,

    panelTitle() {
      const titles = {
        creative_intent: "1 · 创作意图",
        concept_pitch:   "2 · 市场定位 · 卖点",
        trope_library:   "2 · 市场定位 · 套路",
        tone_manual:     "2 · 市场定位 · 文风",
        world:           "3 · 世界 · 世界观",
        power_system:    "3 · 世界 · 力量体系",
        geography:       "3 · 世界 · 地理",
        timeline:        "3 · 世界 · 时间线",
        economy:         "3 · 世界 · 经济",
        factions:        "3 · 世界 · 势力格局",
        characters:       "4 · 人物 · 档案",
        relationship_web: "4 · 人物 · 关系网络",
        character_arcs:   "4 · 人物 · 心理弧光",
        book_structure:      "5 · 情节 · 全书起承转合",
        volumes:             "5 · 情节 · 卷结构",
        lines:               "5 · 情节 · 叙事线",
        conflict_ladder:     "5 · 情节 · 冲突阶梯",
        emotion_curve:       "5 · 情节 · 情绪曲线",
        rhythm_plans:        "5 · 情节 · 节奏",
        satisfaction_points: "5 · 情节 · 爽点",
        foreshadow_items:    "5 · 情节 · 伏笔",
        red_herrings:        "5 · 情节 · 红鲱鱼",
        twist_system:        "5 · 情节 · 反转系统",
        fortunes:            "5 · 情节 · 机缘",
        protagonist_journey: "5 · 情节 · 主角历程",
        story_stages:         "6 · 章节 · 叙事舞台",
        chapter_type_plans:   "6 · 章节 · 章节类型规划",
        completed_chapters:   "6 · 章节 · 已完成章节",
        glossary:     "⚙ 元系统 · 术语表",
        world_events: "⚙ 元系统 · 世界事件",
        approvals:    "⚙ 元系统 · HITL 审批",
        prompts:      "⚙ 元系统 · 📝 提示词管理",
        module_flow:  "⚙ 元系统 · 模块流程图",
        setup_ledger:   "⚙ 元系统 · 📒 setup 账本",
        flavor_advices: "⚙ 元系统 · 🧂 调味建议",
        platform_rules: "⚙ 元系统 · 📚 平台规则",
      };
      return titles[this.current] || (this.current ? this.current : "选择左侧模块开始");
    },

    canRegen() {
      return ["trope_library", "tone_manual", "conflict_ladder", "emotion_curve",
              "economy", "geography", "relationship_web", "power_system",
              "lines", "twist_system"].includes(this.current);
    },

    stageVolumes() {
      const nums = (this.data || [])
        .map(s => Number(s.volume))
        .filter(n => Number.isFinite(n) && n > 0);
      return [...new Set(nums)].sort((a, b) => a - b);
    },

    // 警告里有任何 level=error 的吗？——徽章变红
    hasErrorWarnings() {
      const ws = (this.currentStep && this.currentStep.warnings) || [];
      return ws.some(w => (w.level || "warn") === "error");
    },

    // phase id / group id → 用户能看懂的中文标签（与左侧大纲一致）
    PHASE_LABELS: {
      "-1": "意图分析", "0": "立项三件套", "0.5": "全书蓝图", "0.6": "主角内核",
      "1A": "力量体系", "1A2": "力量刻度",
      "1B": "卷结构", "1C": "势力格局", "1D": "世界观", "1E": "世界观校验",
      "1F": "地理", "1G": "时间线", "1H": "经济",
      "2": "人物档案", "2A": "人物档案", "2A2": "人物深化",
      "2B": "关系网络", "2C": "特殊能力", "2D": "心理弧光",
      "3A": "全局叙事线", "3B": "卷内叙事线", "3B2": "冲突阶梯",
      "3C": "爽点系统", "3D": "节奏", "3D2": "情绪曲线",
      "3E": "伏笔", "3E2": "红鲱鱼", "3E3": "反转系统",
      "3F": "机缘", "3G": "主角历程",
    },
    GROUP_LABELS: {
      "G1_intent": "立项", "G2_world": "世界",
      "G3_characters": "人物", "G4_plot": "情节", "G5_framework_ready": "框架就绪",
    },

    // 把 warning 的 source 字段（如 "phase:1E"）转成"Phase 1-E（世界观校验）"——
    // 让用户一眼对应到左侧大纲项，不用猜代号。
    prettyWarningSource(source) {
      if (!source) return "";
      // phase:X
      let m = source.match(/^phase:(.+)$/);
      if (m) {
        const pid = m[1];
        const label = this.PHASE_LABELS[pid] || "";
        return label ? `Phase ${pid}（${label}）` : `Phase ${pid}`;
      }
      // group:X
      m = source.match(/^group:(.+)$/);
      if (m) {
        const gid = m[1];
        const label = this.GROUP_LABELS[gid] || "";
        return label ? `阶段组（${label}）` : `阶段组（${gid}）`;
      }
      // review:X（模块审核）
      m = source.match(/^review:(.+)$/);
      if (m) {
        const pid = m[1];
        const label = this.PHASE_LABELS[pid] || "";
        return label ? `审核·${label}（${pid}）` : `审核·${pid}`;
      }
      // agent:X
      m = source.match(/^agent:(.+)$/);
      if (m) return `Agent: ${m[1]}`;
      // regen:X
      m = source.match(/^regen:(.+)$/);
      if (m) return `重建: ${m[1]}`;
      // chapter:N
      m = source.match(/^chapter:(\d+)$/);
      if (m) return `第 ${m[1]} 章`;
      // director:crash 等
      return source;
    },

    // currentStep.phase 字符串 → sidebar 项的 key（左侧大纲高亮用）
    // phase 字符串形如 "Phase 1-E" / "Phase 0.6" / "第3卷《X》叙事舞台设计" / "Wave #1"
    runningSidebarKey() {
      const raw = (this.currentStep && this.currentStep.phase) || "";
      if (!raw) return "";
      // 卷级 phase："第N卷《X》叙事舞台" / "章节大纲" / "章节类型"
      if (/叙事舞台/.test(raw)) return "story_stages";
      if (/章节大纲/.test(raw)) return "volumes";
      if (/章节类型/.test(raw)) return "chapter_type_plans";
      if (/章节循环|开始写作|正文写作/.test(raw)) return "completed_chapters";
      // 提取 phase id：匹配 "Phase X" 中的 X
      const m = raw.match(/Phase\s+([0-9.A-Z\-_]+)/i);
      const pid = m ? m[1].replace(/-/g, "") : "";
      const map = {
        "-1":  "creative_intent",
        "0":   "concept_pitch",
        "0.5": "book_structure",
        "0.6": "protagonist_journey",
        "1A":  "power_system",
        "1A2": "power_system",
        "1B":  "volumes",
        "1C":  "factions",
        "1D":  "world",
        "1E":  "world",
        "1F":  "geography",
        "1G":  "timeline",
        "1H":  "economy",
        "2":   "characters",
        "2A":  "characters",
        "2A2": "characters",
        "2B":  "relationship_web",
        "2C":  "power_system",
        "2D":  "character_arcs",
        "3A":  "lines",
        "3B":  "lines",
        "3B2": "conflict_ladder",
        "3C":  "satisfaction_points",
        "3D":  "rhythm_plans",
        "3D2": "emotion_curve",
        "3E":  "foreshadow_items",
        "3E2": "red_herrings",
        "3E3": "twist_system",
        "3F":  "fortunes",
        "3G":  "protagonist_journey",
      };
      return map[pid] || "";
    },

    // 当 running 项变了，自动展开它所在的 nav-group
    _autoExpandRunning() {
      const key = this.runningSidebarKey();
      if (!key || key === this._lastAutoExpandedFor) return;
      this._lastAutoExpandedFor = key;
      // sidebar key → group key
      const groupOf = {
        creative_intent: "phase1",
        concept_pitch: "phase2", trope_library: "phase2", tone_manual: "phase2",
        world: "phase3", power_system: "phase3", geography: "phase3", timeline: "phase3", economy: "phase3", factions: "phase3",
        characters: "phase4", relationship_web: "phase4", character_arcs: "phase4",
        book_structure: "phase5", volumes: "phase5", lines: "phase5",
        conflict_ladder: "phase5", emotion_curve: "phase5", rhythm_plans: "phase5",
        satisfaction_points: "phase5", foreshadow_items: "phase5", red_herrings: "phase5",
        twist_system: "phase5", fortunes: "phase5", protagonist_journey: "phase5",
        story_stages: "phase6", chapter_type_plans: "phase6", completed_chapters: "phase6",
      };
      const g = groupOf[key];
      if (g && !this.sidebarOpen[g]) {
        this.sidebarOpen[g] = true;
        try { localStorage.setItem("sidebarOpen", JSON.stringify(this.sidebarOpen)); } catch {}
      }
    },

    // 中文小说字数（与后端 state.count_chapter_words 一致）：
    // 汉字 + 英文 word + 数字，标点/空格/换行不算。
    countChapterWords(text) {
      if (!text) return 0;
      const cn  = (text.match(/[一-鿿㐀-䶿]/g) || []).length;
      const en  = (text.match(/[A-Za-z]+/g) || []).length;
      const num = (text.match(/\d+(?:\.\d+)?/g) || []).length;
      return cn + en + num;
    },

    toggleSidebar(key) {
      this.sidebarOpen[key] = !this.sidebarOpen[key];
      try {
        localStorage.setItem("sidebarOpen", JSON.stringify(this.sidebarOpen));
      } catch (e) { /* quota/隐身模式忽略 */ }
    },

    collapseAllSidebar() {
      Object.keys(this.sidebarOpen).forEach(k => this.sidebarOpen[k] = false);
      try { localStorage.setItem("sidebarOpen", JSON.stringify(this.sidebarOpen)); } catch(e) {}
    },

    expandAllSidebar() {
      Object.keys(this.sidebarOpen).forEach(k => this.sidebarOpen[k] = true);
      try { localStorage.setItem("sidebarOpen", JSON.stringify(this.sidebarOpen)); } catch(e) {}
    },

    async init() {
      // 恢复侧栏展开偏好
      try {
        const saved = localStorage.getItem("sidebarOpen");
        if (saved) {
          const obj = JSON.parse(saved);
          Object.keys(this.sidebarOpen).forEach(k => {
            if (typeof obj[k] === "boolean") this.sidebarOpen[k] = obj[k];
          });
        }
      } catch (e) { /* ignore */ }

      await this.loadProjects();
      // 默认项目：列表第一个
      if (this.projects.length > 0 && !this.currentProject) {
        this.currentProject = this.projects[0].id;
      }
      await this.refreshState();
      await this.refreshStatus();   // ★ 立即拉一次状态，不等 3 秒
      await this.loadVersions();
      await this.loadStateAudit();  // 审计报告（显示哪些模块未生成）
      await this.refreshNextUnwritten();  // 下一未写章号，用于单章按钮标签
      // 启动状态轮询（每 3 秒刷当前项目状态；每 10 秒刷项目列表捕捉新建）
      this._statusTimer = setInterval(() => this.refreshStatus(), 3000);
      this._projectListTimer = setInterval(() => this.loadProjects(), 10000);
    },

    async loadProjects() {
      try {
        const r = await fetch("/api/projects");
        if (r.ok) this.projects = await r.json();
      } catch (e) { this.error = "读项目列表失败：" + e.message; }
    },

    async refreshState() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/state?project=${encodeURIComponent(this.currentProject)}`);
        if (r.ok) this.state = await r.json();
      } catch (e) { /* ignore */ }
    },

    async refreshStatus() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/status`);
        if (r.ok) {
          const j = await r.json();
          this.projStatus = j.status;
          this.progressInfo = j.progress || {};
          this.currentStep = j.current_step || {};
          const p = this.projects.find(x => x.id === this.currentProject);
          if (p) { p.status = j.status; p.progress = j.progress; }

          // 无论状态如何，只要选了项目就持续拉 progress_status.json
          // 同步操作（web 分析/精炼）和子进程写作都会写这个文件
          this._ensureProgressTimer();
        }
      } catch (e) { /* ignore */ }
      // 拉模式 + 阶段组进度（独立失败不影响主流程）
      await this.refreshPhaseGroups();
    },

    async refreshPhaseGroups() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/next_phase_group?_t=${Date.now()}`);
        if (!r.ok) return;
        const j = await r.json();
        this.projMode = j.mode || "auto";
        this.phaseGroups = j.groups || [];
        this.nextGroupId = j.next_group_id;
        const prevCurrent = this.currentGroupId;
        this.currentGroupId = j.current_group_id;
        this.frameworkReady = !!j.framework_ready;
        // 当前已完成到的组发生变化 → stepwise 模式自动跳转到对应面板
        if (this.currentGroupId && this.currentGroupId !== prevCurrent) {
          this._maybeAutoJump(this.currentGroupId);
        }
        // 框架就绪后，顺便查下一章号
        if (this.frameworkReady) await this.refreshNextChapter();
      } catch (e) { /* ignore */ }
    },

    async setProjMode(mode) {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/mode`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode }),
        });
        if (r.ok) {
          this.projMode = mode;
          this.flash = `模式切为 ${mode === "stepwise" ? "逐步审核" : "一键到底"}`;
        }
      } catch (e) { this.error = e.message; }
    },

    // 阶段组 → 默认跳转到的 section
    _groupToSection(groupId) {
      return {
        G1_intent:           "creative_intent",
        G2_world:            "world",
        G3_characters:       "characters",
        G4_plot:             "lines",
        G5_framework_ready:  "completed_chapters",
      }[groupId] || null;
    },

    // 监听当前已完成到的组变化，自动跳转（每个组只跳一次）
    _maybeAutoJump(newCurrentGroupId) {
      if (!newCurrentGroupId) return;
      if (this.projMode !== "stepwise") return;
      const key = `autojump_${this.currentProject}_${newCurrentGroupId}`;
      try {
        if (sessionStorage.getItem(key) === "1") return;
      } catch (e) { /* ignore */ }
      const section = this._groupToSection(newCurrentGroupId);
      if (section && section !== this.current) {
        this.load(section);
      }
      try { sessionStorage.setItem(key, "1"); } catch (e) { /* ignore */ }
    },

    // 某个阶段组是否已经二选一过（true=不再允许按另一个）
    groupActed(gid) {
      return !!(gid && this.actedGroups[gid]);
    },
    groupActionLabel(gid) {
      const v = gid && this.actedGroups[gid];
      if (v === "reviewed")     return "✓ 已确认应用";
      if (v === "rolled_back")  return "↺ 已回滚";
      return "";
    },

    // ════════════ 审核 Modal ════════════
    async openReviewModal(groupId) {
      if (!this.currentProject || !groupId) return;
      this.reviewModal.open = true;
      this.reviewModal.groupId = groupId;
      this.reviewModal.groupName = this._groupNameOf(groupId);
      this.reviewModal.phases = [];
      this.reviewModal.activeTab = "";
      this.reviewModal.tabData = {};
      try {
        const r = await fetch(
          `/api/projects/${encodeURIComponent(this.currentProject)}/group_review_payload?group_id=${encodeURIComponent(groupId)}`
        );
        if (!r.ok) {
          this.reviewModal.phases = [];
          this.error = `审核界面加载失败:HTTP ${r.status}`;
          return;
        }
        const j = await r.json();
        this.reviewModal.phases = j.phases || [];
        if (this.reviewModal.phases.length) {
          await this.switchReviewTab(this.reviewModal.phases[0].phase_id);
        }
      } catch (e) {
        this.error = `审核界面加载异常:${e.message}`;
      }
    },

    closeReviewModal() {
      this.reviewModal.open = false;
      this.feedbackModal.open = false;
    },

    async switchReviewTab(phaseId) {
      if (!phaseId) return;
      this.reviewModal.activeTab = phaseId;
      // 同步顺手拉候选(可能有,可能没有)
      this.loadCandidates(phaseId).catch(() => {});
      // 若已加载过 section 数据则直接显示
      const cached = this.reviewModal.tabData[phaseId];
      if (cached && cached.data !== null && !cached.error) return;
      // 拉数据
      const phase = (this.reviewModal.phases || []).find(p => p.phase_id === phaseId);
      if (!phase) return;
      this.reviewModal.tabData[phaseId] = { loading: true, data: null, error: "", busy: false };
      try {
        const r = await fetch(this._api(`/api/section/${phase.section}`));
        if (!r.ok) {
          this.reviewModal.tabData[phaseId] = {
            loading: false, data: null, busy: false,
            error: `加载 section=${phase.section} 失败 HTTP ${r.status}`,
          };
          return;
        }
        const data = await r.json();
        this.reviewModal.tabData[phaseId] = { loading: false, data, error: "", busy: false };
      } catch (e) {
        this.reviewModal.tabData[phaseId] = {
          loading: false, data: null, busy: false,
          error: `网络异常:${e.message}`,
        };
      }
    },

    activeReviewPhaseName() {
      const pid = this.reviewModal.activeTab;
      const p = (this.reviewModal.phases || []).find(p => p.phase_id === pid);
      return p ? p.name : pid;
    },

    activeReviewRegenAction() {
      const pid = this.reviewModal.activeTab;
      const p = (this.reviewModal.phases || []).find(p => p.phase_id === pid);
      return p ? (p.regen_action || "") : "";
    },

    formatReviewData(data) {
      if (data === null || data === undefined) return "(无数据)";
      try {
        return JSON.stringify(data, null, 2);
      } catch (e) {
        return String(data);
      }
    },

    // ── 审核 modal 美化渲染：把 raw JSON 转成结构化 HTML ──
    // 输出供 x-html 用，所有外部字符串必须经 _escHtml() 转义
    _escHtml(str) {
      if (str === null || str === undefined) return "";
      return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    },

    // 字段名美化：snake_case → 中文（命中 map 时）或保留原名
    // 立项 4 个 phase（creative_intent/concept_pitch/master_outline/protagonist_journey）
    // 的字段优先映射；未命中字段直接显示原 key（程序员可以对回 state 字段）
    _prettyFieldName(k) {
      const map = {
        // 通用
        generated: "已生成", analyzed: "已分析", revisions: "修订记录",
        // creative_intent
        raw_description: "原始描述",
        suggested_title: "建议书名", suggested_genre: "建议题材",
        suggested_subgenre: "建议子类", suggested_theme: "建议主题",
        audience_hint: "读者倾向", age_group_hint: "年龄段",
        platform_hint: "平台倾向",
        selling_points_hints: "卖点候选", benchmark_hints: "对标作品",
        differentiation_hint: "差异化", embrace_tropes_hints: "拥抱套路",
        avoid_tropes_hints: "回避套路", preferred_sp_types_hints: "偏好爽点类型",
        villain_policy_hint: "反派策略", romance_policy_hint: "感情策略",
        harem_policy_hint: "后宫策略",
        protagonist_archetype_hint: "主角类型",
        world_tone_hint: "世界基调", narrative_voice_hint: "叙事人称",
        style_reference_hint: "风格参考", dialogue_style_hint: "对白风格",
        tone_summary: "整体气质", analyzer_notes: "分析师备注",
        // concept_pitch
        one_line_pitch: "一句话简介", core_selling_points: "核心卖点",
        target_audience: "目标读者", target_age_group: "年龄段",
        target_platform: "目标平台", reader_profile: "读者画像",
        benchmark_works: "对标作品", differentiation: "差异化定位",
        expected_total_words: "预计总字数",
        expected_volumes: "预计卷数",
        expected_completion_weeks: "预计完结周数",
        // master_outline
        story_premise: "故事前提", central_conflict: "核心冲突",
        thematic_core: "主题内核", character_slots: "角色槽位",
        slot_id: "槽位ID", role_tag: "角色类型", function: "功能定位",
        brief_hint: "简介", relationship_hint: "关系暗示",
        narrative_arc_hint: "弧光暗示",
        first_volume: "首登卷", last_volume: "退场卷",
        narrative_function: "叙事功能", support_role: "辅助角色",
        function_detail: "功能详情",
        // protagonist_journey
        protagonist_id: "主角ID", inner_wound: "内在创伤",
        outer_goal: "外在目标", inner_lie: "内在谎言",
        truth_to_learn: "终须明白的真相",
        controlling_belief: "支配信念", growth_arc: "成长弧光",
      };
      return map[k] || k;
    },

    // 递归把任意 JSON 渲染成 HTML 卡片
    // depth 仅用于控制嵌套时的样式（顶层 vs 内嵌）
    renderPretty(data, depth = 0) {
      // 空值
      if (data === null || data === undefined) {
        return '<span class="rmp-empty">—</span>';
      }
      // 布尔
      if (typeof data === "boolean") {
        return data
          ? '<span class="rmp-bool rmp-bool-true">✓ 是</span>'
          : '<span class="rmp-bool rmp-bool-false">✗ 否</span>';
      }
      // 数字
      if (typeof data === "number") {
        const s = Number.isFinite(data) ? data.toLocaleString("zh-CN") : String(data);
        return `<span class="rmp-num">${this._escHtml(s)}</span>`;
      }
      // 字符串
      if (typeof data === "string") {
        if (!data.trim()) return '<span class="rmp-empty">—</span>';
        const escaped = this._escHtml(data);
        // 多行 / 长文本：渲染成段落块，保留换行
        if (data.includes("\n") || data.length > 60) {
          return `<div class="rmp-text rmp-text-long">${escaped.replace(/\n/g, "<br>")}</div>`;
        }
        return `<span class="rmp-text">${escaped}</span>`;
      }
      // 数组
      if (Array.isArray(data)) {
        if (data.length === 0) return '<span class="rmp-empty">— (空)</span>';
        // 全部是原始类型 → chip 云
        const allPrimitive = data.every(
          (x) => x === null || typeof x !== "object"
        );
        if (allPrimitive) {
          const chips = data
            .map((x) => `<span class="rmp-chip">${this._escHtml(String(x))}</span>`)
            .join("");
          return `<div class="rmp-chips">${chips}</div>`;
        }
        // 否则 → 编号卡片列表
        const cards = data
          .map(
            (x, i) =>
              `<div class="rmp-card">` +
              `<div class="rmp-card-head">#${i + 1}</div>` +
              `<div class="rmp-card-body">${this.renderPretty(x, depth + 1)}</div>` +
              `</div>`
          )
          .join("");
        return `<div class="rmp-cards">${cards}</div>`;
      }
      // 对象
      if (typeof data === "object") {
        const keys = Object.keys(data);
        if (keys.length === 0) return '<span class="rmp-empty">— (空)</span>';
        const rows = keys
          .map((k) => {
            const niceKey = this._prettyFieldName(k);
            return (
              `<div class="rmp-field">` +
              `<div class="rmp-key" title="${this._escHtml(k)}">${this._escHtml(niceKey)}</div>` +
              `<div class="rmp-val">${this.renderPretty(data[k], depth + 1)}</div>` +
              `</div>`
            );
          })
          .join("");
        const cls = depth === 0 ? "rmp-obj rmp-obj-root" : "rmp-obj rmp-obj-nested";
        return `<div class="${cls}">${rows}</div>`;
      }
      return `<span>${this._escHtml(String(data))}</span>`;
    },

    async regenCurrentPhase() {
      const action = this.activeReviewRegenAction();
      if (!action) {
        this.error = "本项无 regen action,无法直接重生成";
        return;
      }
      const pid = this.reviewModal.activeTab;
      if (!confirm(`确认重新生成【${this.activeReviewPhaseName()}】?\n这会覆盖当前数据(自动留版本快照)。`)) return;
      const slot = this.reviewModal.tabData[pid] || { loading: false, data: null, error: "", busy: false };
      slot.busy = true;
      this.reviewModal.tabData[pid] = { ...slot };
      try {
        const r = await fetch(this._api(`/api/regen/${encodeURIComponent(action)}`), { method: "POST" });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = `重生成失败 HTTP ${r.status}: ${j.error || ""}`;
          slot.busy = false;
          this.reviewModal.tabData[pid] = { ...slot };
          return;
        }
        this.flash = `✓ 已重新生成【${this.activeReviewPhaseName()}】`;
        // 清缓存重拉
        this.reviewModal.tabData[pid] = { loading: false, data: null, error: "", busy: false };
        await this.switchReviewTab(pid);
      } catch (e) {
        this.error = `重生成网络异常:${e.message}`;
        slot.busy = false;
        this.reviewModal.tabData[pid] = { ...slot };
      }
    },

    regenWithFeedback() {
      const action = this.activeReviewRegenAction();
      if (!action) {
        this.error = "本项无 regen action,无法带反馈重生成";
        return;
      }
      this.feedbackModal.open = true;
      this.feedbackModal.phaseId = this.reviewModal.activeTab;
      this.feedbackModal.phaseName = this.activeReviewPhaseName();
      this.feedbackModal.text = "";
    },

    async submitFeedbackRegen() {
      const fb = (this.feedbackModal.text || "").trim();
      if (!fb) return;
      const action = this.activeReviewRegenAction();
      const pid = this.feedbackModal.phaseId;
      this.feedbackModal.open = false;
      const slot = this.reviewModal.tabData[pid] || { loading: false, data: null, error: "", busy: false };
      slot.busy = true;
      this.reviewModal.tabData[pid] = { ...slot };
      try {
        // user_feedback 走 query param,后端 regen 函数可选读取(本期 Phase 1 后端可能尚未消费,
        // 提示用户:目前 feedback 文本仅作为日志,Phase 2 才真正塞 prompt)
        const r = await fetch(
          this._api(`/api/regen/${encodeURIComponent(action)}?user_feedback=${encodeURIComponent(fb)}`),
          { method: "POST" }
        );
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = `带反馈重生成失败 HTTP ${r.status}: ${j.error || ""}`;
          slot.busy = false;
          this.reviewModal.tabData[pid] = { ...slot };
          return;
        }
        this.flash = `✓ 已用反馈重生成【${this.activeReviewPhaseName()}】(注:Phase 1 反馈未塞 prompt,Phase 2 实现)`;
        this.reviewModal.tabData[pid] = { loading: false, data: null, error: "", busy: false };
        await this.switchReviewTab(pid);
      } catch (e) {
        this.error = `带反馈重生成网络异常:${e.message}`;
        slot.busy = false;
        this.reviewModal.tabData[pid] = { ...slot };
      }
    },

    // ── Phase 2:3 候选生成 / 切换 / 选定 / 丢弃 ──────────────────
    async openCandidateGeneration() {
      const action = this.activeReviewRegenAction();
      const pid = this.reviewModal.activeTab;
      if (!action) {
        this.error = "本项无 regen action,无法生成 3 候选";
        return;
      }
      if (!confirm(`确认生成 3 个候选版本?\n这会调 LLM 3 次(单次约 30s,总耗时 1-3 分钟),不影响当前 state。`)) return;
      const slot = this.reviewModal.tabData[pid] || { loading: false, data: null, error: "", busy: false };
      slot.busy = true;
      this.reviewModal.tabData[pid] = { ...slot };
      try {
        const r = await fetch(
          this._api(`/api/phase_drafts/${encodeURIComponent(pid)}/generate?count=3`),
          { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
        );
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = `生成 3 候选失败 HTTP ${r.status}: ${j.error || ""}`;
          slot.busy = false;
          this.reviewModal.tabData[pid] = { ...slot };
          return;
        }
        this.flash = `✓ 已生成 ${j.generated || 0} 个候选,共 ${j.total_drafts || 0} 条`;
        // 加载候选列表
        await this.loadCandidates(pid);
        slot.busy = false;
        this.reviewModal.tabData[pid] = { ...slot };
      } catch (e) {
        this.error = `生成 3 候选网络异常:${e.message}`;
        slot.busy = false;
        this.reviewModal.tabData[pid] = { ...slot };
      }
    },

    async loadCandidates(phaseId) {
      if (!phaseId) return;
      const cand = this.reviewModal.candidates[phaseId]
        || { loading: false, drafts: [], supported: false, activeVersion: 0 };
      cand.loading = true;
      this.reviewModal.candidates[phaseId] = { ...cand };
      try {
        const r = await fetch(this._api(`/api/phase_drafts/${encodeURIComponent(phaseId)}`));
        if (!r.ok) {
          cand.loading = false;
          this.reviewModal.candidates[phaseId] = { ...cand };
          return;
        }
        const j = await r.json();
        cand.loading = false;
        cand.drafts = j.drafts || [];
        cand.supported = !!j.supported;
        if (cand.drafts.length && !cand.activeVersion) {
          cand.activeVersion = cand.drafts[0].version_index;
        }
        this.reviewModal.candidates[phaseId] = { ...cand };
      } catch (e) {
        cand.loading = false;
        this.reviewModal.candidates[phaseId] = { ...cand };
      }
    },

    activeCandidates() {
      const pid = this.reviewModal.activeTab;
      return this.reviewModal.candidates[pid] || null;
    },

    activeCandidatePayload() {
      const cand = this.activeCandidates();
      if (!cand || !cand.drafts.length) return null;
      const target = cand.drafts.find(d => d.version_index === cand.activeVersion);
      return target ? target.payload : null;
    },

    setActiveVersion(version) {
      const pid = this.reviewModal.activeTab;
      const cand = this.reviewModal.candidates[pid];
      if (!cand) return;
      cand.activeVersion = version;
      this.reviewModal.candidates[pid] = { ...cand };
    },

    async selectCandidate() {
      const pid = this.reviewModal.activeTab;
      const cand = this.activeCandidates();
      if (!pid || !cand || !cand.activeVersion) return;
      const v = cand.activeVersion;
      if (!confirm(`确认采用候选 v${v}?\n会把这版写回 state,其他候选丢弃。`)) return;
      try {
        const r = await fetch(
          this._api(`/api/phase_drafts/${encodeURIComponent(pid)}/select?version=${v}`),
          { method: "POST" }
        );
        const j = await r.json().catch(() => ({}));
        if (!r.ok) {
          this.error = `选定失败 HTTP ${r.status}: ${j.error || ""}`;
          return;
        }
        this.flash = `✓ 已采用 v${v},其他候选已丢弃`;
        // 清候选 + 重拉本 phase 数据
        this.reviewModal.candidates[pid] = { loading: false, drafts: [], supported: true, activeVersion: 0 };
        this.reviewModal.tabData[pid] = { loading: false, data: null, error: "", busy: false };
        await this.switchReviewTab(pid);
      } catch (e) {
        this.error = `选定网络异常:${e.message}`;
      }
    },

    async discardCandidates() {
      const pid = this.reviewModal.activeTab;
      if (!pid) return;
      if (!confirm("确认丢弃所有候选?")) return;
      try {
        const r = await fetch(
          this._api(`/api/phase_drafts/${encodeURIComponent(pid)}/discard`),
          { method: "POST" }
        );
        if (!r.ok) {
          this.error = `丢弃失败 HTTP ${r.status}`;
          return;
        }
        this.flash = "✓ 已丢弃所有候选";
        this.reviewModal.candidates[pid] = { loading: false, drafts: [], supported: true, activeVersion: 0 };
      } catch (e) {
        this.error = `丢弃网络异常:${e.message}`;
      }
    },

    openPhaseSection() {
      const pid = this.reviewModal.activeTab;
      const p = (this.reviewModal.phases || []).find(p => p.phase_id === pid);
      if (!p) return;
      this.closeReviewModal();
      this.load(p.section);
    },

    async markGroupReviewed() {
      if (!this.currentProject) {
        this.error = "确认失败：当前没选中项目";
        return;
      }
      if (!this.currentGroupId) {
        this.error = "确认失败：当前没有已完成的阶段组。"
          + "可能项目还没开始跑，或后端 next_phase_group 接口暂未返回。";
        return;
      }
      if (this.groupActed(this.currentGroupId)) {
        const label = this.actedGroups[this.currentGroupId] === "reviewed" ? "确定应用" : "取消应用";
        this.error = `本阶段已点过「${label}」，不能再操作。`;
        return;
      }
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/stepwise/mark_reviewed`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group_id: this.currentGroupId }),
        });
        let j = {};
        try { j = await r.json(); } catch (_) {}
        if (r.ok) {
          this.flash = `✓ 已确认${this._groupNameOf(this.currentGroupId)}阶段的修改`;
          this.actedGroups = { ...this.actedGroups, [this.currentGroupId]: "reviewed" };
          this.error = "";
          await this.loadVersions();
        } else {
          this.error = `确认失败（HTTP ${r.status}）：${j.error || "未知错误"}`;
        }
      } catch (e) { this.error = `网络异常：${e.message}`; }
    },

    async rollbackGroup() {
      // ── 显式诊断：所有失败路径都给用户视觉反馈，不再静默 return ──
      if (!this.currentProject) {
        this.error = "回滚失败：当前没选中项目";
        return;
      }
      if (!this.currentGroupId) {
        this.error = "回滚失败：当前没有已完成的阶段组（currentGroupId 为空）。"
          + "可能原因：① 项目还没开始跑；② 你是 auto 模式跑的，没写回滚快照；"
          + "③ 后端 next_phase_group 接口暂时没返回——刷新页面试试。";
        return;
      }
      if (this.groupActed(this.currentGroupId)) {
        const label = this.actedGroups[this.currentGroupId] === "reviewed" ? "确定应用" : "取消应用";
        this.error = `本阶段已点过「${label}」，不能再操作。如需再次回滚，刷新页面后重试。`;
        return;
      }
      if (!confirm(`确认回滚【${this._groupNameOf(this.currentGroupId)}】阶段到完成时的快照？\n这会丢掉你在审核期间的所有编辑。`)) {
        this.flash = "已取消回滚操作";
        return;
      }
      this.flash = `⏳ 正在回滚【${this._groupNameOf(this.currentGroupId)}】...`;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/stepwise/rollback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group_id: this.currentGroupId }),
        });
        let j = {};
        try { j = await r.json(); } catch (_) {}
        if (r.ok) {
          this.flash = `↺ 已回滚到${this._groupNameOf(this.currentGroupId)}阶段完成时的状态`;
          this.actedGroups = { ...this.actedGroups, [this.currentGroupId]: "rolled_back" };
          this.error = "";
          // 用户已明确点「取消应用」=丢弃所有审核期编辑——先清 hasEdits 避免
          // load(section) 里的 "本模块有未保存的编辑，切换会丢失" confirm 弹窗
          // 拦截 reload，让前端真的拿到回滚后的内容
          this.hasEdits = false;
          // 刷所有可能被改的东西
          await this.refreshState();
          await this.refreshPhaseGroups();
          if (this.current) await this.load(this.current);
        } else {
          this.error = `回滚失败（HTTP ${r.status}）：${j.error || "未知错误"}`;
          this.flash = "";
        }
      } catch (e) {
        this.error = `回滚网络异常：${e.message}`;
        this.flash = "";
      }
    },

    _groupNameOf(id) {
      const g = (this.phaseGroups || []).find(x => x.id === id);
      return g ? g.name : id;
    },

    async refreshNextChapter() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/chapter/next_unwritten`);
        if (!r.ok) return;
        const j = await r.json();
        const idx = j.chapter_index || 0;
        if (idx !== this.nextChapterIndex) {
          this.nextChapterIndex = idx;
          this.nextChapterInspiration = "";
          this.nextChapterPreview = null;
          this.showNextChapterCard = true;   // 新章号 → 卡片重新出现
          if (idx > 0) {
            try {
              const r2 = await fetch(`/api/chapter_inspiration/${idx}?project=${encodeURIComponent(this.currentProject)}&_t=${Date.now()}`);
              if (r2.ok) {
                const j2 = await r2.json();
                this.nextChapterInspiration = j2.text || "";
              }
            } catch (e) { /* ignore */ }
            this.loadNextChapterPreview();
          }
        }
      } catch (e) { /* ignore */ }
    },

    async loadNextChapterPreview() {
      if (!this.currentProject || !this.nextChapterIndex) return;
      this.nextChapterPreviewLoading = true;
      try {
        const r = await fetch(
          `/api/projects/${encodeURIComponent(this.currentProject)}/chapter/${this.nextChapterIndex}/preview?_t=${Date.now()}`
        );
        if (r.ok) this.nextChapterPreview = await r.json();
      } catch (e) { /* ignore */ }
      finally { this.nextChapterPreviewLoading = false; }
    },

    async saveNextChapterOutline() {
      if (!this.currentProject || !this.nextChapterIndex || !this.nextChapterPreview) return;
      try {
        const r = await fetch(
          `/api/projects/${encodeURIComponent(this.currentProject)}/chapter/${this.nextChapterIndex}/outline`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              goal:           this.nextChapterPreview.outline_goal || "",
              purpose:        this.nextChapterPreview.outline_purpose || "",
              expression:     this.nextChapterPreview.outline_expression || "",
              chapter_focus:  this.nextChapterPreview.outline_chapter_focus || "",
              reader_hook:    this.nextChapterPreview.outline_reader_hook || "",
            }),
          }
        );
        if (r.ok) {
          this.flash = `✓ 第 ${this.nextChapterIndex} 章已知条件已保存`;
          this.nextChapterOutlineDirty = false;
        } else {
          this.error = "保存已知条件失败";
        }
      } catch (e) { this.error = e.message; }
    },

    reloadNextChapterPreview() {
      this.loadNextChapterPreview();
    },

    // ── Stage / Volume 审查报告 ──────────────────────
    async loadStageReviews(volumeIndex) {
      if (!this.currentProject) return;
      try {
        const url = volumeIndex
          ? `/api/stage_review_reports?project=${encodeURIComponent(this.currentProject)}&volume=${volumeIndex}&_t=${Date.now()}`
          : `/api/stage_review_reports?project=${encodeURIComponent(this.currentProject)}&_t=${Date.now()}`;
        const r = await fetch(url);
        if (!r.ok) return;
        const j = await r.json();
        for (const rep of (j.reports || [])) {
          this.stageReviewMap[rep.stage_id] = {
            ...rep, loaded: true,
          };
        }
      } catch (e) { console.warn("loadStageReviews:", e); }
    },

    async loadVolumeReviews(volumeIndex) {
      if (!this.currentProject) return;
      try {
        const url = volumeIndex
          ? `/api/volume_review_reports?project=${encodeURIComponent(this.currentProject)}&volume=${volumeIndex}&_t=${Date.now()}`
          : `/api/volume_review_reports?project=${encodeURIComponent(this.currentProject)}&_t=${Date.now()}`;
        const r = await fetch(url);
        if (!r.ok) return;
        const j = await r.json();
        for (const rep of (j.reports || [])) {
          this.volumeReviewMap[rep.volume] = { ...rep, loaded: true };
        }
      } catch (e) { console.warn("loadVolumeReviews:", e); }
    },

    async runStageReview(stageId) {
      if (!this.currentProject || this.reviewRunning) return;
      this.reviewRunning = { kind: "stage", id: stageId };
      try {
        const r = await fetch(
          `/api/review/stage/${encodeURIComponent(stageId)}?project=${encodeURIComponent(this.currentProject)}`,
          { method: "POST" }
        );
        if (r.ok) {
          await this.loadStageReviews();
          this.flash = `✓ Stage 审查完成`;
        } else {
          this.error = "Stage 审查失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.reviewRunning = null; }
    },

    async runVolumeReview(volumeIndex) {
      if (!this.currentProject || this.reviewRunning) return;
      this.reviewRunning = { kind: "volume", id: volumeIndex };
      try {
        const r = await fetch(
          `/api/review/volume/${volumeIndex}?project=${encodeURIComponent(this.currentProject)}`,
          { method: "POST" }
        );
        if (r.ok) {
          await this.loadVolumeReviews();
          this.flash = `✓ 卷审查完成`;
        } else {
          this.error = "卷审查失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.reviewRunning = null; }
    },

    stageReviewSummary(stageId) {
      const rep = this.stageReviewMap[stageId];
      if (!rep || !rep.loaded) return null;
      const c = (rep.issues || []).filter(i => i.level === "critical").length;
      const m = (rep.issues || []).filter(i => i.level === "major").length;
      const n = (rep.issues || []).filter(i => i.level === "minor").length;
      return { passed: rep.passed, critical: c, major: m, minor: n, total: c + m + n };
    },

    volumeReviewSummary(volumeIndex) {
      const rep = this.volumeReviewMap[volumeIndex];
      if (!rep || !rep.loaded) return null;
      const c = (rep.issues || []).filter(i => i.level === "critical").length;
      const m = (rep.issues || []).filter(i => i.level === "major").length;
      const n = (rep.issues || []).filter(i => i.level === "minor").length;
      return { passed: rep.passed, critical: c, major: m, minor: n, total: c + m + n };
    },

    async saveNextChapterInspiration() {
      if (!this.currentProject || !this.nextChapterIndex) return;
      this.savingInspiration = true;
      try {
        const r = await fetch(`/api/chapter_inspiration/${this.nextChapterIndex}?project=${encodeURIComponent(this.currentProject)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: this.nextChapterInspiration || "" }),
        });
        if (r.ok) {
          this.flash = `✓ 第 ${this.nextChapterIndex} 章灵感已保存`;
        } else {
          this.error = "保存灵感失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.savingInspiration = false; }
    },

    async writeNextChapter() {
      if (!this.currentProject || !this.nextChapterIndex) return;
      // 写之前先把 textarea 里的灵感存一下
      await this.saveNextChapterInspiration();
      this.writingOne = true;
      this.error = "";
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/chapter/write_next`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chapter_index: this.nextChapterIndex }),
        });
        const j = await r.json();
        if (r.ok && j.status === "ok") {
          this.flash = `✓ 第 ${j.chapter_index} 章已写完（${j.word_count} 字）`;
          this.nextChapterInspiration = "";
          await this.refreshPhaseGroups();
          if (this.current === "completed_chapters") await this.load("completed_chapters");
        } else {
          this.error = j.error || j.message || "写章失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.writingOne = false; }
    },

    _ensureProgressTimer() {
      if (this._progressTimer) return;
      this._progressTimer = setInterval(async () => {
        if (!this.currentProject) return;
        try {
          const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/progress`);
          if (r.ok) {
            const newStep = await r.json();
            // 章节计数变化 → 章节列表/相关视图自动刷新（避免用户手动 F5）
            const prevDone = (this.currentStep && typeof this.currentStep.chapters_done === "number")
              ? this.currentStep.chapters_done : -1;
            const newDone = (typeof newStep.chapters_done === "number") ? newStep.chapters_done : -1;
            this.currentStep = newStep;
            this._autoExpandRunning();
            if (newDone > prevDone && newDone >= 0 && prevDone >= 0) {
              await this._onChapterCountAdvanced(newDone);
            }
          }
        } catch (e) { /* ignore */ }
      }, 1500);
    },

    // chapters_done 增加时调一次：重新拉对应视图的数据
    async _onChapterCountAdvanced(newDone) {
      try {
        if (this.current === "completed_chapters") {
          await this.load("completed_chapters");
        }
        // "下一未写章号"按钮也得跟新
        await this.refreshNextUnwritten();
        // 顶部状态条里的进度也用得上
        await this.refreshState();
        this.flash = `✓ 第 ${newDone} 章已完成`;
      } catch (e) { /* ignore */ }
    },

    async clearProgressWarnings() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/warnings/clear`, { method: "POST" });
        if (r.ok) {
          if (this.currentStep) this.currentStep.warnings = [];
        }
      } catch (e) { /* ignore */ }
    },

    _stopProgressTimer() {
      if (this._progressTimer) {
        clearInterval(this._progressTimer);
        this._progressTimer = null;
      }
    },

    statusEmoji(st) {
      return { idle: "💤", running: "▶️", paused: "⏸", error: "⚠️" }[st] || "·";
    },

    // 所有 /api/ 调用自动带上 ?project=currentProject
    _api(path) {
      if (!this.currentProject) return path;
      const sep = path.includes("?") ? "&" : "?";
      return `${path}${sep}project=${encodeURIComponent(this.currentProject)}`;
    },

    async switchProject(pid) {
      if (this.hasEdits && !confirm("当前面板有未保存编辑，切换会丢失。继续？")) {
        // 回退
        this.currentProject = this.projects[0]?.id || "";
        return;
      }
      this.currentProject = pid;
      this.hasEdits = false;
      this.current = null;
      this.data = null;
      this.report = "";
      this.error = "";
      this.flash = "";
      // 清掉上个项目的实时状态，避免串线
      this.currentStep = {};
      this.progressInfo = {};
      this.projStatus = "idle";
      this._stopProgressTimer();
      // 弹窗/预览/流式——切项目时一律关掉
      this.chatModal = null;
      this.chatMessages = [];
      this.chatInput = "";
      this.chatPreview = "";
      this.chatOriginalText = "";
      this.chatStreaming = false;
      this.chatAccepting = false;
      this.chatClearing = false;
      this.rewriteModal = null;
      this.rewriteFeedback = "";
      this.rewriteInspiration = "";
      this.rewriting = false;
      this.auditModal = null;
      this.auditReRunning = false;
      this.polishPreview = "";
      this.polishStreaming = false;
      this.polishAccepting = false;
      this.chapterModal = null;
      this.promptEditor = null;
      this.showLog = false;
      this.showVersions = false;
      this.showModelPicker = false;
      this.showModelEdit = false;
      // 项目级缓存——新项目重新拉
      this.abilityAudits = {};
      this.readerAudits = {};
      this.readerModal = null;
      this.dialogueAudits = {};
      this.dialogueModal = null;
      this.versions = [];
      this.approvals = [];
      this.stateAudit = null;
      this.auditActions = {};
      this.auditFixLog = [];
      // stepwise 阶段组 & 下一章卡片——必须重置，否则跨项目看到旧进度
      this.phaseGroups = [];
      this.nextGroupId = null;
      this.currentGroupId = null;
      this.frameworkReady = false;
      this.projMode = "auto";
      this.actedGroups = {};
      this.nextChapterIndex = 0;
      this.nextChapterInspiration = "";
      this.nextChapterPreview = null;
      this.nextChapterPreviewLoading = false;
      this.showNextChapterCard = true;
      await this.refreshState();
      await this.refreshStatus();
      await this.loadVersions();
      await this.loadStateAudit();
      await this.refreshNextUnwritten();
      this.flash = `切到项目《${this.state.title || pid}》`;
    },

    // ── 向导导航 ─────────────────────────────────────
    wizardCanGoNext() {
      // Step 1：必须选根基；真实模式下还必须填历史背景
      if (this.wizardStep === 1) {
        const rb = this.wizardPicks.reality_basis;
        if (!rb) return false;
        if ((rb === "real_history" || rb === "real_adapted")
            && !(this.wizardPicks.historical_setting || "").trim()) return false;
        return true;
      }
      // Step 2：题材
      if (this.wizardStep === 2) {
        if (!this.wizardPicks.genre) return false;
        if (this.wizardPicks.genre === "__custom__"
            && !(this.wizardPicks.customGenreText || "").trim()) return false;
        return true;
      }
      if (this.wizardStep === 3) return this.wizardPicks.tropes.length > 0;
      if (this.wizardStep === 4) return !!this.wizardPicks.archetype;
      if (this.wizardStep === 5) return !!this.wizardPicks.tone;
      return true;
    },

    wizardCanSubmit() {
      const p = this.wizardPicks;
      // 根基 + 真实模式下的历史背景
      if (!p.reality_basis) return false;
      if ((p.reality_basis === "real_history" || p.reality_basis === "real_adapted")
          && !(p.historical_setting || "").trim()) return false;
      const genreOk = p.genre && (p.genre !== "__custom__" || (p.customGenreText || "").trim());
      return genreOk && p.tropes.length > 0 && p.archetype && p.tone && p.audience
             && p.title && p.title.trim().length > 0;
    },

    // creative_intent 面板：把 newRealPerson 输入框的人物 push 进 data.real_persons
    addRealPersonToIntent() {
      const v = (this.newRealPerson || "").trim();
      if (!v || !this.data) return;
      if (!Array.isArray(this.data.real_persons)) this.data.real_persons = [];
      const parts = v.split(/[、，,;；\s]+/).map(s => s.trim()).filter(Boolean);
      for (const p of parts) {
        if (!this.data.real_persons.includes(p)) {
          this.data.real_persons.push(p);
        }
      }
      this.newRealPerson = "";
      this.hasEdits = true;
    },

    // 真实人物 chip 增删（向导）
    wizardAddRealPerson() {
      const v = (this.wizardPicks.realPersonInput || "").trim();
      if (!v) return;
      // 允许"李世民、长孙皇后、魏征"这种逗号/顿号分隔
      const parts = v.split(/[、，,;；\s]+/).map(s => s.trim()).filter(Boolean);
      for (const p of parts) {
        if (!this.wizardPicks.real_persons.includes(p)) {
          this.wizardPicks.real_persons.push(p);
        }
      }
      this.wizardPicks.realPersonInput = "";
    },

    // 向导提交时取实际题材字符串（自定义/预设统一）
    wizardEffectiveGenre() {
      if (this.wizardPicks.genre === "__custom__") {
        return (this.wizardPicks.customGenreText || "").trim();
      }
      return this.wizardPicks.genre;
    },

    wizardNext() {
      if (!this.wizardCanGoNext()) return;
      if (this.wizardStep < 6) this.wizardStep++;
    },

    wizardPrev() {
      if (this.wizardStep > 1) this.wizardStep--;
    },

    wizardGoto(n) {
      // 只允许跳到已填过的步骤
      if (n < this.wizardStep) this.wizardStep = n;
    },

    wizardToggleTrope(id) {
      const tr = this.wizardPicks.tropes;
      const idx = tr.indexOf(id);
      if (idx >= 0) tr.splice(idx, 1);
      else if (tr.length < 3) tr.push(id);  // 最多 3 个
    },

    // 实时把 5 步选择合成自然语言意图
    get wizardFinalIntent() {
      const p = this.wizardPicks;
      if (!p.audience) return "";
      const stored = this._wizardIntentOverride;
      if (stored !== undefined) return stored;  // 用户手动编辑过就用编辑版
      return this.composeWizardIntent();
    },

    set wizardFinalIntent(val) {
      this._wizardIntentOverride = val;
    },

    composeWizardIntent() {
      const p = this.wizardPicks;
      // 自定义题材时用用户输入的文本
      const genre = (p.genre === "__custom__")
        ? ((p.customGenreText || "").trim() || "自定义题材")
        : p.genre;
      const tropes = p.tropes.join("、");
      const audience = p.audience;

      // ── 故事根基段（首段最显眼，强化下游 LLM 的注意力）──
      let basisLine = "";
      if (p.reality_basis === "real_history") {
        basisLine = `【故事根基】严格基于真实历史 —— 朝代/事件/人物言行须符合史料。`;
        if (p.historical_setting) basisLine += `历史背景：${p.historical_setting}。`;
        if (p.real_persons.length) basisLine += `须尊重的真实人物：${p.real_persons.join("、")}。`;
        basisLine += `\n`;
      } else if (p.reality_basis === "real_adapted") {
        basisLine = `【故事根基】基于真实人物/事件改编 —— 大方向尊重史料，细节可文学化演绎。`;
        if (p.historical_setting) basisLine += `历史背景：${p.historical_setting}。`;
        if (p.real_persons.length) basisLine += `须尊重的真实人物：${p.real_persons.join("、")}。`;
        basisLine += `\n`;
      } else if (p.reality_basis === "fictional") {
        basisLine = `【故事根基】完全虚构 —— 不受真实人物史料约束，可自由编撰。\n`;
      }

      let base = (
        basisLine +
        `我想写一本【${genre}】小说，面向【${audience}】读者。\n` +
        `故事主打【${tropes}】这类套路（${p.tropes.length}个方向）。\n` +
        `主角是【${p.archetype}】的类型——` + this.archetypeDetail(p.archetype) + `\n` +
        `整体基调【${p.tone}】——` + this.toneDetail(p.tone) + `\n` +
        `具体对标哪部作品、故事梗概、具体卖点，请由系统基于以上取向 + 平台风格自行推断。`
      );
      const notes = (p.extraNotes || "").trim();
      if (notes) {
        base += `\n\n【作者补充的细节 / 想特别强调的点】\n${notes}`;
      }
      return base;
    },

    archetypeDetail(id) {
      const opt = this.WIZARD_PRESETS.archetype.find(o => o.id === id);
      return opt ? opt.desc : "";
    },

    toneDetail(id) {
      const opt = this.WIZARD_PRESETS.tone.find(o => o.id === id);
      return opt ? opt.desc : "";
    },

    async wizardSubmit() {
      if (!this.wizardCanSubmit()) return;
      const composed = this.wizardFinalIntent;
      const title = this.wizardPicks.title.trim();
      const payload = {
        id: title,
        title: title,
        genre: this.wizardEffectiveGenre(),
        theme: "",
        intent_description: composed,
        analyze_now: this.newProj.analyze_now,
        start_after: this.newProj.start_after,
        mode: this.newProj.mode || "stepwise",
        // ── 故事根基（真实 / 虚构）──
        reality_basis: this.wizardPicks.reality_basis || "fictional",
        historical_setting: (this.wizardPicks.historical_setting || "").trim(),
        real_persons: [...this.wizardPicks.real_persons],
      };

      // 乐观 UI：立刻关 modal、展示 toast，不等后端
      const savedWizardState = JSON.parse(JSON.stringify(this.wizardPicks));
      this.showNewProject = false;
      this.wizardStep = 1;
      this.wizardPicks = {
        reality_basis: "", historical_setting: "", real_persons: [], realPersonInput: "",
        genre: "", customGenreText: "", tropes: [], archetype: "", tone: "",
        audience: "", title: "", extraNotes: "",
      };
      this._wizardIntentOverride = undefined;
      this.flash = `⏳ 正在创建《${title}》... 后端需要 ~10-30 秒，可在左侧项目列表看到新项目出现`;
      this.creating = true;
      this.error = "";

      try {
        const r = await fetch("/api/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const j = await r.json();
        if (r.ok) {
          let msg = "✓ 项目已创建：" + j.title;
          if (j.analysis_result) msg += " | " + j.analysis_result;
          if (j.start_result) msg += " | " + j.start_result;
          this.flash = msg;
          await this.loadProjects();
          this.currentProject = j.id;
          await this.switchProject(j.id);
          if (payload.intent_description) {
            await this.load("creative_intent");
          }
        } else if (r.status === 409) {
          this.error = `⏳ ${j.error || "该项目已有任务在运行"}——请等正在进行的任务完成`;
          this.wizardPicks = savedWizardState;
          this.showNewProject = true;
          this.wizardStep = 5;
        } else {
          this.error = `创建失败：${j.error || '未知错误'}`;
          this.wizardPicks = savedWizardState;
          this.showNewProject = true;
          this.wizardStep = 5;
        }
      } catch (e) {
        this.error = `网络错误：${e.message}`;
        this.wizardPicks = savedWizardState;
        this.showNewProject = true;
        this.wizardStep = 5;
      } finally {
        this.creating = false;
      }
    },

    async createProject() {
      if (!this.newProj.id || !this.newProj.title) return;
      // 高级模式：题材现在是直接 input，newProj.genre 就是用户输入的文本——不再需要 __custom__ 间接层
      const payload = { ...this.newProj };
      // 兜底：万一是空的（极端边界），用一个默认值，避免后端拒绝
      if (!(payload.genre || "").trim()) {
        this.error = "请填写题材（或在输入框点击下拉选预设）";
        return;
      }
      payload.genre = payload.genre.trim();
      delete payload.customGenreText;  // 高级模式不再使用，但 wizard 仍可能产出，清理掉

      this.creating = true;
      this.error = "";
      try {
        const r = await fetch("/api/projects", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const j = await r.json();
        if (r.ok) {
          let msg = "✓ 项目已创建：" + j.title;
          if (j.analysis_result) msg += " | " + j.analysis_result;
          if (j.start_result) msg += " | " + j.start_result;
          this.flash = msg;
          this.showNewProject = false;
          await this.loadProjects();
          this.currentProject = j.id;
          await this.switchProject(j.id);
          // 如果有意图，自动跳到意图面板让用户看分析结果
          if (this.newProj.intent_description) {
            await this.load("creative_intent");
          }
          // 重置表单
          this.newProj = { id: "", title: "", genre: "玄幻", customGenreText: "",
                           theme: "", intent_description: "",
                           analyze_now: true, start_after: true, mode: "stepwise" };
        } else {
          this.error = j.error || "创建失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.creating = false; }
    },

    async deleteProject() {
      if (!this.currentProject) return;
      if (!confirm(`确定删除项目【${this.currentProject}】？所有章节、规划、版本历史都会被清除。`)) return;
      if (!confirm("最终确认——此操作不可恢复！")) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}?force=true`, {
          method: "DELETE",
        });
        if (r.ok) {
          this.flash = "已删除";
          await this.loadProjects();
          this.currentProject = this.projects[0]?.id || "";
          if (this.currentProject) await this.switchProject(this.currentProject);
        } else {
          const j = await r.json();
          this.error = j.error || "删除失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async projectStart() {
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/start`, { method: "POST" });
        if (r.ok) {
          this.flash = "▶ 已启动";
          await this.refreshStatus();
        } else {
          const j = await r.json();
          this.error = j.error || "启动失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async projectPause() {
      try {
        await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/pause`, { method: "POST" });
        this.flash = "⏸ 已请求暂停（director 会在下个安全点停下）";
        await this.refreshStatus();
      } catch (e) { this.error = e.message; }
    },

    async projectStop() {
      if (!confirm("确定停止？当前进度会保存，但不会继续写。")) return;
      try {
        await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/stop`, { method: "POST" });
        this.flash = "⏹ 已停止";
        await this.refreshStatus();
      } catch (e) { this.error = e.message; }
    },

    async loadLog() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/log?lines=400`);
        if (r.ok) {
          const j = await r.json();
          this.logText = j.log || "";
          // 滚到底
          this.$nextTick(() => {
            const el = document.querySelector(".log-body");
            if (el) el.scrollTop = el.scrollHeight;
          });
        }
      } catch (e) { /* ignore */ }
    },

    toggleAutoRefresh() {
      if (this._logTimer) { clearInterval(this._logTimer); this._logTimer = null; }
      if (this.autoRefresh) {
        this._logTimer = setInterval(() => this.loadLog(), 3000);
      }
    },

    // ── 模型管理（用户自定义 + 按用途分配）─────────────
    usageLabel(u) {
      // 单个 usage 字符串 → 中文短标签
      return ({
        main: "🎯 主模型",
        reviewer: "🔍 审核",
        fallback: "🛟 备用",
        in_story_ai: "🎭 叙事内 AI",
        custom: "📌 自定义",
      }[u] || u || "—");
    },
    usageList(u) {
      // 容错：后端返 list；旧缓存可能返字符串/null
      if (Array.isArray(u)) return u.filter(x => x);
      if (typeof u === "string" && u) return [u];
      return [];
    },

    async loadModels() {
      try {
        // 加时间戳防缓存
        const r = await fetch("/api/llm_profiles?_t=" + Date.now());
        if (r.ok) {
          const j = await r.json();
          this.userModels = j.user_models || [];
          this.builtinProfiles = j.builtin_profiles || [];
          this.modelProviders = j.providers || [];
          this.allModels = [...this.userModels, ...this.builtinProfiles];
          // 调试日志——用户遇到"暂无模型"时方便看原因
          console.log(`[loadModels] user_models=${this.userModels.length}, builtin=${this.builtinProfiles.length}`);
          if (this.userModels.length === 0) {
            console.warn("[loadModels] user_models 为空——检查 F:/xiaoshuo/user_models.json 和 /api/llm_profiles 响应");
          }
        } else {
          console.error("[loadModels] HTTP", r.status);
          this.error = `加载模型失败 HTTP ${r.status}`;
        }
        if (this.currentProject) {
          const r2 = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/llm_profile?_t=` + Date.now());
          if (r2.ok) {
            const j2 = await r2.json();
            this.currentProfileId = j2.profile_id;
            this.currentProfile = j2.profile;
          }
        }
      } catch (e) {
        this.error = e.message;
        console.error("[loadModels] exception:", e);
      }
    },

    openModelEdit(existing) {
      // 后端返 usage 一定是 list；老缓存返字符串就归一化成 list
      const normUsage = (u) => Array.isArray(u) ? [...u] : (u ? [String(u)] : []);
      if (existing) {
        // 编辑：拷贝字段；api_key 留空让后端保持原值
        this.editingModel = {
          id: existing.id,
          display_name: existing.display_name || "",
          base_url: existing.base_url || "",
          api_key: "",  // 编辑时默认留空
          model: existing.model || "",
          usage: normUsage(existing.usage),
          notes: existing.notes || "",
        };
      } else {
        this.editingModel = {
          id: "", display_name: "", base_url: "https://", api_key: "",
          model: "", usage: ["main"], notes: "",
        };
      }
      this.showModelEdit = true;
    },

    onUsageToggle(usage, ev) {
      // UNIQUE_USAGES 互斥：本 model 勾上某 unique usage 时，
      // 如果别的 model 已占用同 usage → confirm 是否替换。
      // 取消勾选不做检查（用户自由）。
      if (!ev.target.checked) return;
      const UNIQUE = new Set(["main", "planner", "reviewer", "extractor"]);
      if (!UNIQUE.has(usage)) return;
      const myId = this.editingModel.id;
      const conflict = (this.userModels || []).find(m =>
        m.id !== myId && (m.usage || []).includes(usage)
      );
      if (!conflict) return;  // 无冲突
      const ok = confirm(
        `「${usage}」usage 当前已被 model "${conflict.id}" 占用。\n\n` +
        `保存时后端会自动从 "${conflict.id}" 移除「${usage}」，让本 model 接管。\n\n` +
        `确认替换？（取消则撤销本次勾选）`
      );
      if (!ok) {
        // 用户取消——回退 checkbox 状态
        this.editingModel.usage = (this.editingModel.usage || []).filter(u => u !== usage);
      }
    },

    async saveModelEdit() {
      if (!this.editingModel.display_name || !this.editingModel.base_url || !this.editingModel.model) {
        this.error = "显示名 / BASE_URL / MODEL 必填";
        return;
      }
      if (!this.editingModel.id && !this.editingModel.api_key) {
        this.error = "新增模型必须填 API_KEY";
        return;
      }
      // usage 允许空——不勾代表这条只是登记备用，不参与任何路由
      if (!Array.isArray(this.editingModel.usage)) {
        this.editingModel.usage = [];
      }
      this.savingModel = true;
      this.error = "";
      this.flash = "正在验证模型连通性...";
      try {
        const isEdit = !!this.editingModel.id;
        const url = isEdit
          ? `/api/user_models/${encodeURIComponent(this.editingModel.id)}`
          : `/api/user_models`;
        const method = isEdit ? "PUT" : "POST";
        const r = await fetch(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(this.editingModel),
        });
        const j = await r.json();
        if (r.ok) {
          const latency = j.test && j.test.latency_ms ? `（验证 ${j.test.latency_ms}ms）` : "";
          this.flash = (isEdit ? "✓ 模型已更新" : "✓ 模型已添加") + latency;
          this.showModelEdit = false;
          await this.loadModels();
        } else {
          this.error = j.error || "保存失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.savingModel = false; }
    },

    async deleteModel(modelId) {
      if (!confirm(`删除模型 "${modelId}"？\n（配置会被移除，后续使用此用途的 agent 将回退到内置默认）`)) return;
      try {
        const r = await fetch(`/api/user_models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
        if (r.ok) {
          this.flash = "✓ 已删除";
          await this.loadModels();
        } else {
          const j = await r.json();
          this.error = j.error || "删除失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async selectModel(profile_id) {
      if (!this.currentProject) return;
      if (profile_id === this.currentProfileId) return;
      if (!confirm(`切换到「${this.allModels.find(m=>m.id===profile_id)?.display_name}」？后续所有 LLM 调用都会用新模型（包括正在跑的子进程会在下次调用时切换）。`)) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/llm_profile`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profile_id }),
        });
        const j = await r.json();
        if (r.ok) {
          this.currentProfileId = profile_id;
          this.currentProfile = this.allModels.find(m => m.id === profile_id);
          this.flash = `✓ 已切换到 ${this.currentProfile?.display_name}`;
        } else {
          this.error = j.error || "切换失败";
        }
      } catch (e) { this.error = e.message; }
    },

    // ── 章节重写（合并灵感）────────────────────────
    async openRewrite(index, title) {
      this.rewriteModal = { index, title };
      this.rewriteFeedback = "";
      this.rewriteInspiration = "";
      // 预加载当前章节已存的灵感（如果有）
      try {
        const r = await fetch(this._api(`/api/chapter_inspiration/${index}?_t=${Date.now()}`));
        if (r.ok) {
          const j = await r.json();
          this.rewriteInspiration = j.text || "";
        }
      } catch (e) { /* 加载灵感失败不影响弹窗打开 */ }
    },

    async doRewrite() {
      if (!this.rewriteModal) return;
      const idx = this.rewriteModal.index;
      this.rewriting = true;
      this.error = "";
      try {
        // 1. 先保存/更新灵感（空串则清空持久化）
        //    directive.user_inspiration 会在 director 生成 directive 时自动读取
        const inspText = (this.rewriteInspiration || "").trim();
        try {
          await fetch(this._api(`/api/chapter_inspiration/${idx}`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: inspText }),
          });
        } catch (e) { /* 灵感保存失败不中断重写 */ }

        // 2. 触发重写
        const r = await fetch(
          `/api/projects/${encodeURIComponent(this.currentProject)}/chapter/${idx}/rewrite`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ feedback: this.rewriteFeedback || "" }),
          }
        );
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${j.chapter_index} 章已重写（${j.word_count} 字）`;
          this.rewriteModal = null;
          this.rewriteFeedback = "";
          this.rewriteInspiration = "";
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
          this.chapterModal = null;
          await this.refreshState();
          await this.loadVersions();
        } else {
          this.error = j.error || "重写失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.rewriting = false; }
    },

    // ── 章节对话调整（chat-based prose editing）─────────
    async openChat(index, title) {
      this.chatModal = { index, title };
      this.chatMessages = [];
      this.chatInput = "";
      this.chatPreview = "";
      this.chatOriginalText = "";
      this.chatStreaming = false;
      // 加载历史 + 原正文
      try {
        const [rHist, rChap] = await Promise.all([
          fetch(this._api(`/api/chapter/${index}/chat?_t=${Date.now()}`)),
          fetch(this._api(`/api/chapter/${index}?_t=${Date.now()}`)),
        ]);
        if (rHist.ok) {
          const j = await rHist.json();
          this.chatMessages = j.messages || [];
        }
        if (rChap.ok) {
          const j = await rChap.json();
          this.chatOriginalText = j.content || "";
          // 预览默认显示最后一条 assistant 消息（如有），否则显示原正文
          const lastAi = [...this.chatMessages].reverse().find(m => m.role === "assistant");
          this.chatPreview = lastAi ? lastAi.content : this.chatOriginalText;
        }
      } catch (e) { this.error = e.message; }
    },

    closeChat() {
      if (this.chatStreaming) {
        if (!confirm("正在流式生成，确认关闭？（已生成的部分会丢失）")) return;
      }
      this.chatModal = null;
      this.chatMessages = [];
      this.chatInput = "";
      this.chatPreview = "";
      this.chatStreaming = false;
    },

    async sendChat() {
      if (!this.chatModal || this.chatStreaming) return;
      const msg = (this.chatInput || "").trim();
      if (!msg) return;
      const idx = this.chatModal.index;

      // 先把用户消息塞进去，AI 占位
      this.chatMessages.push({ role: "user", content: msg, ts: new Date().toISOString() });
      this.chatInput = "";
      this.chatPreview = "";
      this.chatStreaming = true;
      this.error = "";

      try {
        const resp = await fetch(
          this._api(`/api/chapter/${idx}/chat/message`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: msg }),
          }
        );
        if (!resp.ok || !resp.body) {
          const t = await resp.text();
          this.error = `请求失败：${t}`;
          this.chatStreaming = false;
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        let streamEnd = false;   // error 或 done 都设 true，退出外层 while
        outer: while (!streamEnd) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // SSE 按 \n\n 分事件
          let sep;
          while ((sep = buffer.indexOf("\n\n")) !== -1) {
            const evt = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            // 只解析 data: 行
            for (const line of evt.split("\n")) {
              if (!line.startsWith("data:")) continue;
              const payload = line.slice(5).trim();
              if (!payload) continue;
              try {
                const obj = JSON.parse(payload);
                if (obj.type === "delta") {
                  this.chatPreview += obj.text;
                } else if (obj.type === "error") {
                  this.error = obj.message || "LLM 错误";
                  streamEnd = true;
                  try { reader.cancel(); } catch (_) {}
                  break outer;
                } else if (obj.type === "done") {
                  // 服务端已保存——追加 assistant 消息到本地
                  this.chatMessages.push({
                    role: "assistant",
                    content: this.chatPreview,
                    ts: new Date().toISOString(),
                  });
                  streamEnd = true;
                }
              } catch (e) { /* 忽略解析异常 */ }
            }
          }
        }
      } catch (e) {
        this.error = `流式中断：${e.message}`;
      } finally {
        this.chatStreaming = false;
      }
    },

    async acceptChat() {
      if (!this.chatModal || !this.chatPreview.trim()) return;
      if (!confirm(`确认采纳这一版？会覆盖第 ${this.chatModal.index} 章正文文件（有快照备份）`)) return;
      const idx = this.chatModal.index;
      this.chatAccepting = true;
      this.error = "";
      try {
        const r = await fetch(
          this._api(`/api/chapter/${idx}/chat/accept`),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: this.chatPreview }),
          }
        );
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${idx} 章已采纳（${j.word_count} 字）`;
          this.chatOriginalText = this.chatPreview;
          // 刷新已完成章节列表
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
          await this.loadVersions();
        } else {
          this.error = j.error || "采纳失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.chatAccepting = false; }
    },

    discardChat() {
      // 放弃预览——下一轮基于原正文/上次采纳版继续
      this.chatPreview = this.chatOriginalText;
    },

    async clearChat() {
      if (!this.chatModal) return;
      if (!confirm("清空本章全部对话历史？（章节正文不动）")) return;
      const idx = this.chatModal.index;
      this.chatClearing = true;
      try {
        const r = await fetch(this._api(`/api/chapter/${idx}/chat`), { method: "DELETE" });
        if (r.ok) {
          this.chatMessages = [];
          this.chatPreview = this.chatOriginalText;
          this.flash = "对话已清空";
        } else {
          this.error = "清空失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.chatClearing = false; }
    },

    // ── 章后能力审计 ────────────────────────────────
    async loadAbilityAudits() {
      try {
        const r = await fetch(this._api(`/api/ability_audits?_t=${Date.now()}`));
        if (!r.ok) return;
        const j = await r.json();
        const m = {};
        for (const it of (j.items || [])) {
          m[it.chapter_index] = it;
        }
        this.abilityAudits = m;
      } catch (e) { /* 静默 */ }
    },

    auditBadge(index) {
      const a = this.abilityAudits[index];
      if (!a) return null;
      const c = a.sev_counts || {};
      return {
        score: a.overall_score,
        cnt: a.issue_count,
        crit: c.critical || 0,
        maj: c.major || 0,
        min: c.minor || 0,
        summary: a.summary || "",
      };
    },

    // 读者视角审计 ────────────────────────────────────
    async loadReaderAudits() {
      try {
        const r = await fetch(this._api(`/api/reader_audits?_t=${Date.now()}`));
        if (!r.ok) return;
        const j = await r.json();
        const m = {};
        for (const it of (j.items || [])) m[it.chapter_index] = it;
        this.readerAudits = m;
      } catch (e) { /* 静默 */ }
    },

    readerBadge(index) {
      const a = this.readerAudits[index];
      if (!a) return null;
      const c = a.sev_counts || {};
      return {
        score: a.overall_score,
        retention: a.retention_estimate,
        emo: a.emotional_anchor,
        hook: a.hook_strength,
        cnt: a.issue_count,
        risks: a.risk_count || 0,
        crit: c.critical || 0,
        summary: a.summary || "",
      };
    },

    async openReaderAudit(index, title) {
      this.readerModal = { index, title, audit: null, loading: true };
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/reader_audit?_t=${Date.now()}`));
        if (r.ok) {
          const j = await r.json();
          this.readerModal.audit = j.audit;
        }
      } catch (e) { this.error = e.message; }
      finally { this.readerModal.loading = false; }
    },

    closeReaderAudit() { this.readerModal = null; },

    async rerunReaderAudit(index) {
      if (!confirm(`重新跑一次第 ${index} 章的读者视角审计？会消耗一次 LLM 调用。`)) return;
      this.readerReRunning = true;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/reader_audit`), { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${index} 章读者审计：${j.audit.overall_score}/10，留存估计 ${j.audit.retention_estimate}%`;
          if (this.readerModal && this.readerModal.index === index) {
            this.readerModal.audit = j.audit;
          }
          await this.loadReaderAudits();
        } else {
          this.error = j.error || "重审失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.readerReRunning = false; }
    },

    // 对话质量审计 ────────────────────────────────────
    async loadDialogueAudits() {
      try {
        const r = await fetch(this._api(`/api/dialogue_audits?_t=${Date.now()}`));
        if (!r.ok) return;
        const j = await r.json();
        const m = {};
        for (const it of (j.items || [])) m[it.chapter_index] = it;
        this.dialogueAudits = m;
      } catch (e) { /* 静默 */ }
    },

    dialogueBadge(index) {
      const a = this.dialogueAudits[index];
      if (!a) return null;
      const c = a.sev_counts || {};
      return {
        score: a.overall_score,
        subtext: a.subtext_density,
        distinct: a.voice_distinctiveness,
        infodump: a.infodump_level,
        ratio: a.dialogue_ratio_percent,
        cnt: a.issue_count,
        crit: c.critical || 0,
        summary: a.summary || "",
      };
    },

    // ── Batch 3:钩子类型 chip 中文映射 ────────────────────────────
    hookTypeLabel(t) {
      const map = {
        suspense: '悬', reversal: '转', info_reveal: '信',
        emotional: '情', physical: '物', death: '死', cliff: '崖',
      };
      return map[t] || '';
    },
    hookTypeFull(t) {
      const map = {
        suspense: '悬念钩', reversal: '反转钩', info_reveal: '信息钩',
        emotional: '情感钩', physical: '物理钩', death: '死亡钩', cliff: '悬崖钩',
      };
      return map[t] || t || '';
    },

    // ── Batch 5:模拟读者评论 chip / 弹窗 ─────────────────────────
    commentBadgeText(comments) {
      if (!comments || !comments.length) return '';
      const pos = comments.filter(c => c.sentiment === 'positive').length;
      const neg = comments.filter(c =>
        c.sentiment === 'negative' || c.sentiment === 'critical'
      ).length;
      let s = '💬' + comments.length;
      if (pos) s += ' 👍' + pos;
      if (neg) s += ' 👎' + neg;
      return s;
    },
    commentBadgeClass(comments) {
      if (!comments || !comments.length) return {};
      const pos = comments.filter(c => c.sentiment === 'positive').length;
      const neg = comments.filter(c =>
        c.sentiment === 'negative' || c.sentiment === 'critical'
      ).length;
      return {
        'audit-ok':   pos > neg && neg === 0,
        'audit-warn': neg > 0 && neg <= pos,
        'audit-bad':  neg > pos,
      };
    },
    openSimulatedComments(index, title, comments) {
      if (!comments || !comments.length) {
        this.report = '<strong>本章无模拟评论</strong>';
        return;
      }
      const grouped = { 追读派: [], 挑刺派: [], 路过派: [], 章评党: [] };
      comments.forEach(c => {
        (grouped[c.reader_type] || grouped['路过派']).push(c);
      });
      const esc = (s) => String(s || '').replace(/[&<>"']/g,
        (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
      let html = `<strong>第 ${index} 章《${esc(title)}》模拟读者评论 (${comments.length} 条):</strong>`;
      for (const [type, list] of Object.entries(grouped)) {
        if (!list.length) continue;
        html += `<h4 style="margin:0.6em 0 0.2em">${type} (${list.length})</h4><ul style="margin:0;padding-left:1.4em">`;
        list.forEach(c => {
          const emoji = c.sentiment === 'positive' ? '👍' :
            (c.sentiment === 'critical' || c.sentiment === 'negative') ? '👎' : '💬';
          html += `<li>${emoji} <b>${esc(c.nickname)}</b>: ${esc(c.text)}</li>`;
        });
        html += '</ul>';
      }
      this.report = html;
    },

    async openDialogueAudit(index, title) {
      this.dialogueModal = { index, title, audit: null, loading: true };
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/dialogue_audit?_t=${Date.now()}`));
        if (r.ok) {
          const j = await r.json();
          this.dialogueModal.audit = j.audit;
        }
      } catch (e) { this.error = e.message; }
      finally { this.dialogueModal.loading = false; }
    },

    closeDialogueAudit() { this.dialogueModal = null; },

    async rerunDialogueAudit(index) {
      if (!confirm(`重新跑一次第 ${index} 章的对话审计？`)) return;
      this.dialogueReRunning = true;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/dialogue_audit`), { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${index} 章对话审计：${j.audit.overall_score}/10`;
          if (this.dialogueModal && this.dialogueModal.index === index) this.dialogueModal.audit = j.audit;
          await this.loadDialogueAudits();
        } else {
          this.error = j.error || "重审失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.dialogueReRunning = false; }
    },

    async openAudit(index, title) {
      this.auditModal = { index, title, audit: null, loading: true };
      this.polishPreview = "";
      this.polishStreaming = false;
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/ability_audit?_t=${Date.now()}`));
        if (r.ok) {
          const j = await r.json();
          this.auditModal.audit = j.audit;
        }
      } catch (e) { this.error = e.message; }
      finally { this.auditModal.loading = false; }
    },

    closeAudit() {
      if (this.polishStreaming) {
        if (!confirm("正在流式润色，确认关闭？（已生成的部分会丢失）")) return;
      }
      this.auditModal = null;
      this.polishPreview = "";
      this.polishStreaming = false;
    },

    async rerunAudit(index) {
      if (!confirm(`重新审计第 ${index} 章？会消耗一次 LLM 调用。`)) return;
      this.auditReRunning = true;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/ability_audit`), { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${index} 章已重审（评分 ${j.audit.overall_score}，${j.audit.issues?.length || 0} 项问题）`;
          if (this.auditModal && this.auditModal.index === index) {
            this.auditModal.audit = j.audit;
          }
          await this.loadAbilityAudits();
        } else {
          this.error = j.error || "重审失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.auditReRunning = false; }
    },

    // ── 按审计结果润色（streaming）────────────────────
    async startPolish() {
      if (!this.auditModal || this.polishStreaming) return;
      const idx = this.auditModal.index;
      this.polishPreview = "";
      this.polishStreaming = true;
      this.error = "";
      try {
        const resp = await fetch(this._api(`/api/chapter/${idx}/polish`), { method: "POST" });
        if (!resp.ok || !resp.body) {
          const t = await resp.text();
          try {
            const j = JSON.parse(t);
            this.error = j.error || t;
          } catch { this.error = t; }
          this.polishStreaming = false;
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        let streamEnd = false;
        outer: while (!streamEnd) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let sep;
          while ((sep = buffer.indexOf("\n\n")) !== -1) {
            const evt = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            for (const line of evt.split("\n")) {
              if (!line.startsWith("data:")) continue;
              const payload = line.slice(5).trim();
              if (!payload) continue;
              try {
                const obj = JSON.parse(payload);
                if (obj.type === "delta") this.polishPreview += obj.text;
                else if (obj.type === "error") {
                  this.error = obj.message || "LLM 错误";
                  streamEnd = true;
                  try { reader.cancel(); } catch (_) {}
                  break outer;
                } else if (obj.type === "done") {
                  streamEnd = true;
                }
              } catch { /* ignore parse errors */ }
            }
          }
        }
      } catch (e) {
        this.error = `流式中断：${e.message}`;
      } finally {
        this.polishStreaming = false;
      }
    },

    async acceptPolish() {
      if (!this.auditModal || !this.polishPreview.trim()) return;
      if (!confirm(`采纳这一版润色？会覆盖第 ${this.auditModal.index} 章（有快照备份），并自动重审。`)) return;
      const idx = this.auditModal.index;
      this.polishAccepting = true;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${idx}/polish/accept`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: this.polishPreview }),
        });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${idx} 章润色已采纳（${j.word_count} 字）`;
          if (j.new_audit && this.auditModal) {
            this.auditModal.audit = j.new_audit;
          }
          this.polishPreview = "";
          await this.loadAbilityAudits();
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
          await this.loadVersions();
        } else {
          this.error = j.error || "采纳失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.polishAccepting = false; }
    },

    discardPolish() {
      this.polishPreview = "";
    },

    async load(section) {
      if (this.hasEdits && !confirm("本模块有未保存的编辑，切换会丢失。继续？")) return;
      this.current = section;
      this.data = null;
      this.hasEdits = false;
      this.rawJson = false;
      this.report = "";
      // 进入 power_system 面板时自动拉一次 user_models（能力面板的"绑外部 LLM"下拉用）
      if (section === "power_system" && (!this.userModels || this.userModels.length === 0)) {
        await this.loadModels().catch(() => {});
      }
      // Batch 2/6 新面板:走独立 endpoint,不是 /api/section/<name>
      const _customEndpoint = {
        setup_ledger:   "/api/setup_ledger",
        flavor_advices: "/api/flavor_advices",
        platform_rules: "/api/platform_rules",
      }[section];
      if (_customEndpoint) {
        try {
          const r = await fetch(this._api(_customEndpoint));
          if (!r.ok) { this.error = await r.text(); return; }
          this.data = await r.json();
          this.rawText = JSON.stringify(this.data, null, 2);
        } catch (e) { this.error = e.message; }
        return;
      }
      try {
        const r = await fetch(this._api(`/api/section/${section}`));
        if (!r.ok) { this.error = await r.text(); return; }
        this.data = await r.json();
        this.rawText = JSON.stringify(this.data, null, 2);
        // 意图面板：把 state 里已存的描述回填到草稿框；兜底新字段以免老 state 没这些 key
        if (section === "creative_intent") {
          this.intentDraft = this.data.raw_description || "";
          if (!this.data.reality_basis) this.data.reality_basis = "fictional";
          if (!Array.isArray(this.data.real_persons)) this.data.real_persons = [];
          if (this.data.historical_setting == null) this.data.historical_setting = "";
          if (this.data.respect_real_figures == null) this.data.respect_real_figures = false;
          this.newRealPerson = "";
        }
        if (section === "module_flow") {
          this.flowVolume = this.data.default_arg || this.flowVolume || 1;
          if (!this.selectedFlowNodeId && this.data.nodes && this.data.nodes.length) {
            this.selectedFlowNodeId = this.data.nodes[0].id;
          }
        }
        // 已完成章节面板：加载三种审计汇总
        if (section === "completed_chapters") {
          this.loadAbilityAudits();
          this.loadReaderAudits();
          this.loadDialogueAudits();
        }
        // 特殊面板的渲染副作用
        this.$nextTick(() => this.renderVisualizations());
      } catch (e) { this.error = e.message; }
    },

    refineButtonLabel() {
      if (this.intentCascadeLevel === "light") return "应用追加（只更新意图）";
      if (this.intentCascadeLevel === "phase0") return "应用追加 + 重建立项";
      if (this.intentCascadeLevel === "full") return "应用追加 + 完整增量精炼";
      return "应用追加";
    },

    refineModuleLabel(sec) {
      const map = {
        world_setting: "世界观",
        power_system: "体系",
        geography: "地理",
        timeline: "时间线",
        economy: "经济",
        factions: "势力",
        characters: "人物",
        relationship_web: "关系网",
        volumes: "卷结构",
      };
      return map[sec] || sec;
    },

    refineStatusLabel(status) {
      if (status === true) return "✓ 已精炼";
      if (status === "skipped") return "— 跳过";
      return "⚠ 未变";
    },

    refineModuleClass(status) {
      if (status === true) return "rm-ok";
      if (status === "skipped") return "rm-skip";
      return "rm-fail";
    },

    async refineIntent() {
      if (!this.intentAddition.trim()) return;
      if (this.intentCascadeLevel === "full") {
        const msg = [
          "【完整增量精炼】会对现有世界观/势力/人物/关系/卷做 LLM 级别的增量修改——",
          "不会清空已生成内容，只做 [保留 + 修正 + 新增] 的 additive merge。",
          "每个模块一次 LLM 调用，共约 9 次，耗时可能几分钟。",
          "",
          "已写章节不会回溯，只影响未来生成的章节。",
          "",
          "继续？",
        ].join("\n");
        if (!confirm(msg)) return;
      }
      this.refining = true;
      this.error = "";
      this.refineResultDetails = null;
      try {
        const r = await fetch(this._api("/api/refine_intent"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            addition: this.intentAddition,
            cascade_level: this.intentCascadeLevel,
          }),
        });
        const j = await r.json();
        if (r.ok) {
          this.data = j.creative_intent;
          this.intentDraft = j.creative_intent.raw_description || "";
          this.intentAddition = "";
          let msg = `✓ 第 ${j.creative_intent.revisions?.length || '?'} 轮追加已应用`;
          if (j.phase0_result) msg += " | " + j.phase0_result;
          if (j.cascade_result?.summary) {
            msg += " | " + j.cascade_result.summary;
            this.refineResultDetails = j.cascade_result.details;
          }
          this.flash = msg;
          await this.loadVersions();
          await this.refreshState();
        } else if (r.status === 409) {
          this.error = `⏳ ${j.error || "已有任务在运行"}——请等正在进行的任务完成（顶栏会显示进度）`;
        } else {
          this.error = j.error || "追加失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.refining = false; }
    },

    async reanalyzeAndRegenerate() {
      if (!this.intentDraft.trim()) return;
      // 双重确认——这个操作不可逆
      const ok1 = confirm(
        `【重新分析并重建】\n\n` +
        `这会彻底清空现有所有生成内容：\n` +
        `  ✗ 世界观 / 力量体系 / 地理 / 时间线 / 经济\n` +
        `  ✗ 势力格局 / 人物档案 / 关系网 / 心理弧\n` +
        `  ✗ 卷结构 / 叙事线 / 冲突阶梯 / 伏笔 / 机缘 / 主角历程\n` +
        `  ✗ 叙事舞台 / 章节类型 / 已完成章节（包括 .txt 文件）\n\n` +
        `保留：\n` +
        `  ✓ 项目元信息（标题 / 题材 / 主题）\n` +
        `  ✓ 你下面填的意图文本\n\n` +
        `然后按新意图从头重跑——几十分钟到几小时。\n\n` +
        `确定继续？`
      );
      if (!ok1) return;
      const ok2 = confirm("最后确认：真的清空并重新生成整本小说？此操作不可撤销！");
      if (!ok2) return;

      this.analyzing = true;
      this.error = "";
      this.flash = "⏳ 清空旧数据 + 重新分析意图 + 启动写作...";
      try {
        const r = await fetch(this._api("/api/reanalyze_intent"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            raw_description: this.intentDraft,
          }),
        });
        const j = await r.json();
        if (r.ok) {
          this.data = j.creative_intent;
          let msg = "✓ 已清空旧数据 + 重新分析意图";
          if (j.reset_summary) msg += " | " + j.reset_summary;
          if (j.start_result) msg += " | " + j.start_result;
          this.flash = msg;
          await this.loadVersions();
          await this.refreshStatus();
          await this.refreshState();
        } else {
          this.error = j.error || "重新分析失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.analyzing = false; }
    },

    async analyzeIntent(startAfter = false) {
      if (!this.intentDraft.trim()) return;
      // startAfter=true 表示点的是 🚀 一键按钮（强制触发完整流程）
      // startAfter=false 表示点的是 🔎 只分析（只走 Phase -1/0）
      const actualStartAfter = startAfter || this.intentStartAfter;
      const actualRegenDownstream = startAfter || this.intentRegenDownstream;
      this.analyzing = true;
      this.error = "";
      try {
        const r = await fetch(this._api("/api/analyze_intent"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            raw_description: this.intentDraft,
            regen_downstream: actualRegenDownstream,
            start_after: actualStartAfter,
          }),
        });
        const j = await r.json();
        if (r.ok) {
          this.data = j.creative_intent;
          let msg = "✓ 意图已分析";
          if (j.regen_downstream) msg += " | " + j.regen_downstream;
          if (j.start_after) msg += " | " + j.start_after;
          this.flash = msg;
          await this.loadVersions();
          await this.refreshStatus();
          await this.refreshState();
        } else if (r.status === 409) {
          this.error = `⏳ ${j.error || "已有任务在运行"}——请等正在进行的任务完成（顶栏会显示进度）`;
        } else {
          this.error = j.error || "分析失败";
        }
      } catch (e) { this.error = e.message; }
      finally { this.analyzing = false; }
    },

    // 动态加载 vis-network / Chart.js——只在真正要画图时才拉这 ~600KB
    async _loadVendorScript(src) {
      return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[data-vendor="${src}"]`);
        if (existing) {
          if (existing.dataset.loaded === "1") return resolve();
          existing.addEventListener("load", () => resolve());
          existing.addEventListener("error", reject);
          return;
        }
        const s = document.createElement("script");
        s.src = src;
        s.dataset.vendor = src;
        s.onload = () => { s.dataset.loaded = "1"; resolve(); };
        s.onerror = reject;
        document.head.appendChild(s);
      });
    },

    async ensureVisNetwork() {
      if (window.vis) return;
      try {
        await this._loadVendorScript("/static/vendor/vis-network.min.js");
      } catch (e) {
        // 本地 404 则退回 CDN
        await this._loadVendorScript("https://unpkg.com/vis-network/standalone/umd/vis-network.min.js");
      }
    },

    async ensureChartJs() {
      if (window.Chart) return;
      try {
        await this._loadVendorScript("/static/vendor/chart.umd.min.js");
      } catch (e) {
        await this._loadVendorScript("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js");
      }
    },

    flowNodeById(id) {
      return ((this.data && this.data.nodes) || []).find(n => n.id === id) || null;
    },

    selectedFlowNode() {
      return this.flowNodeById(this.selectedFlowNodeId) || ((this.data && this.data.nodes) || [])[0] || null;
    },

    flowLinkedNodes(ids) {
      return (ids || []).map(id => this.flowNodeById(id)).filter(Boolean);
    },

    async drawModuleFlowGraph() {
      if (!this.data || !Array.isArray(this.data.nodes)) return;
      await this.ensureVisNetwork();
      const container = document.getElementById("moduleFlowNetwork");
      if (!container || !window.vis) return;
      if (!this.selectedFlowNodeId && this.data.nodes.length) {
        this.selectedFlowNodeId = this.data.nodes[0].id;
      }
      const phaseColors = {
        "1 起点": "#3b82f6",
        "2 定位": "#22c55e",
        "3 蓝图": "#f59e0b",
        "4 世界": "#06b6d4",
        "5 人物": "#ec4899",
        "6 情节": "#8b5cf6",
        "7 章节": "#ef4444",
        "8 正文": "#94a3b8",
      };
      const nodes = this.data.nodes.map(n => ({
        id: n.id,
        label: n.label,
        title: `${n.phase}\n${n.desc || ""}`,
        shape: "box",
        margin: 10,
        color: {
          background: n.id === this.selectedFlowNodeId ? "#fbbf24" : "#1f2937",
          border: phaseColors[n.phase] || "#64748b",
          highlight: { background: "#fbbf24", border: "#f59e0b" },
        },
        font: { color: n.id === this.selectedFlowNodeId ? "#111827" : "#e5e7eb", size: 14 },
      }));
      const edges = (this.data.edges || []).map(e => ({
        from: e.from,
        to: e.to,
        arrows: "to",
        color: { color: "#64748b", highlight: "#fbbf24" },
        smooth: { type: "cubicBezier", forceDirection: "horizontal", roundness: 0.35 },
      }));
      if (this.moduleFlowNetwork) this.moduleFlowNetwork.destroy();
      this.moduleFlowNetwork = new vis.Network(container, { nodes, edges }, {
        layout: { hierarchical: { enabled: true, direction: "LR", sortMethod: "directed", levelSeparation: 150, nodeSpacing: 120 } },
        physics: false,
        interaction: { hover: true, navigationButtons: true, keyboard: true },
      });
      this.moduleFlowNetwork.on("click", params => {
        if (params.nodes && params.nodes.length) {
          this.selectedFlowNodeId = params.nodes[0];
          this.drawModuleFlowGraph();
        }
      });
    },

    async rebuildFlowNode(node) {
      if (!node || !node.can_rebuild) return;
      const label = node.label || node.id;
      if (!confirm(`确认重建【${label}】？\n\n会覆盖该模块当前生成结果，并自动留下版本快照。`)) return;
      if (node.rebuild_mode === "arg") {
        await this.regenArg(node.rebuild_action, this.flowVolume || 1);
      } else {
        await this.regen(node.rebuild_action);
      }
    },

    async renderVisualizations() {
      if (this.current === "module_flow") {
        await this.drawModuleFlowGraph();
      }
      if (this.current === "relationship_web") {
        await this.ensureVisNetwork();
        this.drawRelationshipGraph();
      }
      if (this.current === "economy") {
        await this.ensureChartJs();
        this.drawWealthChart();
      }
      if (this.current === "satisfaction_points") {
        await this.ensureChartJs();
        this.drawSpChart();
      }
      if (this.current === "completed_chapters") {
        await this.ensureChartJs();
        this.drawTensionChart();
      }
    },

    // ── 地理面板辅助 ─────────────────────────────────
    regionNameById(rid) {
      if (!rid || !this.data || !this.data.regions) return rid || "(未指定)";
      const r = this.data.regions.find(x => x.region_id === rid);
      return r ? r.name : rid;
    },
    importanceLabel(imp) {
      return {
        protagonist_active: "🎯 主角活跃",
        occasional: "🚶 途经",
        background: "🌫 背景",
      }[imp] || "🌫 背景";
    },
    sortedRegions(regions) {
      if (!regions) return [];
      // 按 importance 排序：active > occasional > background
      const order = { protagonist_active: 0, occasional: 1, background: 2 };
      return [...regions].sort((a, b) => {
        const oa = order[a.importance] ?? 2;
        const ob = order[b.importance] ?? 2;
        if (oa !== ob) return oa - ob;
        // 同级按 parent hierarchy
        return (a.level || "").localeCompare(b.level || "");
      });
    },

    drawRelationshipGraph() {
      if (!this.data || !this.data.bonds) return;
      const container = document.getElementById("network");
      if (!container) return;
      const nodesMap = {};
      const edges = [];
      for (const b of this.data.bonds) {
        nodesMap[b.char_a] = { id: b.char_a, label: b.char_a };
        nodesMap[b.char_b] = { id: b.char_b, label: b.char_b };
        edges.push({
          from: b.char_a, to: b.char_b,
          label: b.surface_relation,
          title: `表面：${b.surface_relation}\n真实：${b.true_relation}\n张力：${b.tension_source}\n未来：${b.future_trajectory}`,
          dashes: b.hidden_secret ? true : false,
        });
      }
      const nodes = Object.values(nodesMap);
      if (this.network) this.network.destroy();
      this.network = new vis.Network(container, { nodes, edges }, {
        nodes: { shape: "box", font: { color: "#eee" }, color: { background: "#334", border: "#88f" } },
        edges: { color: { color: "#888", highlight: "#f90" }, font: { color: "#aaa", size: 10 }, arrows: "" },
        physics: { solver: "forceAtlas2Based" },
      });
    },

    drawWealthChart() {
      if (!this.data || !this.data.protagonist_wealth_curve) return;
      const ctx = document.getElementById("wealthChart");
      if (!ctx) return;
      if (this.wealthChart) this.wealthChart.destroy();
      const curve = [...(this.data.protagonist_wealth_curve || [])].sort((a,b)=>a.volume-b.volume);
      const tiers = ["赤贫", "温饱", "小康", "富足", "巨富", "富可敌国"];
      this.wealthChart = new Chart(ctx, {
        type: "line",
        data: {
          labels: curve.map(w => "V" + w.volume),
          datasets: [{
            label: "主角财富",
            data: curve.map(w => tiers.indexOf(w.tier) >= 0 ? tiers.indexOf(w.tier) : 1),
            borderColor: "#fa0", backgroundColor: "rgba(255,170,0,0.2)", tension: 0.3,
          }],
        },
        options: {
          plugins: { tooltip: { callbacks: { label: ctx => curve[ctx.dataIndex].tier + "：" + curve[ctx.dataIndex].description } } },
          scales: { y: { ticks: { callback: v => tiers[v] || "" }, min: 0, max: tiers.length - 1 } },
        },
      });
    },

    drawSpChart() {
      if (!Array.isArray(this.data)) return;
      const ctx = document.getElementById("spChart");
      if (!ctx) return;
      if (this.spChart) this.spChart.destroy();
      const sorted = [...this.data].sort((a,b)=>a.target_chapter-b.target_chapter);
      this.spChart = new Chart(ctx, {
        type: "bar",
        data: {
          labels: sorted.map(s => "Ch" + s.target_chapter),
          datasets: [{
            label: "强度",
            data: sorted.map(s => s.intensity),
            backgroundColor: sorted.map(s => s.triggered ? "#4c7" : "#f90"),
          }],
        },
        options: {
          scales: { y: { max: 10, min: 0 } },
          plugins: { tooltip: { callbacks: { label: ctx => sorted[ctx.dataIndex].title + "(" + sorted[ctx.dataIndex].sp_type + ")" } } },
        },
      });
    },

    drawTensionChart() {
      if (!Array.isArray(this.data)) return;
      const ctx = document.getElementById("tensionChart");
      if (!ctx) return;
      if (this.tensionChart) this.tensionChart.destroy();
      const tensionMap = { "平静": 1, "上升": 2, "高潮": 4, "下落": 2, "反转": 3 };
      this.tensionChart = new Chart(ctx, {
        type: "line",
        data: {
          labels: this.data.map(c => c.index),
          datasets: [{
            label: "张力",
            data: this.data.map(c => tensionMap[c.tension] || 2),
            borderColor: "#f36", backgroundColor: "rgba(255,50,100,0.2)", tension: 0.3, pointRadius: 2,
          }],
        },
        options: { scales: { y: { max: 4, min: 0 } } },
      });
    },

    async save() {
      if (!this.current || this.data === null) return;
      // 清掉前端临时字段（以 _ 开头）
      const cleaned = this._cleanUnderscore(this.data);
      try {
        const r = await fetch(this._api(`/api/section/${this.current}`), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cleaned),
        });
        if (!r.ok) { this.error = await r.text(); return; }
        this.data = await r.json();
        this.rawText = JSON.stringify(this.data, null, 2);
        this.hasEdits = false;
        this.flash = "已保存 ✓ " + new Date().toLocaleTimeString();
        // 如果是立项类，提示重建下游
        if (this.current === "concept_pitch") {
          if (confirm("立项改了，是否重建【套路库+文风手册】？")) {
            await this.regen("after_concept_pitch");
          }
        }
        await this.loadVersions();
      } catch (e) { this.error = e.message; }
    },

    _cleanUnderscore(obj) {
      // 去掉 _xxx 临时字段
      if (Array.isArray(obj)) return obj.map(x => this._cleanUnderscore(x));
      if (obj && typeof obj === "object") {
        const r = {};
        for (const k in obj) if (!k.startsWith("_")) r[k] = this._cleanUnderscore(obj[k]);
        return r;
      }
      return obj;
    },

    async applyRaw() {
      try {
        const parsed = JSON.parse(this.rawText);
        this.data = parsed;
        this.hasEdits = true;
        this.rawJson = false;
      } catch (e) { this.error = "JSON 无效：" + e.message; }
    },

    async regenCurrent() {
      const mapping = {
        trope_library: "trope_library",
        tone_manual: "tone_manual",
        conflict_ladder: "conflict_ladder",
        emotion_curve: "emotion_curve",
        economy: "economy",
        geography: "geography",
        relationship_web: "relationships",
        power_system: "power_system",   // ✓ 修正：真正重建体系（原来映射到 power_scaling 只补战力刻度没换体系）
        lines: "lines",
        twist_system: "twists",
      };
      const action = mapping[this.current];
      if (!action) return;
      if (!confirm(`确认重建【${this.panelTitle()}】？（当前数据会被覆盖，自动留版本快照）`)) return;
      await this.regen(action);
    },

    async regen(action) {
      this.flash = "重建中……";
      try {
        const r = await fetch(this._api(`/api/regen/${action}`), { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          this.flash = "✓ 重建完成：" + action;
          // 刷新当前面板
          if (this.current) await this.load(this.current);
          await this.loadVersions();
        } else {
          this.error = j.error || "重建失败";
          this.flash = "";
        }
      } catch (e) { this.error = e.message; this.flash = ""; }
    },

    async regenArg(action, arg) {
      // 已经在跑就不要重复点
      if (this.regenRunning) {
        this.flash = `已经有重建任务在跑（${this.regenRunning.action}/${this.regenRunning.arg}），等它完成`;
        return;
      }
      const labels = {
        volume_outline: "本卷章节大纲",
        stages: "本卷叙事舞台",
        chapter_types: "本卷章节类型",
        character_arc: "角色心理弧",
        character_refine: "角色深化",
      };
      const labelText = `${labels[action] || action}（V${arg}）`;
      // 启动状态——按钮 disable、显示计时
      this.regenRunning = { action, arg, label: labelText, startedAt: Date.now(), secondsElapsed: 0 };
      this.flash = `⏳ 正在重建：${labelText}（LLM 调用中，30-90 秒...）`;
      this.error = "";
      // 每秒刷一次 secondsElapsed 让用户看到进度
      this._regenTimer = setInterval(() => {
        if (this.regenRunning) {
          this.regenRunning.secondsElapsed = Math.floor((Date.now() - this.regenRunning.startedAt) / 1000);
        }
      }, 1000);
      try {
        const r = await fetch(this._api(`/api/regen/${action}/${encodeURIComponent(arg)}`), { method: "POST" });
        let j;
        try { j = await r.json(); } catch { j = {}; }
        if (r.ok) {
          const took = Math.floor((Date.now() - this.regenRunning.startedAt) / 1000);
          this.flash = `✓ ${labelText} 重建完成（用时 ${took}s）`;
          if (this.current) await this.load(this.current);
          await this.loadVersions();
        } else {
          this.error = `重建 ${labelText} 失败：${j.error || ('HTTP ' + r.status)}`;
          this.flash = "";
        }
      } catch (e) {
        this.error = `重建 ${labelText} 网络/解析错误：${e.message}`;
        this.flash = "";
      } finally {
        if (this._regenTimer) { clearInterval(this._regenTimer); this._regenTimer = null; }
        this.regenRunning = null;
      }
    },

    async loadVersions() {
      try {
        const r = await fetch(this._api("/api/versions"));
        if (r.ok) this.versions = await r.json();
      } catch (e) { /* ignore */ }
    },

    async rollback(timestamp) {
      if (!confirm(`确认回退到 ${timestamp}？当前状态会先备份。`)) return;
      try {
        const r = await fetch(this._api(`/api/rollback/${timestamp}`), { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          this.flash = "✓ 已回退";
          this.showVersions = false;
          // 重新加载当前面板 + overview
          await this.init();
          if (this.current) await this.load(this.current);
        } else {
          this.error = j.error || "回退失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async loadApprovals() {
      this.current = "approvals";
      try {
        const r = await fetch(this._api("/api/approvals"));
        if (r.ok) this.approvals = await r.json();
      } catch (e) { this.error = e.message; }
    },

    async approveHITL(approval_id) {
      const note = prompt("审批备注（可选）", "") || "";
      try {
        const r = await fetch(this._api(`/api/approvals/${approval_id}/approve`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
        if (r.ok) {
          this.flash = "✓ 已批准";
          await this.loadApprovals();
        } else {
          this.error = "批准失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async loadStateAudit() {
      try {
        const r = await fetch(this._api(`/api/state_audit?_t=${Date.now()}`));
        if (!r.ok) return;
        const j = await r.json();
        this.stateAudit = j;
        // 同步拉一次可用的修复动作映射（只拉一次就够，但每次刷新也不贵）
        try {
          const ra = await fetch(this._api(`/api/state_audit/actions?_t=${Date.now()}`));
          if (ra.ok) this.auditActions = await ra.json();
        } catch (e) { /* ignore */ }
      } catch (e) { /* 审计失败不打扰 */ }
    },

    auditIconFor(status) {
      return ({ ok: "✓", partial: "⚠", empty: "✗" })[status] || "·";
    },

    get auditMissingCount() {
      if (!this.stateAudit) return 0;
      const s = this.stateAudit.summary || {};
      return (s.empty || 0) + (s.partial || 0);
    },

    // ── 提示词管理 ──────────────────────────────────
    async openPrompts() {
      if (this.hasEdits && !confirm("本模块有未保存的编辑，切换会丢失。继续？")) return;
      this.current = "prompts";
      this.data = { _virtual: true };  // 非 null，panels 块会渲染
      this.hasEdits = false;
      this.rawJson = false;
      this.error = "";
      this.promptEditor = null;
      this.showPromptDefault = false;
      await this.loadPrompts();
    },

    async loadPrompts() {
      try {
        const r = await fetch(`/api/prompts?_t=${Date.now()}`);
        if (!r.ok) { this.error = await r.text(); return; }
        this.promptsData = await r.json();
      } catch (e) { this.error = e.message; }
    },

    async openPromptEditor(promptId) {
      try {
        const r = await fetch(`/api/prompts/${encodeURIComponent(promptId)}?_t=${Date.now()}`);
        if (!r.ok) { this.error = await r.text(); return; }
        this.promptEditor = await r.json();
        this.showPromptDefault = false;
      } catch (e) { this.error = e.message; }
    },

    closePromptEditor() {
      if (this.promptEditor && this.promptEditor.current !== this._promptEditorOriginal) {
        if (!confirm("有未保存的修改，确认关闭？")) return;
      }
      this.promptEditor = null;
    },

    async savePrompt() {
      if (!this.promptEditor) return;
      this.promptBusy = true;
      try {
        const r = await fetch(
          `/api/prompts/${encodeURIComponent(this.promptEditor.id)}`,
          { method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ text: this.promptEditor.current || "" }) }
        );
        const j = await r.json();
        if (!r.ok || j.error) {
          this.error = j.error || r.statusText;
        } else {
          this.flash = `✓ 已保存【${this.promptEditor.label}】${j.overridden ? '（已覆盖代码默认）' : '（等同默认，未保存覆盖）'}`;
          this.promptEditor = null;
          await this.loadPrompts();
        }
      } catch (e) {
        this.error = e.message;
      } finally {
        this.promptBusy = false;
      }
    },

    async resetPromptToDefault() {
      if (!this.promptEditor) return;
      if (!confirm("确认恢复到代码默认值？会清除你的自定义修改。")) return;
      this.promptBusy = true;
      try {
        const r = await fetch(
          `/api/prompts/${encodeURIComponent(this.promptEditor.id)}`,
          { method: "DELETE" }
        );
        const j = await r.json();
        if (!r.ok || j.error) {
          this.error = j.error || r.statusText;
        } else {
          this.flash = `✓ 已恢复默认`;
          this.promptEditor = null;
          await this.loadPrompts();
        }
      } catch (e) {
        this.error = e.message;
      } finally {
        this.promptBusy = false;
      }
    },

    // ── 单章生成 ────────────────────────────────────
    async refreshNextUnwritten() {
      if (!this.currentProject) return;
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(this.currentProject)}/chapter/next_unwritten?_t=${Date.now()}`);
        if (!r.ok) return;
        const j = await r.json();
        this.nextUnwrittenIdx = j.chapter_index || 0;
        this.totalChaptersDone = j.done || 0;
        this.totalChaptersPlanned = j.total || 0;
      } catch (e) { /* 不打扰 */ }
    },

    async writeOneChapter() {
      if (this.writingOne) return;
      if (this.projStatus === "running") {
        this.error = "项目正在连续写作中，先按 ⏸ 暂停";
        return;
      }
      await this.refreshNextUnwritten();
      if (!this.nextUnwrittenIdx) {
        this.flash = "✓ 所有章节已写完，无下一章";
        return;
      }
      const idx = this.nextUnwrittenIdx;
      if (!confirm(`确认写第 ${idx} 章？\n同步执行，前端会阻塞 30 秒到 5 分钟直到写完。`)) return;
      this.writingOne = true;
      this.flash = `正在写第 ${idx} 章，请耐心等待...`;
      try {
        const r = await fetch(
          `/api/projects/${encodeURIComponent(this.currentProject)}/chapter/write_next`,
          { method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ chapter_index: idx }) }
        );
        const j = await r.json();
        if (!r.ok || j.error) {
          this.error = `写第 ${idx} 章失败：${j.error || r.statusText}`;
        } else if (j.status === "done") {
          this.flash = j.message || "所有章节已写完";
        } else if (j.status === "already_done") {
          this.flash = j.message || `第 ${idx} 章已写过`;
        } else {
          this.flash = `✓ 写完第 ${j.chapter_index} 章（${j.word_count} 字）`;
          // 刷新状态
          await this.refreshNextUnwritten();
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
        }
      } catch (e) {
        this.error = e.message;
      } finally {
        this.writingOne = false;
      }
    },

    // 打开审计面板（从侧栏 / 小 pill 点进来）
    async openStateAudit() {
      if (this.hasEdits && !confirm("本模块有未保存的编辑，切换会丢失。继续？")) return;
      this.current = "state_audit";
      this.data = { _virtual: true };  // 非 null 让 panels 块渲染
      this.hasEdits = false;
      this.rawJson = false;
      this.error = "";
      await this.loadStateAudit();
    },

    // 从审计点"查看"跳到对应 section 面板
    async loadSectionFromAudit(key) {
      // 审计里的 key → 实际 section 名映射（大多一致；少数不同）
      const KEY_TO_SECTION = {
        creative_intent: "creative_intent",
        concept_pitch: "concept_pitch",
        master_outline: null,           // 没有独立面板
        power_system: "power_system",
        volumes: "volumes",
        factions: "factions",
        world_setting: "world",
        geography: "geography",
        timeline: "timeline",
        economy: "economy",
        characters: "characters",
        lines: "lines",
        satisfaction: "satisfaction_points",
        foreshadows: "foreshadow_items",
        twists: "twist_system",
        stages: "story_stages",
      };
      const sec = KEY_TO_SECTION[key];
      if (!sec) {
        this.flash = `${key} 没有独立面板，请编辑 state.json 或 raw JSON 视图`;
        return;
      }
      await this.load(sec);
    },

    _appendAuditLog(icon, section, msg, status) {
      this.auditFixLog.unshift({
        ts: Date.now() + Math.random(),  // 唯一 key
        icon, section, msg, status,
      });
      // 最多保留 30 行
      if (this.auditFixLog.length > 30) this.auditFixLog.length = 30;
    },

    async fixOneFromAudit(key, label) {
      if (this.auditBusy) return;
      if (!confirm(`确认重建【${label}】？会清空现有该模块数据（已自动打快照）。`)) return;
      this.auditBusy = true;
      this._appendAuditLog("⏳", label, "修复中...", "running");
      try {
        const r = await fetch(this._api(`/api/state_audit/fix/${encodeURIComponent(key)}`), {
          method: "POST",
        });
        const j = await r.json();
        if (!r.ok || j.error) {
          this._appendAuditLog("✗", label, j.error || "失败", "failed");
          this.error = `修复【${label}】失败：${j.error || r.statusText}`;
        } else {
          this._appendAuditLog("✓", label, `完成（${JSON.stringify(j.result)}）`, "ok");
          this.flash = `✓ 已重建【${label}】`;
          this.stateAudit = j.audit || this.stateAudit;
        }
      } catch (e) {
        this._appendAuditLog("✗", label, e.message, "failed");
        this.error = e.message;
      } finally {
        this.auditBusy = false;
      }
    },

    async fixAllFromAudit() {
      if (this.auditBusy) return;
      const n = this.auditMissingCount;
      if (!n) return;
      if (!confirm(`确认一键修复全部 ${n} 个有问题的模块？\n将按上游→下游顺序重建，每一步自动打快照。预计耗时较久。`)) return;
      this.auditBusy = true;
      this.auditFixLog = [];
      this._appendAuditLog("▶", "全部", `开始一键复盘（${n} 个待修）`, "running");
      try {
        const r = await fetch(this._api(`/api/state_audit/fix_all`), { method: "POST" });
        const j = await r.json();
        if (!r.ok || j.error) {
          this._appendAuditLog("✗", "全部", j.error || r.statusText, "failed");
          this.error = j.error || r.statusText;
        } else {
          for (const item of (j.results || [])) {
            if (item.status === "fixed") {
              this._appendAuditLog("✓", item.section, JSON.stringify(item.result || {}), "ok");
            } else if (item.status === "failed") {
              this._appendAuditLog("✗", item.section, item.error || "失败", "failed");
            } else {
              this._appendAuditLog("·", item.section, item.reason || "跳过", "skip");
            }
          }
          this.stateAudit = j.audit || this.stateAudit;
          this.flash = `复盘完成：修复 ${(j.results||[]).filter(x=>x.status==="fixed").length} 个`;
        }
      } catch (e) {
        this._appendAuditLog("✗", "全部", e.message, "failed");
        this.error = e.message;
      } finally {
        this.auditBusy = false;
      }
    },

    async runInvariants() {
      try {
        const r = await fetch(this._api("/api/invariants"));
        const j = await r.json();
        const issues = j.issues || [];
        if (!issues.length) {
          this.report = "<strong>✓ 一致性检查通过</strong>";
        } else {
          this.report = "<strong>一致性报告：" + issues.length + " 个问题</strong><ul>" +
            issues.map(i => `<li>[${i.severity}] [${i.area}] ${i.message}</li>`).join("") + "</ul>";
        }
      } catch (e) { this.error = e.message; }
    },

    async deleteChapter(index, mode = "this_and_after") {
      // 两种模式清楚区分
      let confirmMsg;
      if (mode === "only_this") {
        confirmMsg =
          `【只删除第 ${index} 章】\n\n` +
          `⚠ 警告：后续章节可能引用这章的情节/人物状态，删除单章可能造成不一致。\n\n` +
          `✗ 删掉 chapter_${String(index).padStart(4,'0')}.txt 文件\n` +
          `✗ 从已完成列表移除这一章\n` +
          `✓ 保留所有规划数据（世界观/人物/卷结构/伏笔等）\n` +
          `✓ 后续章节文件保留不动（可能产生悬空引用）\n\n` +
          `确定只删这一章？`;
      } else {
        confirmMsg =
          `【删除第 ${index} 章及之后的所有章节】\n\n` +
          `✗ 删掉 Ch${index} 起所有的 chapter_*.txt 文件\n` +
          `✗ 从已完成列表移除这批章节\n` +
          `✓ 保留所有规划数据\n\n` +
          `删完后可从 Ch${index} 重新写作（推荐用法）。\n\n` +
          `确定删除？`;
      }
      if (!confirm(confirmMsg)) return;

      try {
        const r = await fetch(
          this._api(`/api/chapter/${index}?mode=${mode}`),
          { method: "DELETE" }
        );
        const j = await r.json();
        if (r.ok) {
          const n = (j.deleted_chapter_indexes || []).length;
          const modeLabel = mode === "only_this" ? "单章" : "本章及之后";
          this.flash = `✓ 已${modeLabel}删除（共 ${n} 个章节）——可重新写作`;
          // ★ 强制刷新——绕开 load() 里的"未保存编辑"拦截（这里没有编辑冲突）
          this.hasEdits = false;
          await this.refreshState();
          if (this.current === "completed_chapters") {
            // 直接拉最新 completed_chapters 数据，不走 load() 路径
            try {
              const rr = await fetch(this._api(`/api/section/completed_chapters`));
              if (rr.ok) {
                this.data = await rr.json();
                this.rawText = JSON.stringify(this.data, null, 2);
                this.$nextTick(() => this.renderVisualizations());
              }
            } catch (e) { /* ignore */ }
          }
          await this.refreshStatus();
        } else {
          this.error = j.error || `删除失败（HTTP ${r.status}）`;
          console.error("delete chapter failed:", j);
        }
      } catch (e) {
        this.error = e.message;
        console.error("delete chapter exception:", e);
      }
    },

    async deleteAllChapters() {
      if (!confirm(
        `【清空全部章节】\n\n` +
        `这会删除本项目所有已生成章节的 .txt 文件和记忆，但保留：\n` +
        `  ✓ 世界观/人物/卷结构/伏笔等所有规划\n\n` +
        `确定清空所有章节？`
      )) return;
      if (!confirm("最后确认：真的清空所有章节？此操作不可撤销。")) return;
      try {
        const r = await fetch(this._api(`/api/chapter/0?mode=all`), { method: "DELETE" });
        const j = await r.json();
        if (r.ok) {
          const n = (j.deleted_chapter_indexes || []).length;
          this.flash = `✓ 已删除全部 ${n} 个章节，可重新开始写作`;
          this.hasEdits = false;
          await this.refreshState();
          if (this.current === "completed_chapters") {
            try {
              const rr = await fetch(this._api(`/api/section/completed_chapters`));
              if (rr.ok) {
                this.data = await rr.json();
                this.$nextTick(() => this.renderVisualizations());
              }
            } catch (e) { /* ignore */ }
          }
          await this.refreshStatus();
        } else {
          this.error = j.error || "删除失败";
          console.error("delete all failed:", j);
        }
      } catch (e) { this.error = e.message; }
    },

    async openChapter(index, draft = false) {
      try {
        const suffix = draft ? "?draft=1" : "";
        const r = await fetch(this._api(`/api/chapter/${index}${suffix}`));
        const j = await r.json();
        if (r.ok) {
          this.chapterModal = { index, ...j, title: (j.summary && j.summary.title) || ("第" + index + "章") };
        } else {
          // 详细错误信息帮助排障
          let msg = j.error || `章节 ${index} 读取失败（HTTP ${r.status}）`;
          if (j.searched_paths) {
            msg += `\n当前项目：${j.current_project || "?"}`;
            msg += `\n输出目录：${j.output_dir || "?"}`;
            msg += `\n已搜索路径：\n  ` + j.searched_paths.join("\n  ");
            console.warn("chapter 404 detail:", j);
          }
          this.error = msg;
        }
      } catch (e) { this.error = e.message; }
    },

    async acceptDraft(index, overwrite = false) {
      const ok = confirm(
        `第 ${index} 章当前是未通过定稿前校验的草稿。\n\n` +
        `保存为正文后，系统会把它视为正式章节继续往后写；这不会自动修复原来的 canon/外部 AI 违规。${overwrite ? "\n\n注意：这次会覆盖已有正式正文。" : ""}\n\n` +
        `确定保存为正文吗？`
      );
      if (!ok) return;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/accept_draft`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ overwrite }),
        });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${index} 章草稿已保存为正文`;
          if (this.chapterModal && this.chapterModal.index === index) {
            this.chapterModal.is_draft = false;
            this.chapterModal.status = "final";
            this.chapterModal.draft_reason = "";
            this.chapterModal.summary = j.summary || this.chapterModal.summary;
          }
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
          await this.refreshProjectStatus?.();
          await this.refreshState?.();
        } else if (r.status === 409) {
          const overwriteOk = confirm("正式正文已经存在。要用这份草稿覆盖正式正文吗？");
          if (overwriteOk) return this.acceptDraft(index, true);
        } else {
          this.error = j.error || "保存草稿失败";
        }
      } catch (e) { this.error = e.message; }
    },

    async discardDraft(index) {
      const ok = confirm(
        `弃用第 ${index} 章草稿？\n\n` +
        `这只会删除 .draft 草稿文件，不会删除正式正文，不会回滚章节进度，也不会影响后续章节。`
      );
      if (!ok) return;
      this.error = "";
      try {
        const r = await fetch(this._api(`/api/chapter/${index}/draft`), { method: "DELETE" });
        const j = await r.json();
        if (r.ok) {
          this.flash = `✓ 第 ${index} 章草稿已弃用`;
          if (this.chapterModal && this.chapterModal.index === index && this.chapterModal.is_draft) {
            this.chapterModal = null;
          }
          if (this.current === "completed_chapters") {
            await this.load("completed_chapters");
          }
          await this.refreshStatus?.();
        } else {
          this.error = j.error || "弃用草稿失败";
        }
      } catch (e) { this.error = e.message; }
    },
  };
}
