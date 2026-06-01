"""
PlotEnhancer —— Phase -0.7：主动反问"只看作者写的会不会无聊"，补 3-5 个能吸引读者的情节钩子。

═══ 设计动机 ═══

作者的 intent_description 通常只描述题材取向（主角是谁、故事大致走向、对标风格），
但不会把"哪些情节能让读者翻下一页"想清楚。结果就是下游 satisfaction_system /
foreshadow_manager / twist_designer 各自闭门造车——爽点不点头、伏笔没回收、反转无铺垫。

本 agent 在 intent_analyzer（Phase -1）之后、concept_pitch（Phase 0）之前跑一次，
主动**补**而不是**重写**作者意图。产出列表给作者审，采纳的进 state.creative_intent
.plot_supplements，下游 agent 必须显式落地。

═══ 工作流 ═══

  · 输入：state.creative_intent.raw_description + suggested_theme + reality_basis
  · LLM 任务："只看作者写的，故事够不够吸引读者？补 3-5 个能让读者留下的钩子。
              每个都解释为什么这能让读者追"
  · 输出 plot_supplements 列表
  · 失败时不阻塞 phase——返回空列表

═══ 设计原则 ═══

  · prompt 完全通用——不硬编码项目术语（按 [[feedback_generic_prompts]]）
  · 尊重 reality_basis：real_history 模式下不许补穿越/系统/异能；real_adapted 适度；
    fictional 完全自由
  · 走 'extractor' usage 路由——结构化短任务，轻量便宜模型
  · 失败兜底：LLM 调用失败 → 返回空 list 不阻塞 Phase 0
"""
from __future__ import annotations
from typing import Optional

from persistence.state import NovelState, PlotSupplement


SYSTEM = """你是【小说情节增强师】——任务：读作者的"想写什么"自然语言，主动反问
"只看这段描述，故事会让读者哭吗？笑吗？掩卷沉思吗？"，然后补 5-8 个**能让读者翻页/落泪/会心一笑/陷入沉思**的钩子。

═══ 你要补的 8 类钩子（不要只补悬念——一部好作品要有层次）═══

  1. **suspense（悬念钩子）**：让读者忍不住翻页的未解谜团——具体到画面/对话
     · 例 A："主角发现父亲遗物里夹着一张陌生女人的照片，背面只写'对不起'"
     · 例 B："深夜书房传出第二个脚步声——可家里只有主角一人"

  2. **emotional（情感锚点）**：让读者代入主角并产生强烈共鸣的场景
     · 例 A："主角第一次撒谎是为了保护一个他后来要亲手送走的人"
     · 例 B："主角看着别人的婚礼，发现自己竟在为新郎而不是新娘心动"

  3. **philosophical（哲理钩子）**：让读者掩卷沉思的命题/抉择/悖论
     · 例 A："主角必须在'救一个无辜的人'和'守住一个秘密让千万人安宁'之间选一个"
     · 例 B："越接近真相，主角越发现自己也是真相的一部分——他能审判自己吗"
     · 例 C："这个世界的善人都死得早，主角必须决定是当下一个善人，还是活下去"

  4. **humorous（趣味反差）**：让读者会心一笑的反差/反套路/段子时刻
     · 例 A："威震八方的大人物，回家最怕老婆——但他老婆只怕家里养的猫"
     · 例 B："主角终于学会传说中的禁术——结果第一次施展只是用来烤红薯"

  5. **moving（感人时刻）**：让读者落泪的牺牲/和解/告别
     · 例 A："师父最后一次教主角写字——握着主角的手，把'忘'字写成了'活'"
     · 例 B："冷峻反派死前没说狠话，只递给主角一封早就写好的、给主角母亲的回信"

  6. **setting_payoff（设定爆点）**：揭开后扭转主角对世界认知的真相
     · 例 A："这个世界的雨是温的——但读到一半才知道，是因为雨水里全是血"
     · 例 B："主角一直追的那个'未来'其实是过去——时间在这个世界是倒着走的"

  7. **relationship_twist（关系反转伏笔）**：与主角最近的某人最终意外转向
     · 例 A："最护主角的姨母，二十年前是杀害主角生母的执行者"
     · 例 B："主角的死对头其实是失散多年的亲哥哥，但只有死对头知道"

  8. **signature_detail（微设定钩子）**：一句话能让读者记住整本书的标志性细节
     · 例 A："这个世界的雨是温的；只有真正死过一次的人才会觉得它冰"
     · 例 B："这里的人临终前会闻到桂花香——哪怕死在寒冬"

═══ 数量分布建议 ═══
  · 共 5-8 个建议
  · **悬念/情感/哲理/感人 这四类至少各 1 个**（让作品有 4 重情绪层次）
  · 其余 1-4 个从 趣味/设定爆点/关系反转/微设定 里挑

═══ 关键原则 ═══

  · **补**而不是**重写**作者意图——所有建议必须能"嫁接"到作者原意上，不许换主线
  · **具体而非抽象**——不许写"加点感情戏 / 多埋伏笔 / 让节奏更紧凑"这种废话
  · 每个建议必须能说清「**为什么这能让读者留下**」——一句话讲清心理机制（如"陡然反差感"
    "未解谜团驱动力""第一人称代入悬念""第三次相遇的悲剧""我是不是也会这样"等）
  · 建议要**互相不重复**——不同 kind 之间互补
  · 建议要**可落地**——下游 satisfaction_system/foreshadow/twist/writer 能从你的描述
    直接转成具体设计；不要写抽象哲学命题

═══ ⚠ 故事根基约束（由调用方在 user prompt 里告知）═══

  · real_history（严格基于真实历史）：所有建议必须在史料合理推演范围内；
    禁止补穿越/重生/系统/异能/灵魂出窍等超现实元素；
    哲理钩子可以借历史人物已有抉择的真实困境展开
  · real_adapted（真实人物/事件改编）：允许有限超现实；建议的核心张力应基于
    真实历史事件本身的戏剧性
  · fictional（完全虚构）：完全自由

═══ 输出严格 JSON ═══

{
  "supplements": [
    {
      "kind": "suspense|emotional|philosophical|humorous|moving|setting_payoff|relationship_twist|signature_detail",
      "name": "10-15 字短名（给作者一眼能记住）",
      "what": "具体补什么（60 字，写清画面/事件/转折/对话）",
      "why_engaging": "为什么这能让读者留下（40 字，讲清心理机制）",
      "where_to_inject": "建议注入到哪——卷/章范围（如'第 1 卷中段'/'贯穿全书'）",
      "intensity": "low|mid|high"
    }
  ]
}

═══ 铁律 ═══

  · 5-8 个建议——少于 5 个说明你没充分挖；多于 8 个会让作者审不过来
  · 8 类 kind 至少覆盖 4 类（包含 emotional/philosophical/moving 中的至少 2 类——
    一部只有悬念没有情感哲理的作品不会被记住）
  · 不许复述作者已经写过的内容
  · 不许提项目特定术语（如"系统流""修真"等）作为示例参考——按本书自身设定补
  · 不许编造作者已有意图之外的主角设定（如改主角名字/性别/原型）"""


def enhance_plot(state: NovelState) -> list[PlotSupplement]:
    """
    主入口：从 state.creative_intent.raw_description 补充情节建议。

    幂等：若 state.creative_intent.plot_supplements 已有内容（含被审过的），跳过——
    用户若想重跑，先在前端清空 plot_supplements 即可。

    失败兜底：LLM 调用失败 → 返回空列表 + progress_warning，不阻塞下游 phase。
    """
    intent = getattr(state, "creative_intent", None)
    raw = (getattr(intent, "raw_description", "") or "").strip() if intent else ""
    if not raw:
        return []

    if intent.plot_supplements:
        print(f"  ✓ plot_enhancer 已跑过（{len(intent.plot_supplements)} 条建议），跳过")
        return list(intent.plot_supplements)

    basis = (intent.reality_basis or "").strip() or "fictional"
    basis_label = {
        "real_history": "real_history（严格基于真实历史——禁止补任何超现实元素）",
        "real_adapted": "real_adapted（真实人物/事件改编——允许有限超现实）",
        "fictional":    "fictional（完全虚构——自由发挥）",
    }.get(basis, "fictional")

    user = f"""作者写的"想写什么"自然语言：
\"\"\"
{raw[:4000]}
\"\"\"

【故事根基约束】{basis_label}

按 SYSTEM 规则补 3-5 个能让读者翻下一页的情节钩子。每个都解释为什么这能留住读者。
严格 JSON 输出。"""

    from llm_layer.llm_call import request_json_for_task
    try:
        data = request_json_for_task(
            "extraction",
            system=SYSTEM, user=user,
            required_keys=["supplements"],
            max_retries=2, temperature=0.5,
            agent_name="PlotEnhancer",
            empty_ok=True,
        )
    except Exception as e:
        print(f"  ⚠ plot_enhancer 失败：{type(e).__name__}: {e}")
        _warn_failure(str(e))
        return []

    raw_list = (data.get("supplements") if data else []) or []
    if not raw_list:
        print("  ✓ plot_enhancer 未产出建议（可能 LLM 觉得作者意图已足够丰富）")
        return []

    out: list[PlotSupplement] = []
    valid_intensities = {"low", "mid", "high"}
    valid_kinds = {"suspense", "emotional", "philosophical", "humorous",
                    "moving", "setting_payoff", "relationship_twist", "signature_detail"}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        what = str(item.get("what") or "").strip()
        if not name or not what:
            continue
        intensity = str(item.get("intensity") or "mid").strip().lower()
        if intensity not in valid_intensities:
            intensity = "mid"
        kind = str(item.get("kind") or "suspense").strip().lower()
        if kind not in valid_kinds:
            kind = "suspense"
        out.append(PlotSupplement(
            name=name[:40],
            what=what[:200],
            why_engaging=str(item.get("why_engaging") or "")[:120],
            where_to_inject=str(item.get("where_to_inject") or "")[:60],
            intensity=intensity,
            kind=kind,
            adopted=None,  # 待作者审
            notes="",
        ))

    intent.plot_supplements = out

    # 写一条 progress_warning 让前端"创作意图"面板能看到
    try:
        from persistence.checkpoint import add_progress_warning, clear_progress_warnings
        clear_progress_warnings(source="plot_enhancer")
        if out:
            names_preview = " / ".join(f"《{s.name}》" for s in out[:5])
            add_progress_warning(
                level="info",
                source="plot_enhancer",
                message=(
                    f"plot_enhancer 已生成 {len(out)} 条补充情节建议：{names_preview}。"
                    "请到「创作意图」面板审核——采纳的会被下游 satisfaction_system / "
                    "foreshadow_manager / twist_designer 落地。"
                ),
            )
    except Exception:
        pass

    _KIND_LABELS = {
        "suspense": "悬念", "emotional": "情感", "philosophical": "哲理",
        "humorous": "趣味", "moving": "感人", "setting_payoff": "设定爆点",
        "relationship_twist": "关系反转", "signature_detail": "微设定",
    }
    print(f"  ✓ plot_enhancer 产出 {len(out)} 条补充情节建议（待作者审核）：")
    for s in out:
        adoption_tag = "" if s.adopted is None else (" ✓" if s.adopted else " ✗")
        kind_tag = _KIND_LABELS.get(s.kind, s.kind)
        print(f"    · [{kind_tag}·{s.intensity}] 《{s.name}》{adoption_tag}：{s.what[:50]}")
    return out


def _warn_failure(msg: str) -> None:
    """LLM 调用整体失败时挂个 warning（不阻塞下游）。"""
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source="plot_enhancer",
            message=f"plot_enhancer LLM 调用失败：{msg[:120]}。下游会按"
                     "作者原意图直接走 concept_pitch——可在前端「创作意图」面板手动补充情节。",
        )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  下游引用：把 adopted 的建议格式化成 prompt 段
# ═══════════════════════════════════════════════════════════════

def format_adopted_supplements(intent) -> str:
    """
    把 adopted=True 的 plot_supplements 格式化成下游 agent prompt 用的硬约束段。
    供 satisfaction_system / foreshadow_manager / twist_designer / volume_planner 调用。

    未采纳（None / False）的建议不输出——它们只在前端面板里供作者参考。
    """
    if not intent or not getattr(intent, "plot_supplements", None):
        return ""
    adopted = [s for s in intent.plot_supplements if s.adopted is True]
    if not adopted:
        return ""

    _KIND_LABELS = {
        "suspense": "悬念", "emotional": "情感", "philosophical": "哲理",
        "humorous": "趣味", "moving": "感人", "setting_payoff": "设定爆点",
        "relationship_twist": "关系反转", "signature_detail": "微设定",
    }
    lines = ["═══ 【作者已采纳的补充情节建议】（下游必须落地） ═══"]
    for s in adopted:
        intensity_tag = {"low": "[暗线]", "mid": "[主线钩子]", "high": "[核心爆点]"}.get(s.intensity, "")
        kind_tag = f"[{_KIND_LABELS.get(s.kind, s.kind)}]"
        lines.append(f"  ◆ {kind_tag}{intensity_tag}《{s.name}》")
        lines.append(f"      内容：{s.what}")
        if s.why_engaging:
            lines.append(f"      读者钩子：{s.why_engaging}")
        if s.where_to_inject:
            lines.append(f"      建议注入：{s.where_to_inject}")
        if s.notes:
            lines.append(f"      作者备注：{s.notes}")
    lines.append("")
    lines.append("  ⚠ 这些是作者审过的硬约束——你产出的设计必须把它们落地为具体爽点/伏笔/反转/感情戏，"
                  "不许忽略，也不许换内核。"
                  "特别地：哲理/感人/趣味类钩子要被 writer 在合适章节的具体场景里落出来，"
                  "不要只挂在叙事线层就算完事。")
    return "\n".join(lines)
