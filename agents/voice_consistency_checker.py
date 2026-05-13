"""
VoiceConsistencyCheckerAgent — Phase 5：角色口吻一致性校验。

专门对本章所有对话和心理描写做检查，对照每个角色的 VoiceProfile：
- high_freq_vocab / verbal_tics（该说的词有没有）
- speech_taboo（不该说的话有没有）
- sentence_length_preference（句式偏好）
- speech_under_anger/fear/joy（情绪下的语言变化）

低于阈值的段落标出来，写入 issues 列表，供 director 决定是否 revise。
"""
from json_utils import request_json, pick_list
from state import NovelState, ChapterDirective, CharacterRole


SYSTEM = """你是角色对话一致性校审员。你专挑"这个角色说的话不像他"的问题。
每个角色有自己的语言指纹（高频词/口癖/禁区/情绪语言/句式偏好）。
你的工作：对照语言指纹，找出本章正文里对话和心理描写中不像他的地方。
- 他本该爱用某些词，但整章一次没出现——扣分
- 他本该不说粗口/不说文言，但说了——扣分
- 情绪下的语言变化没有体现（怒时没变短/惧时没沉默/喜时没反差）——扣分
- 多个角色说话像同一个人（没个性）——扣分

严格实事求是。找不到问题就说"无问题"。
输出严格 JSON。"""


def check_voice_consistency(state: NovelState, directive: ChapterDirective, content: str) -> dict:
    """对本章做角色口吻校验。"""
    # 只检查核心角色——次要配角的语言指纹通常不完整
    core_chars = [
        c for c in state.characters
        if c.role in (CharacterRole.PROTAGONIST, CharacterRole.MAJOR, CharacterRole.ANTAGONIST)
        and (c.high_freq_vocab or c.speech_taboo or c.verbal_tics)
    ]
    if not core_chars:
        return {"has_issues": False, "severity": "none", "issues": []}

    # 只挑本章可能出场的角色（通过蓝图中 scene_beats.characters）
    in_scene_names = set()
    if directive.blueprint:
        for beat in directive.blueprint.scene_beats:
            in_scene_names.update(beat.characters)
    # 如果蓝图没指定，默认只查主角
    if not in_scene_names:
        in_scene_names = {c.name for c in core_chars if c.role == CharacterRole.PROTAGONIST}

    relevant_chars = [c for c in core_chars if c.name in in_scene_names]
    if not relevant_chars:
        return {"has_issues": False, "severity": "none", "issues": []}

    # 构造语言指纹摘要
    profiles_block = _format_voice_profiles(relevant_chars)

    # 正文——对话/心理描写占比较大，取开头+中段+结尾
    if len(content) > 4000:
        third = len(content) // 3
        content_sample = content[:1500] + "\n[...]\n" + content[third:third+1000] + "\n[...]\n" + content[-1500:]
    else:
        content_sample = content

    prompt = f"""口吻一致性校验：第 {directive.chapter_index} 章。

【本章出场的核心角色语言指纹】
{profiles_block}

【本章正文节选】
{content_sample}

═══ 审查要求 ═══
对每个角色，检查他在本章的对话和内心独白是否符合自己的语言指纹：
- 高频词/口癖 本该出现但没出现 → 扣分
- 语言禁区 居然说了 → 严重扣分
- 情绪下的语言变化 没有体现 → 扣分
- 与其他角色说话分不出区别 → 扣分

针对每个问题角色，给出：character_name / score(1-10) / issues 具体问题 / examples 正文中的跳戏例子

输出 JSON：
{{
  "has_issues": true 或 false,
  "severity": "none" | "minor" | "major" | "critical",
  "char_scores": [
    {{
      "character_name": "...",
      "score": 1到10,
      "issues": ["具体问题1"],
      "examples": ["正文中的跳戏句子"]
    }}
  ]
}}
"""
    data = request_json(
        system=SYSTEM, user=prompt,
        required_keys=["has_issues"],
        max_retries=3, temperature=0.3,
        agent_name=f"VoiceChecker[Ch{directive.chapter_index}]",
        empty_ok=True,
    )
    if not data:
        return {"has_issues": False, "severity": "none", "char_scores": []}
    # 把 char_scores 转成 issues 格式，方便 director 统一处理
    issues = []
    for cs in data.get("char_scores", []):
        if cs.get("score", 10) < 7:
            issues.append({
                "character": cs.get("character_name", ""),
                "score": cs.get("score", 0),
                "problems": cs.get("issues", []),
                "examples": cs.get("examples", []),
            })
    data["issues"] = issues
    return data


def _format_voice_profiles(chars) -> str:
    lines = []
    for c in chars[:6]:
        parts = [f"【{c.name}（{c.role.value}）】"]
        if c.high_freq_vocab:
            parts.append(f"高频词：{' / '.join(c.high_freq_vocab[:4])}")
        if c.verbal_tics:
            parts.append(f"口癖：{' / '.join(c.verbal_tics[:3])}")
        if c.speech_taboo:
            parts.append(f"语言禁区：{' / '.join(c.speech_taboo[:3])}")
        if c.sentence_length_preference:
            parts.append(f"句式：{c.sentence_length_preference}")
        emo = []
        if c.speech_under_anger:
            emo.append(f"怒:{c.speech_under_anger}")
        if c.speech_under_fear:
            emo.append(f"惧:{c.speech_under_fear}")
        if c.speech_under_joy:
            emo.append(f"喜:{c.speech_under_joy}")
        if emo:
            parts.append("情绪语言：" + " ｜ ".join(emo))
        lines.append(" ｜ ".join(parts))
    return "\n".join(lines)
