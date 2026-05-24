"""
CharacterWebAgent — 设计完整的人物关系网络。

在人物设计完成之后运行，专门挖掘：
- 表面关系 vs 真实关系（隐藏身份/隐藏目的）
- 每段关系的"张力"和"演变轨迹"
- 权力链条（谁暗中控制谁）
- 隐藏同盟（前期不揭露）
- 各卷关系如何变化（对立/和解/背叛/牺牲）
"""
from utils.json_utils import repair_json, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, RelationshipWeb, CharacterBond
from config import (
    NUM_VOLUMES,
    RELATIONSHIP_BONDS_MIN, RELATIONSHIP_BONDS_MAX,
    HIDDEN_RELATIONS_MIN, HIDDEN_RELATIONS_MAX,
    TRIANGLE_RELATIONS_MIN, TRIANGLE_RELATIONS_MAX,
    POWER_CHAINS_MIN, POWER_CHAINS_MAX,
    HIDDEN_ALLIANCES_MIN, HIDDEN_ALLIANCES_MAX,
    CROSS_FACTION_BONDS_MIN, CROSS_FACTION_BONDS_MAX,
    RELATIONSHIP_HINTS_PER_CHAPTER,
)


SYSTEM = """你是小说关系网络设计师，专注于人物关系的复杂性和戏剧张力。
你设计的关系网络需要：
- 尽可能覆盖所有核心人物两两之间的"有张力"关系——不是冷冰冰的"认识/陌生人"，而是有具体情感/利益/历史勾连的关系
- 有大量"表里不一"的关系（表面一种，真实另一种）
- 每段重要关系都有具体的张力来源（不只是"对立"或"友好"）
- 关系随卷推进发生变化（演变、背叛、和解、揭秘）
- 多种关系类型并存：情感羁绊 / 血亲 / 师承 / 同门 / 旧情 / 宿仇 / 盟约 / 债务 / 威胁 / 照镜对照 / 镜像人生
- 关系网可以有多个节点（配角之间也有独立关系），但整张网的存在意义是：
  **每条关系最终都要对【唯一主角】产生可感知的影响**——要么直接牵扯主角，要么通过配角的行动间接改变主角的处境/心境。
  不存在"与主角毫无关联"的纯背景关系；如果你想写一条独立关系，必须说清它最终如何投射到主角身上。
输出严格JSON。"""


def design_relationship_web(state: NovelState) -> None:
    """
    设计完整关系网络——按"语义主题"分批，每批 6-8 条关系，然后合并。
    这样避免一次 LLM 吐 25+ 条复杂结构体导致 JSON 截断。

    批次设计：
      1) 主角核心圈：主角与主要盟友/对手/爱人/长辈的直接关系（~8-10 条）
      2) 配角间张力：盟友vs盟友、盟友vs反派、反派vs反派（~6-8 条）
      3) 隐藏/翻转：表里不一 + 跨敌我 + 血亲暗线（~6-8 条）
      4) 权力结构 + 隐藏同盟（separate LLM call for non-bond items）
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS
    from agents import require_upstream
    if not require_upstream(state, "CharacterWeb",
        characters=lambda s: bool(s.characters),
    ):
        return

    char_list = "\n".join(
        f"- {c.name}【{c.role.value}】{c.personality[:30]} | 动机：{c.motivation[:30]}"
        for c in state.characters[:30]  # 过多人物截取前 30
    )
    volumes_brief = "\n".join(f"第{v.index}卷：{v.theme}" for v in state.volumes)

    common_context = f"""
为《{state.title}》设计人物关系——{len(state.characters)} 人，{len(state.volumes)} 卷。

人物列表：
{char_list}

全书卷结构：
{volumes_brief}

【单主角铁律】所有关系最终都要对主角产生可感知的影响（直接或间接）。
"""

    BOND_SCHEMA = """{
      "bond_id": "b001（唯一编号，批次间不重复）",
      "char_a": "角色A名（必须是人物列表里存在的名字）",
      "char_b": "角色B名",
      "surface_relation": "表面关系（如师徒/陌生人/普通朋友）",
      "true_relation": "真实关系",
      "hidden_secret": "一方或双方不知的秘密（无则留空）",
      "tension_source": "张力来源（30字）",
      "volume_evolution": {"1": "第1卷状态（20字）", "2": "..."},
      "future_trajectory": "未来走向（50字）",
      "projected_changes": {"3": "...", "5": "..."},
      "reveal_volume": -1,
      "affects_protagonist": true
    }"""

    # ── 批次定义 ────────────────────────────────────
    # 每批目标 6-8 条；总体配额 ~20-25 条，匹配 RELATIONSHIP_BONDS_MIN..MAX
    batches = [
        {
            "label": "主角核心圈",
            "focus": (
                "仅涉及主角与其他核心角色（主要配角/反派/感情线）的直接关系。"
                "每条都要能在下游写作里具体调用。"
            ),
            "target_min": 6,
            "target_max": 10,
            "hidden_required": 1,
        },
        {
            "label": "配角间张力",
            "focus": (
                "**不要涉及主角**的两两关系：盟友↔盟友、盟友↔反派、反派↔反派。"
                "这些关系最终必须通过某个路径影响主角（在 future_trajectory 里说清）。"
            ),
            "target_min": 5,
            "target_max": 8,
            "hidden_required": 2,
        },
        {
            "label": "隐藏与反转",
            "focus": (
                "表里不一的关系（surface_relation 和 true_relation 明显不同）、"
                "跨敌我阵营的复杂关系（敌方对主角或友方有真情感）、"
                "血亲/旧情/宿仇等暗线。"
            ),
            "target_min": 5,
            "target_max": 8,
            "hidden_required": 4,
        },
    ]

    def _gen_batch(batch: dict) -> list[dict]:
        # Phase 2.2:thread-local user_feedback 注入
        from utils.feedback_helper import get_user_feedback_prefix
        feedback_prefix = get_user_feedback_prefix()
        prompt = f"""{feedback_prefix}{common_context}

═══ 本批次：【{batch['label']}】 ═══
{batch['focus']}

═══ 本批要求 ═══
- 生成 {batch['target_min']}-{batch['target_max']} 条关系
- 其中至少 {batch['hidden_required']} 条 hidden_secret 非空（埋暗线给后期揭露）
- bond_id 用 "{batch['label'][:2]}_01" 这种前缀，避免与其他批次冲突
- volume_evolution 至少覆盖 2-3 卷的变化

输出 JSON：
{{
  "bonds": [
    {BOND_SCHEMA}
  ]
}}
"""
        data = request_json(
            system=SYSTEM, user=prompt,
            list_candidates=["bonds", "relations", "items"],
            min_items=3, max_retries=3, temperature=0.75,
            agent_name=f"CharacterWeb[{batch['label']}]",
            empty_ok=True,
        )
        return data.get("bonds", []) if data else []

    # 并发跑三批
    print(f"  并发分 3 批生成关系（每批独立 LLM 调用）...")
    batch_results = parallel_map(
        fn=_gen_batch,
        items=batches,
        max_workers=min(3, PARALLEL_WORKERS),
        label="CharacterWeb",
    )

    # 合并所有批次的 bonds
    web = RelationshipWeb()
    bond_id_seen = set()
    valid_char_names = {c.name for c in state.characters}

    for batch_bonds in batch_results:
        if not batch_bonds:
            continue
        for bd in batch_bonds:
            if not isinstance(bd, dict):
                continue
            bid = bd.get("bond_id", "")
            if not bid or bid in bond_id_seen:
                bid = f"b{len(web.bonds)+1:03d}"
            bond_id_seen.add(bid)
            # 校验角色名——LLM 有时会造角色
            a = bd.get("char_a", "")
            b = bd.get("char_b", "")
            if a not in valid_char_names or b not in valid_char_names:
                continue
            web.bonds.append(CharacterBond(
                bond_id=bid,
                char_a=a, char_b=b,
                surface_relation=bd.get("surface_relation", ""),
                true_relation=bd.get("true_relation", ""),
                hidden_secret=bd.get("hidden_secret", ""),
                tension_source=bd.get("tension_source", ""),
                volume_evolution={int(k): v for k, v in bd.get("volume_evolution", {}).items() if str(k).isdigit()},
                reveal_volume=int(bd.get("reveal_volume", -1) or -1),
                affects_protagonist=bool(bd.get("affects_protagonist", True)),
                future_trajectory=bd.get("future_trajectory", ""),
                projected_changes={int(k): v for k, v in bd.get("projected_changes", {}).items() if str(k).isdigit()},
            ))

    # ── 最后单独一次 LLM 生成权力链条 + 隐藏同盟（这些是字符串列表，便宜）──
    structure_prompt = f"""{common_context}

已设计的关系摘要：
{chr(10).join(f'  {b.char_a}↔{b.char_b}：{b.surface_relation}/{b.true_relation}' for b in web.bonds[:15])}

═══ 要求 ═══
基于上面的关系，设计：
1. power_chains（{POWER_CHAINS_MIN}-{POWER_CHAINS_MAX} 条权力链条）——谁暗中控制谁，每条说明对主角的影响路径
2. hidden_alliances（{HIDDEN_ALLIANCES_MIN}-{HIDDEN_ALLIANCES_MAX} 个）——前期读者不知道的秘密同盟
3. faction_affiliations——每个核心角色归属哪些势力（字典）

输出 JSON：
{{
  "power_chains": ["A通过...控制B", "..."],
  "hidden_alliances": ["X与Y的秘密同盟", "..."],
  "faction_affiliations": {{"角色名": ["势力名1"]}}
}}
"""
    struct_data = request_json(
        system=SYSTEM, user=structure_prompt,
        max_retries=3, temperature=0.7,
        agent_name="CharacterWeb[结构]",
        empty_ok=True,
    ) or {}
    web.power_chains = struct_data.get("power_chains", [])
    web.hidden_alliances = struct_data.get("hidden_alliances", [])
    web.faction_affiliations = struct_data.get("faction_affiliations", {})

    state.relationship_web = web

    print(f"  ✓ 关系网络：{len(web.bonds)} 条关系线")
    hidden = [b for b in web.bonds if b.hidden_secret]
    tense = [b for b in web.bonds if b.reveal_volume > 0]
    print(f"    含秘密关系：{len(hidden)} 对 | 待揭露关系：{len(tense)} 对")
    print(f"    权力链条：{len(web.power_chains)} 条 | 隐藏同盟：{len(web.hidden_alliances)} 个")

    for bond in web.bonds:
        if bond.hidden_secret:
            reveal = f"（第{bond.reveal_volume}卷揭露）" if bond.reveal_volume > 0 else ""
            state.memory.facts.append(
                f"[关系秘密] {bond.char_a}↔{bond.char_b}：{bond.hidden_secret}{reveal}"
            )


def get_web_context_for_chapter(state: NovelState, chapter_index: int, chars_in_scene: list[str]) -> str:
    """为写作agent提供本章涉及角色的关系上下文。"""
    if not state.relationship_web.bonds:
        return ""
    vol = state.current_volume_index
    relevant = [
        b for b in state.relationship_web.bonds
        if b.char_a in chars_in_scene or b.char_b in chars_in_scene
    ]
    if not relevant:
        return ""
    lines = []
    # 按配置的条数上限截取（预算控制），剩下的关系虽然不注入 prompt 但仍保存在 state 里
    for b in relevant[:RELATIONSHIP_HINTS_PER_CHAPTER]:
        evo = b.volume_evolution.get(vol, "")
        secret_hint = f"（隐藏：{b.hidden_secret[:25]}）" if b.hidden_secret else ""
        lines.append(
            f"  {b.char_a}↔{b.char_b}：{b.surface_relation}"
            f"{secret_hint} | 张力：{b.tension_source[:25]}"
            + (f" | 本卷：{evo}" if evo else "")
        )
    return "关系提示：\n" + "\n".join(lines)
