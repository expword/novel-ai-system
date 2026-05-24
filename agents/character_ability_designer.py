"""
CharacterAbilityDesigner —— 为每个核心角色生成结构化能力档案。

═══ 解决用户的诉求 ═══

> "主角的能力，还有其他人的能力都是需要长时间记录的，刚刚生成小说的时候就需要生成，
>  防止后面矛盾，还需要记录什么时候用什么能力"
> "每个具体的能力/道具/特异功能/金手指要单独调用一次模型"

→ 本 agent 在 Phase 2A 角色设计完成后跑——为每个有能力倾向的角色生成
  CharacterAbilityProfile（详见 state.py）。配套 power_timeline_tracker
  在写章后追踪使用事件、配套 invariant 防止跟历史设定矛盾。

═══ 两步流程（同 intent_asset_extractor）═══

Step A · 为每个核心角色列名（每角色 1 次轻量调用，可并行）
        输出 [(char_name, [{name, kind_hint}])...] —— 该角色应该有的能力轮廓

Step B · 每个 (角色 × 能力) 独立深化（每对 1 次独立调用，可并行）
        生成 LearnedAbility 完整字段：source / ceiling / cost / cooldown / etc

═══ 范围 ═══

只为「主角 + 主要配角 + 反派」做（≤ 10 个核心角色）。次要配角 / 卷内角色
不在此 agent 范围——他们的能力在 chapter_planner 阶段按需即时生成。

主角的 special_abilities（金手指）通过 holder_name 反查 → 自动写到
linked_special_assets，避免重复登记。
"""
from __future__ import annotations
from typing import Optional

from utils.json_utils import request_json, request_json_with_profile
from utils.concurrency import parallel_map
from config import PARALLEL_WORKERS
from persistence.state import (
    NovelState, CharacterAbilityProfile, LearnedAbility, Character,
)


# ═══════════════════════════════════════════════════════════════
#  Step A · 列名
# ═══════════════════════════════════════════════════════════════

SYSTEM_LIST = """你是【角色能力轮廓列名员】——任务：给一个角色，列出**他应该掌握的能力名 + 一句话定位**。
不要展开详细字段——只列轮廓。后续每个能力单独深化（独享上下文）。

═══ 输出范围 ═══

  innate_talents       天赋（出生就有的能力倾向）—— 1-3 条短词，例：["绝对音感","过目不忘"]
  learned_abilities    已学/规划要学的能力清单 —— 每个 {name, kind_hint, learn_at_volume(int)}
                       · learn_at_volume：哪一卷学到（1 = 开篇就会；2 = 第二卷学到）
                       · 主角 3-6 项；主要配角 2-4 项；反派 2-4 项

═══ 严格 JSON 输出 ═══

{
  "innate_talents": ["...", "..."],
  "learned_abilities": [
    {"name": "...", "kind_hint": "一句话定位（≤30字）", "learn_at_volume": 1}
  ]
}

═══ 铁律 ═══
  · 跟角色 archetype / personality / 已有 ability 字段保持一致——不要造跟角色性格矛盾的能力
  · 学习时机要符合卷级节奏——开篇全会会让后续没成长空间
  · 配角能力**不能盖过主角**——主角永远是主线
  · 不要展开 ceiling / cost / cooldown / source —— 那是 Step B 的事"""


def _step_a_list_abilities_for_char(state: NovelState, char: Character) -> dict:
    """为单个角色列能力轮廓。"""
    role = getattr(getattr(char, "role", None), "value", "")
    ps = state.power_system
    system_brief = ""
    if ps:
        system_brief = (
            f"本书力量体系：{ps.system_name}（{ps.system_type}）\n"
            f"  描述：{(ps.system_description or '')[:120]}"
        )
    realms_brief = ""
    if ps and ps.realms:
        realms_brief = "境界阶梯：" + " → ".join(r.name for r in ps.realms[:8])

    user = f"""角色档案：
  名：{char.name}
  身份：{role}
  原型：（无 archetype 字段，看 personality）
  性格：{(char.personality or '')[:80]}
  背景：{(char.background or '')[:150]}
  动机：{(char.motivation or '')[:80]}
  现有 ability 描述（如有）：{(char.ability or '')[:150]}
  当前境界：{char.realm or '(未明)'}
  出场卷范围：第 {char.first_volume} 卷 → 第 {char.last_volume if char.last_volume > 0 else 'N'} 卷

═══ 本书力量体系参考 ═══
{system_brief}
{realms_brief}

按 SYSTEM 规则列出该角色的天赋 + 能力轮廓。严格 JSON 输出。"""

    try:
        data = request_json_with_profile(
            "extractor", system=SYSTEM_LIST, user=user,
            required_keys=["learned_abilities"],
            max_retries=2, temperature=0.3,
            agent_name=f"CharAbilityList[{char.name}]", empty_ok=True,
        )
    except Exception as _e:
        print(f"  ⚠ 列名《{char.name}》失败：{type(_e).__name__}: {_e}")
        return {}
    return data or {}


# ═══════════════════════════════════════════════════════════════
#  Step B · 单个能力深化
# ═══════════════════════════════════════════════════════════════

SYSTEM_DEEPEN = """你是【单个能力深化设计员】——为角色 {char_name}（{char_role}）的能力 {ability_name}
（轮廓："{kind_hint}"），深度展开**所有可机器消费字段**。

═══ 必填字段 ═══

  source            怎么学到的（≤50 字）—— 师承/机缘/天赋觉醒/物品获得 等
  ceiling           当前能做到的极限（≤120 字）—— 越具体越好（数字 / 范围 / 时长）
  cost              使用代价（≤120 字）—— 精确说每次付出什么
  cooldown          冷却描述（≤80 字）—— "每日一次" / "每月一次" / "无冷却" / "首次免费后冷却 N 天"
  growth_hints      成长方向（≤80 字）—— 未来可能升级到什么程度
  notes             杂项备注（≤120 字）—— 跟其他能力的克制/配合关系、特殊使用条件等

═══ 严格 JSON 输出 ═══

{{
  "source": "...",
  "ceiling": "...",
  "cost": "...",
  "cooldown": "...",
  "growth_hints": "...",
  "notes": "..."
}}

═══ 铁律 ═══
  · 字段要具体——不要"消耗精神"这种空话，要"每次消耗 1-3 分钟无法说话"
  · 跟该角色 learn_at_volume 保持一致——初期能力 ceiling 不要太高
  · 跟本书力量体系兼容——不要造体系外的能力"""


# ═══ P0 优化:单角色一次 LLM 出全部能力深化 ════════════════════
SYSTEM_DEEPEN_BATCH = """你是【角色能力批量深化设计员】——为角色 {char_name}({char_role}) 一次性深化多个能力。

═══ 每条能力都要给齐 6 个字段 ═══
  name              对应 input 的 ability_name(原样回写,必须严格匹配输入)
  source            怎么学到的(≤50字)—— 师承/机缘/天赋觉醒/物品获得 等
  ceiling           当前能做到的极限(≤120字)—— 越具体越好(数字/范围/时长)
  cost              使用代价(≤120字)—— 精确说每次付出什么
  cooldown          冷却描述(≤80字)—— "每日一次"/"每月一次"/"无冷却"/"首次免费后 N 天"
  growth_hints      成长方向(≤80字)
  notes             杂项备注(≤120字)—— 跟其他能力的克制/配合关系等

═══ 严格 JSON 输出 ═══

{{
  "abilities": [
    {{"name":"...","source":"...","ceiling":"...","cost":"...","cooldown":"...","growth_hints":"...","notes":"..."}},
    ...
  ]
}}

═══ 铁律 ═══
  · abilities 数组长度必须等于输入条数,每条对应一个 input,name 严格匹配
  · 字段要具体,不要"消耗精神"这种空话
  · learn_at_volume 越早的 ceiling 越低,逐级递增
  · 跟本书力量体系兼容——不要造体系外的能力
  · N 条能力之间互有差异,避免互相抄袭字段(否则深化失去意义)"""


def _step_b_deepen_one(state: NovelState, char_name: str, char_role: str,
                         ability_name: str, kind_hint: str) -> Optional[dict]:
    """对 (角色, 能力) 单独深化(保留兼容入口,新代码用 _step_b_deepen_all_for_char)。"""
    ps = state.power_system
    system_brief = ""
    if ps:
        system_brief = f"本书力量体系：{ps.system_name}（{ps.system_type}）"

    user = f"""═══ 上下文 ═══
{system_brief}

═══ 现在专注深化这一个 (角色 × 能力) ═══
角色：{char_name}（{char_role}）
能力：{ability_name}
轮廓：{kind_hint or '（未给轮廓）'}

按 SYSTEM 深化所有字段。严格 JSON 输出。"""

    try:
        return request_json_with_profile(
            "extractor",
            system=SYSTEM_DEEPEN.format(
                char_name=char_name, char_role=char_role,
                ability_name=ability_name, kind_hint=kind_hint,
            ),
            user=user,
            required_keys=["ceiling", "cost"],
            max_retries=2, temperature=0.3,
            agent_name=f"AbilityDeepen[{char_name}:{ability_name}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  ⚠ 深化《{char_name}:{ability_name}》失败：{type(e).__name__}: {e}")
        return None


def _step_b_deepen_all_for_char(state: NovelState, char_name: str, char_role: str,
                                  abilities: list[tuple[str, str, int]]) -> dict[str, dict]:
    """P0 优化:单角色一次 LLM 出全部能力深化结果。

    abilities: list[(name, kind_hint, learn_at_volume)]
    返回 dict[name -> {source/ceiling/cost/cooldown/growth_hints/notes}]
    失败的能力不在返回 dict,调用方按需兜底。
    """
    if not abilities:
        return {}
    ps = state.power_system
    system_brief = f"本书力量体系:{ps.system_name}({ps.system_type})" if ps else ""

    ab_lines = []
    for i, (name, hint, vol) in enumerate(abilities, 1):
        ab_lines.append(f"  {i}. name={name!r} | 轮廓={hint or '(未给)'} | learn_at_volume={vol}")
    ab_block = "\n".join(ab_lines)

    user = f"""═══ 上下文 ═══
{system_brief}

═══ 角色 ═══
姓名:{char_name}({char_role})

═══ 待深化的 {len(abilities)} 条能力 ═══
{ab_block}

按 SYSTEM 一次性为以上每条能力深化所有字段。严格 JSON 输出(abilities 数组长度必须等于 {len(abilities)})。"""

    try:
        data = request_json_with_profile(
            "extractor",
            system=SYSTEM_DEEPEN_BATCH.format(char_name=char_name, char_role=char_role),
            user=user,
            required_keys=["abilities"],
            max_retries=2, temperature=0.3,
            agent_name=f"AbilityDeepenBatch[{char_name}]",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  ⚠ 批量深化《{char_name}》失败:{type(e).__name__}: {e}")
        return {}

    if not data:
        return {}

    by_name: dict[str, dict] = {}
    for item in (data.get("abilities") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        by_name[name] = item
    return by_name


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

def design_all_character_abilities(state: NovelState, *, force: bool = False) -> dict[str, CharacterAbilityProfile]:
    """Phase 2A 之后调——为每个核心角色生成 CharacterAbilityProfile。

    幂等：state.character_ability_profiles 已有该角色档案 + force=False 则跳过该角色。
    范围：主角 + 主要配角 + 反派（次要配角 / 卷内角色不做——按需即时生成）。
    """
    if not state.characters:
        print("  ⚠ 无角色，跳过 character_ability_designer")
        return state.character_ability_profiles

    core_roles = {"主角", "主要配角", "反派"}
    targets = [c for c in state.characters
                if getattr(getattr(c, "role", None), "value", "") in core_roles]
    if not targets:
        print("  ⚠ 无核心角色（主角/主要配角/反派），跳过")
        return state.character_ability_profiles

    if not force:
        targets = [c for c in targets if c.name not in state.character_ability_profiles]
        if not targets:
            print(f"  ✓ 所有核心角色已有 ability profile（{len(state.character_ability_profiles)} 个），跳过")
            return state.character_ability_profiles

    print(f"  ── character_ability_designer：为 {len(targets)} 个核心角色生成档案 ──")

    # ── Step A：每个角色列名（并发）──
    print(f"  Step A · 列名（{len(targets)} 个并发调用）")
    list_results = parallel_map(
        fn=lambda c: (c, _step_a_list_abilities_for_char(state, c)),
        items=targets,
        max_workers=min(4, len(targets)),
        label="CharAbilityList",
    )

    # ── Step B (P0 优化):单角色一次 LLM 出全部能力深化(原:每能力一次,慢 5-10×)──
    # 先收集每个角色的能力清单
    char_outline: dict[str, dict] = {}     # char_name → outline dict
    by_char_specs: dict[str, list] = {}    # char_name → list[(name, kind_hint, learn_vol)]
    chars_to_deepen: list[Character] = []
    for pair in (list_results or []):
        if not pair:
            continue
        char, data = pair
        if not data:
            continue
        char_outline[char.name] = data
        specs = []
        for ab in (data.get("learned_abilities") or []):
            if not isinstance(ab, dict):
                continue
            name = str(ab.get("name") or "").strip()
            if not name:
                continue
            kind = str(ab.get("kind_hint") or "")[:50]
            learn_vol = int(ab.get("learn_at_volume", 1) or 1)
            specs.append((name, kind, learn_vol))
        if specs:
            by_char_specs[char.name] = specs
            chars_to_deepen.append(char)

    total_abilities = sum(len(v) for v in by_char_specs.values())
    print(f"  Step B · 单角色批量深化({len(chars_to_deepen)} 个角色并发,共 {total_abilities} 条能力)")

    def _deepen_char_batch(char):
        role_val = getattr(getattr(char, "role", None), "value", "")
        specs = by_char_specs.get(char.name, [])
        return char, _step_b_deepen_all_for_char(state, char.name, role_val, specs)

    deep_results = parallel_map(
        fn=_deepen_char_batch,
        items=chars_to_deepen,
        max_workers=min(PARALLEL_WORKERS, len(chars_to_deepen) or 1),
        label="AbilityDeepenByChar",
    ) if chars_to_deepen else []

    # 把深化结果按角色聚合
    by_char: dict[str, list[LearnedAbility]] = {}
    for pair in (deep_results or []):
        if not pair:
            continue
        char, name_to_data = pair
        if not isinstance(name_to_data, dict):
            name_to_data = {}
        specs = by_char_specs.get(char.name, [])
        for name, _kind, learn_vol in specs:
            data = name_to_data.get(name)
            if not data:
                # 该能力深化缺失——保底登记壳子(可能 LLM 输出条数不全)
                data = {"source": "", "ceiling": "(待补)", "cost": "(待补)",
                        "cooldown": "", "growth_hints": "", "notes": "深化 LLM 缺该条目,待补充"}
            # learn_at_chapter = -1 (起手就会) or 负数标记卷号(后续 chapter_planner 细化到章)
            learn_at_chapter = -1 if learn_vol == 1 else -learn_vol
            la = LearnedAbility(
                name=name,
                learned_at_chapter=learn_at_chapter,
                source=str(data.get("source") or "")[:60],
                ceiling=str(data.get("ceiling") or "")[:120],
                cost=str(data.get("cost") or "")[:120],
                cooldown=str(data.get("cooldown") or "")[:80],
                notes=str(data.get("notes") or "")[:200],
            )
            by_char.setdefault(char.name, []).append(la)

    # 写回 state.character_ability_profiles
    out = state.character_ability_profiles
    for char in targets:
        outline = char_outline.get(char.name, {})
        innate = [str(t)[:30] for t in (outline.get("innate_talents") or [])][:5]
        learned = by_char.get(char.name, [])
        # 主角的 special_abilities 反查
        linked = []
        if state.power_system:
            linked = [ab.name for ab in (state.power_system.special_abilities or [])
                       if ab.holder_name == char.name and ab.name]
        prof = CharacterAbilityProfile(
            holder_name=char.name,
            innate_talents=innate,
            learned_abilities=learned,
            linked_special_assets=linked,
            ceiling_now=(learned[0].ceiling if learned else ""),
            weakness="",  # 留空，未来扩展由独立 agent 设计
        )
        out[char.name] = prof
        role_val = getattr(getattr(char, "role", None), "value", "")
        print(f"  ✓ 《{char.name}》({role_val}): {len(innate)} 天赋 / {len(learned)} 能力 / "
              f"{len(linked)} 关联金手指")

    # progress_warning 让用户在 UI 看到
    try:
        from persistence.checkpoint import add_progress_warning, clear_progress_warnings
        clear_progress_warnings(source="character_ability_designer")
        if out:
            add_progress_warning(
                level="info",
                source="character_ability_designer",
                message=(
                    f"已为 {len(targets)} 个核心角色生成结构化能力档案（每个能力独立 LLM 调用）。"
                    "防后续矛盾的累积契约已就绪。请在 web UI 查看并按需调整。"
                ),
            )
    except Exception:
        pass

    return out
