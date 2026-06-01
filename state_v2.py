"""V2 State —— Pydantic 模型，作为 LangGraph 的 state schema。

设计原则：
  · 阶段 1 只覆盖 G1 意图组（-1/0/0.5/0.6）产出的 6 个字段。
  · 产物用 dict 存（不重建一堆嵌套 Pydantic 子类）—— 让 LangGraph checkpointer
    序列化最简单（JSON 直接落 SQLite）。阶段 2-3 字段会扩展。
  · `phases_done` 记录已完成 phase id，对应 LangGraph 的"断点恢复"语义。

阶段 1 - G1 输入/输出字段一览：
  输入：project_id / title / genre / theme / intent_description
  -1 产出  → creative_intent
  0  产出  → concept_pitch / trope_library / tone_manual
  0.5 产出 → master_outline
  0.6 产出 → protagonist_journey（部分：overall_theme / fatal_flaw / 等核心字段）
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class NovelStateV2(BaseModel):
    """LangGraph 主 state。每个节点接收它、返回 dict 形式的 patch。"""

    # ── 项目元数据（启动时设进来）──
    project_id: str = ""
    title: str = ""
    genre: str = ""
    theme: str = ""
    intent_description: str = ""

    # ── G1 产物（按 phase 顺序填充）──
    creative_intent: dict = Field(default_factory=dict)
    concept_pitch: dict = Field(default_factory=dict)
    trope_library: dict = Field(default_factory=dict)
    tone_manual: dict = Field(default_factory=dict)
    master_outline: dict = Field(default_factory=dict)
    protagonist_journey: dict = Field(default_factory=dict)

    # ── G2 产物（世界组）──
    power_system: dict = Field(default_factory=dict)   # 1A + 1A2 + 2C + 2C2 共同写入
    volumes: list = Field(default_factory=list)        # 1B
    factions: list = Field(default_factory=list)       # 1C
    world_setting: dict = Field(default_factory=dict)  # 1D
    world_checklist_gaps: list = Field(default_factory=list)  # 1E
    geography: dict = Field(default_factory=dict)      # 1F
    timeline: dict = Field(default_factory=dict)       # 1G
    economy: dict = Field(default_factory=dict)        # 1H

    # ── G3 产物（人物组）──
    characters: list = Field(default_factory=list)            # 2/2A + 2A2 + 2C 回写 holder
    relationship_web: dict = Field(default_factory=dict)      # 2B
    character_arcs: list = Field(default_factory=list)        # 2D + 2C2 标 ability_trigger
    # 2C 写 power_system.special_abilities；2C2 在它基础上加 lifecycle_nodes + 反向 SP

    # ── G4 产物（情节组）──
    global_lines: list = Field(default_factory=list)          # 3A
    volume_lines: list = Field(default_factory=list)          # 3B
    conflict_ladder: dict = Field(default_factory=dict)       # 3B2
    satisfaction_points: list = Field(default_factory=list)   # 3C + 2C2 反向追加
    rhythm_plans: list = Field(default_factory=list)          # 3D
    emotion_curve: dict = Field(default_factory=dict)         # 3D2
    twist_system: dict = Field(default_factory=dict)          # 3E3
    foreshadow_items: list = Field(default_factory=list)      # 3E
    red_herrings: list = Field(default_factory=list)          # 3E2
    fortunes: list = Field(default_factory=list)              # 3F
    # 3G 写 protagonist_journey.milestones（已存在的字段，累加）

    # ── 进度追踪 ──
    phases_done: list[str] = Field(default_factory=list)
    # 当前正在跑哪个 phase（供 stepwise interrupt 后用户 inspect）
    current_phase: str = ""
    current_phase_label: str = ""

    # 卷级循环游标（1-based）：vol_lifecycle 节点跑完后 +1，conditional edge 判断
    # 是否还有下一卷要跑。等于 len(volumes)+1 即所有卷处理完。
    current_volume_index: int = 1
    # 章级循环游标（全书 1-based，嵌套在卷级 cycle 内）：
    # · chapter_loop_init 把它初始化成"当前卷"的 chapter_start
    # · chapter_advance 节点 +1；> 卷的 chapter_end 时跳回卷级（下一卷或 END）
    current_chapter_index: int = 0
    # 已写完的章节集合（同 v1 progress.chapters，用于断点恢复 + 跳过已写章）
    chapters_done: list[int] = Field(default_factory=list)

    # ── 警告/错误收集（agent 跑出来的问题不阻塞流程但要展示）──
    warnings: list[dict] = Field(default_factory=list)

    class Config:
        # 允许后续扩展字段，不破坏旧 checkpoint
        extra = "allow"
