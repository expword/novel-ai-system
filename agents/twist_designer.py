"""
TwistDesignerAgent —— 反转设计师。

职责：产出"层层反转"的烧脑体验——读者以为 X，揭露是 Y；以为 Y 是终点，又发现是 Z；
以为 Z 才是真相，结果连 Z 都是误导，背后还有 W。

两步拆分（先总→并发分）：
  Step A：脑暴 3-6 条反转链的"种子"（title/category/initial_setup/target_layers/涉及角色）
  Step B：每条链并发展开所有层（TwistLayer 列表）

核心原则：
  1. 每层反转都要提前埋伏笔——不是作弊凭空冒出
  2. 越深层越颠覆——Layer 1 局部反转，Layer 4 世界观崩塌
  3. 情感代价递增——每层揭露都要带来更大的情感冲击
  4. 前层必须"自洽"——读到揭露前读者相信它是真的

差分等级：
  · moderate       ：2 层反转（常规"原来如此"）
  · brain_burning  ：3 层反转（两次打脸读者）
  · mind_bending   ：4 层反转（终极烧脑）
"""
from utils.json_utils import request_json, pick_list
from persistence.state import (
    NovelState, TwistSystem, TwistChain, TwistLayer,
)
from agents.concept_pitch import format_world_context_brief
from agents.plot_enhancer import format_adopted_supplements


SYSTEM = """你是反转设计师（Twist Designer）——专门设计"读者永远猜不到下一步"的多层反转。

你不是写场景，是设计"认知颠覆"——把读者的判断推到极限，再打破它。

【四类反转手法】
  · 信息缺失补全：读者看到的是事实的一部分，补全后意义反转
  · 视角欺骗：主角/叙述者的视角本身是错的，真相从旁观者显现
  · 因果颠倒：A 导致 B 其实是 B 预埋了 A；施害者实为受害者
  · 身份替换：某人不是他自称/被认为的那个人（本体/化身/伪装）

【层层递进的节奏】
  Layer 1：局部反转——某个人/某件事的表面不是真相（"他不是敌人，其实是卧底"）
  Layer 2：动机反转——Layer 1 揭示的"新真相"本身也是演出来的（"他是卧底但为了更深的算计"）
  Layer 3：视角反转——前两层都是读者视角被误导，真正的视角来自另一个人（"整个事件的主角其实不是我"）
  Layer 4：设定反转——世界规则本身被颠覆（"从头到尾读者以为的世界规则是假的"）

【严格约束】
  · 每层的 surface_belief 必须自洽——揭露前读者真的会信
  · 每层 reveal 必须有前期伏笔支撑（clues_planted）——作者公平，不搞脑洞突袭
  · 反转的"爽感"不在数量，在质量——宁可 2 层惊艳，不要 4 层强行

输出严格 JSON。"""


def design_twists(state: NovelState) -> None:
    """
    生成完整反转系统，写入 state.twist_system。
    两步：先脑暴反转链种子，再并发展开每条链的层。
    """
    from utils.concurrency import parallel_map
    from agents import require_upstream
    if not require_upstream(state, "TwistDesigner",
        volumes=lambda s: bool(s.volumes),
        characters=lambda s: bool(s.characters),
        master_outline=lambda s: bool(s.master_outline and s.master_outline.generated),
    ):
        return

    # ═══ Step A：脑暴 3-6 条反转链种子 ═══
    print("  [TwistDesigner] Step A: 脑暴反转链种子")
    seeds_result = _design_chain_seeds(state)
    if not seeds_result or not seeds_result.get("chains"):
        print("  [TwistDesigner] ✗ Step A 反转链种子生成失败——TwistSystem 不可用")
        return

    chain_seeds = seeds_result.get("chains", [])
    design_principle = seeds_result.get("design_principle", "")
    reader_curve = seeds_result.get("reader_experience_curve", "")

    print(f"    产出 {len(chain_seeds)} 条反转链种子")
    for seed in chain_seeds:
        print(f"    · [{seed.get('difficulty','?')}] {seed.get('title','?')} （{seed.get('target_layers','?')} 层）")

    # ═══ Step B：并发展开每条链的层 ═══
    print(f"  [TwistDesigner] Step B: 并发展开 {len(chain_seeds)} 条反转链的每一层")

    def _expand(seed: dict) -> dict:
        return _design_chain_layers(state, seed, chain_seeds)

    expanded = parallel_map(
        fn=_expand,
        items=chain_seeds,
        max_workers=min(4, len(chain_seeds)),
        label="TwistDesigner-Layers",
    )

    # ═══ 组装 TwistSystem ═══
    chains: list[TwistChain] = []
    failed_seeds: list[str] = []
    for seed, detail in zip(chain_seeds, expanded):
        if not detail:
            failed_seeds.append(seed.get("title", "?"))
            continue
        layers_data = detail.get("layers", []) or []
        layers = [
            TwistLayer(
                layer=int(l.get("layer", i + 1)),
                surface_belief=l.get("surface_belief", ""),
                reveal=l.get("reveal", ""),
                clues_planted=l.get("clues_planted", []) or [],
                reveal_anchor=l.get("reveal_anchor", ""),
                emotional_impact=l.get("emotional_impact", ""),
                twist_mechanism=l.get("twist_mechanism", ""),
            )
            for i, l in enumerate(layers_data)
            if isinstance(l, dict)
        ]
        scope = seed.get("scope") or ("within_volume" if seed.get("difficulty") == "moderate" else "cross_volume")
        vol_span = [int(v) for v in (seed.get("volume_span") or []) if isinstance(v, (int, str))]
        anchor_vol = int(seed.get("anchor_volume", 0) or 0)
        if scope == "within_volume" and anchor_vol and not vol_span:
            vol_span = [anchor_vol]
        chain = TwistChain(
            chain_id=seed.get("chain_id") or f"twist_{len(chains)+1:02d}",
            title=seed.get("title", ""),
            category=seed.get("category", ""),
            initial_setup=seed.get("initial_setup", ""),
            target_layers=int(seed.get("target_layers", 2)),
            layers=layers,
            involved_characters=seed.get("involved_characters", []) or [],
            involved_factions=seed.get("involved_factions", []) or [],
            difficulty=seed.get("difficulty", "moderate"),
            design_rationale=detail.get("design_rationale", seed.get("design_rationale", "")),
            linked_foreshadow_ids=detail.get("linked_foreshadow_ids", []) or [],
            scope=scope,
            volume_span=vol_span,
            anchor_volume=anchor_vol,
        )
        chains.append(chain)

    if failed_seeds:
        print(f"  ⚠ {len(failed_seeds)} 条反转链的层展开失败（已跳过）：{failed_seeds}")

    state.twist_system = TwistSystem(
        chains=chains,
        design_principle=design_principle,
        reader_experience_curve=reader_curve,
    )

    cross = sum(1 for c in chains if c.scope == "cross_volume")
    within = sum(1 for c in chains if c.scope == "within_volume")
    print(f"  ✓ 反转系统：{len(chains)} 条反转链（大反转 {cross} / 小反转 {within}）")
    for c in chains:
        scope_tag = "跨卷" if c.scope == "cross_volume" else f"单卷@第{c.anchor_volume}卷"
        span_tag = f" 跨 {c.volume_span}" if c.scope == "cross_volume" and c.volume_span else ""
        print(f"    · {c.title}（{c.category}｜{c.difficulty}｜{scope_tag}{span_tag}｜{len(c.layers)}/{c.target_layers} 层）")
        for layer in c.layers[:4]:
            print(f"        L{layer.layer} [{layer.twist_mechanism}] {layer.reveal[:40]}"
                  f" @ {layer.reveal_anchor}")


# ═══════════════════════════════════════════════════════
#  Step A：反转链种子
# ═══════════════════════════════════════════════════════

def _design_chain_seeds(state: NovelState) -> dict:
    """只产出种子——每条链 1-2 行信息，不展开层。输出量 ~800-1200 字。"""
    ctx = format_world_context_brief(state)
    mo = state.master_outline

    # 候选素材：MasterOutline 的 plot_setpieces + 人物槽位 + 势力骨架
    setpiece_brief = ""
    if mo and mo.generated and mo.plot_setpieces:
        setpiece_brief = "\n【MasterOutline 预设节点（可作反转种子）】\n" + "\n".join(
            f"  · {p.anchor}｜{p.kind}：{p.gist[:60]}"
            for p in mo.plot_setpieces[:10]
        )

    char_brief = ""
    if state.characters:
        char_brief = "\n【现有角色（反转链可挂在他们身上）】\n" + "\n".join(
            f"  · {c.name}（{getattr(c.role, 'value', str(c.role))}）：{(c.personality or '')[:40]}"
            for c in state.characters[:12]
        )

    faction_brief = ""
    if state.factions:
        faction_brief = "\n【现有势力（反转链可挂到势力阵营）】\n" + "\n".join(
            f"  · {f.name}（T{f.tier}{'[隐]' if f.is_hidden else ''}）"
            for f in state.factions[:10]
        )

    volume_brief = "\n".join(
        f"第{v.index}卷《{v.title}》：{v.theme}" for v in state.volumes
    ) if state.volumes else ""

    supplements_block = format_adopted_supplements(state.creative_intent)
    if supplements_block:
        supplements_block = (
            supplements_block
            + "\n  ⚠ 含「关系反转伏笔」「设定爆点」类建议 → 必须转成本批次的具体反转链"
            "（每条采纳建议都要在 layers 里被某一层揭露）"
        )

    # Phase 2.2:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    # 用户创作意图（world_tone_hint / avoid_tropes_hints）
    from utils.intent_helper import build_intent_brief
    intent_brief = build_intent_brief(state, "twist_designer")
    prompt = f"""{feedback_prefix}{intent_brief}
为《{state.title}》（题材：{state.genre}）设计【层层反转】的骨架。

{ctx}

{supplements_block}

全书主题：{state.theme}
故事前提：{getattr(mo, 'story_premise', '')[:160]}
核心矛盾：{getattr(mo, 'central_conflict', '')[:100]}
{setpiece_brief}
{char_brief}
{faction_brief}

卷结构（判断反转揭露时机）：
{volume_brief}

═══ 本步任务：只产出反转链"种子"（不要展开每层细节）═══

设计 3-6 条反转链。按"大反转/小反转"组合：

【大反转链（跨越多卷）——scope = "cross_volume"】
  · 层的 reveal_anchor 分散在不同卷，营造长期悬念
  · 对应 brain_burning（3 层，跨 2-3 卷）或 mind_bending（4 层，跨 3-N 卷）
  · 至少要 1 条 mind_bending——全书最深反转（终极身世/世界真相/主角本体）
  · 至少要 1 条 brain_burning——中后期炸裂

【小反转链（单卷之内）——scope = "within_volume"】
  · 所有层的 reveal_anchor 都落在同一卷的不同章节
  · 对应 moderate（2 层）——本卷内"以为是 A，其实是 B"的快速反转
  · 服务单卷的情节张力，不拖累主线

每条链只填：
  - chain_id（"twist_01"..."twist_06"）
  - title（反转链命名，10-15 字，如"主角身世三重真相"）
  - category（身世/阵营/目的/因果/身份/设定 之一）
  - initial_setup（全书开头读者相信的设定，50 字）
  - target_layers（2 / 3 / 4，按 difficulty 推）
  - difficulty（moderate / brain_burning / mind_bending）
  - scope（"within_volume" = 小反转单卷内｜"cross_volume" = 大反转跨卷）
  - anchor_volume（小反转：落在哪一卷；大反转：起始卷号）
  - volume_span（若 cross_volume：层会覆盖的卷号列表，按顺序，如 [2,3,5]；若 within_volume：就填 [anchor_volume]）
  - involved_characters（挂在哪些角色身上，用现有角色名；没有合适的可留空）
  - involved_factions（若涉及势力阵营，用现有势力名）
  - design_rationale（60 字：为什么这条反转链有意义；颠覆读者的什么判断）

同时在顶层给出：
  - design_principle（80 字：整套反转系统的设计理念——你要让读者经历什么）
  - reader_experience_curve（100 字：读者从开头到结尾的认知曲线描述——从相信 X 到怀疑到崩溃到重建）

本步**严禁**输出 layers 详情（下一步会为每条链并发展开）。

输出 JSON：
{{
  "design_principle": "...",
  "reader_experience_curve": "...",
  "chains": [
    {{
      "chain_id": "twist_01",
      "title": "...",
      "category": "身世|阵营|目的|因果|身份|设定",
      "initial_setup": "...",
      "target_layers": 2,
      "difficulty": "moderate|brain_burning|mind_bending",
      "scope": "within_volume|cross_volume",
      "anchor_volume": 1,
      "volume_span": [1],
      "involved_characters": ["..."],
      "involved_factions": ["..."],
      "design_rationale": "..."
    }}
  ]
}}
"""
    example = (
        '{"design_principle":"...","reader_experience_curve":"...",'
        '"chains":[{"chain_id":"twist_01","title":"...","category":"身世",'
        '"initial_setup":"...","target_layers":3,"difficulty":"brain_burning",'
        '"scope":"cross_volume","anchor_volume":2,"volume_span":[2,3,5],'
        '"involved_characters":["..."],"involved_factions":[],"design_rationale":"..."}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["chains"],
        list_candidates=["chains"],
        min_items=2,
        max_retries=4, temperature=0.78,
        agent_name="TwistDesigner/Seeds",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  Step B：展开一条反转链的每一层
# ═══════════════════════════════════════════════════════

_LAYER_GUIDES = {
    1: '局部反转——某个人/某件事的表面真相被揭露。读者首次"原来如此"。',
    2: "动机反转——Layer 1 揭示的'新真相'本身也是表演。读者开始怀疑一切。",
    3: "视角反转——前两层都是误导，真正的主视角来自另一个人。读者的世界观第一次动摇。",
    4: "设定反转——世界规则本身被颠覆。读者彻底推翻此前的一切判断，全书基调重建。",
}


def _design_chain_layers(state: NovelState, seed: dict, all_seeds: list[dict]) -> dict:
    """
    为单条反转链展开所有层——输出 TwistLayer 列表。
    输出量约 800-1200 字（每层 200-300 字 × 2-4 层）。
    """
    target = int(seed.get("target_layers", 2))
    target = max(2, min(4, target))

    # 本链涉及的角色/势力简述
    char_bits = []
    for name in (seed.get("involved_characters") or [])[:4]:
        c = next((x for x in state.characters if x.name == name), None)
        if c:
            char_bits.append(f"{name}（{getattr(c.role, 'value', '?')}：{(c.personality or '')[:30]}）")
        else:
            char_bits.append(name)
    faction_bits = []
    for fname in (seed.get("involved_factions") or [])[:4]:
        f = next((x for x in state.factions if x.name == fname), None)
        if f:
            faction_bits.append(f"{fname}（T{f.tier}）")
        else:
            faction_bits.append(fname)

    # 伏笔提示：给 LLM 列现有 foreshadow 的 id 和内容，让它挂钩
    fw_brief = ""
    if state.foreshadow_items:
        fw_brief = "\n【可挂钩的现有伏笔（linked_foreshadow_ids 用这些 id）】\n" + "\n".join(
            f"  · [{fw.fw_id}] {fw.content[:50]}（卷{fw.planted_chapter}埋，卷{fw.planned_resolve_volume}兑）"
            for fw in state.foreshadow_items[:12]
        )

    other_chains_brief = "\n".join(
        f"  · {s.get('title','?')}（{s.get('category','?')}/{s.get('difficulty','?')}）"
        for s in all_seeds if s.get("chain_id") != seed.get("chain_id")
    )

    guides_block = "\n".join(
        f"  · Layer {i}：{_LAYER_GUIDES[i]}"
        for i in range(1, target + 1)
    )

    scope = seed.get("scope", "cross_volume")
    anchor_vol = int(seed.get("anchor_volume", 0) or 0)
    vol_span = seed.get("volume_span") or []
    if scope == "within_volume":
        scope_rule = (
            f"【本链为小反转——单卷内完成】\n"
            f"  · 所有 {target} 层的 reveal_anchor 必须落在【第 {anchor_vol} 卷】，只变化章号\n"
            f"  · 示例：Layer 1 @ 第{anchor_vol}卷中段，Layer 2 @ 第{anchor_vol}卷末\n"
            f"  · 节奏紧凑，服务单卷情节"
        )
    else:
        span_hint = " / ".join(f"第{v}卷" for v in vol_span) if vol_span else f"第{anchor_vol}卷起到全书末"
        scope_rule = (
            f"【本链为大反转——跨越多卷】\n"
            f"  · {target} 层的 reveal_anchor 必须分散在不同卷，形成长期悬念\n"
            f"  · 推荐卷号范围：{span_hint}\n"
            f"  · 越深层越靠后——Layer 1 在最早的卷，Layer {target} 在最后的卷\n"
            f"  · 中间可以跨 1-2 卷的「沉默期」让读者以为真相已揭（其实还没）"
        )

    prompt = f"""
为以下反转链展开每一层的具体内容：

═══ 反转链种子 ═══
标题：{seed.get('title','')}
类别：{seed.get('category','')}
初始认知：{seed.get('initial_setup','')}
目标层数：{target}
难度：{seed.get('difficulty','')}
跨度：{scope}（anchor_volume={anchor_vol}，volume_span={vol_span}）
设计理念：{seed.get('design_rationale','')}
涉及角色：{' / '.join(char_bits) if char_bits else '（未指定）'}
涉及势力：{' / '.join(faction_bits) if faction_bits else '（未指定）'}

{scope_rule}

═══ 其他反转链（避免重复/对冲）═══
{other_chains_brief if other_chains_brief else '（本书仅此一条）'}

{fw_brief}

═══ 本链层层递进要求（共 {target} 层）═══
{guides_block}

═══ 输出要求 ═══
展开每一层的：
  - layer（层号 1 到 {target}）
  - surface_belief（揭露前读者/主角相信的内容，40字——必须自洽可信）
  - reveal（揭露的真相，50字——颠覆 surface_belief）
  - clues_planted（提前要埋的伏笔 2-3 条，各 30 字——让揭露来得公平）
  - reveal_anchor（何时揭露，用卷/章锚点，如"第3卷中段"/"第5卷高潮前夕"）
  - emotional_impact（对主角或读者的情感冲击，25字）
  - twist_mechanism（四选一："信息缺失补全" / "视角欺骗" / "因果颠倒" / "身份替换"）

另填：
  - design_rationale（如果种子的设计理念需要补充，可优化；不变可原样）
  - linked_foreshadow_ids（如有可挂钩的现有伏笔 id，填上；没有留空数组）

**严格约束**：
  1. 每层 surface_belief 必须是上一层 reveal 的延伸——形成链条
  2. 每层 reveal 必须和 clues_planted 对齐——不能凭空揭晓
  3. reveal_anchor 的卷号必须单调不减——越深层越靠后揭露

输出 JSON：
{{
  "design_rationale": "...",
  "linked_foreshadow_ids": ["..."],
  "layers": [
    {{
      "layer": 1,
      "surface_belief": "...",
      "reveal": "...",
      "clues_planted": ["伏笔1","伏笔2"],
      "reveal_anchor": "第2卷末",
      "emotional_impact": "...",
      "twist_mechanism": "信息缺失补全|视角欺骗|因果颠倒|身份替换"
    }}
  ]
}}
"""
    example = (
        '{"design_rationale":"...","linked_foreshadow_ids":[],"layers":['
        '{"layer":1,"surface_belief":"...","reveal":"...","clues_planted":["..."],'
        '"reveal_anchor":"第2卷末","emotional_impact":"...","twist_mechanism":"信息缺失补全"}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["layers"],
        list_candidates=["layers"],
        min_items=max(1, target - 1),  # 容忍少一层
        max_retries=3, temperature=0.75,
        agent_name=f"TwistDesigner/Chain-{seed.get('chain_id','?')}",
        example_schema=example,
        empty_ok=True,
    )
    return data or {}


# ═══════════════════════════════════════════════════════
#  下游用：给 writer/chapter_planner 注入反转上下文
# ═══════════════════════════════════════════════════════

def format_twists_brief(state: NovelState, volume_index: int = 0, max_chars: int = 500) -> str:
    """
    给 writer/chapter_planner 注入的反转上下文。
    只展示当前卷可能触发的反转（reveal_anchor 匹配本卷）。
    """
    ts = getattr(state, "twist_system", None)
    if not ts or not ts.chains:
        return ""

    lines = ["【本卷可触发的反转】"]
    hit = 0
    for chain in ts.chains:
        relevant_layers = [
            layer for layer in chain.layers
            if not volume_index or f"第{volume_index}卷" in (layer.reveal_anchor or "")
        ]
        if not relevant_layers:
            continue
        hit += 1
        lines.append(f"· [{chain.title}] {chain.category}/{chain.difficulty}")
        for layer in relevant_layers[:2]:
            lines.append(
                f'    L{layer.layer}@{layer.reveal_anchor}：'
                f'从「{layer.surface_belief[:30]}」→「{layer.reveal[:40]}」'
            )
    if hit == 0:
        return ""
    result = "\n".join(lines)
    return result[:max_chars] if len(result) > max_chars else result
