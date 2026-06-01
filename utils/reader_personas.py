"""
ReaderPersonas —— 通用读者画像库（4 类网文典型读者）。

═══ 用途 ═══

抽取自 comment_simulator 的"4 类读者身份"定义,作为可复用的 persona 数据源,
供任何需要"模拟读者视角"的 agent 引用:
  · comment_simulator      章后模拟评论
  · expectation_manager    写章前预测预期(可选启用 persona)
  · reader_experience_auditor  审追读率(可选 persona 视角)
  · progress_dashboard     "读者画像"板块(未来扩展)

═══ 4 类典型读者 ═══

每个 persona 含:
  · key            英文 key(代码引用)
  · label          中文标签(给 LLM/UI)
  · stance         倾向(positive/critical/neutral/mixed)
  · interests      关注点
  · pain_points    让他骂的事(prompt 警示)
  · delight_points 让他赞的事
  · catchphrases   典型口头禅/句式样本

═══ 设计原则 ═══

· 纯数据 + 工具函数,不引入 LLM 依赖
· 通用——不绑特定题材/平台
· 按 [[feedback_generic_prompts]] —— 不写死具体项目术语
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReaderPersona:
    key: str               # 代码引用的 key(英文)
    label: str             # 中文标签
    stance: str            # positive / critical / neutral / mixed
    interests: tuple       # 关注点
    pain_points: tuple     # 让他骂的事
    delight_points: tuple  # 让他赞的事
    catchphrases: tuple    # 典型口头禅样本

    def to_prompt_block(self) -> str:
        """渲染为 LLM prompt 可用的 persona 段落。"""
        return (
            f"· {self.label}({self.stance})\n"
            f"  关注: {' / '.join(self.interests)}\n"
            f"  会骂: {' / '.join(self.pain_points)}\n"
            f"  会赞: {' / '.join(self.delight_points)}\n"
            f"  典型口吻样本: " + " / ".join(f"「{c}」" for c in self.catchphrases)
        )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "stance": self.stance,
            "interests": list(self.interests),
            "pain_points": list(self.pain_points),
            "delight_points": list(self.delight_points),
            "catchphrases": list(self.catchphrases),
        }


# ═══════════════════════════════════════════════════════
#  4 类标准读者
# ═══════════════════════════════════════════════════════

DIE_HARD = ReaderPersona(
    key="die_hard",
    label="追读派",
    stance="positive",
    interests=("主线推进", "情感投入", "爽点", "主角弧"),
    pain_points=("主线停滞", "无意义章节", "拖戏", "主角降智"),
    delight_points=("大爽点", "情感高潮", "钩子炸裂", "回收伏笔"),
    catchphrases=("催更!", "主角太惨了快点反击啊", "这章看哭了", "明天更不更?"),
)

NITPICKER = ReaderPersona(
    key="nitpicker",
    label="挑刺派",
    stance="critical",
    interests=("逻辑严密", "设定自洽", "文笔", "人物 OOC"),
    pain_points=("逻辑漏洞", "设定矛盾", "降智写法", "节奏拖沓", "对话同质"),
    delight_points=("精妙伏笔", "智斗", "文学性句子", "细节呼应"),
    catchphrases=("这里逻辑不对", "前面铺垫白做了", "OOC 警告", "为什么主角突然知道?"),
)

CASUAL = ReaderPersona(
    key="casual",
    label="路过派",
    stance="neutral",
    interests=("玩梗", "搞笑", "轻松", "吐槽"),
    pain_points=("过于沉重", "看不懂", "节奏太慢"),
    delight_points=("反差萌", "好笑桥段", "出彩配角"),
    catchphrases=("哈哈这章看着乐", "xxx 这反派演技不错", "笑死", "这画面我能脑补半天"),
)

QUOTER = ReaderPersona(
    key="quoter",
    label="章评党",
    stance="mixed",
    interests=("金句", "画面感", "经典段落", "可截图段"),
    pain_points=("陈词滥调", "强行煽情", "出戏比喻"),
    delight_points=("震撼场景描写", "经典对白", "意境句"),
    catchphrases=("「xx」这句封神", "这一段我截图了", "这画面太美了不敢看", "金句预定"),
)

ALL_PERSONAS = [DIE_HARD, NITPICKER, CASUAL, QUOTER]
PERSONA_BY_KEY = {p.key: p for p in ALL_PERSONAS}
PERSONA_BY_LABEL = {p.label: p for p in ALL_PERSONAS}


# ═══════════════════════════════════════════════════════
#  公共 API
# ═══════════════════════════════════════════════════════

def get_persona(key_or_label: str) -> ReaderPersona | None:
    """按 key(英文)或 label(中文)查 persona。"""
    if not key_or_label:
        return None
    return PERSONA_BY_KEY.get(key_or_label) or PERSONA_BY_LABEL.get(key_or_label)


def format_all_for_prompt(*, header: str = "") -> str:
    """把所有 persona 渲染成单一 prompt block。供 LLM 系统级 prompt 注入。"""
    lines = []
    if header:
        lines.append(header)
    for p in ALL_PERSONAS:
        lines.append(p.to_prompt_block())
        lines.append("")
    return "\n".join(lines).rstrip()


def format_personas_for_prompt(*personas: ReaderPersona, header: str = "") -> str:
    """按指定 persona 列表渲染(用于只用一两类的场景)。"""
    if not personas:
        return ""
    lines = []
    if header:
        lines.append(header)
    for p in personas:
        lines.append(p.to_prompt_block())
        lines.append("")
    return "\n".join(lines).rstrip()


def all_labels() -> list[str]:
    """返回所有 persona 中文标签(供 schema 校验/UI 下拉框)。"""
    return [p.label for p in ALL_PERSONAS]


def all_keys() -> list[str]:
    """返回所有 persona 英文 key。"""
    return [p.key for p in ALL_PERSONAS]
