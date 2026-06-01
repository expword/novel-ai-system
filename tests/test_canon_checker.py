"""canon_checker.validate_text 回归测试。

覆盖 4 类规则：
  · dynasty_name_mismatch  ——朝代名漂移
  · real_ai_dangerous_command ——真 AI 被命令查询本书设定
  · external_ai_no_placeholder ——chapter 正文 asset 名出现但没占位
  · source 分流 ——占位检查仅对 chapter:* 跑
"""
import unittest
from tests._helpers import make_minimal_state


class TestDynastyMismatch(unittest.TestCase):
    """world_canon.dynasty_name 锚定后，文本里出现其他"X 朝"应抓为 critical。"""

    def setUp(self):
        from agents.canon_checker import validate_text
        self.validate = validate_text
        self.state = make_minimal_state(dynasty="大雍王朝")

    def _has_dynasty_mismatch(self, text, source="outline:V1Ch1.goal"):
        r = self.validate(self.state, source, text)
        return any(i["kind"] == "dynasty_name_mismatch" for i in r["issues"])

    def test_violation_caught(self):
        self.assertTrue(self._has_dynasty_mismatch("主角穿越成白鹿朝寒门秀才"))

    def test_canon_dynasty_passes(self):
        self.assertFalse(self._has_dynasty_mismatch("主角穿越成大雍王朝寒门秀才"))

    def test_alias_passes(self):
        # 大雍是 canonical_aliases[0]（make_minimal_state 自动取前 2 字）
        self.assertFalse(self._has_dynasty_mismatch("大雍开国三百余年"))

    def test_blocked_prefix_skipped(self):
        # "本朝/前朝/古朝/历史朝" 等修饰词前缀不算朝代
        for text in ["本朝太祖", "前朝旧梦", "历史朝代的兴衰", "今朝有酒"]:
            self.assertFalse(self._has_dynasty_mismatch(text), f"误报: {text}")

    def test_compound_word_skipped(self):
        # "X 朝廷/朝阳/朝堂" 是复合词，不该被当朝代
        for text in ["朝廷上下", "朝阳初升", "朝堂之上"]:
            self.assertFalse(self._has_dynasty_mismatch(text), f"误报: {text}")

    def test_dynasty_mismatch_in_chapter_too(self):
        # 不只 outline——chapter 正文也要查
        self.assertTrue(self._has_dynasty_mismatch("处于白鹿朝景和年间", source="chapter:1"))


class TestRealAIDangerousCommand(unittest.TestCase):
    """文本要求"真 AI asset + 高风险动词" → 抓 critical（outline）或 warn（chapter）。"""

    def setUp(self):
        from agents.canon_checker import validate_text
        self.validate = validate_text
        self.state = make_minimal_state(real_ai_asset=True, asset_name="豆包")

    def test_outline_dangerous_command_critical(self):
        r = self.validate(self.state, "outline:V1Ch1.goal",
                          "通过豆包确认所处朝代")
        hits = [i for i in r["issues"] if i["kind"] == "real_ai_dangerous_command"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "error")

    def test_chapter_dangerous_command_warn(self):
        r = self.validate(self.state, "chapter:1", "豆包确认这是真的")
        hits = [i for i in r["issues"] if i["kind"] == "real_ai_dangerous_command"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "warn")

    def test_no_dangerous_verb_passes(self):
        r = self.validate(self.state, "outline:V1Ch1.goal",
                          "主角发现豆包跟着穿越来了")
        hits = [i for i in r["issues"] if i["kind"] == "real_ai_dangerous_command"]
        self.assertEqual(hits, [])


class TestSourceRouting(unittest.TestCase):
    """external_ai_no_placeholder 只对 chapter:* 跑，outline / blueprint 不误报。"""

    def setUp(self):
        from agents.canon_checker import validate_text
        self.validate = validate_text
        self.state = make_minimal_state(real_ai_asset=True, asset_name="豆包")

    def test_chapter_plain_asset_description_passes(self):
        r = self.validate(self.state, "chapter:1", "豆包的登录页面在他脑海里一闪而过。")
        self.assertFalse(
            any(i["kind"] == "external_ai_no_placeholder" for i in r["issues"])
        )

    def test_chapter_ai_interaction_without_placeholder_critical(self):
        r = self.validate(self.state, "chapter:1", "他向豆包询问复利算法，豆包很快给出答案。")
        self.assertTrue(
            any(i["kind"] == "external_ai_no_placeholder" for i in r["issues"])
        )

    def test_outline_without_placeholder_skipped(self):
        # outline 本来就不该有占位——不该被报"no_placeholder"
        r = self.validate(self.state, "outline:V1Ch1.goal", "本章涉及豆包")
        self.assertFalse(
            any(i["kind"] == "external_ai_no_placeholder" for i in r["issues"])
        )

    def test_chapter_with_placeholder_passes(self):
        r = self.validate(self.state, "chapter:1",
                          "他打开 [[ASK_AI:豆包|古代借贷利率]] 思索良久")
        self.assertFalse(
            any(i["kind"] == "external_ai_no_placeholder" for i in r["issues"])
        )


class TestSystemWindowFormat(unittest.TestCase):
    """【豆包：...】/【系统检测...】/【宿主，...】网文系统弹窗格式 = critical。"""

    def setUp(self):
        from agents.canon_checker import validate_text
        self.validate = validate_text
        self.state = make_minimal_state(real_ai_asset=True, asset_name="豆包")

    def _has_kind(self, text, kind):
        r = self.validate(self.state, "chapter:1", text)
        return any(i["kind"] == kind for i in r["issues"])

    def test_asset_window_caught(self):
        self.assertTrue(self._has_kind(
            "他启动手机后【豆包·分析完成。债务总额：87,643两。】",
            "system_window_format",
        ))

    def test_system_window_caught(self):
        # 文本里有 asset 名（让规则进入）+ 系统弹窗
        self.assertTrue(self._has_kind(
            "豆包在他脑海中。突然【系统检测到债务文书，是否开启分析？】",
            "system_window_format",
        ))

    def test_hostess_prompt_caught(self):
        self.assertTrue(self._has_kind(
            "豆包浮现界面。【宿主，检测到规则压迫，是否开启金融杠杆？】",
            "system_window_format",
        ))

    def test_clean_chapter_passes(self):
        clean = "他打开手机，输入了 [[ASK_AI:豆包|古代借贷常见利率原理]]。等了几秒，豆包给出回答。"
        # 不该报 system_window_format
        self.assertFalse(self._has_kind(clean, "system_window_format"))

    def test_uncovered_interaction_caught(self):
        """AI 交互出现 + 200 字窗口内无占位 → external_ai_no_placeholder critical。"""
        # 章前段有占位，但章末又出现"豆包"独立（无占位伴随，且 200 字外）
        text = (
            "开篇：他打开手机，输入 [[ASK_AI:豆包|古代借贷原理]]。等了片刻。"
            + ("场景描写。" * 60)  # 拉远超 200 字
            + "三日后，豆包再次说话——这次他没用占位。"
        )
        self.assertTrue(self._has_kind(text, "external_ai_no_placeholder"))


class TestBackwardCompat(unittest.TestCase):
    """check_canon(state, chapter_index, content) 老签名要继续工作。"""

    def test_check_canon_wrapper(self):
        from agents.canon_checker import check_canon
        state = make_minimal_state(real_ai_asset=True, asset_name="豆包")
        rep = check_canon(state, 5, "豆包出现在第5章")
        self.assertEqual(rep.get("chapter_index"), 5)
        self.assertEqual(rep.get("source"), "chapter:5")


if __name__ == "__main__":
    unittest.main()
