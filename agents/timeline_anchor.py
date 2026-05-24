"""
TimelineAnchorAgent — Phase 1-G：世界历史时间轴。

为什么独立拎出来：没有一张世界历史表，ForeshadowManager 就不知道有什么古老事件可以拿来当伏笔。
"三千年前的神魔大战""一百年前的门派分裂"这些都是伏笔的温床——写出来、存起来、
后面的 agent 才能自然引用。

产出 state.timeline：6-12 个历史事件，从上古到当代，标注距今年数、涉及势力、可作为伏笔的角度。
"""
from utils.json_utils import request_json, pick_list
from persistence.state import NovelState, Timeline, TimelineEvent
from agents.concept_pitch import format_concept_brief, format_world_context_brief


SYSTEM = """你是写给各类小说的"历史/往事"设计师——给这个故事的时间背景建一份年表作为伏笔温床。

【核心原则】
1. 历史事件形态完全看题材：
   · 修真/玄幻：神魔大战/创世/古神陨落/宗门分裂/灵气衰竭/飞升门
   · 武侠：武林大会/魔教入侵/江山易主/剑圣殒落
   · 都市/现代：真实历史事件（改革开放/互联网浪潮/金融危机/疫情）+ 行业或家族的关键转折
   · 古代：朝代更替/宫廷政变/名将之死
   · 末世：灾变 Day0/第一避难所/第一次大型变异/变种起源
   · 星际/科幻：大航海时代/外星接触/星际战争/母星毁灭
   · 克苏鲁：古神觉醒事件/非凡协会成立/某次重大非凡事件
   · 校园/言情：主角家族关键转折、校园历史名人、某段未解情缘
2. 每个事件必须有：时间锚点 / 内容 / 对当前的影响 / 伏笔角度
3. 精炼：6-12 个足够，不要堆砌
4. 言情/极简故事的"历史"可以只是主角的家族往事或童年记忆，不必架空大世界史
5. 现代/都市题材的历史可以用真实年份（如"2008 年金融危机"），不强制编造架空纪元

输出严格 JSON。"""


def design_timeline(state: NovelState) -> None:
    concept = format_concept_brief(state)
    factions_brief = "\n".join(
        f"- {f.name}（{f.faction_type}，{f.tier_name()}）"
        for f in state.factions[:8]
    )
    world_secrets = [f for f in state.memory.facts if "世界秘密" in f][:5]

    world_ctx = format_world_context_brief(state)
    # Phase 2.1:thread-local user_feedback 注入
    from utils.feedback_helper import get_user_feedback_prefix
    feedback_prefix = get_user_feedback_prefix()
    prompt = f"""{feedback_prefix}
为《{state.title}》设计历史/往事时间轴。

{world_ctx}

{concept}

世界观摘要：{state.world_setting[:200]}

主要势力：
{factions_brief}

已知的世界秘密（时间线事件可以作为这些秘密的背景基础）：
{chr(10).join(world_secrets) if world_secrets else '（暂无）'}

═══ 要求 ═══
1. **current_era**：当前剧情所处的纪元/时代描述——按题材选：
   · 架空：自创纪元名（如"大夏王朝"、"新纪元 12 年"）
   · 现代/都市：用真实时间（如"2024 年当代"）
   · 末世：灾变后时间（如"灾变后第 15 年"）
   · 星际：宇宙纪年（如"星联历 2850 年"）
2. **current_year_desc**：当前年份的具体描述
3. **events**（6-12 个）——时间跨度和 era 分层按题材：
   · 架空玄幻：上古(3000+年前)/中古(500-3000)/近代(50-500)/当代(0-50)
   · 现代都市：历史背景（如 2008 年前）/改革时代（00-10 年代）/最近（近 10 年）
   · 末世：灾变前/Day 0/第一年/近几年
   · 星际：母星时代/大航海时代/接触时代/当下
   · 言情/校园：主角的童年/青春/现在
   era 字段是题材合适的分代名，不必硬套"上古/中古"。
4. 每个事件要说：内容、对当前的影响、可作为伏笔的角度
5. 言情/极简故事可以只给 3-5 个主角个人往事，不必编世界史

输出 JSON：
{{
  "current_era": "（纪元名，按题材选）",
  "current_year_desc": "（当前年份描述）",
  "events": [
    {{
      "event_id": "ev_01",
      "era": "（本事件所属时代分类，按题材自定）",
      "years_ago": 距今年数（整数；现代用真实年龄，如 76 表示 1948 年）,
      "name": "事件名",
      "description": "事件描述（60字）",
      "consequences": "对当前世界的影响（40字）",
      "related_factions": ["相关势力"],
      "foreshadow_potential": "可作为伏笔的角度（30字）"
    }}
  ]
}}
"""
    example = (
        '{"current_era":"...","current_year_desc":"...",'
        '"events":[{"event_id":"ev_01","era":"...","years_ago":100,"name":"...",'
        '"description":"...","consequences":"...","related_factions":[],"foreshadow_potential":"..."}]}'
    )
    data = request_json(
        system=SYSTEM, user=prompt,
        list_candidates=["events", "items"],
        min_items=4,
        max_retries=4, temperature=0.72, agent_name="TimelineAnchor",
        example_schema=example,
        empty_ok=True,
    )
    if not data:
        print("  ⚠ TimelineAnchor 跳过（LLM 重试失败）——本书将缺少历史纵深")
        return

    tl = Timeline(
        events=[TimelineEvent(
            event_id=e.get("event_id", f"ev_{i+1:02d}"),
            era=e.get("era", "近代"),
            years_ago=int(e.get("years_ago", 0)),
            name=e.get("name", "（未命名事件）"),
            description=e.get("description", ""),
            consequences=e.get("consequences", ""),
            related_factions=e.get("related_factions", []),
            foreshadow_potential=e.get("foreshadow_potential", ""),
        ) for i, e in enumerate(pick_list(data, "events", "items"))],
        current_era=data.get("current_era", ""),
        current_year_desc=data.get("current_year_desc", ""),
    )
    state.timeline = tl

    # 时间线中的"可作为伏笔的角度"存入 memory.facts，供 ForeshadowManager 参考
    for e in tl.events:
        if e.foreshadow_potential:
            state.memory.facts.append(f"[历史伏笔·{e.name}] {e.foreshadow_potential}")

    print(f"  ✓ 时间线：{len(tl.events)} 个历史事件（{tl.current_era}·{tl.current_year_desc}）")
    for e in tl.events_sorted()[:6]:
        print(f"    [{e.years_ago}年前·{e.era}]《{e.name}》：{e.description[:40]}")
