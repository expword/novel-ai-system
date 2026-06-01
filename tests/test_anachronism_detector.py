"""tests for agents.anachronism_detector."""
from __future__ import annotations
import unittest
from unittest.mock import patch

from tests._helpers import make_minimal_state
from agents import anachronism_detector as ad
from agents.anachronism_detector import AnachronismIssue


class _CreativeIntent:
    """轻量 stub。"""
    def __init__(self, subgenre="", reality_basis="", raw=""):
        self.suggested_subgenre = subgenre
        self.reality_basis = reality_basis
        self.raw_description = raw


class TestIsApplicable(unittest.TestCase):
    def test_穿越_subgenre_applicable(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        self.assertTrue(ad.is_applicable(state))

    def test_重生_subgenre_applicable(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="重生")
        self.assertTrue(ad.is_applicable(state))

    def test_reality_basis_applicable(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(reality_basis="主角穿越到古代")
        self.assertTrue(ad.is_applicable(state))

    def test_raw_description_fallback(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(raw="一个程序员穿越成了王爷")
        self.assertTrue(ad.is_applicable(state))

    def test_仙侠_not_applicable(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="仙侠")
        self.assertFalse(ad.is_applicable(state))

    def test_no_creative_intent_not_applicable(self):
        state = make_minimal_state()
        state.creative_intent = None
        self.assertFalse(ad.is_applicable(state))


class TestFastFilter(unittest.TestCase):
    def test_no_modern_words_returns_empty(self):
        text = "他抬头看着月光,叹了口气。这冷天难捱。"
        self.assertEqual(ad.fast_filter(text), [])

    def test_detects_gdp(self):
        text = "主角心想:这个国家的 GDP 还没到农业社会水平。"
        hits = ad.fast_filter(text)
        self.assertTrue(any(t == "GDP" for t, _ in hits))

    def test_detects_multiple(self):
        text = "微积分 是什么? 量子 又是什么? 区块链 呢?"
        hits = ad.fast_filter(text)
        terms = {t for t, _ in hits}
        self.assertIn("微积分", terms)
        self.assertIn("量子", terms)
        self.assertIn("区块链", terms)

    def test_caps_at_30(self):
        text = "GDP " * 100
        hits = ad.fast_filter(text)
        self.assertLessEqual(len(hits), 30)


class TestAuditChapter(unittest.TestCase):
    def test_returns_empty_if_not_applicable(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="仙侠")
        with patch.object(ad, "request_json_with_profile") as mock:
            issues = ad.audit_chapter(state, 5, "GDP " * 200)
        self.assertEqual(issues, [])
        mock.assert_not_called()

    def test_skips_llm_when_no_hits(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        with patch.object(ad, "request_json_with_profile") as mock:
            issues = ad.audit_chapter(state, 5, "他看着月亮叹了口气。" * 30)
        self.assertEqual(issues, [])
        mock.assert_not_called()

    def test_returns_violations_from_llm(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        fake = {"violations": [
            {"term": "GDP", "excerpt": "...GDP 还没到...",
             "reason": "主角对古人说出", "severity": "critical",
             "suggestion": "换为'国民产值'"}
        ]}
        with patch.object(ad, "request_json_with_profile", return_value=fake):
            issues = ad.audit_chapter(state, 5, "他说:GDP 还没起色。" + "x" * 200)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].term, "GDP")
        self.assertEqual(issues[0].severity, "critical")

    def test_llm_failure_returns_empty(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        with patch.object(ad, "request_json_with_profile",
                          side_effect=RuntimeError("fail")):
            issues = ad.audit_chapter(state, 5, "他说:GDP 还没起色。" + "x" * 200)
        self.assertEqual(issues, [])

    def test_skips_short_text(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        with patch.object(ad, "request_json_with_profile") as mock:
            issues = ad.audit_chapter(state, 5, "GDP")
        self.assertEqual(issues, [])
        mock.assert_not_called()


class TestAuditAndSurface(unittest.TestCase):
    def test_no_issues_doesnt_crash(self):
        state = make_minimal_state()
        state.creative_intent = _CreativeIntent(subgenre="穿越")
        with patch.object(ad, "request_json_with_profile",
                          return_value={"violations": []}):
            issues = ad.audit_and_surface(state, 5, "GDP " + "x" * 200)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
