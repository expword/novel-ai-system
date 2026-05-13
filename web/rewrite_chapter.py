"""
章节重写 —— 删除旧稿 + 带作者反馈重新生成。

流程：
  1. 读当前 state
  2. 版本快照（留后路）
  3. 从 completed_chapters 删除 + 从 progress 删除
  4. 删除章节 txt 文件
  5. 清理按章派生状态（cleanup_chapter_state）——防止旧稿记忆污染新稿
  6. 实例化 DirectorAgent，调 _write_one_chapter（directive.user_feedback 带作者反馈）
  7. 反馈贯穿 chapter_planner 和 writer 所有 prompt
"""
from __future__ import annotations
import os


def rewrite_chapter(project_id: str, chapter_index: int,
                     user_feedback: str = "") -> dict:
    """
    重写一章。同步执行（阻塞直到写完）。
    返回 {"status": "ok", "chapter_index": ..., "word_count": ...}
    """
    from project_mgmt import project_context
    project_context.set_project(project_id)

    from persistence.checkpoint import load_state, save_state, load_progress, _save_progress
    from persistence import version_control

    state = load_state()
    if state is None:
        raise RuntimeError("state.json 不存在——该项目还没开始写")

    vol = None
    for v in state.volumes:
        if v.chapter_start <= chapter_index <= v.chapter_end:
            vol = v
            break
    if not vol:
        raise RuntimeError(f"找不到第 {chapter_index} 章对应的卷")

    # 1. 版本快照
    version_control.snapshot(
        state, label=f"before_rewrite_ch{chapter_index}",
        chapter_index=chapter_index,
        notes=f"重写前快照（feedback={user_feedback[:60]}）",
    )

    # 2. 从 completed_chapters 删除
    state.completed_chapters = [c for c in state.completed_chapters if c.index != chapter_index]

    # 3. 从 progress 删除
    progress = load_progress()
    progress["chapters"] = [c for c in progress["chapters"] if c != chapter_index]
    _save_progress(progress)

    # 4. 删除章节文件
    vol_dir = f"{project_context.project_dir()}/vol{vol.index:02d}"
    path = f"{vol_dir}/chapter_{chapter_index:04d}.txt"
    if os.path.exists(path):
        os.remove(path)

    # 5. 清理按章派生状态（memory / 快照 / 世界事件 / 爽点触发 / 伏笔回收 等）
    #    不清会导致旧稿的记忆和状态污染新稿
    from persistence.chapter_cleanup import cleanup_chapter_state
    cleanup_chapter_state(state, {chapter_index})

    save_state(state)

    # 6. 实例化 director，写本章
    from core.director import DirectorAgent
    agent = DirectorAgent(resume=True)
    # 把作者反馈临时挂到 state 上，director 的 _generate_directive
    # 会把它写进 directive.user_feedback
    agent._rewrite_feedback_for_chapter = {chapter_index: user_feedback}
    agent.state.current_volume_index = vol.index
    agent.state.current_chapter_index = chapter_index

    # 跑卷级规划——新加的 phase（如 4_lifecycle_X 把粗粒度 lifecycle 节点细化到章）
    # 必须在写章前确保完成。已完成的 phase 内部会跳过。
    agent.prepare_volume_planning(vol.index)

    # 确保卷输出目录存在
    os.makedirs(vol_dir, exist_ok=True)

    # try/finally：in-process 写章 director 在 __init__ 写了 running.pid，
    # 这里要在写完后清掉，否则 web 进程长期不退出就让 status() 永远显示 running
    try:
        agent._write_one_chapter(chapter_index, vol.index)
    finally:
        try:
            os.remove(project_context.pid_file())
        except OSError:
            pass

    # 读回字数（中文小说标准——汉字+英文word+数字，不含标点空格）
    from persistence.state import count_chapter_words
    word_count = 0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            word_count = count_chapter_words(f.read())

    return {
        "status": "ok",
        "chapter_index": chapter_index,
        "word_count": word_count,
        "volume_index": vol.index,
    }
