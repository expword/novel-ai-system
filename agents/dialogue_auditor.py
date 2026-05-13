"""
对话质量章后审计 —— 网文里"角色能不能立住"70% 看对话。

跟 voice_consistency_checker 的区别：
  · voice_consistency_checker：只看"单个角色说的话像不像他自己"
  · dialogue_auditor：看整章对话的综合质量——
      - 潜台词密度（层次 vs 直说）
      - 角色间的声音差异化（全一个调调 vs 各有特色）
      - 对话穿插动作/表情/沉默（纯乒乓 vs 带血肉）
      - 情感节拍（吵架→缓和→爆发 vs 平铺直叙）
      - 称谓/身份用词准确
      - 说教程度（作者借角色嘴解释 vs 角色真在说话）
      - 对话目的性（每轮推进情节/关系 vs 凑字数）

产出：评分 + 具体问题条（含定位、涉及角色、问题片段、建议）
"""
from __future__ import annotations
from typing import Optional

from persistence.state import NovelState, DialogueAudit, DialogueIssue


SYSTEM_TEMPLATE = """你是资深{genre}网文编辑，专审"这一章的对话够不够好"。

**你跟"角色口吻检查员"的分工**：
- 口吻检查员：只看"张三说这句话像不像张三"（单角色维度）
- **你**：看整章所有对话的综合质量——不是单人一致性，而是整章对话作为"戏剧结构"的质量

---

## 审计维度（各 1-10 分）

### 一、subtext_density（潜台词密度）
- **满分 10**：有重要戏剧情境的对话至少 50% 不是字面意思——角色在试探、暗讽、欲言又止、话外有音
- **6-7**：大部分关键对话有一点潜台词，但还有不少直说的地方
- **1-3**：全是直白的"你好""我很生气""我不会让你得逞"式直球对话
- **典型扣分**：
  - 反派直接宣告计划："我要灭了你师门！" → -3（应该用笑、或眼神、或暗示）
  - 情侣吵架直接说："你就是不爱我了对不对" → -2（应该绕弯子说、话说一半）
  - 主角内心想的事角色张嘴就说出来 → -2

### 二、voice_distinctiveness（角色间差异化）
- **满分 10**：闭着眼读，能从用词/句式/口癖/节奏判断是谁在说话
- **扣分**：
  - 两个角色说话风格一模一样（都是同样的句长、同样的词汇） → -3
  - 配角"模板化"（反派必阴阳怪气、师父必深沉）→ -2
  - 角色身份差（农民 vs 王爷）但说话没层次差异 → -2

### 三、action_beats_integration（动作/表情/沉默穿插）
- **满分 10**：每轮对话之间有动作、微表情、环境反应、沉默——不是纯乒乓球
- **扣分**：
  - 连续 5+ 轮 "A 说...B 说...A 说..." 没有任何穿插 → -3
  - 人物说话时完全没有"此刻他在做什么、看什么、身体怎样"的注解 → -2

### 四、emotional_pacing（情感节拍）
- **满分 10**：对话里有情感曲线——铺垫、小紧张、缓和、爆发、余波，不是情绪平行线
- **扣分**：
  - 一场对峙从头到尾一个情绪，没有起伏 → -3
  - 吵架突然转和解没有过渡 → -2
  - 情感高点没有铺垫就爆发（读者没被带进节奏）→ -2

### 五、address_accuracy（称谓/身份用词）
- **满分 10**：上下位、亲疏远近、场合正式度的称谓和用词完全到位
- **扣分**：
  - 小厮对王爷直呼其名（无合理理由）→ -3
  - 古代人物突然冒现代词（"搞定""感觉"）→ -2（除非是穿越文的主角）
  - 同一场合不同角色对同一人的称呼不符合他们各自的关系 → -1

### 六、infodump_level（信息灌输程度，10=没有说教）
- **满分 10**：读者通过角色的互动**感受到**世界观/关系/设定，不是被告知
- **扣分**：
  - 角色 A 大段向 B 讲解 A 早就应该知道的事 → -4（典型说教）
  - 主角内心独白连续几段"这个世界是这样的..."→ -3
  - 反派说出大段自曝动机 → -2

### 七、dialogue_purpose（对话目的性）
- **满分 10**：每轮对话都推进了情节/关系/信息——没有"为聊天而聊天"
- **扣分**：
  - 大段对话后读者不知道发生了什么变化 → -3
  - 礼节性寒暄占超过 10% 篇幅 → -2
  - 角色重复说同一件事（上一轮已表态，下一轮又说一遍）→ -1

---

## 统计字段
- **total_dialogue_count**：本章对话的**行数**（估算，每个引号对算一行）
- **speaking_characters**：本章开口说话的角色名列表
- **dialogue_ratio_percent**：对话字数占整章字数的百分比（估算 0-100）

## 问题清单格式
每个 issue 必须包含：
- type: one of `on_the_nose`(直白缺潜台词) | `infodump_speech`(说教) | `voice_mismatch`(角色声错位) | `wrong_address`(称谓不对) | `tone_flat`(情绪平铺) | `emotional_beat_missing`(节拍缺失) | `repetitive`(重复) | `too_explicit`(过度明说) | `pacing_broken`(节奏断裂) | `other`
- severity: critical|major|minor
- location: 定位（"第二幕中段主角与师父对话" 式，30 字内）
- excerpt: 有问题的对话**原文片段**（30-80 字，让作者能定位）
- character: 涉及的角色（如适用）
- description: 具体问题（60字）
- suggested_fix: 怎么改（40字）

---

## 输出严格 JSON

{{
  "total_dialogue_count": 12,
  "speaking_characters": ["主角名", "角色B", ...],
  "dialogue_ratio_percent": 35,
  "subtext_density": 7,
  "voice_distinctiveness": 6,
  "action_beats_integration": 8,
  "emotional_pacing": 7,
  "address_accuracy": 9,
  "infodump_level": 7,
  "dialogue_purpose": 8,
  "overall_score": 7,
  "summary": "整体对话合格，但个别场景潜台词不足（40字内）",
  "issues": [
    {{"type":"on_the_nose", "severity":"major",
      "location":"第二幕主角与反派对峙", "excerpt":"「你别得意，我会让你付出代价」",
      "character":"反派头目", "description":"反派直接威胁，失了分寸和威严",
      "suggested_fix":"改成笑/沉默/意味深长的半句"}}
  ]
}}

【硬要求】
- 如果本章几乎没对话（<5 句），total_dialogue_count=小数字，大部分评分用默认 8，issues=[]，summary="本章对话很少"
- issues 只列"确实扣分"的，不强凑
- excerpt 必须是**原章里真实出现的对话片段**，不要自己编"""


def _format_character_voices(state: NovelState, chapter_text: str) -> str:
    """
    给 auditor 提供本章涉及角色的"声音档案"。
    让它能判断"张三说这句像不像张三"。
    """
    # 从角色列表里挑出名字可能出现在本章的
    chars = state.characters or []
    lines = []
    for c in chars[:15]:  # 上限 15 个
        if not c.name or c.name not in chapter_text:
            continue
        parts = [f"- {c.name}"]
        if getattr(c, "role", None):
            role = getattr(c.role, "value", str(c.role))
            parts.append(f"[{role}]")
        if getattr(c, "speech_pattern", ""):
            parts.append(f"说话：{c.speech_pattern[:60]}")
        tics = getattr(c, "verbal_tics", None) or []
        if tics:
            parts.append(f"口癖：{'/'.join(tics[:3])}")
        sig = getattr(c, "signature_mannerisms", None) or []
        if sig:
            parts.append(f"小动作：{'/'.join(sig[:3])[:60]}")
        lines.append(" ".join(parts))
    if not lines:
        return ""
    return "【本章涉及角色的声音档案（auditor 用来核对声音差异）】\n" + "\n".join(lines)


def audit_chapter(
    state: NovelState,
    chapter_index: int,
    chapter_text: str,
    *,
    max_retries: int = 2,
) -> Optional[DialogueAudit]:
    """对一章做对话质量审计。失败返回 None。"""
    from utils.json_utils import run_chapter_audit

    voice_ctx = _format_character_voices(state, chapter_text)
    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "网文")
    user = (
        f"═══ 审计目标：第 {chapter_index} 章 对话质量 ═══\n\n"
        f"{voice_ctx}\n\n"
        f"═══ 本章正文 ═══\n{chapter_text}\n\n"
        f"按 SYSTEM 的 JSON schema 输出。"
    )

    result = run_chapter_audit(
        chapter_index=chapter_index,
        chapter_text=chapter_text,
        system=system, user=user,
        required_keys=[
            "subtext_density", "voice_distinctiveness", "action_beats_integration",
            "emotional_pacing", "address_accuracy", "infodump_level", "dialogue_purpose",
            "overall_score", "summary",
        ],
        agent_label="DialogueAuditor",
        temperature=0.35,
        max_retries=max_retries,
    )
    if result is None:
        return None
    data, ts, profile_id = result

    def _clamp(v, lo=1, hi=10, default=7):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    issues = []
    for i in (data.get("issues") or []):
        if not isinstance(i, dict):
            continue
        issues.append(DialogueIssue(
            type=str(i.get("type", "other"))[:30],
            severity=str(i.get("severity", "minor"))[:16],
            location=str(i.get("location", ""))[:100],
            excerpt=str(i.get("excerpt", ""))[:200],
            character=str(i.get("character", ""))[:40],
            description=str(i.get("description", ""))[:200],
            suggested_fix=str(i.get("suggested_fix", ""))[:150],
        ))

    chars_raw = data.get("speaking_characters") or []
    chars = [str(c)[:40] for c in chars_raw if isinstance(c, str) and c.strip()][:15]

    try:
        total_dialog = int(data.get("total_dialogue_count", 0))
    except (TypeError, ValueError):
        total_dialog = 0
    try:
        ratio = int(data.get("dialogue_ratio_percent", 0))
    except (TypeError, ValueError):
        ratio = 0
    ratio = max(0, min(100, ratio))

    return DialogueAudit(
        chapter_index=chapter_index,
        total_dialogue_count=total_dialog,
        speaking_characters=chars,
        dialogue_ratio_percent=ratio,
        subtext_density=_clamp(data.get("subtext_density")),
        voice_distinctiveness=_clamp(data.get("voice_distinctiveness")),
        action_beats_integration=_clamp(data.get("action_beats_integration")),
        emotional_pacing=_clamp(data.get("emotional_pacing")),
        address_accuracy=_clamp(data.get("address_accuracy"), default=8),
        infodump_level=_clamp(data.get("infodump_level"), default=8),
        dialogue_purpose=_clamp(data.get("dialogue_purpose")),
        issues=issues,
        overall_score=_clamp(data.get("overall_score")),
        summary=str(data.get("summary", ""))[:150],
        ts=ts,
        auditor_model=profile_id,
    )
