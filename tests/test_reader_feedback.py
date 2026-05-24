"""读者反馈闭环 (Batch 5) 回归测试 —— comment_simulator + expectation_manager。

覆盖:
  · SimulatedComment / ReaderExpectation dataclass 字段 + 默认值
  · ChapterSummary.simulated_comments 默认 [] 向后兼容
  · ChapterDirective.reader_expectations 默认 [] 向后兼容
  · _load_chapter_summary 容忍旧 state.json 无 simulated_comments
  · expectation_manager.format_expectations_for_prompt 字符串拼接
  · expectation_manager.predict_reader_expectations 第 1 章跳过(无前章基础)

测试不发真 LLM 调用。
"""
import unittest
from tests._helpers import make_minimal_state


class TestSimulatedCommentDataclass(unittest.TestCase):
    def test_construct_with_defaults(self):
        from persistence.state import SimulatedComment
        c = SimulatedComment(reader_type="追读派", nickname="网友A", text="主角太惨了催更")
        self.assertEqual(c.sentiment, "neutral")

    def test_construct_with_sentiment(self):
        from persistence.state import SimulatedComment
        c = SimulatedComment(reader_type="挑刺派", nickname="老书虫",
                              text="设定漏洞", sentiment="critical")
        self.assertEqual(c.sentiment, "critical")


class TestReaderExpectationDataclass(unittest.TestCase):
    def test_default_decision_empty(self):
        from persistence.state import ReaderExpectation
        e = ReaderExpectation(expectation="反派会动手", based_on="末钩子")
        self.assertEqual(e.decision, "")

    def test_construct_with_decision(self):
        from persistence.state import ReaderExpectation
        e = ReaderExpectation(expectation="反派会动手", based_on="末钩子", decision="reverse")
        self.assertEqual(e.decision, "reverse")


class TestChapterSummaryBackwardCompat(unittest.TestCase):
    def test_default_simulated_comments_empty(self):
        from persistence.state import ChapterSummary, TensionLevel
        s = ChapterSummary(
            index=1, volume_index=1, title="t", summary="x",
            word_count=3000, tension=TensionLevel.RISING,
        )
        self.assertEqual(s.simulated_comments, [])

    def test_load_chapter_summary_no_comments_key(self):
        from persistence.checkpoint import _load_chapter_summary
        d = {
            "index": 5, "volume_index": 1, "title": "t",
            "summary": "x", "word_count": 3000, "tension": "RISING",
            # 没 simulated_comments 字段
        }
        s = _load_chapter_summary(d)
        self.assertEqual(s.simulated_comments, [])

    def test_load_chapter_summary_with_comments(self):
        from persistence.checkpoint import _load_chapter_summary
        d = {
            "index": 5, "volume_index": 1, "title": "t",
            "summary": "x", "word_count": 3000, "tension": "RISING",
            "simulated_comments": [
                {"reader_type": "追读派", "nickname": "网友A",
                 "text": "催更", "sentiment": "positive"},
                {"reader_type": "挑刺派", "nickname": "书虫",
                 "text": "节奏太慢", "sentiment": "critical"},
            ],
        }
        s = _load_chapter_summary(d)
        self.assertEqual(len(s.simulated_comments), 2)
        self.assertEqual(s.simulated_comments[0].reader_type, "追读派")
        self.assertEqual(s.simulated_comments[1].sentiment, "critical")


class TestChapterDirectiveExpectations(unittest.TestCase):
    def test_default_reader_expectations_empty(self):
        from persistence.state import ChapterDirective, TensionLevel, RhythmType
        d = ChapterDirective(
            chapter_index=5, volume_index=1,
            tension=TensionLevel.RISING, rhythm=RhythmType.SLOW_BUILD,
            active_lines=[], primary_line="",
            must_include=[], satisfaction_points=[],
            foreshadow_plant=[], foreshadow_resolve=[],
            emotional_note="", chapter_position="中段", word_pace="中等",
        )
        self.assertEqual(d.reader_expectations, [])


class TestExpectationManagerHelpers(unittest.TestCase):
    """expectation_manager 纯逻辑函数."""

    def test_format_empty_returns_empty(self):
        from agents.expectation_manager import format_expectations_for_prompt
        self.assertEqual(format_expectations_for_prompt([]), "")

    def test_format_includes_expectation_and_based_on(self):
        from agents.expectation_manager import format_expectations_for_prompt
        from persistence.state import ReaderExpectation
        out = format_expectations_for_prompt([
            ReaderExpectation(
                expectation="反派会暗中布局",
                based_on="第 3 章末:反派说'我已经'",
            ),
        ])
        self.assertIn("反派会暗中布局", out)
        self.assertIn("反派说", out)
        self.assertIn("satisfy", out)
        self.assertIn("reverse", out)
        self.assertIn("stack", out)

    def test_format_shows_existing_decision(self):
        from agents.expectation_manager import format_expectations_for_prompt
        from persistence.state import ReaderExpectation
        out = format_expectations_for_prompt([
            ReaderExpectation(
                expectation="主角会重伤",
                based_on="末钩子",
                decision="reverse",
            ),
        ])
        # 已有 decision 应当显示出来
        self.assertIn("reverse", out)

    def test_predict_chapter_one_returns_empty(self):
        """第 1 章没有前章作为基础——直接返回 []."""
        from agents.expectation_manager import predict_reader_expectations
        state = make_minimal_state()
        out = predict_reader_expectations(state, chapter_index=1)
        self.assertEqual(out, [])

    def test_predict_no_previous_chapters_returns_empty(self):
        """state.completed_chapters 为空 → 跳过."""
        from agents.expectation_manager import predict_reader_expectations
        state = make_minimal_state()
        # chapter_index > 1 但无任何 completed_chapters
        out = predict_reader_expectations(state, chapter_index=5)
        self.assertEqual(out, [])


class TestPromptsRegistryEntries(unittest.TestCase):
    def test_comment_simulator_registered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertIn("agents.comment_simulator:SYSTEM", ids)

    def test_expectation_manager_registered(self):
        from utils.prompts_registry import all_entries
        ids = {e.id for e in all_entries()}
        self.assertIn("agents.expectation_manager:SYSTEM", ids)


class TestCommentSimulatorEmptyContent(unittest.TestCase):
    def test_short_content_returns_empty(self):
        """正文 < 200 字 → 直接返回 [],不调 LLM."""
        from agents.comment_simulator import simulate_comments
        state = make_minimal_state()
        out = simulate_comments(state, chapter_index=1, content="短")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
