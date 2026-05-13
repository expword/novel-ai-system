import sys
import os

# 在 import 任何会读路径的模块之前，先根据 --project / 环境变量固定项目
# （project_context 在 import 时读 XIAOSHUO_PROJECT_ID；这里额外支持命令行参数）
_proj_arg = None
for i, a in enumerate(sys.argv):
    if a == "--project" and i + 1 < len(sys.argv):
        _proj_arg = sys.argv[i + 1]
        break
    if a.startswith("--project="):
        _proj_arg = a.split("=", 1)[1]
        break
if _proj_arg:
    os.environ["XIAOSHUO_PROJECT_ID"] = _proj_arg

from project_mgmt import project_context  # 触发路径初始化

from core.director import DirectorAgent

if __name__ == "__main__":
    # 用法：
    #   python main.py                        → 用默认项目 "main"（或环境变量）
    #   python main.py --project myproj       → 用指定项目
    #   python main.py --fresh                → 清本项目断点重跑
    fresh = "--fresh" in sys.argv
    print(f"  📚 项目：{project_context.current()}")
    agent = DirectorAgent(resume=not fresh)
    try:
        agent.run()
    except SystemExit:
        raise  # stepwise 主动退出，不当成错误
    except BaseException as _e:
        # 任何未捕获异常——traceback 同时写到 progress_status 的 warning
        # 让前端能看到崩在哪里，不只是"按钮变可点"
        import traceback
        tb_short = traceback.format_exception_only(type(_e), _e)[-1].strip()
        print(f"\n!! 子进程崩溃：{tb_short}")
        traceback.print_exc()
        try:
            from persistence.checkpoint import add_progress_warning
            add_progress_warning(
                level="error",
                source="director:crash",
                message=f"子进程异常退出：{tb_short}（详见 stdout.log）",
            )
        except Exception:
            pass
        # 清 pid 让 status 回到 idle
        try:
            os.remove(project_context.pid_file())
        except OSError:
            pass
        raise
