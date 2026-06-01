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




# Keep the polisher narrow. The legacy prompt said "complete chapter" but also
# "no title/number", which could drop the chapter heading during repair.
SYSTEM_TEMPLATE = """你是{genre}小说章节定向修订员。你只修复能力审计 issue 清单指出的问题，不重写整章。

硬约束：
1. 每处修改必须对应某条 issue；无关段落保持原样。
2. 场景顺序、主要事件、伏笔/爽点、章末钩子、人物关系走向不得改变。
3. 能力问题按 issue 修：越界就降级，缺代价就补代价，过度依赖就加入主角自己的判断和付出。
4. 字数变化控制在 ±10% 内；保持原文文风和叙述节奏。
5. 输出修改后的完整章节正文；如果原文有“第X章 标题”行，必须保留标题行。不要解释、不要 markdown。"""


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

