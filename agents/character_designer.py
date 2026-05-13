"""
CharacterDesignerAgent — 两阶段人物设计。

阶段A: 核心人物（主角+主要配角+主要反派，8-12人）
阶段B: 扩展人物（卷内角色+工具人+背景角色，每卷2-3人）

核心原则：
- 人物要为自己活着，不是为剧情存在
- 关系网络要够复杂（三角/隐藏/背叛/羁绊）
- 同一势力内部也有矛盾和对立
- 每个人物都有"对读者隐藏的秘密"
"""
from typing import Optional
from utils.json_utils import repair_json, request_json, pick_list
from llm_layer.llm import system_user
from persistence.state import NovelState, Character, CharacterRole, Relationship
from config import (
    NUM_VOLUMES,
    PROTAGONIST_CIRCLE_MIN, PROTAGONIST_CIRCLE_MAX,
    MAJOR_ALLIES_MIN, MAJOR_ALLIES_MAX,
    ANTAGONISTS_MIN, ANTAGONISTS_MAX,
    MINOR_CHARS_PER_VOLUME_MIN, MINOR_CHARS_PER_VOLUME_MAX,
)
from agents.concept_pitch import format_concept_brief, format_world_context_brief

SYSTEM = """你是专业的小说人物设计师，擅长创造有深度、关系复杂的人物体系。
【单主角铁律】
整部小说只有一个主角。其他所有角色（包括反派、挚友、导师、爱人、长辈）都是配角。
配角可以鲜活、有血肉、有自己的动机和创伤——但他们在整本书里的存在意义，最终必须服务于主角的成长/冲突/抉择。
设计每一个配角时，都必须显式回答："这个人物对主角意味着什么？他/她会在哪些关键时刻让主角产生真实的变化？"

【关系网铺厚铁律】
每个人物的 relationships 字段必须填 **2-5 条**具体关系，不能只写 1 条或留空：
- 至少 1 条与主角（或已登场的主角圈成员）
- 其余覆盖与其他已有/后续角色（盟友、反派、长辈、旧识、血亲、宿敌、旧情都行）
- relation 要具体到"师徒/宿敌/血亲/旧情"等，不要只写"朋友/敌人"
这样后续 CharacterWeb 才有足够素材织出真正复杂的关系网。

【题材自适应】
人物的"身份/能力/阶层"按本书题材具体化——不要给现代文写"金丹期"，也别给修真文写"项目经理"：
  · 修真/玄幻：境界 + 功法 + 灵根（如"筑基中期、剑修、上品灵根"）
  · 武侠：武功修为 + 江湖身份（如"二流高手、丐帮长老"）
  · 都市/职场：职位 + 行业经验（如"市场部总监、十年金融背景"）
  · 校园：年级 + 身份（如"高三尖子生、校学生会主席"）
  · 末世：幸存者等级 + 异能/技能（如"避难所首领、A 级觉醒者"）
  · 星际：军衔/职业 + 种族（如"星际战舰副舰长、机械师"）
  · 古代/宫斗：官阶/品级/家世（如"正三品户部侍郎、御前红人"）
  · 言情/纯情感：职业 + 家庭背景（如"建筑设计师、单亲家庭长大"）
  · 系统流：宿主等级 + 系统类型
  · 克苏鲁：序列等级 + 途径（如"序列 5 太阳途径"）
realm 字段在没有"境界体系"的题材里写作"身份/级别"（如"市场部总监"/"高三 7 班学生"）。
不要硬塞"修真境界"。

【设计风格】
- 每个人物有清晰的心理动机（为自己活着，不是为剧情而存在）——但他们的"活着"最终会与主角产生化学反应
- 有心理创伤和成长弧线，性格有内在矛盾
- 说话有独特风格（同一情境不同角色反应完全不同）
- 人物之间关系要有张力、秘密、可能的背叛或羁绊
- 严禁把某个配角塑造成"本卷真主角"，导致主角沦为旁观者
输出严格JSON。"""

_CHAR_TEMPLATE = """
      "name": "姓名",
      "role": "主角|主要配角|次要配角|反派|卷内角色",
      "gender": "男|女",
      "age_desc": "外貌年龄（如'看似十七岁'）",
      "appearance": "外貌（60字，有辨识度细节）",
      "personality": "性格关键词（3-5词）",
      "personality_detail": "性格深度描述含矛盾面（100字）",
      "background": "背景身世（100字）",
      "trauma": "心理创伤/阴影（40字）",
      "desire": "内心真正渴望（30字）",
      "fear": "最深恐惧（20字）",
      "speech_pattern": "说话风格（如'惜字如金/爱用反问/总引经据典'，20字）",
      "ability": "能力特长（50字，按题材：法术/武功/技术/异能/职业技能/学科/才艺……）",
      "realm": "登场时的级别/身份（按题材：境界/武功修为/职位/年级/异能等级/官阶/家世……不强求叫'境界'）",
      "arc": "整体成长轨迹从第1卷到最后（100字）",
      "role_for_protagonist": "【配角必填，主角填'—'】这个人物对主角意味着什么？在哪些关键时刻会让主角发生真实的变化？（40字）",
      "motivation": "核心动机（30字）",
      "fatal_flaw": "致命弱点（20字）",
      "hidden_secret": "这个人物对世界/主角隐藏的秘密（40字，可以是身份/目的/过去）",
      "first_volume": 首次登场卷号,
      "last_volume": 最后重要出场卷（-1表示全程）,
      "relationships": [
        {
          "target_name": "另一角色名（必填——必须是本书已设计或将设计的角色）",
          "relation": "关系类型（尽量具体：师徒/宿敌/青梅竹马/隐藏盟友/血亲/旧情/债主/共犯/镜像对照/未婚约定/血海深仇/暗中保护/利益同盟...）",
          "evolution": "关系如何在全书变化（50字，包含关键转折点）"
        }
      ],
      "volume_arcs": {
        "1": "第1卷本角色的弧线（40字，如是配角则说明本卷如何作用于主角）"
      },
      "volume_realm": {
        "1": "第1卷末的级别/身份变化（按题材，可以是境界/职位/技能等级/家境……如本书无层级体系可省）"
      }"""


def design_all_characters(state: NovelState) -> None:
    """
    多批次设计所有人物，写入 state.characters。

    **新架构**：如果 state.master_outline.generated，走 slot-based 并发填充（每个角色一个 LLM 并发）
    **旧架构**：否则退化到原来的主角圈→盟友→反派→扩展四批次串行
    """
    if state.master_outline.generated and state.master_outline.character_slots:
        _design_by_slots(state)
    else:
        print("  ⚠ 无 MasterOutline——退化到传统分批模式")
        _design_by_legacy_batches(state)


def _design_by_slots(state: NovelState) -> None:
    """
    新架构：读 master_outline.character_slots，每个 slot 一个 LLM 并发填充完整 Character。

    关键优势：
      - 每次 LLM 只管一个角色（prompt 短、任务单一、不易截断）
      - 多个角色可并发（NUM_SLOTS / PARALLEL_WORKERS 倍加速）
      - 主角必须先跑（其他角色的 contrast_with_protagonist 要参照主角）
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS
    from agents.master_dispatcher import format_master_brief

    slots = state.master_outline.character_slots
    # 分离：主角槽位先跑，其他槽位后跑
    mc_slots = [s for s in slots if s.role_tag == "主角"]
    other_slots = [s for s in slots if s.role_tag != "主角"]

    if not mc_slots:
        print("  ⚠ Master slots 缺少主角——退化到传统模式")
        _design_by_legacy_batches(state)
        return

    master_ctx = format_master_brief(state, include_slots=False, include_setpieces=True)
    common_ctx = _common_context(state) + "\n\n" + master_ctx

    # ── Step 1: 先跑主角（其他角色的 contrast 需要主角） ─────────
    print(f"  Step 1: 先生成主角（1 次 LLM）")
    mc_char_dict = _flesh_out_slot(mc_slots[0], common_ctx, protagonist_brief="")
    if mc_char_dict:
        _parse_and_add(state, [mc_char_dict])

    protagonist = next((c for c in state.characters if c.role.value == "主角"), None)
    prot_sketch = ""
    if protagonist:
        prot_sketch = (
            f"【主角参照】{protagonist.name}：{protagonist.personality[:30]}"
            f"｜动机：{protagonist.motivation[:40]}"
        )

    # ── Step 2: 并发跑所有其他角色 ─────────────────────────
    if not other_slots:
        print(f"  ✓ 仅主角（MasterOutline 只给了 1 个槽位）")
        return

    print(f"  Step 2: 并发生成 {len(other_slots)} 个配角/反派（max_workers={PARALLEL_WORKERS}）")
    results = parallel_map(
        fn=lambda slot: _flesh_out_slot(slot, common_ctx, protagonist_brief=prot_sketch),
        items=other_slots,
        max_workers=PARALLEL_WORKERS,
        label="CharSlotFill",
    )

    # 主线程串行 append（避免 state.characters 并发写竞态）
    for char_dict in results:
        if char_dict:
            _parse_and_add(state, [char_dict])

    # ── Step 3: 按 config 数量补齐不足的角色类型 ─────────────
    _topup_to_config_targets(state, common_ctx, prot_sketch)

    # ── Step 4: 为每卷生成次要配角（卷内角色）─────────────
    _design_extended_cast(state)

    print(f"  ✓ 角色档案总计：{len(state.characters)} 个")


def _topup_to_config_targets(state: NovelState, common_ctx: str, prot_sketch: str) -> None:
    """
    MasterDispatcher 只给了核心槽位（~15-25 个）。
    根据 config 里的 MAJOR_ALLIES_MAX / ANTAGONISTS_MAX 数量，如果还不够就补齐——
    通过一次"补槽位"LLM 产出若干 CharacterSlot，然后并发填充。
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS
    from persistence.state import CharacterSlot

    # 数当前各 role_tag 数量
    by_role = {"主角": 0, "主要配角": 0, "次要配角": 0, "反派": 0, "卷内角色": 0}
    for c in state.characters:
        r = c.role.value
        by_role[r] = by_role.get(r, 0) + 1

    deficits: list[tuple[str, int]] = []
    if by_role["主要配角"] < MAJOR_ALLIES_MIN:
        deficits.append(("主要配角", MAJOR_ALLIES_MIN - by_role["主要配角"]))
    if by_role["反派"] < ANTAGONISTS_MIN:
        deficits.append(("反派", ANTAGONISTS_MIN - by_role["反派"]))

    if not deficits:
        return

    print(f"  Step 3: 补齐 config 数量 → 需补 {deficits}")

    # 为每个缺口生成槽位并填充
    for role_tag, gap in deficits:
        _topup_slots_for_role(state, role_tag, gap, common_ctx, prot_sketch)


def _topup_slots_for_role(state: NovelState, role_tag: str, gap: int,
                            common_ctx: str, prot_sketch: str) -> None:
    """
    为指定 role_tag 生成 gap 个补充槽位 + 并发填充。
    一次 LLM 产 gap 个轻量槽位（只要 narrative_function + brief_hint），然后并发填详情。
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS
    from persistence.state import CharacterSlot
    from agents.master_dispatcher import format_master_brief

    existing_names = [c.name for c in state.characters]
    used_slots_brief = "\n".join(
        f"- {c.name} [{c.role.value}·{c.narrative_function or '？'}] {c.personality[:30]}"
        for c in state.characters[:20]
    )
    master_ctx = format_master_brief(state)

    # 针对 role_tag 的功能分布提示
    if role_tag == "主要配角":
        function_hint = (
            "盟友伙伴 / 情感支撑者 / 成长引导者 / 中立者 / 镜像角色 / 背叛者 这几类里挑"
            "——根据上面已有角色的缺口补充"
        )
    elif role_tag == "反派":
        function_hint = "对立冲突者 / 搅局者 / 背叛者 其中一类——不要重复已有反派的动机/风格"
    else:
        function_hint = "按本书需要"

    prompt = f"""
为《当前小说》补 {gap} 个【{role_tag}】槽位——只要轻量 slot 描述（下游会并发填详情）。

{master_ctx}

已有角色（不要重名，功能要差异化）：
{used_slots_brief}

═══ 要求 ═══
- 生成 {gap} 个 role_tag="{role_tag}" 的槽位
- 每个必须指明 narrative_function（从 12 类里选）
- {function_hint}
- 功能要互相有差异（不要 {gap} 个都是"盟友伙伴"）
- 每个槽位的 brief_hint 要和已有角色明显区分开

输出 JSON：
{{
  "slots": [
    {{
      "slot_id": "{role_tag[:4]}_extra_01",
      "role_tag": "{role_tag}",
      "narrative_function": "从 12 类里选：情感支撑者/成长引导者/对立冲突者/信使引路者/考验者/搅局者/盟友伙伴/背叛者/中立者/象征性人物/叙述观察者/镜像角色",
      "support_role": "（细分——情感支撑者里的伴侣/家人/挚友；成长引导者里的师父/前辈 等；其他留空）",
      "function": "作用（30字）",
      "brief_hint": "一句话人设（40字）",
      "relationship_hint": "与主角/其他角色的关系（20字）",
      "narrative_arc_hint": "弧光方向（20字）",
      "function_detail": "在故事里具体怎么发挥（50字）",
      "first_volume": 1,
      "last_volume": -1
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["slots", "items"],
        min_items=max(1, gap // 2),
        max_retries=3, temperature=0.72,
        agent_name=f"TopupSlots[{role_tag}×{gap}]",
        empty_ok=True,
    )
    slots_data = pick_list(data, "slots", "items") if data else []
    if not slots_data:
        print(f"    ⚠ 补 {role_tag} 的槽位失败")
        return

    new_slots = []
    for i, sd in enumerate(slots_data):
        if not isinstance(sd, dict):
            continue
        new_slots.append(CharacterSlot(
            slot_id=sd.get("slot_id", f"{role_tag}_topup_{i+1:02d}"),
            role_tag=role_tag,
            function=sd.get("function", ""),
            brief_hint=sd.get("brief_hint", ""),
            relationship_hint=sd.get("relationship_hint", ""),
            narrative_arc_hint=sd.get("narrative_arc_hint", ""),
            first_volume=int(sd.get("first_volume", 1)),
            last_volume=int(sd.get("last_volume", -1)),
            narrative_function=sd.get("narrative_function", ""),
            support_role=sd.get("support_role", ""),
            function_detail=sd.get("function_detail", ""),
        ))

    print(f"    补 {role_tag} 的 {len(new_slots)} 个新槽位，并发填充中...")
    results = parallel_map(
        fn=lambda s: _flesh_out_slot(s, common_ctx, protagonist_brief=prot_sketch),
        items=new_slots,
        max_workers=PARALLEL_WORKERS,
        label=f"Topup[{role_tag}]",
    )
    for char_dict in results:
        if char_dict:
            _parse_and_add(state, [char_dict])

    print(f"  ✓ 角色档案总计：{len(state.characters)} 个")


def _flesh_out_slot(slot, common_ctx: str, protagonist_brief: str = "") -> Optional[dict]:
    """
    给一个 CharacterSlot 生成完整 Character dict（不 mutate state，线程安全）。
    按 slot.narrative_function 给出功能专属的设计重点。
    """
    is_mc = (slot.role_tag == "主角") or (slot.narrative_function == "主角本人")
    contrast_line = ""
    if protagonist_brief and not is_mc:
        contrast_line = f"\n{protagonist_brief}\n请在 relationships 里说清与主角的关系。"
    elif is_mc:
        contrast_line = "\n（你是主角本人）"

    # ── 按 narrative_function 生成专属设计指南 ──
    function_guide = _function_specific_guide(slot.narrative_function, slot.support_role)

    prompt = f"""
为一个角色槽位生成完整档案。

{common_ctx}
{contrast_line}

═══ 槽位信息（MasterOutline 指派给你的）═══
slot_id：{slot.slot_id}
组织身份 (role)：{slot.role_tag}
★ 叙事功能：{slot.narrative_function or '（未指定，按 role 推断）'}
★ 细分角色：{slot.support_role or '—'}
功能发挥：{slot.function_detail or slot.function}
人设提示：{slot.brief_hint}
关系提示：{slot.relationship_hint}
弧光方向：{slot.narrative_arc_hint}
出场范围：第 {slot.first_volume} 卷 - 第 {slot.last_volume if slot.last_volume != -1 else "最后"} 卷

{function_guide}

═══ 通用要求 ═══
**只填这一个角色**——不要生成别人。按槽位提示展开，起具体名字、写完整档案。
relationships 必须至少 2 条：1 条与主角（或主角圈），其他与已有/将有的角色。
role 字段填"{slot.role_tag}"，narrative_function 字段填"{slot.narrative_function}"。

输出 JSON（单个角色对象）：
{{
  "name": "起个具体的名字",
  "role": "{slot.role_tag}",
  "narrative_function": "{slot.narrative_function}",
  "support_role": "{slot.support_role}",
  "function_detail": "这个角色在其叙事功能里的具体发挥（50字，结合 slot 的 function_detail 拓展）",
  "gender": "男|女",
  "age_desc": "外貌年龄",
  "appearance": "外貌（60字）",
  "personality": "性格关键词（3-5词）",
  "personality_detail": "性格深度（100字）",
  "background": "背景身世（100字）",
  "trauma": "心理创伤（40字）",
  "desire": "内心真正渴望（30字）",
  "fear": "最深恐惧（20字）",
  "speech_pattern": "说话风格（20字）",
  "ability": "能力特长（50字）",
  "realm": "登场级别/身份",
  "arc": "整体成长轨迹（100字）",
  "role_for_protagonist": "对主角的意义（40字，结合 narrative_function 给具体场景；主角填 '—'）",
  "motivation": "核心动机（30字）",
  "fatal_flaw": "致命弱点（20字）",
  "hidden_secret": "对主角/世界隐藏的秘密（40字）",
  "first_volume": {slot.first_volume},
  "last_volume": {slot.last_volume},
  "relationships": [
    {{"target_name": "另一角色名或 slot_id", "relation": "具体关系类型", "evolution": "全书变化（50字）"}}
  ],
  "volume_arcs": {{"1": "本角色第1卷做什么（40字）"}},
  "volume_realm": {{"1": "第1卷末的级别/身份"}}
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["name", "role"],
        max_retries=3, temperature=0.78,
        agent_name=f"SlotFill[{slot.slot_id}]",
        empty_ok=True,
    )
    if not data or not data.get("name"):
        return None
    data["role"] = slot.role_tag
    data["_slot_id"] = slot.slot_id
    # 注入叙事功能字段（哪怕 LLM 没给，也按 slot 带着）
    data.setdefault("narrative_function", slot.narrative_function)
    data.setdefault("support_role", slot.support_role)
    if not data.get("function_detail"):
        data["function_detail"] = slot.function_detail
    return data


# 按叙事功能给 LLM 的专属设计指南
_FUNCTION_GUIDES = {
    "情感支撑者": """
═══ 功能专属要求（情感支撑者）═══
- 必须有"什么时候会给主角情感释放/倾听"的具体场景（写进 function_detail）
- 必须有"什么时候会和主角产生情感冲突/撕扯"的隐患（写进 hidden_secret 或 trauma）
- 如果是伴侣：双方的感情从哪开始 / 何时有危机 / 如何修复
- 如果是家人：血缘关系的负担与温暖并存
- 如果是挚友：友谊的边界在哪里会被考验
""",
    "成长引导者": """
═══ 功能专属要求（成长引导者）═══
- 必须明确"传授给主角什么"（技术/认知/心法/人情世故，写进 ability 或 function_detail）
- 必须有"何时离开主角"的计划（死亡/隐退/背离），不会陪主角到最后
- arc 里说清自己的遗憾——导师往往有未完成的心愿需要主角替他完成
- 和主角的师徒关系不能是单向灌输，双方要有真实的张力（主角偶尔质疑师父也是常态）
""",
    "对立冲突者": """
═══ 功能专属要求（对立冲突者——反派）═══
- 自洽的理念：不能是纯粹作恶，必须有一套说服自己的逻辑
- 和主角的分歧点要具体（不只是"邪恶"，而是某个理念/利益/旧怨）
- 如果是大反派：第 1 卷就应该埋伏线（隐藏身份或侧面提及）
- 如果是阶段反派：每卷被主角击败后应有"权力真空"——空出来的位置给谁
- fatal_flaw 必须是具体的、主角能利用的漏洞
""",
    "信使引路者": """
═══ 功能专属要求（信使引路者）═══
- 必须带具体的"什么信息/任务/线索"给主角（写进 function_detail 和 hidden_secret）
- 信使自己的来历往往是谜团——后期可能揭露是另一阵营派来的
- 出场章节相对集中，完成使命后可能消失或转型
""",
    "考验者": """
═══ 功能专属要求（考验者）═══
- 明确"设置了什么障碍"（具体事件/题目/规则，写进 function_detail）
- 考验不是单纯刁难——考验者必须有自己的理由（检验新人/守护某物/执法）
- 考验通过后，考验者应改变态度（认可 / 仍敌视 / 成为新任务的发布者）
""",
    "搅局者": """
═══ 功能专属要求（搅局者）═══
- 搅局者自己不一定恶意——可能是"混乱之人"或"不受控制的力量"
- 出场必然制造意外转折（function_detail 写清"在哪卷搅什么局"）
- 与主角的关系暧昧——可能是敌非敌、可能是友非友
""",
    "盟友伙伴": """
═══ 功能专属要求（盟友伙伴）═══
- 必须有独立目标——不是主角工具（motivation 填自己的，不是"帮主角"）
- 至少 1 个盟友在某卷会和主角产生真正的对立或短暂背叛
- 盟友之间也要有张力（不只是"一起帮主角"）
- role_for_protagonist 具体写"在什么关键时刻让主角产生真实变化"
""",
    "背叛者": """
═══ 功能专属要求（背叛者）═══
- 背叛必须有"说得通"的原因（理念冲突 / 被利用 / 保护某人 / 早就埋下的暗线）
- function_detail 里说清：哪一卷背叛、背叛的触发事件、事后是否有和解可能
- 前期必须有足够的亲密铺垫——否则背叛没冲击力
- 背叛后的下场：死亡？对峙？幕后？必须想清楚
""",
    "中立者": """
═══ 功能专属要求（中立者）═══
- 中立必须有明确理由（立场原则 / 个人规则 / 避世）
- 关键时刻会被卷入——中立者不是永远中立，后期可能被迫选边
- function_detail 说清"什么情况下他会打破中立"
""",
    "象征性人物": """
═══ 功能专属要求（象征性人物）═══
- 必须承载某个理念/价值观（motivation 反映该理念）
- 出场常与主题节点绑定——他出现的章节多半在表达某个重要主题
- function_detail 说清"代表什么理念"+"如何通过他的命运说明这个理念"
""",
    "叙述观察者": """
═══ 功能专属要求（叙述观察者）═══
- 视角特殊——可能是旁观者、记录者、转述者
- 他看到的东西和主角看到的不一样（这是他的价值）
- function_detail 说清"提供什么独特视角"
""",
    "镜像角色": """
═══ 功能专属要求（镜像角色）═══
- 背景/起点和主角相似，但关键岔路处走了不同的路
- 必须明确"岔路点"是什么——是哪次选择让他们分道
- 后期与主角相遇必须有"如果当初那样选就会变成这样"的震撼
- 可以是警示（堕落镜像）也可以是鼓励（坚持到底的镜像）
""",
}


def _function_specific_guide(function: str, support_role: str) -> str:
    """根据 narrative_function 返回专属设计指南。"""
    guide = _FUNCTION_GUIDES.get(function, "")
    if support_role and function in ("情感支撑者", "成长引导者", "对立冲突者"):
        guide += f"\n【本角色的细分】：{support_role}（在上述功能要求里重点贴合这一细分的特质）"
    return guide


def _design_by_legacy_batches(state: NovelState) -> None:
    """老架构（无 MasterOutline 时退化）：主角圈→盟友→反派→扩展四批次串行。"""
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS

    # Step 1: 主角圈
    _design_protagonist_circle(state)

    # Step 2: 盟友 + 反派并发
    # 两个 batch 都会 mutate state.characters.append——这里用普通 for 循环顺序执行更安全，
    # 但两次 LLM 可同时发出去；用 parallel_map 跑两个 closure 让 LLM 并发，
    # 然后在主线程顺序 append
    def _allies_task(dummy):
        _design_major_allies(state)
        return "allies"

    def _antagonists_task(dummy):
        _design_antagonists(state)
        return "antagonists"

    # 两个任务各自内部会 append 到 state.characters——为了避免两个线程同时 append，
    # 我们用锁或者改成"返回数据，主线程 append"。最简单：串行跑这两个。
    # 实际测试这两个 batch LLM 调用各 ~20 秒，串行 40 秒 vs 并发 20 秒——值得
    import threading
    state_lock = threading.Lock()

    def _allies_safe(dummy):
        before = len(state.characters)
        try:
            _design_major_allies(state)  # 这会 append 到 state.characters
        except Exception as e:
            print(f"  ⚠ 盟友批次失败：{e}")
        # 本函数的 append 已经做了，返回新增数量（仅供日志）
        return len(state.characters) - before

    def _antagonists_safe(dummy):
        before = len(state.characters)
        try:
            _design_antagonists(state)
        except Exception as e:
            print(f"  ⚠ 反派批次失败：{e}")
        return len(state.characters) - before

    # 注意：_design_major_allies 和 _design_antagonists 内部都会 `state.characters.append(...)`
    # Python list.append 在 CPython 有 GIL 保护是线程安全的，
    # 但 _parse_and_add 会读 `existing_names = {c.name for c in state.characters}` 再 add，
    # 两个线程各自的 existing_names 快照可能都不含对方的新增——导致同名冲突没被发现。
    # 我们用锁让两个 batch 在"读+写"阶段互斥，LLM 调用阶段依然并行。
    # 实际上最简洁：串行跑这两个 batch。耗时差距 40s vs 20s，换来正确性更稳。
    # 所以回退到串行——但加个注释留给未来优化者。
    _design_major_allies(state)
    _design_antagonists(state)

    # Step 3: 扩展卷内角色（需要前面所有角色名字作为 existing cast）
    _design_extended_cast(state)
    print(f"  ✓ 人物档案总计：{len(state.characters)} 个角色")


def _common_context(state) -> str:
    """所有核心人物设计共享的世界/势力上下文 + 立项取向（反派洗白/感情线等关键决策源）。"""
    realm_list = state.power_system.realm_list_str() if state.power_system else "（本书无层级体系）"
    factions_brief = "\n".join(
        f"- [{f.tier_name()}] {f.name}（{f.faction_type}）：{f.surface_goal[:30]}"
        for f in state.factions[:10]
    )
    volumes_desc = "\n".join(
        f"第{v.index}卷《{v.title}》主题：{v.theme}，对手：{v.volume_antagonist}"
        for v in state.volumes
    )
    world_ctx = format_world_context_brief(state)
    concept_block = format_concept_brief(state)
    return (
        f"{world_ctx}\n\n"
        f"{concept_block}\n\n"
        f"世界观：{state.world_setting[:300]}\n"
        f"层级/能力体系：{realm_list}\n"
        f"势力体系（共{len(state.factions)}个势力）：\n{factions_brief}\n"
        f"各卷主题：\n{volumes_desc}"
    )


def _existing_cast_brief(state) -> str:
    """已经设计好的人物名单（供后续批次参考，避免重复 + 可以设计关系）。"""
    if not state.characters:
        return "（尚无已设计角色）"
    return "\n".join(
        f"- {c.name}（{c.role.value}）：{c.personality[:25]} | 动机：{c.motivation[:30]}"
        for c in state.characters
    )


def _design_protagonist_circle(state: NovelState) -> None:
    """Batch 1：主角 + 引路人/长者 + 感情线核心（数量由 config 决定）。"""
    realm_plan = state.power_system.protagonist_realm_plan if state.power_system else '逐步提升'
    # 从目标范围里扣掉必选的主角(1人)得到剩余额度——引路人+感情线核心共 (PROTAGONIST_CIRCLE_MIN-1) ~ (PROTAGONIST_CIRCLE_MAX-1) 人
    circle_extra_min = max(0, PROTAGONIST_CIRCLE_MIN - 1)
    circle_extra_max = max(1, PROTAGONIST_CIRCLE_MAX - 1)
    prompt = f"""
为《{state.title}》设计【主角 + 引路人 + 感情线核心】——主角身边最贴身的 {PROTAGONIST_CIRCLE_MIN}-{PROTAGONIST_CIRCLE_MAX} 人（含主角）。

{_common_context(state)}

═══ 设计目标 ═══
【主角】（1人，必须）
- 致命弱点 + 难以启齿的心理创伤
- 表面目标 ≠ 内心真正渴望（后期产生撕裂）
- 全 {NUM_VOLUMES} 卷成长弧线，每卷末境界参考：{realm_plan}
- 主角是所有戏剧张力的核心——设计时多琢磨"他的弱点怎么反复折磨他"

【主角身边核心圈】（共 {circle_extra_min}-{circle_extra_max} 人；引路人 + 感情线核心 + 知己/死敌/血亲等自由组合）
- 引路人/重要长者（建议 1 人以上）：传承或引导主角的关键人物；可能在某卷死亡/隐退推动主角成长；与主角有深层羁绊或秘密
- 感情线核心（可选，若故事需要）：感情要有波折、误会、背景冲突或命运对立；与主角产生真正的化学反应而非陪衬
- 其他可能角色：童年挚友、血亲、宿敌知己、命运相牵者等——按故事需要组合

输出 JSON：
{{
  "characters": [
    {{
{_CHAR_TEMPLATE}
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["characters", "items"],
        min_items=max(1, PROTAGONIST_CIRCLE_MIN // 2),   # 软目标：哪怕只出 1-2 人也接受
        max_retries=4, temperature=0.80,
        agent_name="CharacterDesigner[主角圈]",
        empty_ok=True,
    )
    _parse_and_add(state, pick_list(data, "characters", "items") if data else [])
    if not state.characters:
        print("  ⚠ 主角圈生成彻底失败——请检查 LLM 连接，或在 Creative Intent 面板里添加更多细节后重试")
        return
    print(f"  ✓ 主角核心圈：{len(state.characters)} 人")
    for c in state.characters:
        print(f"    {c.brief()}")


def _design_major_allies(state: NovelState) -> None:
    """
    Batch 2：主要盟友/伙伴。

    如果 MAX > 6，自动分成多批生成，每批 4-5 人；
    否则一次搞定。每批都允许 empty_ok，不会因单批失败导致整个 Phase 崩溃。
    """
    target_total = MAJOR_ALLIES_MAX
    before_total = len(state.characters)

    if target_total <= 6:
        # 小规模：一次生成
        _generate_characters_batch(
            state,
            label="主要配角",
            instruction=f"设计【主要盟友/伙伴】（{MAJOR_ALLIES_MIN}-{MAJOR_ALLIES_MAX} 人）——和主角并肩却有独立目标的伙伴。",
            target_min=MAJOR_ALLIES_MIN,
            target_max=MAJOR_ALLIES_MAX,
            design_goals="""- 每人都有独立目标，不是主角工具
- 至少 1 人在某卷与主角产生真正的对立或短暂背叛
- 至少 1 人隐藏着影响主角命运的秘密
- 盟友之间互相也要有张力
- relationships 字段覆盖与主角 + 其他角色的 2-3 条关系""",
            agent_name="CharacterDesigner[主要配角]",
        )
    else:
        # 大规模：分批生成，每批 4-5 人
        batch_size = 5
        remaining = target_total
        batch_num = 1
        while remaining > 0:
            batch_target = min(batch_size, remaining)
            _generate_characters_batch(
                state,
                label=f"主要配角·批次{batch_num}",
                instruction=f"设计【主要盟友/伙伴·第{batch_num}批】（{max(2,batch_target-1)}-{batch_target} 人，避免与已有角色重名/同质）",
                target_min=max(2, batch_target - 1),
                target_max=batch_target,
                design_goals="""- 每人独立目标；避免与已有盟友同质化（风格、动机要互有区别）
- 其中至少 1 人与主角有冲突/背叛；至少 1 人有秘密（hidden_secret）
- relationships 与已有角色建立 2-3 条具体关系""",
                agent_name=f"CharacterDesigner[主要配角·B{batch_num}]",
            )
            added_this_batch = len(state.characters) - before_total - (target_total - remaining)
            if added_this_batch <= 0:
                # LLM 连续失败了，别死循环——跳出
                print(f"  ⚠ 批次 {batch_num} 未产出新角色，停止分批")
                break
            remaining = max(0, remaining - added_this_batch)
            batch_num += 1
            if batch_num > 5:  # 保底：最多 5 批
                break

    print(f"  ✓ 主要配角总计新增 {len(state.characters) - before_total} 人")
    for c in state.characters[before_total:]:
        print(f"    {c.brief()}")


def _design_antagonists(state: NovelState) -> None:
    """Batch 3：反派体系（数量由 config 决定）——大规模自动分批。"""
    target_total = ANTAGONISTS_MAX
    before_total = len(state.characters)

    if target_total <= 6:
        _generate_characters_batch(
            state,
            label="反派",
            instruction=f"设计【反派体系】（{ANTAGONISTS_MIN}-{ANTAGONISTS_MAX} 人，层次分明）",
            target_min=ANTAGONISTS_MIN,
            target_max=ANTAGONISTS_MAX,
            design_goals="""- 大反派：最终 BOSS，第 1 卷埋伏线，有自洽的世界观
- 中层反派：各卷主要对手；有些可能被主角理解
- 至少 1 个反派有悲剧背景；1-2 个与配角/主角亲友有复杂关系
- role_for_protagonist 说明如何让主角发生真实变化""",
            agent_name="CharacterDesigner[反派]",
        )
    else:
        batch_size = 5
        remaining = target_total
        batch_num = 1
        while remaining > 0:
            batch_target = min(batch_size, remaining)
            hint = "含大反派" if batch_num == 1 else "中层反派/阶段对手（不要重复已有）"
            _generate_characters_batch(
                state,
                label=f"反派·批次{batch_num}",
                instruction=f"设计【反派·第{batch_num}批】（{max(2,batch_target-1)}-{batch_target} 人，{hint}）",
                target_min=max(2, batch_target - 1),
                target_max=batch_target,
                design_goals="""- 避免与已有反派同质化（动机、手段要有差异）
- 至少 1 人与主角或主角盟友有复杂关系
- role_for_protagonist 说明如何让主角发生变化""",
                agent_name=f"CharacterDesigner[反派·B{batch_num}]",
            )
            added_this_batch = len(state.characters) - before_total - (target_total - remaining)
            if added_this_batch <= 0:
                print(f"  ⚠ 反派批次 {batch_num} 未产出新角色，停止")
                break
            remaining = max(0, remaining - added_this_batch)
            batch_num += 1
            if batch_num > 5:
                break

    print(f"  ✓ 反派总计新增 {len(state.characters) - before_total} 人")
    for c in state.characters[before_total:]:
        print(f"    {c.brief()}")


def _generate_characters_batch(
    state: NovelState,
    label: str,
    instruction: str,
    target_min: int,
    target_max: int,
    design_goals: str,
    agent_name: str,
) -> int:
    """
    通用单批次人物生成——供 _design_major_allies / _design_antagonists / 等复用。
    empty_ok=True 避免 LLM 一次吐不够就崩；min_items 设为保守的"软目标"。

    返回本批成功新增的角色数。
    """
    existing = _existing_cast_brief(state)
    # min_items 是 request_json 的"软目标"——设成 2，确保 LLM 至少吐两个；
    # target_max 通过 prompt 传给 LLM 当上限，但数量不达标也接受
    prompt = f"""
为《{state.title}》{instruction}

{_common_context(state)}

已设计的人物（避免重复 + 可互相关联）：
{existing}

═══ 设计目标 ═══
{design_goals}

输出 JSON（目标 {target_min}-{target_max} 人；字段要完整但允许简洁）：
{{
  "characters": [
    {{
{_CHAR_TEMPLATE}
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["characters", "items"],
        min_items=max(2, target_min // 2),   # 软目标：至少 2 个或 target_min 的一半
        max_retries=3,
        temperature=0.78,
        agent_name=agent_name,
        empty_ok=True,                        # ★ 关键：失败不抛异常，返回空 dict
    )
    before = len(state.characters)
    _parse_and_add(state, pick_list(data, "characters", "items") if data else [])
    added = len(state.characters) - before
    print(f"    [{label}] +{added} 人")
    return added


def _design_extended_cast(state: NovelState) -> None:
    """
    阶段B：每卷扩展角色（数量由 config 决定）——逐卷分批，避免一次性吐出 90 个全书角色撑爆 JSON。
    """
    before_total = len(state.characters)

    for v in state.volumes:
        vi = v.index
        # 每卷目标人数——如果很大（>=6），再拆成更小的批次
        per_vol_target = MINOR_CHARS_PER_VOLUME_MAX

        if per_vol_target <= 6:
            _generate_characters_batch(
                state,
                label=f"卷{vi}扩展角色",
                instruction=(
                    f"为第{vi}卷《{v.title}》[{v.chapter_start}-{v.chapter_end}章]"
                    f"设计 {MINOR_CHARS_PER_VOLUME_MIN}-{MINOR_CHARS_PER_VOLUME_MAX} 个专属卷内角色。"
                    f"卷主题：{v.theme}；对手：{v.volume_antagonist}。"
                    f"first_volume={vi}，last_volume 可 >= vi（若后续卷仍有戏份）"
                ),
                target_min=MINOR_CHARS_PER_VOLUME_MIN,
                target_max=MINOR_CHARS_PER_VOLUME_MAX,
                design_goals=(
                    "- 每个角色有独立弧线，不是背景板\n"
                    f"- 至少 1 人的命运影响第{vi}卷高潮（死亡/背叛/牺牲/觉醒）\n"
                    "- relationships 必须与已有核心人物建立关联（旧识/仇人/亲属/师承）\n"
                    "- 至少 1 个让读者喜爱然后失去的角色\n"
                    "- role 字段填「次要配角」或「卷内角色」"
                ),
                agent_name=f"CharacterDesigner[扩展·V{vi}]",
            )
        else:
            # 大规模：每卷分 2-3 小批
            batch_size = 5
            remaining = per_vol_target
            batch_num = 1
            while remaining > 0:
                batch_target = min(batch_size, remaining)
                before_batch = len(state.characters)
                _generate_characters_batch(
                    state,
                    label=f"卷{vi}扩展·批次{batch_num}",
                    instruction=(
                        f"为第{vi}卷《{v.title}》[{v.chapter_start}-{v.chapter_end}章]"
                        f"设计第 {batch_num} 批专属卷内角色（{max(2,batch_target-1)}-{batch_target} 人，"
                        f"避免与已有同卷角色重复）。卷主题：{v.theme}。"
                    ),
                    target_min=max(2, batch_target - 1),
                    target_max=batch_target,
                    design_goals=(
                        "- 各角色风格/功能要互有区别\n"
                        "- 至少 1 人与已有核心人物有 relationships 关联\n"
                        "- role 字段填「次要配角」或「卷内角色」\n"
                        f"- first_volume={vi}"
                    ),
                    agent_name=f"CharacterDesigner[扩展·V{vi}·B{batch_num}]",
                )
                added_this = len(state.characters) - before_batch
                if added_this <= 0:
                    print(f"  ⚠ 第{vi}卷批次{batch_num}无产出，跳出")
                    break
                remaining = max(0, remaining - added_this)
                batch_num += 1
                if batch_num > 4:
                    break

    added_total = len(state.characters) - before_total
    print(f"  ✓ 卷内扩展角色总计：{added_total} 人")
    for c in state.characters[before_total:]:
        print(f"    {c.brief()}")


def _parse_and_add(state: NovelState, char_list: list) -> None:
    role_map = {r.value: r for r in CharacterRole}
    existing_names = {c.name for c in state.characters}

    for cd in char_list:
        name = cd.get("name", "")
        if not name or name in existing_names:
            continue
        rels = [
            Relationship(
                target_name=r["target_name"],
                relation=r["relation"],
                evolution=r.get("evolution", ""),
            )
            for r in cd.get("relationships", [])
        ]
        char = Character(
            name=name,
            role=role_map.get(cd.get("role", "次要配角"), CharacterRole.MINOR),
            gender=cd.get("gender", "男"),
            age_desc=cd.get("age_desc", ""),
            appearance=cd.get("appearance", ""),
            personality=cd.get("personality", ""),
            personality_detail=cd.get("personality_detail", cd.get("personality", "")),
            background=cd.get("background", ""),
            trauma=cd.get("trauma", "无"),
            desire=cd.get("desire", "未知"),
            fear=cd.get("fear", "未知"),
            speech_pattern=cd.get("speech_pattern", "普通"),
            ability=cd.get("ability", ""),
            realm=cd.get("realm", "普通人"),
            arc=cd.get("arc", ""),
            motivation=cd.get("motivation", ""),
            fatal_flaw=cd.get("fatal_flaw", ""),
            first_volume=cd.get("first_volume", 1),
            last_volume=cd.get("last_volume", -1),
            relationships=rels,
            volume_arcs={int(k): v for k, v in cd.get("volume_arcs", {}).items()},
            volume_realm={int(k): v for k, v in cd.get("volume_realm", {}).items()},
            narrative_function=cd.get("narrative_function", ""),
            support_role=cd.get("support_role", ""),
            function_detail=cd.get("function_detail", ""),
            source_slot_id=cd.get("_slot_id", ""),
        )
        # 存储隐藏秘密到memory facts（供后续agent参考）
        secret = cd.get("hidden_secret", "")
        if secret:
            state.memory.facts.append(f"[人物秘密-{name}] {secret}")

        # 存储"对主角的意义"——配角必填，用于后续写作/评审时提醒"这个人存在是为了主角"
        rfp = cd.get("role_for_protagonist", "").strip()
        if rfp and rfp != "—" and char.role != CharacterRole.PROTAGONIST:
            state.memory.facts.append(f"[配角·对主角作用-{name}] {rfp}")

        state.characters.append(char)
        state.memory.character_states[char.name] = f"待登场（第{char.first_volume}卷）"
        existing_names.add(name)
