"""utils/agent_contract 回归测试。

覆盖：
  · get_path 嵌套字段 / 列表通配
  · is_field_present 缺失检测
  · validate_contract 跑 invariants
  · register 注册到全局表
"""
import unittest
from tests._helpers import make_minimal_state


class TestGetPath(unittest.TestCase):
    def setUp(self):
        from utils.agent_contract import get_path
        self.get_path = get_path
        self.state = make_minimal_state(dynasty="测试朝", real_ai_asset=True)

    def test_top_level(self):
        self.assertEqual(self.get_path(self.state, "title"), "测试书")

    def test_nested_dataclass(self):
        self.assertEqual(self.get_path(self.state, "world_canon.dynasty_name"), "测试朝")

    def test_list_wildcard(self):
        names = self.get_path(self.state, "characters[*].name")
        self.assertEqual(names, ["测试主角"])

    def test_missing_returns_none(self):
        self.assertIsNone(self.get_path(self.state, "no_such_field"))

    def test_deep_missing_returns_none(self):
        self.assertIsNone(self.get_path(self.state, "world_canon.no_field"))


class TestIsFieldPresent(unittest.TestCase):
    def test_filled_returns_true(self):
        from utils.agent_contract import is_field_present
        state = make_minimal_state(dynasty="测试朝")
        self.assertTrue(is_field_present(state, "world_canon.dynasty_name"))

    def test_empty_string_returns_false(self):
        from utils.agent_contract import is_field_present
        state = make_minimal_state()  # 无 dynasty
        # world_canon 字段存在但 dynasty_name=""
        self.assertFalse(is_field_present(state, "world_canon.dynasty_name"))

    def test_missing_path_returns_false(self):
        from utils.agent_contract import is_field_present
        state = make_minimal_state()
        self.assertFalse(is_field_present(state, "no_such_field"))


class TestValidateContract(unittest.TestCase):
    def test_missing_input_reported(self):
        from utils.agent_contract import AgentContract, validate_contract
        state = make_minimal_state()  # 缺 dynasty
        ct = AgentContract(
            name="test", inputs=["world_canon.dynasty_name", "no_such_field"],
        )
        issues = validate_contract(ct, state)
        kinds = [i.get("kind") for i in issues]
        # 两个字段都缺/为空
        self.assertEqual(kinds.count("missing_input"), 2)

    def test_invariant_runs(self):
        from utils.agent_contract import AgentContract, validate_contract
        state = make_minimal_state()
        ct = AgentContract(
            name="test", inputs=[],
            invariants=[
                lambda s: [{"severity": "warn", "message": "inv1"}],
                lambda s: [{"severity": "error", "message": "inv2"}],
            ],
        )
        issues = validate_contract(ct, state)
        messages = [i.get("message") for i in issues]
        self.assertIn("inv1", messages)
        self.assertIn("inv2", messages)

    def test_invariant_exception_handled(self):
        from utils.agent_contract import AgentContract, validate_contract
        def bad_inv(s):
            raise RuntimeError("boom")
        state = make_minimal_state()
        ct = AgentContract(name="test", invariants=[bad_inv])
        issues = validate_contract(ct, state)
        # 异常被捕获且报告，不抛出
        kinds = [i.get("kind") for i in issues]
        self.assertIn("invariant_exception", kinds)


class TestRegister(unittest.TestCase):
    def test_register_returns_contract(self):
        from utils.agent_contract import AgentContract, register, all_contracts
        ct = AgentContract(name="test.uniq_xyz", inputs=[])
        returned = register(ct)
        self.assertIs(returned, ct)
        self.assertIn("test.uniq_xyz", all_contracts())


if __name__ == "__main__":
    unittest.main()
