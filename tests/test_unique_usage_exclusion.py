"""user_models UNIQUE_USAGES 互斥 + llm_call facade 路由 回归测试。

不真发 LLM——只测 routing 表 + 互斥逻辑。
"""
import unittest
import tempfile
import os
import json
import shutil


class TestLLMCallRouting(unittest.TestCase):
    """llm_call.TASK_USAGE 映射 + get_task_routing_summary 行为。"""

    def test_all_known_tasks_covered(self):
        from llm_layer.llm_call import TASK_USAGE, TASK_DESCRIPTIONS
        # 每个 task 都要有 description
        for task in TASK_USAGE:
            self.assertIn(task, TASK_DESCRIPTIONS, f"task {task} 缺 description")

    def test_unknown_task_raises(self):
        from llm_layer.llm_call import _resolve_usage
        with self.assertRaises(ValueError):
            _resolve_usage("unknown_task")

    def test_routing_summary_structure(self):
        from llm_layer.llm_call import get_task_routing_summary
        s = get_task_routing_summary()
        # 至少包含核心 task
        for task in ["writing", "extraction", "review"]:
            self.assertIn(task, s)
            self.assertIn("usage", s[task])


class TestUniqueUsageExclusion(unittest.TestCase):
    """UNIQUE_USAGES 互斥——勾上某 unique usage 自动从其他 model 移除。"""

    def setUp(self):
        # 用临时 user_models.json 隔离测试
        self.tmp = tempfile.mkdtemp(prefix="usage_test_")
        self.json_path = os.path.join(self.tmp, "user_models.json")
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump({"models": [
                {"id": "model_a", "display_name": "A", "base_url": "http://a",
                 "api_key": "ka", "model": "ma", "usage": ["main", "fallback"]},
                {"id": "model_b", "display_name": "B", "base_url": "http://b",
                 "api_key": "kb", "model": "mb", "usage": ["reviewer"]},
            ]}, f)
        # patch STORAGE_PATH
        from llm_layer import user_models
        self._orig_path = user_models.STORAGE_PATH
        user_models.STORAGE_PATH = self.json_path

    def tearDown(self):
        from llm_layer import user_models
        user_models.STORAGE_PATH = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_update_main_strips_from_old_model(self):
        """给 model_b 加 main usage → model_a 的 main 应被自动移除。"""
        from llm_layer import user_models
        user_models.update("model_b", {"usage": ["main", "reviewer"]})
        all_m = user_models.list_all()
        m_a = next(m for m in all_m if m["id"] == "model_a")
        m_b = next(m for m in all_m if m["id"] == "model_b")
        self.assertNotIn("main", m_a["usage"], "model_a 应该被自动移除 main")
        self.assertIn("fallback", m_a["usage"], "fallback 不互斥，应保留")
        self.assertIn("main", m_b["usage"])

    def test_fallback_not_exclusive(self):
        """fallback 不在 UNIQUE_USAGES——两个 model 都可以同时勾。"""
        from llm_layer import user_models
        user_models.update("model_b", {"usage": ["reviewer", "fallback"]})
        all_m = user_models.list_all()
        m_a = next(m for m in all_m if m["id"] == "model_a")
        m_b = next(m for m in all_m if m["id"] == "model_b")
        self.assertIn("fallback", m_a["usage"])
        self.assertIn("fallback", m_b["usage"])

    def test_add_with_unique_strips_old(self):
        """新加 model 带 unique usage → 老 model 的同 usage 被剥。"""
        from llm_layer import user_models
        user_models.add({
            "display_name": "C", "base_url": "http://c", "api_key": "kc",
            "model": "mc", "usage": ["main"],
        })
        all_m = user_models.list_all()
        m_a = next(m for m in all_m if m["id"] == "model_a")
        self.assertNotIn("main", m_a["usage"], "新加的占了 main，model_a 应失去 main")


if __name__ == "__main__":
    unittest.main()
