"""
WorldCanonExtractor —— 从 state.world_setting 大段自然语言抽出机器可读的结构化锚点。

设计动机：
  · state.world_setting 是几千字的自然语言（含 [geography]/[history]/[society]/
    [economy]/[culture]/[taboos] 段落），下游 agent 和 validator 无法直接机器比对
  · LLM 自己每次从大段文本抽取容易漂移（如 outline 写"白鹿朝"和 world_setting
    里的"大雍王朝"对不上）
  · 把朝代/年号/根地理/时代定性这些**关键锚点**抽出来作为结构化字段
    （state.world_canon），下游所有引用走结构化数据

幂等：
  · 用 world_setting 的 md5 前 12 位作 source_hash，未变化则跳过
  · force=True 强制重抽

设计原则（[[feedback_generic_prompts]]）：
  · prompt 完全通用，不硬编码任何项目术语（朝代名 / 题材 / 桥段）
  · 从 state.world_setting 动态读取，让 LLM 自己抽
"""
from __future__ import annotations
import hashlib

from utils.json_utils import request_json, request_json_with_profile
from persistence.state import NovelState, WorldCanon


def _hash_world_setting(text: str) -> str:
    """world_setting 的 md5 前 12 位——变更检测用。"""
    if not text:
        return ""
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


SYSTEM = """你是【世界观结构化抽取员】。任务：从作者写的"世界设定"大段自然语言里
抽出几个机器可读的关键锚点字段，让下游 agent 能精准引用、validator 能机器比对。

抽取范围（**只抽 world_setting 文本里明确写过的**——不要发挥、不要补全）：

· dynasty_name      朝代/国号的**完整正式名**（如"大雍王朝"/"东周"/"联邦"/"火星共和国"）
                    若文本里没明确提朝代/国号（如现代都市文），留空
· era_name          当前年号/纪元/时间锚点（如"景和十七年"/"贞观三年"/"2087年"/"末世第十二年"）
                    没明确写就留空
· region_root       故事主要发生地的**完整名**（如"江州府青石县"/"北京朝阳区"/"赤色矿带 7 号坑"）
                    取最具体那一级
· epoch_summary     时代的一句话定性（≤30 字，如"皇权衰落，门阀垄断土地"）
· canonical_aliases 朝代名的常用别称/简称（list，如朝代名"大雍王朝"→["大雍","雍朝","大雍朝"]）
                    没朝代就空 list
· forbidden_anchors 文本里提到的**不可改写关键设定**（list，如 [taboos] 段里列的禁忌、
                    与主角直接相关的世界级硬约束。≤6 条，每条≤20 字）

═══ 输出严格 JSON ═══
{
  "dynasty_name": "...",
  "era_name": "...",
  "region_root": "...",
  "epoch_summary": "...",
  "canonical_aliases": ["...", "..."],
  "forbidden_anchors": ["...", "..."]
}

铁律：
  · 只抽**文本里出现过的**——找不到就字段留空（""）或空 list
  · 不要造词——朝代名/年号在文本里没出现，绝对不要瞎补
  · canonical_aliases 不要超出朝代名的明显简称（"大雍王朝" → ["大雍"] 合理；
    "大雍王朝" → ["雍王"] 不合理，"雍王"是人不是朝代简称）"""


def extract_world_canon(state: NovelState, force: bool = False) -> WorldCanon:
    """抽取 + 写回 state.world_canon。返回新的 WorldCanon（也已写到 state 上）。

    幂等：source_hash 未变化时跳过；force=True 强制重抽。
    抽取失败时返回当前 world_canon（保持向后兼容，不阻塞流程）。
    """
    world_text = (state.world_setting or "").strip()
    if not world_text:
        return state.world_canon  # world_setting 还没填，没东西可抽

    cur_hash = _hash_world_setting(world_text)
    if not force and state.world_canon and state.world_canon.source_hash == cur_hash:
        return state.world_canon  # 内容没变，跳过

    user = f"""作者写的世界设定：
\"\"\"
{world_text[:4000]}
\"\"\"

按 SYSTEM 里的规则抽取关键锚点。严格 JSON 输出。"""

    # 走 'extractor' usage 路由——专用结构化提取模型（轻量便宜，不浪费 main 的能力）
    # 用户没绑 extractor profile 时，user_models.find_by_usage 返回 None，
    # request_json_with_profile 内部自然 fallback 到 main——向后兼容。
    try:
        data = request_json_with_profile(
            "extractor",
            system=SYSTEM, user=user,
            required_keys=["dynasty_name", "region_root"],
            max_retries=2, temperature=0.2,
            agent_name="WorldCanonExtractor",
            empty_ok=True,
        )
    except Exception as _e:
        # extractor profile 不可用 → 退到默认 request_json（走 main）
        print(f"  ⚠ extractor 模型失败（{type(_e).__name__}），回退到默认模型")
        data = request_json(
            system=SYSTEM, user=user,
            required_keys=["dynasty_name", "region_root"],
            max_retries=2, temperature=0.2,
            agent_name="WorldCanonExtractor",
            empty_ok=True,
        )
    if not data:
        print("  ⚠ world_canon 抽取 LLM 失败，保留现有 canon")
        return state.world_canon

    canon = WorldCanon(
        dynasty_name=str(data.get("dynasty_name") or "")[:40],
        era_name=str(data.get("era_name") or "")[:40],
        region_root=str(data.get("region_root") or "")[:60],
        epoch_summary=str(data.get("epoch_summary") or "")[:80],
        canonical_aliases=[str(a)[:20] for a in (data.get("canonical_aliases") or []) if a][:6],
        forbidden_anchors=[str(a)[:30] for a in (data.get("forbidden_anchors") or []) if a][:8],
        extracted_at_phase=getattr(state, "_current_phase", "") or "1D",
        source_hash=cur_hash,
    )
    state.world_canon = canon
    print(
        f"  ✓ world_canon 抽取完成："
        f"朝代={canon.dynasty_name!r} 年号={canon.era_name!r} "
        f"根地理={canon.region_root!r} 别称={canon.canonical_aliases}"
    )
    return canon
