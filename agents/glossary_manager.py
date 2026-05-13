"""
GlossaryManager —— 术语表管理。

每章写完后提取新专有名词（地名/人名/功法/法器/组织/阵法/境界）加入术语表。
目的：防止后续章节同一事物换名字（"紫霄峰"/"紫霄山"/"紫霄顶"的事故）。

读阶段：Writer 上下文里提供已有术语，避免重新生成。
写阶段：Memory 之后扫一遍，新词入库，旧词别名合并。
"""
from __future__ import annotations
from json_utils import request_json, pick_list
from state import NovelState, GlossaryEntry


SYSTEM = """你是小说术语登记员。
你看一段章节正文，找出其中"明确的专有名词"——按本书题材判断该收什么：
  · 修真/玄幻：地名/人名/功法/法器/阵法/境界/灵物/宗门
  · 武侠：地名/人名/武功/兵器/门派/江湖称号
  · 都市/职场：地名/人名/公司/职位/行业术语/产品/项目代号
  · 校园：地名/人名/学校/班级/社团/比赛名/老师称呼
  · 末世：地名/人名/避难所/异能/物资/势力/变种生物
  · 星际：地名/星球/人名/飞船/科技/星际组织/种族
  · 古代/宫斗：地名/人名/官位/府邸/礼制/宫殿
  · 言情：地名/人名/工作单位/重要场所
  · 系统流：系统名/任务/积分/技能/装备
  · 克苏鲁：地名/人名/序列/魔药/非凡组织/古神

只登记明确的专有名词。普通描述性词不登记（如"一把剑""一个人"）。
对每个专有名词判断它是否是已知的（给定列表里有）——如果是已知的同义别名，填进 aliases；否则记为新词。
category 字段按本书题材自由命名，不强制使用某一固定列表。
输出严格 JSON。"""


# 通用类别（题材中性的兜底；具体由 LLM 按本书题材选择）
CATEGORIES = ["地名", "人名", "组织/势力", "技能/能力", "物品/装备", "称号/身份", "事件", "其他"]


def update_glossary(state: NovelState, chapter_index: int, content: str) -> int:
    """
    从一章正文里提取专有名词更新 glossary。
    返回新加入的词数。
    """
    # 准备已知词列表（规范名 + 别名全部）
    known = []
    for g in state.glossary:
        known.append(g.term)
        known.extend(g.aliases)
    known_str = "、".join(known[-60:]) if known else "（暂无已登记词）"
    # 正文缩短（取 2500 字关键片段）
    if len(content) > 2500:
        content_sample = content[:1500] + "\n[...]\n" + content[-1000:]
    else:
        content_sample = content

    prompt = f"""分析第 {chapter_index} 章，提取专有名词。

【已登记的专有名词（部分）】
{known_str}

【正文节选】
{content_sample}

═══ 要求 ═══
1. 找出本章出现的所有专有名词
2. 对每个词分类（{' / '.join(CATEGORIES)}）
3. 给出 40 字以内的定义（从正文上下文推断；无法推断就写"未详"）
4. 判断是已知词的别名，还是全新词
   - 如果是已知词的别名（如"紫霄山"是已登记"紫霄峰"的别名），放进 aliases_of
   - 如果是新词，放进 new_entries

输出 JSON：
{{
  "new_entries": [
    {{"term": "...", "category": "地名", "definition": "..."}}
  ],
  "alias_merges": [
    {{"canonical_term": "已登记的规范名", "alias": "本章用的别名"}}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        max_retries=2, temperature=0.3,
        agent_name=f"Glossary[Ch{chapter_index}]",
        empty_ok=True,
    )
    if not data:
        return 0

    added = 0
    # 1. 新词入库
    existing_terms = {g.term for g in state.glossary}
    for e in data.get("new_entries", []):
        term = (e.get("term") or "").strip()
        if not term or term in existing_terms:
            continue
        state.glossary.append(GlossaryEntry(
            term=term,
            category=e.get("category", "其他"),
            definition=e.get("definition", ""),
            first_appeared_chapter=chapter_index,
            aliases=[],
        ))
        existing_terms.add(term)
        added += 1

    # 2. 别名合并
    merged = 0
    for m in data.get("alias_merges", []):
        canon = (m.get("canonical_term") or "").strip()
        alias = (m.get("alias") or "").strip()
        if not canon or not alias:
            continue
        g = state.get_glossary_term(canon)
        if g and alias not in g.aliases and alias != g.term:
            g.aliases.append(alias)
            merged += 1

    if added or merged:
        print(f"  ✓ 术语表：新增 {added} 个｜合并别名 {merged} 条｜共 {len(state.glossary)} 条")
    return added


def format_glossary_for_writer(state: NovelState, max_items: int = 15) -> str:
    """Writer 上下文注入用——只给最近 max_items 个术语，避免爆预算。"""
    return state.glossary_brief(max_items=max_items)
