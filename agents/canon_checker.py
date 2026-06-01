"""
CanonCheckerAgent —— 设定护栏（确定性、无 LLM、快）。

**通用文本 canon 校验**：扫一段文本是否引用了未定义的专有名词、
真 AI asset 出现却没用占位符等。源签名 `validate_text(state, source, text)`
不绑定章节语境，能在 chapter / outline.goal / blueprint.beat / inspiration
等任何写文本字段的地方复用——同一份规则，多处校验。

工作机制（纯字符串规则，无 LLM）：
  · 抓 《...》 / 【...】 / 「...」 / 〔...〕 内的短语
  · 抓"...宗/门/派/帮/会/城/国/山/谷/洲"等常见命名后缀
  · 和 state 里已定义的【能力/境界/地名/势力/角色/术语】交叉比对
  · 真 AI asset 名出现但没用 [[ASK_AI:...]] 占位 → critical

警告不阻断流程——汇总到 issues 列表，调用方决定如何处理（写 progress_warning
红字 / 触发修订 / 阻塞下游）。

向后兼容：`check_canon(state, chapter_index, content)` 是薄包装，原有调用点
（director.py 写章后审计）不需要改动。
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from persistence.state import NovelState


# 中文专有名词的四种常见包裹符
_BRACKET_PATTERNS = [
    (r"《([^》《]{1,12})》",  "书名号"),
    (r"【([^】【]{1,12})】",  "方头括号"),
    (r"「([^」「]{1,12})」",  "日式引号"),
    (r"〔([^〕〔]{1,12})〕",  "六角括号"),
]

# 命名后缀启发 —— 用于抓取势力/地点类专有名词
_FACTION_SUFFIXES = ["宗", "门", "派", "会", "盟", "帮", "教", "殿", "阁", "楼", "堂", "阵营", "家族"]
_LOCATION_SUFFIXES = ["城", "山", "岭", "谷", "峰", "洲", "原", "国", "都", "府", "街", "镇", "村", "寨", "坊", "境"]
_ABILITY_SUFFIXES = ["诀", "咒", "术", "经", "典", "功", "法", "技", "奥义", "神通", "秘术"]

# 停用词——避免把普通词当成专有名词
_STOP_WORDS = {
    "一般", "现实", "故事", "主角", "师父", "师傅", "朋友", "仇敌", "世界", "天下", "修仙",
    "修真", "异能", "觉醒", "重生", "穿越", "任务", "系统", "能力", "技能", "实力", "境界",
    "大陆", "江湖", "天地", "天空", "大地", "山川", "河流", "城市", "学校", "公司", "医院",
    "此刻", "片刻", "顷刻", "须臾", "突然", "忽然", "刹那",
}


@dataclass
class CanonIssue:
    kind: str              # "ability" / "faction" / "region" / "character"
    term: str              # 原文出现的词
    context_snippet: str   # 上下文片段（30 字）
    severity: str          # "info" / "warn"
    suggestion: str        # 建议（如"在 power_system.special_abilities 中补充定义"）


def validate_text(state: NovelState, source: str, text: str) -> dict:
    """
    **通用文本 canon 校验**——不绑定章节语境，可用于校验：
      · 章节正文        source="chapter:N"
      · outline.goal    source="outline:V{vol}Ch{ch}.goal"
      · blueprint.beat  source="blueprint:V{vol}Ch{ch}.beat{n}"
      · inspiration     source="inspiration:V{vol}Ch{ch}"

    返回 {
      "source": source,
      "issues": [CanonIssue 的 dict 形式],
      "stats":  {"new_abilities": N, "new_factions": N, ...}
    }
    issues 不阻断流程；由调用方决定如何处理（progress_warning / revise / 阻塞下游）。
    """
    content = text  # 内部保留原变量名，避免大段改动
    # ── 构造已定义 canon 集合（规范名 + 别名）──
    known_abilities = set()
    known_realms = set()
    if state.power_system:
        for ab in state.power_system.special_abilities or []:
            known_abilities.add(ab.name)
        for r in state.power_system.realms or []:
            known_realms.add(r.name)

    known_regions = set()
    geo = state.geography
    if geo and geo.regions:
        for rg in geo.regions:
            known_regions.add(rg.name)

    known_factions = {f.name for f in (state.factions or [])}
    known_characters = {c.name for c in (state.characters or [])}
    known_glossary = set()
    for g in (state.glossary or []):
        known_glossary.add(g.term)
        known_glossary.update(g.aliases or [])

    # world_canon 锚点（朝代名 + 别称 + 根地理）——也是 canon 的一部分，
    # 让"白鹿朝"出现在文本中时能和 state.world_canon.dynasty_name="大雍王朝"对照
    known_world_canon = set()
    wc = getattr(state, "world_canon", None)
    if wc:
        if wc.dynasty_name:
            known_world_canon.add(wc.dynasty_name)
        known_world_canon.update(a for a in (wc.canonical_aliases or []) if a)
        if wc.region_root:
            known_world_canon.add(wc.region_root)

    # 总的"可接受"集合（术语表是大兜底，world_canon 加进来让朝代/根地理也能比对）
    all_known = (
        known_abilities | known_realms | known_regions |
        known_factions | known_characters | known_glossary |
        known_world_canon
    )

    issues: list[CanonIssue] = []
    stats = {
        "new_abilities": 0, "new_factions": 0, "new_regions": 0,
        "unknown_brackets": 0, "total_scanned": 0,
    }

    def _is_variant(term: str, known_set: set[str]) -> bool:
        """term 是否是已知名的变体（子串/超串）。"""
        if term in known_set:
            return True
        for name in known_set:
            if not name:
                continue
            # 变体判定：若 term 是 name 的子串（≥2 字）或反之，视为匹配
            if len(term) >= 2 and (term in name or name in term):
                return True
        return False

    def _snippet(term: str) -> str:
        idx = content.find(term)
        if idx < 0:
            return ""
        start = max(0, idx - 10)
        end = min(len(content), idx + len(term) + 10)
        return content[start:end].replace("\n", " ")

    # ── 1. 括号包裹的词 ──
    for pat, _kind in _BRACKET_PATTERNS:
        for m in re.finditer(pat, content):
            term = m.group(1).strip()
            if not term or term in _STOP_WORDS or len(term) < 2:
                continue
            stats["total_scanned"] += 1
            if _is_variant(term, all_known):
                continue
            # 按后缀推测类别
            if any(term.endswith(s) for s in _ABILITY_SUFFIXES):
                issues.append(CanonIssue(
                    kind="ability", term=term,
                    context_snippet=_snippet(term),
                    severity="warn",
                    suggestion="看似功法/技能但未在 power_system.special_abilities 定义；确认是笔误/新设定",
                ))
                stats["new_abilities"] += 1
            else:
                issues.append(CanonIssue(
                    kind="unknown_bracket", term=term,
                    context_snippet=_snippet(term),
                    severity="info",
                    suggestion="括号内专有名词但未登记——若是固定设定，建议补进 glossary",
                ))
                stats["unknown_brackets"] += 1

    # ── 2. 后缀命名法：2-3 字专有名词 + 典型后缀 ──
    # 边界放宽：句首/标点后/常见动词介词后（见/到/在/向/朝/往/进/入/出/至/是/道/的/为/被）
    BOUNDARY = r"(?:^|(?<=[，。！？、；：「」『』（）\s\"\'""''见到在向朝往进入出至是道的为被回返抵投奔过经离开了]))"

    # 势力：在子句边界后，紧接着 2-3 字汉字 + 后缀
    faction_pattern = BOUNDARY + r"([一-龥]{2,3})(" + "|".join(_FACTION_SUFFIXES) + r")"
    for m in re.finditer(faction_pattern, content):
        full = m.group(1) + m.group(2)
        if full in _STOP_WORDS or len(full) < 3:
            continue
        # 后缀后再来一个汉字 → 可能是词的一部分（如"宗师"/"门徒"），跳过
        end = m.end()
        if end < len(content) and re.match(r"[一-龥]", content[end]):
            tail = content[end]
            # 允许常见助词/方位词跟随："宗的"/"派内"/"殿门口"
            if tail not in "的之及与和或者也者们里内外上下中前后旁门口侧周围":
                continue
        stats["total_scanned"] += 1
        if _is_variant(full, known_factions | known_glossary):
            continue
        issues.append(CanonIssue(
            kind="faction", term=full,
            context_snippet=_snippet(full),
            severity="warn",
            suggestion="看似势力/组织但未在 factions/glossary 定义；若是新势力请补 state.factions",
        ))
        stats["new_factions"] += 1

    # 地点
    region_pattern = BOUNDARY + r"([一-龥]{2,3})(" + "|".join(_LOCATION_SUFFIXES) + r")"
    seen_regions = set()
    for m in re.finditer(region_pattern, content):
        full = m.group(1) + m.group(2)
        if full in seen_regions:
            continue
        seen_regions.add(full)
        if len(full) < 3 or full in _STOP_WORDS:
            continue
        end = m.end()
        if end < len(content) and re.match(r"[一-龥]", content[end]):
            tail = content[end]
            if tail not in "的之及与和或者也者们里内外上下脉":
                continue
        stats["total_scanned"] += 1
        if _is_variant(full, known_regions | known_glossary):
            continue
        issues.append(CanonIssue(
            kind="region", term=full,
            context_snippet=_snippet(full),
            severity="info",
            suggestion="看似地点但未在 geography.regions/glossary 定义；若是新场景请补 geography 或 glossary",
        ))
        stats["new_regions"] += 1

    # ── 3a. 外接 LLM asset 占位符检查（仅对 chapter:* 正文有效）──
    # 占位符是 writer 在章节正文阶段的事——outline.goal / blueprint.beat 等规划
    # 阶段文本本来就不会有占位（占位由 writer 在写正文时插入）。
    # 所以这条规则按 source 分流，仅对 chapter 文本生效。
    is_chapter_source = source.startswith("chapter:")
    if is_chapter_source and state.power_system:
        _ASK_AI_PAT = re.compile(r"\[\[ASK_AI:([^|\]]+)\|[^\]]+\]\]")
        placeholders_used = {m.group(1).strip()
                              for m in _ASK_AI_PAT.finditer(content)}

        # ─── 3a.1 新增：系统弹窗格式检测（最高优先级——不管是否有占位都要抓）───
        # 历史 bug：本章只要有过 1 次 [[ASK_AI:豆包|...]]，下面的 ab.name not in placeholders_used
        # 检查就被绕过。但 writer 仍可大段编 【豆包：...】 系统弹窗——
        # 这是网文 LLM 训练数据里看了几十万次的"系统流"格式，本能模仿。
        # 必须独立检测此 format，无论是否伴随占位。
        for ab in state.power_system.special_abilities or []:
            if not (ab.external_llm_profile or "").strip() or not ab.name:
                continue
            # 模式 1：【<asset 名>...】 直接弹窗（"豆包：分析完成..."）
            pat1 = re.compile(r"【" + re.escape(ab.name) + r"[^】]*】")
            # 模式 2：【系统...】/【宿主，...】/【...完成中】系统流标配弹窗
            # （asset 名出现在本章 + 弹窗格式都成立时报警）
            pat2 = re.compile(r"【(?:系统[^】]*|宿主[，,][^】]*|[^】]{1,15}完成中|检测[^】]*|分析完成[^】]*|扫描[^】]*完成)】")
            hits = list(pat1.finditer(content)) + list(pat2.finditer(content))
            if hits and ab.name in content:  # 必须本章真涉及该 asset
                snippet_pos = hits[0].start()
                preview = content[max(0, snippet_pos-5):snippet_pos+40].replace("\n", " ")
                issues.append(CanonIssue(
                    kind="system_window_format",
                    term=f"{ab.name}/系统弹窗",
                    context_snippet=preview,
                    severity="error",
                    suggestion=(
                        f"正文出现【...】系统弹窗格式（共 {len(hits)} 处，例「{hits[0].group(0)[:30]}」），"
                        f"这是网文标配 UI 包装 = writer 编的 AI 输出。"
                        f"AI 真实回答会被替换进 [[ASK_AI:{ab.name}|具体问题]] 占位，"
                        "格式是自然段落，不是【】UI 弹窗。删除所有【豆包/系统/宿主...】块。"
                    ),
                ))

        # ─── 3a.2 真 AI 交互占位缺失检查 ───
        # 只在"提问 / 输入问题 / 获得回答 / 生成建议"这类真实交互场景要求占位。
        # 普通叙述（如"豆包登录页一闪而过""脑中的豆包界面若隐若现"）是能力表现，
        # 不代表 writer 编了 AI 回答，不能误伤。
        def _is_real_ai_interaction(asset_name: str, pos: int) -> bool:
            w_start, w_end = max(0, pos - 80), min(len(content), pos + len(asset_name) + 120)
            window = content[w_start:w_end]
            rel_pos = pos - w_start
            before = window[:rel_pos]
            after = window[rel_pos + len(asset_name):]
            name = re.escape(asset_name)

            # 明确发问：向/问/请教/咨询 + asset
            ask_before = r"(?:向|问|询问|提问|追问|求助|请教|咨询|请|让|要求).{0,24}" + name + r"$"
            if re.search(ask_before, before):
                return True

            # asset 后紧跟提问/输入/输出/推理行为。
            action_after = (
                r"^.{0,40}(?:问|询问|提问|追问|求助|请教|咨询|输入|键入|打字|发送|提交|"
                r"说话|开口|发声|回答|回复|回应|答复|告诉|告知|建议|解释|说明|"
                r"给出|输出|生成|返回|列出|提供|分析|推演|推算|计算|算出|确认|判断|预测)"
            )
            if re.search(action_after, after):
                return True

            # asset 的产物名词：豆包的答案/建议/公式/图纸...
            product_after = r"^.{0,16}(?:答案|回答|回复|建议|解释|方案|策略|公式|图纸|步骤|结论|结果)"
            if re.search(product_after, after):
                return True

            return False

        for ab in state.power_system.special_abilities or []:
            llm_profile = (ab.external_llm_profile or "").strip()
            if not llm_profile:
                continue
            if not ab.name or ab.name not in content:
                continue
            # 找到 asset 名所有出现位置；只有交互语境才必须由占位承接。
            name_positions = [m.start() for m in re.finditer(re.escape(ab.name), content)]
            # 排除被 [[ASK_AI:asset|...]] 占位包裹的出现位置（占位内部的 asset 名不算独立出现）
            placeholder_spans = [(m.start(), m.end()) for m in _ASK_AI_PAT.finditer(content)]
            def _in_placeholder(pos):
                return any(s <= pos < e for s, e in placeholder_spans)
            uncovered = []
            for pos in name_positions:
                if _in_placeholder(pos):
                    continue
                # ±200 字窗口内有占位？
                w_start, w_end = max(0, pos - 200), min(len(content), pos + 200)
                window = content[w_start:w_end]
                has_nearby_placeholder = bool(_ASK_AI_PAT.search(window))
                if not has_nearby_placeholder and _is_real_ai_interaction(ab.name, pos):
                    uncovered.append(pos)
            if not uncovered:
                continue
            issues.append(CanonIssue(
                kind="external_ai_no_placeholder",
                term=ab.name,
                context_snippet=_snippet(ab.name),
                severity="error",
                suggestion=(
                    f"《{ab.name}》绑定真 LLM（external_llm_profile={llm_profile}），"
                    f"主角与它的交互正文里必须用 [[ASK_AI:{ab.name}|具体问题]] 占位；"
                    "writer 不许自己编 AI 的回答。把'X 说...'/'X 告诉他...'"
                    f"改写成主角提问 + 占位 + 反应的形式。"
                ),
            ))

    # ── 3b. 真 AI asset 被命令查询本书虚构设定（通用——outline 阶段尤其重要）──
    # 检测模式："<asset 名> ... [确认/查询/告知/得知/告诉/求/分析出/推演出/算出/给出] ..."
    # —— 真 AI 训练数据没有本书虚构设定，让它"确认朝代/查询律法/告知人物底牌"
    # 必然导致下游 writer 编 AI 回答（即便 chapter 阶段拦住了 writer，也会出现 writer
    # 不得不"圆场"——写一段豆包"调取离线数据库"之类的本来不该存在的功能）。
    # 在 outline.goal 阶段拦住此模式 = 源头消除污染。
    _DANGEROUS_VERBS = ["确认", "查询", "告知", "得知", "告诉",
                          "分析出", "推演出", "算出", "给出"]
    if state.power_system:
        for ab in state.power_system.special_abilities or []:
            llm_profile = (ab.external_llm_profile or "").strip()
            if not llm_profile or not ab.name or ab.name not in content:
                continue
            # 在 asset 名后 0~40 字范围内找高风险动词
            pos = 0
            while True:
                idx = content.find(ab.name, pos)
                if idx < 0:
                    break
                window = content[idx + len(ab.name) : idx + len(ab.name) + 40]
                hit_verb = next((v for v in _DANGEROUS_VERBS if v in window), None)
                if hit_verb:
                    sev = "error" if not is_chapter_source else "warn"
                    issues.append(CanonIssue(
                        kind="real_ai_dangerous_command",
                        term=f"{ab.name}…{hit_verb}",
                        context_snippet=_snippet(ab.name),
                        severity=sev,
                        suggestion=(
                            f"文本要求《{ab.name}》「{hit_verb}」——真 AI 训练数据"
                            "没有本书虚构设定，无法答朝代名/年号/律法/虚构人名/本地行情"
                            "等专有信息。改写：让主角问 AI「现代真实世界相关原理」，"
                            "再结合 canon 里的本地信息自己推断剧情线索。"
                        ),
                    ))
                    break  # 同一 asset 报一次即可
                pos = idx + len(ab.name)

    # ── 3c. 朝代/国号一致性（用 world_canon.dynasty_name 锚定）──
    # 抓文本里所有看起来像朝代名的词（X 王朝 / X 朝 / X 国），与 world_canon
    # 比对。不匹配 = canon 漂移（如 outline.goal 写"白鹿朝"但 canon 是"大雍王朝"）。
    # 这条修的是用户案例里第 1 章那种"豆包确认架空白鹿朝"的根因——
    # 朝代名漂移在 outline 阶段就该被抓到。
    if wc and wc.dynasty_name:
        # 允许的朝代名集合 = 全名 + 别称
        allowed_dynasty_names = {wc.dynasty_name} | set(wc.canonical_aliases or [])

        # 只查 "X 朝" / "X 王朝"——"X 国"误报太多（中国/王国/国家/国民...）
        # 朝代名前缀禁词（这些前缀 + "朝/王朝" 是常见误报，不是朝代名）
        _DYNASTY_PREFIX_BLOCK = {
            # 修饰/泛指词
            "历史", "架空", "虚构", "任何", "哪个", "哪些", "这个", "那个", "某个",
            "本朝", "该朝", "此朝", "前朝", "后朝", "古朝", "现朝", "今朝", "未来", "过去",
            "核算", "成为", "将王", "史当", "队入", "封建", "供养", "重写", "掌握",
            # 单字修饰（2-字 prefix 的第一字）
            "本", "该", "此", "前", "后", "古", "现", "今", "新", "旧", "全", "外", "我",
            "他", "她", "它", "你", "您",
            "核", "成", "将", "史", "队", "封", "供", "重", "掌",
            # 朝代名以外的"X朝"（佛朝/僧朝/僧侣常见组合等）
            "佛", "圣", "天", "神",
        }
        # 朝代名只看 2 字 prefix 最稳——历史上 99% 朝代名都是 1-2 字
        # （"大雍"/"东周"/"南宋"/"白鹿"），3 字 prefix 几乎都是误报
        # 1 字 prefix 太短易误报（"国朝/我朝/本朝"），用上面 BLOCK 兜
        dynasty_pattern = re.compile(r"([一-龥]{2})(王朝|朝)")
        seen_mismatch = set()
        for m in dynasty_pattern.finditer(content):
            full = m.group(0)       # 如"白鹿朝"
            prefix = m.group(1)     # 如"白鹿"
            # 2-字 prefix 的第一字是修饰词 → 跳过（"本朝/前朝/今朝"）
            if prefix in _DYNASTY_PREFIX_BLOCK or prefix[0] in _DYNASTY_PREFIX_BLOCK:
                continue
            # "X 朝" 后面跟特定字构成复合词（非朝代后缀）——跳过这些
            # 如 朝廷/朝阳/朝堂/朝代/朝向/朝拜/朝会/朝野...
            _NON_DYNASTY_TAIL = set("廷阳堂向代会见野夕晖气暮霞拜贡贺鲜思觐礼")
            end = m.end()
            suffix = m.group(2)
            if suffix == "朝" and end < len(content):
                tail = content[end]
                if tail in _NON_DYNASTY_TAIL:
                    continue  # "X 朝廷/X 朝阳"等复合词，不是朝代
            if full in seen_mismatch:
                continue
            matched = any(
                full == name or full in name or name in full
                for name in allowed_dynasty_names
            )
            if matched:
                continue
            seen_mismatch.add(full)
            issues.append(CanonIssue(
                kind="dynasty_name_mismatch",
                term=full,
                context_snippet=_snippet(full),
                severity="error",
                suggestion=(
                    f"文本提到「{full}」但 state.world_canon.dynasty_name="
                    f"「{wc.dynasty_name}」（别称：{wc.canonical_aliases or '无'}）。"
                    "改写：使用 canon 朝代名；若该名字是新设定，先在 world_setting "
                    "里增补并重跑 world_canon_extractor。"
                ),
            ))

        # ── 3d. 真实历史专名漂移 ─────────────────────────
        # 架空王朝项目里，LLM 很容易把明代制度/人物名顺手带进来。
        # 这里不阻断所有历史元素，只在当前 canon 不是“大明/明朝”时提示污染。
        dynasty_text = " / ".join(allowed_dynasty_names)
        if "明" not in dynasty_text:
            historical_terms = ["大明", "锦衣卫", "司礼监", "张居正", "冯保"]
            for term in historical_terms:
                if term not in content:
                    continue
                issues.append(CanonIssue(
                    kind="historical_anchor_drift",
                    term=term,
                    context_snippet=_snippet(term),
                    severity="error",
                    suggestion=(
                        f"当前 canon 朝代是「{wc.dynasty_name}」，文本却出现真实历史锚点「{term}」。"
                        "若不是刻意引用真实明代，请改成本书自有机构/人物名。"
                    ),
                ))

        # ── 3e. 古代/王朝语境的现代硬词 ───────────────────
        # 现代金融/工业词可以作为主角思维出现，但规划层直接写硬词会把古代舞台拉穿。
        is_dynasty_setting = bool(wc.dynasty_name) or "古代" in (state.genre or "") or "王朝" in content
        if is_dynasty_setting:
            modern_terms = ["KPI", "电脑", "铁路", "资产证券化"]
            for term in modern_terms:
                if term not in content:
                    continue
                issues.append(CanonIssue(
                    kind="anachronistic_term",
                    term=term,
                    context_snippet=_snippet(term),
                    severity="warn",
                    suggestion=(
                        "古代/王朝语境里不要在规划层裸用现代硬词；"
                        "改成本世界可落地的说法，如考课、札记、新式官道、凭票分润等。"
                    ),
                ))

    # ── 3f. 故事根基（真实 / 虚构）规约 ────────────────────────
    # 当 state.creative_intent.reality_basis ∈ {real_history, real_adapted}：
    #   · respect_real_figures=True 且名单非空 → 每提一次真实人物名，挂一条 info/warn
    #     提醒 writer/reviewer 校验言行；让 canon-revise 反馈里能复述这条约束。
    #   · real_history 模式还要硬抓「穿越/重生/系统提示/异世/金手指」这类违和元素 = error
    # fictional 模式不在此处加规则——前面 3d/3e 已经对架空王朝做了反向 anchor 检测。
    ci = getattr(state, "creative_intent", None)
    if ci and ci.analyzed:
        basis = (ci.reality_basis or "fictional").strip()
        if basis in {"real_history", "real_adapted"}:
            persons = [p.strip() for p in (ci.real_persons or []) if p and p.strip()]
            if ci.respect_real_figures and persons and is_chapter_source:
                seen_persons = set()
                for pname in persons:
                    if pname in seen_persons or pname not in content:
                        continue
                    seen_persons.add(pname)
                    issues.append(CanonIssue(
                        kind="real_person_check",
                        term=pname,
                        context_snippet=_snippet(pname),
                        severity="warn",
                        suggestion=(
                            f"本章出现真实历史人物「{pname}」——故事根基="
                            f"{basis}，须确认其台词/动机/抉择/结局符合主流史料。"
                            "凭空虚构其立场或安排史料明确否定的行为会触发设定漂移。"
                        ),
                    ))

            # real_history 严格模式：穿越/系统/异能等违和元素 = critical
            if basis == "real_history":
                anachronisms = [
                    ("穿越", "穿越者"),
                    ("重生归来", "重生设定"),
                    ("系统提示", "金手指系统"),
                    ("【宿主", "系统宿主提示"),
                    ("修真境界", "修真元素"),
                    ("系统商城", "系统流商城"),
                ]
                for kw, label in anachronisms:
                    if kw in content:
                        issues.append(CanonIssue(
                            kind="real_history_anachronism",
                            term=kw,
                            context_snippet=_snippet(kw),
                            severity="error",
                            suggestion=(
                                f"故事根基=real_history（严格基于真实历史），"
                                f"正文出现「{label}」属违和元素。改写：移除超现实"
                                "设定，或把根基调成 real_adapted/fictional。"
                            ),
                        ))

    # ── 4. 去重（同一个词只报一次，按首次出现）──
    seen = set()
    unique_issues = []
    for iss in issues:
        key = (iss.kind, iss.term)
        if key in seen:
            continue
        seen.add(key)
        unique_issues.append(iss)

    return {
        "source": source,
        "issues": [iss.__dict__ for iss in unique_issues],
        "stats": stats,
    }


def check_canon(state: NovelState, chapter_index: int, content: str) -> dict:
    """向后兼容包装——保留 chapter_index 字段，给老调用点（director.py 写章后审计）用。

    新代码请用 validate_text(state, source, text)——能在 outline / blueprint /
    inspiration 等任何文本字段产出后调用，不绑章节语境。
    """
    report = validate_text(state, f"chapter:{chapter_index}", content)
    report["chapter_index"] = chapter_index
    return report


def format_canon_report(report: dict, max_items: int = 8) -> str:
    """人眼可读的简报，给 director 日志用。"""
    issues = report.get("issues", [])
    if not issues:
        return ""
    by_kind: dict[str, list[dict]] = {}
    for iss in issues:
        by_kind.setdefault(iss["kind"], []).append(iss)
    lines = []
    for kind, items in by_kind.items():
        kind_label = {
            "ability": "未定义能力", "faction": "未定义势力",
            "region": "未定义地点", "unknown_bracket": "未登记括号词",
            "real_person_check": "真实人物言行待核",
            "real_history_anachronism": "真实历史违和元素",
            "historical_anchor_drift": "真实历史专名漂移",
            "dynasty_name_mismatch": "朝代名漂移",
            "anachronistic_term": "时代错位词",
            "system_window_format": "系统弹窗格式",
            "external_ai_no_placeholder": "真 AI 占位缺失",
            "real_ai_dangerous_command": "真 AI 危险指令",
        }.get(kind, kind)
        sev_icon = "⚠" if any(i["severity"] == "warn" for i in items) else "·"
        terms_preview = " / ".join(i["term"] for i in items[:max_items])
        more = f"（另 {len(items) - max_items} 个）" if len(items) > max_items else ""
        lines.append(f"  {sev_icon} {kind_label}：{terms_preview}{more}")
    return "设定护栏报告：\n" + "\n".join(lines)
