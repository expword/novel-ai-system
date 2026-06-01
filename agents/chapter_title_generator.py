"""
ChapterTitleGenerator —— 独立章节标题生成器。

═══ 解决的问题 ═══

网文章节标题决定推荐位的点击率——平台展示给读者的就是「第 X 章 标题」。
当前 chapter title 由 volume_planner 顺手起，prompt 没有"标题学"专门训练：
  · 容易土气（"激战 / 风云 / 觉醒"等老套词）
  · 容易撞前缀（连续 5 章都"第 N 章 战"）
  · 容易剧透（"反派落败" 这种把高潮吐出来的）
  · 不抓 hook（标题不勾人、不留悬念）

ChapterTitleGenerator 把"起标题"从 volume_planner 拆出来,作为独立 LLM 调用：
  · 输入: 本章 goal / closing_hook / chapter_type / 主角名 / 卷主题 / 近 5 章已用标题
  · 输出: 一个候选标题列表(3-5 个),按"勾人度"排序
  · 选最高分作为 final title

═══ 应用场景 ═══

· writer.py 兜底分支(outline 没给 title 时)调一次,替代让 writer 自拟
· web UI 暴露"重生本章标题"按钮(后续接入)
· batch polish(volume_planner 跑完后批量优化,可选)

═══ 设计原则(按 [[feedback_generic_prompts]])═══

· prompt 通用——不硬编码具体项目术语
· 标题学规则从通用网文经验出来,不针对某一本
· 走 'extractor' usage 路由(轻量便宜)
· 失败兜底返回简单 fallback(不阻塞 writer)
"""
from __future__ import annotations
from typing import Optional

from utils.json_utils import request_json_with_profile
from utils.agent_contract import AgentContract, register


CONTRACT = register(AgentContract(
    name="chapter_title_generator.generate_title",
    inputs=[
        "characters[*].name",
        "characters[*].role",
        "volumes[*].theme",
        "completed_chapters[*].title",
    ],
    outputs=[
        # 返回 str 给调用方,不直接写 state
    ],
    invariants=[],
    notes=(
        "独立章节标题生成器。writer.py 兜底分支(无 outline title 时)调用,"
        "或 web UI 'rewrite title' 调用。走 extractor usage,失败兜底简单串。"
    ),
))


SYSTEM = """你是【网文章节标题工程师】——专精网文标题学。

═══ 你的任务 ═══

为单章生成 3-5 个候选标题(每个 ≤10 字,优选 4-6 字),按「读者推荐位点击率」排序。

═══ 标题学铁律 ═══

1. 【勾人 > 描述】标题是诱饵，不是摘要。读者看一眼就想知道「然后呢?」
2. 【不剧透】禁止把本章高潮/反转结果写进标题(× "反派落败" / "真相大白")
3. 【避开撞前缀】不要连续多章用同一首字(× 「战…」「战…」「战…」)
4. 【动词优先】用具体动作动词(挑、撕、跪、问、刺)比形容词("激烈的""惊天")更勾人
5. 【意象点睛】1-2 字具体意象 + 1-2 字动作/转折(如「雪夜·叩门」「酒中·见血」)
   或纯短动作("斩"、"跪求"、"破局")
6. 【避免老套词】禁词:风云、激战、觉醒、惊变、决战、燃天、震惊、逆天、王者
7. 【可玩转折号 / 句号】"未亡人。" / "他疯了——" 等带情绪标点的短句也行
8. 【符合本章本质】战斗章可激烈,日常章不要起战斗标题(类型错配=诈骗)

═══ 你输出 ═══

JSON: {"candidates": [{"title": "...", "appeal_score": 1-10, "reason": "为何勾人(20字)"}, ...]}

3-5 个候选,按 appeal_score 降序排好。"""


def generate_title(
    state,
    chapter_index: int,
    *,
    chapter_goal: str = "",
    closing_hook: str = "",
    chapter_type: str = "",
    structure_role: str = "",
    volume_theme: str = "",
    avoid_titles: Optional[list[str]] = None,
    fallback: str = "",
) -> str:
    """
    给单章生成标题。返回单个 str(最优候选);失败返回 fallback。

    avoid_titles: 近 N 章已用的标题,LLM 必须避开
    fallback: LLM 失败时的兜底字符串(通常用上游已有的 placeholder)
    """
    avoid = list(avoid_titles or [])
    proto_name = _get_protagonist_name(state)
    user = _build_user_prompt(
        chapter_index=chapter_index,
        chapter_goal=chapter_goal,
        closing_hook=closing_hook,
        chapter_type=chapter_type,
        structure_role=structure_role,
        volume_theme=volume_theme,
        protagonist=proto_name,
        avoid_titles=avoid,
    )

    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["candidates"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return fallback

    if not isinstance(result, dict):
        return fallback
    candidates = result.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return fallback

    # 取 appeal_score 最高的
    best = None
    best_score = -1
    for c in candidates:
        if not isinstance(c, dict):
            continue
        title = (c.get("title") or "").strip().strip("《》「」\"'")
        if not title:
            continue
        # 跳过 avoid_titles 命中的
        if any(_titles_too_similar(title, av) for av in avoid):
            continue
        try:
            score = int(c.get("appeal_score") or 0)
        except Exception:
            score = 0
        if score > best_score:
            best_score = score
            best = title

    return best or fallback


def generate_candidates(
    state,
    chapter_index: int,
    *,
    chapter_goal: str = "",
    closing_hook: str = "",
    chapter_type: str = "",
    structure_role: str = "",
    volume_theme: str = "",
    avoid_titles: Optional[list[str]] = None,
) -> list[dict]:
    """
    返回完整候选列表(供 web UI 让作者挑选)。每个元素 {title, appeal_score, reason}。
    """
    avoid = list(avoid_titles or [])
    proto_name = _get_protagonist_name(state)
    user = _build_user_prompt(
        chapter_index=chapter_index,
        chapter_goal=chapter_goal,
        closing_hook=closing_hook,
        chapter_type=chapter_type,
        structure_role=structure_role,
        volume_theme=volume_theme,
        protagonist=proto_name,
        avoid_titles=avoid,
    )
    try:
        result = request_json_with_profile(
            system_prompt=SYSTEM,
            user_prompt=user,
            required_keys=["candidates"],
            usage="extractor",
            max_attempts=2,
            empty_ok=True,
        )
    except Exception as e:
        _surface_failure(chapter_index, e)
        return []
    if not isinstance(result, dict):
        return []
    cands = result.get("candidates") or []
    out = []
    for c in cands:
        if isinstance(c, dict) and (c.get("title") or "").strip():
            out.append({
                "title": (c.get("title") or "").strip().strip("《》「」\"'"),
                "appeal_score": int(c.get("appeal_score") or 0),
                "reason": (c.get("reason") or "").strip(),
            })
    return out


# ═══════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════

def _build_user_prompt(
    *,
    chapter_index: int,
    chapter_goal: str,
    closing_hook: str,
    chapter_type: str,
    structure_role: str,
    volume_theme: str,
    protagonist: str,
    avoid_titles: list[str],
) -> str:
    parts = [f"为第 {chapter_index} 章生成 3-5 个候选标题。"]
    if chapter_type:
        parts.append(f"章型: {chapter_type}")
    if structure_role:
        parts.append(f"结构定位: {structure_role}")
    if volume_theme:
        parts.append(f"本卷主题: {volume_theme[:60]}")
    if protagonist:
        parts.append(f"主角: {protagonist}")
    if chapter_goal:
        parts.append(f"本章目标: {chapter_goal[:120]}")
    if closing_hook:
        parts.append(f"章末钩子: {closing_hook[:80]}")
    if avoid_titles:
        avoid_str = " / ".join(f"《{t}》" for t in avoid_titles[:10])
        parts.append(f"近期已用标题(必须避开撞前缀/相似句式): {avoid_str}")
    parts.append("")
    parts.append("输出 JSON 严格按 schema: {\"candidates\":[{\"title\":\"...\",\"appeal_score\":1-10,\"reason\":\"...\"}]}")
    return "\n".join(parts)


def _get_protagonist_name(state) -> str:
    try:
        for c in (getattr(state, "characters", None) or []):
            role_val = getattr(c.role, "value", str(c.role))
            if role_val == "主角":
                return c.name
    except Exception:
        pass
    return ""


def _titles_too_similar(a: str, b: str) -> bool:
    """判定两个标题是否相似(撞前缀/同长度同首字)。粗启发式。"""
    if not a or not b:
        return False
    a = a.strip()
    b = b.strip()
    if a == b:
        return True
    # 同首字 且 长度相差 ≤2 → 视为撞(包括单字标题与"单字+扩展"的撞)
    if a[0] == b[0] and abs(len(a) - len(b)) <= 2:
        return True
    return False


def _surface_failure(chapter_index: int, e: Exception) -> None:
    try:
        from persistence.checkpoint import add_progress_warning
        add_progress_warning(
            level="warn",
            source=f"chapter:{chapter_index}:title_generator",
            message=f"标题生成失败,走 fallback: {type(e).__name__}: {str(e)[:120]}",
        )
    except Exception:
        pass
