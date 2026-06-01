# tests/

stdlib `unittest` 套件——不引入新依赖。

跑全部：

```bash
python -m unittest discover tests
```

跑单个：

```bash
python -m unittest tests.test_canon_checker
python -m unittest tests.test_revise_loop -v
```

## 覆盖范围

| 文件 | 覆盖模块 | 关键 case |
|---|---|---|
| `test_canon_checker.py` | `agents/canon_checker.py` | dynasty_name_mismatch / real_ai_dangerous_command / source 分流 / 占位检查 |
| `test_world_canon_extractor.py` | `agents/world_canon_extractor.py` | source_hash 幂等 / 空 world_setting 跳过 |
| `test_downstream_staleness.py` | `agents/downstream_staleness.py` | 扫描合规 outline 0 issue / 扫违规聚合写 warning |
| `test_revise_loop.py` | `core/revise_loop.py` | clean exit / max_rounds / 短路连续退出 / on_residual 回调 |
| `test_agent_contract.py` | `utils/agent_contract.py` | get_path 嵌套 / 列表通配 / 缺失检测 / invariant 执行 |

## 设计原则

- **不 mock LLM**——所有规则/算法路径都纯函数可测；涉及 LLM 的 agent（如 world_canon_extractor）通过依赖注入跳过实际调用
- **不依赖项目数据**——每个 test 自己构造最小 NovelState，按 [[feedback_generic_prompts]] 避开项目术语
- **每模块一文件**——失败时 `python -m unittest tests.test_X -v` 即可定位

未来加新模块请按此模式建 `tests/test_<module>.py`。
