"""测试辅助——构造最小 NovelState、跳过 sys.path 配置等。

所有测试 import `from tests._helpers import make_minimal_state` 即可。
不引入项目数据 / 项目术语（按 [[feedback_generic_prompts]]）——
让每个测试纯粹验证模块逻辑，不耦合任何具体小说项目。

═══ 测试项目隔离 ═══

import 本 helper 时**强制设 XIAOSHUO_PROJECT_ID** 到一个临时目录——
否则 add_progress_warning 等 IO 操作会用 project_context.DEFAULT_PROJECT_ID="main"，
把测试副作用写到 projects/main/ 真实目录里（历史 bug：用户 web UI 刷新看到无来由的
"main" 项目）。

atexit 注册自动清理临时目录，跑完测试不留垃圾。
"""
from __future__ import annotations
import sys
import os
import tempfile
import atexit
import shutil

# 让 tests/ 可以 import 项目根的模块
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

# 测试项目隔离——若未显式设 env，自动指向 tmp 目录；跑完 atexit 清掉
if not os.environ.get("XIAOSHUO_PROJECT_ID"):
    _TEST_PROJ_DIR = tempfile.mkdtemp(prefix="xiaoshuo_test_")
    _TEST_PROJ_ID = "__pytest_isolated__"
    os.environ["XIAOSHUO_PROJECT_ID"] = _TEST_PROJ_ID
    # 把 projects/<id> 指向 tmp（不动 PROJECTS_ROOT，只 patch 单个 project 的目录解析）
    # 通过 project_context 的内部 override 实现
    try:
        from project_mgmt import project_context as _pc
        # 直接 patch _override_project_dir（若 project_context 支持）；不支持就靠 env 路由
        if hasattr(_pc, "_OVERRIDE_PROJECT_DIR"):
            _pc._OVERRIDE_PROJECT_DIR = _TEST_PROJ_DIR
    except Exception:
        pass

    def _cleanup_test_proj():
        try:
            # 清 tmp 目录
            if os.path.isdir(_TEST_PROJ_DIR):
                shutil.rmtree(_TEST_PROJ_DIR, ignore_errors=True)
            # 同时清 projects/__pytest_isolated__/ 如果被物化了
            stray = os.path.join(_PROJ_ROOT, "projects", _TEST_PROJ_ID)
            if os.path.isdir(stray):
                shutil.rmtree(stray, ignore_errors=True)
        except Exception:
            pass
    atexit.register(_cleanup_test_proj)


def make_minimal_state(
    *,
    dynasty: str = "",
    region: str = "",
    real_ai_asset: bool = False,
    asset_name: str = "测试金手指",
    asset_profile: str = "test_profile",
):
    """构造最小 NovelState 供测试用。

    dynasty/region 非空时填充 world_canon。
    real_ai_asset=True 时加一个绑了 LLM profile 的 SpecialAbility。
    """
    from persistence.state import (
        NovelState, WorldCanon, PowerSystem, SpecialAbility, Character, CharacterRole,
    )
    state = NovelState(title="测试书", genre="测试题材", theme="测试主题")
    if dynasty or region:
        state.world_canon = WorldCanon(
            dynasty_name=dynasty,
            region_root=region,
            canonical_aliases=[dynasty[:2]] if len(dynasty) >= 2 else [],
        )
    state.power_system = PowerSystem(system_name="", system_description="", realms=[])
    if real_ai_asset:
        state.power_system.special_abilities = [
            SpecialAbility(
                name=asset_name,
                source="测试来源",
                description="测试描述",
                unlock_condition="测试条件",
                holder_role="主角自身",
                external_llm_profile=asset_profile,
            )
        ]
    state.characters = [
        Character(
            name="测试主角",
            role=CharacterRole("主角"),  # 用 value lookup 避免源码中文属性
            gender="", age_desc="", appearance="", personality="",
            personality_detail="", background="", trauma="", desire="",
            fear="", speech_pattern="", ability="", realm="",
            arc="", motivation="", fatal_flaw="",
            first_volume=1, last_volume=-1,
        )
    ]
    return state
