"""
InspirationValidator —— 用户填章节灵感时的合规预检。

设计动机：作者填的 inspiration 在 writer.py 里被定位为"本章核心目标"，LLM 会优先
兑现灵感的字面要求。如果灵感本身就要求违规操作（让真 AI 答本书设定专有信息 /
引用 canon 之外术语 / 违反角色硬事实 / 让 acquired 章正式问答），最终正文必然
违规——后续 canon-revise 循环能兜底，但成本高且可能改不到位。

最干净的修法是**在源头拦截**——用户提交灵感时就提示"这条灵感会和铁律冲突，
建议改写为 XXX"。轻量 LLM 调用（~1-2s），不阻塞保存。

设计原则（按 [[feedback_generic_prompts]]）：通用、不写死项目术语
  · 系统级 SYSTEM prompt 用占位（"asset 名"/"题材"），不出现具体术语
  · 所有 asset / canon / 角色清单从 state 动态取
  · 换题材（仙侠/科幻/言情）不需要改本文件文字
"""
from __future__ import annotations
from dataclasses import dataclass, field

from utils.json_utils import request_json
from persistence.state import NovelState


@dataclass
class InspirationIssue:
    kind: str            # asset_misuse / undefined_term / character_fact / lifecycle_misalignment / other
    description: str     # 人话描述哪里违规
    severity: str = "warn"  # warn / critical（critical 也不阻塞，但前端会红字）


@dataclass
class ValidationResult:
    ok: bool = True                                # LLM 调用是否成功
    has_issues: bool = False
    issues: list[InspirationIssue] = field(default_factory=list)
    suggested_rewrite: str = ""                    # LLM 给的合规改写建议
    summary: str = ""                              # 一句话总评

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "has_issues": self.has_issues,
            "issues": [{"kind": i.kind, "description": i.description, "severity": i.severity}
                       for i in self.issues],
            "suggested_rewrite": self.suggested_rewrite,
            "summary": self.summary,
        }


SYSTEM = """你是【灵感合规预检员】。作者给某章填了一段"创作灵感"——
要让本章兑现这条灵感，但小说生成系统有几条铁律是不可妥协的。
你要审核灵感文本是否会迫使 writer 违反铁律，如果会，**给出可执行的改写建议**
（保留灵感的情绪/主轴，调整字面细节让它合规）。

═══ 系统铁律（不可妥协）═══

1. **真·AI 占位符 + 功能边界**
   本书可能绑了真实大语言模型（asset 清单见用户消息）。
   主角与这些 asset 的对话**必须用 [[ASK_AI:名|问题]] 占位**，且真 AI 只能答
   "现代真实世界知识 / 普世原理"——**不能答本书虚构设定专有信息**
   （朝代名 / 律法条文 / 本地行情 / 虚构人名 / 预言 / 未来）。

   ✗ 违规灵感模式举例（替换具体题材后类同）：
     · "主角问 AI 当朝某律法第几条"
     · "主角让 AI 算出某虚构人物的弱点 / 底牌"
     · "主角问 AI 自己穿越到了哪个朝代 / 怎么回去"
     · "主角让 AI 告知契约中的具体漏洞"（漏洞是 canon 里的具体设定）

   ✓ 合规改写思路（保留情绪与主轴，把字面要求合规化）：
     · "问 AI 当朝律法" → 改成"问 AI 古代真实社会借贷利率原理"（AI 能答），
       再让主角自己结合本地打听到的信息推断当朝条文（剧情线索保留）
     · "问 AI 我在哪" → 改成主角自己根据环境/方言/服饰判断（不问 AI）
     · "问 AI 契约漏洞" → 改成"问 AI 现代合同法中常见的格式陷阱"，
       主角自己拿到契约后比对

2. **未定义术语**
   不能引用 canon 清单（角色名 / 地名 / 势力 / 能力）之外的专有名词。
   ✗ 违规：灵感里出现 canon 清单没登记的人名/地名/势力名/功法名
   ✓ 合规：使用清单里已有的名字，或写"某不知名的 X"（让 writer 用通用描述）

3. **角色硬事实**
   不能违反本章涉及角色当前的位置 / 伤势 / 已知物品 / 身份级别。
   ✗ 违规：本章角色应当在 A 城重伤卧床，灵感却写"主角与他在 B 城对饮"

4. **lifecycle 节点性质**
   asset 的 lifecycle 节点已规划好不同戏份：
     · acquired 章只演"获得 / 亮相 / 首次感知"——**不展开正式问答**
       （首次正式调用留给 first_use 节点章）
     · locked 章 asset 被封禁——**本章不能用占位**
     · first_use / unlocked / escalation 才是正式调用
   ✗ 违规：acquired 章的灵感却写"主角向 AI 求出完整方案"
     —— 这是 first_use 章的戏，被提前吃掉
   ✓ 合规：acquired 章只让主角"发现 / 试探性触发 / 极简自我介绍"，
     正式问答挪到 first_use 章

═══ 输出 JSON（严格格式）═══
{
  "has_issues": true|false,
  "issues": [
    {
      "kind": "asset_misuse" | "undefined_term" | "character_fact" | "lifecycle_misalignment" | "other",
      "description": "(40-80 字) 灵感的哪一句话哪里违反了哪条铁律",
      "severity": "warn" | "critical"
    }
  ],
  "suggested_rewrite": "如 has_issues=true，给一段改写后的灵感（兑现原情绪与主轴，字面合规）；否则空串",
  "summary": "一句话总评（25 字内）：合规 / 有 N 处需调整 / 多处违规需改写"
}

判断尺度：
  · 灵感完全合规 → has_issues=false, issues=[]
  · 出现违规迹象 → warn
  · 明显冲突（asset 被命令答本书设定 / 直接引用 canon 外术语）→ critical
  · 灵感太短 / 看不出明显违规 → has_issues=false
"""


def _na(node, key, default=""):
    """从 lifecycle node 取字段——兼容 dataclass 与 dict。"""
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def _format_assets(state: NovelState) -> str:
    """asset 清单 + 功能边界——通用，不写死项目术语。"""
    if not state.power_system or not state.power_system.special_abilities:
        return "  （本书未配置 asset / 金手指）"
    lines = []
    for ab in state.power_system.special_abilities:
        is_real_ai = bool((ab.external_llm_profile or "").strip())
        tag = "🔌 真 AI" if is_real_ai else "  设定型"
        lines.append(f"  {tag} 《{ab.name}》：{(ab.description or '')[:80]}")
        # lifecycle 节点（提示哪些章是什么节点）
        for n in (ab.lifecycle_nodes or []):
            nt = _na(n, "node_type")
            tv = _na(n, "target_volume")
            tc = _na(n, "target_chapter")
            purp = _na(n, "narrative_purpose")
            lines.append(
                f"      lifecycle [{nt}] V{tv}·Ch{tc}: {str(purp)[:50]}"
            )
    return "\n".join(lines)


def _format_canon_summary(state: NovelState) -> str:
    """canon 清单（角色 / 地点 / 势力 / 境界 / 术语）—— 灵感里出现的名字要在这里能找到。"""
    parts = []
    if state.characters:
        parts.append("  角色: " + " / ".join(c.name for c in state.characters[:20]))
    if state.geography and state.geography.regions:
        parts.append("  地点: " + " / ".join(r.name for r in state.geography.regions[:15]))
    if state.factions:
        parts.append("  势力: " + " / ".join(f.name for f in state.factions[:15]))
    if state.power_system and state.power_system.realms:
        parts.append("  境界/级别: " + " / ".join(r.name for r in state.power_system.realms[:10]))
    if state.glossary:
        parts.append("  术语: " + " / ".join(g.term for g in state.glossary[:20]))
    return "\n".join(parts) if parts else "  （canon 清单为空）"


def _format_chapter_context(state: NovelState, chapter_index: int) -> str:
    """本章简要上下文（卷/章/lifecycle 节点命中）——让 LLM 知道这章的位置。"""
    lines = []
    vol = None
    for v in state.volumes:
        if v.chapter_start <= chapter_index <= v.chapter_end:
            vol = v
            break
    if vol:
        local = chapter_index - vol.chapter_start + 1
        lines.append(
            f"  · 第 {vol.index} 卷《{vol.title}》第 {chapter_index} 章"
            f"（卷内第 {local}/{vol.total_chapters} 章）"
        )
    # lifecycle 节点命中
    try:
        from agents.ability_roadmap_planner import find_nodes_hitting_chapter
        proto_name = next(
            (c.name for c in state.characters if c.role.value == "主角"), None
        )
        hits = find_nodes_hitting_chapter(state, chapter_index, holder_name=proto_name)
        if hits:
            lines.append("  · 本章 lifecycle 命中：" + " / ".join(
                f"《{h['asset_name']}》[{h['node_type']}]" for h in hits
            ))
    except Exception:
        # ability_roadmap_planner 不存在 / 不可用 时降级，不影响验证主流程
        pass
    return "\n".join(lines) if lines else f"  · 第 {chapter_index} 章"


def validate_inspiration(state: NovelState, chapter_index: int,
                          inspiration_text: str) -> ValidationResult:
    """对用户填的灵感做合规预检。

    返回 ValidationResult。失败时 ok=False（前端展示"验证未跑通"，不阻塞保存）。
    """
    text = (inspiration_text or "").strip()
    if not text or len(text) < 6:
        return ValidationResult(ok=True, has_issues=False, summary="灵感为空或太短，跳过检查")

    assets_block = _format_assets(state)
    canon_block = _format_canon_summary(state)
    ch_block = _format_chapter_context(state, chapter_index)

    user_prompt = f"""审核以下灵感是否会让 writer 违反铁律。

═══ 本章上下文 ═══
{ch_block}

═══ 本书 asset 清单（含 lifecycle 节点 / 真 AI 接入标记）═══
{assets_block}

═══ canon 清单（角色 / 地点 / 势力 / 境界 / 术语）═══
{canon_block}

═══ 作者灵感原文 ═══
\"\"\"
{text[:1500]}
\"\"\"

按 SYSTEM 里 4 条铁律审核，输出严格 JSON。
"""
    data = request_json(
        system=SYSTEM, user=user_prompt,
        required_keys=["has_issues", "summary"],
        max_retries=2, temperature=0.3,
        agent_name="InspirationValidator",
        empty_ok=True,
    )
    if not data:
        return ValidationResult(ok=False, summary="LLM 验证调用失败（不阻塞保存）")

    raw_issues = data.get("issues") or []
    issues: list[InspirationIssue] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        issues.append(InspirationIssue(
            kind=(raw.get("kind") or "other"),
            description=(raw.get("description") or "")[:240],
            severity=(raw.get("severity") or "warn"),
        ))

    has_issues = bool(data.get("has_issues")) and len(issues) > 0
    return ValidationResult(
        ok=True,
        has_issues=has_issues,
        issues=issues,
        suggested_rewrite=(data.get("suggested_rewrite") or "")[:1500],
        summary=(data.get("summary") or "")[:80],
    )
