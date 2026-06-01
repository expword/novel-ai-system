# A1 三块巨石重构 ROADMAP

> 起因：陌生视角审查时定位的"演化到中期规模"症状。三块石头：
> - `core/director.py` **3110 行 / 60 函数**
> - `persistence/state.py` **2773 行 / 194 个 dataclass**
> - `persistence/checkpoint.py` **1472 行 / 80 个 `_load_xxx`**
>
> 一次性大改造风险极高。本文档按 ROI / risk / 依赖关系排出**分阶段**蓝图，每个阶段都是
> 可独立交付、独立测试的小步——避免"重构地狱"。
>
> 当前 safety net：`tests/` 40 个回归测试（stdlib unittest，0.1s 跑完）。新拆出来的
> 模块都要补对应 test 文件，覆盖率才能撑住后续重构。

---

## Stage A.1 · `state.py` 拆分（先做——其他重构都依赖它）

**为什么先做**：60+ agent 都 `import` 这个文件。当前 2773 行单文件让 IDE 跳转/搜索/
review 都很慢。拆分后下游引用路径不变（保留 `from persistence.state import X`
shim），但物理结构清晰。

### 拆分方案

按 ARCHITECTURE.md Phase 分组对应拆：

```
persistence/state/
├── __init__.py             ← 重新导出所有名字（向后兼容）
├── core.py                 ← NovelState 主 dataclass
├── concept.py              ← CreativeIntent / ConceptPitch / TropeLibrary / ToneManual / MasterOutline
├── world.py                ← WorldCanon / Geography / Timeline / Economy / Faction
├── power.py                ← Realm / PowerSystem / SpecialAbility / LifecycleNode / AbilityAwakeningStage
├── character.py            ← Character / CharacterRole / Relationship / CharacterStateSnapshot /
│                              CharacterArc / RelationshipWeb / Bond
├── plot.py                 ← Volume / BookStructurePlan / StoryStage / NarrativeLine / LinePhase /
│                              ConflictLadder / ConflictEntry / EmotionCurve
├── beats.py                ← SatisfactionPoint / ForeshadowItem / RedHerring / TwistChain /
│                              TwistLayer / TwistSystem / Fortune / Promise
├── chapter.py              ← ChapterDirective / ChapterSummary / ChapterBlueprint / SceneBeat /
│                              VolumeRhythmPlan / RhythmSegment / VolumeChapterTypeDistribution
└── audit.py                ← AbilityAudit / ReaderAudit / DialogueAudit / WorldEvent / AssetUsage
```

### 实施步骤（每步独立 commit）

1. 建 `persistence/state/__init__.py` 重新导出当前所有 public 名字
2. 把 `state.py` 改名为 `persistence/state_legacy.py`（保留历史），新 `state/__init__.py`
   先 `from persistence.state_legacy import *` —— 此时**没拆**但目录结构就位
3. 跑 tests 确认无 import 错误
4. 一次提取一个子模块（如先 `power.py`：把 Realm/PowerSystem/SpecialAbility/LifecycleNode
   全部 cut/paste，原文件改成 `from .power import *`），跑 tests，commit
5. 重复直到 state_legacy.py 为空
6. 删 state_legacy.py

### 风险与缓解

- **循环引用**：当前文件内 dataclass 互相 reference。拆分时按依赖拓扑（power 不依赖
  character，character 依赖 power）排序，避免循环
- **被 `from persistence.state import *` 大量引用**：先做 step 1 的 shim 兼容旧 import
- **测试**：拆前/拆后跑 `python -m unittest discover tests` 必须全过

**预计工作量**：4-8 小时（按子模块拆，每个 30-60 分钟）

---

## Stage A.2 · `checkpoint.py` 自动序列化

**根因**：每加一个 dataclass 字段就要手写 `_load_xxx` 函数 / 在 `state_storage.py` 注册。
这是 80 个手写 loader 的来源。

### 改造方案

**A.2.a 用 `dataclasses_json` 库或自己写反射式 loader**

```python
# 当前
def _load_world_canon(d: dict) -> WorldCanon:
    return WorldCanon(
        dynasty_name=str(d.get("dynasty_name") or ""),
        era_name=str(d.get("era_name") or ""),
        ...
    )

# 改造后（基于 dataclass.fields 自动反射）
def load_dataclass(cls, d: dict):
    if not isinstance(d, dict):
        return cls()  # 缺失返回默认实例
    kwargs = {}
    for f in fields(cls):
        if f.name in d:
            kwargs[f.name] = _coerce_value(f.type, d[f.name])
    return cls(**kwargs)
```

可省 95% 的 `_load_xxx` 函数。剩 5% 是有复杂嵌套（如 list of dataclass）或类型转换
（如 enum from str）的——单独保留手写。

### 实施步骤

1. 写 `persistence/dataclass_loader.py` —— 通用 `load_dataclass(cls, d)` + 支持
   list/Optional/Enum 嵌套
2. 加 tests/test_dataclass_loader.py —— 覆盖各种 dataclass shape
3. 一次替换一个 `_load_xxx`——先替换简单的（如 _load_world_canon），跑 tests
4. 替换所有 simple loader 后，复杂的（如 _load_master_outline 套娃 character_slots）保留

### 风险与缓解

- **向后兼容**：老 state.json 可能有字段已被删除——loader 要忽略未知字段（不要报错）
- **类型转换**：原 `int(d.get("x") or 0)` 这种容错逻辑要保留——`load_dataclass` 需要
  对 None / 字符串等做 sensible default

**预计工作量**：6-10 小时（写 loader + 替换 + 测试）

---

## Stage A.3 · `director.py` 拆分

**这是风险最高的一块**——director 是写章主管线，任何 bug 都直接影响生成质量。

### 当前结构（按行号扫描）

```
1-300       ConfigDefaults + 工具函数
300-1100    DirectorAgent.__init__ / phase 调度（Phase -1 ~ 3G）
1100-1500   prepare_volume_planning（Phase 4 卷级规划）
1500-2500   _write_one_chapter（Phase 5 章级写作主循环）—— **最复杂**
2500-3000   _generate_directive + 辅助
3000-3110   收尾 + 主程序
```

### 拆分方案

```
core/
├── director.py             ← DirectorAgent class + 入口（瘦身到 < 800 行）
├── phase_planning.py       ← Phase -1 ~ 3G 规划期编排（约 500 行）
├── phase_volume.py         ← prepare_volume_planning（约 350 行）
├── phase_writing.py        ← _write_one_chapter 主循环（约 800 行）
├── chapter_directive.py    ← _generate_directive + 辅助（约 400 行）
├── revise_loop.py          ← (已有) 通用 audit-revise 框架
└── helpers.py              ← _section / _print_block 等小工具
```

### 实施步骤

1. **先建 tests/test_director_phase_writing.py**——给 `_write_one_chapter` 单测覆盖
   核心路径（mock 各 agent，只验证编排顺序）。这是必须的 safety net
2. 提取 `_generate_directive` → `chapter_directive.py`，DirectorAgent 用 `self.gen_directive = ChapterDirectiveFactory(self.state)` 调用
3. 提取 `_write_one_chapter` → `phase_writing.py:write_chapter(state, directive, ...)`
4. 提取 `prepare_volume_planning` → `phase_volume.py`
5. 提取 Phase -1 ~ 3G 规划块（已在 scheduler_tasks.py 部分声明）→ 删 director 里的双轨制

### 风险与缓解

- **方法间 self.state 共享**：拆出去后变 free function，所有 state 引用要显式参数传递
- **`_set_current_step` 等 director 内部回调**：拆出去的 function 需要回调机制（传一个
  `progress_callback` 参数）
- **scheduler 双轨制**：director.py 手写一遍 + scheduler_tasks.py 又一遍——拆 director
  时同步删 director 里那一份

**预计工作量**：12-20 小时（最大、最危险）

---

## 总计：分 3 个 milestone

| Milestone | Stage | 工作量 | 风险 | 收益 |
|---|---|---|---|---|
| M1 | A.1 state 拆分 | 4-8h | 低 | IDE/搜索/review 速度立即提升 |
| M2 | A.2 checkpoint 自动序列化 | 6-10h | 中 | 80 个手写 loader 减 75% |
| M3 | A.3 director 拆分 | 12-20h | 高 | director 单文件 3110 → < 800 |

**总计 22-38 小时**——不应在单次 session 完成。建议每个 Milestone 单独 PR，跑完 tests
+ 实测一次重生章节再合并。

## 决策建议

**M1 现在做** —— 风险低、收益立显、为 M2/M3 铺路
**M2 后续做** —— 中等风险，需要重点测序列化往返
**M3 最后做** —— 必须先有 director 单测覆盖核心路径
