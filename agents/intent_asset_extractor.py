"""
IntentAssetExtractor —— 从用户的 intent_description 主动抽出**显式声明的 asset**。

═══ 根因背景 ═══

实际案例：用户写"金手指设定：豆包 / 定位：超级搜索引擎 / 限制：不能预知古代具体人事"，
但因为 realm_designer 把 system_type 判定为 progression_arc（社会进阶题材），
触发"非修真题材跳过 asset 设计"早退条件，导致豆包**从未被登记**到
state.power_system.special_abilities。本 agent 把"用户意图里写明的 asset"
提升为**结构化契约**——下游 realm_designer 不再有权一票否决。

═══ 两步工作流 ═══

按用户要求"每个 asset / 道具 / 特异功能 / 金手指要单独调用一次模型"——
不一次 LLM 同时生成多个 asset（一次性多 asset 会上下文撑爆 / 每个潦草 / 互相干扰）。

  Step A · 列名（1 次轻量调用）
    从 intent_description 抽**asset 名单 + 一句话定位**——只要轮廓
    输出 [{"name": "豆包", "kind_hint": "AI 助手"}, ...]

  Step B · 逐个深化（每个 asset 1 次独立调用，可并行）
    对每个 name 单独调 LLM，独享上下文注意力——生成完整 SpecialAbility
    （description / limits / cost / cooldown / is_real_ai_like / etc）

═══ 设计原则 ═══

按 [[feedback_generic_prompts]]：prompt 完全通用，不硬编码项目术语
（豆包/金手指/系统/etc）——所有素材从 state.creative_intent.raw_description 取。
LLM usage：走 'extractor' 路由（结构化短任务 → 轻量便宜模型）。
失败时单项 fallback：list_step 失败返回空 list；single_step 失败不阻塞其他 asset。
"""
from __future__ import annotations
from typing import Optional

from utils.json_utils import request_json, request_json_with_profile
from utils.concurrency import parallel_map
from persistence.state import NovelState, SpecialAbility


# ═══════════════════════════════════════════════════════════════
#  Step A · 列名
# ═══════════════════════════════════════════════════════════════

SYSTEM_LIST = """你是【作者意图 asset 列名员】——任务：从作者写的"想写什么"自然语言里，
列出他**明确声明的金手指/系统/AI/特殊物件/能力/法宝**等"叙事核心 asset"的**名字 + 一句话定位**。

不要展开详细字段——只列轮廓。后续会对每个 asset 单独深化（独享上下文）。

═══ 触发模式（必须用过其中之一，且 X 必须是**具体可命名的工具/系统/物件**）═══
  · "金手指设定：X" / "金手指：X" / "外挂：X" / "系统设定：X" / "X 系统"
  · "X（定位：... 限制：...）" 类结构化描述（X 必须是具名实体，不能是兴趣或背景）
  · "主角的 X 是 ..." / "主角有一台 X" / "主角脑中有一个 X 系统"
  · 题目本身明确暗示（如《被 X 带飞了》《我有 X》《重生之 X 助我登顶》——X 是具体物件名）

═══ ❌ 严禁误判（这些都不是 asset，而是人物背景/兴趣/技能）═══
  · 「主角是 X 爱好者 / 是个 X 迷 / 沉迷 X / 对 X 有研究」
      ← 兴趣爱好，不是 asset。X 可以是任何领域，全部排除。
  · 「主角是 X（职业）」—— X 是任何职业身份（医生/律师/教师/工程师/学者/警察…）
      ← 职业背景，不是 asset。
  · 「主角熟悉 X / 精通 X / 擅长 X / 出身于 X 世家」
      ← 技能特长 / 家世，不是 asset。
  · 「主角穿越前是 X」/「穿越带着 X 的知识」/「主角脑中残留 X 的记忆」
      ← 自身的兴趣、职业、学识、记忆 **永远不是 asset**——哪怕剧情里频繁运用，
        也属于"人物设定背景"，应由 character_designer 处理，不是金手指。
      ← 例外：当用户给出**具体可命名实体**（如「Y 系统」「Y 戒指」「Y 玉佩」「Y AI 助手」
        「Y 图鉴」「Y 装置」），Y 是名字而不是范畴时，才算 asset。

═══ 否决性自检（列名前先问自己）═══
  1. 用户给这个东西 **起了具体名字** 吗（一个独立实体的称呼，而不是某个领域/范畴）？
     · ✓ 是名字：「神级系统」「无名玉佩」「破天剑」「问道仪」「时空手镯」「百宝囊」
       —— 共同特点：一个独立可指代的"东西"
     · ✗ 不是名字：「对 X 的了解」「X 知识」「X 能力」「X 经验」「X 记忆」
       —— 共同特点：是某种"领域/范畴"而非具名实体
  2. 它是**外部赋予/植入/获得**的，而不是主角自己学的吗？
  3. 它有**外挂式的、超越常人能力的功能**吗（如即时查询/无限调用/AI 回答/不依赖主观努力）？
  这三问任何一个答 "否" → **不是 asset**，跳过不列。

═══ 输出严格 JSON ═══
{
  "assets": [
    {"name": "...", "kind_hint": "一句话定位（≤30 字）"}
  ]
}

═══ 铁律 ═══
  · 只列用户**明确写了**的——找不到就 assets=[] 空数组（**这是常态，不是异常**）
  · 现代人物穿越后保留的**自身知识/兴趣/职业/记忆**不算 asset
  · 一本书通常 0-2 个核心 asset；超过 3 个几乎肯定列多了
  · 不要展开 description / limits / cost——那是 Step B 的事
  · 同一 asset 不许用两个近义名重复列（指代相同实体的不同称呼 → 只列一个）"""


def _step_a_list_assets(state: NovelState, raw: str) -> list[dict]:
    """轻量调用，返回 [{name, kind_hint}, ...]。

    走 llm_call facade：task='extraction' 自动路由到 extractor usage，没绑 fallback main。
    替代旧的 request_json_with_profile(... try/except 手写 fallback) 模式。
    """
    user = f"""作者写的"想写什么"自然语言：
\"\"\"
{raw[:4000]}
\"\"\"

按 SYSTEM 规则列出所有声明的 asset 的名字 + 一句话定位。严格 JSON 输出。"""
    from llm_layer.llm_call import request_json_for_task
    data = request_json_for_task(
        "extraction",
        system=SYSTEM_LIST, user=user,
        required_keys=["assets"], max_retries=2, temperature=0.2,
        agent_name="IntentAssetExtractor.list", empty_ok=True,
    )
    out = []
    for a in (data.get("assets") if data else []) or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()
        if not name:
            continue
        out.append({"name": name, "kind_hint": str(a.get("kind_hint") or "")[:40]})
    return out


# ═══════════════════════════════════════════════════════════════
#  Step A · 候选去重
# ═══════════════════════════════════════════════════════════════

def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    """合并近义 name 的候选——避免 LLM 把同一物列两次。

    规则（按顺序检查）：
      1. 完全同名 → 丢弃后者
      2. 一方是另一方的**严格子串** (>=2 字) → 视为同一物
         （如「破天剑」是「破天神剑」的子串）
      3. kind_hint **几乎相同**（去停用词后字符集重合 ≥0.7）→ 视为同一物
         （两个名字字面不同但定位描述相似，多半指同一实体）

    保留较具体（长 name）的候选；其他丢弃。

    传入 [{name, kind_hint}, ...]，返回去重后的同结构 list。
    """
    if not candidates or len(candidates) == 1:
        return candidates

    # 简易停用词，用于 kind_hint 比对
    _STOP = set("的之与和或者是有个种类型一二三四五六七八九十系统能力asset")

    def _norm(s: str) -> set[str]:
        return {ch for ch in (s or "") if ch not in _STOP and len(ch.strip()) > 0}

    def _is_near_dup(a: dict, b: dict) -> bool:
        an, bn = (a.get("name") or "").strip(), (b.get("name") or "").strip()
        if not an or not bn:
            return False
        if an == bn:
            return True
        # ① 连续子串关系（短的是长的子串）
        if len(an) >= 2 and (an in bn or bn in an):
            return True
        # ② 字符集 Jaccard ≥ 0.6 且长度差 ≤ 2 → 同一物加修饰字
        #    "破天剑"(3) vs "破天神剑"(4)：长度差 1，字符 Jaccard 3/4=0.75 → 合并
        #    "灵狐"(2) vs "灵狐玉"(3)：长度差 1，Jaccard 2/3=0.67 → 合并
        #    "百宝囊"(3) vs "乾坤袋"(3)：字符无重合 → 不合并
        if abs(len(an) - len(bn)) <= 2:
            sa, sb = set(an), set(bn)
            inter, union = len(sa & sb), len(sa | sb)
            if union > 0 and inter / union >= 0.60:
                return True
        # ③ kind_hint 字符集 Jaccard ≥ 0.65 + name 字符也至少有点重合
        #    防 LLM 把多个不同 asset 的 kind_hint 都写成"主角金手指"这种通用短语
        #    导致字符面完全无关的 asset 被误合并
        ka, kb = _norm(a.get("kind_hint", "")), _norm(b.get("kind_hint", ""))
        if ka and kb:
            inter = len(ka & kb)
            union = len(ka | kb)
            if union > 0 and inter / union >= 0.65:
                # name 之间也必须有 ≥ 30% 字符重合，避免纯靠 kind_hint 误合并
                sa, sb = set(an), set(bn)
                name_inter, name_union = len(sa & sb), len(sa | sb)
                if name_union > 0 and name_inter / name_union >= 0.30:
                    return True
        return False

    kept: list[dict] = []
    for cand in candidates:
        dup_idx = None
        for i, k in enumerate(kept):
            if _is_near_dup(cand, k):
                dup_idx = i
                break
        if dup_idx is None:
            kept.append(cand)
        else:
            # 保留较具体（更长的）name；kind_hint 较具体的也保留
            existing = kept[dup_idx]
            if len(cand.get("name", "")) > len(existing.get("name", "")):
                # 长 name 更具体——替换 name；hint 取更长的
                merged = {
                    "name": cand["name"],
                    "kind_hint": cand.get("kind_hint", "") or existing.get("kind_hint", ""),
                }
                kept[dup_idx] = merged
            print(f"    ↻ 合并近义 asset：《{cand['name']}》 ≈ 《{existing['name']}》"
                  f"——保留《{kept[dup_idx]['name']}》")
    return kept


# ═══════════════════════════════════════════════════════════════
#  Step B · 单个深化
# ═══════════════════════════════════════════════════════════════

SYSTEM_DEEPEN = """你是【单个 asset 深化设计员】——任务：用户已经声明了一个名为 {asset_name} 的
叙事核心 asset（轮廓："{kind_hint}"），现在深度展开它的**所有可机器消费字段**。

═══ 必填字段（找不到就推断合理默认，但**不要瞎补名字**）═══

  source            来源/获取方式（≤50 字）——怎么来到主角手里
  description       一句话本质定位（≤120 字）——能干什么、是什么
  unlock_condition  最初解锁条件（≤80 字）——什么情况下能用
  functional_limits 功能限制（≤180 字）——不能做什么、AI 不知道什么
  usage_cost        使用代价（≤120 字）——每次使用付出什么
  ceiling_at_intro  初登场时的能力上限（≤80 字）——首次出现能做到多少
  growth_hints      成长方向提示（≤80 字）——会怎么演化升级
  is_real_ai_like   bool：是不是"AI/搜索引擎/方案生成器"类（适合绑真 LLM）
                    yes：豆包/手机 AI/系统面板/水晶球/搜索神器
                    no：剑/丹药/血脉/功法/法宝
  is_protagonist_signature  bool：是不是主角的核心金手指（一般 true）
  signature_use_modes      list[str]：典型使用场景模式（2-4 条，每条 ≤30 字）
                            例：["主角问它现代真实知识 + 主角自己结合古代信息推断剧情线索",
                                "主角让它估算复杂博弈胜率，但最终决策权在主角"]

═══ 输出严格 JSON ═══

{{
  "source": "...", "description": "...", "unlock_condition": "...",
  "functional_limits": "...", "usage_cost": "...",
  "ceiling_at_intro": "...", "growth_hints": "...",
  "is_real_ai_like": true|false,
  "is_protagonist_signature": true|false,
  "signature_use_modes": ["..."]
}}

═══ 铁律 ═══
  · 用户在 intent 里写明的细节**必须照搬**（不要改 / 不要润色覆盖）
  · 用户没写的字段用**合理推断**（保守，向 conservative 倾斜）
  · 不要造新 asset 名 / 不要把这个 asset 跟其他 asset 混淆"""


def _step_b_deepen_one(state: NovelState, asset_name: str, kind_hint: str,
                         raw_intent: str) -> Optional[dict]:
    """单个 asset 深化——独享 LLM 调用。失败返回 None（不影响其他 asset）。

    走 llm_call facade（task='extraction'）。
    """
    user = f"""作者写的 intent_description 全文（供参考）：
\"\"\"
{raw_intent[:3000]}
\"\"\"

═══ 现在专注深化这一个 asset ═══
名字：{asset_name}
轮廓：{kind_hint or '（用户未给一句话定位）'}

按 SYSTEM 规则深化所有字段。严格 JSON 输出。"""
    from llm_layer.llm_call import request_json_for_task
    try:
        return request_json_for_task(
            "extraction",
            system=SYSTEM_DEEPEN.format(asset_name=asset_name, kind_hint=kind_hint),
            user=user,
            required_keys=["description", "functional_limits"],
            max_retries=2, temperature=0.25,
            agent_name=f"IntentAssetExtractor.deepen[{asset_name}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  ⚠ 深化 asset《{asset_name}》失败：{type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

def extract_assets_from_intent(state: NovelState, *, force: bool = False) -> list[SpecialAbility]:
    """两步抽取 + 写回 state.power_system.special_abilities。

    Step A 一次列名 → Step B 每个 asset 一次独立深化（并发跑）。
    幂等：state.power_system.special_abilities 已有 [intent_declared] 标记的跳过。

    ── reality_basis 联动（防止把"人物兴趣/背景"误识别成 asset）──
    · real_history → 直接 return []，严格真实历史不允许超现实金手指
    · real_adapted → 走标准抽取，但 SYSTEM_LIST 已内含"自身知识/兴趣/职业不是 asset"规则
    · fictional   → 标准行为
    """
    intent = getattr(state, "creative_intent", None)
    raw = (getattr(intent, "raw_description", "") or "").strip() if intent else ""
    if not raw:
        return []

    # ── reality_basis 硬否决：严格历史模式直接跳过 ──
    basis = (getattr(intent, "reality_basis", "") or "").strip()
    if basis == "real_history":
        print("  ✓ 故事根基 = real_history（严格基于真实历史）—— "
              "跳过 intent_asset_extractor（真实历史不允许超现实金手指）")
        # 写一条 progress_warning 让前端能看到 + 给个手动声明的逃生口
        try:
            from persistence.checkpoint import add_progress_warning, clear_progress_warnings
            clear_progress_warnings(source="intent_asset_extractor")
            add_progress_warning(
                level="info",
                source="intent_asset_extractor",
                message=(
                    "故事根基=real_history（严格真实历史），已跳过自动金手指抽取。"
                    "若主角确有超现实金手指（如外置系统/植入装置/AI 助手等具名实体），"
                    "请到力量体系→特殊能力面板手动添加；"
                    "或把根基改为 real_adapted/fictional 后重跑 Phase -1.5。"
                ),
            )
        except Exception:
            pass
        return []

    ps = state.power_system
    if not force and ps and ps.special_abilities:
        already = [ab for ab in ps.special_abilities
                    if ab.description.startswith("[intent_declared]")]
        if already:
            print(f"  ✓ intent_asset_extractor 已跑过（{len(already)} 个 asset），跳过")
            return already

    # ── Step A：列名（1 次 LLM）──
    print(f"  ── intent_asset_extractor Step A：列名（reality_basis={basis or 'fictional'}）──")
    candidates = _step_a_list_assets(state, raw)
    if not candidates:
        print("  ✓ intent 未声明任何 asset——跳过")
        return []

    # ── 同名/近义名去重（防 LLM 把同一物列两遍）──
    candidates = _dedupe_candidates(candidates)
    print(f"  ✓ 列出 {len(candidates)} 个候选（去重后）：" +
          " / ".join(f"《{c['name']}》" for c in candidates))

    # ── real_adapted 模式：候选过多时给一条 progress_warning 让用户审 ──
    if basis == "real_adapted" and len(candidates) > 0:
        try:
            from persistence.checkpoint import add_progress_warning
            names_preview = " / ".join(f"《{c['name']}》" for c in candidates[:5])
            add_progress_warning(
                level="info",
                source="intent_asset_extractor",
                message=(
                    f"故事根基=real_adapted（真实人物/事件改编），"
                    f"已自动抽取 {len(candidates)} 个 asset：{names_preview}。"
                    "请到力量体系→特殊能力面板核对——确认它们不是"
                    "主角的'兴趣/职业/学识/记忆'被误判成金手指。"
                ),
            )
        except Exception:
            pass

    # ── Step B：每个 asset 单独深化（并发，可控）──
    print(f"  ── intent_asset_extractor Step B：逐个深化（{len(candidates)} 个并发调用）──")
    deepened = parallel_map(
        fn=lambda c: (c, _step_b_deepen_one(state, c["name"], c["kind_hint"], raw)),
        items=candidates,
        max_workers=min(4, len(candidates)),
        label="IntentAssetDeepen",
    )

    # ── 转 SpecialAbility 并写回 ──
    proto_name = ""
    for c in (state.characters or []):
        role = getattr(getattr(c, "role", None), "value", "")
        if role == "主角":
            proto_name = c.name
            break

    if not ps:
        from persistence.state import PowerSystem
        state.power_system = PowerSystem(system_name="", system_description="", realms=[])
        ps = state.power_system

    from llm_layer import user_models
    in_story_profile = user_models.find_by_usage("in_story_ai")
    in_story_id = in_story_profile.get("id", "") if in_story_profile else ""

    existing_names = {ab.name for ab in (ps.special_abilities or [])}
    out: list[SpecialAbility] = []
    for pair in (deepened or []):
        if not pair:
            continue
        cand, data = pair
        name = cand["name"]
        if name in existing_names:
            continue
        if not data:
            # 深化失败——用 Step A 的轮廓做兜底登记，至少别让 asset 完全丢
            print(f"  ⚠ 《{name}》深化失败，用 Step A 轮廓登记（description 较简）")
            data = {
                "description": cand.get("kind_hint", ""),
                "functional_limits": "", "usage_cost": "",
                "is_real_ai_like": False, "is_protagonist_signature": True,
                "source": "", "unlock_condition": "",
                "ceiling_at_intro": "", "growth_hints": "", "signature_use_modes": [],
            }
        # 组合 description——含 [intent_declared] 标记 + 所有字段
        desc_bits = [f"[intent_declared] {data.get('description','')}"]
        if data.get("functional_limits"): desc_bits.append(f"功能限制：{data['functional_limits']}")
        if data.get("usage_cost"):        desc_bits.append(f"使用代价：{data['usage_cost']}")
        if data.get("ceiling_at_intro"):  desc_bits.append(f"初登场上限：{data['ceiling_at_intro']}")
        if data.get("growth_hints"):      desc_bits.append(f"成长方向：{data['growth_hints']}")
        if data.get("signature_use_modes"):
            modes = " | ".join(data["signature_use_modes"][:3])
            desc_bits.append(f"典型用法：{modes}")
        full_desc = " / ".join(desc_bits)[:600]

        is_real_ai = bool(data.get("is_real_ai_like", False))
        bound_profile = in_story_id if is_real_ai else ""

        ab = SpecialAbility(
            name=name,
            source=str(data.get("source") or "")[:100],
            description=full_desc,
            unlock_condition=str(data.get("unlock_condition") or "")[:120],
            holder_role="主角自身",
            holder_name=proto_name,
            is_protagonist_signature=bool(data.get("is_protagonist_signature", True)),
            entry_kind="system" if is_real_ai else "ability",
            external_llm_profile=bound_profile,
        )
        ps.special_abilities.append(ab)
        existing_names.add(name)
        out.append(ab)
        bind_note = f"（绑 in_story_ai={bound_profile}）" if bound_profile else ""
        print(f"  ✓ 登记 asset 《{name}》（{ab.entry_kind}）{bind_note}")

    # 写 progress_warning 告诉用户新登记
    try:
        from persistence.checkpoint import add_progress_warning, clear_progress_warnings
        clear_progress_warnings(source="intent_asset_extractor")
        if out:
            names = " / ".join(f"《{ab.name}》" for ab in out[:5])
            add_progress_warning(
                level="info",
                source="intent_asset_extractor",
                message=(
                    f"已从作者意图自动登记 {len(out)} 个 asset：{names}。"
                    "每个 asset 独立 LLM 调用深化生成。"
                    "请在 web UI'力量体系→特殊能力'确认设置。"
                ),
            )
    except Exception:
        pass

    print(f"  ✓ intent_asset_extractor 共新登记 {len(out)} 个 asset"
          f"（{len(candidates)} 候选 / {len(candidates) - len(out)} 已存在或失败）")
    return out
