"""
章节润色器 —— 基于 AbilityAudit 做 targeted fix。

跟 chapter_editor 的区别：
  · chapter_editor = 用户自由对话，改笔触/融合新细节
  · chapter_polisher = 按审计结果定向修问题，不接受额外自由度

原则：
  · 只修 AbilityAudit.issues 列出来的问题
  · 骨架（场景顺序/情节事件/伏笔爽点/章末钩子）保持不变
  · 字数在 ±10% 内
  · 输出完整新版章节正文（纯文本，无 markdown，无说明）
"""
from __future__ import annotations
from typing import Optional


SYSTEM_TEMPLATE = """你是{genre}小说的章节润色员。一位能力审计员刚刚审完本章，指出了若干金手指/技能使用上的合理性问题。你的任务是——**只针对这些具体问题**定向修正章节正文，其他地方一律不要动。

硬约束：

一、【只修审计问题】
你将收到一份 issue 清单，每条有 type / severity / description / suggested_fix。你的每一处修改都必须对应某条 issue——改之前先想"这个改动对应哪条 issue？"。凡是跟 issue 无关的改动都是越权。

二、【绝不动骨架】
- 场景顺序不变、场景数量不变
- 每幕的主要情节事件不变（谁做了什么、去了哪、得到什么）
- 章节推动的伏笔/爽点不变（原文触发/回收什么，修后必须保留）
- 角色关系动态不变
- 章末钩子不变
- 主角内心的总体情绪走向不变

三、【典型修法参考】（按 issue.type）
- overuse → 减少使用次数，合并到一次；或改为主角先自己判断、金手指只补位
- overreach → 把越界的能力效果改成设定允许范围内的替代方案
- no_cost → 在使用处补上设定里约定的代价（冷却/消耗/副作用描写）
- scale_mismatch → 把过强的表现降到主角当前阶段能支撑的程度；或加一句铺垫说明为什么能越级
- underuse → 补一处"主角想到/试过用金手指"的段落
- over_dependence → 插入主角自己的判断/挣扎/付出，让金手指成为辅助而非主导
- reaction_missing → 给对手/配角补一句合乎认知水平的反应（惊讶/困惑/不解）

四、【字数控制】
总字数相对原版波动必须在 ±10% 以内——这是笔触级修正，不是重写。

五、【输出格式】
直接输出修改后的完整章节正文。不要任何前言、解释、章节标题/编号、markdown 标记、代码块。从第一句正文开始，到最后一句结束。

六、【保留风格】
保持原章的文风、叙述节奏、对话节奏不变——你是润色，不是换笔。"""


def build_polish_messages(
    state,
    chapter_index: int,
    chapter_text: str,
    audit,
) -> Optional[list[dict]]:
    """
    拼 OpenAI 格式 messages。返回 None 表示无事可做（审计无 issues）。
    """
    if not audit or not audit.issues:
        return None

    system = SYSTEM_TEMPLATE.format(genre=getattr(state, "genre", "") or "")

    # 把 issue 清单拼成一段
    issue_lines = []
    for i, iss in enumerate(audit.issues, 1):
        fix_part = f" → 建议方向：{iss.suggested_fix}" if iss.suggested_fix else ""
        issue_lines.append(
            f"{i}. [{iss.severity}/{iss.type}] {iss.description}{fix_part}"
        )
    issue_block = "\n".join(issue_lines)

    # 能力使用清单（供润色员定位修哪里）
    use_lines = []
    for u in audit.ability_uses:
        mark = "✓" if u.setting_match else "✗越界"
        cost = u.cost_paid or "未付"
        use_lines.append(f"- {u.ability_name}：{u.how_used}（代价：{cost}，设定：{mark}）")
    uses_block = "\n".join(use_lines) if use_lines else "（审计未识别到能力使用）"

    user = (
        f"═══ 第 {chapter_index} 章 · 润色任务 ═══\n\n"
        f"【审计发现的问题（请定向修正这些）】\n{issue_block}\n\n"
        f"【本章审计员识别的能力使用清单（供定位）】\n{uses_block}\n\n"
        f"【本章原文】\n{chapter_text}\n\n"
        f"现在按 SYSTEM 的要求输出修改后的完整章节正文。"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
