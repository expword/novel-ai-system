# ── LLM 模型与 API key 配置 ───────────────────────
# 所有 LLM/API key 配置走 user_models.json（Web UI ➜「模型管理」可视化增删改）：
#   - 默认会自动写入两条种子（usage="main" 写作主力，usage="reviewer" 审核轻模型）
#   - 项目级覆盖写在 projects/<id>/meta.json 的 llm_profile 字段
#   - 内置厂商目录见 llm_profiles.PROFILES，也可作为 profile id 直接选
# 章节合规审核（setup_reviewer）默认走 user_models.find_by_usage("reviewer")。

# ── 小说基础设定 ──────────────────────────────────
NOVEL_TITLE = "苍穹问道"
NOVEL_GENRE = "玄幻"  # 玄幻/都市/科幻/言情/悬疑
NOVEL_THEME = "一个出身微末的少年，在修仙世界中以凡人之躯逆天改命，最终证道飞升的故事"

# ── Phase -1 创作意图（最高优先级；一段自然语言描述想写什么） ───
# 留空则跳过意图分析；也可以在前端面板里填写
INTENT_DESCRIPTION = ""
# 例：INTENT_DESCRIPTION = "我想写一个从现代穿越到修真世界的腹黑商人——主打反套路、种田发育、情感线细腻；风格偏烟火气+成人向，不要傻白甜"

# ── Phase 0 立项种子（可选；留空则 LLM 自己决定） ───────
# 强信号：下列任一填了，concept_pitch agent 会严格遵守这些偏好
TARGET_AUDIENCE = ""          # "男频" / "女频" / "混合"；留空让 LLM 根据题材推断
TARGET_PLATFORM = ""          # "起点" / "晋江" / "番茄" / "书旗" / "QQ阅读" / "飞卢" 等；留空让 LLM 推断
CORE_SELLING_POINTS_SEEDS = []    # 可选，如 ["反套路穿越", "种田", "宠妻"]
EMBRACE_TROPES_SEEDS = []         # 可选，如 ["扮猪吃虎", "扫地僧"]
AVOID_TROPES_SEEDS = []           # 可选，如 ["师门叛徒", "女主圣母"]
VILLAIN_POLICY_SEED = ""      # 可选："洗白型" / "彻底黑化型" / "灰色模糊型" / "人格魅力型"
NARRATIVE_VOICE_SEED = ""     # 可选："第一人称" / "第三人称限知" / "上帝视角" / "多视角切换"
STYLE_REFERENCE_SEED = ""     # 可选，如 "烽火戏诸侯的诗意 + 天蚕土豆的热血"

# ── 卷结构 ────────────────────────────────────────
NUM_VOLUMES = 6  # 总卷数（5-8）
CHAPTERS_PER_VOLUME_MIN = 60  # 每卷最少章节
CHAPTERS_PER_VOLUME_MAX = 100  # 每卷最多章节
WORDS_PER_CHAPTER = 3000  # 每章目标字数（2000-3000 字常规章节）

# ── 规划深度 ──────────────────────────────────────
# 全局线数量
GLOBAL_STORY_LINES = 4  # 贯穿全书的故事线
GLOBAL_EMOTION_LINES = 3  # 贯穿全书的情感线
GLOBAL_CHARACTER_LINES = 2  # 主角贯穿全书的人物线
# 每卷专属线数量
VOLUME_STORY_LINES = 4  # 每卷故事线（卷内主线+支线）
VOLUME_EMOTION_LINES = 1  # 每卷情感线
VOLUME_CHARACTER_LINES = 2  # 每卷人物线（包含配角成长）

# ── 人物规模（可按故事需要调大调小） ──────────────────
# CharacterDesigner 按下面数量分批生成——全部范围都是 LLM 提示内的"目标区间"
PROTAGONIST_CIRCLE_MIN = 3          # 主角圈（主角+引路人+感情线核心）最少
PROTAGONIST_CIRCLE_MAX = 5          # 主角圈最多
MAJOR_ALLIES_MIN = 10                # 主要配角/盟友最少
MAJOR_ALLIES_MAX = 15                # 主要配角/盟友最多
ANTAGONISTS_MIN = 10                 # 反派最少（含大反派+中层反派）
ANTAGONISTS_MAX = 15                # 反派最多
MINOR_CHARS_PER_VOLUME_MIN = 15      # 每卷次要/卷内角色最少
MINOR_CHARS_PER_VOLUME_MAX = 25      # 每卷次要/卷内角色最多

# ── 势力/组织规模 ─────────────────────────────────
FACTION_TIERS_MIN = 5               # 势力分层数最少（言情/短篇可降到 2-3）
FACTION_TIERS_MAX = 7               # 势力分层数最多（史诗级可到 5-6）
FACTIONS_PER_TIER_MIN = 4           # 每层势力数最少（避免铁板一块）
FACTIONS_PER_TIER_MAX = 6           # 每层势力数最多
NEUTRAL_FACTIONS = 10                # 横跨多层的"中立势力"数量（商会/媒体/情报贩等）
HIDDEN_FACTIONS = 3                 # 隐藏势力数量（幕后黑手/远古遗族/秘密组织）
KEY_MEMBERS_PER_FACTION_MIN = 3     # 每个势力的重要成员数最少
KEY_MEMBERS_PER_FACTION_MAX = 5     # 每个势力的重要成员数最多
FACTION_RELATIONS_MIN = 3           # 每个势力外部关系数最少（与其他势力的互动关系）
FACTION_RELATIONS_MAX = 5           # 每个势力外部关系数最多
FACTION_INTERNAL_CONFLICTS_MIN = 3  # 每个势力内部矛盾数最少（主角可利用）
FACTION_INTERNAL_CONFLICTS_MAX = 4  # 每个势力内部矛盾数最多

# ── 关系网复杂度 ──────────────────────────────────
# CharacterWeb 生成的关系条数（0 表示让 LLM 自由决定）
RELATIONSHIP_BONDS_MIN = 25         # 至少生成这么多条人物关系
RELATIONSHIP_BONDS_MAX = 35         # 最多这么多条（复杂多线关系更带感）
HIDDEN_RELATIONS_MIN = 6            # "表里不一"的关系最少条数
HIDDEN_RELATIONS_MAX = 8            # "表里不一"的关系最多条数
TRIANGLE_RELATIONS_MIN = 6          # 三角/互相制约关系最少组数
TRIANGLE_RELATIONS_MAX = 8          # 三角/互相制约关系最多组数
POWER_CHAINS_MIN = 6                # 权力链条（谁暗中控制谁）最少
POWER_CHAINS_MAX = 8                # 权力链条最多
HIDDEN_ALLIANCES_MIN = 4            # 隐藏同盟（前期不揭露）最少
HIDDEN_ALLIANCES_MAX = 6            # 隐藏同盟最多
CROSS_FACTION_BONDS_MIN = 4         # 跨敌我阵营的人物关系最少（敌方角色对主角真有感情等）
CROSS_FACTION_BONDS_MAX = 6         # 跨敌我阵营的人物关系最多
# 每章注入写作 prompt 的关系提示最多条数（预算控制，不影响数据层面生成）
RELATIONSHIP_HINTS_PER_CHAPTER = 8

# ── 伏笔密度 ──────────────────────────────────────
MAJOR_FORESHADOWS_MIN = 4           # 主线伏笔最少
MAJOR_FORESHADOWS_MAX = 6           # 主线伏笔最多
MINOR_FORESHADOWS_MIN = 6           # 支线伏笔最少
MINOR_FORESHADOWS_MAX = 8           # 支线伏笔最多
DETAIL_FORESHADOWS_MIN = 5          # 细节伏笔最少
DETAIL_FORESHADOWS_MAX = 8          # 细节伏笔最多

# ── 写作质量 ──────────────────────────────────────
MAX_REVISION_ROUNDS = 3  # 审校最多修改轮次（收紧：从 2→3）
MIN_PASS_SCORE = 8  # 审校通过分数线（满分10，收紧：从 7→8）

# ── 并发 ──────────────────────────────────────────
# LLM 并发数——LLM 调用是 I/O 密集，可多线程并发
# 过高会打爆 provider 的 rate limit；保守 3-5 比较安全
PARALLEL_WORKERS = 4

# ── LLM 全局池 ────────────────────────────────────
# 所有 LLM 调用透明走 llm_pool.LLMPool——统一并发/速率/熔断
LLM_MAX_CONCURRENT = 8            # 全局并发上限（跨所有 agent 总计）
LLM_RATE_LIMIT_RPM = 60           # 每分钟请求数上限（按 provider 文档调整）
LLM_CB_FAILURE_THRESHOLD = 5      # 连续失败多少次触发熔断
LLM_CB_COOLDOWN_SEC = 30.0        # 熔断冷却时间

# ── 输出 ──────────────────────────────────────────
# 由 project_context 动态填入（支持多项目）
from project_mgmt import project_context as _pctx
OUTPUT_DIR = _pctx.project_dir()
PLANS_DIR = _pctx.plans_dir()

# ── 敏感词过滤 ────────────────────────────────────
# 这里列的是各平台通用的敏感词/慎用词样例，实际使用时根据目标平台扩展
SENSITIVE_WORDS = [
    # 这里故意留空——用户按平台需要自己填
]
# 敏感词自动替换表：{敏感词: 替换词}
SENSITIVE_REPLACEMENTS = {
    # 例："妈的": "该死"，运行时自动替换
}

# ── 人工介入点开关 ────────────────────────────────
# "pause"（默认，关键节点暂停）| "warn"（只警告不暂停）| "skip"（完全关闭）
HITL_MODE = "skip"

# ── 漂移检测 ──────────────────────────────────────
DRIFT_CHECK_EVERY_N_CHAPTERS = 10

# ── Stage / Volume 级审查 ────────────────────────
# Stage 写完跑 stage_reviewer，整卷写完跑 volume_reviewer。
# critical 问题触发指定章重写，最多重写多少轮（每轮重写后再审一次）。
# 0 = 关闭 stage/volume 级审查（只产报告，不阻塞、不修订）。
STAGE_REVIEW_MAX_REWRITE_ROUNDS = 2
VOLUME_REVIEW_MAX_REWRITE_ROUNDS = 1
