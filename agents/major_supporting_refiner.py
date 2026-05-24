"""
MajorSupportingRefinerAgent — 一人一次 LLM 调用，为主角和主要配角补上细腻刻画层。

为什么单独一个 agent：
- 基础 character_designer 要兼顾全员，给不了每个人足够的"镜头时间"
- 主要配角在小说里戏份重，必须像主角一样立体
- 一人一次 LLM 让每次生成都聚焦于"这一个人"——不会被别的角色稀释
- 生成的字段供 writer/chapter_planner 在写作时调用（signature_mannerisms/verbal_tics/...）

处理对象：主角（1人） + 所有主要配角（2-4人）+ 所有反派（2-4人）= 约 5-9 人
每人一次 LLM 请求。
"""
from utils.json_utils import request_json
from persistence.state import NovelState, Character, CharacterRole
from utils.concurrency import parallel_map
from config import PARALLEL_WORKERS
from typing import Optional, Tuple


SYSTEM = """你是人物雕刻师——把人物从"档案"写成"活生生的人"。

你现在只关注一个人。给这个人找几样东西：
1. 两三个习惯性小动作——他紧张/放松/撒谎/得意时身体会做什么？这种动作读者一看就知道是他。
2. 一两个说话习惯——他爱用什么词？句式？总是先否定再肯定？总以反问收尾？
3. 一个感官标记——他身上有什么读者能"闻到/听到/看到"的标志？（气味/声线/身形/眼神的特殊）
4. 压力下的第一反应——真正的压力来时，他本能做什么？（沉默？冷笑？破罐破摔？过度解释？）
5. 一段定义他的记忆——过去某件事塑造了他现在的样子，40字内说清。
6. 一个从不承认的渴望——他心底想要但从不说出口的东西。
7. （仅配角）他的世界观/做事方式与主角形成什么对比或张力？（让他们相处时产生戏剧性）

还有一份"语言指纹"：
8. 高频词汇——他口语中爱反复出现的 3-5 个词或短语
9. 语言禁区——这个人绝对不会说的话类型（比如读书人不会说粗口，武夫不会用文言，某类角色不自谦）
10. 情绪下的语言变化——他愤怒时、恐惧时、喜悦时分别会怎么说话？（语速/句式/选词的变化）
11. 句式偏好——长句还是短句？交织？排比？

这些细节不是标签，要具体、要特异。避免抽象词（"他很温柔"没用——什么时候温柔，怎么表现温柔？）。
写得像你真的认识这个人。

输出严格 JSON。"""


def refine_major_characters(state: NovelState) -> None:
    """
    为主角 + 主要配角 + 反派逐一调用 LLM，填充细腻刻画字段。
    次要配角/卷内角色跳过（他们戏份不值得这个成本）。
    """
    # 筛选对象：主角 + 主要配角 + 反派
    targets = [
        c for c in state.characters
        if c.role in (CharacterRole.PROTAGONIST, CharacterRole.MAJOR, CharacterRole.ANTAGONIST)
    ]
    if not targets:
        print("  ⚠ 没有需要深化的角色（主角/主要配角/反派）")
        return

    # 主角是参照系——其他人的 contrast_with_protagonist 字段需要主角的档案
    protagonist = next((c for c in state.characters if c.role == CharacterRole.PROTAGONIST), None)
    prot_sketch = ""
    if protagonist:
        prot_sketch = (
            f"【主角参照】{protagonist.name}：{protagonist.personality[:30]}"
            f"｜动机：{protagonist.motivation[:40]}"
            f"｜致命弱点：{protagonist.fatal_flaw[:25]}"
        )

    # 过滤已深化过的（有 signature_mannerisms 就说明做过）
    pending = [c for c in targets if not c.signature_mannerisms]
    skipped = len(targets) - len(pending)
    if skipped:
        print(f"  跳过已深化的 {skipped} 人")
    if not pending:
        return

    print(f"  为 {len(pending)} 个核心角色并发深化（每人一次 LLM 调用）...")

    def refine_one(char: Character) -> Optional[Tuple[Character, dict]]:
        """单个角色的 LLM 调用——返回 (char, data) 或 None。线程安全：只读 state，不写。"""
        is_protagonist = (char.role == CharacterRole.PROTAGONIST)
        char_sheet = (
            f"姓名：{char.name}\n"
            f"角色定位：{char.role.value}\n"
            f"性别/年龄：{char.gender}/{char.age_desc}\n"
            f"外貌：{char.appearance}\n"
            f"性格：{char.personality_detail}\n"
            f"背景：{char.background}\n"
            f"创伤：{char.trauma}\n"
            f"渴望：{char.desire}\n"
            f"恐惧：{char.fear}\n"
            f"说话风格（基础）：{char.speech_pattern}\n"
            f"动机：{char.motivation}\n"
            f"致命弱点：{char.fatal_flaw}\n"
            f"整体弧线：{char.arc}"
        )
        contrast_block = ""
        if not is_protagonist and prot_sketch:
            contrast_block = f"\n{prot_sketch}\n请给出此人与主角的世界观/做事方式的对比张力。"
        elif is_protagonist:
            contrast_block = "\n这是主角本人，contrast_with_protagonist 填 '—'。"

        # Phase 2.2:thread-local user_feedback 注入
        from utils.feedback_helper import get_user_feedback_prefix
        feedback_prefix = get_user_feedback_prefix()
        prompt = f"""{feedback_prefix}为以下人物补上细腻刻画的几样东西。

【已有档案】
{char_sheet}
{contrast_block}

请给：
- signature_mannerisms：2-3 个习惯性小动作（具体可视，非抽象）
- verbal_tics：1-3 条说话习惯（词/句式/口癖）
- sensory_signature：一个感官标记（气味/声线/身形/眼神等，30字）
- default_stress_response：压力下的第一反应（20字）
- defining_memory：塑造其人的一段关键记忆（40字）
- secret_desire：从不承认的渴望（30字）
- contrast_with_protagonist：与主角的对比/张力（30字；主角本人填"—"）
- 【语言指纹】
  - high_freq_vocab：高频词汇 3-5 个
  - speech_taboo：绝不会说的话类型 2-4 条
  - speech_under_anger/fear/joy：三种情绪下的语言变化（各 20 字）
  - sentence_length_preference：句式偏好

输出 JSON：
{{
  "signature_mannerisms": ["动作1", "动作2"],
  "verbal_tics": ["习惯1"],
  "sensory_signature": "...",
  "default_stress_response": "...",
  "defining_memory": "...",
  "secret_desire": "...",
  "contrast_with_protagonist": "...",
  "high_freq_vocab": ["词1","词2","词3"],
  "speech_taboo": ["类型1","类型2"],
  "speech_under_anger": "...",
  "speech_under_fear": "...",
  "speech_under_joy": "...",
  "sentence_length_preference": "..."
}}
"""
        data = request_json(
            system=SYSTEM, user=prompt,
            required_keys=["signature_mannerisms", "verbal_tics"],
            max_retries=3, temperature=0.8,
            agent_name=f"MajorRefiner[{char.name}]",
            empty_ok=True,
        )
        return (char, data) if data else None

    results = parallel_map(
        fn=refine_one,
        items=pending,
        max_workers=PARALLEL_WORKERS,
        label="MajorRefiner",
    )

    # 主线程串行把结果写回 Character（避免多线程同时改同一对象的竞态，虽然此处每线程改不同对象）
    for res in results:
        if not res:
            continue
        char, data = res
        char.signature_mannerisms = data.get("signature_mannerisms", []) or []
        char.verbal_tics = data.get("verbal_tics", []) or []
        char.sensory_signature = data.get("sensory_signature", "")
        char.default_stress_response = data.get("default_stress_response", "")
        char.defining_memory = data.get("defining_memory", "")
        char.secret_desire = data.get("secret_desire", "")
        char.contrast_with_protagonist = data.get("contrast_with_protagonist", "")
        char.high_freq_vocab = data.get("high_freq_vocab", []) or []
        char.speech_taboo = data.get("speech_taboo", []) or []
        char.speech_under_anger = data.get("speech_under_anger", "")
        char.speech_under_fear = data.get("speech_under_fear", "")
        char.speech_under_joy = data.get("speech_under_joy", "")
        char.sentence_length_preference = data.get("sentence_length_preference", "")
        print(f"  ✓ {char.name}（{char.role.value}）")
        if char.signature_mannerisms:
            print(f"      动作：{' / '.join(char.signature_mannerisms[:2])}")
        if char.sensory_signature:
            print(f"      感官：{char.sensory_signature[:40]}")


def format_character_signature_for_writer(char: Character) -> str:
    """
    把角色的细腻刻画字段+语言指纹格式化成 writer/chapter_planner 可读的一段。
    空字段自动跳过。
    """
    if not any([char.signature_mannerisms, char.verbal_tics, char.sensory_signature,
                char.default_stress_response, char.high_freq_vocab, char.speech_taboo]):
        return ""
    parts = [f"【{char.name} 的细腻钩子】"]
    if char.signature_mannerisms:
        parts.append(f"  习惯动作：{' / '.join(char.signature_mannerisms[:2])}")
    if char.verbal_tics:
        parts.append(f"  说话习惯：{' / '.join(char.verbal_tics[:2])}")
    if char.high_freq_vocab:
        parts.append(f"  高频词：{' / '.join(char.high_freq_vocab[:4])}")
    if char.speech_taboo:
        parts.append(f"  语言禁区：{' / '.join(char.speech_taboo[:3])}")
    if char.sentence_length_preference:
        parts.append(f"  句式偏好：{char.sentence_length_preference}")
    if char.sensory_signature:
        parts.append(f"  感官标记：{char.sensory_signature}")
    if char.default_stress_response:
        parts.append(f"  压力反应：{char.default_stress_response}")
    # 情绪下的语言变化（只在需要时在 writer 上下文里出现）
    emo = []
    if char.speech_under_anger:
        emo.append(f"怒:{char.speech_under_anger}")
    if char.speech_under_fear:
        emo.append(f"惧:{char.speech_under_fear}")
    if char.speech_under_joy:
        emo.append(f"喜:{char.speech_under_joy}")
    if emo:
        parts.append(f"  情绪语言：{' ｜ '.join(emo)}")
    return "\n".join(parts)
