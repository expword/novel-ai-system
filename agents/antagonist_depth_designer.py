"""
反派深度设计 —— 在普通 character_designer 跑完后，对反派类角色补一轮"深度刻画"。

为什么需要单独的 agent：
  · character_designer 一视同仁地为所有角色设计 personality/motivation/arc
  · 但反派塑造有专门的网文套路："让读者觉得这反派可怕/可恨/可叹"
  · 单一 motivation 字段不足以让反派立得起来——需要：
      · belief_system  （反派的信仰，越坚定的反派越可怕）
      · despair_moments（反派给主角施加的"绝望时刻"——不是单次打斗，是让主角接受失败的事件）
      · charisma_signature（魅力点——让读者明知他坏却忍不住欣赏）
      · pov_insertion_volumes（哪几卷该切反派 POV 章）
      · inner_wound （反派自己也是受害者的过往，避免脸谱化）

输出：直接更新 state.characters 中所有 ANTAGONIST 类角色的对应字段。
"""
from __future__ import annotations
from typing import Optional

from persistence.state import NovelState


SYSTEM_TEMPLATE = """你是{genre}小说的反派塑造专家。以下网文金句你深以为然：
"反派立得起来，主角的胜利才有重量。"
"读者讨厌反派但记住反派——是反派塑造的最高境界。"

你的任务是为已设计好基础档案的反派角色补充五个**深度字段**，让他/她从"功能性反派"变成"读者一辈子记得的反派"。

---

## 五个字段的设计要求

### 一、belief_system（信仰系统，60-100 字）
反派**相信什么**——不是"我要权力"这种欲望，而是**他自己确信的真理**。
- 越是坚定 → 越可怕。读者会感到恐惧不是因为他强，而是因为他确信。
- 必须能用一两句话概括——他面对所有事情时的内心准则
- ✓ "弱肉强食是天理，怜悯只是弱者的自我欺骗"
- ✓ "秩序高于一切——一个无辜者死，能换百万人活，那就死"
- ✓ "我比那些喊着仁义道德的圣人活得真——我承认我贪婪"
- ✗ 太空泛："我想统治世界"
- ✗ 太外在："因为我童年被欺负所以恨富人"（这是 wound，不是 belief）

### 二、despair_moments（绝望时刻清单，2-4 条，每条 30-60 字）
反派给主角施加的**让主角接受失败**的具体场景设计——不是普通打架，是让主角心碎/无力/必须低头的事件。
**关键**：这些时刻必须是反派**主动设计**的，不是偶然。
- ✓ "在主角刚救出妻子时让她在主角面前自尽——因为她其实是反派的内应"
- ✓ "公开揭露主角师父的黑历史，让主角不得不亲手清理门户"
- ✓ "送主角一份大礼——10 年前那场屠村真凶的名单，要主角自己选'报仇'还是'放下'"
- ✗ 单纯打赢主角（这只是力量差距，不是"绝望")
- 至少 1 条应该是"无关力量、纯靠智慧/算计造成的绝望"

### 三、charisma_signature（魅力点，60-80 字）
读者明知他坏却**忍不住欣赏**的特质。这是把反派从"恶心"提到"可怕"的关键。
- ✓ "对失败者保持体面——挫败他的人他会亲自送一杯酒，再杀"
- ✓ "对智者的真心赞许——破他局的人他会笑着说'有意思'"
- ✓ "永远不动怒，越是局势危急越平静"
- ✓ "在私人场合温柔得像普通父亲，对自己孩子从无威严"
- ✗ "外貌很英俊"（这是设定，不是魅力）

### 四、pov_insertion_volumes（建议切反派 POV 的卷号清单）
出于"读者需要看到全局棋局"的考虑，建议在哪几卷穿插一两章反派视角。
- 一般 1-3 卷一次
- 关键节点：反派完成一次大布局后；反派与某重要配角谈判时；反派回忆自己创伤时
- 输出 list[int]，比如 [2, 5, 8]

### 五、inner_wound（内在创伤，40-60 字）
反派自己也是某种意义上的"受害者"——这一字段让他**立体**而非纸糊。
- ✓ "他出身世家嫡系，10 岁那年被指证偷窃，全族对他施'忘形礼'三日，那之后他不再相信血缘"
- ✓ "曾是少年神童，被国师选中带入宫中三年，回来时已不能再写诗——某些东西被夺走了"
- ✗ "童年被欺负"（太套路）
- ✗ 字段留空——所有反派都有过去

---

## 输出严格 JSON

输入会列出所有反派角色的基础档案。你为每个反派输出：

{{
  "characters": [
    {{
      "name": "反派姓名（必须是输入中的某个）",
      "belief_system": "...60-100 字...",
      "despair_moments": ["...30-60字...", "...", "..."],
      "charisma_signature": "...60-80字...",
      "pov_insertion_volumes": [2, 5, 8],
      "inner_wound": "...40-60字..."
    }}
  ]
}}

【硬约束】
- 只为输入列表里的反派设计——不要编新角色
- belief_system 必须是"他相信的真理"而不是"他想要的东西"
- 至少 2 条 despair_moments
- charisma_signature 必须是积极特质（让读者欣赏）的描述，不是"他会冷笑"这种行为描述
- inner_wound 不能空"""


def _list_villains(state: NovelState) -> list:
    """从 state.characters 找出所有反派角色。"""
    out = []
    for c in (state.characters or []):
        role = getattr(c, "role", None)
        rv = getattr(role, "value", str(role))
        if rv in ("反派", "antagonist", "ANTAGONIST"):
            out.append(c)
    return out


def design_antagonist_depth(state: NovelState, *, max_retries: int = 2) -> dict:
    """为所有反派补深度字段。返回 {"updated": N, "errors": [...]}"""
    villains = _list_villains(state)
    if not villains:
        return {"updated": 0, "errors": ["no villains found"]}

    from utils.json_utils import request_json

    # 构造输入
    villain_brief = []
    for v in villains:
        sheet = []
        sheet.append(f"姓名：{v.name}")
        sheet.append(f"性格：{getattr(v, 'personality', '')}/{getattr(v, 'personality_detail', '')[:80]}")
        if getattr(v, "background", ""):
            sheet.append(f"背景：{v.background[:120]}")
        if getattr(v, "motivation", ""):
            sheet.append(f"动机：{v.motivation[:80]}")
        if getattr(v, "trauma", ""):
            sheet.append(f"创伤：{v.trauma[:80]}")
        if getattr(v, "fatal_flaw", ""):
            sheet.append(f"致命弱点：{v.fatal_flaw[:60]}")
        villain_brief.append("\n".join(sheet))

    # 全书基调 + 主角简介（让反派和主角形成对照）
    ci = getattr(state, "creative_intent", None)
    tone = getattr(ci, "tone_summary", "") if ci else ""

    prot = next((c for c in (state.characters or [])
                 if getattr(getattr(c, "role", None), "value", "") == "主角"), None)
    prot_brief = ""
    if prot:
        prot_brief = (
            f"主角：{prot.name} | "
            f"性格：{getattr(prot, 'personality', '')} | "
            f"动机：{getattr(prot, 'motivation', '')[:60]}"
        )

    user = (
        f"本书基调：{tone[:200]}\n"
        f"{prot_brief}\n\n"
        f"以下是 {len(villains)} 个反派的基础档案。请为每个补充 5 个深度字段。\n\n"
        + "\n\n---\n\n".join(villain_brief)
    )

    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "")
    try:
        data = request_json(
            system=system, user=user,
            required_keys=["characters"],
            list_candidates=["characters"],
            min_items=1,
            max_retries=max_retries,
            temperature=0.85,
            agent_name="AntagonistDepthDesigner",
            empty_ok=True,
        )
    except Exception as e:
        return {"updated": 0, "errors": [f"{type(e).__name__}: {e}"]}

    if not data:
        return {"updated": 0, "errors": ["LLM 无返回"]}

    updated = 0
    errors = []
    for entry in (data.get("characters") or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "").strip()
        if not name:
            continue
        v = next((c for c in villains if c.name == name), None)
        if not v:
            errors.append(f"反派 {name!r} 不在列表里")
            continue
        # 更新字段
        bs = entry.get("belief_system", "")
        if bs:
            v.belief_system = str(bs)[:200]
        dms = entry.get("despair_moments", [])
        if isinstance(dms, list):
            v.despair_moments = [str(x)[:120] for x in dms if isinstance(x, str) and x.strip()][:6]
        cs = entry.get("charisma_signature", "")
        if cs:
            v.charisma_signature = str(cs)[:160]
        povs = entry.get("pov_insertion_volumes", [])
        if isinstance(povs, list):
            try:
                v.pov_insertion_volumes = [int(p) for p in povs if isinstance(p, (int, str))][:6]
            except (TypeError, ValueError):
                pass
        iw = entry.get("inner_wound", "")
        if iw:
            v.inner_wound = str(iw)[:120]
        updated += 1

    return {"updated": updated, "total_villains": len(villains), "errors": errors}
