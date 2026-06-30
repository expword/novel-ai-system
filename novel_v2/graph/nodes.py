"""图节点 —— 每个节点读 state、只返回增量 dict(不 mutate)。

节点即「单一职责 Agent」。LLM 调用走 novel_v2.llm(mock 或真实后端均可)；
RAG 通过 functools.partial 把 RagMemory 绑进来。canon 检查用 **规则**(非 LLM)：
能用代码判定的(占位符残留/正文过短)不交给模型，消除幻觉、降本提速。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict

from .. import llm
from ..llm import MockBackend
from ..rag.indexer import RagMemory
from ..state import ChapterSummary, NovelState
from ..system import PlayerState, SystemEngine, SystemSpec, extract_proposal

MAX_REVISE_ROUNDS = 2
WRITER_SYS = "你是资深中文网络小说写手，文笔生动、画面感强、节奏明快，擅长爽文。"

# 占位/未完成内容的规则特征(真实模型偶尔会残留)
_PLACEHOLDER_RE = re.compile(r"【[^】]{0,30}(待|todo|补全|占位|此处|xxx|略)[^】]{0,30}】", re.I)
_PLACEHOLDER_HINTS = ("（此处", "(此处", "此处省略", "待补", "TODO", "……(", MockBackend.FORBIDDEN)


# ---- 1. RAG 召回 ----
def retrieve_node(state: NovelState, *, rag: RagMemory) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    query = f"{state.get('title','')} {state.get('premise','')} 第{ci}章 前情 主角 冲突"
    ctx = rag.build_context(query, top_k=4, exclude_index=ci)
    return {"retrieved_context": ctx,
            "event_log": [f"[retrieve] ch{ci} 从 {rag.count()} 条记忆中召回相关前情"]}


def _narrative_for_chapter(bible, ci: int) -> dict:
    """叙事引擎:本章揭晓计划 + 已登场灵魂角色档案。"""
    plan = bible.reveal_plan_at(ci)
    souls = [s for s in bible.souls.souls.values() if s.introduced_at <= ci]
    return {
        "souls": [{"name": s.name, "tag": s.tag, "wound": s.wound,
                   "longing": s.longing, "bond": s.bond_with_mc,
                   "secret_out": bool(s.secret_revealed_at and s.secret_revealed_at <= ci),
                   "secret": s.secret if (s.secret_revealed_at and s.secret_revealed_at <= ci) else ""}
                  for s in souls],
        "reveal": plan if plan["reveal_layers"] else None,
    }


# ---- 2. 生成本章指令 ----
def directive_node(state: NovelState, *, bible=None) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    directive = {
        "title": f"第{ci}章",
        "goal": "推进主线冲突，给出一个明确的爽点，并在结尾留下钩子",
        "inject_flaw": bool(state.get("debug_inject_flaw")) and ci == 2,
    }
    spec_d = state.get("system_spec")
    if spec_d:                       # 系统流:本章结算来自单元规划(数值不靠 LLM)
        pr = spec_d["progress_resource"]
        plan = state.get("unit_plan") or {}
        settle = plan.get(str(ci)) or {"gain": {pr: 20}, "quest": f"u_ch{ci}"}
        directive["system_settle"] = settle
        if settle.get("brief"):      # 本章单元简介作为写作目标
            directive["goal"] = settle["brief"]
        if settle.get("scenes"):     # 戏剧蓝图:逐场景写(治平淡/不详细/不闭环)
            directive["scenes"] = settle["scenes"]
    if bible is not None:            # 叙事真相引擎:揭晓计划 + 灵魂角色注入
        directive["narrative"] = _narrative_for_chapter(bible, ci)
    return {"directive": directive, "revise_round": 0,
            "event_log": [f"[directive] ch{ci} 指令就绪"]}


SCENE_SYS = ("你是顶尖中文网文写手，只写有画面的场景：充分的环境、感官(光/声/气味/温度)、"
             "动作、心理、有层次的对话；绝不写梗概，绝不用形容词直接概括人物，用台词和行动演出来。")


def _write_scenes(setting: str, scenes: list) -> str:
    """逐场景调用 + 自动衔接拼接 —— 一个委托拆多场，各自充分展开(详细 + 闭环)。"""
    parts, tail = [], ""
    for sc in scenes:
        prev = f"\n【前文结尾，自然承接，不要重复】\n…{tail}\n" if tail else ""
        user = (f"{setting}{prev}\n【本场景（{sc.get('name','')}）】\n{sc.get('brief','')}\n\n"
                f"【要求】只写这一个场景，900-1200 字，充分的环境/感官/动作/心理/对话，"
                f"自然承接前文，不要小标题/解释/括号备注，直接输出正文。")
        seg = llm.system_user(SCENE_SYS, user, temperature=0.85, max_tokens=3000).strip()
        parts.append(seg)
        tail = seg[-400:]
    return "\n\n".join(parts)


def _narrative_block(narr) -> str:
    """灵魂角色档案 + 本章揭晓任务，拼进写作 prompt(让读者在乎 + 反转)。"""
    if not narr:
        return ""
    b = ""
    for s in (narr.get("souls") or []):
        line = (f"- {s['name']}（{s['tag']}）：创伤={s['wound']}；牵挂={s['longing']}；"
                f"与主角={s['bond']}")
        if s.get("secret_out"):
            line += f"；【已揭秘】{s['secret']}"
        b += line + "\n"
    if b:
        b = "【在场角色(演出他们的创伤与牵挂让读者在乎，别直接说性格)】\n" + b
    rev = narr.get("reveal")
    if rev and rev.get("reveal_layers"):
        L = rev["reveal_layers"][0]
        b += (f"\n【★本章重大反转★】揭开真相：{L['statement']}\n"
              f"要求：揭晓要有冲击力，并让读者回头发现处处早有伏笔。")
        if rev.get("reveal_souls"):
            b += f" 同时揭开 {('、'.join(rev['reveal_souls']))} 的秘密。"
        b += "\n"
    return b


# ---- 3. 写正文(场景化 / 单场，并把初稿 insert 进向量库) ----
def write_node(state: NovelState, *, rag: RagMemory) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    d = state["directive"]
    panel = ""
    if state.get("player_state"):
        panel = "【系统面板】" + PlayerState.from_dict(state["player_state"]).render() + "\n"
    setting = (f"【小说设定】{state.get('premise','(无)')}\n{panel}"
               f"{_narrative_block(d.get('narrative'))}")
    scenes = d.get("scenes")
    if scenes:                       # 戏剧蓝图:逐场景写
        draft = _write_scenes(setting, scenes)
    else:                            # 原单场写(兼容非系统流/无蓝图)
        user = (f"{setting}\n【前情提要】\n{state['retrieved_context']}\n\n"
                f"【本章任务】写第{ci}章，{d['goal']}。要求 800-1200 字，"
                f"直接输出正文，不要标题、解释或任何括号备注。")
        if d.get("inject_flaw"):
            user += "\n[INJECT_FLAW]"
        draft = llm.system_user(WRITER_SYS, user, temperature=0.85, max_tokens=2400)
    rag.index_chapter(index=ci, volume_index=_vol(state, ci),
                      title=d["title"], prose=draft, characters=["主角"])
    mode = f"场景化{len(scenes)}场" if scenes else "单场"
    return {"draft": draft,
            "event_log": [f"[write] ch{ci} 初稿 {len(draft)} 字（{mode}），已 insert"]}


# ---- 3.5 系统引擎裁决(提案-校验-执行；无 spec 则透明) ----
def system_node(state: NovelState) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    spec_d = state.get("system_spec")
    if not spec_d:                  # 非系统流:透明放行
        return {"system_violations": [], "pending_player_state": {}, "pending_events": []}
    spec = SystemSpec.from_dict(spec_d)
    engine = SystemEngine(spec)
    ps = PlayerState.from_dict(state["player_state"])
    settle = state["directive"].get("system_settle", {})
    proposal = extract_proposal(ci, settle, state["draft"], ps.skills, list(ps.items.keys()))
    r = engine.apply(ps, proposal)
    msg = "通过" if r.ok else ("违规:" + "；".join(r.violations))
    return {"system_violations": r.violations,
            "pending_player_state": r.state.to_dict() if r.ok else {},
            "pending_events": r.events,
            "event_log": [f"[system] ch{ci} 系统裁决 {msg}"]}


# ---- 4. Canon 设定检查(规则，非 LLM) ----
def canon_node(state: NovelState, *, bible=None) -> Dict[str, Any]:
    d = state["draft"]
    issues = []
    if _PLACEHOLDER_RE.search(d) or any(h in d for h in _PLACEHOLDER_HINTS):
        issues.append({"level": "critical", "code": "PLACEHOLDER",
                       "message": "正文含占位符/未完成备注，需补全"})
    if len(d.strip()) < 150:
        issues.append({"level": "critical", "code": "TOO_SHORT",
                       "message": f"正文仅 {len(d.strip())} 字，疑似截断或拒答"})
    ci = state["current_chapter_index"]
    if bible is not None:            # 叙事一致性兜底:伏笔/真相栈
        for msg in bible.audit(ci):
            issues.append({"level": "major", "code": "NARRATIVE", "message": msg})
    return {"canon_issues": issues,
            "event_log": [f"[canon] ch{ci} 命中 {len(issues)} 条违规"]}


# ---- 5. 多维 Critic(轻量评分，不阻塞) ----
def critic_node(state: NovelState) -> Dict[str, Any]:
    n = len(state["draft"].strip())
    report = {"length": n, "pass": n >= 150}
    return {"critic_report": report, "event_log": ["[critic] 评分通过"]}


# ---- 6. 自愈修订(LLM 重写 + upsert 删旧增新) ----
def revise_node(state: NovelState, *, rag: RagMemory) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    rnd = state["revise_round"] + 1
    fb = "；".join(i["message"] for i in state["canon_issues"])
    user = (f"以下章节存在问题需修订：{fb}\n"
            f"请去掉所有占位符/未完成/备注内容，保持情节连贯完整，只输出修订后的正文：\n\n"
            f"{state['draft']}")
    new_draft = llm.system_user(WRITER_SYS, user, temperature=0.7, max_tokens=2400)
    # 关键：修订后 upsert(先 delete 旧向量再 insert 新的)，避免旧版本被召回污染
    rag.reindex_chapter(index=ci, volume_index=_vol(state, ci),
                        title=state["directive"]["title"], prose=new_draft,
                        characters=["主角"])
    return {"draft": new_draft, "revise_round": rnd,
            "event_log": [f"[revise] ch{ci} 第{rnd}轮修订，已 upsert(删旧增新)"]}


# ---- 7. 定稿(LLM 摘要 + 落盘 + 记入长期记忆) ----
def finalize_node(state: NovelState, *, rag: RagMemory) -> Dict[str, Any]:
    ci = state["current_chapter_index"]
    summary = llm.system_user(
        "你是小说编辑，用一句话(30字内)概括本章关键情节，只输出概括本身。",
        state["draft"][:1500], temperature=0.3, max_tokens=80).strip().replace("\n", " ")

    out_dir = state.get("output_dir", "")
    saved = ""
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"chapter_{ci:04d}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(state["draft"])
        saved = f" → {path}"

    rag.index_chapter(index=ci, volume_index=_vol(state, ci),
                      title=state["directive"]["title"], prose=state["draft"],
                      summary=summary, characters=["主角"])
    ch: ChapterSummary = {"index": ci, "volume_index": _vol(state, ci),
                          "title": state["directive"]["title"], "summary": summary,
                          "characters": ["主角"], "word_count": len(state["draft"])}
    out = {"completed_chapters": [ch], "current_chapter_index": ci + 1,
           "event_log": [f"[finalize] ch{ci} 定稿入长期记忆{saved}"]}
    pending = state.get("pending_player_state")
    if pending:                      # 系统流:提交本章系统变更(校验已通过)
        out["player_state"] = pending
        out["system_events"] = state.get("pending_events", [])
    return out


# ---- 8. 卷级 HITL 关卡(interrupt 暂停 → 人审 → 续跑) ----
def volume_gate_node(state: NovelState) -> Dict[str, Any]:
    from langgraph.types import interrupt
    finished = state["current_chapter_index"] - 1
    vol = _vol(state, finished)
    decision = interrupt({
        "type": "volume_review",
        "volume": vol,
        "message": f"第 {vol} 卷(至第 {finished} 章)已完成，请审核后决定继续/回头改。",
    })
    return {"human_decision": decision, "awaiting_human": False,
            "event_log": [f"[HITL] 第{vol}卷复盘，作者裁决: {decision}"]}


# ---- 条件路由 ----
def route_after_critic(state: NovelState) -> str:
    has_canon = any(i["level"] == "critical" for i in state["canon_issues"])
    has_system = bool(state.get("system_violations"))
    if (has_canon or has_system) and state["revise_round"] < MAX_REVISE_ROUNDS:
        return "revise"
    return "finalize"


def route_after_finalize(state: NovelState) -> str:
    finished = state["current_chapter_index"] - 1
    if finished >= state["target_chapters"]:
        return "END"
    if finished % state["volume_size"] == 0:   # 到卷边界 → 人审
        return "gate"
    return "retrieve"


def _vol(state: NovelState, chapter_index: int) -> int:
    return (chapter_index - 1) // state["volume_size"] + 1
