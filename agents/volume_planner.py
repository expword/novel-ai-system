"""
VolumePlannerAgent — 规划所有卷的结构（主题/弧线/关键事件/卷首卷尾钩子）。

核心原则（分形起承转合 + 单主角）：
- 整本书是一次完整的起承转合，由卷来承担"起/承/转/合"四个角色；
- 每一卷自身也是一次完整的起承转合（内部由大情节/小情节/章节分形承担）；
- 所有卷的走向都服务于"主角"这一个人——其他角色再重要，也是配角；
- 每一卷都必须明确回答：为什么要写这一卷（purpose）？这一卷想让读者感受到什么（expression）？
"""
import json
from typing import Optional
from utils.json_utils import repair_json, safe_parse, pick_list, request_json
from llm_layer.llm import system_user
from persistence.state import NovelState, Volume, BookStructurePlan
from config import NUM_VOLUMES, CHAPTERS_PER_VOLUME_MIN, CHAPTERS_PER_VOLUME_MAX
from agents.concept_pitch import format_concept_brief


SYSTEM = """你是顶级小说结构师，把"起承转合"当作分形骨架，但知道好小说的生命在骨架之上——跌宕起伏、反转再反转、让读者心跳跟着翻滚。

你设计卷结构时记住几件事：
- 起承转合是每一层的骨架，但骨架不等于节奏。同一段"承"里可以藏三次小反转，同一段"转"可以是"以为要崩盘→竟然赢了→赢的代价是更大的崩盘"这种双重反转。
- 主角故事永远是核心。所有卷都围绕同一个主角展开，配角是照亮主角的镜子，不能喧宾夺主。
- 每一卷要能答清楚："为什么非有这一卷？"和"这一卷想让读者带走什么情绪/认知？"回答要具体可感——例如"让读者第一次为主角心疼"胜过"情感深化"。
- 卷与卷之间要有强力钩子，读者放不下书。

输出严格 JSON。"""


def plan_all_volumes(state: NovelState) -> None:
    """规划所有卷的结构 + 整本书的起承转合分段，分配章节数，写入 state.volumes 和 state.book_structure。

    ⚠ 兜底路径——主路径是 plan_all_volumes_dispatched（依赖 master_outline）。
    本函数仅在 master_dispatcher Step A 静默失败、master_outline.generated=False 时被调用。
    不要直接 import 调用——总是走 plan_all_volumes_dispatched 让它根据 state 选择。
    """

    concept_block = format_concept_brief(state)
    prompt = f"""
为《{state.title}》规划完整的{NUM_VOLUMES}卷结构，先给整本书的起承转合分段，再给每卷细节。

{concept_block}

世界观：{state.world_setting}
能力体系：{state.power_system}
主要势力：{state.world_factions_desc}
整体弧线：{state.overall_arc}

主要人物：
{state.character_brief_list()}

卷章节数：每卷 {CHAPTERS_PER_VOLUME_MIN}-{CHAPTERS_PER_VOLUME_MAX} 章

═══ 第一步：整本书分段 ═══
把{NUM_VOLUMES}卷分到"起/承/转/合"四段——分配比例你定（1-2-2-1 / 2-2-1-1 / 1-2-1-2 都可以），但四段必须齐全。
每一段的"使命"由你根据故事来定；以下只是参考方向，不是规定：
  · 起：让读者进入、让读者开始关心主角
  · 承：主角一边积累一边暗流涌动，读者越来越投入
  · 转：全面打碎前面建立的期待，读者措手不及
  · 合：在破碎中重建，让所有伏笔落地

═══ 第二步：跌宕起伏——按故事需要设计反转 ═══
起承转合是骨架，但骨架不等于精彩。整本书需要反转，但不是每卷都必须有反转——该铺垫的卷就老老实实铺垫，别强塞。反转的出现要让读者"啊！原来如此"，而不是"又来了"。
反转的强度由故事决定：
  · 单反转：主角以为是 A，结果是 B
  · 双重反转：表面 A → 揭露 B → 竟然是 C
  · 多重反转（反转反转再反转）：层层剥皮，每揭一层都颠覆前面认知——这是高阶牌，通常留在高潮卷/结局卷
反转可以是大情节级（整卷走向翻盘），也可以是小情节级（某个关键场景的意外）。在 arc 字段里如有反转设计，说明它在哪里埋、何处爆；如无反转，说明这一卷的张力靠什么（情感积累/悬念维持/主角困境深化）。

═══ 第三步：每卷设计 ═══
每卷给齐：
  · structure_role：起/承/转/合（与第一步分配一致）
  · purpose：这一卷为什么非有不可——说得具体可感（比如"让读者第一次为主角的抉择心疼"好过"情感深化"）
  · expression：读者读完这一卷带走什么（情绪/领悟/信息，具体可感）
  · arc：本卷内部自身的起-承-转-合走势 + **至少一处反转设计**（说明反转的位置和本质）
  · 主角在本卷要完成什么"不可逆的变化"（内心/处境/关系）
  · opening_hook + closing_hook

所有设计围绕主角——配角再出彩也是配角。如果某卷主角戏份少，你得说清楚为什么非这么安排，且主角内心变化如何被折射。

输出 JSON：
{{
  "book_structure": {{
    "book_proposition": "整本书核心命题（30字内）",
    "book_expression": "读者最终带走什么（30字）",
    "phase_volumes": {{"起": [...], "承": [...], "转": [...], "合": [...]}},
    "phase_purposes": {{"起": "...", "承": "...", "转": "...", "合": "..."}},
    "phase_expressions": {{"起": "...", "承": "...", "转": "...", "合": "..."}}
  }},
  "volumes": [
    {{
      "index": 1,
      "title": "卷标题",
      "theme": "本卷核心主题（10字以内）",
      "structure_role": "起|承|转|合",
      "purpose": "为什么必须写这一卷（30-40字，具体可感，不要抽象词）",
      "expression": "读者带走什么（25字，具体可感）",
      "arc": "本卷弧线（180字：本卷自己的起承转合走势 + 若有反转则说明反转在哪/怎么爆；若无反转则说明本卷张力如何积累）",
      "chapters": 章节数（{CHAPTERS_PER_VOLUME_MIN}-{CHAPTERS_PER_VOLUME_MAX}）,
      "volume_antagonist": "本卷主要对手角色名",
      "opening_hook": "卷首钩子（50字）",
      "closing_hook": "卷尾钩子（50字）",
      "key_events": ["主角相关的重大事件，3-5个，每条30字"]
    }}
  ]
}}
"""
    def _validator(d):
        # 软校验：至少 NUM_VOLUMES 的 70% 就可以——如果 LLM 给少了，后面补齐
        vols = pick_list(d, "volumes", "volume_list", "items") if isinstance(d, dict) else []
        min_accept = max(2, (NUM_VOLUMES * 7) // 10)
        if len(vols) < min_accept:
            return False, f"volumes 列表长度 {len(vols)} < 最低接受数 {min_accept}"
        for i, v in enumerate(vols):
            if not isinstance(v, dict):
                return False, f"第{i}卷不是对象"
            for k in ("index", "title", "chapters"):
                if k not in v:
                    return False, f"第{i}卷缺少 {k}"
        return True, ""

    example = (
        '{"book_structure": {"book_proposition": "...", "phase_volumes": {"起":[1],"承":[2,3],"转":[4,5],"合":[6]}, '
        '"phase_purposes": {"起":"...","承":"...","转":"...","合":"..."}, '
        '"phase_expressions": {"起":"...","承":"...","转":"...","合":"..."}}, '
        '"volumes": [{"index":1,"title":"...","theme":"...","structure_role":"起",'
        '"purpose":"...","expression":"...","arc":"...","chapters":80,'
        '"volume_antagonist":"...","opening_hook":"...","closing_hook":"...","key_events":["..."]}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["volumes", "volume_list", "items"],
        min_items=max(2, (NUM_VOLUMES * 7) // 10),
        item_required_keys=["index", "title", "chapters"],
        custom_validator=_validator,
        max_retries=5, temperature=0.7, agent_name="VolumePlanner",
        example_schema=example,
        empty_ok=True,   # 彻底失败也不抛，让后续 graceful degrade
    )
    if not data:
        raise RuntimeError("VolumePlanner 彻底失败——没有卷结构后续 Phase 无法继续。请检查 LLM 连接。")

    # 如果 LLM 给的卷数不足，补齐默认占位卷（director 后续 Phase 也会做更多初始化）
    vols_raw = pick_list(data, "volumes", "volume_list", "items")
    if len(vols_raw) < NUM_VOLUMES:
        print(f"  ⚠ LLM 只给了 {len(vols_raw)}/{NUM_VOLUMES} 卷——自动补齐占位卷（内容可在前端编辑）")
        avg_chapters = (CHAPTERS_PER_VOLUME_MIN + CHAPTERS_PER_VOLUME_MAX) // 2
        while len(vols_raw) < NUM_VOLUMES:
            idx = len(vols_raw) + 1
            vols_raw.append({
                "index": idx,
                "title": f"第{idx}卷（待命名）",
                "theme": "（待设计，建议在前端补齐）",
                "structure_role": "承",
                "purpose": "",
                "expression": "",
                "arc": "",
                "chapters": avg_chapters,
                "volume_antagonist": "",
                "opening_hook": "",
                "closing_hook": "",
                "key_events": [],
            })
        # 把修正后的列表写回 data，让后面逻辑用
        data["volumes"] = vols_raw

    # 整本书起承转合
    bs_data = data.get("book_structure", {}) if isinstance(data, dict) else {}
    state.book_structure = BookStructurePlan(
        book_proposition=bs_data.get("book_proposition", ""),
        book_expression=bs_data.get("book_expression", ""),
        phase_volumes={k: [int(x) for x in v] for k, v in bs_data.get("phase_volumes", {}).items()},
        phase_purposes=bs_data.get("phase_purposes", {}),
        phase_expressions=bs_data.get("phase_expressions", {}),
    )

    volumes_data = pick_list(data, "volumes", "volume_list", "items")

    chapter_cursor = 1
    for vd in volumes_data:
        n_chapters = vd["chapters"]
        # structure_role 优先用 volume 自己声明的；否则从 book_structure 反查
        struct_role = vd.get("structure_role", "") or state.book_structure.role_for_volume(vd["index"])
        vol = Volume(
            index=vd["index"],
            title=vd["title"],
            theme=vd["theme"],
            arc=vd["arc"],
            chapter_start=chapter_cursor,
            chapter_end=chapter_cursor + n_chapters - 1,
            opening_hook=vd["opening_hook"],
            closing_hook=vd["closing_hook"],
            volume_antagonist=vd["volume_antagonist"],
            key_events=vd.get("key_events", []),
            structure_role=struct_role,
            purpose=vd.get("purpose", ""),
            expression=vd.get("expression", ""),
        )
        state.volumes.append(vol)
        chapter_cursor += n_chapters

    total = sum(v.total_chapters for v in state.volumes)
    print(f"  ✓ {NUM_VOLUMES} 卷规划完成，全书共 {total} 章")
    if state.book_structure.book_proposition:
        print(f"    【整本命题】{state.book_structure.book_proposition}")
        for role in ("起", "承", "转", "合"):
            vols = state.book_structure.phase_volumes.get(role, [])
            if vols:
                purpose = state.book_structure.phase_purposes.get(role, "")
                print(f"    [{role}] 卷{vols} — {purpose[:40]}")
    for v in state.volumes:
        tag = f"[{v.structure_role}]" if v.structure_role else ""
        print(f"     第{v.index}卷《{v.title}》{tag}：第{v.chapter_start}-{v.chapter_end}章（{v.total_chapters}章）主题：{v.theme}")
        if v.purpose:
            print(f"       为何：{v.purpose[:50]}")
        if v.expression:
            print(f"       表达：{v.expression[:40]}")


BATCH_SIZE = 30  # 每批最多生成的章节数，避免JSON过长被截断


# ═══════════════════════════════════════════════════════════════
#  新架构：MasterOutline 驱动 + 并发 per-volume
# ═══════════════════════════════════════════════════════════════
#  plan_all_volumes()                ← 传统一次性大 prompt（保留作为 fallback）
#  plan_all_volumes_dispatched()     ← 新入口：Step A 只定 book_structure；
#                                      Step B 按 structure_role 并发填每卷详情
# ═══════════════════════════════════════════════════════════════

_BOOK_STRUCTURE_SYSTEM = """你是小说顶层结构师，只做一件事：把 N 卷分配到"起承转合"四段，
为每段定使命与表达。不要写卷的具体内容——那是下游 per-volume 的事。
输出严格 JSON。"""

_PER_VOLUME_SYSTEM = """你是小说单卷结构师，只管【一卷】——精细、有戏、有钩子。
你接收整本书的蓝图（MasterOutline + book_structure），只为你负责的这一卷填：
标题 / 主题 / 本卷弧线（起承转合内部小分形）/ purpose / expression / 对手 / 首尾钩子 / 关键事件 / 章节数。
不要越界替其他卷做决定。
输出严格 JSON。"""


def plan_book_structure_only(state: NovelState) -> None:
    """Step A：只规划整本书起承转合分段（书命题 + 各段卷分配 + 各段使命）。"""
    from agents.master_dispatcher import format_master_brief

    master_ctx = format_master_brief(state, include_setpieces=True)
    concept_block = format_concept_brief(state)

    prompt = f"""
为《{state.title}》做顶层"起承转合"分段规划。

{master_ctx}

{concept_block}

═══ 规划要求 ═══
把 {NUM_VOLUMES} 卷分到"起/承/转/合"四段——分配比例由你定，但四段必须齐全。
只输出分段 + 使命 + 表达，**不要**为每卷写具体内容。

输出 JSON：
{{
  "book_proposition": "整本书核心命题（30字内）",
  "book_expression": "读者读完带走什么（30字）",
  "phase_volumes": {{"起": [1], "承": [2,3], "转": [4,5], "合": [6]}},
  "phase_purposes": {{"起": "...", "承": "...", "转": "...", "合": "..."}},
  "phase_expressions": {{"起": "...", "承": "...", "转": "...", "合": "..."}}
}}
"""
    data = request_json(
        system=_BOOK_STRUCTURE_SYSTEM, user=prompt,
        required_keys=["book_proposition", "phase_volumes"],
        max_retries=3, temperature=0.65,
        agent_name="BookStructure",
        empty_ok=True,
    )
    if not data:
        print("  ⚠ BookStructure 生成失败——用兜底分配（前 N/4 卷='起'，后 N/4 卷='合'）")
        # 兜底：简单均分
        q = max(1, NUM_VOLUMES // 4)
        phase_volumes = {
            "起": list(range(1, q + 1)),
            "承": list(range(q + 1, q + 1 + q)),
            "转": list(range(q + 1 + q, NUM_VOLUMES - q + 1)),
            "合": list(range(NUM_VOLUMES - q + 1, NUM_VOLUMES + 1)),
        }
        data = {
            "book_proposition": state.theme[:30] or "（待补充）",
            "phase_volumes": phase_volumes,
            "phase_purposes": {"起": "建立", "承": "推进", "转": "颠覆", "合": "收束"},
            "phase_expressions": {"起": "", "承": "", "转": "", "合": ""},
        }

    state.book_structure = BookStructurePlan(
        book_proposition=data.get("book_proposition", ""),
        book_expression=data.get("book_expression", ""),
        phase_volumes={k: [int(x) for x in v] for k, v in data.get("phase_volumes", {}).items()},
        phase_purposes=data.get("phase_purposes", {}),
        phase_expressions=data.get("phase_expressions", {}),
    )
    print(f"  ✓ 整本命题：{state.book_structure.book_proposition}")
    for role in ("起", "承", "转", "合"):
        vs = state.book_structure.phase_volumes.get(role, [])
        if vs:
            print(f"    [{role}] 卷{vs} — {state.book_structure.phase_purposes.get(role, '')[:40]}")


def _plan_one_volume(state: NovelState, volume_index: int) -> Optional['dict']:
    """Step B worker：只规划一卷的详情。线程安全——只读 state。"""
    from agents.master_dispatcher import format_master_brief

    structure_role = state.book_structure.role_for_volume(volume_index)
    phase_purpose = state.book_structure.phase_purposes.get(structure_role, "")
    phase_expression = state.book_structure.phase_expressions.get(structure_role, "")

    master_ctx = format_master_brief(state, include_slots=True, include_setpieces=True)
    concept_block = format_concept_brief(state)

    # 让 LLM 知道"相邻卷"的角色，避免冲突
    other_phases = "\n".join(
        f"  [{role}] 卷 {vols} — {state.book_structure.phase_purposes.get(role, '')[:40]}"
        for role, vols in state.book_structure.phase_volumes.items()
        if role != structure_role
    )

    prompt = f"""
为《{state.title}》的【第 {volume_index} 卷】规划详情。

本卷在整本书起承转合中承担：【{structure_role}】
本段使命：{phase_purpose}
本段表达：{phase_expression}

其他段位（仅供避免重复/冲突）：
{other_phases}

{master_ctx}

{concept_block}

═══ 要求：只为本卷设计，不要越界到其他卷 ═══
- title：卷名（8 字内）
- theme：卷主题（10 字内）
- arc：本卷弧线（180 字，说清本卷内部的起-承-转-合 + 是否有反转）
- purpose：为什么非有这一卷（40字，具体可感）
- expression：读者读完本卷带走什么（25字）
- chapters：本卷章节数（{CHAPTERS_PER_VOLUME_MIN}-{CHAPTERS_PER_VOLUME_MAX}）
- volume_antagonist：本卷主要对手（对应 Master Outline 里某个 character_slot，用 slot_id 指代）
- opening_hook：卷首钩子（50字）
- closing_hook：卷尾钩子（50字）
- key_events：主角相关的 3-5 个重大事件（各 30 字）

输出 JSON：
{{
  "index": {volume_index},
  "structure_role": "{structure_role}",
  "title": "...",
  "theme": "...",
  "arc": "...",
  "purpose": "...",
  "expression": "...",
  "chapters": {(CHAPTERS_PER_VOLUME_MIN + CHAPTERS_PER_VOLUME_MAX) // 2},
  "volume_antagonist": "...",
  "opening_hook": "...",
  "closing_hook": "...",
  "key_events": ["事件1", "事件2", "事件3"]
}}
"""
    data = request_json(
        system=_PER_VOLUME_SYSTEM, user=prompt,
        required_keys=["title", "theme", "chapters"],
        max_retries=3, temperature=0.72,
        agent_name=f"VolumePlanner[V{volume_index}]",
        empty_ok=True,
    )
    return data


def plan_all_volumes_dispatched(state: NovelState) -> None:
    """
    新架构入口：Master Outline 驱动 + 并发 per-volume。
    要求 state.master_outline.generated=True（否则退化到 plan_all_volumes）。

    执行：
      1. plan_book_structure_only(state)  — 1 次 LLM 只定分段
      2. parallel_map(每卷)               — N 次 LLM 并发填详情
    """
    from utils.concurrency import parallel_map
    from config import PARALLEL_WORKERS

    if not state.master_outline.generated:
        print("  ⚠ MasterOutline 未生成，退化到单次大 prompt 模式")
        plan_all_volumes(state)
        return

    # Step A
    print("  Step A: 分配整本书起承转合（1 次 LLM）")
    plan_book_structure_only(state)

    # Step B
    print(f"  Step B: 并发规划 {NUM_VOLUMES} 卷详情（{NUM_VOLUMES} 次并发 LLM，max_workers={PARALLEL_WORKERS}）")
    results = parallel_map(
        fn=lambda i: _plan_one_volume(state, i),
        items=list(range(1, NUM_VOLUMES + 1)),
        max_workers=PARALLEL_WORKERS,
        label="VolumeDetail",
    )

    # 主线程串行 append——保持章节编号连续
    chapter_cursor = 1
    avg_chapters = (CHAPTERS_PER_VOLUME_MIN + CHAPTERS_PER_VOLUME_MAX) // 2
    for i, vd in enumerate(results, 1):
        if not vd or not isinstance(vd, dict):
            # 兜底占位卷
            print(f"    ⚠ 第{i}卷详情失败，补占位卷")
            vd = {
                "index": i, "title": f"第{i}卷（待命名）", "theme": "",
                "arc": "", "purpose": "", "expression": "",
                "chapters": avg_chapters, "volume_antagonist": "",
                "opening_hook": "", "closing_hook": "", "key_events": [],
            }
        n_chapters = int(vd.get("chapters", avg_chapters))
        # 钳制章节数到合法范围
        n_chapters = max(CHAPTERS_PER_VOLUME_MIN, min(CHAPTERS_PER_VOLUME_MAX, n_chapters))
        struct_role = vd.get("structure_role", "") or state.book_structure.role_for_volume(i)
        state.volumes.append(Volume(
            index=i,
            title=vd.get("title", f"第{i}卷"),
            theme=vd.get("theme", ""),
            arc=vd.get("arc", ""),
            chapter_start=chapter_cursor,
            chapter_end=chapter_cursor + n_chapters - 1,
            opening_hook=vd.get("opening_hook", ""),
            closing_hook=vd.get("closing_hook", ""),
            volume_antagonist=vd.get("volume_antagonist", ""),
            key_events=vd.get("key_events", []),
            structure_role=struct_role,
            purpose=vd.get("purpose", ""),
            expression=vd.get("expression", ""),
        ))
        chapter_cursor += n_chapters

    total = sum(v.total_chapters for v in state.volumes)
    print(f"  ✓ 全书 {NUM_VOLUMES} 卷规划完成（共 {total} 章）")
    for v in state.volumes:
        tag = f"[{v.structure_role}]" if v.structure_role else ""
        print(f"     第{v.index}卷《{v.title}》{tag}：Ch{v.chapter_start}-{v.chapter_end} · {v.theme}")


def _build_outline_prompt(volume_index, vol, batch, prev_context, prev_titles, chars_str, lines_str, stage=None):
    """构造一批章节大纲的 prompt。stage 非空时注入大情节上下文。"""
    batch_start, batch_end = batch[0], batch[-1]
    title_dedup_block = ""
    if prev_titles:
        title_dedup_block = (
            "\n【已使用过的标题（本批必须避免类似前缀/同一意象/同一句式结构）】\n"
            + " / ".join(t[-30:] for t in prev_titles[-30:])
            + "\n要求：本批 N 个标题之间也要互不相似——不要全是 X中的Y / 灰烬的Y 这种同套句式。"
        )

    stage_block = ""
    stage_id_hint = ""
    if stage is not None:
        role = f"[{stage.structure_role}]" if stage.structure_role else ""
        stage_block = (
            f"\n═══ ★ 当前批次属于本卷大情节：{stage.name}{role} ═══\n"
            f"  · stage_id：{stage.stage_id}\n"
            f"  · 类型：{stage.stage_type} | 氛围：{stage.atmosphere}\n"
            f"  · 主角处境：{stage.protagonist_role}\n"
            f"  · 这条大情节的使命（purpose）：{stage.purpose or '（未填，按章节范围推进）'}\n"
            f"  · 想让读者感受（expression）：{stage.expression or '（未填）'}\n"
            f"  · 主要活动：{' / '.join(stage.key_activities[:5]) if stage.key_activities else '（按情节自然展开）'}\n"
            f"  · 章节范围：{stage.chapter_start}-{stage.chapter_end}（共 {stage.chapter_end - stage.chapter_start + 1} 章）\n"
            "—— 本批 outlines 是这条大情节内部的起承转合分配，每条 goal 都要直接服务于该 stage 的 purpose / expression。\n"
            "—— 本批要在内部形成完整的起承转合：开头入戏、中段推进、关键节点反转/高潮、结尾落定并交棒下一 stage。\n"
        )
        stage_id_hint = f', "stage_id": "{stage.stage_id}"'

    return f"""为第{volume_index}卷《{vol.title}》生成第{batch_start}~{batch_end}章的大纲。

卷主题：{vol.theme} | 主要对手：{vol.volume_antagonist}
关键事件：{' / '.join(vol.key_events[:3])}
叙事线：{lines_str[:300]}
活跃人物：{chars_str[:300]}
{stage_block}
{prev_context}{title_dedup_block}

【标题质量要求】
- 每个标题 4-12 字，简短有钩子
- 不要全是"灰烬中的 X"/"X 之夜"/"X 的 Y"这种同句式批量化
- 不同章用不同的句式骨架：动词短语 / 名词短语 / 三字短句 / 反问 / 物名 都换着用
- title 字段必须填——下游 writer 会直接用，不再自拟

输出JSON，严格包含{len(batch)}条记录：
{{
  "chapter_outlines": [
    {{"index": {batch_start}, "title": "标题", "goal": "目标（60字以内）", "position": "卷首|普通|卷中高潮|卷尾"{stage_id_hint}}}
  ]
}}"""


def _plan_outlines_for_indices(state, volume_index, vol, indices, prior_outlines, chars_str, lines_str, stage=None):
    """
    为给定一段 chapter_indices 生成 outlines（按 BATCH_SIZE 再切片，避免 JSON 截断）。
    indices 必须是连续递增的整数列表。
    prior_outlines: 当前已生成的所有 outlines（用于续写参考 + 标题去重）。
    返回新生成的 outlines（不含 prior_outlines）。
    """
    new_outlines = []
    chunks = [indices[i:i + BATCH_SIZE] for i in range(0, len(indices), BATCH_SIZE)]
    for chunk in chunks:
        batch_start, batch_end = chunk[0], chunk[-1]
        stage_label = f"[{stage.name}]" if stage is not None else "[未分组]"
        print(f"    {stage_label} 第{batch_start}-{batch_end}章...")

        # 续写参考：取最近 3 条
        prev_context = ""
        all_so_far = prior_outlines + new_outlines
        if all_so_far:
            prev = all_so_far[-3:]
            prev_context = "前几章大纲（续写参考）：\n" + "\n".join(
                f"第{o.get('index','?')}章《{o.get('title','')}》：{(o.get('goal','') or '')[:40]}" for o in prev
            )

        # 标题去重：本卷已生成 + 前几卷
        prev_titles = [o.get("title", "") for o in all_so_far if o.get("title")]
        for prev_vol in state.volumes:
            if prev_vol.index >= volume_index:
                continue
            for o in (prev_vol.chapter_outlines or []):
                if o.get("title"):
                    prev_titles.append(o["title"])

        prompt = _build_outline_prompt(volume_index, vol, chunk, prev_context, prev_titles,
                                       chars_str, lines_str, stage=stage)
        outlines = _request_with_retry(prompt, expected_count=len(chunk), batch_start=batch_start)
        # 强制对齐 index 与 stage_id（防 LLM 漏填或乱填）
        for i, o in enumerate(outlines[:len(chunk)]):
            o["index"] = chunk[i]
            if stage is not None:
                o["stage_id"] = stage.stage_id
        new_outlines.extend(outlines[:len(chunk)])
    return new_outlines


def plan_volume_chapters(state: NovelState, volume_index: int) -> None:
    """
    为指定卷生成逐章大纲——按大情节（StoryStage）切批，每批内联合规划。
    每条 outline 带 stage_id，便于下游 stage-aware 调度。
    没有 stage 覆盖的章节走 fallback 批次。
    """
    vol = state.get_volume(volume_index)
    if not vol:
        return

    active_chars = state.active_characters_in_volume(volume_index)
    chars_str = "\n".join(c.brief() for c in active_chars[:6])  # 最多6个角色

    vol_lines = [
        ln for ln in state.all_lines
        if ln.get_phase_for_chapter(vol.chapter_start) or ln.get_phase_for_chapter(vol.chapter_end)
    ]
    lines_str = "\n".join(
        f"- [{ln.scope.value}/{ln.line_type.value}] {ln.name}：{ln.description[:50]}"
        for ln in vol_lines
    )

    stages = state.stages_in_volume(volume_index)
    all_outlines: list[dict] = []

    if not stages:
        # 没有大情节设计——退回旧的固定批次切法（无 stage_id）
        print(f"    ⚠ 第{volume_index}卷未设计任何 stage，按章节范围 fallback 批次规划")
        indices = list(range(vol.chapter_start, vol.chapter_end + 1))
        all_outlines.extend(
            _plan_outlines_for_indices(state, volume_index, vol, indices, all_outlines,
                                       chars_str, lines_str, stage=None)
        )
    else:
        # 按 stage 切批
        for st in stages:
            indices = list(range(st.chapter_start, st.chapter_end + 1))
            print(f"    Stage 批次：[{st.stage_id}] {st.name}（{st.structure_role or '?'}）")
            all_outlines.extend(
                _plan_outlines_for_indices(state, volume_index, vol, indices, all_outlines,
                                           chars_str, lines_str, stage=st)
            )
        # 处理 stage 未覆盖的章节 gap
        covered = set()
        for st in stages:
            for ci in range(st.chapter_start, st.chapter_end + 1):
                covered.add(ci)
        gap_indices = [ci for ci in range(vol.chapter_start, vol.chapter_end + 1) if ci not in covered]
        if gap_indices:
            print(f"    ⚠ {len(gap_indices)} 章未被任何 stage 覆盖，走 fallback 批次")
            # gap_indices 可能不连续——按连续段切片
            segs = []
            cur = [gap_indices[0]]
            for ci in gap_indices[1:]:
                if ci == cur[-1] + 1:
                    cur.append(ci)
                else:
                    segs.append(cur)
                    cur = [ci]
            segs.append(cur)
            for seg in segs:
                all_outlines.extend(
                    _plan_outlines_for_indices(state, volume_index, vol, seg, all_outlines,
                                               chars_str, lines_str, stage=None)
                )

    # 按 index 升序排（stage 顺序与章节顺序基本一致，但 fallback gap 段可能在末尾）
    all_outlines.sort(key=lambda o: o.get("index", 0))
    vol.chapter_outlines = all_outlines

    n_with_stage = sum(1 for o in all_outlines if o.get("stage_id"))
    print(f"  ✓ 第{volume_index}卷章节大纲：{len(all_outlines)} 章（其中 {n_with_stage} 章含 stage_id）")


def _request_with_retry(prompt: str, expected_count: int, batch_start: int, max_retries: int = 3) -> list[dict]:
    """带重试的JSON请求，失败时自动降级生成占位大纲。"""
    for attempt in range(1, max_retries + 1):
        try:
            raw = system_user(SYSTEM, prompt, temperature=0.65)
            data = repair_json(raw)
            outlines = data.get("chapter_outlines", [])
            if outlines:
                return outlines
        except (json.JSONDecodeError, KeyError) as e:
            print(f"    ⚠ 解析失败（尝试{attempt}/{max_retries}）：{e}")
            if attempt == max_retries:
                print(f"    ⚠ 已达最大重试次数，使用占位大纲")
                return _fallback_outlines(batch_start, expected_count)
    return _fallback_outlines(batch_start, expected_count)


def _fallback_outlines(start_index: int, count: int) -> list[dict]:
    """JSON解析彻底失败时的占位大纲，保证流程不中断。"""
    return [
        {
            "index": start_index + i,
            "title": f"第{start_index + i}章",
            "goal": "继续推进故事，按叙事线自然发展",
            "position": "普通",
        }
        for i in range(count)
    ]

