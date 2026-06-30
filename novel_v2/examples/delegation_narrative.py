"""为「乙方修仙」原创设计的叙事圣经示例:一个四层真相栈 + 伏笔 + 灵魂角色。

仅作结构演示(反转创意应由人审定);它证明:同一套叙事真相引擎，灌进这份配置，
就能守护「反转的反转的反转」一致、伏笔全收、角色秘密对齐。
"""
from __future__ import annotations

from ..narrative import (Foreshadow, ForeshadowLedger, FactBook, NarrativeBible,
                         ReframableFact, SoulCharacter, SoulRegistry,
                         TruthLayer, TruthStack)


def build_bible() -> NarrativeBible:
    truth = TruthStack([
        TruthLayer(0, "表象",
                   "苏明穿越，绑定天道委托所，接单赚信誉、一步步变强。", 0),
        TruthLayer(1, "系统有意志",
                   "系统不是中立工具，它在用一个个委托，把苏明往某条特定的路上引。",
                   30, "推翻『系统只是金手指』"),
        TruthLayer(2, "前人之局",
                   "天道委托所是上一个失败者留下的局，他想借苏明，完成自己没做完的事。",
                   80, "推翻『系统是天道的』"),
        TruthLayer(3, "自我之局",
                   "苏明前世真名陈默——他不是穿越者，而是这个局的设计者，亲手抹除记忆放了进来。"
                   "整个委托所，是他给自己布的局。", 150, "推翻『局是别人布的』"),
    ])

    facts = FactBook()
    facts.add(ReframableFact(
        "f_chenmo", "铁匠恍惚间喊出『陈默』，像是认错了人。",
        {1: "系统在借铁匠之口，试探苏明对这个名字的反应。",
         3: "知情的守门人在提醒:你就是陈默。"}, planted_at=1))
    facts.add(ReframableFact(
        "f_dingjin", "『定金五成，概不赊账』是苏明的口头禅、性格使然。",
        {2: "这条规矩其实是上一任局主立下的，苏明无意识地沿用了。"}, planted_at=1))

    fore = ForeshadowLedger()
    fore.add(Foreshadow("fs_chenmo", "『陈默』之名反复出现", serves_layer=3, planted_at=1, paid_at=150))
    fore.add(Foreshadow("fs_guide", "系统总在关键处不动声色地推主角一把", serves_layer=1, planted_at=5, paid_at=30))
    fore.add(Foreshadow("fs_prevhost", "局中处处是『前一个人』留下的痕迹", serves_layer=2, planted_at=40, paid_at=80))

    souls = SoulRegistry()
    souls.add(SoulCharacter(
        "张铁山", "固执的老铁匠", "只想打最后一把刀的孤寡老人",
        wound="十年前独子离家未归，至今不知生死",
        longing="再见儿子一面，了断这十年心结",
        secret="他是局中始终清醒的『守门人』，认得陈默，是他刻意把第一单引给了苏明。",
        secret_layer=2, bond_with_mc="第一个让苏明动了底线、破例倒贴的人",
        introduced_at=1, secret_revealed_at=80))

    return NarrativeBible(truth, facts, fore, souls)
