"""章级 cycle 真节点——包 v1 writer / critic / revise，与 mock 同样的 4 节点。

  write_draft   ← v1 agents.writer.write_chapter
  critic_review ← v1 agents.critic.review_chapter
  revise        ← v1 agents.writer.revise_chapter
  finalize      ← 纯本地（写盘 + 标 finalize 原因）

ChapterDirective 来源：直接用 v1 DirectorAgent._generate_directive 生成。这是
"为某章准备一个完整 directive（must_include / 张力 / 节奏 / 蓝图 / 伏笔 / 爽点）"
的权威路径——v1 director 本身就是这么做的。

调用方约定：
  · ChapterState 至少要有 chapter_index / volume_index
  · v1 项目 state（含 G1-G4 + 卷级产物）必须已就绪——否则 _generate_directive 拿不到 must_include
  · adapter.ensure_v1_env(project_id) 必须先调用
"""
from __future__ import annotations

from state_chapter import ChapterState
from adapter import ensure_v1_env


def _build_directive_and_blueprint(v1_state, chapter_index: int, volume_index: int,
                                     word_quota: int):
    """用 v1 DirectorAgent._generate_directive + build_chapter_blueprint
    构造完整 directive（含 blueprint）—— writer/critic 都需要它。"""
    # 实例化一个轻量 DirectorAgent（resume=True 复用已保存的 state）
    from director import DirectorAgent  # type: ignore
    agent = DirectorAgent(resume=True)
    # 把 LangGraph 传入的 v1_state 接管为 agent.state（避免 agent 重新加载）
    agent.state = v1_state
    agent.state.current_volume_index = volume_index
    agent.state.current_chapter_index = chapter_index

    # 生成 directive（含 must_include / 张力 / 节奏 / 伏笔 / 爽点 / 反转）
    directive = agent._generate_directive(chapter_index, volume_index)

    # 生成场景蓝图（writer 需要）
    from agents.chapter_planner import build_chapter_blueprint  # type: ignore
    outline = agent._get_outline(chapter_index, volume_index)
    blueprint = build_chapter_blueprint(
        v1_state, directive,
        outline_goal=outline.get("goal", "继续推进故事"),
        total_words=word_quota,
    )
    directive.blueprint = blueprint
    return directive


def node_write_draft(state: ChapterState) -> dict:
    """初稿。第一次进入跑 v1 write_chapter；cycle 回头时不会经过这里（直接 revise）。"""
    proj = state.chapter_directive.get("project_id", "")
    if not proj:
        raise RuntimeError("ChapterState.chapter_directive 必须含 project_id")
    ensure_v1_env(proj)

    from checkpoint import load_state  # type: ignore
    v1 = load_state()
    if v1 is None:
        raise RuntimeError(f"项目 {proj} 没有 v1 state——先跑完 G1-G4 + 卷级")

    directive = _build_directive_and_blueprint(
        v1, state.chapter_index, state.volume_index, state.word_quota,
    )

    # 读上章末尾（衔接）
    from director import DirectorAgent  # type: ignore
    agent = DirectorAgent(resume=True)
    agent.state = v1
    prev_tail = agent._get_prev_chapter_tail(state.chapter_index, state.volume_index)

    from agents.writer import write_chapter  # type: ignore
    print(f"  ▶ [Ch{state.chapter_index}·write_draft] 调 v1 writer.write_chapter ({state.word_quota} 字)")
    draft = write_chapter(v1, directive, state.word_quota, prev_tail=prev_tail)
    print(f"  ✓ [Ch{state.chapter_index}·write_draft] 初稿 {len(draft)} 字")
    return {
        "draft": draft,
        "word_count": len(draft),
        "revision_round": 0,
        # 把 directive 序列化进 state 供后续 critic / revise 用（避免每轮重建）
        "chapter_directive": {**state.chapter_directive,
                                "_directive_built": True,
                                "_volume": state.volume_index,
                                "_chapter": state.chapter_index},
    }


def node_critic_review(state: ChapterState) -> dict:
    """审校。调 v1 critic.review_chapter。"""
    proj = state.chapter_directive.get("project_id", "")
    ensure_v1_env(proj)
    from checkpoint import load_state  # type: ignore
    v1 = load_state()
    directive = _build_directive_and_blueprint(
        v1, state.chapter_index, state.volume_index, state.word_quota,
    )

    next_round = state.revision_round + 1
    from agents.critic import review_chapter  # type: ignore
    print(f"  ▶ [Ch{state.chapter_index}·critic_review] 第 {next_round} 轮 调 v1 critic")
    review = review_chapter(v1, directive, state.draft)
    score = int(review.get("score", 0) or 0)
    passed = bool(review.get("passed", False))
    issues = list(review.get("issues", []) or [])
    feedback = review.get("feedback", "") or ""
    print(f"  ✓ [Ch{state.chapter_index}·critic_review] 第 {next_round} 轮：score={score}/10 passed={passed}"
          f"  issues={len(issues)}")
    return {
        "revision_round": next_round,
        "review_score": score,
        "review_passed": passed,
        "review_issues": [str(i)[:120] for i in issues[:5]],
        "review_feedback": feedback,
    }


def node_revise(state: ChapterState) -> dict:
    """带 feedback 改稿。调 v1 writer.revise_chapter。"""
    proj = state.chapter_directive.get("project_id", "")
    ensure_v1_env(proj)
    from checkpoint import load_state  # type: ignore
    v1 = load_state()
    directive = _build_directive_and_blueprint(
        v1, state.chapter_index, state.volume_index, state.word_quota,
    )

    from agents.writer import revise_chapter  # type: ignore
    print(f"  ▶ [Ch{state.chapter_index}·revise] 调 v1 writer.revise_chapter (轮 {state.revision_round})")
    new_draft = revise_chapter(v1, directive, state.draft, state.review_feedback)
    # 长度兜底：若 v1 输出过短（<70% 原稿），保留原稿
    if not new_draft or len(new_draft) < int(0.7 * len(state.draft)):
        print(f"  ⚠ revise 输出过短 ({len(new_draft)}/{len(state.draft)})——保留原稿")
        return {}
    print(f"  ✓ [Ch{state.chapter_index}·revise] 修订后 {len(new_draft)} 字")
    return {"draft": new_draft, "word_count": len(new_draft)}


def node_finalize(state: ChapterState) -> dict:
    """收尾：写盘 + 标 finalize 原因。"""
    proj = state.chapter_directive.get("project_id", "")
    ensure_v1_env(proj)

    # 写盘到 v2/projects/<proj>/vol{V:02d}/chapter_{C:04d}.txt
    import os
    import project_context as pctx  # type: ignore
    vol_dir = f"{pctx.project_dir()}/vol{state.volume_index:02d}"
    os.makedirs(vol_dir, exist_ok=True)
    path = f"{vol_dir}/chapter_{state.chapter_index:04d}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(state.draft)

    reason = (f"passed at round {state.revision_round}"
              if state.review_passed
              else f"max_rounds_exceeded ({state.max_rounds})")
    print(f"  ✓ [Ch{state.chapter_index}·finalize] 写入 {path}  reason={reason}")
    return {"finalized": True, "finalize_reason": reason}
