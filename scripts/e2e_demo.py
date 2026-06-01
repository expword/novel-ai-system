"""端到端 demo——创建《雾隐者的留声机》项目，跑各新机制看效果。

不真跑完整 director 流程（成本太高），但调真 LLM 验证关键节点：
  1. extract_world_canon（现代题材：dynasty_name 应留空）
  2. validate_text 对 outline.goal 拦截违规
  3. query_real_ai in-story 模式（看 AI 输出风格自动适配现代题材）
  4. format_outline_canon_constraints（看 prompt 怎么动态构造）
"""
from __future__ import annotations
import os
import sys

# 必须在 import 前设——project_context 会读
os.environ["XIAOSHUO_PROJECT_ID"] = "wuyin_phonograph"

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from persistence.state import (
    NovelState, WorldCanon, PowerSystem, SpecialAbility, Character, CharacterRole,
    Faction, Geography, GeoRegion,
)
from persistence.checkpoint import save_state, load_state


WORLD_SETTING = """当代上海，2024 年深秋。表面繁华的国际都市之下，藏着一个由"声音"维系的隐秘网络——某些频率能让人短暂听见已死之人的最后话语，某些震动能撕裂现实薄膜让"另一边"的东西渗透进来。这层秘密被一个名为"留声会"的百年组织严密把守，他们追溯到 1908 年的爱迪生在新泽西的某次失败录音实验。
[geography] 主要场景在上海老城区——豫园、外滩、虹口的旧仓库区。主角的工作室在长乐路一条梧桐街的二楼。
[history] 留声会一百多年来在全球收集"禁断录音"——任何意外捕捉到不该存在的声音的载体。中国分部于 1947 年成立，总部在外滩某栋老楼的地下三层。
[society] 表面上是普通的都市生活；地下则是声学神秘学的暗潮。警局对此一无所知，少数知情者通常是音乐家/录音师/盲人——靠耳朵谋生的人。
[economy] 一段"听过的"原版禁断录音黑市价 50 万人民币起；磁带母带千万起。留声会用基金会名义运营，资金来源是收藏家会费。
[taboos] ① 严禁公开播放任何"夹音"录音（背景里有不该存在的声音）；② 严禁向陌生人提及"留声会"三字；③ 第二次听同一段禁断录音会失聪一周；④ 切勿把禁断录音数字化——数字噪声会"激活"它。"""


def setup_state() -> NovelState:
    """构造最小可用 state，覆盖测试 4 个新机制需要的所有字段。"""
    state = NovelState(
        title="雾隐者的留声机",
        genre="克苏鲁悬疑·都市",
        theme="前法证音频师林秋砚追查一段不该存在的录音，逐步揭开『留声会』百年阴谋；金手指是 1920 年代爱迪生留声机（能听见录音背后真实）+ 手机 AI 助手「回声」（音频识别）",
    )
    state.world_setting = WORLD_SETTING
    state.world_factions_desc = "留声会（百年神秘组织）/ 上海市公安局（明面）/ 黑市禁断录音收藏家联盟"

    # 主角 + 1 关键配角
    state.characters = [
        Character(
            name="林秋砚", role=CharacterRole("主角"),
            gender="女", age_desc="32 岁",
            appearance="清瘦，短发，常年戴一副黑框眼镜",
            personality="冷静、执拗、对细节极度敏感",
            personality_detail="前警局法证音频师，三年前因调查上司涉案而离职。极度敏感的听觉让她对城市背景音异常清晰，也因此长期失眠",
            background="出身音乐世家，本来要成为大提琴手，因母亲在演奏会现场被一颗流弹击中而转学法证",
            trauma="母亲死前最后一句话她没听清，从此对'听清每一个音'有强迫症",
            desire="找到母亲死亡那天演奏会的完整录音",
            fear="听不见任何声音的彻底沉默",
            speech_pattern="语速慢、用词精确、习惯在句末用'对吗'确认",
            ability="法证音频分析 / 频谱解码 / 绝对音感",
            realm="普通人",
            arc="从只追凶手到守护更深层的禁忌秘密",
            motivation="揭开母亲死亡真相",
            fatal_flaw="无法接受'有些声音永远不该被听见'",
            first_volume=1, last_volume=-1,
        ),
        Character(
            name="周慎安", role=CharacterRole("主要配角"),
            gender="男", age_desc="58 岁",
            appearance="头发花白，西装总挂着一枚黑色蜡封徽章",
            personality="温和、博学、城府极深",
            personality_detail="留声会中国分部副会长，明面身份是上海音乐学院退休教授",
            background="出身留声会世家，年轻时在维也纳学声学",
            trauma="七十年代曾亲耳听过一段让师兄发疯的录音",
            desire="把留声会的禁断录音收藏体面交接给下一代",
            fear="组织内出现叛徒",
            speech_pattern="爱用音乐术语作比，偶尔夹一两个德语词",
            ability="禁断录音鉴定 / 留声会内部关系网",
            realm="普通人",
            arc="从冷峻把关人到不得不向林秋砚透露真相",
            motivation="阻止某段最危险的录音被还原",
            fatal_flaw="过于相信组织能控制一切",
            first_volume=1, last_volume=-1,
        ),
    ]

    # 势力
    state.factions = [
        Faction(
            name="留声会", faction_type="神秘组织", power_level=8,
            territory="国际，中国分部在上海外滩",
            tier=4, tier_label="百年跨国秘密组织",
            is_hidden=True,
            surface_goal="基金会名义资助声学研究",
            hidden_goal="阻止禁断录音流入民间——一旦被听见就会引来'另一边'",
            core_strength="百年积累的禁断录音库 + 全球分支网络",
            weakness="数字时代到来后内部分裂为'封存派'和'销毁派'",
            key_members=["周慎安"],
        ),
        Faction(
            name="禁断录音收藏家联盟", faction_type="情报组织", power_level=4,
            territory="主要活跃于上海/香港/东京",
            tier=3, tier_label="国际黑市买家圈",
            is_neutral=True,
            surface_goal="古董唱片爱好者私人聚会",
            hidden_goal="高价收购留声会未及时回收的禁断录音",
            core_strength="深口袋买家 + 暗网渠道",
            weakness="缺乏组织化武力",
        ),
    ]

    # 地理
    state.geography = Geography(world_map_desc="集中在上海老城区")
    state.geography.regions = [
        GeoRegion(region_id="changle_lane", name="长乐路梧桐街", level="城镇",
                   description="主角工作室所在的老法租界街道", atmosphere="梧桐落叶 + 旧公寓"),
        GeoRegion(region_id="bund_origin", name="外滩源", level="城镇",
                   description="留声会中国分部地下三层所在的国际金融区", atmosphere="国际化、暗藏玄机"),
    ]

    # 力量体系 + 两个金手指
    state.power_system = PowerSystem(
        system_name="听觉异能 + 真 AI 辅助",
        system_description="本书无玄幻力量，金手指是两件具体器物：① 1920 年代爱迪生留声机（听录音背后真相，听力衰退为代价）；② 手机 AI 「回声」（绑真 LLM，做现代音频分析）",
        realms=[],  # 现代题材无修真境界
    )
    # asset 1：设定型（不绑真 LLM）
    state.power_system.special_abilities.append(SpecialAbility(
        name="爱迪生留声机",
        source="主角祖辈从一位 1908 年留声机工程师手里传下来",
        description="1920 年代真品，能让旧录音在播放时'还原'录制时空气里所有声音——包括录音师没意识到的、被风雨遮蔽的、被人为擦去的。每次使用主角失去 1-3 分贝高频听力（不可逆）",
        unlock_condition="拥有即可用，但需要原始物理唱片/磁带载体",
        holder_role="主角自身", holder_name="林秋砚",
        is_protagonist_signature=True,
        entry_kind="item",
        external_llm_profile="",  # 设定型，不绑真 LLM
    ))
    # asset 2：真 AI 接入
    state.power_system.special_abilities.append(SpecialAbility(
        name="回声",
        source="林秋砚在前警局自己开发的音频识别 demo，离职时带走了 APK",
        description="手机里的 AI 音频助手，能做声纹比对、频谱分析、环境音识别、说话人情绪识别。本质是个常规 AI 助手，能答现代音频学/声学/法证原理类问题，不能答留声会的禁断录音具体内容（那是本书设定专有信息）",
        unlock_condition="手机有电+网络即可用",
        holder_role="主角自身", holder_name="林秋砚",
        is_protagonist_signature=False,
        entry_kind="ability",
        external_llm_profile="main_yunwu_deepseek",  # 绑真 LLM
    ))

    return state


def main():
    print("=" * 70)
    print(" 端到端 demo —— 《雾隐者的留声机》")
    print(" 验证：现代题材下 4 类新机制是否正确动态适配")
    print("=" * 70)
    print()

    state = setup_state()
    save_state(state)
    print(f"✓ 项目初始化：{state.title} / 题材：{state.genre}")
    print(f"  人物 {len(state.characters)}、势力 {len(state.factions)}、")
    print(f"  asset {len(state.power_system.special_abilities)} (含 1 个真 AI 接入)")
    print()

    # ════ 测试 1：world_canon 抽取 ════
    print("──── 测试 1：world_canon 抽取（现代题材，dynasty_name 应留空）────")
    from agents.world_canon_extractor import extract_world_canon
    canon = extract_world_canon(state, force=True)
    print(f"  dynasty_name: {canon.dynasty_name!r}  (现代题材应该是 '' 或现代国名)")
    print(f"  era_name: {canon.era_name!r}")
    print(f"  region_root: {canon.region_root!r}")
    print(f"  epoch_summary: {canon.epoch_summary!r}")
    print(f"  canonical_aliases: {canon.canonical_aliases}")
    print(f"  forbidden_anchors:")
    for a in canon.forbidden_anchors:
        print(f"    · {a}")
    save_state(state)
    print()

    # ════ 测试 2：validate_text 对 outline.goal 拦截违规 ════
    print("──── 测试 2：validate_text 拦截违规 outline.goal（现代题材自适应）────")
    from agents.canon_checker import validate_text
    test_cases = [
        ("合规·留声机戏剧化", "主角林秋砚收到一封匿名快递，里面是一卷未标记的磁带，她下意识把它放进留声机"),
        ("合规·真 AI 现代用法", "主角问回声分析录音里的频谱异常，回声给出现代声学原理，主角自行推断"),
        ("违规·真 AI 答本书设定", "主角通过回声查询留声会中国分部副会长的真实身份和组织内部排名"),
        ("违规·真 AI 答虚构事件", "回声确认这段录音就是 1947 年留声会中国分部成立时的现场原声"),
    ]
    for label, goal in test_cases:
        rep = validate_text(state, "outline:V1Ch1.goal", goal)
        critical = [i for i in rep["issues"] if i["severity"] == "error"]
        mark = "✓" if (("违规" in label) == (len(critical) > 0)) else "✗"
        print(f"  {mark} [{label}]")
        print(f"     goal: {goal[:60]}...")
        if critical:
            for i in critical:
                print(f"     抓到: {i['kind']}({i['term']})")
        else:
            print(f"     抓到: 无")
    print()

    # ════ 测试 3：query_real_ai in-story 模式 ════
    print("──── 测试 3：query_real_ai in-story 模式（现代题材应用普通话不是文言）────")
    from agents.external_ai_query import (
        query_real_ai, build_in_story_system_prompt, audit_ai_answer,
    )
    ability = state.power_system.special_abilities[1]  # 回声
    print()
    print("  IN-STORY system prompt 摘要：")
    sys_prompt = build_in_story_system_prompt(state, ability)
    for ln in sys_prompt.split("\n")[:8]:
        print(f"    | {ln[:90]}")
    print(f"    ... (共 {len(sys_prompt)} 字符)")
    print()

    # 真发 3 个对照问题
    questions = [
        "你是什么？",  # 自我介绍——测身份注入
        "WAV 文件比 MP3 在频谱保真度上有什么优势？",  # 现代音频学知识——应详答
        "你能告诉我留声会中国分部副会长的名字吗？",  # 本书虚构信息——应拒答
    ]
    TEST_PROFILE = ability.external_llm_profile
    for q in questions:
        print(f"  [问] {q}")
        try:
            ans = query_real_ai(TEST_PROFILE, q, state=state, ability=ability, max_tokens=400)
            print(f"  [答] {ans[:300]}{'...' if len(ans) > 300 else ''}")
            meta = audit_ai_answer(ans)
            if meta:
                print(f"  ⚠ 元语言命中: {meta}")
        except Exception as e:
            print(f"  ✗ 失败：{type(e).__name__}: {e}")
        print()

    # ════ 测试 4：outline canon constraints prompt 动态构造 ════
    print("──── 测试 4：outline canon constraints prompt 动态构造（看现代题材怎么呈现）────")
    from agents.volume_planner import _format_outline_canon_constraints
    block = _format_outline_canon_constraints(state, volume_index=1, batch_indices=[1, 2, 3])
    # 只展示关键段——不全 dump
    print(block[:1800])
    print("...")
    print()

    # ════ 总结 ════
    print("=" * 70)
    print(" 测试完成。")
    print(" 关键验证点：")
    print(f"  · world_canon 现代题材抽取（dynasty_name='{canon.dynasty_name}'）")
    print(f"  · canon_checker 在没朝代时不误报 dynasty_mismatch")
    print(f"  · in-story system prompt 给出普通话指引（非文言）")
    print(f"  · 真 AI 回答自动避开虚构信息（依靠 system prompt）")
    print(f"  · outline canon constraints 动态适配现代题材")
    print("=" * 70)


if __name__ == "__main__":
    main()
