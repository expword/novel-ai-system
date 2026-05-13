"""
写单章 —— web 触发"只写下一章"或"写指定某章"。

与 rewrite_chapter 的区别：
  · rewrite_chapter：删掉已写章节后重写（带作者反馈）
  · write_one_chapter：写尚未生成的章节（不覆盖已有）

流程：
  1. 确定目标章号（显式指定 / 自动找下一章）
  2. 校验前置：state 在，volumes 已规划，对应章未写过
  3. 版本快照（以防失败后回退）
  4. 调 DirectorAgent._write_one_chapter 写一章
  5. 返回写完的章号 + 字数

注意：同步执行，会阻塞到写完；前端请设好 loading 状态。
"""
from __future__ import annotations
import os


def _next_unwritten(state, progress: dict) -> int:
    """找项目里下一章还没写的章号；没有就 return 0（全写完了）。"""
    done = set(progress.get("chapters", []) or [])
    for vol in state.volumes:
        for ci in range(vol.chapter_start, vol.chapter_end + 1):
            if ci not in done:
                return ci
    return 0


def write_one_chapter(project_id: str, chapter_index: int = 0) -> dict:
    """
    写一章。chapter_index=0 表示自动找下一章。
    返回 {"status", "chapter_index", "word_count", "volume_index"}
    """
    import project_context
    project_context.set_project(project_id)

    from checkpoint import load_state, load_progress
    import version_control

    state = load_state()
    if state is None:
        raise RuntimeError("state.json 不存在——该项目还没完成规划阶段")
    if not state.volumes:
        raise RuntimeError("卷结构未规划，无法写章节；请先跑完规划")

    progress = load_progress()

    # 1. 解析目标章号
    if chapter_index <= 0:
        chapter_index = _next_unwritten(state, progress)
        if chapter_index == 0:
            return {
                "status": "done",
                "chapter_index": 0,
                "word_count": 0,
                "message": "所有章节均已生成，无下一章可写",
            }

    # 2. 已写过？
    if chapter_index in (progress.get("chapters") or []):
        return {
            "status": "already_done",
            "chapter_index": chapter_index,
            "message": f"第 {chapter_index} 章已经写过。如需重写，请用「✍ 重写」按钮",
        }

    # 3. 找对应卷
    vol = None
    for v in state.volumes:
        if v.chapter_start <= chapter_index <= v.chapter_end:
            vol = v
            break
    if not vol:
        raise RuntimeError(f"章号 {chapter_index} 不在任何卷的范围内")

    # 4. 版本快照
    version_control.snapshot(
        state, label=f"before_write_ch{chapter_index}",
        chapter_index=chapter_index,
        notes=f"单章生成前快照",
    )

    # 5. 实例化 director 并写本章
    from director import DirectorAgent
    agent = DirectorAgent(resume=True)
    agent.state.current_volume_index = vol.index
    agent.state.current_chapter_index = chapter_index

    # 本卷的 4 个 pre-writing phase（舞台/节拍/大纲/类型）
    # 如果已完成会跳过；第一次在该卷写章节会触发一次规划
    agent.prepare_volume_planning(vol.index)

    vol_dir = f"{project_context.project_dir()}/vol{vol.index:02d}"
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

    # 6. 读回字数（中文小说标准——汉字+英文word+数字，不含标点空格）
    from state import count_chapter_words
    path = f"{vol_dir}/chapter_{chapter_index:04d}.txt"
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
